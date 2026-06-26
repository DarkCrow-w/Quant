from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.main import app


API_FILES = [
    ROOT / "web" / "src" / "api" / "client.ts",
    ROOT / "web" / "src" / "api" / "agent.ts",
]

AXIOS_BASE_RE = re.compile(r"const\s+api\s*=\s*axios\.create\(\{\s*baseURL:\s*['\"]([^'\"]+)['\"]", re.S)
API_CALL_RE = re.compile(r"api\.(get|post|put|delete|patch)\s*(?:<[^>]+>)?\s*\(\s*([`'\"])(.*?)\2", re.S)
WS_RE = re.compile(r"new\s+WebSocket\([^`'\"]*[`'\"](?:[^`'\"]*)?(/api/[^`'\"]+)")
TEMPLATE_EXPR_RE = re.compile(r"\$\{[^}]+\}")
PATH_PARAM_RE = re.compile(r"\{[^}/]+\}")


def normalize(path: str) -> str:
    path = TEMPLATE_EXPR_RE.sub("{}", path)
    path = PATH_PARAM_RE.sub("{}", path)
    path = re.sub(r"/+", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def collect_openapi_routes() -> set[tuple[str, str]]:
    openapi = app.openapi()
    routes: set[tuple[str, str]] = set()
    for path, methods in openapi.get("paths", {}).items():
        for method in methods:
            routes.add((method.upper(), normalize(path)))
    return routes


def collect_websocket_routes() -> set[str]:
    routes: set[str] = set()
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            pending.extend(getattr(original_router, "routes", []))
            continue
        path = getattr(route, "path", "")
        if "websocket" in route.__class__.__name__.lower():
            routes.add(normalize(path))
    return routes


def file_base_url(text: str, path: Path) -> str:
    match = AXIOS_BASE_RE.search(text)
    if not match:
        raise AssertionError(f"{path.relative_to(ROOT)} does not define axios baseURL")
    return match.group(1).rstrip("/")


def full_path(base_url: str, call_path: str) -> str:
    if call_path.startswith("/api/"):
        return normalize(call_path)
    return normalize(f"{base_url}/{call_path.lstrip('/')}")


def main() -> int:
    openapi_routes = collect_openapi_routes()
    websocket_routes = collect_websocket_routes()
    missing: list[str] = []
    calls: list[dict[str, str]] = []

    for path in API_FILES:
        text = path.read_text(encoding="utf-8")
        base_url = file_base_url(text, path)
        rel = str(path.relative_to(ROOT))

        for match in API_CALL_RE.finditer(text):
            method = match.group(1).upper()
            raw_path = match.group(3)
            endpoint = full_path(base_url, raw_path)
            calls.append({"file": rel, "method": method, "path": endpoint})
            if (method, endpoint) not in openapi_routes:
                missing.append(f"{rel}: {method} {endpoint}")

        for match in WS_RE.finditer(text):
            endpoint = normalize(match.group(1))
            calls.append({"file": rel, "method": "WEBSOCKET", "path": endpoint})
            if endpoint not in websocket_routes:
                missing.append(f"{rel}: WEBSOCKET {endpoint}")

    if missing:
        print("Frontend API contract failed:")
        for item in missing:
            print(f"- missing backend route for {item}")
        raise SystemExit(1)

    report = {
        "status": "ok",
        "files": [str(path.relative_to(ROOT)) for path in API_FILES],
        "calls": len(calls),
        "openapi_routes": len(openapi_routes),
        "websocket_routes": len(websocket_routes),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    main()
