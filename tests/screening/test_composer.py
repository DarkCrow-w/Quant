from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import date

from quant.screening.composer import Condition, MetricContext, evaluate_condition
from server.services import factor_strategy_service
from server.models.screening import (
    CompositeCondition,
    CompositeGroup,
    FactorStrategyDraft,
)
from server.services.factor_strategy_service import FactorStrategyStore, get_metric_defs


def _frame() -> pd.DataFrame:
    close = np.array([10.0, 9.0, 8.0, 9.0, 10.0, 12.0])
    return pd.DataFrame(
        {
            "dt": pd.date_range("2026-01-01", periods=len(close)),
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": [100, 110, 90, 120, 160, 240],
            "amount": close * 100,
        }
    )


def test_metric_registry_contains_project_indicators():
    keys = {metric.key for metric in get_metric_defs()}
    assert {"kdj_k", "rsi6", "volume_ratio_5", "bbi", "turnover_rate"} <= keys
    assert {
        "macd_dif_custom",
        "dmi_adx_custom",
        "dma_custom",
        "bbi_custom",
        "cci_custom",
    } <= keys


def test_common_custom_indicators_accept_parameters():
    context = MetricContext(_frame())
    cases = [
        ("kdj_j_custom", {"period": 3, "k_smooth": 2, "d_smooth": 2}),
        ("macd_bar_custom", {"fast": 2, "slow": 4, "signal": 2}),
        ("cci_custom", {"period": 3}),
        ("dmi_adx_custom", {"period": 3, "adx_period": 2}),
        ("dma_custom", {"short": 2, "long": 4, "ama": 2}),
        ("bbi_custom", {"p1": 2, "p2": 3, "p3": 4, "p4": 5}),
        ("boll_up_custom", {"period": 3, "deviation": 2}),
        ("atr_custom", {"period": 3}),
    ]
    for metric, params in cases:
        series = context.series(metric, params)
        assert len(series) == len(_frame())
        assert not series.dropna().empty, metric


def test_condition_can_compare_two_metrics():
    context = MetricContext(_frame())
    result = evaluate_condition(
        context,
        Condition(
            metric="ma_custom",
            operator="above_metric",
            compare_metric="ema_custom",
            params={"period": 2},
        ),
    )
    assert result.available
    assert isinstance(result.passed, bool)


def test_condition_supports_cross_and_rising():
    context = MetricContext(_frame())
    cross = evaluate_condition(
        context,
        Condition(
            metric="close",
            operator="cross_above",
            compare_metric="ma_custom",
            params={"period": 3},
        ),
    )
    rising = evaluate_condition(
        context,
        Condition(metric="close", operator="rising", periods=3),
    )
    assert cross.available
    assert rising.available
    assert rising.passed


def test_strategy_store_round_trip(tmp_path):
    store = FactorStrategyStore(tmp_path / "screening.sqlite3")
    draft = FactorStrategyDraft(
        name="RSI 动量",
        description="RSI 与量比组合",
        groups=[
            CompositeGroup(
                id="group-1",
                conditions=[
                    CompositeCondition(
                        id="condition-1",
                        metric="rsi6",
                        operator="gte",
                        value=50,
                    )
                ],
            )
        ],
    )

    saved = store.save(draft)
    assert store.get(saved.id) == saved
    assert store.list() == [saved]
    assert store.delete(saved.id)
    assert store.list() == []


def test_daily_basic_uses_previous_trading_day(monkeypatch, tmp_path):
    calls: list[str] = []

    class FakeSource:
        def fetch_daily_basic(self, trade_date: str) -> pd.DataFrame:
            calls.append(trade_date)
            if trade_date != "20260612":
                return pd.DataFrame()
            return pd.DataFrame(
                [{"symbol": "000001", "trade_date": trade_date, "turnover_rate": 2.5}]
            )

    monkeypatch.setattr(factor_strategy_service, "_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(factor_strategy_service, "TushareSource", FakeSource)

    snapshot = factor_strategy_service._load_daily_basic(date(2026, 6, 13))

    assert calls == ["20260613", "20260612"]
    assert snapshot["000001"]["turnover_rate"] == 2.5
