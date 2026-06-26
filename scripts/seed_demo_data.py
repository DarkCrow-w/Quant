from __future__ import annotations

import argparse
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEMO_SYMBOLS = [
    "600000",
    "600004",
    "600006",
    "600007",
    "600008",
    "600009",
    "600010",
    "600011",
    "600012",
    "600015",
    "600016",
    "600017",
    "600018",
    "600019",
    "600020",
    "600519",
]


def business_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def build_demo_kline(symbol: str, days: list[date]) -> pd.DataFrame:
    symbol_seed = int(symbol[-3:])
    base = 8.0 + (symbol_seed % 90) * 0.45
    if symbol == "600519":
        base = 1450.0
    rows: list[dict] = []
    previous_close = base
    for index, trade_date in enumerate(days):
        trend = 1 + 0.00025 * index
        cycle = math.sin((index + symbol_seed) / 9.0) * 0.018
        slow_cycle = math.cos((index + symbol_seed) / 31.0) * 0.012
        close = max(1.0, base * trend * (1 + cycle + slow_cycle))
        open_price = max(1.0, previous_close * (1 + math.sin(index / 7.0 + symbol_seed) * 0.004))
        high = max(open_price, close) * (1.006 + abs(math.sin(index / 5.0)) * 0.004)
        low = min(open_price, close) * (0.994 - abs(math.cos(index / 6.0)) * 0.003)
        volume = 900_000 + (symbol_seed % 97) * 8_000 + (index % 20) * 15_000
        if index % 37 == 0:
            volume *= 1.8
        amount = volume * close
        rows.append(
            {
                "dt": trade_date,
                "open": round(open_price, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": round(volume, 2),
                "amount": round(amount, 2),
            }
        )
        previous_close = close
    return pd.DataFrame(rows)


def build_universe(min_universe: int) -> pd.DataFrame:
    rows: list[dict] = []
    seen: set[str] = set()

    def add(symbol: str, name: str, market: str) -> None:
        if symbol in seen:
            return
        seen.add(symbol)
        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "market": market,
                "list_date": "2000-01-01",
                "industry": "Demo",
            }
        )

    demo_names = {
        "600000": "浦发银行",
        "600004": "白云机场",
        "600006": "东风汽车",
        "600007": "中国国贸",
        "600008": "首创环保",
        "600009": "上海机场",
        "600010": "包钢股份",
        "600011": "华能国际",
        "600012": "皖通高速",
        "600015": "华夏银行",
        "600016": "民生银行",
        "600017": "日照港",
        "600018": "上港集团",
        "600019": "宝钢股份",
        "600020": "中原高速",
        "600519": "贵州茅台",
    }
    for symbol in DEMO_SYMBOLS:
        add(symbol, demo_names.get(symbol, f"演示股票{symbol}"), "SH")

    ranges = [
        ("SH", 600000, 600999),
        ("SH", 601000, 601999),
        ("SH", 603000, 603999),
        ("SH", 605000, 605999),
        ("SZ", 1, 999),
        ("SZ", 2000, 2999),
        ("SZ", 300000, 300999),
        ("SZ", 301000, 301999),
        ("BJ", 830000, 830999),
        ("BJ", 831000, 831999),
    ]
    for market, start, end in ranges:
        for code in range(start, end + 1):
            if len(rows) >= min_universe:
                break
            symbol = f"{code:06d}"
            add(symbol, f"演示股票{symbol}", market)
        if len(rows) >= min_universe:
            break

    return pd.DataFrame(rows[:min_universe])


def seed_demo_data(
    root: Path,
    min_cache: int,
    min_universe: int,
    force: bool = False,
) -> dict:
    from quant.data.store import DataStore

    store = DataStore(root)
    existing_symbols = store.list_symbols("day")
    universe_path = store.meta_path("symbols")
    needs_cache = force or len(existing_symbols) < min_cache
    needs_universe = force or not universe_path.exists() or len(store.get_universe()) < min_universe

    written_symbols: list[str] = []
    if needs_universe:
        universe = build_universe(min_universe)
        universe_path.parent.mkdir(parents=True, exist_ok=True)
        universe.to_parquet(universe_path, index=False)

    if needs_cache:
        days = business_days(date(2024, 1, 1), date(2024, 12, 31))
        for symbol in DEMO_SYMBOLS:
            if not force and store.kline_path(symbol, "day").exists():
                continue
            frame = build_demo_kline(symbol, days)
            store.upsert_kline(symbol, frame, freq="day", source="demo-seed", recompute_indicators=True)
            written_symbols.append(symbol)
        store.rebuild_catalog(["day"])

    return {
        "cache_before": len(existing_symbols),
        "cache_after": len(store.list_symbols("day")),
        "universe_after": len(store.get_universe()),
        "written_symbols": written_symbols,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed deterministic offline QuantLab demo market data.")
    parser.add_argument("--root", type=Path, default=ROOT / "data", help="Data root to seed.")
    parser.add_argument("--min-cache", type=int, default=10, help="Only seed cache when fewer symbols exist.")
    parser.add_argument("--min-universe", type=int, default=5000, help="Minimum universe rows to ensure.")
    parser.add_argument("--force", action="store_true", help="Overwrite demo symbols and universe metadata.")
    args = parser.parse_args()

    report = seed_demo_data(args.root, args.min_cache, args.min_universe, args.force)
    print(report)


if __name__ == "__main__":
    main()
