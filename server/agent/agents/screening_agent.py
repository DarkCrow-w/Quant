"""Stock-screening specialist agent."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from server.agent.model import get_agent_model
from server.agent.prompts import SCREENING_AGENT_PROMPT
from server.agent.tools.screening_tools import screen_stocks_tool


def create_screening_agent(
    model: BaseChatModel | None = None,
    checkpointer=None,
):
    return create_react_agent(
        model=model or get_agent_model(),
        tools=[screen_stocks_tool],
        prompt=SCREENING_AGENT_PROMPT,
        name="screening_agent",
        checkpointer=checkpointer,
    )
