from __future__ import annotations

import gc
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from loguru import logger

from quant.config import get_settings
from quant.data.symbol_filter import is_a_share_symbol
from quant.data.store import get_store
from quant.data.updater import (
    DataOperationCancelled,
    DataSource,
    download_all_a,
    fetch_all_a_symbols,
    update_symbols,
)

JobKind = Literal["update", "download"]
_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "meta" / "jobs.sqlite3"
_SETTINGS = get_settings()
_MEMORY_BUDGET_BYTES = int(_SETTINGS.data.memory_budget_gb * 1024**3)
_ESTIMATED_WORKER_BYTES = _SETTINGS.data.estimated_worker_mb * 1024**2
_PROVIDER_WORKER_CAPS: dict[DataSource, int] = {
    "tdx": _SETTINGS.tdx.workers,
    "tushare": _SETTINGS.tushare.workers,
    "akshare": _SETTINGS.data.akshare_workers,
    "baostock": _SETTINGS.data.baostock_workers,
}
_ACTIVE_STATUSES = {"queued", "running", "paused", "cancelling"}


class JobControl:
    """Thread-safe cooperative pause/cancel controller."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._paused = False
        self._cancelled = False

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused

    def pause(self) -> None:
        with self._condition:
            self._paused = True

    def resume(self) -> None:
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def cancel(self) -> None:
        with self._condition:
            self._cancelled = True
            self._paused = False
            self._condition.notify_all()

    def checkpoint(self) -> None:
        with self._condition:
            while self._paused and not self._cancelled:
                self._condition.wait(timeout=0.25)
            if self._cancelled:
                raise DataOperationCancelled("data job cancelled")


class DataJobManager:
    """Single-flight market-data queue with SQLite-backed progress."""

    def __init__(self, db_path: Path | str = _DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._active_job_id: str | None = None
        self._controls: dict[str, JobControl] = {}
        self._initialize()
        self._recover_interrupted_jobs()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS data_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    updated INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    current_symbol TEXT NOT NULL DEFAULT '',
                    current_status TEXT NOT NULL DEFAULT '',
                    workers INTEGER NOT NULL DEFAULT 1,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    elapsed_s REAL NOT NULL DEFAULT 0,
                    error TEXT,
                    result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS data_job_items (
                    job_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_data_job_items_recent
                    ON data_job_items(job_id, updated_at DESC);
                """
            )

    def _recover_interrupted_jobs(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE data_jobs
                SET status = 'interrupted', finished_at = ?,
                    error = COALESCE(error, 'backend restarted while task was running')
                WHERE status IN ('queued', 'running', 'paused', 'cancelling')
                """,
                (_now(),),
            )

    def start(
        self,
        kind: JobKind,
        source: DataSource = "tdx",
        symbols: list[str] | None = None,
        workers: int = 2,
        materialize_indicators: bool = False,
    ) -> dict:
        workers = _effective_workers(source, workers)
        with self._lock:
            active = self.active()
            if active is not None:
                return {"status": "busy", "job": active}

            job_id = uuid.uuid4().hex
            requested_symbols = list(dict.fromkeys(symbols or []))
            target_symbols = requested_symbols
            universe_origin = "request"
            if kind == "download" and not target_symbols:
                target_symbols, universe_origin = _initial_download_symbols(source)
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO data_jobs(
                        id, kind, source, status, total, workers, started_at, result_json
                    ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        kind,
                        source,
                        len(target_symbols),
                        workers,
                        _now(),
                        json.dumps(
                            {
                                "requested_symbols": requested_symbols,
                                "universe_origin": universe_origin,
                            }
                        ),
                    ),
                )
            self._active_job_id = job_id
            self._controls[job_id] = JobControl()
            threading.Thread(
                target=self._run,
                args=(job_id, kind, source, target_symbols or None, workers, materialize_indicators),
                name=f"data-job-{job_id[:8]}",
                daemon=True,
            ).start()
            return {"status": "started", "job": self.get(job_id)}

    def active(self) -> dict | None:
        with self._lock:
            if self._active_job_id:
                job = self.get(self._active_job_id)
                if job and job["running"]:
                    return job
                self._active_job_id = None
            return None

    def pause(self, job_id: str) -> dict:
        with self._lock:
            job = self.get(job_id)
            control = self._controls.get(job_id)
            if job is None:
                return {"status": "not_found"}
            if control is None or job["status"] not in {"queued", "running"}:
                return {"status": "invalid", "job": job}
            control.pause()
            self._set_job(job_id, status="paused", current_status="paused")
            return {"status": "paused", "job": self.get(job_id)}

    def resume(self, job_id: str) -> dict:
        with self._lock:
            job = self.get(job_id)
            control = self._controls.get(job_id)
            if job is None:
                return {"status": "not_found"}
            if control is None or job["status"] != "paused":
                return {"status": "invalid", "job": job}
            self._set_job(job_id, status="running", current_status="running")
            control.resume()
            return {"status": "resumed", "job": self.get(job_id)}

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self.get(job_id)
            control = self._controls.get(job_id)
            if job is None:
                return {"status": "not_found"}
            if control is None or job["status"] not in _ACTIVE_STATUSES:
                return {"status": "invalid", "job": job}
            self._set_job(job_id, status="cancelling", current_status="cancelling")
            control.cancel()
            return {"status": "cancelling", "job": self.get(job_id)}

    def get(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM data_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            items = conn.execute(
                """
                SELECT symbol, status, message, updated_at
                FROM data_job_items WHERE job_id = ?
                ORDER BY updated_at DESC LIMIT 20
                """,
                (job_id,),
            ).fetchall()
        job = dict(row)
        job["running"] = job["status"] in _ACTIVE_STATUSES
        job["paused"] = job["status"] == "paused"
        job["percent"] = round(
            (job["completed"] / job["total"] * 100) if job["total"] else 0,
            1,
        )
        if job["running"]:
            job["elapsed_s"] = max(
                0.0,
                (datetime.now() - datetime.fromisoformat(job["started_at"])).total_seconds(),
            )
        job["speed"] = (
            0
            if job["paused"]
            else round(
                job["completed"] / job["elapsed_s"] if job["elapsed_s"] else 0,
                2,
            )
        )
        remaining = max(0, job["total"] - job["completed"])
        job["eta_s"] = round(remaining / job["speed"]) if job["speed"] else None
        job["recent"] = [dict(item) for item in items]
        job["result"] = json.loads(job.pop("result_json") or "{}")
        return job

    def latest(self) -> dict | None:
        active = self.active()
        if active is not None:
            return active
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM data_jobs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return self.get(str(row["id"])) if row is not None else None

    def _run(
        self,
        job_id: str,
        kind: JobKind,
        source: DataSource,
        symbols: list[str] | None,
        workers: int,
        materialize_indicators: bool,
    ) -> None:
        started = time.monotonic()
        control = self._controls[job_id]
        if control.paused:
            self._set_job(job_id, status="paused")
        else:
            self._set_job(job_id, status="running")

        def progress(done: int, total: int, symbol: str, status: str) -> None:
            self._record_progress(job_id, done, total, symbol, status)
            if done % 50 == 0:
                _release_memory()
            control.checkpoint()

        try:
            control.checkpoint()
            if kind == "download":
                if symbols is None:
                    progress(0, 0, "UNIVERSE", "running:resolving stock universe")
                    symbols, universe_origin = _local_download_symbols(source)
                    self._merge_result(
                        job_id,
                        {
                            "requested_symbols": [],
                            "universe_origin": universe_origin,
                        },
                    )
                    if symbols:
                        self._set_job(job_id, total=len(symbols))
                symbols_info = (
                    [{"symbol": symbol} for symbol in symbols]
                    if symbols is not None
                    else None
                )
                if symbols_info:
                    self._set_job(job_id, total=len(symbols_info))
                result = download_all_a(
                    source=source,
                    max_workers=workers,
                    recompute_indicators=materialize_indicators,
                    delay=_SETTINGS.data.request_delay,
                    on_progress=progress,
                    symbols_info=symbols_info,
                    fallback_sources=False,
                    control=control.checkpoint,
                )
            else:
                target = symbols or get_store().list_symbols("day")
                self._set_job(job_id, total=len(target))
                rows = update_symbols(
                    target,
                    source=source,
                    max_workers=workers,
                    recompute_indicators=materialize_indicators,
                    delay=_SETTINGS.data.request_delay,
                    on_progress=progress,
                    fallback_sources=False,
                    control=control.checkpoint,
                )
                result = {
                    "total": len(rows),
                    "success": sum(row["status"] == "updated" for row in rows),
                    "skipped": sum(row["status"] in {"up_to_date", "no_new_data"} for row in rows),
                    "failed": sum(row["status"] == "error" for row in rows),
                    "errors": [
                        f'{row["symbol"]}: {row.get("error", "unknown")}'
                        for row in rows if row["status"] == "error"
                    ][:50],
                }
            control.checkpoint()
            result_total = int(result.get("total") or 0)
            result_success = int(result.get("success") or 0)
            result_skipped = int(result.get("skipped") or 0)
            result_failed = int(result.get("failed") or 0)
            final_result = self._result_snapshot(job_id)
            final_result.update(result)
            self._set_job(
                job_id,
                status="completed",
                total=result_total,
                completed=result_total,
                updated=result_success,
                skipped=result_skipped,
                failed=result_failed,
                finished_at=_now(),
                elapsed_s=round(time.monotonic() - started, 2),
                result_json=json.dumps(final_result, ensure_ascii=False),
            )
        except DataOperationCancelled:
            self._set_job(
                job_id,
                status="cancelled",
                finished_at=_now(),
                elapsed_s=round(time.monotonic() - started, 2),
                error=None,
            )
        except Exception as exc:
            logger.exception(f"data job {job_id} failed")
            self._set_job(
                job_id,
                status="failed",
                finished_at=_now(),
                elapsed_s=round(time.monotonic() - started, 2),
                error=str(exc),
            )
        finally:
            _release_memory()
            with self._lock:
                self._controls.pop(job_id, None)
                if self._active_job_id == job_id:
                    self._active_job_id = None

    def _record_progress(
        self,
        job_id: str,
        completed: int,
        total: int,
        symbol: str,
        status: str,
    ) -> None:
        raw_status, _, message = status.partition(":")
        normalized = _normalize_status(raw_status)
        message = message.strip()
        now = _now()
        with self._connect() as conn:
            previous = conn.execute(
                "SELECT status FROM data_job_items WHERE job_id = ? AND symbol = ?",
                (job_id, symbol),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO data_job_items(job_id, symbol, status, message, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id, symbol) DO UPDATE SET
                    status = excluded.status,
                    message = excluded.message,
                    updated_at = excluded.updated_at
                """,
                (job_id, symbol, normalized, message, now),
            )
            previous_status = str(previous["status"]) if previous is not None else None
            delta = {
                name: int(normalized == name) - int(previous_status == name)
                for name in ("updated", "skipped", "failed")
            }
            conn.execute(
                """
                UPDATE data_jobs SET
                    total = ?, completed = ?,
                    updated = updated + ?,
                    skipped = skipped + ?,
                    failed = failed + ?,
                    current_symbol = ?, current_status = ?
                WHERE id = ?
                """,
                (
                    total,
                    completed,
                    delta["updated"],
                    delta["skipped"],
                    delta["failed"],
                    symbol,
                    normalized,
                    job_id,
                ),
            )

    def _set_job(self, job_id: str, **values: object) -> None:
        allowed = {
            "status", "total", "completed", "updated", "skipped", "failed",
            "current_symbol", "current_status", "finished_at", "elapsed_s",
            "error", "result_json",
        }
        fields = [(key, value) for key, value in values.items() if key in allowed]
        if not fields:
            return
        sql = ", ".join(f"{key} = ?" for key, _ in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE data_jobs SET {sql} WHERE id = ?",
                [value for _, value in fields] + [job_id],
            )

    def _merge_result(self, job_id: str, values: dict) -> None:
        with self._connect() as conn:
            current = self._result_snapshot(job_id, conn)
            current.update(values)
            conn.execute(
                "UPDATE data_jobs SET result_json = ? WHERE id = ?",
                (json.dumps(current, ensure_ascii=False), job_id),
            )

    def _result_snapshot(
        self,
        job_id: str,
        conn: sqlite3.Connection | None = None,
    ) -> dict:
        owns_connection = conn is None
        active_conn = conn or self._connect()
        try:
            row = active_conn.execute(
                "SELECT result_json FROM data_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return {}
            return json.loads(row["result_json"] or "{}")
        finally:
            if owns_connection:
                active_conn.close()


def _effective_workers(source: DataSource, requested: int) -> int:
    memory_cap = max(1, _MEMORY_BUDGET_BYTES // _ESTIMATED_WORKER_BYTES)
    return max(1, min(int(requested), _PROVIDER_WORKER_CAPS[source], memory_cap))


def _symbols_from_universe_rows(rows: list[dict]) -> list[str]:
    symbols = [
        str(item.get("symbol", "")).zfill(6)
        for item in rows
        if is_a_share_symbol(str(item.get("symbol", "")), include_bj=False)
    ]
    return list(dict.fromkeys(symbols))


def _persist_universe_rows(rows: list[dict]) -> None:
    if not rows:
        return
    import pandas as pd

    store = get_store()
    out_path = store.meta_path("symbols")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out_path, index=False)


def _local_universe_symbols() -> list[str]:
    store = get_store()
    universe = store.get_universe()
    if universe.empty or "symbol" not in universe.columns:
        return []
    symbols = [
        str(symbol).zfill(6)
        for symbol in universe["symbol"].tolist()
        if is_a_share_symbol(str(symbol), include_bj=False)
    ]
    return list(dict.fromkeys(symbols))


def _remote_download_symbols(source: DataSource) -> tuple[list[str], str]:
    remote = fetch_all_a_symbols(source)
    symbols = _symbols_from_universe_rows(remote)
    if symbols:
        _persist_universe_rows(remote)
    return symbols, f"remote:{source}"


def _local_download_symbols(source: DataSource) -> tuple[list[str], str]:
    """Resolve the whole-market target locally so progress has a total immediately."""
    local_symbols = _local_universe_symbols()
    if source == "tushare":
        if len(local_symbols) >= 1000:
            return local_symbols, "symbols.parquet"
        try:
            symbols, origin = _remote_download_symbols("tdx")
            if symbols:
                return symbols, origin
        except Exception as exc:
            logger.warning(f"failed to refresh stable universe before tushare download: {exc}")
        if local_symbols:
            return local_symbols, "symbols.parquet"
        try:
            symbols, origin = _remote_download_symbols(source)
            if symbols:
                return symbols, origin
        except Exception as exc:
            logger.warning(f"failed to refresh tushare universe before download: {exc}")

    if len(local_symbols) >= 1000:
        return local_symbols, "symbols.parquet"
    try:
        symbols, origin = _remote_download_symbols(source if source != "tushare" else "tdx")
        if symbols:
            return symbols, origin
    except Exception as exc:
        logger.warning(f"failed to refresh remote universe before download: {exc}")
    if local_symbols:
        return local_symbols, "symbols.parquet"
    return [], "remote"


def _initial_download_symbols(source: DataSource) -> tuple[list[str], str]:
    """Return only cheap local download targets for the request thread.

    Remote universe refresh can block on provider networking, so it belongs in
    the background worker after the caller has a job id and visible progress.
    """
    local_symbols = _local_universe_symbols()
    if len(local_symbols) >= 1000:
        return local_symbols, "symbols.parquet"
    return [], "background:remote"


def _normalize_status(status: str) -> str:
    status = status.split(":", 1)[0]
    if status in {"updated", "ok", "done"}:
        return "updated"
    if status in {"up_to_date", "no_new_data", "skipped"}:
        return "skipped"
    if status in {"downloading", "fetching", "running"}:
        return "running"
    return "failed"


def _release_memory() -> None:
    gc.collect()
    try:
        import pyarrow as pa

        pa.default_memory_pool().release_unused()
    except Exception:
        pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


_MANAGER: DataJobManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_data_job_manager() -> DataJobManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = DataJobManager()
        return _MANAGER
