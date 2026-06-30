"""Agent 模块 Pydantic 数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------- WebSocket 消息 ----------

class ClientMessage(BaseModel):
    """客户端发送的 WebSocket 消息。"""
    type: str = "message"
    session_id: str | None = None
    content: str = ""
    agent_mode: str = "auto"
    images: list[dict] | None = None  # [{"data": "base64...", "media_type": "image/png"}]


class ServerFrame(BaseModel):
    """服务端发送的 WebSocket 帧。"""
    type: str  # session_init, text_delta, tool_call, tool_result, agent_dispatch, agent_complete, done, error
    session_id: str | None = None
    agent: str | None = None
    content: str | None = None
    tool: str | None = None
    tool_call_id: str | None = None
    input: dict | None = None
    data: dict | None = None
    usage: dict | None = None
    error: str | None = None


# ---------- REST 模型 ----------

class ChatRequest(BaseModel):
    """非流式聊天请求。"""
    session_id: str | None = None
    content: str
    agent_mode: str = "auto"
    images: list[dict] | None = None


class ChatResponse(BaseModel):
    """非流式聊天响应。"""
    session_id: str
    content: str
    tool_calls: list[dict] = Field(default_factory=list)


class SessionInfo(BaseModel):
    """会话信息。"""
    session_id: str
    message_count: int
    created_at: str
    last_active: str
