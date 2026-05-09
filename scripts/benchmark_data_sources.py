"""A 股日线数据源下载速度 benchmark。

对比 tushare / tdx / akshare / baostock / tickflow 在串行 + 并发模式下，
拉取 ~40 只样本股 3 年日线的耗时。

依赖：
    pip install baostock tickflow

运行：
    python /root/quant/scripts/benchmark_data_sources.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd

# 让脚本能从 /root/quant 启动并 import quant.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
SYMBOLS: list[str] = [
    # 沪主板（10）
    "600519", "600036", "600000", "600276", "600887",
    "601318", "601398", "601857", "601012", "600030",
    # 深主板（10）
    "000001", "000002", "000333", "000651", "000858",
    "000725", "000063", "000568", "000625", "000776",
    # 创业板（10）
    "300750", "300059", "300015", "300760", "300122",
    "300274", "300433", "300498", "300661", "300999",
    # 科创板（10）
    "688981", "688111", "688036", "688012", "688256",
    "688599", "688008", "688041", "688169", "688303",
]

START_YYYYMMDD = "20220101"
END_YYYYMMDD = date.today().strftime("%Y%m%d")
START_DASH = f"{START_YYYYMMDD[:4]}-{START_YYYYMMDD[4:6]}-{START_YYYYMMDD[6:8]}"
END_DASH = f"{END_YYYYMMDD[:4]}-{END_YYYYMMDD[4:6]}-{END_YYYYMMDD[6:8]}"

MAX_WORKERS = 8
# 不同源的并发上限：akshare/tickflow 要保守一点，避免被限流卡死
PER_SOURCE_WORKERS = {
    "tushare": 8,
    "tdx": 8,
    "akshare": 4,    # eastmoney 对 IP 限流，>4 容易卡死
    "baostock": 8,
    "tickflow": 4,
}
# 并发段每源的壁钟硬上限，超时未完成的标 timeout
CONCURRENT_CAP_SEC = 120

# ---------------------------------------------------------------------------
# 各数据源的 _fetch_daily 适配器
# 返回标准列：dt(date), open/high/low/close/volume/amount(float)
# ---------------------------------------------------------------------------

def _fetch_tushare(symbol: str) -> pd.DataFrame:
    from quant.data.tushare_feed import _fetch_daily
    return _fetch_daily(symbol, START_YYYYMMDD, END_YYYYMMDD)


def _fetch_tdx(symbol: str) -> pd.DataFrame:
    from quant.data.tdx_feed import _fetch_daily
    return _fetch_daily(symbol, START_YYYYMMDD, END_YYYYMMDD)


def _fetch_akshare(symbol: str) -> pd.DataFrame:
    from quant.data.akshare_feed import _fetch_daily
    return _fetch_daily(symbol, START_YYYYMMDD, END_YYYYMMDD)


# Baostock —— 全局 session，加锁防并发首调
_BS_LOGIN_LOCK = threading.Lock()
_bs_logged_in = False


def _bs_ensure_login() -> None:
    global _bs_logged_in
    if _bs_logged_in:
        return
    with _BS_LOGIN_LOCK:
        if _bs_logged_in:
            return
        import baostock as bs
        rs = bs.login()
        if rs.error_code != "0":
            raise RuntimeError(f"baostock login failed: {rs.error_msg}")
        _bs_logged_in = True


def _bs_code(symbol: str) -> str:
    return f"sh.{symbol}" if symbol.startswith(("6", "9")) else f"sz.{symbol}"


def _fetch_baostock(symbol: str) -> pd.DataFrame:
    import baostock as bs
    _bs_ensure_login()
    rs = bs.query_history_k_data_plus(
        _bs_code(symbol),
        "date,open,high,low,close,volume,amount",
        start_date=START_DASH, end_date=END_DASH,
        frequency="d", adjustflag="2",
    )
    if rs.error_code != "0":
        raise RuntimeError(f"baostock query failed: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        raise ValueError(f"No data for {symbol}")
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    df["dt"] = pd.to_datetime(df["date"]).dt.date
    df = df[["dt", "open", "high", "low", "close", "volume", "amount"]]
    df = df.sort_values("dt").reset_index(drop=True)
    return df


# TickFlow
_TF_CLIENT = None
_TF_LOCK = threading.Lock()


def _tf_client():
    global _TF_CLIENT
    if _TF_CLIENT is not None:
        return _TF_CLIENT
    with _TF_LOCK:
        if _TF_CLIENT is None:
            from tickflow import TickFlow
            _TF_CLIENT = TickFlow.free()
    return _TF_CLIENT


def _fetch_tickflow(symbol: str) -> pd.DataFrame:
    tf = _tf_client()
    suffix = "SH" if symbol.startswith(("6", "9")) else "SZ"
    df = tf.klines.get(
        f"{symbol}.{suffix}",
        period="1d",
        count=1000,
        adjust="forward",
        as_dataframe=True,
    )
    if df is None or df.empty:
        raise ValueError(f"No data for {symbol}")
    if "trade_date" not in df.columns and "timestamp" in df.columns:
        df = df.copy()
        df["dt"] = pd.to_datetime(df["timestamp"], unit="ms").dt.date
    else:
        df = df.copy()
        df["dt"] = pd.to_datetime(df["trade_date"]).dt.date
    df = df[df["dt"] >= date.fromisoformat(START_DASH)]
    df = df[df["dt"] <= date.fromisoformat(END_DASH)]
    if "amount" not in df.columns:
        df["amount"] = df["close"] * df["volume"]
    df = df[["dt", "open", "high", "low", "close", "volume", "amount"]]
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("dt").reset_index(drop=True)
    return df


SOURCES = {
    "tushare": _fetch_tushare,
    "tdx": _fetch_tdx,
    "akshare": _fetch_akshare,
    "baostock": _fetch_baostock,
    "tickflow": _fetch_tickflow,
}


# ---------------------------------------------------------------------------
# Bench runner
# ---------------------------------------------------------------------------

def _run_one(fetcher, sym: str) -> tuple[str, int | None, str | None]:
    try:
        df = fetcher(sym)
        return (sym, len(df), None)
    except Exception as e:
        return (sym, None, f"{type(e).__name__}: {e}")


def run_sequential(fetcher, symbols: list[str]) -> dict:
    t0 = time.perf_counter()
    rows = []
    for sym in symbols:
        rows.append(_run_one(fetcher, sym))
    elapsed = time.perf_counter() - t0
    return _summarize(rows, elapsed)


def run_concurrent(fetcher, symbols: list[str], max_workers: int,
                   cap_sec: float = CONCURRENT_CAP_SEC) -> dict:
    """跑并发拉取。设壁钟硬上限避免卡死整个 benchmark。"""
    t0 = time.perf_counter()
    rows: list[tuple] = []
    seen: set[str] = set()
    ex = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futs = {ex.submit(_run_one, fetcher, sym): sym for sym in symbols}
        deadline = t0 + cap_sec
        for fut in as_completed(futs):
            try:
                remaining = max(0.0, deadline - time.perf_counter())
                rows.append(fut.result(timeout=remaining or 0.001))
            except Exception as e:
                sym = futs[fut]
                rows.append((sym, None, f"future-error: {type(e).__name__}: {e}"))
                seen.add(sym)
                continue
            seen.add(futs[fut])
            if time.perf_counter() > deadline:
                break
        # 没完成的标 timeout
        for fut, sym in futs.items():
            if sym in seen:
                continue
            rows.append((sym, None, f"timeout after {cap_sec}s"))
            fut.cancel()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    elapsed = time.perf_counter() - t0
    return _summarize(rows, elapsed)


def _summarize(rows: list[tuple], elapsed: float) -> dict:
    total = len(rows)
    errors = [(s, e) for s, _, e in rows if e is not None]
    ok_rows = [n for _, n, e in rows if e is None and n is not None]
    return {
        "total_sec": round(elapsed, 2),
        "n_ok": len(ok_rows),
        "n_err": len(errors),
        "mean_sec_per_stock": round(elapsed / total, 3) if total else None,
        "median_rows": int(pd.Series(ok_rows).median()) if ok_rows else None,
        "min_rows": min(ok_rows) if ok_rows else None,
        "max_rows": max(ok_rows) if ok_rows else None,
        "row_counts": {s: n for s, n, e in rows if e is None and n is not None},
        "errors": errors[:5],
    }


def main() -> None:
    print(f"=== A 股日线数据源 benchmark ===")
    print(f"样本: {len(SYMBOLS)} 只 | 区间: {START_DASH} ~ {END_DASH} | 并发: {MAX_WORKERS}")
    print()

    results: dict = {
        "config": {
            "symbols": SYMBOLS,
            "start": START_DASH,
            "end": END_DASH,
            "max_workers": MAX_WORKERS,
        },
        "runs": {},
    }

    for src_name, fetcher in SOURCES.items():
        print(f"--- {src_name} ---")

        # 探活：拉一只验证 import 与连接
        probe_err = None
        try:
            df = fetcher(SYMBOLS[0])
            print(f"  probe ok: {SYMBOLS[0]} -> {len(df)} bars")
        except Exception as e:
            probe_err = f"{type(e).__name__}: {e}"
            print(f"  probe FAILED: {probe_err}")
            traceback.print_exc(limit=2)
            results["runs"][src_name] = {"skipped": probe_err}
            print()
            continue

        try:
            seq = run_sequential(fetcher, SYMBOLS)
            print(f"  sequential : {seq['total_sec']}s  "
                  f"({seq['mean_sec_per_stock']}s/stock, "
                  f"{seq['n_ok']}/{len(SYMBOLS)} ok, median {seq['median_rows']} rows)")
        except Exception as e:
            seq = {"error": f"{type(e).__name__}: {e}"}
            print(f"  sequential FAILED: {seq['error']}")

        try:
            workers = PER_SOURCE_WORKERS.get(src_name, MAX_WORKERS)
            par = run_concurrent(fetcher, SYMBOLS, workers)
            print(f"  concurrent ({workers}w): {par['total_sec']}s  "
                  f"({par['mean_sec_per_stock']}s/stock, "
                  f"{par['n_ok']}/{len(SYMBOLS)} ok, median {par['median_rows']} rows)")
        except Exception as e:
            par = {"error": f"{type(e).__name__}: {e}"}
            print(f"  concurrent FAILED: {par['error']}")

        results["runs"][src_name] = {"sequential": seq, "concurrent": par}
        print()

    # 落 JSON
    out = Path("/root/quant/data/_benchmark_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"结果已写入 {out}")
    print()

    # 总结表
    print("=== 汇总 ===")
    table_rows = []
    for src, info in results["runs"].items():
        if "skipped" in info:
            table_rows.append({"source": src, "mode": "-", "total_sec": None,
                               "mean_per_stock": None, "ok": 0,
                               "err": len(SYMBOLS), "note": info["skipped"][:60]})
            continue
        for mode in ("sequential", "concurrent"):
            r = info.get(mode, {})
            if "error" in r:
                table_rows.append({"source": src, "mode": mode, "total_sec": None,
                                   "mean_per_stock": None, "ok": 0,
                                   "err": len(SYMBOLS), "note": r["error"][:60]})
            else:
                table_rows.append({
                    "source": src, "mode": mode,
                    "total_sec": r["total_sec"],
                    "mean_per_stock": r["mean_sec_per_stock"],
                    "ok": r["n_ok"], "err": r["n_err"],
                    "note": "",
                })
    df = pd.DataFrame(table_rows)
    print(df.to_string(index=False))

    # Row-count 一致性检查
    print()
    print("=== 行数一致性检查（各源 vs 跨源中位数） ===")
    rc: dict[str, dict[str, int]] = {}
    for src, info in results["runs"].items():
        if "skipped" in info:
            continue
        seq = info.get("sequential", {})
        rc[src] = seq.get("row_counts", {}) if isinstance(seq, dict) else {}
    if rc:
        all_syms = set()
        for d in rc.values():
            all_syms.update(d.keys())
        rows = []
        for sym in sorted(all_syms):
            vals = {src: d[sym] for src, d in rc.items() if sym in d}
            if not vals:
                continue
            med = pd.Series(list(vals.values())).median()
            for src, n in vals.items():
                if med and abs(n - med) / med > 0.05:
                    rows.append({"symbol": sym, "source": src, "rows": n,
                                 "median": int(med),
                                 "diff_pct": round((n - med) / med * 100, 1)})
        if rows:
            print(pd.DataFrame(rows).to_string(index=False))
        else:
            print("  全部源行数偏差 ≤ 5%（一致性 OK）")


if __name__ == "__main__":
    main()
