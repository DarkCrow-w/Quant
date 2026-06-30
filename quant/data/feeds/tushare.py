"""Tushare daily-bar source with request-efficient batch downloads."""
from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable, Iterable

import pandas as pd
import requests
from loguru import logger

from quant.config import get_settings
from ..symbols import normalize, to_ts_code

DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,vol,amount"
ADJ_FIELDS = "ts_code,trade_date,adj_factor"
TUSHARE_ROW_LIMIT = 6000
TUSHARE_SAFE_ROWS = 5500
TUSHARE_MAX_CODES_PER_REQUEST = 400
TUSHARE_PRO_URL = "https://api.tushare.pro"
_RATE_LIMIT_RE = re.compile(r"(\d+)\s*次\s*/\s*分钟")

_PRO_CLIENT = None
_PRO_CLIENT_LOCK = threading.Lock()
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


class TushareRateLimitError(RuntimeError):
    """Tushare rejected the request because the account request quota was hit."""

    def __init__(self, message: str, limit_per_minute: int | None = None) -> None:
        super().__init__(message)
        self.limit_per_minute = limit_per_minute


class _HttpTushareClient:
    """Small Tushare Pro HTTP client with explicit request timeouts."""

    def __init__(
        self,
        token: str,
        timeout: float,
        session: requests.Session | None = None,
    ) -> None:
        self.token = token
        self.timeout = timeout
        self.session = session or requests.Session()

    def query(self, api_name: str, **kwargs) -> pd.DataFrame:
        if not self.token:
            raise RuntimeError("TUSHARE_TOKEN is not configured")
        fields = str(kwargs.pop("fields", "") or "")
        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": kwargs,
            "fields": fields,
        }
        try:
            response = self.session.post(
                TUSHARE_PRO_URL,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise TimeoutError(
                f"Tushare {api_name} request timed out after {self.timeout:g}s"
            ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"Tushare {api_name} HTTP request failed: {exc}") from exc

        body = response.json()
        code = int(body.get("code", -1))
        if code != 0:
            message = str(body.get("msg") or body.get("message") or "unknown error")
            match = _RATE_LIMIT_RE.search(message)
            if match or "频率超限" in message or "rate limit" in message.lower():
                limit = int(match.group(1)) if match else None
                raise TushareRateLimitError(
                    f"Tushare {api_name} rate limited: {message} (code={code})",
                    limit_per_minute=limit,
                )
            raise RuntimeError(f"Tushare {api_name} failed: {message} (code={code})")
        data = body.get("data") or {}
        fields = data.get("fields") or []
        items = data.get("items") or []
        return pd.DataFrame(items, columns=fields)

    def __getattr__(self, api_name: str):
        def call(**kwargs) -> pd.DataFrame:
            return self.query(api_name, **kwargs)

        return call


def _get_pro():
    global _PRO_CLIENT
    if _PRO_CLIENT is not None:
        return _PRO_CLIENT
    with _PRO_CLIENT_LOCK:
        if _PRO_CLIENT is None:
            settings = get_settings().tushare
            _PRO_CLIENT = _HttpTushareClient(
                token=settings.token,
                timeout=settings.timeout,
            )
    return _PRO_CLIENT


def _empty_daily() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["dt", "open", "high", "low", "close", "volume", "amount"]
    )


def _normalise_daily(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty_daily()
    out = df.rename(columns={"trade_date": "dt", "vol": "volume"}).copy()
    out["dt"] = pd.to_datetime(out["dt"]).dt.date
    out["volume"] = out["volume"].astype(float) * 100
    out["amount"] = out["amount"].astype(float) * 1000
    keep = ["dt", "open", "high", "low", "close", "volume", "amount"]
    if "adj_factor" in out.columns:
        keep.append("adj_factor")
    return out[keep].sort_values("dt").reset_index(drop=True)


class TushareSource:
    name = "tushare"

    def __init__(
        self,
        pro=None,
        request_interval: float | None = None,
        retries: int | None = None,
        checkpoint: Callable[[], None] | None = None,
        request_progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self._client = pro
        self._calendar_cache: dict[tuple[str, str], list[str]] = {}
        self._checkpoint = checkpoint or (lambda: None)
        self._request_progress = request_progress
        settings = get_settings().tushare
        rpm = settings.rpm
        self._request_interval = (
            max(0.0, request_interval)
            if request_interval is not None
            else 60.0 / rpm
        )
        self._retries = settings.retries if retries is None else max(0, retries)
        self._timeout = settings.timeout

    @property
    def pro(self):
        if self._client is None:
            self._client = _get_pro()
        return self._client

    def _query(self, api_name: str, **kwargs) -> pd.DataFrame:
        global _LAST_REQUEST_AT
        for attempt in range(self._retries + 1):
            try:
                self._checkpoint()
                with _REQUEST_LOCK:
                    wait_s = max(
                        0.0,
                        self._request_interval - (time.monotonic() - _LAST_REQUEST_AT),
                    )
                    if wait_s:
                        self._sleep_with_checkpoint(wait_s)
                    _LAST_REQUEST_AT = time.monotonic()
                self._checkpoint()
                result = getattr(self.pro, api_name)(**kwargs)
                return result if result is not None else pd.DataFrame()
            except TushareRateLimitError as exc:
                if exc.limit_per_minute:
                    self._request_interval = max(
                        self._request_interval,
                        60.0 / max(1, exc.limit_per_minute) * 1.15,
                    )
                if attempt >= self._retries:
                    raise
                cooldown = max(65.0, self._request_interval * 2)
                logger.warning(
                    f"Tushare {api_name} rate limited; waiting {cooldown:.1f}s "
                    f"before retry {attempt + 1}/{self._retries}"
                )
                self._sleep_with_checkpoint(cooldown)
            except Exception:
                if attempt >= self._retries:
                    raise
                self._sleep_with_checkpoint(0.5 * (2**attempt))
        return pd.DataFrame()

    def _sleep_with_checkpoint(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            self._checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.25))

    def _emit_request_progress(self, api_name: str, done: int, total: int) -> None:
        if self._request_progress is None:
            return
        self._request_progress(api_name, done, total)

    def _open_dates(self, start: str, end: str) -> list[str]:
        cache_key = (start, end)
        cached = self._calendar_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            self._emit_request_progress("trade_cal", 0, 1)
            cal = self._query(
                "trade_cal",
                exchange="SSE",
                start_date=start,
                end_date=end,
                is_open="1",
                fields="cal_date",
            )
            self._emit_request_progress("trade_cal", 1, 1)
            if cal is not None and not cal.empty:
                dates = sorted(cal["cal_date"].astype(str).tolist())
                self._calendar_cache[cache_key] = dates
                return dates
        except Exception as exc:
            logger.warning(f"Tushare trade_cal failed, using weekday estimate: {exc}")
        dates = [
            value.strftime("%Y%m%d")
            for value in pd.bdate_range(
                pd.to_datetime(start, format="%Y%m%d"),
                pd.to_datetime(end, format="%Y%m%d"),
            )
        ]
        self._calendar_cache[cache_key] = dates
        return dates

    @staticmethod
    def _choose_plan(symbol_count: int, open_day_count: int) -> tuple[str, int]:
        if open_day_count <= 0:
            return "codes", TUSHARE_MAX_CODES_PER_REQUEST
        codes_per_request = min(
            TUSHARE_MAX_CODES_PER_REQUEST,
            max(1, TUSHARE_SAFE_ROWS // open_day_count),
        )
        code_requests = math.ceil(symbol_count / codes_per_request)
        if open_day_count < code_requests:
            return "dates", codes_per_request
        return "codes", codes_per_request

    def _fetch_endpoint_many(
        self,
        api_name: str,
        symbols: list[str],
        start: str,
        end: str,
        fields: str,
    ) -> pd.DataFrame:
        open_dates = self._open_dates(start, end)
        if not open_dates:
            return pd.DataFrame()

        plan, batch_size = self._choose_plan(len(symbols), len(open_dates))
        ts_codes = [to_ts_code(symbol) for symbol in symbols]
        requested = set(ts_codes)
        frames: list[pd.DataFrame] = []

        if plan == "dates":
            logger.info(
                f"Tushare {api_name}: date batches, {len(open_dates)} requests "
                f"for {len(symbols)} symbols"
            )
            for index, trade_date in enumerate(open_dates, start=1):
                self._checkpoint()
                frame = self._query(api_name, trade_date=trade_date, fields=fields)
                if len(frame) >= TUSHARE_ROW_LIMIT:
                    raise RuntimeError(
                        f"Tushare {api_name} reached {TUSHARE_ROW_LIMIT} rows "
                        f"on {trade_date}; refusing silently truncated data"
                    )
                if not frame.empty:
                    frames.append(frame[frame["ts_code"].isin(requested)])
                self._emit_request_progress(api_name, index, len(open_dates))
        else:
            request_count = math.ceil(len(ts_codes) / batch_size)
            logger.info(
                f"Tushare {api_name}: code batches, {request_count} requests "
                f"for {len(symbols)} symbols"
            )
            for index, offset in enumerate(range(0, len(ts_codes), batch_size), start=1):
                self._checkpoint()
                chunk = ts_codes[offset : offset + batch_size]
                frames.extend(
                    self._fetch_code_chunk(
                        api_name, chunk, start, end, fields
                    )
                )
                self._emit_request_progress(api_name, index, request_count)

        non_empty = [frame for frame in frames if frame is not None and not frame.empty]
        return pd.concat(non_empty, ignore_index=True) if non_empty else pd.DataFrame()

    def _fetch_code_chunk(
        self,
        api_name: str,
        ts_codes: list[str],
        start: str,
        end: str,
        fields: str,
    ) -> list[pd.DataFrame]:
        frame = self._query(
            api_name,
            ts_code=",".join(ts_codes),
            start_date=start,
            end_date=end,
            fields=fields,
        )
        if len(frame) < TUSHARE_ROW_LIMIT:
            return [frame]
        if len(ts_codes) > 1:
            middle = len(ts_codes) // 2
            return [
                *self._fetch_code_chunk(
                    api_name, ts_codes[:middle], start, end, fields
                ),
                *self._fetch_code_chunk(
                    api_name, ts_codes[middle:], start, end, fields
                ),
            ]
        start_dt = pd.to_datetime(start, format="%Y%m%d")
        end_dt = pd.to_datetime(end, format="%Y%m%d")
        if start_dt < end_dt:
            middle_dt = start_dt + (end_dt - start_dt) // 2
            right_start = middle_dt + pd.Timedelta(days=1)
            return [
                *self._fetch_code_chunk(
                    api_name,
                    ts_codes,
                    start_dt.strftime("%Y%m%d"),
                    middle_dt.strftime("%Y%m%d"),
                    fields,
                ),
                *self._fetch_code_chunk(
                    api_name,
                    ts_codes,
                    right_start.strftime("%Y%m%d"),
                    end_dt.strftime("%Y%m%d"),
                    fields,
                ),
            ]
        raise RuntimeError(
            f"Tushare {api_name} reached {TUSHARE_ROW_LIMIT} rows for "
            f"{ts_codes[0]} between {start} and {end}"
        )

    def fetch_daily_many(
        self,
        symbols: Iterable[str],
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]:
        normalised = list(dict.fromkeys(normalize(symbol) for symbol in symbols))
        if not normalised:
            return {}

        if len(normalised) == 1:
            raw = self._query(
                "daily",
                ts_code=to_ts_code(normalised[0]),
                start_date=start,
                end_date=end,
                fields=DAILY_FIELDS,
            )
        else:
            raw = self._fetch_endpoint_many(
                "daily", normalised, start, end, DAILY_FIELDS
            )

        if get_settings().tushare.fetch_adj_factor:
            if len(normalised) == 1:
                adj = self._query(
                    "adj_factor",
                    ts_code=to_ts_code(normalised[0]),
                    start_date=start,
                    end_date=end,
                    fields=ADJ_FIELDS,
                )
            else:
                adj = self._fetch_endpoint_many(
                    "adj_factor", normalised, start, end, ADJ_FIELDS
                )
            if not raw.empty and not adj.empty:
                raw = raw.merge(adj, on=["ts_code", "trade_date"], how="left")

        result = {symbol: _empty_daily() for symbol in normalised}
        if raw is None or raw.empty:
            return result

        raw = raw.copy()
        raw["_symbol"] = raw["ts_code"].astype(str).str.split(".").str[0]
        for symbol, frame in raw.groupby("_symbol", sort=False):
            if symbol not in result:
                continue
            frame = frame.drop(columns=["_symbol"])
            if "adj_factor" in frame.columns and frame["adj_factor"].notna().any():
                frame = frame.sort_values("trade_date", ascending=False)
                latest_factor = float(frame["adj_factor"].dropna().iloc[0])
                ratio = frame["adj_factor"].ffill().bfill() / latest_factor
                for column in ["open", "high", "low", "close"]:
                    frame[column] = frame[column].astype(float) * ratio
            result[symbol] = _normalise_daily(frame)
        return result

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        sym = normalize(symbol)
        return self.fetch_daily_many([sym], start, end)[sym]

    def fetch_daily_basic(self, trade_date: str) -> pd.DataFrame:
        """Fetch one market-wide daily-basic snapshot for factor screening."""
        fields = (
            "ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,"
            "total_share,float_share,free_share,total_mv,circ_mv"
        )
        frame = self._query(
            "daily_basic",
            trade_date=trade_date,
            fields=fields,
        )
        if frame is None or frame.empty:
            return pd.DataFrame()
        out = frame.copy()
        out["symbol"] = out["ts_code"].astype(str).str.split(".").str[0]
        numeric = [
            "turnover_rate", "turnover_rate_f", "volume_ratio",
            "total_share", "float_share", "free_share", "total_mv", "circ_mv",
        ]
        for column in numeric:
            if column in out.columns:
                out[column] = pd.to_numeric(out[column], errors="coerce")
        return out[["symbol", "trade_date", *numeric]]

    def list_symbols(self) -> list[dict]:
        try:
            df = self._query(
                "stock_basic",
                exchange="",
                list_status="L",
                fields="ts_code,symbol,name,industry,market,list_date",
            )
        except Exception as exc:
            logger.warning(f"Tushare list_symbols failed: {exc}")
            return []
        out: list[dict] = []
        for row in df.itertuples(index=False):
            code = str(getattr(row, "symbol", "")).zfill(6)
            if not code.isdigit() or len(code) != 6:
                continue
            ts_code = getattr(row, "ts_code", "")
            market = ts_code.split(".")[-1] if "." in ts_code else ""
            out.append(
                {
                    "symbol": code,
                    "name": getattr(row, "name", ""),
                    "market": market,
                    "industry": getattr(row, "industry", ""),
                    "list_date": getattr(row, "list_date", ""),
                }
            )
        return out
