from __future__ import annotations

from datetime import date
import math
from typing import Any

from quant.data import INDICATORS, get_store
from quant.data.schema import Freq, OHLCV_COLUMNS
from quant.data.symbol_filter import filter_a_share_rows
from server.models.backtest import KlineBar


def get_kline(
    symbol: str,
    start_date: str,
    end_date: str,
    freq: Freq = "day",
    with_indicators: bool | list[str] = False,
) -> list[KlineBar]:
    store = get_store()
    df = store.get_kline(
        symbol, freq=freq,
        start=date.fromisoformat(start_date),
        end=date.fromisoformat(end_date),
        with_indicators=with_indicators,
        columns=None if with_indicators else OHLCV_COLUMNS,
    )
    if df.empty:
        return []
    bars: list[KlineBar] = []
    for r in df.itertuples(index=False):
        bars.append(KlineBar(
            dt=str(r.dt),
            open=round(float(r.open), 4),
            high=round(float(r.high), 4),
            low=round(float(r.low), 4),
            close=round(float(r.close), 4),
            volume=float(r.volume),
        ))
    return bars


def get_indicator(
    symbol: str,
    name: str,
    start_date: str,
    end_date: str,
    freq: Freq = "day",
) -> list[dict]:
    """返回 ``[{dt, <output_columns...>}]``，前端可直接绘图。"""
    if name.upper() not in INDICATORS:
        raise ValueError(f"unknown indicator: {name}; available: {list(INDICATORS)}")
    store = get_store()
    df = store.get_indicator(
        symbol, name,
        start=date.fromisoformat(start_date),
        end=date.fromisoformat(end_date),
        freq=freq,
    )
    if df.empty:
        return []
    return [
        {
            **{c: (str(v) if c == "dt" else (None if v is None or v != v else round(float(v), 6)))
               for c, v in zip(df.columns, row)}
        }
        for row in df.itertuples(index=False)
    ]


def get_universe(market: str | None = None) -> list[dict]:
    df = get_store().get_universe(market=market)
    if df.empty:
        return []
    rows = filter_a_share_rows(df.to_dict(orient="records"))
    return [
        {key: _json_safe(value) for key, value in row.items()}
        for row in rows
    ]


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def get_calendar(start: str | None = None, end: str | None = None) -> list[dict]:
    store = get_store()
    df = store.get_calendar(
        start=date.fromisoformat(start) if start else None,
        end=date.fromisoformat(end) if end else None,
    )
    if df.empty:
        return []
    out: list[dict] = []
    for r in df.itertuples(index=False):
        rec = {"dt": str(r.dt), "is_open": bool(r.is_open)}
        if hasattr(r, "week_close"):
            rec["week_close"] = bool(r.week_close)
        if hasattr(r, "month_close"):
            rec["month_close"] = bool(r.month_close)
        out.append(rec)
    return out


def get_cache_status() -> list[dict]:
    """``[{symbol, freq, last_dt, source, ts_updated}]``。"""
    store = get_store()
    df = store.last_update()
    if df.empty:
        # Fallback: derive from filenames
        return [{"symbol": s, "freq": "day"} for s in store.list_symbols("day")]
    df = df.copy()
    df["last_dt"] = df["last_dt"].astype(str)
    df["ts_updated"] = df["ts_updated"].astype(str)
    return df.to_dict(orient="records")
