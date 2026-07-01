from __future__ import annotations

import math

import pandas as pd

from server.services import market_service


def test_get_universe_keeps_bj_and_sanitizes_nan(monkeypatch):
    class FakeStore:
        def get_universe(self, market=None):
            return pd.DataFrame(
                [
                    {
                        "symbol": "301571",
                        "name": "国科天成",
                        "market": "SZ",
                        "industry": float("nan"),
                    },
                    {
                        "symbol": "920001",
                        "name": "北交所股票",
                        "market": "BJ",
                        "industry": math.inf,
                    },
                    {
                        "symbol": "510300",
                        "name": "ETF",
                        "market": "SH",
                        "industry": "基金",
                    },
                ]
            )

    monkeypatch.setattr(market_service, "get_store", lambda: FakeStore())

    rows = market_service.get_universe()

    assert [row["symbol"] for row in rows] == ["301571", "920001"]
    assert rows[0]["industry"] is None
    assert rows[1]["industry"] is None
