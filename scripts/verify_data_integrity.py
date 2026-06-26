from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.data.indicators import all_indicator_columns
from quant.data.store import DataStore
from scripts.seed_demo_data import DEMO_SYMBOLS, seed_demo_data


SYMBOL_RE = re.compile(r"^\d{6}$")
OHLCV_COLUMNS = {"dt", "open", "high", "low", "close", "volume"}
IMPORTANT_INDICATORS = {"ma5", "ma20", "kdj_k", "kdj_d", "kdj_j", "bbi", "mavol5"}


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_clean_text(value: Any, path: str) -> None:
    if isinstance(value, str):
        ensure("\ufffd" not in value, f"{path} contains replacement character")
        ensure("????" not in value, f"{path} contains question-mark mojibake")
        ensure(not any("\ue000" <= char <= "\uf8ff" for char in value), f"{path} contains private-use mojibake")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            assert_clean_text(str(key), f"{path}.key")
            assert_clean_text(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_clean_text(item, f"{path}[{index}]")


def verify_universe(store: DataStore, min_universe: int) -> pd.DataFrame:
    universe = store.get_universe()
    ensure(len(universe) >= min_universe, f"universe too small: {len(universe)} < {min_universe}")
    ensure("symbol" in universe.columns, "universe missing symbol column")
    ensure("name" in universe.columns, "universe missing name column")
    ensure(universe["symbol"].is_unique, "universe symbols are not unique")
    ensure(universe["symbol"].map(lambda value: bool(SYMBOL_RE.match(str(value)))).all(), "universe has invalid symbols")
    ensure(set(DEMO_SYMBOLS).issubset(set(universe["symbol"])), "demo symbols missing from universe")
    assert_clean_text(universe.head(100).to_dict(orient="records"), "universe")
    return universe


def verify_kline_frame(symbol: str, frame: pd.DataFrame, min_bars: int) -> None:
    ensure(len(frame) >= min_bars, f"{symbol} has too few bars: {len(frame)} < {min_bars}")
    ensure(OHLCV_COLUMNS.issubset(frame.columns), f"{symbol} missing OHLCV columns")
    dt = pd.to_datetime(frame["dt"])
    ensure(dt.notna().all(), f"{symbol} contains invalid trade dates")
    ensure(dt.is_monotonic_increasing, f"{symbol} trade dates are not sorted")
    ensure(not dt.duplicated().any(), f"{symbol} contains duplicate trade dates")

    numeric = frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    ensure(numeric.notna().all().all(), f"{symbol} contains non-numeric OHLCV values")
    ensure((numeric[["open", "high", "low", "close"]] > 0).all().all(), f"{symbol} contains non-positive prices")
    ensure((numeric["volume"] >= 0).all(), f"{symbol} contains negative volume")
    ensure((numeric["high"] >= numeric[["open", "close"]].max(axis=1)).all(), f"{symbol} high is below open/close")
    ensure((numeric["low"] <= numeric[["open", "close"]].min(axis=1)).all(), f"{symbol} low is above open/close")
    if "amount" in frame.columns:
        amount = pd.to_numeric(frame["amount"], errors="coerce")
        ensure(amount.notna().all() and (amount >= 0).all(), f"{symbol} contains invalid amount")


def verify_indicator_frame(symbol: str, frame: pd.DataFrame) -> None:
    expected = set(all_indicator_columns())
    missing = expected - set(frame.columns)
    ensure(not missing, f"{symbol} missing indicator columns: {sorted(missing)[:10]}")
    for column in IMPORTANT_INDICATORS:
        values = pd.to_numeric(frame[column], errors="coerce")
        ensure(values.notna().any(), f"{symbol} indicator {column} is entirely null")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify QuantLab local market-data integrity.")
    parser.add_argument("--root", type=Path, default=ROOT / "data", help="Data root to inspect.")
    parser.add_argument("--min-universe", type=int, default=5000)
    parser.add_argument("--min-cache", type=int, default=5)
    parser.add_argument("--min-bars", type=int, default=30)
    parser.add_argument("--sample-size", type=int, default=16, help="Cached symbols to inspect with indicators.")
    parser.add_argument("--skip-demo-seed", action="store_true")
    args = parser.parse_args()

    seed_report = None
    if not args.skip_demo_seed:
        seed_report = seed_demo_data(args.root, min_cache=args.min_cache, min_universe=args.min_universe)

    store = DataStore(args.root)
    universe = verify_universe(store, args.min_universe)
    symbols = store.list_symbols("day")
    ensure(len(symbols) >= args.min_cache, f"cache too small: {len(symbols)} < {args.min_cache}")
    ensure(len(set(symbols)) == len(symbols), "cached symbols are not unique")
    ensure(set(symbols).issubset(set(universe["symbol"])), "cached symbols missing from universe")

    sample = sorted(symbols)[: args.sample_size]
    ensure(len(sample) >= min(args.min_cache, args.sample_size), "not enough cached symbols to sample")
    total_bars = 0
    for symbol in sample:
        frame = store.get_kline(symbol, with_indicators=True)
        verify_kline_frame(symbol, frame, args.min_bars)
        verify_indicator_frame(symbol, frame)
        total_bars += len(frame)

        entry = store.catalog.get(symbol, "day")
        ensure(entry is not None, f"{symbol} missing from catalog")
        ensure(entry.rows == len(frame), f"{symbol} catalog row count mismatch: {entry.rows} != {len(frame)}")

    last_updates = store.last_update(freq="day")
    ensure(len(last_updates) == len(symbols), "last_update count does not match cached symbols")

    report = {
        "status": "ok",
        "root": str(args.root),
        "seed": seed_report,
        "universe": len(universe),
        "cache": len(symbols),
        "sampled": len(sample),
        "sample_bars": total_bars,
        "indicator_columns": len(all_indicator_columns()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise
