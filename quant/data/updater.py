"""增量更新 A 股 K 线数据 + 自动指标重算。

本模块同时承载两套 API：

1. **新（推荐）**：``update_universe`` / ``refresh_calendar`` / ``derive_week_month``
   - 走 ``DataStore`` 写入 ``data/market/{freq}/{symbol}.parquet``
   - ``Source`` 回退链 (TDX → AKShare → Tushare)
   - 交易日历门控 + 并发 + 自动指标合并写

2. **旧（兼容）**：``update_symbol`` / ``update_all`` / ``download_all_a`` / ``fetch_all_a_symbols``
   - 写扁平 ``data/{symbol}.parquet``，与现有 server/agent 调用契约一致
   - Phase 5 完成数据迁移后会被重新路由到新 API
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Literal

import pandas as pd
from loguru import logger

from quant.config import get_settings
from . import indicators as ind_mod
from .cache import CACHE_DIR
from .concurrency import bounded_futures
from .feeds import AKShareSource, Source, TDXSource, TushareSource
from .schema import ALL_FREQS, Freq, normalize_kline
from .store import DataStore, get_store
from .symbol_filter import filter_a_share_rows, is_a_share_symbol
from .symbols import normalize
from .tushare_feed import _fetch_daily as _tushare_fetch_daily
from .tdx_feed import _fetch_daily as _tdx_fetch_daily
from .akshare_feed import _fetch_daily as _akshare_fetch_daily
from .baostock_feed import (
    _fetch_daily as _baostock_fetch_daily,
    fetch_all_a_symbols_baostock,
)

# 支持的数据源类型
DataSource = Literal["tushare", "tdx", "akshare", "baostock"]
ControlCallback = Callable[[], None] | None


class DataOperationCancelled(RuntimeError):
    """Raised by a cooperative task checkpoint when cancellation is requested."""


class DataSourceCircuitOpen(RuntimeError):
    """Raised after repeated provider errors indicate a systemic outage."""


def _check_consecutive_errors(
    consecutive_errors: int,
    source: DataSource,
    recent_errors: list[str],
) -> None:
    threshold = _SETTINGS.data.max_consecutive_errors
    if consecutive_errors < threshold:
        return
    detail = "; ".join(recent_errors[-3:])
    raise DataSourceCircuitOpen(
        f"{source} stopped after {consecutive_errors} consecutive errors"
        + (f": {detail}" if detail else "")
    )

# 各源是否线程安全（决定 max_workers > 1 时是否真的并发）
# - tdx: 每次新建 socket，安全
# - akshare: HTTP 调用，独立 session，安全（但 eastmoney 限流，建议 max_workers <= 4）
# - tushare: 200/min 频次限流，并发会触发拒绝；保留串行
# - baostock: 全局 session 非线程安全（实测多线程数据流错乱），强制串行
_THREAD_SAFE_SOURCES = {"tdx", "akshare", "tushare"}
_SETTINGS = get_settings()
_LEGACY_SOURCE_SEMAPHORES: dict[str, threading.Semaphore] = {
    "tdx": threading.Semaphore(_SETTINGS.tdx.workers),
    "akshare": threading.Semaphore(_SETTINGS.data.akshare_workers),
    "tushare": threading.Semaphore(_SETTINGS.tushare.workers),
    "baostock": threading.Semaphore(_SETTINGS.data.baostock_workers),
}
_LEGACY_TUSHARE_RATE_LOCK = threading.Lock()
_LEGACY_TUSHARE_LAST_CALL_AT = 0.0
_LEGACY_TUSHARE_MIN_INTERVAL = 60.0 / _SETTINGS.tushare.rpm
_TUSHARE_UPDATE_CHUNK_SIZE = 64
_DAILY_BAR_PUBLISH_HOUR = 18


def _fetch_daily(symbol: str, start: str, end: str, source: DataSource = "tushare") -> pd.DataFrame:
    """根据数据源获取日线数据。"""
    if source == "tdx":
        return _tdx_fetch_daily(symbol, start, end)
    if source == "akshare":
        return _akshare_fetch_daily(symbol, start, end)
    if source == "baostock":
        return _baostock_fetch_daily(symbol, start, end)
    return _tushare_fetch_daily(symbol, start, end)


def _fetch_daily_bounded(
    symbol: str,
    start: str,
    end: str,
    source: DataSource,
) -> pd.DataFrame:
    """Bound provider concurrency while allowing several symbols in flight."""
    global _LEGACY_TUSHARE_LAST_CALL_AT
    semaphore = _LEGACY_SOURCE_SEMAPHORES[source]
    with semaphore:
        if source == "tushare":
            with _LEGACY_TUSHARE_RATE_LOCK:
                now = time.monotonic()
                wait_s = max(
                    0.0,
                    _LEGACY_TUSHARE_MIN_INTERVAL - (now - _LEGACY_TUSHARE_LAST_CALL_AT),
                )
                if wait_s:
                    time.sleep(wait_s)
                _LEGACY_TUSHARE_LAST_CALL_AT = time.monotonic()
        return _fetch_daily(symbol, start, end, source=source)


def _fallback_chain(source: DataSource) -> list[DataSource]:
    if source == "tdx":
        return ["tdx", "tushare"]
    if source == "tushare":
        return ["tushare", "tdx"]
    if source == "akshare":
        return ["akshare", "tdx"]
    return ["baostock", "tdx"]


def list_cached_symbols() -> list[dict]:
    """List cached symbols without opening every Parquet file."""
    df = get_store().last_update(freq="day")
    if df.empty:
        return []
    return [
        {
            "symbol": row.symbol,
            "bars": int(row.rows),
            "start": str(row.first_dt),
            "end": str(row.last_dt),
        }
        for row in df.itertuples(index=False)
    ]


def update_symbol(
    symbol: str,
    end_date: str | None = None,
    source: DataSource = "tushare",
    recompute_indicators: bool = True,
    fallback_sources: bool = False,
    control: ControlCallback = None,
) -> dict:
    """增量更新单只股票，返回更新信息。

    Args:
        symbol: 股票代码
        end_date: 截止日期，默认今天
        source: 数据源, "tushare" 或 "tdx"
    """
    checkpoint = control or (lambda: None)
    checkpoint()

    store = get_store()
    target_end = _target_trade_date(store, end_date)
    today = str(target_end)
    today_clean = _to_yyyymmdd(target_end)
    sym = normalize(symbol)
    last_date = store.get_last_date(sym, "day")
    entry = store.catalog.get(sym, "day")

    if last_date is not None:
        next_day = last_date + timedelta(days=1)
        if last_date >= target_end:
            return {
                "symbol": sym,
                "status": "up_to_date",
                "bars": entry.rows if entry is not None else 0,
                "end": str(last_date),
                "new_bars": 0,
                "source": source,
                "cached": True,
            }
        start = _to_yyyymmdd(next_day)
    else:
        last_date = None
        start = _to_yyyymmdd(target_end - timedelta(days=365 * 3))
        # 没有缓存，默认从目标交易日回溯三年。

    sources = _fallback_chain(source) if fallback_sources else [source]
    df_new = pd.DataFrame()
    actual_source = source
    errors: list[str] = []
    for candidate in sources:
        try:
            checkpoint()
            candidate_frame = _fetch_daily_bounded(
                sym,
                start,
                today_clean,
                source=candidate,
            )
            if candidate_frame is None or candidate_frame.empty:
                actual_source = candidate
                break
            df_new = candidate_frame
            actual_source = candidate
            break
        except DataOperationCancelled:
            raise
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            logger.debug(f"[update] {sym} {candidate} failed: {exc}")

    if df_new.empty and errors:
        return {
            "symbol": sym,
            "status": "error",
            "error": "; ".join(errors),
            "new_bars": 0,
        }

    if df_new.empty:
        return {
            "symbol": sym,
            "status": "no_new_data",
            "bars": entry.rows if entry is not None else 0,
            "new_bars": 0,
            "source": actual_source,
        }

    old_rows = entry.rows if entry is not None else 0
    checkpoint()
    df_combined = store.upsert_kline(
        sym,
        df_new,
        freq="day",
        source=actual_source,
        recompute_indicators=recompute_indicators,
    )
    new_count = max(0, len(df_combined) - old_rows)

    return {
        "symbol": sym,
        "status": "updated",
        "bars": len(df_combined),
        "end": str(df_combined["dt"].max()),
        "new_bars": new_count,
        "source": actual_source,
    }


def update_all(
    end_date: str | None = None,
    delay: float = 0.3,
    source: DataSource = "tushare",
    max_workers: int = 1,
    recompute_indicators: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    control: ControlCallback = None,
) -> list[dict]:
    """更新所有已缓存股票，返回每只的更新结果。

    Args:
        max_workers: 并发线程数。仅 source 在 _THREAD_SAFE_SOURCES（tdx/akshare）时生效；
            其他源（tushare/baostock）会强制降为 1。
    """
    # tdx 无限流，可以用更短的延迟
    if source == "tdx" and delay >= 0.3:
        delay = 0.05

    syms = [p.stem for p in sorted(CACHE_DIR.glob("*.parquet"))]
    return update_symbols(
        syms,
        end_date=end_date,
        delay=delay,
        source=source,
        max_workers=max_workers,
        recompute_indicators=recompute_indicators,
        on_progress=on_progress,
        control=control,
    )


def update_symbols(
    symbols: list[str],
    end_date: str | None = None,
    delay: float = 0.05,
    source: DataSource = "tdx",
    max_workers: int = 2,
    recompute_indicators: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    fallback_sources: bool = False,
    control: ControlCallback = None,
) -> list[dict]:
    """Update a bounded symbol set with controlled concurrency and progress."""
    syms = [normalize(symbol) for symbol in dict.fromkeys(symbols)]
    if source == "tushare":
        return _update_symbols_tushare_batch(
            syms,
            end_date=end_date,
            max_workers=max_workers,
            recompute_indicators=recompute_indicators,
            on_progress=on_progress,
            fallback_sources=fallback_sources,
            control=control,
        )
    if source == "tdx" and delay >= 0.3:
        delay = 0.05
    effective_workers = max_workers if source in _THREAD_SAFE_SOURCES else 1
    if effective_workers != max_workers:
        logger.warning(
            f"[update] source={source} 不支持线程并发，max_workers 从 {max_workers} 降为 1"
        )
    if effective_workers > 1:
        return _update_all_concurrent(
            syms,
            end_date,
            source,
            effective_workers,
            recompute_indicators,
            on_progress,
            fallback_sources,
            control,
        )

    # 串行：保留原行为
    results = []
    consecutive_errors = 0
    recent_errors: list[str] = []
    checkpoint = control or (lambda: None)
    for sym in syms:
        checkpoint()
        r = update_symbol(
            sym,
            end_date,
            source=source,
            recompute_indicators=recompute_indicators,
            fallback_sources=fallback_sources,
            control=control,
        )
        results.append(r)
        if r["status"] == "error":
            consecutive_errors += 1
            recent_errors.append(f"{sym}: {r.get('error', 'unknown')}")
        else:
            consecutive_errors = 0
        logger.info(f"[update] {sym}: {r['status']} (+{r['new_bars']})")
        if on_progress:
            on_progress(len(results), len(syms), sym, r["status"])
        _check_consecutive_errors(consecutive_errors, source, recent_errors)
        if delay > 0:
            time.sleep(delay)
    return results


def _parse_date(value: str | date | None, default: date) -> date:
    if value is None:
        return default
    if isinstance(value, date):
        return value
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return date.fromisoformat(text)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i: i + size] for i in range(0, len(items), max(1, size))]


def _previous_weekday(value: date) -> date:
    current = value
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def _latest_publishable_daily_date(requested: date) -> date:
    now = datetime.now()
    if requested >= now.date() and now.hour < _DAILY_BAR_PUBLISH_HOUR:
        return now.date() - timedelta(days=1)
    return requested


def _target_trade_date(store: DataStore, end_date: str | date | None) -> date:
    """Return the latest trading date that should be present in cache.

    Natural dates cause many pointless provider calls on weekends, holidays, and
    dates whose daily bars are not yet published. Prefer the stored exchange
    calendar, and fall back to the previous weekday when no calendar exists.
    """
    requested = _latest_publishable_daily_date(_parse_date(end_date, date.today()))
    try:
        calendar = store.get_calendar(end=requested)
        if calendar.empty:
            return _previous_weekday(requested)
        return _last_trade_date(store, requested)
    except Exception:
        return _previous_weekday(requested)


def _preclassify_cached_symbols(
    symbols: list[str],
    target_end: date,
    source: DataSource,
    store: DataStore,
) -> tuple[list[str], dict[str, dict]]:
    """Split symbols into pending downloads and already-complete cache hits."""
    last_dates = store.get_last_dates(symbols, "day")
    entries = store.catalog.entries("day")
    pending: list[str] = []
    skipped: dict[str, dict] = {}
    for symbol in symbols:
        last = last_dates.get(symbol)
        if last is not None and last >= target_end:
            entry = entries.get(symbol)
            skipped[symbol] = {
                "symbol": symbol,
                "status": "up_to_date",
                "bars": entry.rows if entry is not None else 0,
                "end": str(last),
                "new_bars": 0,
                "source": source,
                "cached": True,
            }
        else:
            pending.append(symbol)
    return pending, skipped


def _update_symbols_tushare_batch(
    symbols: list[str],
    end_date: str | date | None = None,
    max_workers: int = 4,
    recompute_indicators: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    fallback_sources: bool = False,
    default_start: str | date | None = None,
    store: DataStore | None = None,
    control: ControlCallback = None,
) -> list[dict]:
    """Update many symbols using Tushare multi-code or trade-date queries."""
    if not symbols:
        return []

    store = store or get_store()
    end_d = _target_trade_date(store, end_date)
    pending_symbols, cached_results = _preclassify_cached_symbols(
        symbols, end_d, "tushare", store
    )
    symbols = [
        symbol for symbol in symbols
        if symbol in cached_results or symbol in pending_symbols
    ]
    last_dates = store.get_last_dates(pending_symbols, "day")
    groups: dict[str, list[str]] = {}
    results: dict[str, dict] = dict(cached_results)

    for symbol in pending_symbols:
        last = last_dates.get(symbol)
        start_d = (
            last + timedelta(days=1)
            if last is not None
            else _parse_date(default_start, end_d - timedelta(days=365 * 3))
        )
        if start_d > end_d:
            results[symbol] = {
                "symbol": symbol,
                "status": "up_to_date",
                "new_bars": 0,
                "source": "tushare",
            }
            continue
        groups.setdefault(start_d.strftime("%Y%m%d"), []).append(symbol)

    completed = 0
    total = len(symbols)
    checkpoint = control or (lambda: None)

    def record(symbol: str, result: dict) -> None:
        nonlocal completed
        results[symbol] = result
        completed += 1
        progress_status = result["status"]
        if progress_status == "error" and result.get("error"):
            progress_status = f"error:{str(result['error'])[:300]}"
        logger.info(
            f"[update] {symbol}: {result['status']} "
            f"(+{result.get('new_bars', 0)})"
        )
        if on_progress:
            on_progress(completed, total, symbol, progress_status)
        checkpoint()

    for symbol in symbols:
        if symbol in results:
            record(symbol, results[symbol])

    if not groups:
        return [results[symbol] for symbol in symbols]

    def request_progress(api_name: str, done: int, request_total: int) -> None:
        if on_progress:
            on_progress(
                completed,
                total,
                "TUSHARE",
                f"downloading:{api_name} request {done}/{request_total}",
            )
        checkpoint()

    source_client = TushareSource(
        checkpoint=checkpoint,
        request_progress=request_progress,
    )

    def fetch_group_resilient(
        group: list[str],
        start: str,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        try:
            return (
                source_client.fetch_daily_many(
                    group,
                    start,
                    end_d.strftime("%Y%m%d"),
                ),
                {},
            )
        except DataOperationCancelled:
            raise
        except Exception as exc:
            if len(group) <= 1:
                return {}, {group[0]: str(exc)}
            logger.warning(
                f"Tushare batch failed for {len(group)} symbols "
                f"({start}-{end_d:%Y%m%d}), splitting: {exc}"
            )
            middle = len(group) // 2
            left_frames, left_errors = fetch_group_resilient(group[:middle], start)
            right_frames, right_errors = fetch_group_resilient(group[middle:], start)
            return {**left_frames, **right_frames}, {**left_errors, **right_errors}

    for start, group in groups.items():
        for chunk in _chunks(group, _TUSHARE_UPDATE_CHUNK_SIZE):
            checkpoint()
            logger.info(
                f"Tushare update chunk: {len(chunk)} symbols "
                f"({start}-{end_d:%Y%m%d})"
            )
            frames, symbol_errors = fetch_group_resilient(chunk, start)

            def process(symbol: str) -> dict:
                checkpoint()
                frame = frames.get(symbol, pd.DataFrame())
                if frame is None or frame.empty:
                    if fallback_sources:
                        return update_symbol(
                            symbol,
                            str(end_d),
                            source="tdx",
                            recompute_indicators=recompute_indicators,
                            fallback_sources=False,
                            control=control,
                        )
                    if symbol in symbol_errors:
                        return {
                            "symbol": symbol,
                            "status": "error",
                            "error": symbol_errors[symbol],
                            "new_bars": 0,
                            "source": "tushare",
                        }
                    entry = store.catalog.get(symbol, "day")
                    return {
                        "symbol": symbol,
                        "status": "no_new_data",
                        "bars": entry.rows if entry is not None else 0,
                        "new_bars": 0,
                        "source": "tushare",
                    }

                entry = store.catalog.get(symbol, "day")
                old_rows = entry.rows if entry is not None else 0
                checkpoint()
                combined = store.upsert_kline(
                    symbol,
                    frame,
                    freq="day",
                    source="tushare",
                    recompute_indicators=recompute_indicators,
                )
                return {
                    "symbol": symbol,
                    "status": "updated",
                    "bars": len(combined),
                    "end": str(combined["dt"].max()),
                    "new_bars": max(0, len(combined) - old_rows),
                    "source": "tushare",
                }

            workers = max(1, min(int(max_workers), 8, len(chunk)))
            if workers == 1:
                for symbol in chunk:
                    record(symbol, process(symbol))
            else:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    for symbol, future in bounded_futures(
                        executor,
                        chunk,
                        process,
                        max_pending=workers * 2,
                    ):
                        try:
                            result = future.result()
                        except DataOperationCancelled:
                            raise
                        except Exception as exc:
                            result = {
                                "symbol": symbol,
                                "status": "error",
                                "error": str(exc),
                                "new_bars": 0,
                                "source": "tushare",
                            }
                        record(symbol, result)

    return [results[symbol] for symbol in symbols]


def _update_all_concurrent(
    syms: list[str],
    end_date: str | None,
    source: DataSource,
    max_workers: int,
    recompute_indicators: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    fallback_sources: bool = False,
    control: ControlCallback = None,
) -> list[dict]:
    """并发版 update_all（仅 thread-safe 源使用）。"""
    results: list[dict] = []
    consecutive_errors = 0
    recent_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for sym, fut in bounded_futures(
            ex,
            syms,
            lambda item: update_symbol(
                item,
                end_date,
                source,
                recompute_indicators=recompute_indicators,
                fallback_sources=fallback_sources,
                control=control,
            ),
            max_pending=max_workers * 2,
        ):
            try:
                r = fut.result()
            except DataOperationCancelled:
                raise
            except Exception as e:
                r = {"symbol": sym, "status": "error", "error": str(e), "new_bars": 0}
            results.append(r)
            if r["status"] == "error":
                consecutive_errors += 1
                recent_errors.append(f"{sym}: {r.get('error', 'unknown')}")
            else:
                consecutive_errors = 0
            logger.info(f"[update] {sym}: {r['status']} (+{r.get('new_bars', 0)})")
            if on_progress:
                on_progress(len(results), len(syms), sym, r["status"])
            _check_consecutive_errors(consecutive_errors, source, recent_errors)
    return results


def fetch_all_a_symbols_tushare() -> list[dict]:
    """从 Tushare 获取全部 A 股上市股票列表。"""
    rows = TushareSource().list_symbols()
    rows = filter_a_share_rows(rows)
    if rows:
        try:
            out_path = get_store().meta_path("symbols")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_parquet(out_path, index=False)
        except Exception as exc:
            logger.warning(f"failed to persist Tushare universe cache: {exc}")
        return rows

    try:
        universe = get_store().get_universe()
        if not universe.empty and "symbol" in universe.columns:
            cached = filter_a_share_rows(universe.to_dict("records"))
            if cached:
                logger.warning(
                    "Tushare stock_basic returned no symbols; using cached "
                    f"universe with {len(cached)} symbols"
                )
                return cached
    except Exception as exc:
        logger.warning(f"failed to read cached universe after Tushare miss: {exc}")
    return []


def fetch_all_a_symbols_tdx() -> list[dict]:
    """获取全部 A 股股票列表（用 Baostock，TDX 服务器对单连接列表拉取量限制太严，
    实测仅能取到部分 SZ）。Baostock 一次 HTTP 即返全 5000+ 只，稳定可靠。
    """
    return fetch_all_a_symbols_baostock()


def fetch_all_a_symbols_akshare() -> list[dict]:
    """从 AKShare 获取全 A 股上市股票列表（基础信息）。"""
    import akshare as ak
    df = ak.stock_info_a_code_name()
    if df is None or df.empty:
        return []
    records = []
    for _, r in df.iterrows():
        code = str(r["code"]).zfill(6)
        if not is_a_share_symbol(code):
            continue
        records.append({
            "symbol": code,
            "name": r.get("name", ""),
            "industry": "",
            "market": "SH" if code.startswith(("6", "9")) else "SZ",
            "list_date": "",
        })
    return filter_a_share_rows(records)


def fetch_all_a_symbols(source: DataSource = "tushare") -> list[dict]:
    """获取全部 A 股上市股票列表。"""
    if source == "tdx":
        return filter_a_share_rows(fetch_all_a_symbols_tdx())
    if source == "akshare":
        return filter_a_share_rows(fetch_all_a_symbols_akshare())
    if source == "baostock":
        return filter_a_share_rows(fetch_all_a_symbols_baostock())
    return filter_a_share_rows(fetch_all_a_symbols_tushare())


def download_all_a(
    start_date: str | None = None,
    end_date: str | None = None,
    delay: float = 0.3,
    on_progress: callable = None,
    source: DataSource = "tdx",
    max_workers: int = 4,
    recompute_indicators: bool = False,
    symbols_info: list[dict] | None = None,
    fallback_sources: bool = True,
    control: ControlCallback = None,
) -> dict:
    """下载全 A 股日线数据到缓存。

    Args:
        source: 数据源, "tushare" / "tdx" / "akshare" / "baostock"
            tdx 是默认源（实测最快、并发安全）
        max_workers: 并发线程数。仅 source 在 _THREAD_SAFE_SOURCES（tdx/akshare）时生效；
            tushare（频次限流）、baostock（全局 session 非线程安全）会强制降为 1。

    Returns: {"total": N, "success": N, "skipped": N, "failed": N, "errors": [...]}
    """
    store = get_store()
    target_end = _target_trade_date(store, end_date)
    today = str(target_end)
    start = start_date or str(target_end - timedelta(days=365 * 3))

    # tdx 无限流，减少延迟
    if source == "tdx" and delay >= 0.3:
        delay = 0.05

    effective_workers = max_workers if source in _THREAD_SAFE_SOURCES else 1
    if effective_workers != max_workers:
        logger.warning(
            f"[download_all] source={source} 不支持线程并发，max_workers 从 {max_workers} 降为 1"
        )

    if symbols_info is None:
        symbols_info = fetch_all_a_symbols(source=source)
    normalized_symbols = [normalize(info["symbol"]) for info in symbols_info]
    pending_symbols, cached_results = _preclassify_cached_symbols(
        normalized_symbols, target_end, source, store
    )
    if cached_results:
        logger.info(
            f"[download_all] cache hit: {len(cached_results)} already up to {target_end}"
        )
    total = len(symbols_info)
    success = skipped = failed = 0
    errors: list[str] = []
    consecutive_errors = 0

    if not pending_symbols:
        for completed, symbol in enumerate(normalized_symbols, start=1):
            if on_progress:
                on_progress(completed, total, symbol, "skipped")
            if control:
                control()
        return {
            "total": total,
            "success": 0,
            "skipped": total,
            "failed": 0,
            "errors": [],
        }

    if source == "tushare":
        rows = _update_symbols_tushare_batch(
            normalized_symbols,
            end_date=today,
            max_workers=max_workers,
            recompute_indicators=recompute_indicators,
            on_progress=on_progress,
            fallback_sources=fallback_sources,
            default_start=start,
            control=control,
        )
        return {
            "total": len(rows),
            "success": sum(row["status"] == "updated" for row in rows),
            "skipped": sum(
                row["status"] in {"up_to_date", "no_new_data"} for row in rows
            ),
            "failed": sum(row["status"] == "error" for row in rows),
            "errors": [
                f'{row["symbol"]}: {row.get("error", "unknown")}'
                for row in rows
                if row["status"] == "error"
            ][:50],
        }

    logger.info(
        f"[download_all] Starting download for {total} stocks via {source}, "
        f"{start} ~ {today}, workers={effective_workers}"
    )

    def _process(info: dict) -> tuple[str, str, str | None]:
        """处理单只股票，返回 (symbol, status, error_msg)。status ∈ {ok, skipped, error}"""
        sym = info["symbol"]
        if control:
            control()
        try:
            r = update_symbol(
                sym,
                today,
                source=source,
                recompute_indicators=recompute_indicators,
                fallback_sources=fallback_sources,
                control=control,
            )
            if r["status"] == "error":
                return (sym, "error", r.get("error", "unknown"))
            if r["status"] in {"up_to_date", "no_new_data"}:
                return (sym, "skipped", None)
            return (sym, "ok", None)
        except DataOperationCancelled:
            raise
        except Exception as e:
            return (sym, "error", str(e))

    pending_set = set(pending_symbols)
    pending_info = [
        {"symbol": symbol}
        for symbol in normalized_symbols
        if symbol in pending_set
    ]

    if effective_workers > 1:
        # 并发模式：on_progress 在主线程的 as_completed 消费侧调用，与原契约一致
        with ThreadPoolExecutor(max_workers=effective_workers) as ex:
            completed = 0
            for symbol in normalized_symbols:
                if symbol not in cached_results:
                    continue
                skipped += 1
                completed += 1
                if on_progress:
                    on_progress(completed, total, symbol, "skipped")
            for i, (_, fut) in enumerate(
                bounded_futures(
                    ex,
                    pending_info,
                    _process,
                    max_pending=effective_workers * 2,
                )
            ):
                try:
                    sym, status, err = fut.result()
                except DataOperationCancelled:
                    raise
                if status == "ok":
                    success += 1
                elif status == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    if err:
                        errors.append(f"{sym}: {err}")
                    consecutive_errors += 1
                if status != "error":
                    consecutive_errors = 0
                if on_progress:
                    on_progress(completed + i + 1, total, sym, status)
                _check_consecutive_errors(consecutive_errors, source, errors)
    else:
        # 串行模式：保留原 sleep 节流
        completed = 0
        for symbol in normalized_symbols:
            if symbol not in cached_results:
                continue
            skipped += 1
            completed += 1
            if on_progress:
                on_progress(completed, total, symbol, "skipped")
        for i, info in enumerate(pending_info):
            if control:
                control()
            sym, status, err = _process(info)
            if status == "ok":
                success += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
                if err:
                    errors.append(f"{sym}: {err}")
                consecutive_errors += 1
            if status != "error":
                consecutive_errors = 0
            if on_progress:
                on_progress(completed + i + 1, total, sym, "done" if status != "skipped" else "skipped")
            _check_consecutive_errors(consecutive_errors, source, errors)
            if delay > 0 and status != "skipped":
                time.sleep(delay)

    logger.info(f"[download_all] Done: {success} ok, {skipped} skipped, {failed} failed")
    return {
        "total": total,
        "success": success,
        "skipped": skipped,
        "failed": failed,
        "errors": errors[:50],
    }


# ════════════════════════════════════════════════════════════════════════════
#                            新 API（DataStore 后端）
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class UpdateReport:
    """``update_universe`` 的执行汇总。"""
    total: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    elapsed_s: float = 0.0
    by_source: dict[str, int] = field(default_factory=dict)


# 各源独立 Semaphore：tdx 多并发，akshare 适中，tushare 严格限流
_SOURCE_SEMAPHORES: dict[str, threading.Semaphore] = {
    "tdx": threading.Semaphore(_SETTINGS.tdx.workers),
    "akshare": threading.Semaphore(_SETTINGS.data.akshare_workers),
    "tushare": threading.Semaphore(_SETTINGS.tushare.workers),
    "csv": threading.Semaphore(8),
}
# tushare 还需要每次调用之间留 200ms（free-tier 200/min 限额）
_SOURCE_INTERCALL_DELAY = {"tushare": 60.0 / _SETTINGS.tushare.rpm}
_TUSHARE_RATE_LOCK = threading.Lock()
_TUSHARE_LAST_CALL_AT = 0.0


def _fetch_with_throttle(source: Source, symbol: str, start: str, end: str) -> pd.DataFrame:
    global _TUSHARE_LAST_CALL_AT
    sem = _SOURCE_SEMAPHORES.get(source.name, threading.Semaphore(2))
    with sem:
        delay = _SOURCE_INTERCALL_DELAY.get(source.name, 0.0)
        if delay > 0:
            with _TUSHARE_RATE_LOCK:
                now = time.monotonic()
                wait_s = max(0.0, delay - (now - _TUSHARE_LAST_CALL_AT))
                if wait_s > 0:
                    time.sleep(wait_s)
                _TUSHARE_LAST_CALL_AT = time.monotonic()
        return source.fetch_daily(symbol, start, end)


def _last_trade_date(store: DataStore, end_date: date) -> date:
    """返回 ``<= end_date`` 的最近一个交易日；日历缺失则返回 ``end_date``。"""
    cal = store.get_calendar()
    if cal.empty:
        return end_date
    open_days = cal[(cal["is_open"] == True) & (cal["dt"] <= end_date)]  # noqa: E712
    if open_days.empty:
        return end_date
    return open_days["dt"].max()


def _to_yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _update_one(
    symbol: str,
    freq: Freq,
    sources: list[Source],
    end_date: date,
    last_trade: date,
    existing_last: date | None,
    force: bool,
    recompute_indicators: bool,
    store: DataStore,
) -> dict:
    """单只股票更新 — 在每个 source 间回退；返回结果 dict。"""
    sym = normalize(symbol)
    path = store.kline_path(sym, freq)

    # 1. 计算起始日期
    if force or not path.exists():
        start = date(end_date.year - 3, 1, 1)
        existing_last = None
    else:
        if existing_last is None:
            start = date(end_date.year - 3, 1, 1)
        else:
            start = existing_last + timedelta(days=1)

    if not force and existing_last is not None and existing_last >= last_trade:
        return {"symbol": sym, "status": "up_to_date", "source": "", "new_bars": 0}

    if start > end_date:
        return {"symbol": sym, "status": "up_to_date", "source": "", "new_bars": 0}

    # 2. 源回退链
    start_s = _to_yyyymmdd(start)
    end_s = _to_yyyymmdd(end_date)
    last_err = ""
    for src in sources:
        try:
            df_new = _fetch_with_throttle(src, sym, start_s, end_s)
            if df_new is None or df_new.empty:
                last_err = f"{src.name}: empty"
                continue
            df_new = normalize_kline(df_new)
            store.upsert_kline(
                sym,
                df_new,
                freq=freq,
                source=src.name,
                recompute_indicators=recompute_indicators,
            )
            return {
                "symbol": sym,
                "status": "updated",
                "source": src.name,
                "new_bars": len(df_new),
            }
        except Exception as e:
            last_err = f"{src.name}: {e}"
            logger.debug(f"[update_universe] {sym} {src.name} failed: {e}")
            continue

    return {"symbol": sym, "status": "error", "source": "", "new_bars": 0, "error": last_err}


def update_universe(
    symbols: list[str] | None = None,
    freq: Freq = "day",
    end_date: date | str | None = None,
    sources: list[Source] | None = None,
    workers: int = 4,
    force: bool = False,
    recompute_indicators: bool = True,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    store: DataStore | None = None,
) -> UpdateReport:
    """并发 + 源回退增量更新。

    Args:
        symbols: 待更新股票列表；``None`` 时使用 ``store.list_symbols(freq)``
        freq: ``"day"`` / ``"week"`` / ``"month"``。week/month 不走外网，从 day 重采样
        end_date: 截止日期
        sources: 数据源回退链；``None`` 时为 ``[TDXSource, AKShareSource, TushareSource]``
        workers: 并发线程数
        force: 是否强制全量重拉
        on_progress: ``(done, total, symbol, status) -> None``
    """
    if freq != "day":
        return derive_week_month(symbols=symbols, target_freq=freq, store=store)

    store = store or get_store()
    if end_date is None:
        end_d = date.today()
    elif isinstance(end_date, str):
        end_d = date.fromisoformat(end_date)
    else:
        end_d = end_date

    if symbols is None:
        symbols = store.list_symbols(freq)
    symbols = [normalize(s) for s in symbols]
    sources = sources or [TDXSource(), AKShareSource(), TushareSource()]

    report = UpdateReport(total=len(symbols))
    t0 = time.monotonic()
    if (
        not force
        and len(sources) == 1
        and sources[0].name == "tushare"
    ):
        rows = _update_symbols_tushare_batch(
            symbols,
            end_date=end_d,
            max_workers=workers,
            recompute_indicators=recompute_indicators,
            on_progress=on_progress,
            store=store,
        )
        for row in rows:
            if row["status"] == "updated":
                report.updated += 1
                report.by_source["tushare"] = report.by_source.get("tushare", 0) + 1
            elif row["status"] in {"up_to_date", "no_new_data"}:
                report.skipped += 1
            else:
                report.failed += 1
                if row.get("error") and len(report.errors) < 200:
                    report.errors.append((row["symbol"], row["error"]))
        report.elapsed_s = time.monotonic() - t0
        return report

    last_trade = _last_trade_date(store, end_d)
    last_dates = store.get_last_dates(symbols, freq)

    def _runner(sym: str) -> dict:
        return _update_one(
            sym,
            freq,
            sources,
            end_d,
            last_trade,
            last_dates.get(sym),
            force,
            recompute_indicators,
            store,
        )

    def _record(r: dict) -> None:
        if r["status"] == "updated":
            report.updated += 1
            report.by_source[r["source"]] = report.by_source.get(r["source"], 0) + 1
        elif r["status"] in ("up_to_date", "skipped"):
            report.skipped += 1
        else:
            report.failed += 1
            if r.get("error") and len(report.errors) < 200:
                report.errors.append((r["symbol"], r["error"]))

    workers = max(1, min(int(workers), 8))
    if workers <= 1 or len(symbols) <= 1:
        for i, sym in enumerate(symbols):
            r = _runner(sym)
            _record(r)
            if on_progress:
                on_progress(i + 1, len(symbols), sym, r["status"])
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, (sym, fut) in enumerate(
                bounded_futures(
                    ex,
                    symbols,
                    _runner,
                    max_pending=workers * 2,
                )
            ):
                try:
                    r = fut.result()
                except Exception as e:
                    r = {"symbol": sym, "status": "error", "source": "",
                         "new_bars": 0, "error": str(e)}
                _record(r)
                if on_progress:
                    on_progress(i + 1, len(symbols), sym, r["status"])

    report.elapsed_s = time.monotonic() - t0

    logger.info(
        f"[update_universe] freq={freq} total={report.total} "
        f"updated={report.updated} skipped={report.skipped} failed={report.failed} "
        f"by_source={report.by_source} elapsed={report.elapsed_s:.1f}s"
    )
    return report


def refresh_calendar(store: DataStore | None = None) -> int:
    """从 AKShare 拉取 A 股交易日历，写入 ``data/meta/trade_calendar.parquet``。"""
    import akshare as ak

    store = store or get_store()
    df = ak.tool_trade_date_hist_sina()
    df = df.rename(columns={"trade_date": "dt"})
    df["dt"] = pd.to_datetime(df["dt"]).dt.date
    df["is_open"] = True

    # 补全到月末 — 非交易日填 is_open=False
    full = pd.DataFrame({
        "dt": pd.date_range(df["dt"].min(), df["dt"].max(), freq="D").date,
    })
    full = full.merge(df[["dt", "is_open"]], on="dt", how="left")
    full["is_open"] = full["is_open"].fillna(False)
    full["dt"] = pd.to_datetime(full["dt"]).dt.date

    # 周/月收盘标记：连续 is_open 日期里每周/每月最后一天
    full = full.sort_values("dt").reset_index(drop=True)
    full["week_close"] = False
    full["month_close"] = False
    open_idx = full.index[full["is_open"]].tolist()
    if open_idx:
        s = pd.Series([full.at[i, "dt"] for i in open_idx])
        # week_close = 该周最后一个交易日
        weeks = pd.to_datetime(s).dt.isocalendar()
        last_per_week = s.groupby([weeks["year"], weeks["week"]]).idxmax()
        for orig_pos in last_per_week.values:
            full.at[open_idx[orig_pos], "week_close"] = True
        # month_close = 该月最后一个交易日
        months = pd.to_datetime(s).dt.to_period("M")
        last_per_month = s.groupby(months).idxmax()
        for orig_pos in last_per_month.values:
            full.at[open_idx[orig_pos], "month_close"] = True

    out_path = store.meta_path("trade_calendar")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    full.to_parquet(out_path, index=False)
    logger.info(f"[refresh_calendar] wrote {len(full)} dates ({full['is_open'].sum()} open)")
    return int(full["is_open"].sum())


def refresh_universe(source: Source | None = None, store: DataStore | None = None) -> int:
    """更新 ``data/meta/symbols.parquet`` 全 A 股代码列表。"""
    store = store or get_store()
    src = source or AKShareSource()
    rows = src.list_symbols()
    if not rows:
        logger.warning(f"[refresh_universe] {src.name} returned 0 symbols")
        return 0
    rows = filter_a_share_rows(rows, include_bj=src.name != "tdx")
    if not rows:
        logger.warning(f"[refresh_universe] {src.name} returned 0 A-share symbols after filtering")
        return 0
    df = pd.DataFrame(rows)
    out_path = store.meta_path("symbols")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    logger.info(f"[refresh_universe] wrote {len(df)} symbols via {src.name}")
    return len(df)


_RESAMPLE_RULES = {"week": "W-FRI", "month": "ME"}


def derive_week_month(
    symbols: list[str] | None = None,
    target_freq: Freq = "week",
    store: DataStore | None = None,
) -> UpdateReport:
    """从 day parquet 重采样得到 week/month parquet。永不走外网。"""
    if target_freq not in ("week", "month"):
        raise ValueError(f"target_freq must be week/month, got {target_freq}")
    store = store or get_store()
    rule = _RESAMPLE_RULES[target_freq]

    if symbols is None:
        symbols = store.list_symbols("day")
    symbols = [normalize(s) for s in symbols]

    report = UpdateReport(total=len(symbols))
    t0 = time.monotonic()
    for sym in symbols:
        try:
            day = store.get_kline(sym, freq="day")
            if day.empty:
                report.skipped += 1
                continue
            day = day.copy()
            day["dt"] = pd.to_datetime(day["dt"])
            day = day.set_index("dt")
            agg = day.resample(rule).agg({
                "open": "first", "high": "max", "low": "min", "close": "last",
                "volume": "sum", "amount": "sum",
            }).dropna(subset=["close"]).reset_index()
            agg["dt"] = agg["dt"].dt.date
            store.upsert_kline(sym, agg, freq=target_freq, source=f"resample:{target_freq}")
            report.updated += 1
        except Exception as e:
            report.failed += 1
            report.errors.append((sym, str(e)))
            logger.debug(f"[derive_week_month] {sym} failed: {e}")
    report.elapsed_s = time.monotonic() - t0
    logger.info(
        f"[derive_week_month] freq={target_freq} updated={report.updated} "
        f"skipped={report.skipped} failed={report.failed} elapsed={report.elapsed_s:.1f}s"
    )
    return report
