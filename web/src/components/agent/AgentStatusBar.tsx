import { Tag } from 'antd';
import { CheckCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import type { AgentStatus, AgentToolCall } from '../../types';

type AgentStatusBarProps = {
  agents: AgentStatus[];
  toolCalls: AgentToolCall[];
  isStreaming: boolean;
  currentRequest: string | null;
  hasAnswer: boolean;
};

export default function AgentStatusBar({
  agents,
  toolCalls,
  isStreaming,
  currentRequest,
  hasAnswer,
}: AgentStatusBarProps) {
  if (!isStreaming && agents.length === 0 && toolCalls.length === 0) return null;

  const completedTools = toolCalls.filter((tool) => tool.status === 'done').length;
  const failedTools = toolCalls.filter((tool) => tool.status === 'error').length;
  const workingAgents = agents.filter((agent) => agent.status === 'working').length;
  const stage = (() => {
    if (toolCalls.some((tool) => tool.status === 'running')) {
      return '正在读取数据和指标';
    }
    if (isStreaming && toolCalls.length > 0 && !hasAnswer) {
      return '正在整理工具结果';
    }
    if (isStreaming && !hasAnswer) {
      return '正在规划研究步骤';
    }
    return isStreaming ? '正在生成研究结论' : '研究过程已完成';
  })();

  return (
    <div className="agent-status-bar">
      <div className="agent-process-summary" aria-label="Agent 研究进度">
        <span className={isStreaming ? 'working' : 'done'}>{stage}</span>
        {currentRequest && <strong title={currentRequest}>{currentRequest}</strong>}
        <em>
          {agents.length ? `${workingAgents}/${agents.length} 位专家处理中` : '等待专家响应'}
        </em>
        <em>
          工具 {completedTools}/{toolCalls.length}
          {failedTools ? `，失败 ${failedTools}` : ''}
        </em>
      </div>
      {agents.map((agent) => (
        <Tag
          key={agent.name}
          icon={
            agent.status === 'working' ? (
              <LoadingOutlined spin />
            ) : (
              <CheckCircleOutlined />
            )
          }
          color={agent.status === 'working' ? 'processing' : 'success'}
        >
          {agent.displayName}
          {agent.task ? ` · ${agent.task}` : ''}
        </Tag>
      ))}
    </div>
  );
}
