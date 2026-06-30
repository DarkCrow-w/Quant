import { useEffect, useRef, type ReactNode } from 'react';
import { Button, Popconfirm, Select, Spin, Tag, Tooltip } from 'antd';
import {
  ApartmentOutlined,
  BarChartOutlined,
  BulbOutlined,
  CheckOutlined,
  DatabaseOutlined,
  LineChartOutlined,
  LoadingOutlined,
  PlusOutlined,
  RadarChartOutlined,
  RedoOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAgentStore } from '../../stores/agent';
import AgentStatusBar from './AgentStatusBar';
import ChatInput from './ChatInput';
import MessageBubble from './MessageBubble';
import ToolCallCard from './ToolCallCard';
import type { AgentMode, ChatMessage } from '../../types';
import {
  answerAssuranceItems,
  answerOutline,
  type AnswerAssuranceItem,
} from './answerMeta';

const modeFallbacks = [
  { key: 'auto', label: '自动协作', agent: 'supervisor' },
  { key: 'quant', label: 'Quant Agent', agent: 'quant_agent' },
  { key: 'market', label: '行情分析', agent: 'market_agent' },
  { key: 'screening', label: '智能选股', agent: 'screening_agent' },
  { key: 'backtest', label: '策略回测', agent: 'backtest_agent' },
] as const;

const modeIcons: Record<AgentMode, ReactNode> = {
  auto: <ApartmentOutlined />,
  quant: <BulbOutlined />,
  market: <LineChartOutlined />,
  screening: <RadarChartOutlined />,
  backtest: <BarChartOutlined />,
};

const modeDescriptions: Record<AgentMode, string> = {
  auto: '自动选择行情、选股、回测和综合研究专家，适合开放式量化问题。',
  quant: '把投资想法拆成数据、假设、验证和风险控制，形成可复核的研究链。',
  market: '专注 K 线、成交量、趋势、技术指标、支撑与压力。',
  screening: '专注策略扫描、候选排序、信号解释和下一步验证清单。',
  backtest: '专注策略回测、参数对比、收益归因和风险评估。',
};

function assuranceIcon(item: AnswerAssuranceItem): ReactNode {
  if (item.key === 'tool') return <CheckOutlined />;
  if (item.key === 'data') return <DatabaseOutlined />;
  return <SafetyCertificateOutlined />;
}

type PromptCard = readonly [title: string, detail: string, prompt: string];

const promptsByMode: Record<AgentMode, PromptCard[]> = {
  auto: [
    [
      '诊断一只股票',
      '自动读取行情和指标，输出可以复核的结论。',
      '请分析 000001 最近 120 个交易日的趋势、成交量、KDJ、RSI、MACD 和 BBI，并提示主要风险。',
    ],
    [
      '寻找市场机会',
      '先扫描候选，再解释筛选逻辑和下一步验证。',
      '请用 ma_cross 策略扫描当前缓存市场，lookback 80，给我前 10 个候选和简短解释。',
    ],
    [
      '设计波段策略',
      '把自然语言想法转换为可验证的指标组合。',
      '请基于 KDJ、RSI、成交量和 BBI，设计一个波段抄底策略，并给出买入、卖出和风控条件。',
    ],
    [
      '复盘策略表现',
      '调用回测工具，整理收益、回撤和交易质量。',
      '请用 ma_cross 策略回测 000001，区间 2025-01-01 到 2026-06-29，参数 fast_period=5 slow_period=20，并输出核心指标。',
    ],
  ],
  quant: [
    [
      '形成研究框架',
      '把投资问题拆成数据、假设、验证和风控。',
      '请围绕 000001 设计一个完整研究框架：趋势、动量、量价、风险、验证条件和失效条件。',
    ],
    [
      '组合证据链',
      '联动行情、指标、选股和回测形成可验证判断。',
      '请用 Quant 视角分析 000001 是否适合做波段观察，并说明还需要哪些数据验证。',
    ],
    [
      '策略想法落地',
      '把自然语言策略转成可执行指标条件。',
      '请把“低位缩量企稳后放量突破 BBI”的想法转成买入、卖出、止损和过滤条件。',
    ],
    [
      '每日检查清单',
      '输出可重复执行的研究流程。',
      '请给我一套每天收盘后执行的波段交易检查清单，包含数据、信号、风险和复盘。',
    ],
  ],
  market: [
    [
      '分析单股走势',
      '读取 K 线和技术指标，给出多空倾向。',
      '请分析 000001 最近 60 个交易日的趋势、成交量、KDJ、RSI、MACD 和 BBI。',
    ],
    [
      '找支撑压力',
      '用近期价格和均线结构定位观察位。',
      '请分析 000001 最近 120 个交易日的支撑位、压力位和关键失效条件。',
    ],
    [
      '解释指标变化',
      '把技术指标翻译成普通用户能理解的话。',
      '请解释 000001 当前 KDJ、BBI 和成交量分别说明了什么，结论要简短。',
    ],
    [
      '检查缓存数据',
      '查看本地行情覆盖情况。',
      '请列出当前本地缓存股票概况，并说明数据是否足够支持回测和选股。',
    ],
  ],
  screening: [
    [
      '扫描均线信号',
      '用策略扫描当前缓存市场。',
      '请用 ma_cross 策略扫描当前缓存市场，lookback 80，给我前 10 个候选。',
    ],
    [
      '解释候选逻辑',
      '把命中原因和下一步验证讲清楚。',
      '请扫描 ma_cross 信号，并解释排名前 5 的候选为什么入选、还需要验证什么。',
    ],
    [
      '寻找低位机会',
      '围绕抄底策略寻找候选。',
      '请用 swing_dip_buy 或 dip_buy 思路扫描缓存市场，给我候选和风险提示。',
    ],
    [
      '收紧筛选条件',
      '给出从宽到严的二次过滤思路。',
      '如果 ma_cross 命中太多，请给我一套二次过滤条件，重点控制成交量、趋势和回撤风险。',
    ],
  ],
  backtest: [
    [
      '回测均线策略',
      '输出收益、回撤、胜率和交易次数。',
      '请用 ma_cross 策略回测 000001，区间 2025-01-01 到 2026-06-29，参数 fast_period=5 slow_period=20。',
    ],
    [
      '评估抄底策略',
      '用回测证据判断是否值得继续验证。',
      '请用 swing_dip_buy 策略回测 000001，输出核心指标、交易质量和需要改进的条件。',
    ],
    [
      '比较参数组合',
      '关注收益和最大回撤的平衡。',
      '请对 ma_cross 的 fast_period 和 slow_period 做小范围参数对比，并按夏普和回撤排序。',
    ],
    [
      '复盘交易质量',
      '从交易次数、胜率和盈亏比判断策略稳定性。',
      '请回测 000001 的 ma_cross 策略，并重点解释交易质量、失效条件和下一步优化方向。',
    ],
  ],
};

export default function ChatContainer() {
  const store = useAgentStore();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const bootAgent = async () => {
      const runtime = await store.loadRuntime();
      if (!cancelled && runtime?.enabled) {
        store.connect();
      }
    };
    void bootAgent();
    return () => {
      cancelled = true;
      store.disconnect();
    };
    // Store actions are stable Zustand functions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [store.messages, store.streamingContent, store.pendingToolCalls]);

  const providerLabel =
    store.runtime?.provider === 'deepseek'
      ? 'DeepSeek'
      : store.runtime?.provider === 'anthropic'
        ? 'Anthropic'
        : 'Agent';
  const runtimeUnavailableReason =
    store.runtime && !store.runtime.enabled
      ? store.runtime.reason || '请先配置 Agent 模型 API Key'
      : null;
  const visibleError = runtimeUnavailableReason || store.connectionError;
  const currentModeLabel =
    (store.runtime?.modes ?? modeFallbacks).find(
      (mode) => mode.key === store.selectedMode,
    )?.label ?? modeFallbacks.find((mode) => mode.key === store.selectedMode)?.label;
  const hasRestoredConversation = store.messages.length > 0;
  const canRetryLastMessage =
    !store.isStreaming &&
    store.runtime?.enabled !== false &&
    store.messages.some((message) => message.role === 'user');
  const promptCards = promptsByMode[store.selectedMode] ?? promptsByMode.auto;
  const streamingMessage: ChatMessage | null = store.streamingContent
    ? {
        id: 'streaming',
        role: 'assistant',
        content: store.streamingContent,
        timestamp: Date.now(),
        toolCalls: store.pendingToolCalls.length
          ? store.pendingToolCalls
          : undefined,
      }
    : null;
  const streamingOutline = streamingMessage
    ? answerOutline(streamingMessage.content)
    : [];
  const streamingAssuranceItems = streamingMessage
    ? answerAssuranceItems(streamingMessage)
    : [];

  return (
    <div className="agent-shell">
      <header className="agent-header">
        <div>
          <h1>AI 量化研究员</h1>
          <p>{modeDescriptions[store.selectedMode]}</p>
        </div>
        <div className="agent-header-actions">
          <Select
            className="agent-mode-select"
            value={store.selectedMode}
            disabled={store.isStreaming}
            onChange={(value: AgentMode) => store.setAgentMode(value)}
            options={(store.runtime?.modes ?? modeFallbacks).map((mode) => ({
              value: mode.key,
              label: (
                <span className="agent-mode-option">
                  {modeIcons[mode.key]}
                  {mode.label}
                </span>
              ),
            }))}
            aria-label="选择 AI Agent"
          />
          {store.runtime && (
            <Tag color={store.runtime.enabled ? 'blue' : 'error'}>
              {providerLabel} / {store.runtime.model}
            </Tag>
          )}
          <div
            className={`connection-state ${
              store.connectionState === 'connected' ? 'connected' : ''
            }`}
          >
            <i />
            {runtimeUnavailableReason
              ? '模型未配置'
              : store.connectionState === 'connected'
                ? '研究服务在线'
                : store.connectionState === 'connecting'
                  ? '正在连接'
                  : '研究服务离线'}
          </div>
          {store.connectionState !== 'connected' && (
            <Tooltip title="重新连接">
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={store.reconnect}
                aria-label="重新连接"
              />
            </Tooltip>
          )}
          <Button
            size="small"
            icon={<RedoOutlined />}
            onClick={store.retryLastMessage}
            disabled={!canRetryLastMessage}
          >
            重试上一问
          </Button>
          <Popconfirm
            title="清空当前对话？"
            description="当前模式的本地对话记录会被清空。"
            okText="清空"
            cancelText="保留"
            disabled={!hasRestoredConversation || store.isStreaming}
            onConfirm={store.clearSession}
          >
            <Button
              size="small"
              icon={<PlusOutlined />}
              onClick={
                hasRestoredConversation || store.isStreaming
                  ? undefined
                  : store.clearSession
              }
              disabled={store.isStreaming}
            >
              新对话
            </Button>
          </Popconfirm>
        </div>
      </header>

      {visibleError && (
        <div className="agent-connection-error">{visibleError}</div>
      )}
      <AgentStatusBar
        agents={store.activeAgents}
        toolCalls={store.pendingToolCalls}
        isStreaming={store.isStreaming}
        currentRequest={store.currentRequest}
        hasAnswer={Boolean(store.streamingContent)}
      />
      <div className="agent-session-strip">
        <span>
          {hasRestoredConversation
            ? `已恢复本地对话 · ${store.messages.length} 条消息`
            : '新对话'}
        </span>
        <span>{currentModeLabel}</span>
        {store.sessionId && (
          <span title={store.sessionId}>
            会话 {store.sessionId.slice(0, 8)}
          </span>
        )}
      </div>

      <div className="agent-scroll">
        {store.messages.length === 0 && !store.isStreaming && (
          <div className="agent-welcome">
            <div className="agent-welcome-inner">
              <div className="agent-welcome-mark">
                <RobotOutlined />
              </div>
              <h2>把量化研究问题交给协作式 AI 团队</h2>
              <p>
                {store.selectedMode === 'quant'
                  ? 'Quant Agent 会先核对研究周期、标的和假设，再调用真实数据工具形成可验证判断。'
                  : '你可以让主管自动调度，也可以直接选择专业 Agent。研究过程会展示工具调用、关键数据和最终结论。'}
              </p>
              <div className="prompt-grid">
                {promptCards.map(([title, detail, prompt]) => (
                  <button
                    type="button"
                    key={title}
                    onClick={() => store.sendMessage(prompt)}
                    disabled={store.isStreaming || store.runtime?.enabled === false}
                  >
                    <strong>{title}</strong>
                    <span>{detail}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {store.messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {store.isStreaming && (
          <div className="agent-message">
            <div className="agent-avatar">
              <RobotOutlined />
            </div>
            <div className="agent-bubble-wrap">
              {store.currentRequest && (
                <div className="agent-current-request">
                  <span>正在回答：</span>
                  <strong>{store.currentRequest}</strong>
                </div>
              )}
              {store.pendingToolCalls.map((toolCall) => (
                <ToolCallCard key={toolCall.id} toolCall={toolCall} />
              ))}
              {store.streamingContent ? (
                <div className="agent-bubble">
                  {streamingAssuranceItems.length > 0 && (
                    <div
                      className="agent-answer-assurance"
                      aria-label="实时回答质量确认"
                    >
                      {streamingAssuranceItems.map((item) => (
                        <span key={item.label}>
                          {assuranceIcon(item)}
                          {item.label}
                        </span>
                      ))}
                    </div>
                  )}
                  {streamingOutline.length > 1 && (
                    <div className="agent-answer-outline" aria-label="实时回答结构">
                      {streamingOutline.map((title) => (
                        <span key={title}>{title}</span>
                      ))}
                    </div>
                  )}
                  <div className="agent-markdown">
                    <Markdown remarkPlugins={[remarkGfm]}>
                      {store.streamingContent}
                    </Markdown>
                  </div>
                  <span className="streaming-cursor" />
                </div>
              ) : (
                <div className="agent-thinking">
                  <Spin indicator={<LoadingOutlined spin />} />
                  <span>研究主管正在拆解问题</span>
                </div>
              )}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <ChatInput />
    </div>
  );
}
