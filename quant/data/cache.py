"""Compatibility shim — 旧 API ``load_cache``/``save_cache``/``cache_path``/``CACHE_DIR``。

Phase 5 起，所有读写都走 ``DataStore``，物理路径切换到 ``data/market/day/{symbol}.parquet``。
为兼容旧调用方：
- ``load_cache(symbol)`` 返回仅 OHLCV 列的 DataFrame（指标列被剥离）
- ``save_cache(symbol, df)`` 仍接受 OHLCV，但写入会自动合并 + 重算指标
- ``cache_path(symbol)`` 返回新路径，老代码若直接 ``glob`` 也能用
- ``CACHE_DIR`` 指向 ``data/market/day``
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from .schema import OHLCV_COLUMNS
from .store import get_store
from .symbols import normalize

# 新主目录；包含 5,205 个带指标列的 parquet
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "market" / "day"


def cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{normalize(symbol)}.parquet"


def load_cache(symbol: str) -> pd.DataFrame | None:
    """读取一只股票的 K 线，仅返回 OHLCV 列（兼容老调用方）。"""
    store = get_store()
    df = store.get_kline(symbol, freq="day", columns=OHLCV_COLUMNS)
    if df is None or df.empty:
        return None
    keep = [c for c in OHLCV_COLUMNS if c in df.columns]
    logger.debug(f"Loading cache for {symbol}")
    return df[keep].reset_index(drop=True)


def save_cache(symbol: str, df: pd.DataFrame) -> None:
    """保存 K 线（自动合并 + 重算指标 + 原子写）。"""
    store = get_store()
    store.upsert_kline(symbol, df, freq="day")
    logger.debug(f"Saved cache for {symbol}: {len(df)} rows")
