"""DataStore — 数据层的统一门面。

设计要点：
- 主存储为 parquet：``<root>/market/{freq}/{symbol}.parquet``，OHLCV + 20 指标列
  写在同一个文件，避免文件爆炸（5,200 × 3 = 15,600 比 312k 友好得多）
- 进程内 LRU 缓存：键为 ``(path, mtime_ns)``，写入后自动失效（``os.replace`` 后 mtime 变化）
- 读时检查指标版本：parquet KV-metadata 记录每列的 version_key，如果与当前注册表不一致
  则触发自动重算并原子写回
- 不依赖 Redis/MySQL/Mongo；DuckDB 仅在 ``query.py`` 中按需引入
"""
from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
from loguru import logger

from . import indicators as ind_mod
from .catalog import DataCatalog
from .schema import (
    ALL_FREQS,
    Freq,
    OHLCV_COLUMNS,
    normalize_kline,
    read_parquet_with_meta,
    safe_write_parquet,
)
from .symbols import normalize


_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data"


class DataStore:
    """统一读写 K 线 + 指标的门面。"""

    def __init__(self, root: Path | str = _DEFAULT_ROOT) -> None:
        self.root = Path(root)
        self.catalog = DataCatalog(self.root / "meta" / "catalog.sqlite3")
        self._locks_guard = threading.Lock()
        self._path_locks: dict[Path, threading.RLock] = {}

    # ─── 路径 ────────────────────────────────────────────────────────────
    def kline_path(self, symbol: str, freq: Freq = "day") -> Path:
        if freq not in ALL_FREQS:
            raise ValueError(f"unknown freq: {freq}")
        return self.root / "market" / freq / f"{normalize(symbol)}.parquet"

    def meta_path(self, name: str) -> Path:
        return self.root / "meta" / f"{name}.parquet"

    # ─── 读 ──────────────────────────────────────────────────────────────
    def _path_lock(self, path: Path) -> threading.RLock:
        with self._locks_guard:
            return self._path_locks.setdefault(path, threading.RLock())

    def _read_with_versions(
        self,
        path: Path,
        columns: Iterable[str] | None = None,
        start: date | None = None,
        end: date | None = None,
        tail: int | None = None,
    ) -> tuple[pd.DataFrame, dict[str, str]]:
        return read_parquet_with_meta(path, columns=columns, start=start, end=end, tail=tail)

    def get_kline(
        self,
        symbol: str,
        freq: Freq = "day",
        start: date | str | None = None,
        end: date | str | None = None,
        with_indicators: bool | list[str] = False,
        columns: Iterable[str] | None = None,
        tail: int | None = None,
    ) -> pd.DataFrame:
        """读取一只股票的 K 线，支持列裁剪、日期过滤和最近 N 根。"""
        path = self.kline_path(symbol, freq)
        if not path.exists():
            empty_columns = list(columns) if columns is not None else list(OHLCV_COLUMNS)
            return pd.DataFrame(columns=empty_columns)

        start_d = _to_date(start) if start is not None else None
        end_d = _to_date(end) if end is not None else None
        requested_columns = list(dict.fromkeys(columns)) if columns is not None else None
        df, stored_versions = self._read_with_versions(
            path, columns=requested_columns, start=start_d, end=end_d, tail=tail
        )

        if with_indicators:
            names = (
                list(ind_mod.INDICATORS.keys())
                if with_indicators is True
                else [n.upper() for n in with_indicators]
            )
            missing = [
                name
                for name in names
                if any(column not in df.columns for column in ind_mod.INDICATORS[name].output_columns)
            ]
            to_compute = sorted(set(ind_mod.stale_indicators(stored_versions, names)) | set(missing))
            if to_compute:
                with self._path_lock(path):
                    full, current_versions = self._read_with_versions(path)
                    current_missing = [
                        name
                        for name in names
                        if any(
                            column not in full.columns
                            for column in ind_mod.INDICATORS[name].output_columns
                        )
                    ]
                    current_stale = ind_mod.stale_indicators(current_versions, names)
                    current_compute = sorted(set(current_stale) | set(current_missing))
                    if current_compute:
                        logger.debug(
                            f"recomputing indicators for {symbol}@{freq}: {current_compute}"
                        )
                        self._materialize_unlocked(
                            symbol, freq, current_compute, full, current_versions
                        )
                df, _ = self._read_with_versions(
                    path, columns=requested_columns, start=start_d, end=end_d, tail=tail
                )
        return df

    def get_klines(
        self,
        symbols: Iterable[str],
        freq: Freq = "day",
        start: date | str | None = None,
        end: date | str | None = None,
        with_indicators: bool | list[str] = False,
        columns: Iterable[str] | None = None,
        tail: int | None = None,
    ) -> dict[str, pd.DataFrame]:
        return {
            s: self.get_kline(
                s, freq, start, end, with_indicators, columns=columns, tail=tail
            )
            for s in symbols
        }

    def get_indicator(
        self,
        symbol: str,
        name: str,
        freq: Freq = "day",
        start: date | str | None = None,
        end: date | str | None = None,
    ) -> pd.DataFrame:
        """返回 ``[dt] + indicator.output_columns`` 子集。"""
        spec = ind_mod.INDICATORS[name.upper()]
        keep = ["dt", *spec.output_columns]
        df = self.get_kline(
            symbol,
            freq,
            start,
            end,
            with_indicators=[name],
            columns=keep,
        )
        keep = ["dt"] + [c for c in spec.output_columns if c in df.columns]
        return df[keep].reset_index(drop=True)

    # ─── 写 ──────────────────────────────────────────────────────────────
    def upsert_kline(
        self,
        symbol: str,
        df: pd.DataFrame,
        freq: Freq = "day",
        source: str = "",
        recompute_indicators: bool = True,
    ) -> pd.DataFrame:
        """合并新数据到已有 parquet，去重、排序、原子写回。

        返回合并后的 DataFrame（含指标列）。
        """
        if df is None or len(df) == 0:
            existing = self.get_kline(symbol, freq)
            return existing
        new = normalize_kline(df)

        path = self.kline_path(symbol, freq)
        with self._path_lock(path):
            old, _ = (
                self._read_with_versions(path, columns=OHLCV_COLUMNS)
                if path.exists()
                else (pd.DataFrame(), {})
            )

            base_cols = [c for c in OHLCV_COLUMNS if c in set(new.columns) | set(old.columns)]
            new_base = new[[c for c in base_cols if c in new.columns]]
            if old.empty:
                merged = new_base.copy()
            else:
                old_base = old[[c for c in base_cols if c in old.columns]]
                merged = pd.concat([old_base, new_base], ignore_index=True)
            merged = merged.drop_duplicates(subset=["dt"], keep="last").sort_values("dt").reset_index(drop=True)

            if recompute_indicators:
                merged = ind_mod.compute_all(merged)
                versions = ind_mod.indicator_versions()
            else:
                versions = {}

            safe_write_parquet(merged, path, indicator_versions=versions)
            self._index_frame(symbol, freq, merged, source)
            logger.trace(
                f"upsert {symbol}@{freq}: {len(merged)} bars (source={source or 'n/a'})"
            )
        return merged

    def materialize_indicators(
        self,
        symbol: str,
        freq: Freq = "day",
        names: list[str] | None = None,
        force: bool = False,
    ) -> pd.DataFrame:
        """强制（或按需）重算并写回指标列。"""
        path = self.kline_path(symbol, freq)
        with self._path_lock(path):
            if not path.exists():
                return pd.DataFrame(columns=list(OHLCV_COLUMNS))
            df, stored_versions = self._read_with_versions(path)
            target = [n.upper() for n in (names or list(ind_mod.INDICATORS.keys()))]
            to_compute = (
                target if force else ind_mod.stale_indicators(stored_versions, target)
            )
            if not to_compute:
                return df
            return self._materialize_unlocked(symbol, freq, to_compute, df, stored_versions)

    def _materialize_unlocked(
        self,
        symbol: str,
        freq: Freq,
        names: list[str],
        df: pd.DataFrame,
        stored_versions: dict[str, str],
    ) -> pd.DataFrame:
        """Caller holds self._lock."""
        out = df.copy()
        for name in names:
            cols = ind_mod.compute(name, df)
            for col in cols.columns:
                out[col] = cols[col].to_numpy()
        new_versions = dict(stored_versions)
        new_versions.update(ind_mod.indicator_versions(names))
        path = self.kline_path(symbol, freq)
        safe_write_parquet(out, path, indicator_versions=new_versions)
        self._index_frame(symbol, freq, out, "")
        return out

    # ─── 元数据 ──────────────────────────────────────────────────────────
    def list_symbols(self, freq: Freq = "day") -> list[str]:
        d = self.root / "market" / freq
        if not d.exists():
            return []
        symbols = self.catalog.list_symbols(freq)
        if not symbols and any(d.glob("*.parquet")):
            self.catalog.sync_frequency(d, freq)
            symbols = self.catalog.list_symbols(freq)
        return symbols

    def get_last_date(self, symbol: str, freq: Freq = "day") -> date | None:
        sym = normalize(symbol)
        path = self.kline_path(sym, freq)
        if not path.exists():
            return None
        entry = self.catalog.get(sym, freq)
        if entry is None:
            entry = self.catalog.sync_path(path, sym, freq)
        return entry.last_dt

    def get_last_dates(
        self,
        symbols: Iterable[str] | None = None,
        freq: Freq = "day",
    ) -> dict[str, date | None]:
        entries = self.catalog.entries(freq)
        if not entries:
            self.catalog.sync_frequency(self.root / "market" / freq, freq)
            entries = self.catalog.entries(freq)
        if symbols is None:
            return {symbol: entry.last_dt for symbol, entry in entries.items()}
        normalized = [normalize(symbol) for symbol in symbols]
        for symbol in normalized:
            if symbol in entries:
                continue
            path = self.kline_path(symbol, freq)
            if path.exists():
                entries[symbol] = self.catalog.sync_path(path, symbol, freq)
        return {
            symbol: entries[symbol].last_dt if symbol in entries else None
            for symbol in normalized
        }

    def rebuild_catalog(self, freqs: Iterable[Freq] = ALL_FREQS) -> int:
        total = 0
        for freq in freqs:
            total += self.catalog.sync_frequency(self.root / "market" / freq, freq)
        return total

    def get_latest_snapshot(
        self,
        symbols: Iterable[str] | None = None,
        freq: Freq = "day",
        columns: Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """Return latest rows from SQLite, filling missing snapshots lazily."""
        target = (
            [normalize(symbol) for symbol in symbols]
            if symbols is not None
            else self.list_symbols(freq)
        )
        requested_columns = (
            [column for column in dict.fromkeys(columns) if column != "symbol"]
            if columns is not None
            else None
        )
        cached = self.catalog.latest_snapshots(freq, target, requested_columns)
        cached_symbols = set(cached["symbol"]) if not cached.empty else set()

        for symbol in target:
            if symbol in cached_symbols:
                continue
            df = self.get_kline(symbol, freq=freq, tail=1)
            if df.empty:
                continue
            path = self.kline_path(symbol, freq)
            self.catalog.upsert_snapshot(
                symbol,
                freq,
                df.iloc[-1].to_dict(),
                path.stat().st_mtime_ns,
            )

        return self.catalog.latest_snapshots(freq, target, requested_columns)

    def get_universe(self, market: str | None = None) -> pd.DataFrame:
        path = self.meta_path("symbols")
        if not path.exists():
            return pd.DataFrame(columns=["symbol", "name", "market", "list_date"])
        df = pd.read_parquet(path)
        if market is not None:
            df = df[df["market"] == market.upper()]
        return df.reset_index(drop=True)

    def get_calendar(
        self, start: date | str | None = None, end: date | str | None = None
    ) -> pd.DataFrame:
        path = self.meta_path("trade_calendar")
        if not path.exists():
            return pd.DataFrame(columns=["dt", "is_open"])
        df = pd.read_parquet(path)
        if "dt" in df.columns:
            df["dt"] = pd.to_datetime(df["dt"]).dt.date
        if start is not None:
            df = df[df["dt"] >= _to_date(start)]
        if end is not None:
            df = df[df["dt"] <= _to_date(end)]
        return df.reset_index(drop=True)

    def last_update(
        self, symbol: str | None = None, freq: Freq = "day"
    ) -> pd.DataFrame:
        if not self.catalog.list_symbols(freq):
            d = self.root / "market" / freq
            if d.exists() and any(d.glob("*.parquet")):
                self.catalog.sync_frequency(d, freq)
        return self.catalog.last_updates(
            symbol=normalize(symbol) if symbol is not None else None,
            freq=freq,
        )

    def _index_frame(
        self,
        symbol: str,
        freq: Freq,
        df: pd.DataFrame,
        source: str,
    ) -> None:
        path = self.kline_path(symbol, freq)
        first_dt = df["dt"].iloc[0] if not df.empty else None
        last_dt = df["dt"].iloc[-1] if not df.empty else None
        self.catalog.upsert(
            normalize(symbol),
            freq,
            first_dt,
            last_dt,
            len(df),
            source,
            path.stat().st_mtime_ns,
        )
        if not df.empty:
            self.catalog.upsert_snapshot(
                normalize(symbol),
                freq,
                df.iloc[-1].to_dict(),
                path.stat().st_mtime_ns,
            )


# ─── helpers ────────────────────────────────────────────────────────────────
def _to_date(v: date | str) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


# ─── singleton ──────────────────────────────────────────────────────────────
_STORE: DataStore | None = None
_STORE_LOCK = threading.Lock()


def get_store(root: Path | str | None = None) -> DataStore:
    """进程级单例。第一次传入的 ``root`` 决定后续返回的实例。"""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = DataStore(root or _DEFAULT_ROOT)
        return _STORE


def reset_store() -> None:
    """测试钩子：丢弃单例，下次 ``get_store`` 重新创建。"""
    global _STORE
    with _STORE_LOCK:
        _STORE = None
