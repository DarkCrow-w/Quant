from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd

from quant.core.bar import Bar
from quant.core.events import SignalEvent
from quant.core.events import OrderSide
from quant.data import get_store
from quant.data.concurrency import bounded_futures
from quant.data.schema import OHLCV_COLUMNS
from quant.strategy.base import Context

from quant.screening import FACTOR_DEFS, MultiFactorScorer, ScoreConfig

from server.models.screening import (
    FactorDef,
    FactorScoreItem,
    ScoredStock,
    ScoreRequest,
    ScoreResult,
    ScreenMatch,
    ScreenRequest,
    ScreenResult,
)
from server.services.backtest_service import STRATEGY_REGISTRY

_EMPTY_PORTFOLIO = {"positions": {}, "cash": 0, "equity": 0}


def _screen_symbol(strategy_cls, params: dict, bars: list[Bar], symbol: str) -> SignalEvent | None:
    """对单只股票回放策略，返回最后一根K线的 BUY 信号（如有）。"""
    strategy = strategy_cls(params=params)
    history: list[Bar] = []
    last_signals: list[SignalEvent] = []

    for bar in bars:
        history.append(bar)
        ctx = Context(
            bars={symbol: bar},
            history={symbol: list(history)},
            portfolio_snapshot=_EMPTY_PORTFOLIO,
            current_date=bar.dt,
        )
        last_signals = strategy.on_bar(ctx)

    for sig in last_signals:
        if sig.direction == OrderSide.BUY:
            return sig
    return None


def _process_symbol(
    symbol: str,
    strategy_cls,
    params: dict,
    scan_dt: date,
    lookback: int,
) -> ScreenMatch | None:
    """加载缓存并筛选单只股票。"""
    df = get_store().get_kline(
        symbol,
        freq="day",
        end=scan_dt,
        columns=OHLCV_COLUMNS,
        tail=lookback,
    )
    if df is None or df.empty:
        return None

    if len(df) < 30:
        return None

    bars = [
        Bar(
            symbol=symbol,
            dt=row.dt,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            amount=float(getattr(row, "amount", 0)),
        )
        for row in df.itertuples(index=False)
    ]

    sig = _screen_symbol(strategy_cls, params, bars, symbol)
    if sig is None:
        return None

    last_bar = bars[-1]
    return ScreenMatch(
        symbol=symbol,
        signal_date=str(sig.dt),
        close=round(last_bar.close, 2),
        volume=last_bar.volume,
        amount=last_bar.amount,
        strength=sig.strength,
    )


def run_screening(req: ScreenRequest) -> ScreenResult:
    t0 = time.time()

    entry = STRATEGY_REGISTRY.get(req.strategy)
    if entry is None:
        raise ValueError(f"Unknown strategy: {req.strategy}")

    strategy_cls = entry["cls"]
    scan_dt = date.fromisoformat(req.scan_date) if req.scan_date else date.today()
    params = req.strategy_params or {}

    symbols = get_store().list_symbols("day")

    matches: list[ScreenMatch] = []

    workers = min(4, max(1, len(symbols)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _, fut in bounded_futures(
            pool,
            symbols,
            lambda sym: _process_symbol(
                sym, strategy_cls, params, scan_dt, req.lookback
            ),
            max_pending=workers * 2,
        ):
            try:
                result = fut.result()
                if result is not None:
                    matches.append(result)
            except Exception:
                pass

    matches.sort(key=lambda m: m.strength, reverse=True)

    return ScreenResult(
        strategy=req.strategy,
        scan_date=str(scan_dt),
        total_scanned=len(symbols),
        matches=matches,
        elapsed_seconds=round(time.time() - t0, 2),
    )


def get_factor_defs() -> list[FactorDef]:
    """返回因子元数据（包装 screening.FACTOR_DEFS）。"""
    return [FactorDef(**d) for d in FACTOR_DEFS]


def _score_symbol(symbol: str, scorer: MultiFactorScorer, scan_dt: date, lookback: int):
    """对单只股票做多因子评分，数据不足返回 None。"""
    df = get_store().get_kline(
        symbol,
        freq="day",
        end=scan_dt,
        with_indicators=True,
        tail=lookback,
    )
    if df is None or df.empty:
        return None

    if len(df) < 60:
        return None

    return scorer.score(symbol, df)


def run_scoring(req: ScoreRequest) -> ScoreResult:
    """全市场多因子评分选股。

    读取 list_symbols → ThreadPoolExecutor 并行评分 → 过滤 → 排序 → 取 top_n。
    """
    t0 = time.time()

    try:
        scan_dt = date.fromisoformat(req.scan_date) if req.scan_date else date.today()
    except (ValueError, TypeError):
        scan_dt = date.today()

    config = ScoreConfig(
        weights=req.weights.model_dump(),
        exclude_centipede=req.exclude_centipede,
        min_sandglass=req.min_sandglass,
        min_amount=req.min_amount,
        min_price=req.min_price,
        use_patterns=req.use_patterns,
    )
    scorer = MultiFactorScorer(config)

    symbols = get_store().list_symbols("day")
    if req.max_symbols and req.max_symbols > 0:
        symbols = symbols[: req.max_symbols]

    scores = []
    workers = min(4, max(1, len(symbols)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _, fut in bounded_futures(
            pool,
            symbols,
            lambda sym: _score_symbol(sym, scorer, scan_dt, req.lookback),
            max_pending=workers * 2,
        ):
            try:
                result = fut.result()
                if result is not None:
                    scores.append(result)
            except Exception:
                pass

    total_scanned = len(symbols)
    matched = [s for s in scores if s.passed_filter]
    matched.sort(key=lambda s: s.score, reverse=True)
    top = matched[: req.top_n]

    stocks = [
        ScoredStock(
            symbol=s.symbol,
            score=s.score,
            rating=s.rating,
            factors=FactorScoreItem(
                trend=s.factors.get("trend", 0.0),
                momentum=s.factors.get("momentum", 0.0),
                volume=s.factors.get("volume", 0.0),
                dip=s.factors.get("dip", 0.0),
                risk=s.factors.get("risk", 0.0),
            ),
            reasons=s.reasons,
            warnings=s.warnings,
            signal_date=s.signal_date,
            close=s.close,
            pct_chg=s.pct_chg,
            volume=s.volume,
            amount=s.amount,
            sandglass=s.sandglass,
            wave=s.wave,
            kirin=s.kirin,
        )
        for s in top
    ]

    return ScoreResult(
        scan_date=str(scan_dt),
        total_scanned=total_scanned,
        total_matched=len(matched),
        returned=len(stocks),
        stocks=stocks,
        elapsed_seconds=round(time.time() - t0, 2),
    )
