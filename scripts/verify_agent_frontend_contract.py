from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def ensure_contains(text: str, needle: str, label: str) -> None:
    ensure(needle in text, f"{label} is missing {needle!r}")


def ensure_not_contains(text: str, needle: str, label: str) -> None:
    ensure(needle not in text, f"{label} should not contain {needle!r}")


def verify_agent_page_mount() -> None:
    app = read("web/src/App.tsx")
    page = read("web/src/pages/AgentPage.tsx")
    ensure_contains(app, "<AgentPage />", "Agent page route")
    ensure_contains(page, "<ChatContainer />", "Agent page container")


def verify_agent_session_ui() -> None:
    container = read("web/src/components/agent/ChatContainer.tsx")
    status_bar = read("web/src/components/agent/AgentStatusBar.tsx")
    chat_input = read("web/src/components/agent/ChatInput.tsx")

    for needle in (
        "AI 量化研究员",
        "把量化研究问题交给协作式 AI 团队",
        "自动协作",
        "行情分析",
        "智能选股",
        "策略回测",
        "请先配置 Agent 模型 API Key",
        "研究服务在线",
        "正在回答",
        "重试上一问",
        "清空当前对话",
        "当前模式的本地对话记录会被清空",
        "AgentStatusBar",
        "answerAssuranceItems",
        "answerOutline",
    ):
        ensure_contains(container, needle, "Agent container")

    for prompt in (
        "诊断一只股票",
        "寻找市场机会",
        "设计波段策略",
        "回测均线策略",
        "检查缓存数据",
    ):
        ensure_contains(container, prompt, "Agent prompt cards")

    for needle in (
        "agent-process-summary",
        "Agent 研究进度",
        "正在读取数据和指标",
        "正在规划研究步骤",
        "正在整理工具结果",
        "正在生成研究结论",
        "研究过程已完成",
        "completedTools",
        "failedTools",
    ):
        ensure_contains(status_bar, needle, "Agent process summary")

    for needle in (
        "chat-composer-hint",
        "composerHint",
        "store.queuedMessage",
        "const accepted = store.sendMessage",
        "if (accepted)",
        "正在生成研究结论，工具调用和最终答案会实时更新",
        "研究服务未连接，发送后会自动尝试连接",
        "停止分析",
        "发送",
    ):
        ensure_contains(chat_input, needle, "Agent composer")


def verify_agent_store_contract() -> None:
    store = read("web/src/stores/agent.ts")
    for needle in (
        "stripProcessPreamble",
        "publicAnswerStartPattern",
        "markdownHeadingPattern",
        "stripHeadingDecoration",
        "friendlyAgentError",
        "interruptionMessage",
        "interruptedPartialMessage",
        "## 分析未完成",
        "Agent 模型暂不可用",
        "本次分析超时了",
        "研究服务连接异常",
        "你已停止本次分析",
        "研究服务连接中断",
        "上方是停止前已经生成的部分结果",
        "上方是连接中断前已经生成的部分结果",
        "图片分析请求",
        "请先配置 Agent 模型 API Key",
        "正在连接研究服务，连接后会自动发送",
        "连接失败，正在准备重试",
        "收到无法解析的 Agent 消息",
        "window.localStorage.setItem",
        "window.localStorage.removeItem(CONVERSATION_STORAGE_KEY)",
        "compactMessagesForCache",
        "compactToolCallForCache",
        "cached_summary",
        "retryLastMessage: () => boolean",
        "lastUserMessage",
        "get().sendMessage(lastUserMessage.content",
        "existingIndex",
        "findIndex((call) => call.id === frame.tool_call_id)",
        "input: frame.input ?? nextCalls[existingIndex].input",
        "!intentionalCloseRequested",
        "intentionalCloseRequested = true",
    ):
        ensure_contains(store, needle, "Agent store contract")


def verify_tool_cards() -> None:
    card = read("web/src/components/agent/ToolCallCard.tsx")
    for needle in (
        "工具执行失败",
        "暂无命中",
        "暂无可用 K 线数据",
        "回测已完成，但没有返回核心指标",
        "读取 K 线",
        "技术指标分析",
        "条件选股",
        "执行回测",
        "STATUS_LABELS",
        "toolFacts",
        "agent-tool-facts",
        "工具关键结果",
        "总收益",
        "扫描股票",
        "进行中",
        "已完成",
        "失败",
        "agent-tool-status",
        "inputSummary",
    ):
        ensure_contains(card, needle, "Agent tool card")
    ensure_not_contains(card, "JSON.stringify(toolCall.result", "Agent tool card raw JSON")
    ensure_not_contains(card, "Collapse", "Agent tool card raw JSON")


def verify_message_actions() -> None:
    bubble = read("web/src/components/agent/MessageBubble.tsx")
    answer_meta = read("web/src/components/agent/answerMeta.ts")
    for needle in (
        "answerOutline",
        "answerAssuranceItems",
        "agent-answer-assurance",
        "回答质量确认",
        "DatabaseOutlined",
        "SafetyCertificateOutlined",
        "已核对工具结果",
        "已标注数据窗口",
        "已包含风险提示",
        "agent-answer-outline",
        "复制回答",
        "downloadAnswer",
        "new Blob",
        "quantlab-agent-",
        "DownloadOutlined",
        "下载 Markdown",
        "agent-followups",
        "继续追问建议",
        "重试上一问",
        "重新连接",
    ):
        source = answer_meta if needle in {
            "已核对工具结果",
            "已标注数据窗口",
            "已包含风险提示",
        } else bubble
        ensure_contains(source, needle, "Agent answer actions")


def verify_no_agent_mojibake() -> None:
    paths = [
        "web/src/components/agent/ChatContainer.tsx",
        "web/src/components/agent/ChatInput.tsx",
        "web/src/components/agent/MessageBubble.tsx",
        "web/src/components/agent/ToolCallCard.tsx",
        "web/src/components/agent/AgentStatusBar.tsx",
        "web/src/components/agent/answerMeta.ts",
        "web/src/stores/agent.ts",
    ]
    bad_tokens = ("鎵", "鐮", "锛", "鈥", "銆", "閫", "椋", "绛", "鍥")
    for path in paths:
      text = read(path)
      for token in bad_tokens:
          ensure_not_contains(text, token, f"Agent mojibake in {path}")


def main() -> None:
    verify_agent_page_mount()
    verify_agent_session_ui()
    verify_agent_store_contract()
    verify_tool_cards()
    verify_message_actions()
    verify_no_agent_mojibake()
    print("Agent frontend contract passed.")


if __name__ == "__main__":
    main()
