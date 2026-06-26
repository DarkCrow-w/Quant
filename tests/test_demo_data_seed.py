from __future__ import annotations

import subprocess
import sys

from quant.data.symbol_filter import filter_a_share_rows
from quant.data.store import DataStore
from scripts.seed_demo_data import build_universe, seed_demo_data


def test_demo_universe_survives_a_share_filter():
    universe = build_universe(5000)
    filtered = filter_a_share_rows(universe.to_dict(orient="records"))

    assert len(universe) == 5000
    assert len(filtered) == 5000


def test_seed_demo_data_creates_offline_universe_and_cache(tmp_path):
    report = seed_demo_data(tmp_path, min_cache=10, min_universe=120)
    store = DataStore(tmp_path)

    universe = store.get_universe()
    symbols = store.list_symbols("day")
    kline = store.get_kline("600519", with_indicators=True)

    assert report["cache_before"] == 0
    assert report["cache_after"] >= 16
    assert report["universe_after"] >= 120
    assert "600519" in symbols
    assert len(universe) >= 120
    assert len(kline) >= 200
    assert {"ma5", "ma20", "kdj_k", "bbi"}.issubset(kline.columns)

    second = seed_demo_data(tmp_path, min_cache=10, min_universe=120)
    assert second["written_symbols"] == []
    assert second["cache_after"] == report["cache_after"]


def test_seed_demo_data_script_runs_from_repo_root(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/seed_demo_data.py",
            "--root",
            str(tmp_path),
            "--min-cache",
            "10",
            "--min-universe",
            "120",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "cache_after" in result.stdout
