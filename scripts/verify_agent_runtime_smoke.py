from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.request import urlopen


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


PROCESS_PREFIXES = ("好的", "我来", "我将", "首先", "现在", "接下来", "您好", "让我")
BANNED_ABSOLUTE_PHRASES = (
    "必涨",
    "必跌",
    "确定买入",
    "确定卖出",
    "建议买入",
    "建议卖出",
    "稳赚",
    "无风险",
    "保本",
    "保证收益",
    "年翻倍",
    "满仓",
    "梭哈",
    "不值得继续研究",
)
MOJIBAKE_TOKENS = ("鎵", "鐮", "锛", "鈥", "銆", "閫", "椋", "鑷", "琛", "鏅")
DATA_SCOPE_PATTERN = re.compile(
    r"数据截至|观察窗口|样本|回测区间|扫描|lookback|参数|最近\s*\d+\s*(?:个)?交易日"
)
RISK_LIMIT_PATTERN = re.compile(
    r"风险|限制|失效|不支持直接采用|需要继续验证|暂不具备|观察|回撤|止损|仓位|样本外|回测验证|不构成"
)


@dataclass
class AgentCase:
    mode: str
    content: str
    expected_heading: str
    expected_tools: tuple[str, ...]
    required_inputs: dict[str, tuple[str, ...]]
    expected_result: dict[str, dict[str, Any]]


CASES = (
    AgentCase(
        mode="market",
        content="请分析 000001 最近20个交易日的KDJ、RSI、成交量和BBI，给我简短结论。",
        expected_heading="结论",
        expected_tools=("get_kline_data_tool", "analyze_technicals_tool"),
        required_inputs={
            "get_kline_data_tool": ("symbol", "lookback"),
            "analyze_technicals_tool": ("symbol", "lookback"),
        },
        expected_result={
            "get_kline_data_tool": {"bar_count": 20, "lookback": 20},
            "analyze_technicals_tool": {"returned_bars": 20, "lookback": 20},
        },
    ),
    AgentCase(
        mode="screening",
        content="请用 ma_cross 策略扫描当前缓存市场，lookback 80，给我前5个候选和简短解释。",
        expected_heading="筛选结论",
        expected_tools=("screen_stocks_tool",),
        required_inputs={"screen_stocks_tool": ("strategy", "lookback")},
        expected_result={"screen_stocks_tool": {"strategy": "ma_cross"}},
    ),
    AgentCase(
        mode="backtest",
        content=(
            "请用 ma_cross 策略回测 000001，区间 2025-01-01 到 2026-06-29，"
            "参数 fast_period=5 slow_period=20，输出核心指标。"
        ),
        expected_heading="回测结论",
        expected_tools=("run_backtest_tool",),
        required_inputs={
            "run_backtest_tool": ("symbols", "start_date", "end_date", "strategy", "strategy_params")
        },
        expected_result={"run_backtest_tool": {"metrics": dict}},
    ),
    AgentCase(
        mode="auto",
        content="请先分析 000001 最近20个交易日走势，再建议是否需要用 ma_cross 做回测验证。",
        expected_heading="结论",
        expected_tools=("get_kline_data_tool", "analyze_technicals_tool"),
        required_inputs={
            "get_kline_data_tool": ("symbol", "lookback"),
            "analyze_technicals_tool": ("symbol", "lookback"),
        },
        expected_result={"get_kline_data_tool": {"lookback": 20}},
    ),
)


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def runtime_status(base_url: str) -> dict[str, Any]:
    with urlopen(f"{base_url.rstrip('/')}/api/agent/runtime", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def websocket_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.startswith("https://"):
        return "wss://" + value[len("https://"):] + "/api/agent/chat"
    if value.startswith("http://"):
        return "ws://" + value[len("http://"):] + "/api/agent/chat"
    raise ValueError(f"unsupported base url: {base_url}")


def assert_public_answer_quality(case: AgentCase, text: str) -> None:
    ensure(text.strip(), f"{case.mode}: no public answer text")
    ensure(
        not text.lstrip().startswith(PROCESS_PREFIXES),
        f"{case.mode}: answer starts with process chatter: {text[:120]!r}",
    )
    ensure(
        not text.lstrip().startswith("# "),
        f"{case.mode}: answer should not use H1 inside chat: {text[:120]!r}",
    )
    ensure(
        text.lstrip().startswith("##"),
        f"{case.mode}: answer should start with a level-2 markdown heading: {text[:120]!r}",
    )
    heading_match = re.match(r"\s*##\s+(.+)", text)
    ensure(heading_match is not None, f"{case.mode}: missing first markdown heading")
    first_heading = heading_match.group(1).strip()
    ensure(
        case.expected_heading in first_heading,
        f"{case.mode}: first heading {first_heading!r} should contain {case.expected_heading!r}",
    )
    ensure(
        not re.search(r"[\U0001F300-\U0001FAFF]", first_heading),
        f"{case.mode}: first heading should not use emoji decoration: {first_heading!r}",
    )
    for token in MOJIBAKE_TOKENS:
        ensure(token not in text, f"{case.mode}: answer contains mojibake token {token!r}")
    ensure("```json" not in text.lower(), f"{case.mode}: answer should not expose raw JSON blocks")
    for phrase in BANNED_ABSOLUTE_PHRASES:
        ensure(
            phrase not in text,
            f"{case.mode}: answer contains overly absolute ToC wording {phrase!r}: {text[:240]!r}",
        )
    ensure(
        DATA_SCOPE_PATTERN.search(text) is not None,
        f"{case.mode}: answer should state data scope/date/window/params: {text[:360]!r}",
    )
    ensure(
        RISK_LIMIT_PATTERN.search(text) is not None,
        f"{case.mode}: answer should include risk, limits, invalidation, or validation wording: {text[:360]!r}",
    )


async def run_case(ws_url: str, origin: str, case: AgentCase, timeout_s: float) -> dict[str, Any]:
    try:
        import websockets
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("websockets package is required for runtime smoke") from exc

    frames: list[dict[str, Any]] = []
    async with websockets.connect(
        ws_url,
        open_timeout=10,
        ping_interval=None,
        origin=origin,
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "content": case.content,
                    "agent_mode": case.mode,
                    "session_id": f"smoke-{case.mode}-{uuid.uuid4().hex[:8]}",
                },
                ensure_ascii=False,
            )
        )
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
            frame = json.loads(raw)
            frames.append(frame)
            if frame.get("type") in {"done", "error"}:
                break

    ensure(frames, f"{case.mode}: no frames returned")
    ensure(frames[-1].get("type") == "done", f"{case.mode}: terminal frame is {frames[-1]}")
    text = "".join(str(frame.get("content") or "") for frame in frames if frame.get("type") == "text_delta")
    assert_public_answer_quality(case, text)

    latest_calls: dict[str, dict[str, Any]] = {}
    for frame in frames:
        if frame.get("type") == "tool_call":
            key = str(frame.get("tool_call_id") or frame.get("tool") or len(latest_calls))
            latest_calls[key] = frame
    calls_by_tool = {str(frame.get("tool")): frame for frame in latest_calls.values()}
    for tool_name in case.expected_tools:
        ensure(tool_name in calls_by_tool, f"{case.mode}: missing tool call {tool_name}")
        for input_key in case.required_inputs.get(tool_name, ()):
            ensure(
                input_key in (calls_by_tool[tool_name].get("input") or {}),
                f"{case.mode}: tool {tool_name} missing input {input_key}: {calls_by_tool[tool_name]}",
            )

    results_by_tool = {
        str(frame.get("tool")): frame.get("data") or {}
        for frame in frames
        if frame.get("type") == "tool_result"
    }
    for tool_name, expectations in case.expected_result.items():
        ensure(tool_name in results_by_tool, f"{case.mode}: missing tool result {tool_name}")
        data = results_by_tool[tool_name]
        ensure(not data.get("error"), f"{case.mode}: tool {tool_name} returned error: {data.get('error')}")
        for key, expected in expectations.items():
            if isinstance(expected, type):
                ensure(isinstance(data.get(key), expected), f"{case.mode}: {tool_name}.{key} is not {expected}")
            else:
                ensure(data.get(key) == expected, f"{case.mode}: {tool_name}.{key}={data.get(key)!r}, expected {expected!r}")

    return {
        "mode": case.mode,
        "frames": len(frames),
        "tools": [frame.get("tool") for frame in latest_calls.values()],
        "answer_start": text[:180],
    }


async def run_all(args: argparse.Namespace) -> list[dict[str, Any]]:
    status = runtime_status(args.base_url)
    if not status.get("enabled"):
        if args.allow_disabled:
            return [{"status": "skipped", "reason": status.get("reason") or "agent runtime disabled"}]
        raise AssertionError(f"agent runtime disabled: {status}")
    return [
        await run_case(websocket_url(args.base_url), args.origin, case, args.timeout)
        for case in CASES
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify QuantLab Agent runtime over WebSocket.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--origin", default="http://127.0.0.1:5174")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--allow-disabled", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(run_all(args))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\nAgent runtime smoke passed.\n")


if __name__ == "__main__":
    main()
