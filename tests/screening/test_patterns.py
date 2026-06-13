"""高级形态移植单元测试（patterns.py）。

策略：
- 直接从 ``quant.screening.patterns`` 导入。
- 构造已知形态（持续上涨→拉升；窄幅高波动→蜈蚣；缩量低位→沙漏高分），
  断言方向正确。
- 真实数据冒烟：取首只 symbol（with_indicators=True），断言每个函数跑通不抛异常
  且返回文档约定的字段键。
"""

from __future__ import annotations

import importlib

import numpy as np
import pandas as pd
import pytest

patterns = importlib.import_module("quant.screening.patterns")
slope = patterns.slope
sandglass_score = patterns.sandglass_score
centipede = patterns.centipede
three_waves = patterns.three_waves
kirin_stage = patterns.kirin_stage


# ──────────────────────────────────────────────────────────────────────────
# 合成 K 线
# ──────────────────────────────────────────────────────────────────────────
def _ohlcv(closes, vols, opens=None, highs=None, lows=None) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    dates = pd.date_range("2024-01-01", periods=n, freq="D").date
    prev = np.concatenate([[closes[0]], closes[:-1]])
    if opens is None:
        opens = prev
    opens = np.asarray(opens, dtype=float)
    if highs is None:
        highs = np.maximum(opens, closes) * 1.005
    if lows is None:
        lows = np.minimum(opens, closes) * 0.995
    vols = np.asarray(vols, dtype=float)
    return pd.DataFrame(
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


# ──────────────────────────────────────────────────────────────────────────
# helper 函数
# ──────────────────────────────────────────────────────────────────────────
def test_slope_matches_formula():
    # 完美线性序列 y = 2x + 5 → 斜率应为 2
    y = 2.0 * np.arange(20) + 5.0
    assert slope(y, 20) == pytest.approx(2.0)
    # 不足 2 个点返回 0
    assert slope(np.array([1.0]), 5) == 0.0
    # period 截断为 len
    assert slope(y, 100) == pytest.approx(2.0)


# ──────────────────────────────────────────────────────────────────────────
# 沙漏：缩量低位 → 高分
# ──────────────────────────────────────────────────────────────────────────
def test_sandglass_low_vol_low_position_high_score():
    rng = np.random.default_rng(0)
    n = 60
    # 前期下跌到低位后窄幅企稳贴近支撑，量能逐步缩小
    down = 20.0 * np.cumprod(1.0 + rng.normal(-0.01, 0.003, 30))
    base = down[-1]
    flat = base * (1.0 + rng.normal(0.0, 0.004, 30))
    closes = np.concatenate([down, flat])
    # 量能：前期大且稳定，近期单调缩量收敛（量幅收窄）
    vols = np.concatenate(
        [
            np.full(40, 2_000_000.0),
            np.linspace(700_000, 200_000, 20),
        ]
    )
    df = _ohlcv(closes, vols)
    res = sandglass_score(df)
    assert set(res) == {"score", "rating", "factors", "is_perfect"}
    assert isinstance(res["score"], int)
    assert set(res["factors"]) == {"缩量收敛", "枢轴邻近", "量能斜率", "均线结构", "事件风险"}
    # 缩量收敛 + 贴近低位枢轴应拿到明显分数
    assert res["factors"]["缩量收敛"] >= 8
    assert res["score"] >= 40

    # 对照：放量高波动应明显更低分
    rng2 = np.random.default_rng(7)
    closes2 = 20.0 * np.cumprod(1.0 + rng2.normal(0.0, 0.04, 60))
    vols2 = 1_000_000 * (1.0 + rng2.normal(0.0, 0.8, 60)).clip(0.1, None)
    df2 = _ohlcv(closes2, vols2)
    assert sandglass_score(df2)["score"] <= res["score"] + 5


def test_sandglass_short_data():
    df = _ohlcv(np.linspace(10, 11, 10), np.full(10, 1e6))
    res = sandglass_score(df)
    assert res["score"] == 0
    assert res["rating"] == "极差"


# ──────────────────────────────────────────────────────────────────────────
# 蜈蚣：窄幅高波动 + 影线交替 + 量能无规律 → is_centipede
# ──────────────────────────────────────────────────────────────────────────
def test_centipede_narrow_high_volatility():
    rng = np.random.default_rng(3)
    n = 30
    # 窄幅震荡（总涨跌幅极小）但日波动大
    closes = 10.0 + rng.normal(0.0, 0.4, n)
    closes[0] = 10.0
    closes[-1] = 10.02
    opens = 10.0 + rng.normal(0.0, 0.4, n)
    # 制造长上下影线交替
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0.6, 0.2, n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.6, 0.2, n))
    # 量能无规律（高 CV）
    vols = rng.uniform(2e5, 4e6, n)
    df = _ohlcv(closes, vols, opens=opens, highs=highs, lows=lows)
    res = centipede(df)
    assert set(res) == {"is_centipede", "score", "factors"}
    assert isinstance(res["score"], int)
    assert res["factors"]["量能无规律"] >= 10
    assert res["factors"]["价格无趋势"] >= 10

    # 对照：平滑稳步上涨不应判为蜈蚣
    up = _ohlcv(10.0 * np.cumprod(np.full(40, 1.01)), np.full(40, 1e6))
    assert centipede(up)["is_centipede"] is False


def test_centipede_short_data():
    df = _ohlcv(np.linspace(10, 11, 15), np.full(15, 1e6))
    res = centipede(df)
    assert res == {"is_centipede": False, "score": 0, "factors": {}}


# ──────────────────────────────────────────────────────────────────────────
# 三波：温和稳步上涨 25-50% → 建仓波（可干）
# ──────────────────────────────────────────────────────────────────────────
def test_three_waves_build_phase():
    # 先一段横盘形成低点，再温和上涨约 35%，无涨停、阳线为主
    n_flat = 15
    n_up = 45
    flat = np.full(n_flat, 10.0)
    up = 10.0 * np.cumprod(np.full(n_up, 1.0067))  # ~35% over 45 天
    closes = np.concatenate([flat, up])
    # opens 略低于 close 制造阳线占比 > 60%
    opens = closes / 1.004
    df = _ohlcv(closes, np.full(len(closes), 1e6), opens=opens)
    res = three_waves(df)
    assert set(res) == {"wave", "confidence", "stats", "bd_suggestion"}
    assert res["wave"] == "建仓波"
    assert res["bd_suggestion"] == "可干"
    assert 0.0 <= res["confidence"] <= 1.0


def test_three_waves_pull_phase():
    # 快速拉升 + 频繁涨停 → 拉升波或冲刺波（方向：非建仓、不可干）
    n_flat = 10
    flat = np.full(n_flat, 10.0)
    up = 10.0 * np.cumprod(np.full(20, 1.05))  # 快速翻倍级别
    closes = np.concatenate([flat, up])
    opens = closes / 1.04  # 大阳线
    df = _ohlcv(closes, np.full(len(closes), 2e6), opens=opens)
    res = three_waves(df)
    assert res["wave"] in ("拉升波", "冲刺波")
    assert res["bd_suggestion"] in ("等回调", "不看")


def test_three_waves_short_data():
    df = _ohlcv(np.linspace(10, 11, 25), np.full(25, 1e6))
    assert three_waves(df)["wave"] == "未知"


# ──────────────────────────────────────────────────────────────────────────
# 麒麟：缩量低位 N 型 → 吸筹方向；快速拉升 → 拉升方向
# ──────────────────────────────────────────────────────────────────────────
def test_kirin_stage_keys_and_lasheng():
    n_flat = 30
    flat = 10.0 + np.sin(np.linspace(0, 4 * np.pi, n_flat)) * 0.1
    up = 10.0 * np.cumprod(np.full(40, 1.03))  # 快速拉升
    closes = np.concatenate([flat, up])
    opens = closes / 1.02
    vols = np.concatenate([np.full(n_flat, 8e5), np.full(40, 2.5e6)])
    df = _ohlcv(closes, vols, opens=opens)
    res = kirin_stage(df)
    assert set(res) == {
        "stage",
        "confidence",
        "sub_type",
        "scores",
        "indicators",
        "operation",
    }
    assert set(res["scores"]) == {"xishou", "lasheng", "paifa", "luoluo"}
    assert res["stage"] in ("拉升", "派发")  # 高位快速上涨方向
    assert res["indicators"]["price_position"] in ("低位", "中位", "高位")


def test_kirin_stage_short_data():
    df = _ohlcv(np.linspace(10, 11, 50), np.full(50, 1e6))
    res = kirin_stage(df)
    assert res["stage"] == "未知"
    assert res["operation"] == "观望"


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

    sg = sandglass_score(df)
    assert set(sg) == {"score", "rating", "factors", "is_perfect"}

    cp = centipede(df)
    assert set(cp) == {"is_centipede", "score", "factors"}

    tw = three_waves(df)
    assert set(tw) == {"wave", "confidence", "stats", "bd_suggestion"}

    ks = kirin_stage(df)
    assert set(ks) == {
        "stage",
        "confidence",
        "sub_type",
        "scores",
        "indicators",
        "operation",
    }
