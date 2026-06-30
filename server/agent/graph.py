"""LangGraph supervisor for QuantLab's specialist agents."""

from __future__ import annotations

from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph_supervisor import create_supervisor

from server.agent.agents.backtest_agent import create_backtest_agent
from server.agent.agents.market_agent import create_market_agent
from server.agent.agents.quant_agent import create_quant_agent
from server.agent.agents.screening_agent import create_screening_agent
from server.agent.model import get_agent_model
from server.agent.prompts import SUPERVISOR_SYSTEM_PROMPT

AGENT_MODES = {
    "auto": {"label": "自动协作", "agent": "supervisor"},
    "quant": {"label": "Quant Agent", "agent": "quant_agent"},
    "market": {"label": "行情分析", "agent": "market_agent"},
    "screening": {"label": "智能选股", "agent": "screening_agent"},
    "backtest": {"label": "策略回测", "agent": "backtest_agent"},
}

@lru_cache(maxsize=1)
def _get_checkpointer() -> MemorySaver:
    return MemorySaver()


def build_graph(mode: str = "auto"):
    model = get_agent_model()
    checkpointer = _get_checkpointer()
    if mode == "quant":
        return create_quant_agent(model, checkpointer=checkpointer)
    if mode == "market":
        return create_market_agent(model, checkpointer=checkpointer)
    if mode == "screening":
        return create_screening_agent(model, checkpointer=checkpointer)
    if mode == "backtest":
        return create_backtest_agent(model, checkpointer=checkpointer)
    if mode != "auto":
        raise ValueError(f"unsupported agent mode: {mode}")

    supervisor = create_supervisor(
        agents=[
            create_backtest_agent(model),
            create_screening_agent(model),
            create_market_agent(model),
            create_quant_agent(model),
        ],
        model=model,
        prompt=SUPERVISOR_SYSTEM_PROMPT,
    )
    return supervisor.compile(checkpointer=checkpointer)


@lru_cache(maxsize=len(AGENT_MODES))
def get_graph(mode: str = "auto"):
    return build_graph(mode)


def reset_graph() -> None:
    get_graph.cache_clear()
