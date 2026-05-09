#!/usr/bin/env python3
"""
回测入口。用法：

  python run_backtest.py                          # 默认: 茅台 MA交叉 2023~2024
  python run_backtest.py -s 600519 000858         # 多只股票
  python run_backtest.py -s 600519 --fast 10 --slow 30
  python run_backtest.py --start 2022-01-01 --end 2024-06-30
  python run_backtest.py -c configs/backtest_ma_cross.yaml   # 用YAML配置
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from loguru import logger

from quant.data.akshare_feed import AKShareFeed
from quant.data.baostock_feed import BaostockFeed
from quant.data.csv_feed import CSVFeed
from quant.data.tdx_feed import TDXFeed
from quant.data.tushare_feed import TuShareFeed
from quant.engine.backtest import BacktestEngine
from quant.execution.simulated import SimulatedBroker
from quant.risk.basic import BasicRiskManager
from quant.strategy.examples.ma_cross import MACrossStrategy

STRATEGY_MAP = {
    "ma_cross": MACrossStrategy,
}


def parse_args():
    p = argparse.ArgumentParser(description="量化回测")
    p.add_argument("-c", "--config", help="YAML 配置文件路径")
    p.add_argument("-s", "--symbols", nargs="+", default=["600519"], help="股票代码 (默认: 600519)")
    p.add_argument("--start", default="2023-01-01", help="开始日期 (默认: 2023-01-01)")
    p.add_argument("--end", default="2024-12-31", help="结束日期 (默认: 2024-12-31)")
    p.add_argument("--strategy", default="ma_cross", help="策略名 (默认: ma_cross)")
    p.add_argument("--fast", type=int, default=5, help="快线周期 (默认: 5)")
    p.add_argument("--slow", type=int, default=20, help="慢线周期 (默认: 20)")
    p.add_argument("--cash", type=float, default=1_000_000, help="初始资金 (默认: 100万)")
    p.add_argument("--pos-pct", type=float, default=0.3, help="单票最大仓位 (默认: 0.3)")
    p.add_argument("--no-report", action="store_true", help="不生成HTML报告")
    return p.parse_args()


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_feed(cfg: dict):
    source = cfg["data"]["source"]
    common = dict(
        start_date=cfg["data"]["start_date"],
        end_date=cfg["data"]["end_date"],
        use_cache=cfg["data"].get("use_cache", True),
    )
    if source == "akshare":
        feed = AKShareFeed(**common)
    elif source == "baostock":
        feed = BaostockFeed(**common)
    elif source == "tdx":
        feed = TDXFeed(**common)
    elif source == "tushare":
        feed = TuShareFeed(**common)
    elif source == "csv":
        feed = CSVFeed(csv_dir=cfg["data"]["csv_dir"])
    else:
        raise ValueError(f"Unknown data source: {source}")
    feed.subscribe(cfg["data"]["symbols"])
    return feed


def build_config_from_args(args) -> dict:
    return {
        "data": {
            "source": "akshare",
            "symbols": args.symbols,
            "start_date": args.start,
            "end_date": args.end,
            "use_cache": True,
        },
        "strategy": {
            "name": args.strategy,
            "params": {"fast_period": args.fast, "slow_period": args.slow},
        },
        "risk": {"max_position_pct": args.pos_pct, "max_drawdown": 0.2},
        "broker": {"commission_rate": 0.00025, "min_commission": 5.0},
        "engine": {"initial_cash": args.cash},
        "report": {"output": "results/report.html"},
    }


def main() -> None:
    args = parse_args()

    if args.config:
        cfg = load_config(args.config)
    else:
        cfg = build_config_from_args(args)

    feed = build_feed(cfg)

    strat_name = cfg["strategy"]["name"]
    strat_cls = STRATEGY_MAP.get(strat_name)
    if strat_cls is None:
        raise ValueError(f"Unknown strategy: {strat_name}")
    strategy = strat_cls(params=cfg["strategy"].get("params", {}))

    risk_cfg = cfg.get("risk", {})
    risk_manager = BasicRiskManager(
        max_position_pct=risk_cfg.get("max_position_pct", 0.3),
        max_drawdown=risk_cfg.get("max_drawdown", 0.2),
    )

    broker_cfg = cfg.get("broker", {})
    broker = SimulatedBroker(
        commission_rate=broker_cfg.get("commission_rate", 0.00025),
        min_commission=broker_cfg.get("min_commission", 5.0),
    )

    initial_cash = cfg.get("engine", {}).get("initial_cash", 1_000_000)

    engine = BacktestEngine(
        feed=feed, strategy=strategy, risk_manager=risk_manager,
        broker=broker, initial_cash=initial_cash,
    )
    eq = engine.run()
    engine.print_summary(eq)

    trades = engine.get_trades()
    if not trades.empty:
        Path("results").mkdir(exist_ok=True)
        trades.to_csv("results/trades.csv", index=False)
        logger.info("Trades saved to results/trades.csv")

    if not args.no_report:
        report_path = cfg.get("report", {}).get("output", "results/report.html")
        try:
            engine.generate_report(report_path)
        except Exception as e:
            logger.warning(f"Report generation failed: {e}")


if __name__ == "__main__":
    main()
