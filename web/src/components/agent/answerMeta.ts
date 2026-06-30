import type { ChatMessage } from '../../types';

export type AnswerAssuranceItem = {
  key: 'tool' | 'data' | 'risk';
  label: string;
};

export function answerOutline(content: string) {
  return content
    .split('\n')
    .map((line) => line.match(/^#{2,3}\s+(.+)$/)?.[1]?.trim())
    .filter((title): title is string => Boolean(title))
    .filter((title, index, titles) => titles.indexOf(title) === index)
    .slice(0, 5);
}

export function isRecoveryMessage(content: string) {
  return content.trimStart().startsWith('## 分析未完成');
}

export function answerAssuranceItems(message: ChatMessage): AnswerAssuranceItem[] {
  if (message.role === 'user' || !message.content.trim()) return [];
  if (isRecoveryMessage(message.content)) return [];

  const content = message.content;
  const hasToolEvidence = Boolean(
    message.toolCalls?.some((toolCall) => toolCall.status === 'done'),
  );
  const hasDataScope =
    /数据截至|观察窗口|样本|回测区间|扫描|lookback|参数|最近\s*\d+\s*(?:个)?交易日/.test(
      content,
    );
  const hasRiskFraming =
    /风险|限制|失效|不建议直接采用|需要继续验证|暂不具备|观察|回撤|止损|仓位|二次过滤|样本外|回测验证|不构成/.test(
      content,
    );

  const items: AnswerAssuranceItem[] = [];
  if (hasToolEvidence) {
    items.push({ key: 'tool', label: '已核对工具结果' });
  }
  if (hasDataScope) {
    items.push({ key: 'data', label: '已标注数据窗口' });
  }
  if (hasRiskFraming) {
    items.push({ key: 'risk', label: '已包含风险提示' });
  }
  return items;
}
