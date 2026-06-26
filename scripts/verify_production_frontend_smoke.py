from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and smoke-test the production frontend preview with the backend API proxy."
    )
    parser.add_argument("--backend-port", type=int, default=18001)
    parser.add_argument("--frontend-port", type=int, default=14173)
    parser.add_argument("--skip-build", action="store_true", help="Use the existing web/dist build.")
    parser.add_argument("--timeout", type=float, default=45.0)
    return parser.parse_args()


def run(command: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def request(url: str, timeout: float = 3.0) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "QuantLab production smoke"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content_type = response.headers.get("content-type", "")
        text = response.read().decode("utf-8", errors="replace")
        return response.status, content_type, text


def wait_url(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, _content_type, _text = request(url)
            if status == 200:
                return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def terminate(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


def start_process(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def assert_html_shell(html: str) -> str:
    if '<div id="root"></div>' not in html:
        raise AssertionError("production preview HTML is missing the React root element")
    match = re.search(r'<script type="module" crossorigin src="([^"]+)"></script>', html)
    if not match:
        raise AssertionError("production preview HTML is missing the module entry script")
    return match.group(1)


def assert_entry_script(script: str) -> None:
    if "createRoot" not in script:
        raise AssertionError("production entry bundle does not contain the React createRoot bootstrap")
    if "fetch(e.href" in script:
        raise AssertionError(
            "production entry bundle includes the modulepreload fetch polyfill that can blank the app "
            "in restricted browser runtimes"
        )
    if "modulepreload" in script and "fetch(" in script:
        raise AssertionError("production entry bundle still appears to fetch modulepreload links")


def main() -> int:
    args = parse_args()
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise SystemExit("npm was not found. Install Node.js LTS before running production frontend smoke.")

    if not args.skip_build:
        run([npm, "run", "build"], cwd=WEB_DIR)

    backend_url = f"http://127.0.0.1:{args.backend_port}"
    frontend_url = f"http://127.0.0.1:{args.frontend_port}"
    backend = start_process(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.backend_port),
        ],
        cwd=ROOT,
    )
    preview_env = os.environ.copy()
    preview_env["QUANT_BACKEND_PROXY_HOST"] = "127.0.0.1"
    preview_env["QUANT_BACKEND_PORT"] = str(args.backend_port)
    frontend = start_process(
        [npm, "run", "preview", "--", "--host", "127.0.0.1", "--port", str(args.frontend_port)],
        cwd=WEB_DIR,
        env=preview_env,
    )
    try:
        wait_url(f"{backend_url}/api/health", args.timeout)
        wait_url(frontend_url, args.timeout)
        status, content_type, html = request(frontend_url)
        if status != 200 or "text/html" not in content_type:
            raise AssertionError(f"unexpected production preview HTML response: {status} {content_type}")
        entry_path = assert_html_shell(html)

        status, content_type, proxied_health = request(f"{frontend_url}/api/health")
        if status != 200 or '"status":"ok"' not in proxied_health:
            raise AssertionError(f"production preview API proxy failed: {status} {proxied_health[:200]}")

        status, content_type, script = request(f"{frontend_url}{entry_path}")
        if status != 200 or "javascript" not in content_type:
            raise AssertionError(f"unexpected production entry response: {status} {content_type}")
        assert_entry_script(script)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "frontend": frontend_url,
                    "backend": backend_url,
                    "entry": entry_path,
                    "api_proxy": json.loads(proxied_health),
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        terminate(frontend)
        terminate(backend)


if __name__ == "__main__":
    raise SystemExit(main())
