from __future__ import annotations

import json
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from quant.data import get_store
from quant.data.concurrency import bounded_futures
from quant.data.feeds.tushare import TushareSource
from quant.screening.composer import (
    Condition,
    MetricContext,
    evaluate_condition,
    metric_registry,
)

from server.models.screening import (
    CompositeCondition,
    CompositeMetricDef,
    CompositeScanRequest,
    CompositeScanResult,
    CompositeStock,
    FactorStrategy,
    FactorStrategyDraft,
)

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "meta" / "screening.sqlite3"
_SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "meta" / "daily_basic"


def _condition(
    cid: str,
    metric: str,
    operator: str,
    value: float | int | str | None = 0,
    *,
    compare_metric: str | None = None,
    weight: float = 1,
    required: bool = True,
) -> CompositeCondition:
    return CompositeCondition(
        id=cid,
        metric=metric,
        operator=operator,
        value=value,
        compare_metric=compare_metric,
        weight=weight,
        required=required,
    )


def _builtin_strategy_drafts() -> dict[str, FactorStrategyDraft]:
    return {
        "builtin_ma_cross": FactorStrategyDraft(
            name="MA 均线交叉",
            description="由原 MA 均线策略迁移：快线高于慢线时持有，跌回慢线下方时退出。",
            logic="all",
            groups=[
                {
                    "id": "ma_trend",
                    "name": "均线趋势",
                    "logic": "all",
                    "conditions": [
                        _condition("ma_fast_above_slow", "ma5", "above_metric", None, compare_metric="ma20"),
                    ],
                }
            ],
            min_score=100,
            top_n=100,
            lookback=250,
        ),
        "builtin_vol_kdj_bbi": FactorStrategyDraft(
            name="量价KDJ+BBI",
            description="由原量价 KDJ+BBI 策略迁移：低位 KDJ、放量、价格站上 BBI。",
            logic="all",
            groups=[
                {
                    "id": "vol_kdj_bbi",
                    "name": "低位放量确认",
                    "logic": "all",
                    "conditions": [
                        _condition("kdj_j_low", "kdj_j", "lt", 10),
                        _condition("volume_expand", "volume_ratio_1", "gte", 1.5, weight=1.5),
                        _condition("close_above_bbi", "close", "above_metric", None, compare_metric="bbi"),
                    ],
                }
            ],
            min_score=100,
            top_n=100,
            lookback=250,
        ),
        "builtin_bbi_kdj_trend": FactorStrategyDraft(
            name="BBI趋势+KDJ择时",
            description="由原 BBI 趋势策略迁移：价格在 BBI 上方，KDJ 低位改善，量能不弱。",
            logic="all",
            groups=[
                {
                    "id": "bbi_kdj_trend",
                    "name": "趋势与择时",
                    "logic": "all",
                    "conditions": [
                        _condition("close_above_bbi", "close", "above_metric", None, compare_metric="bbi"),
                        _condition("kdj_j_timing", "kdj_j", "lt", 30),
                        _condition("volume_ok", "volume_ratio_1", "gte", 1.0, required=False),
                    ],
                }
            ],
            min_score=70,
            top_n=100,
            lookback=250,
        ),
        "builtin_dip_buy": FactorStrategyDraft(
            name="抄底（RSI+KDJ+VOL+BBI）",
            description="由原抄底策略迁移：KDJ/RSI 超卖，价格低于 BBI，伴随放量。",
            logic="all",
            groups=[
                {
                    "id": "dip_buy",
                    "name": "超跌放量",
                    "logic": "all",
                    "conditions": [
                        _condition("kdj_j_oversold", "kdj_j", "lt", 10),
                        _condition("rsi6_oversold", "rsi6", "lt", 25),
                        _condition("below_bbi", "close", "below_metric", None, compare_metric="bbi"),
                        _condition("volume_climax", "volume_ratio_1", "gte", 2.0, weight=1.5),
                    ],
                }
            ],
            min_score=100,
            top_n=100,
            lookback=250,
        ),
        "builtin_swing_dip_buy": FactorStrategyDraft(
            name="波段抄底（KDJ+RSI+VOL+BBI）",
            description="基于抄底策略优化：KDJ/RSI 低位、BBI低吸区、恐慌量/缩量回踩和反转确认共同触发，适合波段低吸。",
            logic="all",
            groups=[
                {
                    "id": "oversold_zone",
                    "name": "低位超卖",
                    "logic": "any",
                    "conditions": [
                        _condition("kdj_j_low", "kdj_j", "lt", 18),
                        _condition("rsi6_low", "rsi6", "lt", 32),
                    ],
                },
                {
                    "id": "bbi_volume_reversal",
                    "name": "BBI低吸与量能修复",
                    "logic": "all",
                    "conditions": [
                        _condition("close_below_bbi_zone", "close", "below_metric", None, compare_metric="bbi"),
                        _condition("volume_repair", "volume_ratio_1", "gte", 0.8, required=False),
                    ],
                },
            ],
            min_score=70,
            top_n=100,
            lookback=250,
        ),
    }


class FactorStrategyStore:
    def __init__(self, path: Path | str = _DB_PATH) -> None:
        self.path = Path(path)
        self._seeding = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS factor_strategies (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    definition_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def list(self) -> list[FactorStrategy]:
        if not self._seeding:
            self._seed_builtin_strategies()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM factor_strategies ORDER BY updated_at DESC"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get(self, strategy_id: str) -> FactorStrategy | None:
        if not self._seeding:
            self._seed_builtin_strategies()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM factor_strategies WHERE id = ?", (strategy_id,)
            ).fetchone()
        return self._from_row(row) if row else None

    def save(self, draft: FactorStrategyDraft, strategy_id: str | None = None) -> FactorStrategy:
        now = datetime.now().isoformat(timespec="seconds")
        strategy_id = strategy_id or uuid.uuid4().hex
        existing = self.get(strategy_id)
        created_at = existing.created_at if existing else now
        payload = draft.model_dump(mode="json")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO factor_strategies(id,name,description,definition_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    definition_json=excluded.definition_json,
                    updated_at=excluded.updated_at
                """,
                (
                    strategy_id, draft.name.strip(), draft.description.strip(),
                    json.dumps(payload, ensure_ascii=False), created_at, now,
                ),
            )
        return self.get(strategy_id)  # type: ignore[return-value]

    def delete(self, strategy_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM factor_strategies WHERE id = ?", (strategy_id,)
            )
        return cursor.rowcount > 0

    def _seed_builtin_strategies(self) -> None:
        if self._seeding:
            return
        self._seeding = True
        try:
            with self._connect() as conn:
                existing = {
                    row["id"]
                    for row in conn.execute(
                        "SELECT id FROM factor_strategies WHERE id LIKE 'builtin_%'"
                    ).fetchall()
                }
            for strategy_id, draft in _builtin_strategy_drafts().items():
                if strategy_id not in existing:
                    self.save(draft, strategy_id=strategy_id)
        finally:
            self._seeding = False

    @staticmethod
    def _from_row(row: sqlite3.Row) -> FactorStrategy:
        draft = FactorStrategyDraft.model_validate_json(row["definition_json"])
        return FactorStrategy(
            id=row["id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            **draft.model_dump(),
        )


def get_metric_defs() -> list[CompositeMetricDef]:
    return [
        CompositeMetricDef(
            key=m.key,
            label=m.label,
            category=m.category,
            description=m.description,
            unit=m.unit,
            value_type=m.value_type,
            operators=list(m.operators),
            params=[
                {
                    "key": p.key, "label": p.label, "default": p.default,
                    "min": p.minimum, "max": p.maximum, "step": p.step,
                }
                for p in m.params
            ],
            options=list(m.options),
            source=m.source,
        )
        for m in metric_registry()
    ]


def run_composite_scan(req: CompositeScanRequest, store: FactorStrategyStore) -> CompositeScanResult:
    started = time.monotonic()
    strategy = store.get(req.strategy_id) if req.strategy_id else None
    definition = req.definition or (
        FactorStrategyDraft(**strategy.model_dump(exclude={"id", "created_at", "updated_at"}))
        if strategy else None
    )
    if definition is None:
        raise ValueError("strategy_id or definition is required")
    if not definition.groups or not any(group.conditions for group in definition.groups):
        raise ValueError("strategy must contain at least one condition")

    scan_date = date.fromisoformat(req.scan_date) if req.scan_date else date.today()
    snapshots = _load_daily_basic(scan_date) if _needs_daily_basic(definition) else {}
    symbols = get_store().list_symbols("day")
    if req.max_symbols > 0:
        symbols = symbols[: req.max_symbols]

    results: list[CompositeStock] = []
    scan_errors: list[str] = []
    scan_error_count = 0
    workers = min(4, max(1, len(symbols)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for _, future in bounded_futures(
            executor,
            symbols,
            lambda symbol: _evaluate_symbol(
                symbol, definition, scan_date, snapshots.get(symbol, {})
            ),
            max_pending=workers * 2,
        ):
            try:
                stock = future.result()
                if stock is not None:
                    results.append(stock)
            except Exception as exc:
                scan_error_count += 1
                if len(scan_errors) < 5:
                    scan_errors.append(f"{type(exc).__name__}: {exc}")

    matched = [stock for stock in results if stock.matched]
    matched.sort(key=lambda stock: (stock.score, stock.amount), reverse=True)
    top = matched[: definition.top_n]
    return CompositeScanResult(
        strategy_id=strategy.id if strategy else None,
        strategy_name=definition.name,
        scan_date=str(scan_date),
        total_scanned=len(symbols),
        total_matched=len(matched),
        returned=len(top),
        stocks=top,
        elapsed_seconds=round(time.monotonic() - started, 2),
        warnings=(
            (
                ["换手率快照不可用，包含换手率的必须条件将不会命中"]
                if _needs_daily_basic(definition) and not snapshots else []
            )
            + (
                [f"{scan_error_count} 只股票计算失败：{scan_errors[0]}"]
                if scan_errors else []
            )
        ),
    )


def _evaluate_symbol(
    symbol: str,
    definition: FactorStrategyDraft,
    scan_date: date,
    snapshot: dict[str, Any],
) -> CompositeStock | None:
    df = get_store().get_kline(
        symbol,
        freq="day",
        end=scan_date,
        with_indicators=True,
        tail=definition.lookback,
    )
    if df is None or len(df) < 30:
        return None
    context = MetricContext(df, snapshot)
    group_passes: list[bool] = []
    weighted_total = weighted_passed = 0.0
    passed_count = available_count = 0
    reasons: list[str] = []
    failures: list[str] = []
    values: dict[str, Any] = {}

    for group in definition.groups:
        required_results: list[bool] = []
        for raw in group.conditions:
            if not raw.enabled:
                continue
            condition = Condition(**raw.model_dump(exclude={"id"}))
            result = evaluate_condition(context, condition)
            values[raw.id] = {
                "metric": raw.metric,
                "value": result.value,
                "target": result.target,
                "passed": result.passed,
                "available": result.available,
            }
            if result.available:
                available_count += 1
                weighted_total += max(0.0, raw.weight)
                if result.passed:
                    passed_count += 1
                    weighted_passed += max(0.0, raw.weight)
                    reasons.append(result.message)
                elif raw.required:
                    failures.append(result.message)
            elif raw.required:
                failures.append(result.message)
            if raw.required:
                required_results.append(result.available and result.passed)
        if not required_results:
            group_passes.append(True)
        elif group.logic == "all":
            group_passes.append(all(required_results))
        else:
            group_passes.append(any(required_results))

    hard_pass = (
        all(group_passes) if definition.logic == "all" else any(group_passes)
    )
    score = round(weighted_passed / weighted_total * 100, 1) if weighted_total else 100.0
    matched = hard_pass and score >= definition.min_score
    last = df.iloc[-1]
    previous_close = float(df.iloc[-2]["close"]) if len(df) >= 2 else float(last["close"])
    pct_chg = (
        (float(last["close"]) / previous_close - 1) * 100
        if previous_close else 0.0
    )
    return CompositeStock(
        symbol=symbol,
        matched=matched,
        score=score,
        passed_conditions=passed_count,
        available_conditions=available_count,
        total_conditions=sum(
            condition.enabled
            for group in definition.groups
            for condition in group.conditions
        ),
        signal_date=str(last["dt"]),
        close=round(float(last["close"]), 3),
        pct_chg=round(pct_chg, 2),
        volume=float(last["volume"]),
        amount=float(last.get("amount", 0)),
        turnover_rate=(
            float(snapshot["turnover_rate"])
            if snapshot.get("turnover_rate") is not None else None
        ),
        reasons=reasons[:12],
        failures=failures[:8],
        values=values,
    )


def _needs_daily_basic(definition: FactorStrategyDraft) -> bool:
    return any(
        condition.metric == "turnover_rate"
        for group in definition.groups for condition in group.conditions
        if condition.enabled
    )


def _load_daily_basic(scan_date: date) -> dict[str, dict[str, Any]]:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = _SNAPSHOT_DIR / f"{scan_date:%Y%m%d}.parquet"
    frame = pd.DataFrame()
    if path.exists():
        frame = pd.read_parquet(path)
    else:
        try:
            source = TushareSource()
            for offset in range(11):
                trade_date = scan_date - timedelta(days=offset)
                actual_path = _SNAPSHOT_DIR / f"{trade_date:%Y%m%d}.parquet"
                if actual_path.exists():
                    frame = pd.read_parquet(actual_path)
                else:
                    frame = source.fetch_daily_basic(trade_date.strftime("%Y%m%d"))
                    if not frame.empty:
                        frame.to_parquet(actual_path, index=False)
                if not frame.empty:
                    if actual_path != path:
                        frame.to_parquet(path, index=False)
                    break
        except Exception:
            frame = pd.DataFrame()
    if frame.empty:
        return {}
    return {
        str(row["symbol"]).zfill(6): row.to_dict()
        for _, row in frame.iterrows()
    }
