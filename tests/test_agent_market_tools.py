from __future__ import annotations

import pandas as pd

from server.agent.tools import market_tools


class _FakeStore:
    def get_universe(self):
        return pd.DataFrame(
            [
                {"symbol": "000700", "name": "模塑科技", "market": "SZ"},
                {"symbol": "600519", "name": "贵州茅台", "market": "SH"},
            ]
        )

    def last_update(self, symbol):
        assert symbol == "000700"
        return pd.DataFrame([{"last_dt": "2026-06-12"}])


def test_resolve_stock_symbol_by_chinese_name(monkeypatch):
    monkeypatch.setattr(market_tools, "get_store", lambda: _FakeStore())

    result = market_tools.resolve_stock_symbol("模塑科技")

    assert result["symbol"] == "000700"
    assert result["name"] == "模塑科技"
    assert result["matched_by"] == "exact_name"


def test_resolve_stock_symbol_accepts_market_suffix(monkeypatch):
    monkeypatch.setattr(market_tools, "get_store", lambda: _FakeStore())

    result = market_tools.resolve_stock_symbol("000700.SZ")

    assert result["symbol"] == "000700"
    assert result["name"] == "模塑科技"


def test_effective_end_date_uses_local_latest_data(monkeypatch):
    monkeypatch.setattr(market_tools, "get_store", lambda: _FakeStore())

    assert (
        market_tools._effective_end_date("000700", "2025-01-20", True)
        == "2026-06-12"
    )
    assert (
        market_tools._effective_end_date("000700", "2025-01-20", False)
        == "2025-01-20"
    )
