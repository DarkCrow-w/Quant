from __future__ import annotations

import math

import numpy as np
import pandas as pd

from quant.data.akshare_feed import AKShareFeed
from quant.data.baostock_feed import BaostockFeed
from quant.data.tushare_feed import TuShareFeed
from quant.engine.backtest import BacktestEngine
from quant.execution.simulated import SimulatedBroker
from quant.risk.basic import BasicRiskManager
from quant.strategy.examples.ma_cross import MACrossStrategy
from quant.strategy.examples.vol_kdj_bbi import VolKDJBBIStrategy
from quant.strategy.examples.bbi_kdj_trend import BBIKDJTrendStrategy

from server.models.backtest import (
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
        "cls": MACrossStrategy,
        "display_name": "MA 均线交叉",
        "params_schema": [
            ParamSchema(name="fast_period", type="int", default=5, min=2, max=60, label="快线周期"),
            ParamSchema(name="slow_period", type="int", default=20, min=5, max=120, label="慢线周期"),
        ],
    },
    "vol_kdj_bbi": {
        "cls": VolKDJBBIStrategy,
        "display_name": "量价KDJ+BBI",
        "params_schema": [
            ParamSchema(name="kdj_period", type="int", default=9, min=5, max=21, label="KDJ周期"),
            ParamSchema(name="j_threshold", type="int", default=10, min=0, max=30, label="J值阈值"),
            ParamSchema(name="ma_period", type="int", default=20, min=10, max=60, label="均线周期(N型)"),
            ParamSchema(name="vol_lookback", type="int", default=20, min=10, max=60, label="量能观察期"),
            ParamSchema(name="vol_ratio", type="float", default=1.5, min=1.1, max=3.0, label="量比(涨/跌)"),
            ParamSchema(name="bbi_confirm_days", type="int", default=2, min=1, max=5, label="BBI确认天数"),
            ParamSchema(name="stop_loss_pct", type="float", default=0.05, min=0.02, max=0.15, label="止损百分比"),
        ],
    },
    "bbi_kdj_trend": {
        "cls": BBIKDJTrendStrategy,
        "display_name": "BBI趋势+KDJ择时",
        "params_schema": [
            ParamSchema(name="kdj_period", type="int", default=9, min=5, max=21, label="KDJ周期"),
            ParamSchema(name="j_buy_threshold", type="int", default=30, min=10, max=50, label="J买入阈值"),
            ParamSchema(name="j_sell_threshold", type="int", default=80, min=60, max=95, label="J卖出阈值"),
            ParamSchema(name="bbi_trend_days", type="int", default=3, min=1, max=10, label="BBI趋势天数"),
            ParamSchema(name="bbi_break_days", type="int", default=2, min=1, max=5, label="BBI跌破天数"),
            ParamSchema(name="vol_ratio", type="float", default=1.0, min=0.8, max=2.0, label="放量倍数"),
            ParamSchema(name="atr_trail_mult", type="float", default=2.5, min=1.5, max=4.0, label="追踪止盈ATR倍"),
            ParamSchema(name="stop_loss_pct", type="float", default=0.05, min=0.02, max=0.15, label="硬止损%"),
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
    registry_entry = STRATEGY_REGISTRY.get(req.strategy)
    if registry_entry is None:
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

    # Build kline data from feed
    kline_data: dict[str, list[KlineBar]] = {}
    for sym in req.symbols:
        if sym in feed._data:
            df = feed._data[sym]
            bars = []
            for _, r in df.iterrows():
                bars.append(KlineBar(
                    dt=str(r["dt"]),
                    open=round(float(r["open"]), 4),
                    high=round(float(r["high"]), 4),
                    low=round(float(r["low"]), 4),
                    close=round(float(r["close"]), 4),
                    volume=float(r["volume"]),
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
