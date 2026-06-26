from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_TOP_LEVEL = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".uv-cache",
    ".uv-python",
    ".venv",
    "data",
}
EXCLUDED_PREFIXES = (".venv.",)
EXCLUDED_PATHS = {
    ("web", "dist"),
    ("web", "node_modules"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy the commit-ready worktree to a clean temporary directory and run "
            "the runtime smoke test without local data or config."
        )
    )
    parser.add_argument("--keep", action="store_true", help="Keep the temporary clone directory after the check.")
    parser.add_argument("--min-universe", type=int, default=5000, help="Minimum expected seeded universe size.")
    parser.add_argument("--min-cache", type=int, default=10, help="Minimum expected seeded cache size.")
    parser.add_argument("--symbol", default="600519", help="Preferred smoke-test symbol.")
    return parser.parse_args()


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, text=True)


def tracked_and_untracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    files = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        rel = Path(line)
        if should_exclude(rel):
            continue
        files.append(rel)
    return files


def should_exclude(path: Path) -> bool:
    if not path.parts:
        return True
    top = path.parts[0]
    if top in EXCLUDED_TOP_LEVEL or top.startswith(EXCLUDED_PREFIXES):
        return True
    return any(path.parts[: len(parts)] == parts for parts in EXCLUDED_PATHS)


def copy_repo_snapshot(destination: Path) -> int:
    count = 0
    for rel in tracked_and_untracked_files():
        source = ROOT / rel
        if not source.is_file():
            continue
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1
    return count


def ensure_absent(path: Path) -> None:
    if path.exists():
        raise AssertionError(f"fresh clone snapshot unexpectedly contains {path}")


def count_cache_files(root: Path) -> int:
    cache_dir = root / "data" / "market" / "day"
    if not cache_dir.exists():
        return 0
    return len(list(cache_dir.glob("*.parquet")))


def main() -> int:
    args = parse_args()
    temp_root = Path(tempfile.mkdtemp(prefix="quantlab-fresh-clone-"))
    try:
        copied = copy_repo_snapshot(temp_root)
        ensure_absent(temp_root / "data")
        ensure_absent(temp_root / "config" / "quant.env")

        smoke_command = [
            sys.executable,
            "scripts/verify_runtime_smoke.py",
            "--min-universe",
            str(args.min_universe),
            "--min-cache",
            str(args.min_cache),
            "--symbol",
            args.symbol,
        ]
        run(smoke_command, temp_root)

        config_path = temp_root / "config" / "quant.env"
        universe_path = temp_root / "data" / "meta" / "symbols.parquet"
        cache_files = count_cache_files(temp_root)
        if not config_path.exists():
            raise AssertionError("runtime smoke did not create config/quant.env")
        if not universe_path.exists():
            raise AssertionError("runtime smoke did not create data/meta/symbols.parquet")
        if cache_files < args.min_cache:
            raise AssertionError(f"runtime smoke created only {cache_files} cached symbols")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "snapshot": str(temp_root),
                    "files_copied": copied,
                    "config_seeded": config_path.exists(),
                    "universe_seeded": universe_path.exists(),
                    "cache_files": cache_files,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        if args.keep:
            print(f"kept temporary clone at {temp_root}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
