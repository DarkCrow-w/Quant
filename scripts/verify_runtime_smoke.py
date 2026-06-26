from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def ensure_local_config() -> bool:
    config = ROOT / "config" / "quant.env"
    example = ROOT / "config" / "quant.env.example"
    if config.exists() or not example.exists():
        return False
    shutil.copyfile(example, config)
    return True


CONFIG_SEEDED = ensure_local_config()

from scripts.seed_demo_data import seed_demo_data
from server.main import app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run QuantLab runtime smoke checks against local data and API contracts."
    )
    parser.add_argument("--min-universe", type=int, default=5000, help="Minimum expected stock universe size.")
    parser.add_argument("--min-cache", type=int, default=10, help="Minimum expected cached symbol count.")
    parser.add_argument("--symbol", default="600519", help="Preferred smoke-test symbol.")
    parser.add_argument("--skip-demo-seed", action="store_true", help="Do not create offline demo data before checks.")
    return parser.parse_args()


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def request(client: TestClient, method: str, path: str, **kwargs) -> Any:
    response = getattr(client, method)(path, **kwargs)
    ensure(response.status_code == 200, f"{method.upper()} {path} failed: {response.status_code} {response.text}")
    return response.json()


def response_text(client: TestClient, method: str, path: str, **kwargs) -> str:
    response = getattr(client, method)(path, **kwargs)
    ensure(response.status_code == 200, f"{method.upper()} {path} failed: {response.status_code} {response.text}")
    return response.text


def assert_clean_text(value: Any, path: str = "root") -> None:
    if isinstance(value, str):
        ensure("\ufffd" not in value, f"{path} contains replacement character")
        ensure("????" not in value, f"{path} contains question-mark mojibake")
        ensure(not any("\ue000" <= char <= "\uf8ff" for char in value), f"{path} contains private-use mojibake")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_clean_text(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            assert_clean_text(key, f"{path}.key")
            assert_clean_text(item, f"{path}.{key}")


def choose_symbol(preferred: str, cache: list[Any]) -> str:
    symbols = [item["symbol"] if isinstance(item, dict) else str(item) for item in cache]
    if preferred in symbols:
        return preferred
    ensure(bool(symbols), "No cached symbols available")
    return symbols[0]


def system_check_detail(system: dict[str, Any], key: str) -> dict[str, Any]:
    for check in system.get("checks", []):
        if check.get("key") == key:
            detail = check.get("detail") or {}
            ensure(isinstance(detail, dict), f"System check {key} detail is not an object")
            return detail
    raise AssertionError(f"System check {key} is missing")


def verify_strategy_asset_crud(client: TestClient) -> bool:
    payload = {
        "name": f"runtime smoke strategy {uuid.uuid4().hex[:8]}",
        "description": "created by runtime smoke verification",
        "base_strategy": "ma_cross",
        "params": {"fast_period": 5, "slow_period": 20},
        "tags": ["runtime-smoke"],
        "enabled": True,
    }
    created = request(client, "post", "/api/strategy/assets", json=payload)
    asset_id = created["id"]
    try:
        ensure(created["base_strategy"] == "ma_cross", "Strategy asset base strategy mismatch")
        fetched = request(client, "get", f"/api/strategy/assets/{asset_id}")
        ensure(fetched["id"] == asset_id, "Strategy asset fetch mismatch")
        updated = request(
            client,
            "put",
            f"/api/strategy/assets/{asset_id}",
            json={**payload, "enabled": False, "params": {"fast_period": 8, "slow_period": 30}},
        )
        ensure(updated["enabled"] is False, "Strategy asset update did not persist")
        listing = request(client, "get", "/api/strategy/assets")
        ensure(any(item["id"] == asset_id for item in listing), "Strategy asset not found in listing")
        assert_clean_text(updated, "strategy_asset")
        return True
    finally:
        request(client, "delete", f"/api/strategy/assets/{asset_id}")


def verify_factor_crud(client: TestClient) -> bool:
    key = f"runtime_factor_{uuid.uuid4().hex[:8]}"
    payload = {
        "key": key,
        "label": "Runtime Smoke Factor",
        "category": "custom",
        "description": "created by runtime smoke verification",
        "expression": "close / close.shift(20) - 1",
        "default_weight": 1.0,
        "enabled": True,
    }
    created = request(client, "post", "/api/factors", json=payload)
    factor_id = created["id"]
    try:
        ensure(created["key"] == key, "Factor key mismatch")
        updated = request(client, "put", f"/api/factors/{factor_id}", json={**payload, "default_weight": 2.0})
        ensure(updated["default_weight"] == 2.0, "Factor update did not persist")
        listing = request(client, "get", "/api/factors")
        ensure(any(item["id"] == factor_id for item in listing), "Factor not found in listing")
        assert_clean_text(updated, "factor_crud")
        return True
    finally:
        request(client, "delete", f"/api/factors/{factor_id}")


def verify_risk_rule_crud(client: TestClient) -> bool:
    payload = {
        "name": f"runtime smoke risk {uuid.uuid4().hex[:8]}",
        "description": "created by runtime smoke verification",
        "max_position_pct": 0.3,
        "max_drawdown": 0.2,
        "max_single_order_pct": 0.1,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.25,
        "max_symbols": 10,
        "enabled": True,
    }
    created = request(client, "post", "/api/risk/rules", json=payload)
    rule_id = created["id"]
    try:
        ensure(created["name"] == payload["name"], "Risk rule name mismatch")
        updated = request(client, "put", f"/api/risk/rules/{rule_id}", json={**payload, "max_symbols": 5})
        ensure(updated["max_symbols"] == 5, "Risk rule update did not persist")
        listing = request(client, "get", "/api/risk/rules")
        ensure(any(item["id"] == rule_id for item in listing), "Risk rule not found in listing")
        evaluated = request(
            client,
            "post",
            "/api/risk/evaluate",
            json={
                "rule_id": rule_id,
                "equity": 1_000_000,
                "position_value": 100_000,
                "order_value": 50_000,
                "drawdown": 0.05,
                "symbol_count": 3,
            },
        )
        ensure(any(check["key"] == "position" for check in evaluated["checks"]), "Risk checks missing position")
        assert_clean_text(evaluated, "risk_rule_crud")
        return True
    finally:
        request(client, "delete", f"/api/risk/rules/{rule_id}")


def verify_composer_strategy_crud(client: TestClient) -> bool:
    payload = {
        "name": f"runtime smoke composer {uuid.uuid4().hex[:8]}",
        "description": "created by runtime smoke verification",
        "logic": "all",
        "groups": [
            {
                "id": "group-1",
                "name": "条件组",
                "logic": "all",
                "conditions": [
                    {
                        "id": "cond-1",
                        "metric": "close",
                        "operator": "gte",
                        "value": 1,
                        "weight": 1,
                        "required": True,
                        "enabled": True,
                    }
                ],
            }
        ],
        "min_score": 0,
        "top_n": 20,
        "lookback": 120,
    }
    created = request(client, "post", "/api/screening/composer/strategies", json=payload)
    strategy_id = created["id"]
    try:
        ensure(created["name"] == payload["name"], "Composer strategy name mismatch")
        updated = request(
            client,
            "put",
            f"/api/screening/composer/strategies/{strategy_id}",
            json={**payload, "description": "updated by runtime smoke verification", "top_n": 10},
        )
        ensure(updated["top_n"] == 10, "Composer strategy update did not persist")
        listing = request(client, "get", "/api/screening/composer/strategies")
        ensure(any(item["id"] == strategy_id for item in listing), "Composer strategy not found in listing")
        assert_clean_text(updated, "composer_strategy_crud")
        return True
    finally:
        request(client, "delete", f"/api/screening/composer/strategies/{strategy_id}")


def ensure_research_backtest(client: TestClient, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    research = request(client, "get", "/api/research/summary")
    if research.get("total_backtests", 0) > 0:
        return research, False

    seeded = request(
        client,
        "post",
        "/api/backtest/run",
        params={"save": True},
        json=payload,
    )
    ensure(seeded["equity_curve"], "Research seed backtest returned an empty equity curve")

    research = request(client, "get", "/api/research/summary")
    ensure(research.get("total_backtests", 0) > 0, "Research summary has no backtests after seed run")
    return research, True


def main() -> int:
    args = parse_args()
    seed_report = None
    if not args.skip_demo_seed:
        seed_report = seed_demo_data(ROOT / "data", min_cache=args.min_cache, min_universe=args.min_universe)
    client = TestClient(app)
    report: dict[str, Any] = {}
    report["config_seeded"] = CONFIG_SEEDED
    if seed_report is not None:
        report["demo_seed"] = seed_report

    health = request(client, "get", "/api/health")
    ensure(health.get("status") == "ok", "API health is not ok")
    report["health"] = health

    openapi = request(client, "get", "/openapi.json")
    assert_clean_text(openapi, "openapi")
    report["openapi_paths"] = len(openapi.get("paths", {}))

    system = request(client, "get", "/api/system/status")
    ensure(system.get("status") == "ok", "System status is not ok")
    report["system_score"] = system.get("score")

    agent = request(client, "get", "/api/agent/status")
    ensure("enabled" in agent and "configured" in agent, "Agent status response is missing expected fields")
    assert_clean_text(agent, "agent_status")
    report["agent_configured"] = bool(agent.get("configured"))

    universe = request(client, "get", "/api/market/universe")
    ensure(len(universe) >= args.min_universe, f"Universe too small: {len(universe)} < {args.min_universe}")
    universe_symbols = {item.get("symbol") for item in universe if isinstance(item, dict)}
    report["universe"] = len(universe)

    cache = request(client, "get", "/api/market/cache")
    ensure(len(cache) >= args.min_cache, f"Cache too small: {len(cache)} < {args.min_cache}")
    symbol = choose_symbol(args.symbol, cache)
    cached_symbols = {item["symbol"] if isinstance(item, dict) else str(item) for item in cache}
    ensure(
        cached_symbols.issubset(universe_symbols),
        f"Cached symbols missing from universe: {sorted(cached_symbols - universe_symbols)[:10]}",
    )
    report["cache"] = len(cache)
    report["symbol"] = symbol

    cache_status = request(client, "get", "/api/market/cache/status")
    ensure(len(cache_status) == len(cache), f"Cache status count mismatch: {len(cache_status)} != {len(cache)}")
    status_symbols = {item.get("symbol") for item in cache_status if isinstance(item, dict)}
    ensure(cached_symbols == status_symbols, "Cache status symbols do not match cache symbols")
    report["cache_status"] = len(cache_status)

    data_cache = system_check_detail(system, "data_cache")
    universe_summary = system_check_detail(system, "universe")
    ensure(
        int(data_cache.get("cached_symbols", -1)) == len(cache),
        "System cached_symbols does not match /api/market/cache",
    )
    ensure(
        int(universe_summary.get("symbols", -1)) == len(universe),
        "System universe symbol count does not match /api/market/universe",
    )

    data_job = request(client, "get", "/api/market/jobs/current")
    ensure("status" in data_job and "running" in data_job, "Data job status response is missing expected fields")
    assert_clean_text(data_job, "data_job")
    report["data_job_status"] = data_job["status"]

    kline = request(
        client,
        "get",
        "/api/market/kline",
        params={"symbol": symbol, "start_date": "2024-01-01", "end_date": "2024-12-31"},
    )
    ensure(len(kline) >= 30, f"Kline data for {symbol} is too short: {len(kline)}")
    report["kline_bars"] = len(kline)

    indicators = request(client, "get", "/api/market/indicators")
    ensure(len(indicators) >= 10, f"Expected at least 10 indicators, got {len(indicators)}")
    ensure(any(item["name"] == "MA" for item in indicators), "MA indicator is missing")
    assert_clean_text(indicators, "indicators")
    indicator_rows = request(
        client,
        "get",
        "/api/market/indicator/MA",
        params={"symbol": symbol, "start_date": "2024-01-01", "end_date": "2024-12-31"},
    )
    ensure(len(indicator_rows) >= 30, f"MA indicator data for {symbol} is too short: {len(indicator_rows)}")
    report["indicators"] = len(indicators)
    report["ma_indicator_rows"] = len(indicator_rows)

    strategies = request(client, "get", "/api/strategy/list")
    ensure(len(strategies) >= 4, f"Expected at least 4 strategies, got {len(strategies)}")
    assert_clean_text(strategies, "strategies")
    report["strategies"] = [item["name"] for item in strategies]
    report["strategy_asset_crud"] = verify_strategy_asset_crud(client)

    backtest_payload = {
        "symbols": [symbol],
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "strategy": "ma_cross",
        "strategy_params": {"fast_period": 5, "slow_period": 20},
        "initial_cash": 1_000_000,
        "max_position_pct": 0.3,
        "max_drawdown": 0.2,
        "commission_rate": 0.00025,
    }
    backtest = request(
        client,
        "post",
        "/api/backtest/run",
        params={"save": False},
        json=backtest_payload,
    )
    ensure(backtest["equity_curve"], "Backtest returned an empty equity curve")
    ensure("metrics" in backtest and backtest["metrics"]["final_equity"] > 0, "Backtest metrics are invalid")
    report["backtest_trades"] = len(backtest.get("trades", []))

    grid = request(
        client,
        "post",
        "/api/backtest/grid",
        params={"save": False},
        json={
            "base": {
                "symbols": [symbol],
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "strategy": "ma_cross",
                "strategy_params": {},
                "initial_cash": 1_000_000,
            },
            "parameters": {"fast_period": [5], "slow_period": [20]},
            "max_runs": 2,
            "sort_by": "total_return",
            "sort_order": "desc",
        },
    )
    ensure(grid["requested"] == 1 and grid["completed"] == 1, "Backtest grid smoke failed")
    ensure(grid.get("best"), "Backtest grid did not return a best result")
    assert_clean_text(grid, "backtest_grid")
    report["backtest_grid_completed"] = grid["completed"]

    factors = request(client, "get", "/api/factors")
    ensure(len(factors) >= 5, f"Expected at least 5 managed factors, got {len(factors)}")
    assert_clean_text(factors, "factors")
    report["factors"] = len(factors)
    report["factor_crud"] = verify_factor_crud(client)

    mining = request(
        client,
        "post",
        "/api/factors/mine",
        json={"symbols": [symbol], "lookback": 120, "forward_days": 5, "min_samples": 5},
    )
    ensure(len(mining.get("items", [])) >= 3, "Factor mining returned too few candidates")
    assert_clean_text(mining, "factor_mining")
    report["factor_mining_items"] = len(mining["items"])

    score = request(
        client,
        "post",
        "/api/screening/score",
        json={"lookback": 120, "top_n": 3, "max_symbols": 3},
    )
    ensure(score.get("total_scanned", 0) > 0, "Scoring did not scan any symbols")
    report["score_scanned"] = score["total_scanned"]
    report["score_returned"] = score["returned"]

    classic_scan = request(
        client,
        "post",
        "/api/screening/scan",
        json={
            "strategy": "ma_cross",
            "strategy_params": {"fast_period": 5, "slow_period": 20},
            "lookback": 120,
        },
    )
    ensure(classic_scan.get("total_scanned", 0) > 0, "Classic screening did not scan any symbols")
    assert_clean_text(classic_scan, "classic_screening")
    report["classic_scan_scanned"] = classic_scan["total_scanned"]
    report["classic_scan_matches"] = len(classic_scan.get("matches", []))

    metrics = request(client, "get", "/api/screening/composer/metrics")
    ensure(len(metrics) >= 10, f"Expected composer metrics, got {len(metrics)}")
    assert_clean_text(metrics, "composer_metrics")
    report["composer_metrics"] = len(metrics)
    report["composer_strategy_crud"] = verify_composer_strategy_crud(client)

    risk = request(
        client,
        "post",
        "/api/risk/evaluate",
        json={
            "draft": {
                "name": "runtime smoke risk",
                "description": "runtime smoke",
                "max_position_pct": 0.3,
                "max_drawdown": 0.2,
                "max_single_order_pct": 0.1,
                "stop_loss_pct": 0.08,
                "take_profit_pct": 0.25,
                "max_symbols": 10,
                "enabled": True,
            },
            "equity": 1_000_000,
            "position_value": 100_000,
            "order_value": 50_000,
            "drawdown": 0.05,
            "symbol_count": 3,
        },
    )
    ensure(len(risk.get("checks", [])) >= 4, "Risk evaluation returned too few checks")
    assert_clean_text(risk, "risk")
    report["risk_passed"] = risk["passed"]
    report["risk_rule_crud"] = verify_risk_rule_crud(client)

    research, seeded_research = ensure_research_backtest(client, backtest_payload)
    report["research_total_backtests"] = research["total_backtests"]
    report["research_seeded"] = seeded_research

    runs = request(client, "get", "/api/research/backtests", params={"limit": 5})
    ensure(runs, "Research backtest listing is empty")
    run_id = runs[0]["id"]
    detail = request(client, "get", f"/api/research/backtests/{run_id}")
    ensure(detail["id"] == run_id and "result" in detail, "Research detail response is invalid")
    assert_clean_text(detail, "research_detail")
    report_md = response_text(client, "post", "/api/research/reports/backtests.md", json={"run_ids": [run_id]})
    ensure("QuantLab Research Report" in report_md, "Research markdown report missing title")
    assert_clean_text(report_md, "research_report")
    report["research_report"] = True

    trading = request(client, "get", "/api/trading/status")
    ensure(trading.get("safety_mode") == "manual_start", "Trading status safety mode changed")
    ensure(trading.get("entrypoint"), "Trading status missing manual entrypoint")
    assert_clean_text(trading, "trading_status")
    report["trading_ready"] = bool(trading.get("ready"))

    print(json.dumps({"status": "ok", "report": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise
