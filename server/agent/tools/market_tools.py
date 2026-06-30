"""行情数据相关 LangChain Tools — 包装 market_service 和 updater 函数。"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from langchain_core.tools import tool

from quant.data import get_store
from quant.data.symbols import normalize
from quant.data.updater import fetch_all_a_symbols, list_cached_symbols
from server.services.market_service import get_kline


def resolve_stock_symbol(query: str) -> dict:
    raw = str(query or "").strip()
    code_match = re.search(r"(?<!\d)(\d{6})(?:\.(?:SH|SZ|BJ))?(?!\d)", raw, re.I)
    if code_match:
        code = normalize(code_match.group(1))
        universe = get_store().get_universe()
        match = universe[universe["symbol"].astype(str) == code]
        name = str(match.iloc[0].get("name", "")) if not match.empty else ""
        market = str(match.iloc[0].get("market", "")) if not match.empty else ""
        return {"symbol": code, "name": name, "market": market, "matched_by": "code"}

    universe = get_store().get_universe()
    if universe.empty or "name" not in universe.columns:
        return {"error": f"无法识别股票：{raw}"}

    names = universe["name"].fillna("").astype(str).str.strip()
    exact = universe[names == raw]
    matches = exact if not exact.empty else universe[names.str.contains(raw, regex=False)]
    if matches.empty:
        return {"error": f"未找到股票名称：{raw}"}

    candidates = [
        {
            "symbol": str(row.symbol),
            "name": str(row.name),
            "market": str(row.market),
        }
        for row in matches.head(8).itertuples(index=False)
    ]
    return {
        **candidates[0],
        "matched_by": "exact_name" if not exact.empty else "fuzzy_name",
        "candidates": candidates,
    }


def _resolve_symbol(query: str) -> tuple[str, dict]:
    resolved = resolve_stock_symbol(query)
    if "error" in resolved:
        raise ValueError(resolved["error"])
    return str(resolved["symbol"]), resolved


def _effective_end_date(symbol: str, requested_end: str, use_latest: bool) -> str:
    if not use_latest:
        return requested_end
    updates = get_store().last_update(symbol)
    if updates.empty:
        return str(date.today())
    return str(updates.iloc[0]["last_dt"])


@tool
def resolve_stock_symbol_tool(query: str) -> str:
    """将中文股票名称或带市场后缀的代码解析为 QuantLab 六位股票代码。"""
    return json.dumps(resolve_stock_symbol(query), ensure_ascii=False)


@tool
def get_kline_data_tool(
    symbol: str,
    start_date: str,
    end_date: str,
    use_latest: bool = True,
    lookback: int | None = None,
) -> str:
    """获取股票 K 线数据（日线 OHLCV）。

    Args:
        symbol: 股票代码或中文名称，例如 "600519"、"贵州茅台"
        start_date: 开始日期，格式 YYYY-MM-DD
        end_date: 结束日期，格式 YYYY-MM-DD
        use_latest: 默认 true，将结束日期扩展到本地最新交易日；历史截面分析时设为 false
        lookback: 可选，返回最近 N 根日线。用户说“最近20个交易日/最近60根K线”时必须设置。

    Returns:
        K 线数据的 JSON 字符串，包含日期、开高低收、成交量
    """
    code, resolved = _resolve_symbol(symbol)
    effective_end = _effective_end_date(code, end_date, use_latest)
    bars = get_kline(code, start_date, effective_end)
    if lookback is not None:
        lookback = max(1, min(int(lookback), 240))
    returned_bars = bars[-lookback:] if lookback else bars[-60:]

    output = {
        "symbol": code,
        "name": resolved.get("name", ""),
        "bar_count": len(returned_bars),
        "total_bars": len(bars),
        "lookback": lookback,
        "requested_date_range": f"{start_date} ~ {end_date}",
        "effective_date_range": f"{start_date} ~ {effective_end}",
        "data_as_of": bars[-1].dt if bars else None,
        "use_latest": use_latest,
        "bars": [
            {
                "dt": b.dt,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in returned_bars
        ],
    }
    if returned_bars:
        closes = [b.close for b in returned_bars]
        output["summary"] = {
            "latest_close": closes[-1],
            "period_high": max(b.high for b in returned_bars),
            "period_low": min(b.low for b in returned_bars),
            "period_return": round((closes[-1] - closes[0]) / closes[0], 4) if closes[0] > 0 else 0,
        }
    return json.dumps(output, ensure_ascii=False, default=str)


@tool
def list_cached_stocks_tool() -> str:
    """列出所有已缓存的股票数据（本地 Parquet 文件），包含代码、K 线数量、起止日期。

    Returns:
        缓存股票列表的 JSON 字符串
    """
    symbols = list_cached_symbols()
    output = {
        "total_cached": len(symbols),
        "stocks": symbols[:50],  # 最多展示 50 条
    }
    return json.dumps(output, ensure_ascii=False, default=str)


@tool
def get_all_a_stock_list_tool(source: str = "tushare") -> str:
    """获取全部 A 股股票列表。

    Args:
        source: 数据源，可选 "tushare" 或 "akshare"

    Returns:
        全 A 股列表的 JSON 字符串，包含代码和名称
    """
    stocks = fetch_all_a_symbols(source)
    output = {
        "total": len(stocks),
        "stocks": stocks[:100],  # 最多展示 100 条
    }
    return json.dumps(output, ensure_ascii=False, default=str)
