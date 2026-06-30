from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Some Windows recovery environments have pandas/numpy available outside the
# project venv but miss pure-Python packages such as loguru. Appending keeps the
# active interpreter's compiled packages first while making pure packages usable.
VENV_SITE = ROOT / ".venv" / "Lib" / "site-packages"
if VENV_SITE.exists():
    sys.path.append(str(VENV_SITE))

try:
    from loguru import logger

    logger.remove()
except Exception:
    pass

from quant.core.bar import Bar
from quant.core.events import MarketEvent
from quant.data.base import DataFeed
from quant.data.indicators import compute
from quant.engine.backtest import BacktestEngine
from quant.execution.simulated import SimulatedBroker
from quant.risk.basic import BasicRiskManager
from quant.strategy.examples.dip_buy import DipBuyStrategy, SwingDipBuyStrategy
from scripts.seed_demo_data import DEMO_SYMBOLS, build_demo_kline, business_days


@dataclass
class CandidateResult:
    strategy: str
    params: dict[str, Any]
    max_position_pct: float
    final_equity: float
    total_return: float
    annual_return: float
    max_drawdown: float
    trade_count: int
    score: float


@dataclass
class ResearchData:
    frames: dict[str, pd.DataFrame]
    dates: list[date]
    index_by_date: dict[str, dict[date, int]]


class MemoryFeed(DataFeed):
    def __init__(self, data: dict[str, pd.DataFrame]) -> None:
        self._data = data
        self._symbols: list[str] = []
        self._index = 0
        self._dates = sorted({row.dt for df in data.values() for row in df.itertuples(index=False)})
        self._history: dict[str, list[Bar]] = {symbol: [] for symbol in data}
        self._rows: dict[str, dict[date, Bar]] = {}
        for symbol, df in data.items():
            rows: dict[date, Bar] = {}
            for row in df.itertuples(index=False):
                dt = pd.to_datetime(row.dt).date()
                rows[dt] = Bar(
                    symbol=symbol,
                    dt=dt,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=float(row.volume),
                    amount=float(getattr(row, "amount", 0.0)),
                )
            self._rows[symbol] = rows

    def subscribe(self, symbols: list[str]) -> None:
        self._symbols = [symbol for symbol in symbols if symbol in self._data]

    def has_more(self) -> bool:
        return self._index < len(self._dates)

    def update(self) -> MarketEvent | None:
        if not self.has_more():
            return None
        dt = self._dates[self._index]
        self._index += 1
        bars = {}
        for symbol in self._symbols:
            bar = self._rows[symbol].get(dt)
            if bar is None:
                continue
            self._history[symbol].append(bar)
            bars[symbol] = bar
        return MarketEvent(dt=dt, bars=bars) if bars else self.update()

    def get_latest_bars(self, symbol: str, n: int = 1) -> list[Bar]:
        bars = self._history.get(symbol, [])
        return bars[-n:] if n <= len(bars) else list(bars)


def demo_data(symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    days = business_days(start, end)
    return {symbol: build_demo_kline(symbol, days) for symbol in symbols}


def metrics(equity_curve: list[dict], initial_cash: float) -> tuple[float, float, float, float]:
    if not equity_curve:
        return initial_cash, 0.0, 0.0, 0.0
    eq = pd.DataFrame(equity_curve)
    eq["dt"] = pd.to_datetime(eq["dt"])
    eq = eq.sort_values("dt")
    final_equity = float(eq["equity"].iat[-1])
    total_return = final_equity / initial_cash - 1
    days = max((eq["dt"].iat[-1] - eq["dt"].iat[0]).days, 1)
    annual_return = (1 + total_return) ** (365 / days) - 1 if total_return > -1 else -1
    peak = eq["equity"].cummax()
    max_drawdown = float(((eq["equity"] - peak) / peak).min())
    return final_equity, total_return, annual_return, max_drawdown


def run_candidate(
    strategy_name: str,
    params: dict[str, Any],
    data: dict[str, pd.DataFrame],
    symbols: list[str],
    initial_cash: float,
    max_position_pct: float,
) -> CandidateResult:
    strategy_cls = SwingDipBuyStrategy if strategy_name == "swing_dip_buy" else DipBuyStrategy
    feed = MemoryFeed(data)
    feed.subscribe(symbols)
    engine = BacktestEngine(
        feed=feed,
        strategy=strategy_cls(params=params),
        risk_manager=BasicRiskManager(max_position_pct=max_position_pct, max_drawdown=0.35),
        broker=SimulatedBroker(commission_rate=0.00025, min_commission=5.0),
        initial_cash=initial_cash,
    )
    engine.run()
    final_equity, total_return, annual_return, max_drawdown = metrics(engine.equity_curve, initial_cash)
    trades = engine.get_trades()
    trade_count = 0 if trades.empty else len(trades)
    # Favor high annual return, but penalize large drawdown and no-trade overfit.
    trade_penalty = 0.2 if trade_count < 2 else 0.0
    score = annual_return + max_drawdown * 0.6 - trade_penalty
    return CandidateResult(
        strategy=strategy_name,
        params=params,
        max_position_pct=max_position_pct,
        final_equity=round(final_equity, 2),
        total_return=round(total_return, 6),
        annual_return=round(annual_return, 6),
        max_drawdown=round(max_drawdown, 6),
        trade_count=trade_count,
        score=round(score, 6),
    )


def candidate_grid() -> list[dict[str, Any]]:
    keys = [
        "entry_score",
        "kdj_j_threshold",
        "rsi3_threshold",
        "rsi6_threshold",
        "bbi_lower_band_pct",
        "panic_volume_ratio",
        "take_profit_pct",
        "trailing_stop_pct",
    ]
    values = [
        [4, 3, 5, 6],
        [18, 25, 35, 45],
        [28, 35, 42, 50],
        [32, 40, 48, 56],
        [0.08, 0.12, 0.16, 0.22],
        [1.3, 1.6, 2.0],
        [0.24, 0.36, 0.50, 0.16],
        [0.06, 0.10, 0.16, 0.24],
    ]
    grid = [dict(zip(keys, item)) for item in product(*values)]
    for item in grid:
        item["second_profit_pct"] = max(0.36, item["take_profit_pct"] * 1.8)
    return grid


def _rsi3_series(close: pd.Series) -> pd.Series:
    diffs = close.diff().fillna(0.0)
    ups = diffs.clip(lower=0)
    dns = (-diffs).clip(lower=0)
    au = ups.ewm(alpha=1 / 3, adjust=False).mean()
    ad = dns.ewm(alpha=1 / 3, adjust=False).mean()
    rs = au / ad.replace(0, math.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def prepare_research_data(data: dict[str, pd.DataFrame]) -> ResearchData:
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, raw in data.items():
        df = raw.copy()
        kdj = compute("KDJ", df)
        rsi = compute("RSI", df)
        macd = compute("MACD", df)
        bbi = compute("BBI", df)
        df["kdj_j"] = kdj["kdj_j"]
        df["kdj_j_prev"] = kdj["kdj_j"].shift(1)
        df["rsi3"] = _rsi3_series(df["close"])
        df["rsi6"] = rsi["rsi6"]
        df["dif"] = macd["dif"]
        df["dif_prev"] = macd["dif"].shift(1)
        df["bbi"] = bbi["bbi"]
        df["ma20"] = df["close"].rolling(20, min_periods=1).mean()
        df["ma60"] = df["close"].rolling(60, min_periods=1).mean()
        df["avg10_vol"] = df["volume"].shift(1).rolling(10, min_periods=1).mean()
        df["prev_close"] = df["close"].shift(1)
        df["prev_low"] = df["low"].shift(1)
        prepared[symbol] = df.reset_index(drop=True)
    dates = sorted({pd.to_datetime(row.dt).date() for df in prepared.values() for row in df.itertuples(index=False)})
    index_by_date = {
        symbol: {pd.to_datetime(row.dt).date(): i for i, row in enumerate(df.itertuples(index=False))}
        for symbol, df in prepared.items()
    }
    return ResearchData(frames=prepared, dates=dates, index_by_date=index_by_date)


def _fast_score(df: pd.DataFrame, idx: int, params: dict[str, Any]) -> int:
    row = df.iloc[idx]
    prev = df.iloc[idx - 1]
    lookback = int(params.get("lookback", 30))
    start = max(0, idx - lookback + 1)
    window = df.iloc[start: idx + 1]
    score = 0

    if (
        row.kdj_j <= params["kdj_j_threshold"]
        or row.rsi3 <= params["rsi3_threshold"]
        or row.rsi6 <= params["rsi6_threshold"]
    ):
        score += 2

    if row.bbi and not pd.isna(row.bbi):
        bbi_gap = (row.close - row.bbi) / row.bbi
        if -params["bbi_lower_band_pct"] <= bbi_gap <= params.get("bbi_upper_band_pct", 0.03):
            score += 2

    median_vol = float(window["volume"].median())
    recent = window.tail(min(12, len(window)))
    if median_vol > 0 and bool(
        ((recent["close"] < recent["open"]) & (recent["volume"] >= median_vol * params["panic_volume_ratio"])).any()
    ):
        score += 1

    avg10_vol = float(row.avg10_vol) if not pd.isna(row.avg10_vol) else median_vol
    if avg10_vol > 0 and row.volume <= avg10_vol * params.get("dryup_ratio", 0.85):
        score += 1

    reversal = (
        row.close > row.open
        and row.close >= prev.close * (1 + params.get("reversal_pct", 0.003))
    ) or (
        row.low < prev.low and row.close > prev.close
    )
    if reversal:
        score += 2

    trend_floor_pct = params.get("trend_floor_pct", 0.08)
    if (
        not pd.isna(row.ma20)
        and not pd.isna(row.ma60)
        and row.close >= row.ma60 * (1 - trend_floor_pct)
        and row.ma20 >= row.ma60 * (1 - trend_floor_pct)
    ):
        score += 1

    if (
        (not pd.isna(row.dif_prev) and row.dif > row.dif_prev)
        or (not pd.isna(row.kdj_j_prev) and row.kdj_j > row.kdj_j_prev)
    ):
        score += 1

    return score


def run_research_candidate(
    params: dict[str, Any],
    research: ResearchData,
    initial_cash: float,
    max_position_pct: float,
) -> CandidateResult:
    prepared = research.frames
    dates = research.dates
    index_by_date = research.index_by_date
    cash = initial_cash
    positions: dict[str, dict[str, float | bool | int]] = {}
    pending: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    trade_count = 0

    for current in dates:
        prices: dict[str, float] = {}
        for symbol, df in prepared.items():
            idx = index_by_date[symbol].get(current)
            if idx is not None:
                prices[symbol] = float(df.at[idx, "close"])

        for order in pending:
            symbol = order["symbol"]
            idx = index_by_date[symbol].get(current)
            if idx is None:
                continue
            fill_price = float(prepared[symbol].at[idx, "open"])
            qty = int(order["qty"])
            commission = max(fill_price * qty * 0.00025, 5.0)
            if order["side"] == "BUY":
                cash -= fill_price * qty + commission
                positions[symbol] = {
                    "qty": float(qty),
                    "entry": fill_price,
                    "peak": fill_price,
                    "half_sold": False,
                    "below_bbi": 0,
                    "exit_pending": False,
                }
            else:
                pos = positions.get(symbol)
                if not pos:
                    continue
                sell_qty = min(qty, int(pos["qty"]))
                cash += fill_price * sell_qty - commission
                pos["qty"] = float(pos["qty"]) - sell_qty
                if pos["qty"] <= 0:
                    positions.pop(symbol, None)
            trade_count += 1
        pending = []

        equity = cash + sum(float(pos["qty"]) * prices.get(symbol, float(pos["entry"])) for symbol, pos in positions.items())
        equity_curve.append({"dt": current, "equity": equity})

        for symbol, df in prepared.items():
            idx = index_by_date[symbol].get(current)
            if idx is None or idx < 80:
                continue
            row = df.iloc[idx]
            if pd.isna(row.bbi) or row.bbi <= 0:
                continue

            pos = positions.get(symbol)
            if pos:
                if bool(pos.get("exit_pending", False)):
                    continue
                entry = float(pos["entry"])
                peak = max(float(pos["peak"]), float(row.high))
                pos["peak"] = peak
                qty = int(pos["qty"])
                if row.close <= entry * (1 - params.get("stop_loss_pct", 0.055)):
                    pending.append({"side": "SELL", "symbol": symbol, "qty": qty})
                    pos["exit_pending"] = True
                    continue

                below_bbi = row.close < row.bbi * (1 - params.get("bbi_exit_band_pct", 0.015))
                pos["below_bbi"] = int(pos["below_bbi"]) + 1 if below_bbi else 0
                if int(pos["below_bbi"]) >= params.get("bbi_break_days", 2):
                    pending.append({"side": "SELL", "symbol": symbol, "qty": qty})
                    pos["exit_pending"] = True
                    continue

                gain = (row.close - entry) / entry if entry > 0 else 0
                draw_from_peak = (peak - row.close) / peak if peak > 0 else 0
                if not bool(pos["half_sold"]) and gain >= params["take_profit_pct"]:
                    sell_qty = (int(qty * 0.5) // 100) * 100 or qty
                    pending.append({"side": "SELL", "symbol": symbol, "qty": sell_qty})
                    pos["half_sold"] = True
                elif bool(pos["half_sold"]) and (
                    gain >= params.get("second_profit_pct", 0.22)
                    or draw_from_peak >= params["trailing_stop_pct"]
                ):
                    pending.append({"side": "SELL", "symbol": symbol, "qty": qty})
                    pos["exit_pending"] = True
                continue

            score = _fast_score(df, idx, params)
            if score < params["entry_score"]:
                continue
            strength = min(1.0, 0.65 + score * 0.05)
            qty = int(equity * max_position_pct * strength / row.close)
            qty = (qty // 100) * 100
            if qty > 0:
                pending.append({"side": "BUY", "symbol": symbol, "qty": qty})

    final_equity, total_return, annual_return, max_drawdown = metrics(equity_curve, initial_cash)
    trade_penalty = 0.2 if trade_count < 2 else 0.0
    score = annual_return + max_drawdown * 0.6 - trade_penalty
    return CandidateResult(
        strategy="swing_dip_buy",
        params=params,
        max_position_pct=max_position_pct,
        final_equity=round(final_equity, 2),
        total_return=round(total_return, 6),
        annual_return=round(annual_return, 6),
        max_drawdown=round(max_drawdown, 6),
        trade_count=trade_count,
        score=round(score, 6),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize swing_dip_buy on deterministic demo data.")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--target-annual", type=float, default=1.0)
    parser.add_argument("--max-candidates", type=int, default=0, help="Limit grid size for quick smoke runs.")
    args = parser.parse_args()

    symbols = list(DEMO_SYMBOLS)
    data = demo_data(symbols, date(2023, 1, 1), date(2026, 6, 17))
    initial_cash = 1_000_000.0
    baseline_params = {
        "ma_period": 20,
        "vol_lookback": 20,
        "volume_climax_ratio": 2.0,
        "kdj_j_threshold": 10,
        "rsi3_threshold": 20,
    }
    baseline = run_candidate("dip_buy", baseline_params, data, symbols, initial_cash, 0.3)

    prepared = prepare_research_data(data)
    grid = candidate_grid()
    if args.max_candidates > 0:
        grid = grid[: args.max_candidates]
    results = []
    for max_position_pct in (0.35, 0.5, 0.7, 1.0, 1.3, 1.6, 2.0, 2.5, 3.0):
        for params in grid:
            results.append(run_research_candidate(params, prepared, initial_cash, max_position_pct))
    ranked = sorted(results, key=lambda item: item.score, reverse=True)
    reached = [item for item in ranked if item.annual_return >= args.target_annual]
    report = {
        "status": "ok",
        "data": {
            "symbols": len(symbols),
            "start": "2023-01-01",
            "end": "2026-06-17",
        },
        "baseline": baseline.__dict__,
        "searched": len(results),
        "target_annual": args.target_annual,
        "target_reached": bool(reached),
        "best": ranked[0].__dict__ if ranked else None,
        "top": [item.__dict__ for item in ranked[: args.top]],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ranked else 1


if __name__ == "__main__":
    raise SystemExit(main())
