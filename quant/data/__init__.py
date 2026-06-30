"""Data layer public API."""
from __future__ import annotations

from . import indicators
from .indicators import INDICATORS, IndicatorSpec, all_indicator_columns, compute, compute_all

_LAZY_EXPORTS = {
    "AKShareSource": (".feeds", "AKShareSource"),
    "CSVSource": (".feeds", "CSVSource"),
    "Source": (".feeds", "Source"),
    "StoreFeed": (".feeds", "StoreFeed"),
    "TDXSource": (".feeds", "TDXSource"),
    "TushareSource": (".feeds", "TushareSource"),
    "default_sources": (".feeds", "default_sources"),
    "ALL_FREQS": (".schema", "ALL_FREQS"),
    "OHLCV_COLUMNS": (".schema", "OHLCV_COLUMNS"),
    "Freq": (".schema", "Freq"),
    "normalize_kline": (".schema", "normalize_kline"),
    "DataStore": (".store", "DataStore"),
    "get_store": (".store", "get_store"),
    "reset_store": (".store", "reset_store"),
    "market": (".symbols", "market"),
    "normalize": (".symbols", "normalize"),
    "to_tdx_market": (".symbols", "to_tdx_market"),
    "to_ts_code": (".symbols", "to_ts_code"),
    "UpdateReport": (".updater", "UpdateReport"),
    "derive_week_month": (".updater", "derive_week_month"),
    "refresh_calendar": (".updater", "refresh_calendar"),
    "refresh_universe": (".updater", "refresh_universe"),
    "update_universe": (".updater", "update_universe"),
}


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _LAZY_EXPORTS[name]
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value

__all__ = [
    "AKShareSource",
    "ALL_FREQS",
    "CSVSource",
    "DataStore",
    "Freq",
    "INDICATORS",
    "IndicatorSpec",
    "OHLCV_COLUMNS",
    "Source",
    "StoreFeed",
    "TDXSource",
    "TushareSource",
    "UpdateReport",
    "all_indicator_columns",
    "compute",
    "compute_all",
    "default_sources",
    "derive_week_month",
    "get_store",
    "indicators",
    "market",
    "normalize",
    "normalize_kline",
    "refresh_calendar",
    "refresh_universe",
    "reset_store",
    "to_tdx_market",
    "to_ts_code",
    "update_universe",
]
