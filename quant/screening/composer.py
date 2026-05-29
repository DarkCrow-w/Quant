"""Composable factor strategy engine.

The engine exposes a metric registry shared by the API and UI. Strategies
combine hard filters with weighted score conditions and can compare metrics
against constants or other metrics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from quant.data import indicators
from .patterns import centipede, sandglass_score


@dataclass(frozen=True)
class MetricParam:
    key: str
    label: str
    default: float
    minimum: float
    maximum: float
    step: float = 1.0


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    category: str
    description: str
    unit: str = ""
    value_type: str = "number"
    operators: tuple[str, ...] = (
        "gte", "lte", "gt", "lt", "between", "above_metric",
        "below_metric", "cross_above", "cross_below", "rising", "falling",
    )
    params: tuple[MetricParam, ...] = ()
    options: tuple[str, ...] = ()
    source: str = "kline"


_LABELS = {
    "ma5": ("MA5", "trend"), "ma10": ("MA10", "trend"),
    "ma20": ("MA20", "trend"), "ma60": ("MA60", "trend"),
    "ema12": ("EMA12", "trend"), "ema26": ("EMA26", "trend"),
    "dif": ("MACD DIF", "momentum"), "dea": ("MACD DEA", "momentum"),
    "macd": ("MACD柱", "momentum"),
    "kdj_k": ("KDJ-K", "momentum"), "kdj_d": ("KDJ-D", "momentum"),
    "kdj_j": ("KDJ-J", "momentum"),
    "rsi6": ("RSI6", "momentum"), "rsi12": ("RSI12", "momentum"),
    "rsi24": ("RSI24", "momentum"),
    "boll_mid": ("布林中轨", "trend"), "boll_up": ("布林上轨", "trend"),
    "boll_dn": ("布林下轨", "trend"), "bbi": ("BBI多空线", "trend"),
    "wr10": ("WR10", "momentum"), "wr6": ("WR6", "momentum"),
    "cci": ("CCI", "momentum"), "pdi": ("DMI PDI", "trend"),
    "mdi": ("DMI MDI", "trend"), "adx": ("ADX", "trend"),
    "adxr": ("ADXR", "trend"), "atr": ("ATR", "risk"),
    "obv": ("OBV", "volume"), "mavol5": ("5日均量", "volume"),
    "mavol10": ("10日均量", "volume"), "sar": ("SAR", "trend"),
    "trix": ("TRIX", "trend"), "trix_ma": ("TRIX均线", "trend"),
    "dma": ("DMA", "trend"), "dma_ama": ("DMA均线", "trend"),
    "expma12": ("EXPMA12", "trend"), "expma50": ("EXPMA50", "trend"),
    "psy": ("PSY", "momentum"), "psyma": ("PSY均线", "momentum"),
    "mtm": ("MTM", "momentum"), "mtmma": ("MTM均线", "momentum"),
    "roc": ("ROC", "momentum"), "rocma": ("ROC均线", "momentum"),
}


def metric_registry() -> list[MetricDef]:
    metrics = [
        MetricDef("close", "收盘价", "price", "最新收盘价", "元"),
        MetricDef("pct_chg", "涨跌幅", "price", "相对前一交易日涨跌幅", "%"),
        MetricDef("amplitude", "振幅", "price", "当日最高最低振幅", "%"),
        MetricDef("amount", "成交额", "volume", "当日成交额", "元"),
        MetricDef("volume", "成交量", "volume", "当日成交量", "股"),
        MetricDef("volume_ratio_5", "量比(5日)", "volume", "成交量/5日均量", "倍"),
        MetricDef("volume_ratio_10", "量比(10日)", "volume", "成交量/10日均量", "倍"),
        MetricDef("obv_change_10", "OBV十日变化", "volume", "OBV相对十日前变化", "%"),
        MetricDef(
            "turnover_rate", "换手率", "fundamental",
            "真实换手率，来自TuShare daily_basic快照", "%",
            source="daily_basic",
        ),
    ]
    for column in indicators.all_indicator_columns():
        label, category = _LABELS.get(column, (column.upper(), "technical"))
        unit = "%" if column.startswith(("rsi", "wr", "psy", "roc")) else ""
        metrics.append(MetricDef(column, label, category, f"项目内置指标 {label}", unit))

    period = MetricParam("period", "周期", 20, 2, 250)
    metrics.extend([
        MetricDef("ma_custom", "自定义MA", "custom", "自定义周期简单移动平均", params=(period,)),
        MetricDef("ema_custom", "自定义EMA", "custom", "自定义周期指数移动平均", params=(period,)),
        MetricDef("rsi_custom", "自定义RSI", "custom", "自定义周期Wilder RSI", "%", params=(MetricParam("period", "周期", 14, 2, 120),)),
        MetricDef("volume_ma_custom", "自定义均量", "custom", "自定义周期成交量均线", "股", params=(period,)),
        MetricDef("pct_chg_custom", "区间涨跌幅", "custom", "指定周期累计涨跌幅", "%", params=(MetricParam("period", "周期", 5, 1, 250),)),
        MetricDef("kdj_k_custom", "自定义KDJ-K", "custom", "可调整N、K、D平滑周期", params=(
            MetricParam("period", "N", 9, 3, 120),
            MetricParam("k_smooth", "K平滑", 3, 1, 30),
            MetricParam("d_smooth", "D平滑", 3, 1, 30),
        )),
        MetricDef("kdj_d_custom", "自定义KDJ-D", "custom", "可调整N、K、D平滑周期", params=(
            MetricParam("period", "N", 9, 3, 120),
            MetricParam("k_smooth", "K平滑", 3, 1, 30),
            MetricParam("d_smooth", "D平滑", 3, 1, 30),
        )),
        MetricDef("kdj_j_custom", "自定义KDJ-J", "custom", "可调整N、K、D平滑周期", params=(
            MetricParam("period", "N", 9, 3, 120),
            MetricParam("k_smooth", "K平滑", 3, 1, 30),
            MetricParam("d_smooth", "D平滑", 3, 1, 30),
        )),
        MetricDef("macd_dif_custom", "自定义MACD-DIF", "custom", "可调整快线、慢线和信号周期", params=(
            MetricParam("fast", "快线", 12, 2, 120),
            MetricParam("slow", "慢线", 26, 3, 250),
            MetricParam("signal", "信号", 9, 2, 120),
        )),
        MetricDef("macd_dea_custom", "自定义MACD-DEA", "custom", "可调整快线、慢线和信号周期", params=(
            MetricParam("fast", "快线", 12, 2, 120),
            MetricParam("slow", "慢线", 26, 3, 250),
            MetricParam("signal", "信号", 9, 2, 120),
        )),
        MetricDef("macd_bar_custom", "自定义MACD柱", "custom", "DIF与DEA差值的两倍", params=(
            MetricParam("fast", "快线", 12, 2, 120),
            MetricParam("slow", "慢线", 26, 3, 250),
            MetricParam("signal", "信号", 9, 2, 120),
        )),
        MetricDef("cci_custom", "自定义CCI", "custom", "可调整计算周期的顺势指标", params=(
            MetricParam("period", "周期", 14, 2, 250),
        )),
        MetricDef("dmi_pdi_custom", "自定义DMI-PDI", "custom", "正向趋势强度", params=(
            MetricParam("period", "DMI周期", 14, 2, 120),
            MetricParam("adx_period", "ADX平滑", 6, 2, 60),
        )),
        MetricDef("dmi_mdi_custom", "自定义DMI-MDI", "custom", "负向趋势强度", params=(
            MetricParam("period", "DMI周期", 14, 2, 120),
            MetricParam("adx_period", "ADX平滑", 6, 2, 60),
        )),
        MetricDef("dmi_adx_custom", "自定义DMI-ADX", "custom", "趋势强度指标", params=(
            MetricParam("period", "DMI周期", 14, 2, 120),
            MetricParam("adx_period", "ADX平滑", 6, 2, 60),
        )),
        MetricDef("dmi_adxr_custom", "自定义DMI-ADXR", "custom", "ADX平滑趋势强度", params=(
            MetricParam("period", "DMI周期", 14, 2, 120),
            MetricParam("adx_period", "ADX平滑", 6, 2, 60),
        )),
        MetricDef("dma_custom", "自定义DMA", "custom", "短周期均线减长周期均线", params=(
            MetricParam("short", "短周期", 10, 2, 120),
            MetricParam("long", "长周期", 50, 3, 500),
            MetricParam("ama", "信号周期", 10, 2, 120),
        )),
        MetricDef("dma_ama_custom", "自定义DMA均线", "custom", "DMA的信号均线", params=(
            MetricParam("short", "短周期", 10, 2, 120),
            MetricParam("long", "长周期", 50, 3, 500),
            MetricParam("ama", "信号周期", 10, 2, 120),
        )),
        MetricDef("bbi_custom", "自定义BBI", "custom", "四条均线平均形成的多空线", params=(
            MetricParam("p1", "周期1", 3, 2, 120),
            MetricParam("p2", "周期2", 6, 2, 180),
            MetricParam("p3", "周期3", 12, 2, 250),
            MetricParam("p4", "周期4", 24, 2, 500),
        )),
        MetricDef("boll_mid_custom", "自定义BOLL中轨", "custom", "布林带中轨", params=(
            MetricParam("period", "周期", 20, 2, 250),
            MetricParam("deviation", "标准差", 2, 0.1, 6, 0.1),
        )),
        MetricDef("boll_up_custom", "自定义BOLL上轨", "custom", "布林带上轨", params=(
            MetricParam("period", "周期", 20, 2, 250),
            MetricParam("deviation", "标准差", 2, 0.1, 6, 0.1),
        )),
        MetricDef("boll_dn_custom", "自定义BOLL下轨", "custom", "布林带下轨", params=(
            MetricParam("period", "周期", 20, 2, 250),
            MetricParam("deviation", "标准差", 2, 0.1, 6, 0.1),
        )),
        MetricDef("atr_custom", "自定义ATR", "custom", "自定义周期真实波幅", params=(
            MetricParam("period", "周期", 14, 2, 250),
        )),
        MetricDef("sandglass_score", "沙漏评分", "pattern", "沙漏形态综合评分", "分"),
        MetricDef("centipede_score", "蜈蚣图风险", "pattern", "无序震荡风险评分", "分"),
    ])
    return metrics


METRICS = {metric.key: metric for metric in metric_registry()}


@dataclass
class Condition:
    metric: str
    operator: str
    value: float | str | None = None
    value2: float | None = None
    compare_metric: str | None = None
    params: dict[str, float] = field(default_factory=dict)
    periods: int = 3
    weight: float = 1.0
    required: bool = True
    enabled: bool = True


@dataclass
class ConditionResult:
    passed: bool
    available: bool
    value: float | str | None
    target: float | str | None
    message: str


class MetricContext:
    def __init__(self, df: pd.DataFrame, snapshot: dict[str, Any] | None = None) -> None:
        self.df = df
        self.snapshot = snapshot or {}
        self._cache: dict[tuple[str, tuple[tuple[str, float], ...]], pd.Series] = {}

    def series(self, key: str, params: dict[str, float] | None = None) -> pd.Series:
        params = params or {}
        cache_key = (key, tuple(sorted((name, float(value)) for name, value in params.items())))
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = self._compute_series(key, params)
        self._cache[cache_key] = result
        return result

    def _compute_series(self, key: str, params: dict[str, float]) -> pd.Series:
        df = self.df
        if key in df.columns:
            return pd.to_numeric(df[key], errors="coerce")
        if key == "pct_chg":
            return df["close"].pct_change() * 100
        if key == "amplitude":
            return (df["high"] - df["low"]) / df["close"].shift(1) * 100
        if key == "volume_ratio_5":
            return df["volume"] / df["volume"].rolling(5, min_periods=1).mean()
        if key == "volume_ratio_10":
            return df["volume"] / df["volume"].rolling(10, min_periods=1).mean()
        if key == "obv_change_10":
            obv = self.series("obv")
            return obv.pct_change(10) * 100
        if key == "turnover_rate":
            value = self.snapshot.get("turnover_rate")
            return pd.Series([np.nan] * (len(df) - 1) + [value], index=df.index, dtype=float)
        if key in {"ma_custom", "ema_custom", "volume_ma_custom", "pct_chg_custom"}:
            period = max(1, int(params.get("period", 20)))
            if key == "ma_custom":
                return df["close"].rolling(period, min_periods=1).mean()
            if key == "ema_custom":
                return df["close"].ewm(span=period, adjust=False).mean()
            if key == "volume_ma_custom":
                return df["volume"].rolling(period, min_periods=1).mean()
            return df["close"].pct_change(period) * 100
        if key == "rsi_custom":
            period = max(2, int(params.get("period", 14)))
            diff = df["close"].diff()
            up = diff.clip(lower=0)
            down = (-diff).clip(lower=0)
            avg_up = up.ewm(alpha=1 / period, adjust=False).mean()
            avg_down = down.ewm(alpha=1 / period, adjust=False).mean()
            rs = avg_up / avg_down.replace(0, np.nan)
            return (100 - 100 / (1 + rs)).fillna(50)
        if key.startswith("kdj_") and key.endswith("_custom"):
            period = max(3, int(params.get("period", 9)))
            k_smooth = max(1, int(params.get("k_smooth", 3)))
            d_smooth = max(1, int(params.get("d_smooth", 3)))
            low = df["low"].rolling(period, min_periods=period).min()
            high = df["high"].rolling(period, min_periods=period).max()
            rsv = ((df["close"] - low) / (high - low).replace(0, np.nan) * 100).fillna(50)
            k = rsv.ewm(alpha=1 / k_smooth, adjust=False).mean()
            d = k.ewm(alpha=1 / d_smooth, adjust=False).mean()
            return {"kdj_k_custom": k, "kdj_d_custom": d, "kdj_j_custom": 3 * k - 2 * d}[key]
        if key.startswith("macd_") and key.endswith("_custom"):
            fast = max(2, int(params.get("fast", 12)))
            slow = max(fast + 1, int(params.get("slow", 26)))
            signal = max(2, int(params.get("signal", 9)))
            dif = (
                df["close"].ewm(span=fast, adjust=False).mean()
                - df["close"].ewm(span=slow, adjust=False).mean()
            )
            dea = dif.ewm(span=signal, adjust=False).mean()
            return {
                "macd_dif_custom": dif,
                "macd_dea_custom": dea,
                "macd_bar_custom": (dif - dea) * 2,
            }[key]
        if key == "cci_custom":
            period = max(2, int(params.get("period", 14)))
            typical = (df["high"] + df["low"] + df["close"]) / 3
            average = typical.rolling(period, min_periods=1).mean()
            deviation = typical.rolling(period, min_periods=1).apply(
                lambda values: np.mean(np.abs(values - values.mean())),
                raw=True,
            )
            return ((typical - average) / (0.015 * deviation.replace(0, np.nan))).fillna(0)
        if key.startswith("dmi_") and key.endswith("_custom"):
            period = max(2, int(params.get("period", 14)))
            adx_period = max(2, int(params.get("adx_period", 6)))
            high, low, close = df["high"], df["low"], df["close"]
            up_move = high.diff()
            down_move = -low.diff()
            plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move.fillna(0)
            minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move.fillna(0)
            true_range = pd.concat(
                [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
                axis=1,
            ).max(axis=1)
            atr = indicators._sma_wilder(true_range, period, 1)
            pdi = 100 * indicators._sma_wilder(plus_dm, period, 1) / atr.replace(0, np.nan)
            mdi = 100 * indicators._sma_wilder(minus_dm, period, 1) / atr.replace(0, np.nan)
            dx = (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan) * 100
            adx = indicators._sma_wilder(dx, adx_period, 1)
            adxr = (adx + adx.shift(adx_period)) / 2
            return {
                "dmi_pdi_custom": pdi.fillna(0),
                "dmi_mdi_custom": mdi.fillna(0),
                "dmi_adx_custom": adx.fillna(0),
                "dmi_adxr_custom": adxr.fillna(0),
            }[key]
        if key in {"dma_custom", "dma_ama_custom"}:
            short = max(2, int(params.get("short", 10)))
            long = max(short + 1, int(params.get("long", 50)))
            ama_period = max(2, int(params.get("ama", 10)))
            dma = (
                df["close"].rolling(short, min_periods=1).mean()
                - df["close"].rolling(long, min_periods=1).mean()
            )
            return dma if key == "dma_custom" else dma.rolling(ama_period, min_periods=1).mean()
        if key == "bbi_custom":
            periods = [
                max(2, int(params.get(name, default)))
                for name, default in (("p1", 3), ("p2", 6), ("p3", 12), ("p4", 24))
            ]
            return sum(
                (df["close"].rolling(value, min_periods=1).mean() for value in periods),
                start=pd.Series(0.0, index=df.index),
            ) / len(periods)
        if key.startswith("boll_") and key.endswith("_custom"):
            period = max(2, int(params.get("period", 20)))
            deviation = max(0.1, float(params.get("deviation", 2)))
            middle = df["close"].rolling(period, min_periods=1).mean()
            std = df["close"].rolling(period, min_periods=1).std(ddof=0)
            return {
                "boll_mid_custom": middle,
                "boll_up_custom": middle + deviation * std,
                "boll_dn_custom": middle - deviation * std,
            }[key]
        if key == "atr_custom":
            period = max(2, int(params.get("period", 14)))
            true_range = pd.concat(
                [
                    df["high"] - df["low"],
                    (df["high"] - df["close"].shift()).abs(),
                    (df["low"] - df["close"].shift()).abs(),
                ],
                axis=1,
            ).max(axis=1)
            return true_range.rolling(period, min_periods=1).mean()
        if key == "sandglass_score":
            value = float(sandglass_score(df).get("score", 0))
            return self._snapshot_series(value)
        if key == "centipede_score":
            value = float(centipede(df).get("score", 0))
            return self._snapshot_series(value)
        return pd.Series(np.nan, index=df.index, dtype=float)

    def _snapshot_series(self, value: float) -> pd.Series:
        return pd.Series([np.nan] * (len(self.df) - 1) + [value], index=self.df.index)


def evaluate_condition(context: MetricContext, condition: Condition) -> ConditionResult:
    metric = METRICS.get(condition.metric)
    if metric is None:
        return ConditionResult(False, False, None, None, f"未知指标 {condition.metric}")
    series = context.series(condition.metric, condition.params)
    current = _last_valid(series)
    if current is None:
        return ConditionResult(False, False, None, condition.value, f"{metric.label}暂无可用数据")

    target: float | str | None = condition.value
    compare_series: pd.Series | None = None
    if condition.compare_metric:
        compare_series = context.series(condition.compare_metric)
        target = _last_valid(compare_series)
        if target is None:
            return ConditionResult(False, False, current, None, "对比指标暂无可用数据")

    op = condition.operator
    passed = False
    try:
        if op == "eq":
            passed = str(current) == str(target)
        elif op == "neq":
            passed = str(current) != str(target)
        elif op == "gt":
            passed = float(current) > float(target)
        elif op == "gte":
            passed = float(current) >= float(target)
        elif op == "lt":
            passed = float(current) < float(target)
        elif op == "lte":
            passed = float(current) <= float(target)
        elif op == "between":
            passed = float(condition.value) <= float(current) <= float(condition.value2)
            target = f"{condition.value}~{condition.value2}"
        elif op in {"above_metric", "below_metric"}:
            passed = float(current) > float(target) if op == "above_metric" else float(current) < float(target)
        elif op in {"cross_above", "cross_below"}:
            left = pd.to_numeric(series, errors="coerce")
            right = (
                pd.to_numeric(compare_series, errors="coerce")
                if compare_series is not None
                else pd.Series(float(condition.value), index=left.index)
            )
            valid = pd.DataFrame({"left": left, "right": right}).dropna()
            if len(valid) >= 2:
                prev, now = valid.iloc[-2], valid.iloc[-1]
                passed = (
                    prev.left <= prev.right and now.left > now.right
                    if op == "cross_above"
                    else prev.left >= prev.right and now.left < now.right
                )
        elif op in {"rising", "falling"}:
            count = max(2, int(condition.periods))
            values = pd.to_numeric(series, errors="coerce").dropna().tail(count).to_numpy()
            if len(values) == count:
                delta = np.diff(values)
                passed = bool(np.all(delta > 0)) if op == "rising" else bool(np.all(delta < 0))
            target = f"连续{count}期"
    except (TypeError, ValueError, OverflowError):
        return ConditionResult(False, False, current, target, f"{metric.label}条件参数无效")

    target_label = _format_target(target)
    if condition.compare_metric and condition.compare_metric in METRICS:
        target_label = (
            f"{METRICS[condition.compare_metric].label}"
            f" ({_format_target(target)})"
        )
    symbol = "✓" if passed else "×"
    return ConditionResult(
        passed, True, _clean_value(current), _clean_value(target),
        f"{symbol} {metric.label} {_operator_label(op)} {target_label}",
    )


def _last_valid(series: pd.Series) -> Any | None:
    valid = series.dropna()
    return valid.iloc[-1] if not valid.empty else None


def _clean_value(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return float(value)
    return value


def _format_target(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def _operator_label(operator: str) -> str:
    return {
        "eq": "等于", "neq": "不等于", "gt": "大于", "gte": "不低于",
        "lt": "小于", "lte": "不高于", "between": "位于",
        "above_metric": "高于", "below_metric": "低于",
        "cross_above": "上穿", "cross_below": "下穿",
        "rising": "连续走高", "falling": "连续走低",
    }.get(operator, operator)
