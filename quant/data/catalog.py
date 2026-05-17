"""SQLite catalog for Parquet market-data files.

The Parquet files remain the source of truth for bars and indicators. SQLite
stores frequently queried metadata plus one latest-row snapshot per symbol so
status and cross-section operations do not open thousands of Parquet files.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.parquet as pq

from .schema import Freq


@dataclass(frozen=True)
class CatalogEntry:
    symbol: str
    freq: str
    first_dt: date | None
    last_dt: date | None
    rows: int
    source: str
    ts_updated: str
    mtime_ns: int


class DataCatalog:
    """Small SQLite index over the file-based market-data store."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialized = False
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _initialize(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS kline_catalog (
                        symbol TEXT NOT NULL,
                        freq TEXT NOT NULL,
                        first_dt TEXT,
                        last_dt TEXT,
                        rows INTEGER NOT NULL,
                        source TEXT NOT NULL DEFAULT '',
                        ts_updated TEXT NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        PRIMARY KEY (symbol, freq)
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kline_catalog_freq_last "
                    "ON kline_catalog(freq, last_dt)"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS latest_snapshot (
                        symbol TEXT NOT NULL,
                        freq TEXT NOT NULL,
                        dt TEXT,
                        payload TEXT NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        PRIMARY KEY (symbol, freq)
                    )
                    """
                )
            self._initialized = True

    def get(self, symbol: str, freq: Freq) -> CatalogEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM kline_catalog WHERE symbol = ? AND freq = ?",
                (symbol, freq),
            ).fetchone()
        return _entry_from_row(row) if row is not None else None

    def list_symbols(self, freq: Freq) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol FROM kline_catalog WHERE freq = ? ORDER BY symbol",
                (freq,),
            ).fetchall()
        return [str(row["symbol"]) for row in rows]

    def entries(self, freq: Freq) -> dict[str, CatalogEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM kline_catalog WHERE freq = ?",
                (freq,),
            ).fetchall()
        return {
            str(row["symbol"]): _entry_from_row(row)
            for row in rows
        }

    def upsert(
        self,
        symbol: str,
        freq: Freq,
        first_dt: date | None,
        last_dt: date | None,
        rows: int,
        source: str,
        mtime_ns: int,
        ts_updated: str | None = None,
    ) -> None:
        record = (
            symbol,
            freq,
            _date_text(first_dt),
            _date_text(last_dt),
            int(rows),
            source,
            ts_updated or datetime.now().isoformat(timespec="seconds"),
            int(mtime_ns),
        )
        with self._connect() as conn:
            self._upsert_many(conn, [record])

    def sync_path(self, path: Path, symbol: str, freq: Freq, source: str = "") -> CatalogEntry:
        current = self.get(symbol, freq)
        mtime_ns = path.stat().st_mtime_ns
        if current is not None and current.mtime_ns == mtime_ns:
            return current

        record = _inspect_parquet(path, symbol, freq, source)
        with self._connect() as conn:
            self._upsert_many(conn, [record])
        entry = self.get(symbol, freq)
        if entry is None:
            raise RuntimeError(f"failed to index {path}")
        return entry

    def sync_frequency(self, market_dir: Path, freq: Freq) -> int:
        """Index new/changed files and discard entries for deleted files."""
        paths = sorted(market_dir.glob("*.parquet")) if market_dir.exists() else []
        with self._connect() as conn:
            existing_rows = conn.execute(
                "SELECT symbol, mtime_ns, source FROM kline_catalog WHERE freq = ?",
                (freq,),
            ).fetchall()
        existing = {
            str(row["symbol"]): (int(row["mtime_ns"]), str(row["source"]))
            for row in existing_rows
        }

        records: list[tuple] = []
        symbols: set[str] = set()
        for path in paths:
            symbol = path.stem
            symbols.add(symbol)
            mtime_ns = path.stat().st_mtime_ns
            old = existing.get(symbol)
            if old is not None and old[0] == mtime_ns:
                continue
            records.append(_inspect_parquet(path, symbol, freq, old[1] if old else ""))

        with self._connect() as conn:
            if records:
                self._upsert_many(conn, records)
            stale = set(existing) - symbols
            if stale:
                conn.executemany(
                    "DELETE FROM kline_catalog WHERE symbol = ? AND freq = ?",
                    [(symbol, freq) for symbol in stale],
                )
        return len(paths)

    def last_updates(self, symbol: str | None = None, freq: Freq | None = None) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[str] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if freq is not None:
            clauses.append("freq = ?")
            params.append(freq)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT symbol, freq, last_dt, source, ts_updated, rows, first_dt "
            f"FROM kline_catalog{where} ORDER BY freq, symbol"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return pd.DataFrame(
            [dict(row) for row in rows],
            columns=["symbol", "freq", "last_dt", "source", "ts_updated", "rows", "first_dt"],
        )

    def upsert_snapshot(
        self,
        symbol: str,
        freq: Freq,
        values: dict[str, object],
        mtime_ns: int,
    ) -> None:
        payload = {
            key: _json_value(value)
            for key, value in values.items()
            if key != "symbol"
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO latest_snapshot(symbol, freq, dt, payload, mtime_ns)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, freq) DO UPDATE SET
                    dt = excluded.dt,
                    payload = excluded.payload,
                    mtime_ns = excluded.mtime_ns
                """,
                (
                    symbol,
                    freq,
                    payload.get("dt"),
                    json.dumps(payload, separators=(",", ":"), allow_nan=False),
                    int(mtime_ns),
                ),
            )

    def latest_snapshots(
        self,
        freq: Freq,
        symbols: Iterable[str] | None = None,
        columns: Iterable[str] | None = None,
    ) -> pd.DataFrame:
        requested = set(symbols) if symbols is not None else None
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.symbol, s.payload
                FROM latest_snapshot AS s
                JOIN kline_catalog AS k
                  ON k.symbol = s.symbol AND k.freq = s.freq
                WHERE s.freq = ? AND s.mtime_ns = k.mtime_ns
                ORDER BY s.symbol
                """,
                (freq,),
            ).fetchall()
        keep = (
            [column for column in dict.fromkeys(columns) if column != "symbol"]
            if columns is not None
            else None
        )
        records: list[dict[str, object]] = []
        for row in rows:
            symbol = str(row["symbol"])
            if requested is not None and symbol not in requested:
                continue
            payload = json.loads(str(row["payload"]))
            record: dict[str, object] = {"symbol": symbol}
            if keep is None:
                record.update(payload)
            else:
                record.update({column: payload.get(column) for column in keep})
            records.append(record)
        output_columns = ["symbol", *(keep or [])] if keep is not None else None
        return pd.DataFrame(records, columns=output_columns)

    @staticmethod
    def _upsert_many(conn: sqlite3.Connection, records: Iterable[tuple]) -> None:
        conn.executemany(
            """
            INSERT INTO kline_catalog
                (symbol, freq, first_dt, last_dt, rows, source, ts_updated, mtime_ns)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, freq) DO UPDATE SET
                first_dt = excluded.first_dt,
                last_dt = excluded.last_dt,
                rows = excluded.rows,
                source = CASE
                    WHEN excluded.source <> '' THEN excluded.source
                    ELSE kline_catalog.source
                END,
                ts_updated = excluded.ts_updated,
                mtime_ns = excluded.mtime_ns
            """,
            records,
        )


def _inspect_parquet(path: Path, symbol: str, freq: Freq, source: str) -> tuple:
    parquet = pq.ParquetFile(path)
    rows = parquet.metadata.num_rows
    first_dt: date | None = None
    last_dt: date | None = None
    dt_index = parquet.schema_arrow.get_field_index("dt")

    if dt_index >= 0:
        for i in range(parquet.metadata.num_row_groups):
            stats = parquet.metadata.row_group(i).column(dt_index).statistics
            if stats is None or not stats.has_min_max:
                first_dt = last_dt = None
                break
            group_min = _as_date(stats.min)
            group_max = _as_date(stats.max)
            if group_min is not None and (first_dt is None or group_min < first_dt):
                first_dt = group_min
            if group_max is not None and (last_dt is None or group_max > last_dt):
                last_dt = group_max

    if rows and (first_dt is None or last_dt is None):
        dt_table = pq.read_table(path, columns=["dt"])
        values = pd.to_datetime(dt_table.column("dt").to_pandas()).dt.date
        first_dt = values.min()
        last_dt = values.max()

    stat = path.stat()
    return (
        symbol,
        freq,
        _date_text(first_dt),
        _date_text(last_dt),
        int(rows),
        source,
        datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        int(stat.st_mtime_ns),
    )


def _entry_from_row(row: sqlite3.Row) -> CatalogEntry:
    return CatalogEntry(
        symbol=str(row["symbol"]),
        freq=str(row["freq"]),
        first_dt=_as_date(row["first_dt"]),
        last_dt=_as_date(row["last_dt"]),
        rows=int(row["rows"]),
        source=str(row["source"]),
        ts_updated=str(row["ts_updated"]),
        mtime_ns=int(row["mtime_ns"]),
    )


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()  # type: ignore[no-any-return]
    return date.fromisoformat(str(value)[:10])


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()  # type: ignore[assignment, union-attr]
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[union-attr]
    return value
