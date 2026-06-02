"""Market-data specialist agent."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from server.agent.model import get_agent_model
from server.agent.prompts import MARKET_AGENT_PROMPT
from server.agent.tools.analysis_tools import analyze_technicals_tool
from server.agent.tools.market_tools import (
    get_all_a_stock_list_tool,
    get_kline_data_tool,
    list_cached_stocks_tool,
    resolve_stock_symbol_tool,
)


def create_market_agent(
    model: BaseChatModel | None = None,
    checkpointer=None,
):
    return create_react_agent(
        model=model or get_agent_model(),
        tools=[
            resolve_stock_symbol_tool,
            get_kline_data_tool,
            list_cached_stocks_tool,
            get_all_a_stock_list_tool,
            analyze_technicals_tool,
        ],
        prompt=MARKET_AGENT_PROMPT,
        name="market_agent",
        checkpointer=checkpointer,
    )
