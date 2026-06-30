from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"


def run_step(name: str, cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"\n[agent-verify] {name}")
    print("[agent-verify] " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def runtime_is_available(base_url: str) -> tuple[bool, str]:
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/agent/runtime", timeout=8) as response:
            text = response.read().decode("utf-8")
    except URLError as exc:
        return False, f"agent backend not reachable: {exc}"
    except OSError as exc:
        return False, f"agent backend not reachable: {exc}"
    try:
        status = json.loads(text)
    except json.JSONDecodeError:
        return False, text
    return bool(status.get("enabled")), text


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify QuantLab Agent quality gates.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--origin", default="http://127.0.0.1:5174")
    parser.add_argument("--skip-web-build", action="store_true")
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument(
        "--require-runtime",
        action="store_true",
        help="Fail instead of skipping when Agent runtime or model config is unavailable.",
    )
    args = parser.parse_args()

    run_step("Agent frontend contract", [sys.executable, "scripts/verify_agent_frontend_contract.py"])
    run_step(
        "Agent Python compile",
        [
            sys.executable,
            "-m",
            "py_compile",
            "server/agent/router.py",
            "server/agent/prompts.py",
            "server/agent/quant_skill.py",
            "server/agent/schemas.py",
            "server/agent/tools/market_tools.py",
            "server/agent/tools/analysis_tools.py",
            "scripts/verify_agent_runtime_smoke.py",
        ],
    )
    run_step(
        "Agent backend tests",
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_agent_market_tools.py",
            "tests/test_agent_router_contract.py",
            "-q",
        ],
    )

    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not args.skip_web_build:
        if not npm:
            raise SystemExit("npm was not found. Install Node.js LTS or rerun with --skip-web-build.")
        run_step("Agent frontend lint", [npm, "run", "lint"], cwd=WEB_DIR)
        run_step("Agent frontend build", [npm, "run", "build"], cwd=WEB_DIR)

    if args.skip_runtime:
        print("\n[agent-verify] runtime smoke skipped by request")
        print("[agent-verify] Agent quality verification passed.")
        return

    enabled, reason = runtime_is_available(args.base_url)
    if not enabled:
        message = f"Agent runtime smoke skipped: {reason}"
        if args.require_runtime:
            raise SystemExit(message)
        print(f"\n[agent-verify] {message}")
        print("[agent-verify] Agent quality verification passed.")
        return

    run_step(
        "Agent runtime smoke",
        [
            sys.executable,
            "scripts/verify_agent_runtime_smoke.py",
            "--base-url",
            args.base_url,
            "--origin",
            args.origin,
        ],
    )
    print("\n[agent-verify] Agent quality verification passed.")


if __name__ == "__main__":
    main()
