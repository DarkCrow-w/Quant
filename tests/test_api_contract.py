from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from server.models.backtest import BacktestGridItem, BacktestGridResult, BacktestRequest, PerformanceMetrics
from server.main import app


def test_health_contract_exposes_service_metadata():
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "QuantLab API",
        "version": "0.1.0",
    }


def test_agent_status_contract_is_available_without_model_key():
    client = TestClient(app)

    response = client.get("/api/agent/status")

    assert response.status_code == 200
    body = response.json()
    assert "enabled" in body
    assert "configured" in body
    assert "provider" in body
    assert "model" in body


def test_system_status_contract_exposes_readiness_checks():
    client = TestClient(app)

    response = client.get("/api/system/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "warning", "error"}
    assert 0 <= body["score"] <= 100
    assert body["summary"]["required_total"] > 0
    checks = {check["key"]: check for check in body["checks"]}
    for key in (
        "api",
        "config",
        "data_cache",
        "universe",
        "strategies",
        "indicators",
        "research_assets",
        "agent",
        "live_trading",
        "deployment",
        "risk",
    ):
        assert key in checks
        assert checks[key]["level"] in {"ok", "warning", "error"}
        assert isinstance(checks[key]["message"], str)


def test_trading_status_contract_is_read_only_and_manual_start():
    client = TestClient(app)

    response = client.get("/api/trading/status")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "live"
    assert body["safety_mode"] == "manual_start"
    assert "run_live.py" in body["entrypoint"]
    assert body["broker"]["type"] == "futu"
    assert body["simulation"]["available"] is True
    assert len(body["manual_confirmations"]) >= 3
    checks = {check["key"]: check for check in body["checks"]}
    for key in ("config", "strategy", "symbols", "risk", "broker"):
        assert key in checks
        assert checks[key]["level"] in {"ok", "warning", "error"}


def test_market_data_contract_exposes_cache_universe_and_series(monkeypatch):
    from server.routers import market as market_router
    from quant.data import updater as data_updater

    monkeypatch.setattr(
        market_router,
        "get_kline",
        lambda symbol, start_date, end_date, freq="day": [
            {
                "dt": "2024-01-02",
                "open": 10,
                "high": 11,
                "low": 9.8,
                "close": 10.5,
                "volume": 100000,
            }
        ],
    )
    monkeypatch.setattr(
        market_router,
        "get_indicator",
        lambda symbol, name, start_date, end_date, freq="day": [
            {"dt": "2024-01-02", "MA5": 10.2}
        ],
    )
    monkeypatch.setattr(
        market_router,
        "get_universe",
        lambda market=None: [
            {"symbol": "600000", "name": "浦发银行", "market": "SH"}
        ],
    )
    monkeypatch.setattr(
        market_router,
        "get_calendar",
        lambda start=None, end=None: [
            {"dt": "2024-01-02", "is_open": True}
        ],
    )
    monkeypatch.setattr(
        market_router,
        "get_cache_status",
        lambda: [
            {"symbol": "600000", "freq": "day", "last_dt": "2024-01-02", "source": "tdx"}
        ],
    )
    monkeypatch.setattr(
        data_updater,
        "list_cached_symbols",
        lambda: [
            {"symbol": "600000", "bars": 1, "start": "2024-01-02", "end": "2024-01-02"}
        ],
    )

    client = TestClient(app)

    kline = client.get("/api/market/kline", params={"symbol": "600000"})
    assert kline.status_code == 200
    assert kline.json()[0]["close"] == 10.5

    indicator = client.get("/api/market/indicator/MA", params={"symbol": "600000"})
    assert indicator.status_code == 200
    assert indicator.json()[0]["MA5"] == 10.2

    unknown_indicator = client.get("/api/market/indicator/not_real", params={"symbol": "600000"})
    assert unknown_indicator.status_code == 404

    indicators = client.get("/api/market/indicators")
    assert indicators.status_code == 200
    assert any("name" in item and "columns" in item for item in indicators.json())

    universe = client.get("/api/market/universe")
    assert universe.status_code == 200
    assert universe.json()[0]["symbol"] == "600000"

    calendar = client.get("/api/market/calendar")
    assert calendar.status_code == 200
    assert calendar.json()[0]["is_open"] is True

    cache = client.get("/api/market/cache")
    assert cache.status_code == 200
    assert cache.json()[0]["bars"] == 1

    cache_status = client.get("/api/market/cache/status")
    assert cache_status.status_code == 200
    assert cache_status.json()[0]["freq"] == "day"


def test_strategy_list_and_composer_strategy_contract():
    client = TestClient(app)

    strategies = client.get("/api/strategy/list")
    assert strategies.status_code == 200
    names = {item["name"] for item in strategies.json()}
    assert {"ma_cross", "vol_kdj_bbi", "bbi_kdj_trend", "dip_buy"} <= names
    assert all(item["params_schema"] for item in strategies.json())

    metrics = client.get("/api/screening/composer/metrics")
    assert metrics.status_code == 200
    metric_keys = {item["key"] for item in metrics.json()}
    assert {"ma5", "ma20", "kdj_j", "volume_ratio_1"} <= metric_keys

    payload = {
        "name": f"pytest composer {uuid.uuid4().hex[:8]}",
        "description": "created by api contract test",
        "logic": "all",
        "min_score": 1,
        "top_n": 20,
        "lookback": 250,
        "groups": [
            {
                "id": "group-1",
                "name": "pytest group",
                "logic": "all",
                "conditions": [
                    {
                        "id": "condition-1",
                        "metric": "ma5",
                        "operator": "above_metric",
                        "value": None,
                        "compare_metric": "ma20",
                        "weight": 1,
                        "required": True,
                        "enabled": True,
                    }
                ],
            }
        ],
    }

    created = client.post("/api/screening/composer/strategies", json=payload)
    assert created.status_code == 200
    strategy = created.json()
    try:
        assert strategy["name"] == payload["name"]
        assert strategy["groups"][0]["conditions"][0]["compare_metric"] == "ma20"

        updated = client.put(
            f"/api/screening/composer/strategies/{strategy['id']}",
            json={**payload, "min_score": 10},
        )
        assert updated.status_code == 200
        assert updated.json()["min_score"] == 10

        listing = client.get("/api/screening/composer/strategies")
        assert listing.status_code == 200
        assert any(item["id"] == strategy["id"] for item in listing.json())
    finally:
        deleted = client.delete(f"/api/screening/composer/strategies/{strategy['id']}")
        assert deleted.status_code == 200
        assert deleted.json() == {"status": "deleted"}


def test_backtest_grid_contract_returns_ranked_experiment_items(monkeypatch):
    from server.routers import backtest as backtest_router

    def fake_grid(req, save_result=None):
        base = BacktestRequest(strategy_params={"fast_period": 5, "slow_period": 20})
        return BacktestGridResult(
            requested=1,
            completed=1,
            failed=0,
            sort_by=req.sort_by,
            sort_order=req.sort_order,
            best=BacktestGridItem(
                status="completed",
                strategy_params=base.strategy_params,
                request=base,
                run_id="run-1",
                metrics=PerformanceMetrics(
                    initial_cash=1000000,
                    final_equity=1010000,
                    total_return=0.01,
                    annual_return=0.04,
                    max_drawdown=-0.02,
                    trade_count=2,
                    total_commission=10,
                ),
            ),
            results=[],
        )

    monkeypatch.setattr(backtest_router, "run_backtest_grid", fake_grid)
    client = TestClient(app)

    response = client.post(
        "/api/backtest/grid",
        json={
            "base": {"strategy": "ma_cross", "strategy_params": {}},
            "parameters": {"fast_period": [5], "slow_period": [20]},
            "max_runs": 4,
            "sort_by": "total_return",
            "sort_order": "desc",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requested"] == 1
    assert body["completed"] == 1
    assert body["failed"] == 0
    assert body["best"]["run_id"] == "run-1"


def test_research_report_contract_returns_markdown(monkeypatch):
    from server.routers import research as research_router

    class FakeStore:
        def build_backtest_report(self, run_ids):
            assert run_ids == ["run-1"]
            return "# QuantLab Research Report\n\n- Experiments: `1`\n"

    monkeypatch.setattr(research_router, "get_research_store", lambda: FakeStore())
    client = TestClient(app)

    response = client.post("/api/research/reports/backtests.md", json={"run_ids": ["run-1"]})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]
    assert "QuantLab Research Report" in response.text


def test_strategy_asset_crud_contract():
    client = TestClient(app)
    payload = {
        "name": "pytest strategy asset",
        "description": "created by api contract test",
        "base_strategy": "ma_cross",
        "params": {"fast_period": 5, "slow_period": 20},
        "tags": ["pytest"],
        "enabled": True,
    }

    created = client.post("/api/strategy/assets", json=payload)
    assert created.status_code == 200
    asset = created.json()
    assert asset["name"] == payload["name"]
    assert asset["base_strategy"] == "ma_cross"

    updated = client.put(
        f"/api/strategy/assets/{asset['id']}",
        json={**payload, "enabled": False, "params": {"fast_period": 8, "slow_period": 30}},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    listing = client.get("/api/strategy/assets")
    assert listing.status_code == 200
    assert any(item["id"] == asset["id"] for item in listing.json())

    deleted = client.delete(f"/api/strategy/assets/{asset['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted"}


def test_factor_management_and_mining_contract(monkeypatch):
    from server.routers import factors as factor_router
    from server.models.factor import FactorMiningItem, FactorMiningResult

    client = TestClient(app)
    factor_key = f"pytest_factor_{uuid.uuid4().hex[:8]}"
    payload = {
        "key": factor_key,
        "label": "Pytest Factor",
        "category": "custom",
        "description": "created by api contract test",
        "expression": "close / close.shift(20) - 1",
        "default_weight": 1.0,
        "enabled": True,
    }

    created = client.post("/api/factors", json=payload)
    assert created.status_code == 200
    factor = created.json()
    try:
        assert factor["key"] == factor_key

        updated = client.put(f"/api/factors/{factor['id']}", json={**payload, "default_weight": 2.0})
        assert updated.status_code == 200
        assert updated.json()["default_weight"] == 2.0

        def fake_mine(req):
            return FactorMiningResult(
                lookback=req.lookback,
                forward_days=req.forward_days,
                symbols=3,
                items=[
                    FactorMiningItem(
                        key="momentum_20",
                        label="20日动量",
                        category="momentum",
                        ic=0.1234,
                        abs_ic=0.1234,
                        samples=42,
                        coverage=1.0,
                        direction="positive",
                    )
                ],
            )

        monkeypatch.setattr(factor_router, "mine_factors", fake_mine)
        mined = client.post("/api/factors/mine", json={"lookback": 120, "forward_days": 5, "min_samples": 10})
        assert mined.status_code == 200
        assert mined.json()["items"][0]["key"] == "momentum_20"
    finally:
        deleted = client.delete(f"/api/factors/{factor['id']}")
        assert deleted.status_code == 200


def test_risk_rule_crud_and_evaluation_contract():
    client = TestClient(app)
    payload = {
        "name": "pytest risk rule",
        "description": "created by api contract test",
        "max_position_pct": 0.3,
        "max_drawdown": 0.2,
        "max_single_order_pct": 0.1,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.25,
        "max_symbols": 10,
        "enabled": True,
    }

    created = client.post("/api/risk/rules", json=payload)
    assert created.status_code == 200
    rule = created.json()
    try:
        assert rule["name"] == payload["name"]

        updated = client.put(f"/api/risk/rules/{rule['id']}", json={**payload, "max_symbols": 5})
        assert updated.status_code == 200
        assert updated.json()["max_symbols"] == 5

        evaluated = client.post(
            "/api/risk/evaluate",
            json={
                "rule_id": rule["id"],
                "equity": 1000000,
                "position_value": 400000,
                "order_value": 50000,
                "drawdown": 0.1,
                "symbol_count": 3,
            },
        )
        assert evaluated.status_code == 200
        body = evaluated.json()
        assert body["rule"]["name"] == payload["name"]
        assert any(check["key"] == "position" for check in body["checks"])
    finally:
        deleted = client.delete(f"/api/risk/rules/{rule['id']}")
        assert deleted.status_code == 200
