from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd

from server.agent.tools import market_tools
from server.agent.tools import analysis_tools
from server.models.backtest import KlineBar


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


def _fake_bars(count: int = 50) -> list[KlineBar]:
    start = date(2026, 1, 1)
    return [
        KlineBar(
            dt=str(start + timedelta(days=index)),
            open=10 + index * 0.1,
            high=10.5 + index * 0.1,
            low=9.8 + index * 0.1,
            close=10.2 + index * 0.1,
            volume=100000 + index * 1000,
        )
        for index in range(count)
    ]


def test_kline_tool_respects_lookback(monkeypatch):
    monkeypatch.setattr(market_tools, "_resolve_symbol", lambda symbol: ("000700", {"name": "demo"}))
    monkeypatch.setattr(market_tools, "_effective_end_date", lambda *args: "2026-02-19")
    monkeypatch.setattr(market_tools, "get_kline", lambda *args: _fake_bars(50))

    payload = json.loads(
        market_tools.get_kline_data_tool.invoke(
            {
                "symbol": "000700",
                "start_date": "2026-01-01",
                "end_date": "2026-02-19",
                "lookback": 20,
            }
        )
    )

    assert payload["bar_count"] == 20
    assert payload["total_bars"] == 50
    assert payload["lookback"] == 20
    assert len(payload["bars"]) == 20


def test_technical_tool_respects_lookback(monkeypatch):
    monkeypatch.setattr(analysis_tools, "_resolve_symbol", lambda symbol: ("000700", {"name": "demo"}))
    monkeypatch.setattr(analysis_tools, "_effective_end_date", lambda *args: "2026-02-19")
    monkeypatch.setattr(analysis_tools, "get_kline", lambda *args: _fake_bars(50))

    payload = json.loads(
        analysis_tools.analyze_technicals_tool.invoke(
            {
                "symbol": "000700",
                "start_date": "2026-01-01",
                "end_date": "2026-02-19",
                "lookback": 20,
            }
        )
    )

    assert payload["total_bars"] == 50
    assert payload["returned_bars"] == 20
    assert payload["lookback"] == 20
    assert len(payload["recent_indicators"]) == 20
