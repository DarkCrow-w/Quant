from __future__ import annotations

import json
import shutil
import sys
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
from server.services.backtest_service import STRATEGY_REGISTRY
from server.services.factor_strategy_service import FactorStrategyStore


EXPECTED_BUILTIN_COMPOSITES = {
    "ma_cross": "builtin_ma_cross",
    "vol_kdj_bbi": "builtin_vol_kdj_bbi",
    "bbi_kdj_trend": "builtin_bbi_kdj_trend",
    "dip_buy": "builtin_dip_buy",
}


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def request(client: TestClient, method: str, path: str, **kwargs) -> Any:
    response = getattr(client, method)(path, **kwargs)
    ensure(response.status_code == 200, f"{method.upper()} {path} failed: {response.status_code} {response.text}")
    return response.json()


def verify_frontend_strategy_source() -> dict[str, bool]:
    source = (ROOT / "web" / "src" / "stores" / "backtest.ts").read_text(encoding="utf-8")
    checks = {
        "loads_basic_strategies": "fetchStrategyList()" in source,
        "loads_factor_strategies": "fetchFactorStrategies()" in source,
        "uses_composite_strategy_names": "composite:${strategy.id}" in source,
        "defaults_to_builtin_composite": "composite:builtin_ma_cross" in source,
    }
    missing = [name for name, ok in checks.items() if not ok]
    ensure(not missing, f"Backtest store is missing strategy library wiring: {missing}")
    return checks


def main() -> int:
    seed_report = seed_demo_data(ROOT / "data", min_cache=10, min_universe=5000)
    client = TestClient(app)
    store = FactorStrategyStore()

    basic_strategies = request(client, "get", "/api/strategy/list")
    basic_names = {item["name"] for item in basic_strategies}
    ensure(set(STRATEGY_REGISTRY) <= basic_names, "API strategy list does not expose every backend strategy")
    ensure(set(EXPECTED_BUILTIN_COMPOSITES) <= basic_names, "API strategy list is missing expected basic strategies")

    composer_strategies = request(client, "get", "/api/screening/composer/strategies")
    composer_ids = {item["id"] for item in composer_strategies}
    expected_composer_ids = set(EXPECTED_BUILTIN_COMPOSITES.values())
    ensure(expected_composer_ids <= composer_ids, "Composer strategy library is missing migrated builtin strategies")

    for basic_name, composite_id in EXPECTED_BUILTIN_COMPOSITES.items():
        saved = store.get(composite_id)
        ensure(saved is not None, f"Builtin composite strategy {composite_id} for {basic_name} cannot be loaded")
        ensure(saved.groups, f"Builtin composite strategy {composite_id} has no condition groups")
        ensure(
            any(group.conditions for group in saved.groups),
            f"Builtin composite strategy {composite_id} has no executable conditions",
        )

    composite_backtest = request(
        client,
        "post",
        "/api/backtest/run",
        params={"save": False},
        json={
            "symbols": ["600519"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "strategy": "composite:builtin_ma_cross",
            "strategy_params": {},
            "initial_cash": 1_000_000,
            "max_position_pct": 0.3,
            "max_drawdown": 0.2,
            "commission_rate": 0.00025,
        },
    )
    ensure(composite_backtest["equity_curve"], "Composite strategy backtest returned an empty equity curve")
    ensure(
        composite_backtest["metrics"]["final_equity"] > 0,
        "Composite strategy backtest returned invalid performance metrics",
    )

    frontend_checks = verify_frontend_strategy_source()
    report = {
        "config_seeded": CONFIG_SEEDED,
        "demo_seed": seed_report,
        "basic_strategies": sorted(basic_names),
        "builtin_composites": EXPECTED_BUILTIN_COMPOSITES,
        "composer_strategy_count": len(composer_strategies),
        "composite_backtest_points": len(composite_backtest["equity_curve"]),
        "frontend_checks": frontend_checks,
    }
    print(json.dumps({"status": "ok", "report": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise
