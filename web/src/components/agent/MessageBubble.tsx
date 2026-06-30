import { useState, type ReactNode } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button, Tooltip } from 'antd';
import {
  CheckOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DownloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { ChatMessage } from '../../types';
import { useAgentStore } from '../../stores/agent';
import ToolCallCard from './ToolCallCard';
import {
  answerAssuranceItems,
  answerOutline,
  isRecoveryMessage,
  type AnswerAssuranceItem,
} from './answerMeta';

function followUpSuggestions(message: ChatMessage) {
  if (message.role === 'user' || !message.content.trim()) return [];
  if (isRecoveryMessage(message.content)) return [];
  const content = message.content;
  const tools = new Set(message.toolCalls?.map((toolCall) => toolCall.tool) ?? []);
  const suggestions: string[] = [];

  const hasToolContext = tools.size > 0;
  const isCacheAnswer =
    tools.has('list_cached_stocks_tool') ||
    tools.has('get_all_a_stock_list_tool') ||
    (!hasToolContext && /缓存概况|缓存股票|股票池|数据覆盖|数据质量评估/.test(content));
  const isBacktestAnswer =
    tools.has('run_backtest_tool') ||
    tools.has('compare_backtests_tool') ||
    (!hasToolContext && /回测结论|交易质量|最大回撤|夏普|胜率/.test(content));
  const isScreeningAnswer =
    tools.has('screen_stocks_tool') ||
    (!hasToolContext && /筛选结论|扫描结果|命中|候选股|入选原因/.test(content));
  const isMarketAnswer =
    tools.has('get_kline_data_tool') ||
    tools.has('analyze_technicals_tool') ||
    (!hasToolContext && /KDJ|RSI|BBI|均线|成交量|支撑|压力/.test(content));

  if (isCacheAnswer) {
    suggestions.push('检查数据缺口和最新交易日覆盖');
    suggestions.push('用当前缓存扫描一个策略信号');
  }
  if (isBacktestAnswer) {
    suggestions.push('继续给出 3 组可优化参数');
    suggestions.push('解释这次回测的主要失效原因');
  }
  if (isScreeningAnswer) {
    suggestions.push('解释排名前 5 的入选原因');
    suggestions.push('帮我收紧筛选条件，减少误判');
  }
  if (isMarketAnswer) {
    suggestions.push('列出下一步观察价位和触发条件');
    suggestions.push('把主要风险和失效条件讲具体');
  }
  suggestions.push('整理成明天可执行的检查清单');

  return Array.from(new Set(suggestions)).slice(0, 3);
}

function assuranceIcon(item: AnswerAssuranceItem): ReactNode {
  if (item.key === 'tool') return <CheckOutlined />;
  if (item.key === 'data') return <DatabaseOutlined />;
  return <SafetyCertificateOutlined />;
}

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const [copied, setCopied] = useState(false);
  const store = useAgentStore();
  const isUser = message.role === 'user';
  const canCopy = !isUser && Boolean(message.content.trim());
  const outline = !isUser ? answerOutline(message.content) : [];
  const assuranceItems = !isUser ? answerAssuranceItems(message) : [];
  const isRecovery = !isUser && isRecoveryMessage(message.content);
  const suggestions = followUpSuggestions(message);
  const followUpDisabled = store.isStreaming || store.runtime?.enabled === false;

  const copyAnswer = async () => {
    if (!canCopy) return;
    try {
      await navigator.clipboard.writeText(message.content.trim());
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  const downloadAnswer = () => {
    if (!canCopy) return;
    const timestamp = new Date(message.timestamp)
      .toISOString()
      .replace(/[:.]/g, '-');
    const blob = new Blob([message.content.trim()], {
      type: 'text/markdown;charset=utf-8',
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `quantlab-agent-${timestamp}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className={`agent-message ${isUser ? 'user' : ''}`}>
      <div className="agent-avatar">
        {isUser ? <UserOutlined /> : <RobotOutlined />}
      </div>
      <div className="agent-bubble-wrap">
        {message.images?.length ? (
          <div className="agent-message-images">
            {message.images.map((image, index) => (
              <img
                key={`${image.slice(0, 16)}-${index}`}
                src={`data:image/png;base64,${image}`}
                alt="用户上传的分析图片"
              />
            ))}
          </div>
        ) : null}
        {message.toolCalls?.map((toolCall) => (
          <ToolCallCard key={toolCall.id} toolCall={toolCall} />
        ))}
        {message.content && (
          <div className="agent-bubble">
            {isUser ? (
              <span style={{ whiteSpace: 'pre-wrap' }}>{message.content}</span>
            ) : (
              <>
                {assuranceItems.length > 0 && (
                  <div className="agent-answer-assurance" aria-label="回答质量确认">
                    {assuranceItems.map((item) => (
                      <span key={item.label}>
                        {assuranceIcon(item)}
                        {item.label}
                      </span>
                    ))}
                  </div>
                )}
                {outline.length > 1 && (
                  <div className="agent-answer-outline" aria-label="回答结构">
                    {outline.map((title) => (
                      <span key={title}>{title}</span>
                    ))}
                  </div>
                )}
                <div className="agent-markdown">
                  <Markdown remarkPlugins={[remarkGfm]}>
                    {message.content}
                  </Markdown>
                </div>
                {isRecovery && (
                  <div className="agent-recovery-card" aria-label="错误恢复操作">
                    <span>这次分析没有完整完成，可以直接恢复</span>
                    <div>
                      <Button
                        size="small"
                        onClick={store.retryLastMessage}
                        disabled={followUpDisabled}
                      >
                        重试上一问
                      </Button>
                      <Button
                        size="small"
                        type="text"
                        onClick={store.reconnect}
                        disabled={store.runtime?.enabled === false}
                      >
                        重新连接
                      </Button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}
        <div className="agent-time">
          {new Date(message.timestamp).toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
          })}
          {canCopy && (
            <>
              <Tooltip title={copied ? '已复制' : '复制回答'}>
                <Button
                  type="text"
                  size="small"
                  className="agent-message-action"
                  icon={copied ? <CheckOutlined /> : <CopyOutlined />}
                  onClick={copyAnswer}
                  aria-label={copied ? '已复制回答' : '复制回答'}
                />
              </Tooltip>
              <Tooltip title="下载 Markdown">
                <Button
                  type="text"
                  size="small"
                  className="agent-message-action"
                  icon={<DownloadOutlined />}
                  onClick={downloadAnswer}
                  aria-label="下载 Markdown"
                />
              </Tooltip>
            </>
          )}
        </div>
        {suggestions.length > 0 && (
          <div className="agent-followups" aria-label="继续追问建议">
            {suggestions.map((suggestion) => (
              <button
                type="button"
                key={suggestion}
                disabled={followUpDisabled}
                onClick={() => store.sendMessage(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
