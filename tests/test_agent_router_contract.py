from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from server.main import app
from server.agent import router as agent_router


def test_agent_websocket_rejects_invalid_json():
    client = TestClient(app)

    with client.websocket_connect("/api/agent/chat") as websocket:
        websocket.send_text("{not-json")
        frame = websocket.receive_json()

    assert frame["type"] == "error"
    assert "JSON" in frame["error"]


def test_agent_websocket_rejects_empty_content():
    client = TestClient(app)

    with client.websocket_connect("/api/agent/chat") as websocket:
        websocket.send_json({"content": "   ", "agent_mode": "market"})
        frame = websocket.receive_json()

    assert frame["type"] == "error"
    assert "不能为空" in frame["error"]


def test_agent_websocket_rejects_unknown_mode():
    client = TestClient(app)

    with client.websocket_connect("/api/agent/chat") as websocket:
        websocket.send_json({"content": "hello", "agent_mode": "missing"})
        frame = websocket.receive_json()

    assert frame["type"] == "error"
    assert "missing" in frame["error"]


def test_agent_websocket_rejects_disabled_runtime(monkeypatch):
    monkeypatch.setattr(
        agent_router,
        "get_agent_runtime_status",
        lambda: {
            "enabled": False,
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "configured": False,
            "reason": "deepseek API key is not configured",
        },
    )
    client = TestClient(app)

    with client.websocket_connect("/api/agent/chat") as websocket:
        websocket.send_json({"content": "分析 000001", "agent_mode": "market"})
        frame = websocket.receive_json()

    assert frame["type"] == "error"
    assert "API key" in frame["error"]


def test_agent_rest_rejects_disabled_runtime(monkeypatch):
    monkeypatch.setattr(
        agent_router,
        "get_agent_runtime_status",
        lambda: {
            "enabled": False,
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "configured": False,
            "reason": "deepseek API key is not configured",
        },
    )
    client = TestClient(app)

    response = client.post(
        "/api/agent/chat",
        json={"content": "分析 000001", "agent_mode": "market"},
    )

    assert response.status_code == 503
    assert "API key" in response.json()["detail"]


def test_agent_rest_rejects_unknown_mode(monkeypatch):
    monkeypatch.setattr(
        agent_router,
        "get_agent_runtime_status",
        lambda: {
            "enabled": True,
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "configured": True,
            "reason": None,
        },
    )
    client = TestClient(app)

    response = client.post(
        "/api/agent/chat",
        json={"content": "hello", "agent_mode": "missing"},
    )

    assert response.status_code == 400
    assert "missing" in response.json()["detail"]


def _enabled_runtime():
    return {
        "enabled": True,
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "configured": True,
        "reason": None,
    }


def test_agent_rest_strips_process_preamble(monkeypatch):
    class FakeGraph:
        def invoke(self, *args, **kwargs):
            return {
                "messages": [
                    AIMessage(
                        content="好的，我先查询 000001 的行情。\n\n## 结论\n短线偏弱。"
                    )
                ]
            }

    monkeypatch.setattr(agent_router, "get_agent_runtime_status", _enabled_runtime)
    monkeypatch.setattr(agent_router, "get_graph", lambda mode: FakeGraph())
    client = TestClient(app)

    response = client.post(
        "/api/agent/chat",
        json={"content": "分析 000001", "agent_mode": "market"},
    )

    assert response.status_code == 200
    assert response.json()["content"].startswith("## 结论")
    assert "我先查询" not in response.json()["content"]


def test_agent_public_answer_strips_heading_emoji():
    raw = (
        "## 📊 缓存概况\n"
        "本地已缓存 5115 只股票。\n\n"
        "### ✅ 是否支持回测？\n"
        "支持短中期回测。"
    )

    cleaned = agent_router._strip_process_preamble(raw)

    assert cleaned.startswith("## 缓存概况")
    assert "### 是否支持回测？" in cleaned
    assert "📊" not in cleaned
    assert "✅" not in cleaned


def test_agent_public_answer_removes_empty_headings():
    raw = "## 结论\n短线偏弱。\n\n#\n\n### 核心指标\n暂无新增信号。"

    cleaned = agent_router._strip_process_preamble(raw)

    assert "\n#\n" not in cleaned
    assert cleaned.startswith("## 结论")
    assert "### 核心指标" in cleaned


def test_agent_public_answer_clamps_heading_levels():
    raw = "# 结论\n短线偏弱。\n\n#### 过深标题\n细节。"

    cleaned = agent_router._strip_process_preamble(raw)

    assert cleaned.startswith("## 结论")
    assert "### 过深标题" in cleaned
    assert "\n# " not in cleaned
    assert "####" not in cleaned


def test_agent_public_answer_strips_screening_summary_preamble():
    raw = (
        "扫描完成，共命中 50 只股票。以下按成交金额排序，选出前 5 只候选。\n---\n\n"
        "## 筛选结论\n\nma_cross 策略命中 50 只候选。"
    )

    cleaned = agent_router._strip_process_preamble(raw)

    assert cleaned.startswith("## 筛选结论")
    assert "扫描完成" not in cleaned
    assert "---" not in cleaned.splitlines()[:2]


def test_agent_public_answer_adds_screening_risk_guardrail():
    raw = "## 筛选结论\nma_cross 策略命中 5 只候选。"

    guarded = agent_router._ensure_public_answer_guardrails(raw, "screening")

    assert guarded.startswith("## 筛选结论")
    assert "### 风险提示" in guarded
    assert "不等于买入结论" in guarded
    assert "回测验证" in guarded
    assert "候选逻辑可能失效" in guarded


def test_agent_public_answer_does_not_duplicate_risk_guardrail():
    raw = "## 筛选结论\n命中 5 只候选。\n\n### 风险提示\n需要继续验证。"

    guarded = agent_router._ensure_public_answer_guardrails(raw, "screening")

    assert guarded == raw
    assert guarded.count("### 风险提示") == 1


def test_agent_public_answer_adds_general_risk_guardrail():
    raw = "## 结论\n000001 最近 60 个交易日震荡。"

    guarded = agent_router._ensure_public_answer_guardrails(raw, "market")

    assert guarded.startswith("## 结论")
    assert "### 风险提示" in guarded
    assert "不构成确定性买卖建议" in guarded
    assert "仓位控制" in guarded


def test_agent_rest_hides_internal_transfer_tools(monkeypatch):
    class FakeGraph:
        def invoke(self, *args, **kwargs):
            return {
                "messages": [
                    AIMessage(
                        content="## 结论\n已完成。",
                        tool_calls=[
                            {
                                "name": "transfer_to_market_agent",
                                "args": {},
                                "id": "internal-1",
                                "type": "tool_call",
                            },
                            {
                                "name": "get_kline_data_tool",
                                "args": {"symbol": "000001"},
                                "id": "tool-1",
                                "type": "tool_call",
                            },
                        ],
                    )
                ]
            }

    monkeypatch.setattr(agent_router, "get_agent_runtime_status", _enabled_runtime)
    monkeypatch.setattr(agent_router, "get_graph", lambda mode: FakeGraph())
    client = TestClient(app)

    response = client.post(
        "/api/agent/chat",
        json={"content": "分析 000001", "agent_mode": "auto"},
    )

    assert response.status_code == 200
    tool_names = [tool["name"] for tool in response.json()["tool_calls"]]
    assert tool_names == ["get_kline_data_tool"]


def test_agent_rest_adds_risk_guardrail(monkeypatch):
    class FakeGraph:
        def invoke(self, *args, **kwargs):
            return {
                "messages": [
                    AIMessage(content="## 筛选结论\nma_cross 策略命中 5 只候选。")
                ]
            }

    monkeypatch.setattr(agent_router, "get_agent_runtime_status", _enabled_runtime)
    monkeypatch.setattr(agent_router, "get_graph", lambda mode: FakeGraph())
    client = TestClient(app)

    response = client.post(
        "/api/agent/chat",
        json={"content": "扫描 ma_cross", "agent_mode": "screening"},
    )

    assert response.status_code == 200
    content = response.json()["content"]
    assert content.startswith("## 筛选结论")
    assert "### 风险提示" in content
    assert "不等于买入结论" in content
