from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"


def run_step(name: str, cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"\n[verify] {name}")
    print("[verify] " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify that a fresh QuantLab clone can import, test, and build."
    )
    parser.add_argument(
        "--skip-web",
        action="store_true",
        help="Skip the frontend build when Node dependencies are unavailable.",
    )
    args = parser.parse_args()

    run_step(
        "backend import check",
        [
            sys.executable,
            "-c",
            "import server.main; print(server.main.health())",
        ],
    )
    run_step("configuration contract", [sys.executable, "scripts/verify_config_contract.py"])
    run_step("deployment configuration", [sys.executable, "scripts/verify_deployment_config.py"])
    run_step("offline demo data seed", [sys.executable, "scripts/seed_demo_data.py"])
    run_step("market data integrity", [sys.executable, "scripts/verify_data_integrity.py", "--skip-demo-seed"])
    run_step("strategy library consistency", [sys.executable, "scripts/verify_strategy_consistency.py"])
    run_step("launch scripts", [sys.executable, "scripts/verify_launch_scripts.py"])
    run_step("Windows launch smoke", [sys.executable, "scripts/verify_windows_launch_smoke.py"])
    run_step("backend tests", [sys.executable, "-m", "pytest", "-q"])
    run_step("runtime API smoke", [sys.executable, "scripts/verify_runtime_smoke.py", "--skip-demo-seed"])
    run_step("fresh clone smoke", [sys.executable, "scripts/verify_fresh_clone_smoke.py"])

    if args.skip_web:
        print("\n[verify] frontend build skipped by request")
        return
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise SystemExit("npm was not found. Install Node.js LTS or rerun with --skip-web.")
    if not (WEB_DIR / "node_modules").exists():
        run_step("frontend dependencies", [npm, "ci"], cwd=WEB_DIR)
    run_step("frontend API contract", [sys.executable, "scripts/verify_frontend_api_contract.py"])
    run_step("frontend text smoke", [sys.executable, "scripts/verify_frontend_text_smoke.py"])
    run_step("frontend UI inventory", [sys.executable, "scripts/verify_frontend_ui_inventory.py"])
    run_step("frontend production build", [npm, "run", "build"], cwd=WEB_DIR)
    run_step(
        "frontend production preview smoke",
        [sys.executable, "scripts/verify_production_frontend_smoke.py", "--skip-build"],
    )

    print("\n[verify] QuantLab clone verification passed.")


if __name__ == "__main__":
    main()
