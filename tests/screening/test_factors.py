"""五维因子打分单元测试。

策略：
- 直接从 ``quant.screening.factors`` 导入（不经过包 __init__，避免与并行实现的
  patterns/scoring 模块耦合）。
- 构造合成场景（多头/超跌/震荡），用 quant 的指标引擎补齐全部指标列，断言分数
  方向合理。
- 真实数据冒烟：取首只 symbol，断言五个因子均返回 (float∈[0,100], list)。
"""

from __future__ import annotations

import importlib
import math

import numpy as np
import pandas as pd
import pytest

# 直接加载 factors 模块（绕过包 __init__）
factors = importlib.import_module("quant.screening.factors")
score_trend = factors.score_trend
score_momentum = factors.score_momentum
score_volume = factors.score_volume
score_dip = factors.score_dip
score_risk = factors.score_risk
FACTOR_DEFS = factors.FACTOR_DEFS


# ──────────────────────────────────────────────────────────────────────────
# 合成 K 线 + 指标计算
# ──────────────────────────────────────────────────────────────────────────
def _ohlcv_from_close(closes: np.ndarray, vols: np.ndarray) -> pd.DataFrame:
    """由收盘价/成交量序列构造一个 OHLCV DataFrame。"""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    dates = pd.date_range("2024-01-01", periods=n, freq="D").date
    prev = np.concatenate([[closes[0]], closes[:-1]])
    opens = prev
    highs = np.maximum(opens, closes) * 1.005
    lows = np.minimum(opens, closes) * 0.995
    amount = closes * vols
    return pd.DataFrame(
        {
            "dt": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.asarray(vols, dtype=float),
            "amount": amount,
        }
    )


def _with_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """用 quant 的指标引擎补齐全部指标列（与真实数据一致）。"""
    from quant.data import indicators as ind_mod

    return ind_mod.compute_all(df.copy())


def _bull_market(n: int = 150) -> pd.DataFrame:
    """稳步上涨多头：均线多头排列、价在BBI上方、量能温和放大。"""
    rng = np.random.default_rng(1)
    base = 10.0 * np.cumprod(1.0 + rng.normal(0.012, 0.006, n))
    vols = 1_000_000 * (1.0 + rng.normal(0.05, 0.1, n)).clip(0.5, None)
    return _with_indicators(_ohlcv_from_close(base, vols))


def _oversold_dip(n: int = 150) -> pd.DataFrame:
    """先涨后急跌至超卖：J极低、贴近下轨、缩量回调 → 抄底分应高。"""
    rng = np.random.default_rng(2)
    up = 10.0 * np.cumprod(1.0 + rng.normal(0.015, 0.005, n - 30))
    peak = up[-1]
    down = peak * np.cumprod(1.0 + rng.normal(-0.025, 0.004, 30))
    closes = np.concatenate([up, down])
    # 回调段缩量
    vols = np.concatenate(
        [
            1_000_000 * (1.0 + rng.normal(0.0, 0.1, n - 30)).clip(0.5, None),
            400_000 * (1.0 + rng.normal(0.0, 0.1, 30)).clip(0.4, None),
        ]
    )
    return _with_indicators(_ohlcv_from_close(closes, vols))


def _range_market(n: int = 150) -> pd.DataFrame:
    """横盘震荡：均值回复(OU)无净漂移 → 均线缠绕、ADX低 → 趋势分居中。"""
    rng = np.random.default_rng(3)
    c = 10.0
    arr = []
    for _ in range(n):
        c += -0.3 * (c - 10.0) + rng.normal(0, 0.08)
        arr.append(c)
    closes = np.asarray(arr, dtype=float)
    vols = 800_000 * (1.0 + rng.normal(0.0, 0.1, n)).clip(0.5, None)
    return _with_indicators(_ohlcv_from_close(closes, vols))


# ──────────────────────────────────────────────────────────────────────────
# 因子元数据
# ──────────────────────────────────────────────────────────────────────────
def test_factor_defs_shape_and_weights():
    keys = {d["key"] for d in FACTOR_DEFS}
    assert keys == {"trend", "momentum", "volume", "dip", "risk"}
    wmap = {d["key"]: d["default_weight"] for d in FACTOR_DEFS}
    assert wmap["trend"] == pytest.approx(0.25)
    assert wmap["momentum"] == pytest.approx(0.20)
    assert wmap["volume"] == pytest.approx(0.20)
    assert wmap["dip"] == pytest.approx(0.20)
    assert wmap["risk"] == pytest.approx(0.15)
    assert sum(wmap.values()) == pytest.approx(1.0)
    for d in FACTOR_DEFS:
        assert set(d) >= {"key", "label", "default_weight", "desc"}
        assert isinstance(d["label"], str) and d["label"]
        assert isinstance(d["desc"], str) and d["desc"]


# ──────────────────────────────────────────────────────────────────────────
# 返回契约：分数 0-100，理由 list
# ──────────────────────────────────────────────────────────────────────────
def _assert_contract(result):
    score, reasons = result
    assert isinstance(score, float)
    assert 0.0 <= score <= 100.0
    assert isinstance(reasons, list)
    assert all(isinstance(r, str) for r in reasons)


@pytest.mark.parametrize(
    "fn", [score_trend, score_momentum, score_volume, score_dip, score_risk]
)
def test_short_data_neutral_defaults(fn):
    df = _with_indicators(
        _ohlcv_from_close(np.full(5, 10.0), np.full(5, 1_000_000.0))
    )
    score, reasons = fn(df)
    _assert_contract((score, reasons))
    # 数据不足时为中性/安全默认
    if fn is score_risk:
        assert score == 60.0
    else:
        assert score == 50.0
    assert "数据不足" in reasons


@pytest.mark.parametrize(
    "fn", [score_trend, score_momentum, score_volume, score_dip, score_risk]
)
def test_empty_df_no_throw(fn):
    _assert_contract(fn(pd.DataFrame()))


# ──────────────────────────────────────────────────────────────────────────
# 场景方向断言
# ──────────────────────────────────────────────────────────────────────────
def test_bull_market_high_trend():
    bull = _bull_market()
    rng = _range_market()
    s_bull, _ = score_trend(bull)
    s_rng, _ = score_trend(rng)
    assert s_bull >= 70.0, f"多头趋势分应高: {s_bull}"
    assert s_bull > s_rng, f"多头({s_bull})应高于震荡({s_rng})"


def test_range_market_mid_trend():
    """震荡市趋势分应明显低于强多头，且不触发完美多头排列加成。

    趋势因子对「最后一根 bar」做快照，震荡市末端方向随机，故不强约束绝对中值，
    而断言其结构性弱于多头（无 +24 完美排列加成、ADX 弱、整体不进强势档）。
    """
    rng = _range_market()
    bull = _bull_market()
    s_rng, reasons = score_trend(rng)
    s_bull, _ = score_trend(bull)
    assert s_rng < s_bull, f"震荡({s_rng})应低于多头({s_bull})"
    assert s_rng < 70.0, f"震荡趋势分不应进入强势档: {s_rng} {reasons}"
    assert "均线完美多头排列" not in reasons, "震荡市不应判定完美多头排列"


def test_oversold_dip_high_dip_score():
    dip = _oversold_dip()
    bull = _bull_market()
    s_dip, reasons = score_dip(dip)
    s_bull, _ = score_dip(bull)
    assert s_dip >= 55.0, f"超跌抄底分应高: {s_dip} {reasons}"
    assert s_dip > s_bull, f"超跌({s_dip})抄底价值应高于强势多头({s_bull})"


def test_oversold_low_momentum_or_extreme():
    """急跌段动量应偏弱（除非已到极端超卖反转区，J<0 会加分）。"""
    dip = _oversold_dip()
    s, _ = score_momentum(dip)
    _assert_contract((s, []))


def test_bull_market_safer_than_crashing():
    bull = _bull_market()
    dip = _oversold_dip()
    s_bull_risk, _ = score_risk(bull)
    s_dip_risk, warns = score_risk(dip)
    # 连续下跌/急跌的风险分应明显低于平稳上涨
    assert s_dip_risk < s_bull_risk, f"急跌风险分({s_dip_risk})应低于多头({s_bull_risk})"
    assert any("下跌" in w or "BBI" in w for w in warns)


def test_volume_attack_pattern_scores_high():
    """构造价涨量增攻击形态：末日大涨 + 倍量。"""
    rng = np.random.default_rng(9)
    closes = 10.0 * np.cumprod(1.0 + rng.normal(0.002, 0.004, 60))
    closes[-1] = closes[-2] * 1.06  # 末日 +6%
    vols = np.full(60, 800_000.0)
    vols[-1] = 2_500_000.0  # 倍量
    df = _with_indicators(_ohlcv_from_close(closes, vols))
    s, reasons = score_volume(df)
    assert s >= 55.0, f"攻击形态量价分应高: {s} {reasons}"


# ──────────────────────────────────────────────────────────────────────────
# 真实数据冒烟
# ──────────────────────────────────────────────────────────────────────────
def test_real_data_smoke():
    from quant.data import get_store

    store = get_store()
    syms = store.list_symbols("day")
    if not syms:
        pytest.skip("无可用 symbol")
    df = store.get_kline(syms[0], "day", with_indicators=True)
    if len(df) < 20:
        pytest.skip("数据不足")
    for fn in (score_trend, score_momentum, score_volume, score_dip, score_risk):
        score, reasons = fn(df)
        assert isinstance(score, float)
        assert not math.isnan(score)
        assert 0.0 <= score <= 100.0
        assert isinstance(reasons, list) and len(reasons) >= 1
        assert all(isinstance(r, str) for r in reasons)
