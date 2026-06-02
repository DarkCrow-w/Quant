"""Backtest specialist agent."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from server.agent.model import get_agent_model
from server.agent.prompts import BACKTEST_AGENT_PROMPT
from server.agent.tools.backtest_tools import (
    compare_backtests_tool,
    list_strategies_tool,
    run_backtest_tool,
)


def create_backtest_agent(
    model: BaseChatModel | None = None,
    checkpointer=None,
):
    return create_react_agent(
        model=model or get_agent_model(),
        tools=[run_backtest_tool, list_strategies_tool, compare_backtests_tool],
        prompt=BACKTEST_AGENT_PROMPT,
        name="backtest_agent",
        checkpointer=checkpointer,
    )
