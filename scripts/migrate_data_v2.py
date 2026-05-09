#!/usr/bin/env python3
"""一次性数据迁移：扁平 ``data/{symbol}.parquet`` → ``data/market/day/{symbol}.parquet``
+ 计算 20 个指标列 + 重采样得到 week/month + 构建 meta 元信息。

幂等：每个 stage 可以重复运行；--dry-run 只盘点不写入。

用法：
    python scripts/migrate_data_v2.py --dry-run
    python scripts/migrate_data_v2.py --stage=copy
    python scripts/migrate_data_v2.py --stage=meta
    python scripts/migrate_data_v2.py --stage=indicators
    python scripts/migrate_data_v2.py --stage=resample
    python scripts/migrate_data_v2.py --stage=flip       # 移动旧文件到 data/legacy/
    python scripts/migrate_data_v2.py --all              # 跑除 flip 外的所有 stage
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.data import compute_all, get_store  # noqa: E402
from quant.data.indicators import indicator_versions  # noqa: E402
from quant.data.schema import OHLCV_COLUMNS, normalize_kline, safe_write_parquet  # noqa: E402

LEGACY_DIR = ROOT / "data"
NEW_DIR = ROOT / "data" / "market" / "day"
META_DIR = ROOT / "data" / "meta"
LEGACY_BACKUP = ROOT / "data" / "legacy"


# ─── stage: dry-run ────────────────────────────────────────────────────────
def stage_dry_run() -> dict:
    files = [p for p in LEGACY_DIR.glob("*.parquet") if p.is_file()]
    total_bytes = sum(p.stat().st_size for p in files)
    sample = files[:5]
    schema_issues: list[str] = []
    for p in sample:
        try:
            df = pd.read_parquet(p)
            missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
            if missing:
                schema_issues.append(f"{p.name}: missing {missing}")
        except Exception as e:
            schema_issues.append(f"{p.name}: {e}")
    print(f"[dry-run] legacy files:    {len(files)}")
    print(f"[dry-run] total size:      {total_bytes / 1e6:.1f} MB")
    print(f"[dry-run] target dir:      {NEW_DIR}")
    print(f"[dry-run] schema sample:   {len(sample)} files OK"
          if not schema_issues else f"[dry-run] schema issues: {schema_issues}")
    return {"files": len(files), "bytes": total_bytes, "issues": schema_issues}


# ─── stage: copy ───────────────────────────────────────────────────────────
def _copy_one(p: Path) -> tuple[str, str]:
    sym = p.stem
    target = NEW_DIR / f"{sym}.parquet"
    try:
        df = pd.read_parquet(p)
        if df.empty:
            return (sym, "skipped_empty")
        missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
        if missing:
            return (sym, f"schema_drift:{missing}")
        df = normalize_kline(df)
        # No indicators yet — written in stage_indicators
        safe_write_parquet(df, target, indicator_versions={})
        return (sym, "ok")
    except Exception as e:
        return (sym, f"error:{e}")


def stage_copy(workers: int = 8) -> dict:
    NEW_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in LEGACY_DIR.glob("*.parquet") if p.is_file())
    if not files:
        # Maybe already migrated — bail out with a note instead of silent no-op
        if NEW_DIR.exists() and any(NEW_DIR.glob("*.parquet")):
            print(f"[copy] no legacy files found; new dir already populated ({len(list(NEW_DIR.glob('*.parquet')))} files)")
            return {"copied": 0, "skipped": 0, "errors": []}
        print("[copy] no legacy files found and new dir empty — nothing to do")
        return {"copied": 0, "skipped": 0, "errors": []}

    t0 = time.monotonic()
    ok = skipped = 0
    errors: list[str] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_copy_one, p) for p in files]
        for i, fut in enumerate(as_completed(futs)):
            sym, status = fut.result()
            if status == "ok":
                ok += 1
            elif status.startswith("skipped"):
                skipped += 1
            else:
                errors.append(f"{sym}: {status}")
            if (i + 1) % 500 == 0:
                print(f"[copy] {i + 1}/{len(files)} ({ok} ok, {len(errors)} errors)")
    elapsed = time.monotonic() - t0
    print(f"[copy] done: {ok} ok, {skipped} skipped, {len(errors)} errors ({elapsed:.1f}s)")
    if errors[:5]:
        print(f"[copy] first errors: {errors[:5]}")
    return {"copied": ok, "skipped": skipped, "errors": errors}


# ─── stage: meta ───────────────────────────────────────────────────────────
def stage_meta() -> dict:
    META_DIR.mkdir(parents=True, exist_ok=True)
    store = get_store()

    # 1. symbols.parquet — 用 AKShare 拉一遍带名称/市场
    print("[meta] fetching symbol universe via AKShare...")
    try:
        from quant.data.feeds.akshare import AKShareSource
        rows = AKShareSource().list_symbols()
    except Exception as e:
        print(f"[meta] AKShare failed: {e}; falling back to filename-only")
        rows = []

    if not rows:
        # Fallback: derive from cached files
        files = list(NEW_DIR.glob("*.parquet")) or list(LEGACY_DIR.glob("*.parquet"))
        for p in files:
            code = p.stem
            if not (code.isdigit() and len(code) == 6):
                continue
            mkt = "SH" if code.startswith(("6", "5", "9")) else "SZ" if code.startswith(("0", "3")) else "BJ"
            rows.append({"symbol": code, "name": "", "market": mkt})

    df_sym = pd.DataFrame(rows)
    df_sym.to_parquet(META_DIR / "symbols.parquet", index=False)
    print(f"[meta] symbols.parquet: {len(df_sym)} rows")

    # 2. trade_calendar.parquet
    print("[meta] fetching trade calendar via AKShare...")
    try:
        from quant.data.updater import refresh_calendar
        n_open = refresh_calendar(store=store)
        print(f"[meta] trade_calendar.parquet: {n_open} open days")
    except Exception as e:
        print(f"[meta] calendar fetch failed: {e}; skipping")

    # 3. last_update.parquet — 从 day 文件 dt.max() 推断
    files = sorted(NEW_DIR.glob("*.parquet"))
    last_rows = []
    for p in files:
        try:
            df = pd.read_parquet(p, columns=["dt"])
            if df.empty:
                continue
            last_rows.append({
                "symbol": p.stem, "freq": "day",
                "last_dt": df["dt"].max(),
                "source": "migration",
                "ts_updated": pd.Timestamp.now(),
            })
        except Exception:
            continue
    df_last = pd.DataFrame(last_rows)
    df_last.to_parquet(META_DIR / "last_update.parquet", index=False)
    print(f"[meta] last_update.parquet: {len(df_last)} rows")

    return {"symbols": len(df_sym), "last_update_rows": len(df_last)}


# ─── stage: indicators ─────────────────────────────────────────────────────
def _materialize_one(p: Path) -> tuple[str, str]:
    try:
        df = pd.read_parquet(p)
        if df.empty:
            return (p.stem, "skipped_empty")
        full = compute_all(df)
        safe_write_parquet(full, p, indicator_versions=indicator_versions())
        return (p.stem, "ok")
    except Exception as e:
        return (p.stem, f"error:{e}")


def stage_indicators(workers: int = 8) -> dict:
    files = sorted(NEW_DIR.glob("*.parquet"))
    if not files:
        print("[indicators] no day parquets — run --stage=copy first")
        return {"computed": 0, "errors": []}
    t0 = time.monotonic()
    ok = 0
    errors: list[str] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_materialize_one, p) for p in files]
        for i, fut in enumerate(as_completed(futs)):
            sym, status = fut.result()
            if status == "ok":
                ok += 1
            elif not status.startswith("skipped"):
                errors.append(f"{sym}: {status}")
            if (i + 1) % 500 == 0:
                print(f"[indicators] {i + 1}/{len(files)} ({ok} ok)")
    elapsed = time.monotonic() - t0
    print(f"[indicators] done: {ok}/{len(files)} ({elapsed:.1f}s)")
    if errors[:5]:
        print(f"[indicators] first errors: {errors[:5]}")
    return {"computed": ok, "errors": errors}


# ─── stage: resample ───────────────────────────────────────────────────────
def stage_resample() -> dict:
    from quant.data.updater import derive_week_month

    print("[resample] day → week...")
    week = derive_week_month(target_freq="week")
    print(f"[resample] week: {week.updated} updated, {week.failed} failed ({week.elapsed_s:.1f}s)")

    print("[resample] day → month...")
    month = derive_week_month(target_freq="month")
    print(f"[resample] month: {month.updated} updated, {month.failed} failed ({month.elapsed_s:.1f}s)")
    return {"week": week.updated, "month": month.updated}


# ─── stage: flip ───────────────────────────────────────────────────────────
def stage_flip(confirm: bool = False) -> dict:
    """把扁平 ``data/*.parquet`` 移动到 ``data/legacy/`` 备份。

    需要 --confirm 才执行。这是不可逆操作（理论上可以复原，但请先验证新路径数据正确）。
    """
    if not confirm:
        print("[flip] DRY: 加 --confirm 才会真正移动文件")
    files = sorted(p for p in LEGACY_DIR.glob("*.parquet") if p.is_file())
    if not files:
        print("[flip] no legacy files to move (already flipped?)")
        return {"moved": 0}
    LEGACY_BACKUP.mkdir(parents=True, exist_ok=True)
    if not confirm:
        print(f"[flip] would move {len(files)} files to {LEGACY_BACKUP}")
        return {"would_move": len(files)}
    moved = 0
    for p in files:
        target = LEGACY_BACKUP / p.name
        if target.exists():
            target.unlink()
        shutil.move(str(p), str(target))
        moved += 1
    print(f"[flip] moved {moved} files to {LEGACY_BACKUP}")
    return {"moved": moved}


# ─── CLI ───────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stage", choices=["copy", "meta", "indicators", "resample", "flip"])
    parser.add_argument("--all", action="store_true",
                        help="run copy + meta + indicators + resample (skip flip)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--confirm", action="store_true", help="required for --stage=flip")
    args = parser.parse_args()

    if args.dry_run:
        stage_dry_run()
        return
    if args.all:
        stage_copy(workers=args.workers)
        stage_meta()
        stage_indicators(workers=args.workers)
        stage_resample()
        return
    if args.stage == "copy":
        stage_copy(workers=args.workers)
    elif args.stage == "meta":
        stage_meta()
    elif args.stage == "indicators":
        stage_indicators(workers=args.workers)
    elif args.stage == "resample":
        stage_resample()
    elif args.stage == "flip":
        stage_flip(confirm=args.confirm)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
