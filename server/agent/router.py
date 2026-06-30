"""Agent WebSocket and REST endpoints."""

from __future__ import annotations

import asyncio
import json
import re
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

_PROCESS_PREAMBLE_RE = re.compile(
    r"^\s*(?:好的[，。,.\s]*)?"
    r"(?:我(?:来|先|会|将)|现在|首先|接下来|下面|让我们)"
    r"[\s\S]{0,240}?(?:\n\s*\n|。|；|;|：|:)"
    r"(?=\s*(?:#{1,3}\s*)?(?:结论|核心|关键|风险|筛选|扫描|回测|技术|行情|一、|二、|三、))",
)
_PROCESS_OPENING_RE = re.compile(
    r"^\s*(?:好的[，。,.\s]*)?(?:我(?:来|先|会|将)|现在|首先|接下来|下面|让我们)"
)
_PRESENTATION_OPENING_RE = re.compile(
    r"^\s*好的[，。,.\s]*(?:以下是|这是|为你|给你|根据|)"
)
_SUMMARY_OPENING_RE = re.compile(
    r"^\s*(?:扫描完成|回测完成|分析完成|筛选完成|共扫描|已完成)"
)
_IDENTIFY_OPENING_RE = re.compile(
    r"^\s*(?:\d{6}|[\u4e00-\u9fffA-Za-z]{2,20})\s*(?:是|为).{0,80}?(?:现在|获取|查询|分析|读取)"
)
_PUBLIC_ANSWER_START_RE = re.compile(
    r"(#{1,3}\s*(?:结论|核心|关键|风险|筛选|扫描|回测|技术|行情)|"
    r"(?:^|\n)\s*(?:结论|核心指标|关键证据|风险提示|筛选结论|扫描结果|回测结果)[:：]?)"
)
_MARKDOWN_HEADING_RE = re.compile(r"#{1,3}\s+\S")
_MARKDOWN_HEADING_LINE_RE = re.compile(r"^(#{1,6})(\s+)(.*)$")
_RISK_LIMIT_RE = re.compile(
    r"风险|限制|失效|不建议直接采用|需要继续验证|暂不具备|观察|回撤|止损|"
    r"仓位|二次过滤|样本外|回测验证|不构成"
)
_SCREENING_ANSWER_RE = re.compile(
    r"筛选结论|扫描结果|命中|候选|信号强度|screen_stocks|选股"
)

def _is_heading_decoration(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x1F300 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or codepoint in {0xFE0F, 0x200D}
    )


def _strip_heading_decoration(text: str) -> str:
    """Keep public answers visually stable by removing emoji from headings."""
    lines = []
    for line in text.splitlines():
        match = _MARKDOWN_HEADING_LINE_RE.match(line)
        if match:
            prefix, spacing, title = match.groups()
            if len(prefix) < 2:
                prefix = "##"
            elif len(prefix) > 3:
                prefix = "###"
            title = title.lstrip()
            while title and _is_heading_decoration(title[0]):
                title = title[1:].lstrip()
            if not title:
                continue
            line = f"{prefix}{spacing}{title}"
        elif re.fullmatch(r"\s*#{1,6}\s*", line):
            continue
        lines.append(line)
    return "\n".join(lines)


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


def _strip_process_preamble(text: str) -> str:
    """Remove user-visible process chatter before the actual public answer."""
    cleaned = _PROCESS_PREAMBLE_RE.sub("", text, count=1).lstrip()
    if cleaned != text.lstrip():
        return _strip_heading_decoration(cleaned)
    heading = _MARKDOWN_HEADING_RE.search(text)
    if heading and 0 < heading.start() <= 800:
        prefix = text[: heading.start()].strip()
        if prefix.startswith("好的") or re.search(
            r"(您好|查询|获取|读取|分析|指标|数据|现在|我先|让我|转交|工具|Agent|"
            r"扫描完成|回测完成|分析完成|筛选完成|命中|候选)",
            prefix,
        ):
            return _strip_heading_decoration(text[heading.start():].lstrip())
    if not _looks_like_process_preamble(text):
        return _strip_heading_decoration(text)
    match = _PUBLIC_ANSWER_START_RE.search(text)
    if match and match.start() > 0:
        return _strip_heading_decoration(text[match.start():].lstrip())
    if heading and heading.start() > 0:
        return _strip_heading_decoration(text[heading.start():].lstrip())
    return _strip_heading_decoration(text)


def _ensure_public_answer_guardrails(text: str, agent_mode: str | None = None) -> str:
    """Guarantee consumer-facing answers include basic ToC risk framing."""
    cleaned = _strip_process_preamble(text)
    stripped = cleaned.strip()
    if not stripped or stripped.startswith("## 分析未完成"):
        return cleaned
    if _RISK_LIMIT_RE.search(stripped):
        return cleaned

    is_screening = agent_mode == "screening" or bool(
        _SCREENING_ANSWER_RE.search(stripped)
    )
    if is_screening:
        note = (
            "\n\n### 风险提示\n"
            "以上信号只代表策略条件命中，不等于买入结论。请继续做回测验证、"
            "二次过滤和样本外观察；如果后续跌破信号触发日低点，或量能无法延续，"
            "候选逻辑可能失效。"
        )
    else:
        note = (
            "\n\n### 风险提示\n"
            "以上结论依赖当前数据窗口和参数设置，不构成确定性买卖建议；"
            "请结合失效条件、仓位控制和后续数据继续验证。"
        )
    return cleaned.rstrip() + note


def _looks_like_process_preamble(text: str) -> bool:
    return bool(
        _PROCESS_OPENING_RE.match(text) or _PRESENTATION_OPENING_RE.match(text)
        or _SUMMARY_OPENING_RE.match(text)
        or _IDENTIFY_OPENING_RE.match(text)
    )


def _has_public_answer_start(text: str) -> bool:
    return bool(
        _PUBLIC_ANSWER_START_RE.search(text) or _MARKDOWN_HEADING_RE.search(text)
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
            status = get_agent_runtime_status()
            if not status["enabled"]:
                await ws.send_text(
                    _frame(
                        "error",
                        error=status.get("reason") or "Agent 模型 API Key 未配置",
                    )
                )
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
    latest_tool_calls: dict[str, dict[str, Any]] = {}
    tool_chunk_call_ids: dict[tuple[str, int], str] = {}
    tool_arg_buffers: dict[str, str] = {}
    supervisor_chunks: list[str] = []
    specialist_text: dict[str, str] = {}
    streamed_parts: list[str] = []
    stream_buffer: list[str] = []
    stream_started = False
    stream_agent = AGENT_MODES[agent_mode]["agent"]
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

    async def flush_stream(force: bool = False) -> None:
        nonlocal stream_started
        if not stream_buffer:
            return
        text = "".join(stream_buffer)
        if not force and len(text) < 48 and "\n" not in text:
            return
        if not stream_started:
            cleaned = _strip_process_preamble(text)
            if not _has_public_answer_start(cleaned):
                if force and _looks_like_process_preamble(cleaned):
                    stream_buffer.clear()
                    return
                if not force:
                    return
            if cleaned == text and _looks_like_process_preamble(text) and not force:
                return
            text = cleaned
        stream_buffer.clear()
        if not text:
            return
        stream_started = True
        await ws.send_text(
            _frame(
                "text_delta",
                content=text,
                agent=stream_agent,
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
            for chunk in getattr(message, "tool_call_chunks", None) or []:
                chunk_id = chunk.get("id")
                chunk_index = int(chunk.get("index") or 0)
                chunk_key = (node_name, chunk_index)
                if chunk_id:
                    tool_chunk_call_ids[chunk_key] = str(chunk_id)
                call_id = tool_chunk_call_ids.get(chunk_key)
                if not call_id:
                    continue
                existing = latest_tool_calls.get(call_id, {})
                tool_name = chunk.get("name") or existing.get("name")
                if tool_name:
                    latest_tool_calls[call_id] = {
                        "name": tool_name,
                        "args": existing.get("args") or {},
                        "agent": public_agent or existing.get("agent") or "supervisor",
                    }
                args_piece = chunk.get("args")
                if isinstance(args_piece, str) and args_piece:
                    tool_arg_buffers[call_id] = (
                        tool_arg_buffers.get(call_id, "") + args_piece
                    )
                    try:
                        parsed_args = json.loads(tool_arg_buffers[call_id])
                    except json.JSONDecodeError:
                        parsed_args = None
                    if isinstance(parsed_args, dict):
                        existing = latest_tool_calls.get(call_id, {})
                        latest_tool_calls[call_id] = {
                            "name": existing.get("name") or tool_name or "",
                            "args": parsed_args,
                            "agent": public_agent or existing.get("agent") or "supervisor",
                        }

            for tool_call in message.tool_calls or []:
                tool_name = tool_call.get("name")
                if not tool_name or _is_internal_tool(tool_name):
                    continue
                if node_name == "supervisor":
                    supervisor_chunks.clear()
                else:
                    specialist_text[node_name] = ""
                await flush_stream(force=True)
                call_id = str(tool_call.get("id") or "")
                if call_id:
                    existing = latest_tool_calls.get(call_id, {})
                    args = tool_call.get("args") or existing.get("args") or {}
                    latest_tool_calls[call_id] = {
                        "name": tool_name,
                        "args": args,
                        "agent": public_agent or existing.get("agent") or "supervisor",
                    }
                if call_id and call_id in seen_tool_calls:
                    continue
                if call_id:
                    seen_tool_calls.add(call_id)
                await ws.send_text(
                    _frame(
                        "tool_call",
                        agent=public_agent or "supervisor",
                        tool=tool_name,
                        tool_call_id=call_id or None,
                        input=tool_call.get("args") or {},
                    )
                )

            raw_text = _message_text(message.content)
            text = raw_text if isinstance(message, AIMessageChunk) else raw_text.strip()
            if text and not text.strip().lower().startswith("transferring "):
                if isinstance(message, AIMessageChunk) and public_agent:
                    streamed_parts.append(text)
                    stream_agent = public_agent
                    stream_buffer.append(text)
                    await flush_stream()
                elif node_name == "supervisor":
                    supervisor_chunks.append(text)
                else:
                    specialist_text[node_name] = (
                        specialist_text.get(node_name, "") + text
                    )

        elif isinstance(message, ToolMessage):
            if _is_internal_tool(message.name):
                continue
            await flush_stream(force=True)
            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id and tool_call_id in latest_tool_calls:
                latest = latest_tool_calls[tool_call_id]
                if latest.get("args"):
                    await ws.send_text(
                        _frame(
                            "tool_call",
                            agent=str(latest.get("agent") or public_agent or "supervisor"),
                            tool=str(latest.get("name") or message.name or "tool"),
                            tool_call_id=tool_call_id,
                            input=latest.get("args") or {},
                        )
                    )
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
                    tool_call_id=tool_call_id,
                    data=parsed if isinstance(parsed, dict) else {"result": parsed},
                )
            )

    await producer
    await flush_stream(force=True)
    candidates = [
        text.strip()
        for text in [*specialist_text.values(), "".join(supervisor_chunks)]
        if text.strip()
    ]
    final_text = max(candidates, key=len) if candidates else ""
    final_text = _ensure_public_answer_guardrails(final_text, agent_mode)
    if final_text and not streamed_parts:
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
    elif streamed_parts:
        streamed_text = _strip_process_preamble("".join(streamed_parts))
        guarded_stream = _ensure_public_answer_guardrails(streamed_text, agent_mode)
        suffix = (
            guarded_stream[len(streamed_text):]
            if guarded_stream.startswith(streamed_text)
            else ""
        )
        if suffix:
            await ws.send_text(
                _frame(
                    "text_delta",
                    content=suffix,
                    agent=stream_agent,
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
            tool_calls.extend(
                tool_call
                for tool_call in message.tool_calls or []
                if not _is_internal_tool(tool_call.get("name"))
            )

    return ChatResponse(
        session_id=session_id,
        content=_ensure_public_answer_guardrails(final_text, req.agent_mode),
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
