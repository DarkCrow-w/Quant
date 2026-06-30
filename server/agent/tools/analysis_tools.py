"""技术分析辅助 LangChain Tools。"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
from langchain_core.tools import tool

from server.services.market_service import get_kline
from server.agent.tools.market_tools import (
    _effective_end_date,
    _resolve_symbol,
)


def _compute_ma(closes: list[float], period: int) -> list[float | None]:
    """计算简单移动平均线。"""
    s = pd.Series(closes)
    ma = s.rolling(window=period).mean()
    return [round(v, 4) if not np.isnan(v) else None for v in ma]


def _compute_kdj(highs, lows, closes, period=9):
    """计算 KDJ 指标。"""
    h = pd.Series(highs)
    l_ = pd.Series(lows)
    c = pd.Series(closes)

    low_min = l_.rolling(window=period).min()
    high_max = h.rolling(window=period).max()
    rsv = (c - low_min) / (high_max - low_min) * 100
    rsv = rsv.fillna(50)

    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    return k.tolist(), d.tolist(), j.tolist()


@tool
def analyze_technicals_tool(
    symbol: str,
    start_date: str,
    end_date: str,
    use_latest: bool = True,
    lookback: int | None = None,
) -> str:
    """计算股票的技术指标：MA5/MA10/MA20/MA60、KDJ、BBI、成交量均线。

    Args:
        symbol: 股票代码或中文名称
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        use_latest: 默认 true，使用本地最新交易日；历史截面分析时设为 false
        lookback: 可选，返回最近 N 个交易日的指标。用户说“最近20个交易日/最近60根K线”时必须设置。

    Returns:
        技术指标摘要的 JSON 字符串（默认最近 10 个交易日，可由 lookback 调整）
    """
    code, resolved = _resolve_symbol(symbol)
    effective_end = _effective_end_date(code, end_date, use_latest)
    bars = get_kline(code, start_date, effective_end)
    if not bars:
        return json.dumps({"error": f"未找到 {code} 的数据"}, ensure_ascii=False)
    if lookback is not None:
        lookback = max(1, min(int(lookback), 120))

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    volumes = [b.volume for b in bars]

    ma5 = _compute_ma(closes, 5)
    ma10 = _compute_ma(closes, 10)
    ma20 = _compute_ma(closes, 20)
    ma60 = _compute_ma(closes, 60)

    # BBI = (MA3 + MA6 + MA12 + MA24) / 4
    ma3 = pd.Series(closes).rolling(3).mean()
    ma6 = pd.Series(closes).rolling(6).mean()
    ma12 = pd.Series(closes).rolling(12).mean()
    ma24 = pd.Series(closes).rolling(24).mean()
    bbi = ((ma3 + ma6 + ma12 + ma24) / 4).tolist()

    k_vals, d_vals, j_vals = _compute_kdj(highs, lows, closes)
    vol_ma5 = _compute_ma(volumes, 5)

    # 返回用户指定窗口；未指定时控制在最近 10 天，避免工具结果过长。
    n = min(lookback or 10, len(bars))
    recent = []
    for i in range(-n, 0):
        idx = len(bars) + i
        recent.append({
            "dt": bars[idx].dt,
            "close": closes[idx],
            "ma5": ma5[idx],
            "ma10": ma10[idx],
            "ma20": ma20[idx],
            "ma60": ma60[idx],
            "bbi": round(bbi[idx], 4) if not np.isnan(bbi[idx]) else None,
            "kdj_k": round(k_vals[idx], 2),
            "kdj_d": round(d_vals[idx], 2),
            "kdj_j": round(j_vals[idx], 2),
            "volume": volumes[idx],
            "vol_ma5": vol_ma5[idx],
        })

    # 趋势判断
    latest_close = closes[-1]
    trend_signals = []
    if ma5[-1] and ma20[-1]:
        if ma5[-1] > ma20[-1]:
            trend_signals.append("短期均线在长期均线上方(多头排列)")
        else:
            trend_signals.append("短期均线在长期均线下方(空头排列)")

    if j_vals[-1] < 20:
        trend_signals.append("KDJ J值处于超卖区域")
    elif j_vals[-1] > 80:
        trend_signals.append("KDJ J值处于超买区域")

    if not np.isnan(bbi[-1]) and latest_close > bbi[-1]:
        trend_signals.append("收盘价在BBI上方(偏多)")
    elif not np.isnan(bbi[-1]):
        trend_signals.append("收盘价在BBI下方(偏空)")

    output = {
        "symbol": code,
        "name": resolved.get("name", ""),
        "total_bars": len(bars),
        "returned_bars": n,
        "lookback": lookback,
        "requested_date_range": f"{start_date} ~ {end_date}",
        "effective_date_range": f"{start_date} ~ {effective_end}",
        "data_as_of": bars[-1].dt,
        "use_latest": use_latest,
        "recent_indicators": recent,
        "trend_signals": trend_signals,
    }
    return json.dumps(output, ensure_ascii=False, default=str)
