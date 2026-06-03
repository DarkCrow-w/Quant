"""Agent WebSocket and REST endpoints."""

from __future__ import annotations

import asyncio
import json
import traceback
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    ToolMessage,
)

from server.agent.graph import AGENT_MODES, get_graph
from server.agent.memory import session_manager
from server.agent.model import get_agent_runtime_status
from server.agent.schemas import ChatRequest, ChatResponse, ServerFrame, SessionInfo

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _build_human_message(
    content: str,
    images: list[dict] | None = None,
) -> HumanMessage:
    if not images:
        return HumanMessage(content=content)

    blocks: list[dict] = [{"type": "text", "text": content}]
    for image in images:
        blocks.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:{image.get('media_type', 'image/png')};base64,"
                        f"{image['data']}"
                    )
                },
            }
        )
    return HumanMessage(content=blocks)


def _frame(frame_type: str, **kwargs: Any) -> str:
    return ServerFrame(type=frame_type, **kwargs).model_dump_json(
        exclude_none=True
    )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _is_internal_tool(tool_name: str | None) -> bool:
    name = tool_name or ""
    return name == "transfer_back_to_supervisor" or name.startswith(
        "transfer_to_"
    )


def _produce_graph_stream(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
    session_id: str,
    human_message: HumanMessage,
    agent_mode: str,
) -> None:
    """Run the synchronous model stream away from Uvicorn's event loop."""
    try:
        for item in get_graph(agent_mode).stream(
            {"messages": [human_message]},
            config={
                "configurable": {
                    "thread_id": f"{agent_mode}:{session_id}",
                }
            },
            stream_mode="messages",
        ):
            loop.call_soon_threadsafe(queue.put_nowait, ("item", item))
    except Exception as exc:
        loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, ("done", None))


@router.get("/runtime")
def runtime_status():
    return {
        **get_agent_runtime_status(),
        "modes": [
            {"key": key, **details}
            for key, details in AGENT_MODES.items()
        ],
    }


@router.websocket("/chat")
async def websocket_chat(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(_frame("error", error="无效的 JSON 格式"))
                continue

            content = str(data.get("content", "")).strip()
            images = data.get("images")
            agent_mode = str(data.get("agent_mode", "auto")).strip().lower()
            if agent_mode not in AGENT_MODES:
                await ws.send_text(
                    _frame("error", error=f"不支持的 Agent 模式：{agent_mode}")
                )
                continue
            if not content and not images:
                await ws.send_text(_frame("error", error="消息内容不能为空"))
                continue

            session_id = data.get("session_id")
            if not session_id or not session_manager.exists(session_id):
                session_id = session_manager.create_session()
            session_manager.touch(session_id)
            await ws.send_text(_frame("session_init", session_id=session_id))

            try:
                await _stream_graph_response(
                    ws,
                    session_id,
                    _build_human_message(content, images),
                    agent_mode,
                )
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                traceback.print_exc()
                await ws.send_text(
                    _frame("error", error=f"Agent 执行出错：{exc}")
                )
    except WebSocketDisconnect:
        return
    except Exception:
        traceback.print_exc()


async def _stream_graph_response(
    ws: WebSocket,
    session_id: str,
    human_message: HumanMessage,
    agent_mode: str = "auto",
) -> None:
    dispatched: set[str] = set()
    seen_tool_calls: set[str] = set()
    supervisor_chunks: list[str] = []
    specialist_text: dict[str, str] = {}
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    producer = asyncio.create_task(
        asyncio.to_thread(
            _produce_graph_stream,
            loop,
            queue,
            session_id,
            human_message,
            agent_mode,
        )
    )
    initial_agent = AGENT_MODES[agent_mode]["agent"]
    initial_task = (
        "正在拆解研究任务"
        if agent_mode == "auto"
        else f"已进入{AGENT_MODES[agent_mode]['label']}模式"
    )
    dispatched.add(initial_agent)
    await ws.send_text(
        _frame(
            "agent_dispatch",
            agent=initial_agent,
            content=initial_task,
        )
    )

    while True:
        event_type, payload = await queue.get()
        if event_type == "done":
            break
        if event_type == "error":
            raise payload

        message, metadata = payload
        node_name = str(metadata.get("langgraph_node") or "supervisor")
        if agent_mode == "auto":
            public_agent = node_name if node_name != "supervisor" else None
        else:
            public_agent = AGENT_MODES[agent_mode]["agent"]

        if public_agent and public_agent not in dispatched:
            supervisor_chunks.clear()
            dispatched.add(public_agent)
            await ws.send_text(
                _frame(
                    "agent_dispatch",
                    agent=public_agent,
                    content="正在分析任务",
                )
            )

        if isinstance(message, (AIMessage, AIMessageChunk)):
            for tool_call in message.tool_calls or []:
                tool_name = tool_call.get("name")
                if not tool_name or _is_internal_tool(tool_name):
                    continue
                if node_name == "supervisor":
                    supervisor_chunks.clear()
                else:
                    specialist_text[node_name] = ""
                call_id = str(tool_call.get("id") or "")
                if call_id and call_id in seen_tool_calls:
                    continue
                if call_id:
                    seen_tool_calls.add(call_id)
                await ws.send_text(
                    _frame(
                        "tool_call",
                        agent=public_agent or "supervisor",
                        tool=tool_name,
                        input=tool_call.get("args") or {},
                    )
                )

            text = _message_text(message.content).strip()
            if text and not text.lower().startswith("transferring "):
                if node_name == "supervisor":
                    supervisor_chunks.append(text)
                else:
                    specialist_text[node_name] = (
                        specialist_text.get(node_name, "") + text
                    )

        elif isinstance(message, ToolMessage):
            if _is_internal_tool(message.name):
                continue
            try:
                parsed = (
                    json.loads(message.content)
                    if isinstance(message.content, str)
                    else message.content
                )
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw": str(message.content)}
            await ws.send_text(
                _frame(
                    "tool_result",
                    agent=public_agent or "supervisor",
                    tool=message.name or "tool",
                    data=parsed if isinstance(parsed, dict) else {"result": parsed},
                )
            )

    await producer
    candidates = [
        text.strip()
        for text in [*specialist_text.values(), "".join(supervisor_chunks)]
        if text.strip()
    ]
    final_text = max(candidates, key=len) if candidates else ""
    if final_text:
        response_agent = (
            "supervisor"
            if agent_mode == "auto"
            else AGENT_MODES[agent_mode]["agent"]
        )
        await ws.send_text(
            _frame(
                "text_delta",
                content=final_text,
                agent=response_agent,
            )
        )
    for agent_name in dispatched:
        await ws.send_text(_frame("agent_complete", agent=agent_name))
    await ws.send_text(_frame("done", session_id=session_id))


@router.post("/chat", response_model=ChatResponse)
def rest_chat(req: ChatRequest) -> ChatResponse:
    status = get_agent_runtime_status()
    if not status["enabled"]:
        raise HTTPException(status_code=503, detail=status["reason"])
    if req.agent_mode not in AGENT_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported agent mode: {req.agent_mode}",
        )

    session_id = req.session_id
    if not session_id or not session_manager.exists(session_id):
        session_id = session_manager.create_session()
    session_manager.touch(session_id)

    result = get_graph(req.agent_mode).invoke(
        {"messages": [_build_human_message(req.content, req.images)]},
        config={
            "configurable": {
                "thread_id": f"{req.agent_mode}:{session_id}",
            }
        },
    )
    final_text = ""
    tool_calls: list[dict] = []
    for message in result.get("messages", []):
        if isinstance(message, AIMessage):
            text = _message_text(message.content)
            if text:
                final_text = text
            tool_calls.extend(message.tool_calls or [])

    return ChatResponse(
        session_id=session_id,
        content=final_text,
        tool_calls=tool_calls,
    )


@router.get("/sessions", response_model=list[SessionInfo])
def list_sessions():
    return [SessionInfo(**session) for session in session_manager.list_sessions()]


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    return {
        "status": "deleted" if session_manager.delete(session_id) else "not_found"
    }
