"""多因子评分引擎单元测试。

策略：
- 直接从 ``quant.screening.scoring`` 导入（也验证包 __init__ 导出齐全）。
- 构造合成 K 线（用 quant 指标引擎补齐全部指标列），断言：
  * 完美低吸图形（超跌缩量贴下轨）综合分高于高位冲刺波图形；
  * 权重全压 trend 时，强多头票排在震荡票之前；
  * exclude_centipede 命中蜈蚣图时 passed_filter=False；
- 真实数据冒烟：3 只 symbol 跑通、字段完整、不抛异常。
"""

from __future__ import annotations

import importlib

import numpy as np
import pandas as pd
import pytest

# 经包 __init__ 导入（顺带验证导出）
import quant.screening as screening
from quant.screening import (
    MultiFactorScorer,
    ScoreConfig,
    StockScore,
    FACTOR_DEFS,
    default_config,
    rating_for,
    RATING_TIERS,
)

ind_mod = importlib.import_module("quant.data.indicators")


# ──────────────────────────────────────────────────────────────────────────
# 合成 K 线 + 指标计算
# ──────────────────────────────────────────────────────────────────────────
def _ohlcv(closes, vols, opens=None, highs=None, lows=None) -> pd.DataFrame:
    """由序列构造 OHLCV 并补齐指标列。"""
    closes = np.asarray(closes, dtype=float)
    vols = np.asarray(vols, dtype=float)
    n = len(closes)
    dates = pd.date_range("2023-01-01", periods=n, freq="D").date
    prev = np.concatenate([[closes[0]], closes[:-1]])
    if opens is None:
        opens = prev
    opens = np.asarray(opens, dtype=float)
    if highs is None:
        highs = np.maximum(opens, closes) * 1.005
    if lows is None:
        lows = np.minimum(opens, closes) * 0.995
    df = pd.DataFrame(
        {
            "dt": dates,
            "open": opens,
            "high": np.asarray(highs, dtype=float),
            "low": np.asarray(lows, dtype=float),
            "close": closes,
            "volume": vols,
            "amount": closes * vols,
        }
    )
    return ind_mod.compute_all(df)


def _perfect_dip(n: int = 200) -> pd.DataFrame:
    """完美低吸：温和上涨后缩量回调至超卖低位（J极低、贴下轨、缩量）。"""
    rng = np.random.default_rng(11)
    up = 10.0 * np.cumprod(1.0 + rng.normal(0.010, 0.004, n - 25))
    peak = up[-1]
    down = peak * np.cumprod(1.0 + rng.normal(-0.020, 0.003, 25))
    closes = np.concatenate([up, down])
    vols = np.concatenate(
        [
            1_000_000 * (1.0 + rng.normal(0.0, 0.08, n - 25)).clip(0.6, None),
            350_000 * (1.0 + rng.normal(0.0, 0.06, 25)).clip(0.4, None),
        ]
    )
    return _ohlcv(closes, vols)


def _sprint_top(n: int = 200) -> pd.DataFrame:
    """高位冲刺波：低位起爆，多个涨停，翻倍以上 → 三波判冲刺波。"""
    rng = np.random.default_rng(22)
    base = 10.0 * np.cumprod(1.0 + rng.normal(0.0, 0.004, n - 30))
    start = base[-1]
    # 30 日急拉：日均约 +7%，穿插涨停
    daily = rng.normal(0.07, 0.02, 30)
    daily[[3, 7, 11, 16, 21]] = 0.10  # 涨停
    sprint = start * np.cumprod(1.0 + daily)
    closes = np.concatenate([base, sprint])
    vols = np.concatenate(
        [
            800_000 * (1.0 + rng.normal(0.0, 0.08, n - 30)).clip(0.6, None),
            3_000_000 * (1.0 + rng.normal(0.0, 0.1, 30)).clip(0.6, None),
        ]
    )
    return _ohlcv(closes, vols)


def _strong_bull(n: int = 200) -> pd.DataFrame:
    """稳步强多头：均线多头排列、ADX 强。"""
    rng = np.random.default_rng(33)
    closes = 10.0 * np.cumprod(1.0 + rng.normal(0.013, 0.005, n))
    vols = 1_000_000 * (1.0 + rng.normal(0.04, 0.08, n)).clip(0.6, None)
    return _ohlcv(closes, vols)


def _range_market(n: int = 200) -> pd.DataFrame:
    """横盘震荡：均值回复无净漂移 → 均线缠绕、趋势弱。"""
    rng = np.random.default_rng(44)
    c = 10.0
    arr = []
    for _ in range(n):
        c += -0.3 * (c - 10.0) + rng.normal(0, 0.08)
        arr.append(c)
    closes = np.asarray(arr, dtype=float)
    vols = 800_000 * (1.0 + rng.normal(0.0, 0.08, n)).clip(0.6, None)
    return _ohlcv(closes, vols)


def _centipede(n: int = 60) -> pd.DataFrame:
    """蜈蚣图：近 20 根窄幅震荡 + 长上下影 + 量能无规律。"""
    rng = np.random.default_rng(55)
    base = np.full(n, 10.0)
    closes = base * (1.0 + rng.normal(0.0, 0.003, n))  # 几乎不漂移
    opens = closes * (1.0 + rng.normal(0.0, 0.005, n))
    # 长上下影：high/low 大幅偏离实体
    highs = np.maximum(opens, closes) * (1.0 + np.abs(rng.normal(0.03, 0.01, n)))
    lows = np.minimum(opens, closes) * (1.0 - np.abs(rng.normal(0.03, 0.01, n)))
    # 量能无规律（CV 大）
    vols = np.abs(rng.normal(1_000_000, 900_000, n)).clip(50_000, None)
    return _ohlcv(closes, vols, opens=opens, highs=highs, lows=lows)


# ──────────────────────────────────────────────────────────────────────────
# 基础契约
# ──────────────────────────────────────────────────────────────────────────
def test_exports_present():
    for name in (
        "MultiFactorScorer",
        "ScoreConfig",
        "StockScore",
        "FACTOR_DEFS",
        "default_config",
        "rating_for",
    ):
        assert hasattr(screening, name), f"包未导出 {name}"
    # patterns/factors 主函数也应导出
    for name in ("slope", "sandglass_score", "three_waves", "kirin_stage", "score_trend"):
        assert hasattr(screening, name)


def test_rating_tiers_and_rating_for():
    assert rating_for(95) == RATING_TIERS[0][1]
    assert rating_for(70) == "★★★★☆ 推荐"
    assert rating_for(55) == "★★★☆☆ 可关注"
    assert rating_for(40) == "★★☆☆☆ 谨慎"
    assert rating_for(10) == "★☆☆☆☆ 不推荐"
    assert rating_for(0) == RATING_TIERS[-1][1]


def test_default_config():
    cfg = default_config()
    assert isinstance(cfg, ScoreConfig)
    assert cfg.exclude_centipede is True
    assert cfg.use_patterns is True
    keys = {d["key"] for d in FACTOR_DEFS}
    assert set(cfg.weights) == keys


def _assert_score_obj(s: StockScore):
    assert isinstance(s, StockScore)
    assert 0.0 <= s.score <= 100.0
    assert isinstance(s.rating, str) and s.rating
    assert set(s.factors) == {"trend", "momentum", "volume", "dip", "risk"}
    for v in s.factors.values():
        assert 0.0 <= v <= 100.0
    assert isinstance(s.reasons, list) and s.reasons
    assert isinstance(s.warnings, list) and s.warnings
    assert isinstance(s.signal_date, str) and s.signal_date
    assert isinstance(s.passed_filter, bool)


def test_short_data_no_throw():
    df = _ohlcv(np.full(5, 10.0), np.full(5, 1_000_000.0))
    s = MultiFactorScorer().score("TEST", df)
    _assert_score_obj(s)


# ──────────────────────────────────────────────────────────────────────────
# 核心断言：完美低吸 vs 高位冲刺
# ──────────────────────────────────────────────────────────────────────────
def test_perfect_dip_beats_sprint_top():
    scorer = MultiFactorScorer(default_config())
    dip = scorer.score("DIP", _perfect_dip())
    sprint = scorer.score("SPRINT", _sprint_top())
    _assert_score_obj(dip)
    _assert_score_obj(sprint)

    # 冲刺波形态应被识别并触发 ×0.7 惩罚
    assert sprint.wave == "冲刺波", f"应判冲刺波: wave={sprint.wave}"
    # 完美低吸综合分应高于高位冲刺
    assert dip.score > sprint.score, f"低吸({dip.score})应高于冲刺({sprint.score})"
    # 低吸抄底因子应明显高
    assert dip.factors["dip"] >= 55.0, f"低吸抄底分应高: {dip.factors['dip']}"


# ──────────────────────────────────────────────────────────────────────────
# 权重全压 trend：趋势票排第一
# ──────────────────────────────────────────────────────────────────────────
def test_trend_only_weight_ranks_trend_stock_first():
    cfg = ScoreConfig(
        weights={"trend": 1.0, "momentum": 0.0, "volume": 0.0, "dip": 0.0, "risk": 0.0},
        use_patterns=False,  # 排除形态调整干扰，纯看趋势因子
    )
    scorer = MultiFactorScorer(cfg)
    bull = scorer.score("BULL", _strong_bull())
    rng = scorer.score("RANGE", _range_market())
    assert bull.score > rng.score, f"强多头({bull.score})应高于震荡({rng.score})"
    # 综合分应等于 trend 因子分（权重全压 trend，无形态调整）
    assert bull.score == pytest.approx(bull.factors["trend"], abs=0.11)


# ──────────────────────────────────────────────────────────────────────────
# 蜈蚣过滤生效
# ──────────────────────────────────────────────────────────────────────────
def test_centipede_filter_sets_passed_false():
    cp_df = _centipede()
    # 先确认形态模块确实判定为蜈蚣图
    cp = screening.centipede(cp_df)
    assert cp["is_centipede"], f"测试构造未命中蜈蚣图: {cp}"

    on = MultiFactorScorer(ScoreConfig(exclude_centipede=True)).score("CP", cp_df)
    off = MultiFactorScorer(ScoreConfig(exclude_centipede=False)).score("CP", cp_df)
    assert on.passed_filter is False, "exclude_centipede=True 时蜈蚣图应过滤"
    # 关闭蜈蚣过滤（且无其它过滤项）时应通过
    assert off.passed_filter is True, "exclude_centipede=False 时不应因蜈蚣被过滤"


def test_hard_filters_amount_and_price():
    df = _strong_bull()
    base = MultiFactorScorer(ScoreConfig(exclude_centipede=False)).score("X", df)
    # 设极高 min_amount → 必不通过
    s_amt = MultiFactorScorer(
        ScoreConfig(exclude_centipede=False, min_amount=1e18)
    ).score("X", df)
    s_price = MultiFactorScorer(
        ScoreConfig(exclude_centipede=False, min_price=1e9)
    ).score("X", df)
    assert s_amt.passed_filter is False
    assert s_price.passed_filter is False
    # 过滤只影响标记，不影响分数
    assert s_amt.score == base.score


# ──────────────────────────────────────────────────────────────────────────
# 真实数据冒烟（3 只）
# ──────────────────────────────────────────────────────────────────────────
def test_real_data_smoke_three_symbols():
    from quant.data import get_store

    store = get_store()
    syms = store.list_symbols("day")
    if not syms:
        pytest.skip("无可用 symbol")

    scorer = MultiFactorScorer()
    checked = 0
    for sym in syms[:3]:
        df = store.get_kline(sym, "day", with_indicators=True)
        if len(df) < 60:
            continue
        s = scorer.score(sym, df)
        _assert_score_obj(s)
        assert s.symbol == sym
        assert s.signal_date == str(df.iloc[-1]["dt"])
        assert s.wave in ("建仓波", "拉升波", "冲刺波", "未知")
        assert s.kirin in ("吸筹", "拉升", "派发", "回落", "未知")
        checked += 1
    if checked == 0:
        pytest.skip("真实数据均不足 60 根")
