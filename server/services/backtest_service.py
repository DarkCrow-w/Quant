from __future__ import annotations

from collections.abc import Callable
from itertools import product
import math
from typing import Any

import numpy as np
import pandas as pd

from quant.data.akshare_feed import AKShareFeed
from quant.data.baostock_feed import BaostockFeed
from quant.data.tushare_feed import TuShareFeed
from quant.engine.backtest import BacktestEngine
from quant.execution.simulated import SimulatedBroker
from quant.risk.basic import BasicRiskManager
from quant.strategy.registry import BASIC_STRATEGY_CLASSES
from server.services.composite_backtest_strategy import CompositeRuleStrategy
from server.services.factor_strategy_service import FactorStrategyStore

from server.models.backtest import (
    BacktestGridItem,
    BacktestGridRequest,
    BacktestGridResult,
    BacktestRequest,
    BacktestResult,
    EquityPoint,
    KlineBar,
    PerformanceMetrics,
    TradeRecord,
)
from server.models.market import ParamSchema, StrategyInfo

STRATEGY_REGISTRY: dict[str, dict] = {
    "ma_cross": {
        "cls": BASIC_STRATEGY_CLASSES["ma_cross"],
        "display_name": "MA 均线交叉",
        "params_schema": [
            ParamSchema(name="fast_period", type="int", default=5, min=2, max=60, label="快线周期"),
            ParamSchema(name="slow_period", type="int", default=20, min=5, max=120, label="慢线周期"),
        ],
    },
    "vol_kdj_bbi": {
        "cls": BASIC_STRATEGY_CLASSES["vol_kdj_bbi"],
        "display_name": "量价KDJ+BBI",
        "params_schema": [
            ParamSchema(name="kdj_period", type="int", default=9, min=5, max=21, label="KDJ周期"),
            ParamSchema(name="j_threshold", type="int", default=10, min=0, max=30, label="J值阈值"),
            ParamSchema(name="ma_period", type="int", default=20, min=10, max=60, label="均线周期"),
            ParamSchema(name="vol_lookback", type="int", default=20, min=10, max=60, label="量能观察期"),
            ParamSchema(name="vol_ratio", type="float", default=1.5, min=1.1, max=3.0, label="量比阈值"),
            ParamSchema(name="bbi_confirm_days", type="int", default=2, min=1, max=5, label="BBI确认天数"),
            ParamSchema(name="stop_loss_pct", type="float", default=0.05, min=0.02, max=0.15, label="止损百分比"),
        ],
    },
    "bbi_kdj_trend": {
        "cls": BASIC_STRATEGY_CLASSES["bbi_kdj_trend"],
        "display_name": "BBI趋势+KDJ择时",
        "params_schema": [
            ParamSchema(name="kdj_period", type="int", default=9, min=5, max=21, label="KDJ周期"),
            ParamSchema(name="j_buy_threshold", type="int", default=30, min=10, max=50, label="J买入阈值"),
            ParamSchema(name="j_sell_threshold", type="int", default=80, min=60, max=95, label="J卖出阈值"),
            ParamSchema(name="bbi_trend_days", type="int", default=3, min=1, max=10, label="BBI趋势天数"),
            ParamSchema(name="bbi_break_days", type="int", default=2, min=1, max=5, label="BBI跌破天数"),
            ParamSchema(name="vol_ratio", type="float", default=1.0, min=0.8, max=2.0, label="放量倍数"),
            ParamSchema(name="atr_trail_mult", type="float", default=2.5, min=1.5, max=4.0, label="追踪止盈ATR倍数"),
            ParamSchema(name="stop_loss_pct", type="float", default=0.05, min=0.02, max=0.15, label="硬止损"),
        ],
    },
    "dip_buy": {
        "cls": BASIC_STRATEGY_CLASSES["dip_buy"],
        "display_name": "抄底（RSI+KDJ+VOL+BBI）",
        "params_schema": [
            ParamSchema(name="ma_period", type="int", default=20, min=10, max=60, label="均线周期"),
            ParamSchema(name="vol_lookback", type="int", default=20, min=10, max=40, label="量能观察期"),
            ParamSchema(name="volume_climax_ratio", type="float", default=2.0, min=1.5, max=6.0, label="天量倍数"),
            ParamSchema(name="kdj_j_threshold", type="int", default=10, min=0, max=30, label="KDJ-J阈值"),
            ParamSchema(name="rsi3_threshold", type="int", default=20, min=10, max=40, label="RSI(3)阈值"),
            ParamSchema(name="bbi_band_pct", type="float", default=0.05, min=0.02, max=0.10, label="BBI带宽百分比"),
            ParamSchema(name="doji_shrink_ratio", type="float", default=0.85, min=0.50, max=1.00, label="缩量倍数"),
            ParamSchema(name="stop_loss_pct", type="float", default=0.04, min=0.02, max=0.08, label="止损百分比"),
            ParamSchema(name="bbi_confirm_days", type="int", default=2, min=1, max=5, label="BBI确认天数"),
            ParamSchema(name="n_form_close_pct", type="float", default=0.05, min=0.02, max=0.10, label="N形前低接近度"),
        ],
    },
    "swing_dip_buy": {
        "cls": BASIC_STRATEGY_CLASSES["swing_dip_buy"],
        "display_name": "波段抄底（KDJ+RSI+VOL+BBI）",
        "params_schema": [
            ParamSchema(name="lookback", type="int", default=30, min=20, max=80, label="观察窗口"),
            ParamSchema(name="entry_score", type="int", default=4, min=3, max=9, label="入场评分"),
            ParamSchema(name="kdj_j_threshold", type="int", default=18, min=5, max=35, label="KDJ-J低位阈值"),
            ParamSchema(name="rsi3_threshold", type="int", default=28, min=10, max=45, label="RSI(3)低位阈值"),
            ParamSchema(name="rsi6_threshold", type="int", default=32, min=15, max=50, label="RSI(6)低位阈值"),
            ParamSchema(name="bbi_lower_band_pct", type="float", default=0.10, min=0.03, max=0.18, label="BBI下方低吸带"),
            ParamSchema(name="bbi_upper_band_pct", type="float", default=0.03, min=0.00, max=0.08, label="BBI上方容忍带"),
            ParamSchema(name="panic_volume_ratio", type="float", default=1.8, min=1.2, max=4.0, label="恐慌量倍数"),
            ParamSchema(name="dryup_ratio", type="float", default=0.85, min=0.50, max=1.10, label="缩量倍数"),
            ParamSchema(name="stop_loss_pct", type="float", default=0.055, min=0.02, max=0.12, label="硬止损"),
            ParamSchema(name="take_profit_pct", type="float", default=0.36, min=0.05, max=0.60, label="首段止盈"),
            ParamSchema(name="second_profit_pct", type="float", default=0.648, min=0.10, max=1.00, label="二段止盈"),
            ParamSchema(name="trailing_stop_pct", type="float", default=0.08, min=0.03, max=0.20, label="追踪止盈回撤"),
            ParamSchema(name="bbi_break_days", type="int", default=2, min=1, max=5, label="BBI破位天数"),
        ],
    },
}


def get_strategy_list() -> list[StrategyInfo]:
    return [
        StrategyInfo(
            name=name,
            display_name=info["display_name"],
            params_schema=info["params_schema"],
        )
        for name, info in STRATEGY_REGISTRY.items()
    ]


def _compute_enhanced_metrics(
    trades_df: pd.DataFrame,
    equity_curve: list[dict],
    initial_cash: float,
    total_commission: float,
) -> PerformanceMetrics:
    if not equity_curve:
        return PerformanceMetrics(
            initial_cash=initial_cash,
            final_equity=initial_cash,
            total_return=0,
            annual_return=0,
            max_drawdown=0,
            trade_count=0,
            total_commission=total_commission,
        )

    eq = pd.DataFrame(equity_curve)
    eq["dt"] = pd.to_datetime(eq["dt"])
    eq = eq.set_index("dt").sort_index()

    final = float(eq["equity"].iloc[-1])
    ret = (final - initial_cash) / initial_cash
    peak = eq["equity"].cummax()
    dd = (eq["equity"] - peak) / peak
    max_dd = float(dd.min())
    days = (eq.index[-1] - eq.index[0]).days
    ann_ret = (1 + ret) ** (365 / max(days, 1)) - 1 if days > 0 else 0.0

    # Sharpe ratio
    daily_returns = eq["equity"].pct_change().dropna()
    sharpe = None
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = round(float(daily_returns.mean() / daily_returns.std() * math.sqrt(252)), 4)

    # Win rate & profit/loss ratio — pair BUY/SELL per symbol
    win_rate = None
    pl_ratio = None
    if not trades_df.empty:
        profits: list[float] = []
        open_buys: dict[str, list[dict]] = {}
        for _, row in trades_df.iterrows():
            sym = row["symbol"]
            if row["side"] == "BUY":
                open_buys.setdefault(sym, []).append(row.to_dict())
            elif row["side"] == "SELL" and sym in open_buys and open_buys[sym]:
                buy = open_buys[sym].pop(0)
                pnl = (row["price"] - buy["price"]) * row["qty"] - row["commission"] - buy["commission"]
                profits.append(pnl)

        if profits:
            wins = [p for p in profits if p > 0]
            losses = [p for p in profits if p <= 0]
            win_rate = round(len(wins) / len(profits), 4)
            avg_win = np.mean(wins) if wins else 0
            avg_loss = abs(np.mean(losses)) if losses else 0
            pl_ratio = round(float(avg_win / avg_loss), 4) if avg_loss > 0 else None

    return PerformanceMetrics(
        initial_cash=initial_cash,
        final_equity=round(final, 2),
        total_return=round(ret, 6),
        annual_return=round(ann_ret, 6),
        max_drawdown=round(max_dd, 6),
        trade_count=len(trades_df),
        total_commission=round(total_commission, 2),
        win_rate=win_rate,
        sharpe_ratio=sharpe,
        profit_loss_ratio=pl_ratio,
    )


def run_backtest(req: BacktestRequest) -> BacktestResult:
    is_composite = req.strategy.startswith("composite:")
    registry_entry = STRATEGY_REGISTRY.get(req.strategy)
    if registry_entry is None and not is_composite:
        raise ValueError(f"Unknown strategy: {req.strategy}")

    # Build components — fallback 链 TuShare -> Baostock -> AKShare
    feed = None
    last_err: Exception | None = None
    for cls in (TuShareFeed, BaostockFeed, AKShareFeed):
        try:
            feed = cls(
                start_date=req.start_date,
                end_date=req.end_date,
                use_cache=True,
            )
            feed.subscribe(req.symbols)
            break
        except Exception as e:
            last_err = e
            feed = None
    if feed is None:
        raise RuntimeError(f"All data sources failed; last error: {last_err}")

    if is_composite:
        strategy_id = req.strategy.split(":", 1)[1]
        saved = FactorStrategyStore().get(strategy_id)
        if saved is None:
            raise ValueError(f"Unknown strategy: {strategy_id}")
        strategy = CompositeRuleStrategy(saved, params=req.strategy_params or {})
    else:
        strategy_cls = registry_entry["cls"]
        strategy = strategy_cls(params=req.strategy_params or {})

    risk_manager = BasicRiskManager(
        max_position_pct=req.max_position_pct,
        max_drawdown=req.max_drawdown,
    )
    broker = SimulatedBroker(
        commission_rate=req.commission_rate,
        min_commission=5.0,
    )

    engine = BacktestEngine(
        feed=feed,
        strategy=strategy,
        risk_manager=risk_manager,
        broker=broker,
        initial_cash=req.initial_cash,
    )

    engine.run()
    trades_df = engine.get_trades()

    # Build equity curve
    equity_curve = [
        {"dt": str(pt["dt"]), "equity": pt["equity"]}
        for pt in engine.equity_curve
    ]

    # Build trades list
    trades = []
    if not trades_df.empty:
        for _, row in trades_df.iterrows():
            trades.append(TradeRecord(
                dt=str(row["dt"]),
                symbol=row["symbol"],
                side=row["side"],
                qty=int(row["qty"]),
                price=round(float(row["price"]), 4),
                commission=round(float(row["commission"]), 2),
            ))

    # Build kline data from feed (uses DataFeed.get_dataframe — no private reach-in)
    kline_data: dict[str, list[KlineBar]] = {}
    for sym in req.symbols:
        df = feed.get_dataframe(sym)
        if df.empty:
            continue
        bars = []
        for r in df.itertuples(index=False):
            bars.append(KlineBar(
                dt=str(r.dt),
                open=round(float(r.open), 4),
                high=round(float(r.high), 4),
                low=round(float(r.low), 4),
                close=round(float(r.close), 4),
                volume=float(r.volume),
            ))
        kline_data[sym] = bars

    metrics = _compute_enhanced_metrics(
        trades_df, equity_curve, req.initial_cash, engine.portfolio.total_commission
    )

    return BacktestResult(
        metrics=metrics,
        equity_curve=[EquityPoint(**pt) for pt in equity_curve],
        trades=trades,
        kline_data=kline_data,
    )


def run_backtest_grid(
    req: BacktestGridRequest,
    runner: Callable[[BacktestRequest], BacktestResult] = run_backtest,
    save_result: Callable[[BacktestRequest, BacktestResult], str | None] | None = None,
) -> BacktestGridResult:
    """Run a deterministic parameter grid and keep failed combinations visible."""
    requests = _expand_grid_requests(req)
    results: list[BacktestGridItem] = []
    for item_request in requests:
        try:
            result = runner(item_request)
            run_id = save_result(item_request, result) if save_result else None
            results.append(
                BacktestGridItem(
                    status="completed",
                    strategy_params=item_request.strategy_params,
                    request=item_request,
                    metrics=result.metrics,
                    run_id=run_id,
                )
            )
        except Exception as e:
            results.append(
                BacktestGridItem(
                    status="failed",
                    strategy_params=item_request.strategy_params,
                    request=item_request,
                    error=str(e),
                )
            )

    completed = sorted(
        [item for item in results if item.status == "completed"],
        key=lambda item: _grid_sort_value(item, req.sort_by),
        reverse=req.sort_order == "desc",
    )
    failed = [item for item in results if item.status == "failed"]
    ordered = completed + failed
    return BacktestGridResult(
        requested=len(requests),
        completed=len(completed),
        failed=len(failed),
        sort_by=req.sort_by,
        sort_order=req.sort_order,
        best=completed[0] if completed else None,
        results=ordered,
    )


def _expand_grid_requests(req: BacktestGridRequest) -> list[BacktestRequest]:
    grid = {key: values for key, values in req.parameters.items() if values}
    empty_keys = [key for key, values in req.parameters.items() if not values]
    if empty_keys:
        raise ValueError(f"Grid parameters cannot be empty: {', '.join(empty_keys)}")

    if not grid:
        return [req.base]

    keys = sorted(grid.keys())
    combinations = list(product(*(grid[key] for key in keys)))
    if len(combinations) > req.max_runs:
        raise ValueError(
            f"Grid expands to {len(combinations)} runs, exceeding max_runs={req.max_runs}"
        )

    requests: list[BacktestRequest] = []
    for values in combinations:
        params = dict(req.base.strategy_params or {})
        params.update({key: value for key, value in zip(keys, values, strict=True)})
        requests.append(_copy_backtest_request(req.base, strategy_params=params))
    return requests


def _grid_sort_value(item: BacktestGridItem, sort_by: str) -> Any:
    if item.metrics is None:
        return float("-inf")
    value = getattr(item.metrics, sort_by)
    if value is None:
        return float("-inf")
    return value


def _copy_backtest_request(req: BacktestRequest, **updates) -> BacktestRequest:
    if hasattr(req, "model_copy"):
        return req.model_copy(update=updates)
    return req.copy(update=updates)
