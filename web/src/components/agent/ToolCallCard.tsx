import { Spin, Tag } from 'antd';
import type { ReactNode } from 'react';
import {
  BarChartOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  LineChartOutlined,
  LoadingOutlined,
  SearchOutlined,
  UnorderedListOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { AgentToolCall } from '../../types';

type ToolFact = {
  label: string;
  value: string;
  tone?: 'good' | 'bad' | 'neutral';
};

const TOOL_LABELS: Record<string, { label: string; icon: ReactNode }> = {
  run_backtest_tool: { label: '执行回测', icon: <BarChartOutlined /> },
  list_strategies_tool: { label: '读取策略库', icon: <UnorderedListOutlined /> },
  compare_backtests_tool: { label: '对比回测', icon: <BarChartOutlined /> },
  screen_stocks_tool: { label: '条件选股', icon: <SearchOutlined /> },
  get_kline_data_tool: { label: '读取 K 线', icon: <LineChartOutlined /> },
  list_cached_stocks_tool: { label: '检查本地缓存', icon: <DatabaseOutlined /> },
  get_all_a_stock_list_tool: { label: '读取 A 股列表', icon: <DatabaseOutlined /> },
  resolve_stock_symbol_tool: { label: '识别股票', icon: <SearchOutlined /> },
  analyze_technicals_tool: { label: '技术指标分析', icon: <LineChartOutlined /> },
};

const AGENT_LABELS: Record<string, string> = {
  supervisor: '研究主管',
  quant_agent: 'Quant Agent',
  market_agent: '行情专家',
  screening_agent: '选股专家',
  backtest_agent: '回测专家',
};

const STATUS_LABELS: Record<
  AgentToolCall['status'],
  { label: string; color: string }
> = {
  running: { label: '进行中', color: 'processing' },
  done: { label: '已完成', color: 'success' },
  error: { label: '失败', color: 'error' },
};

const INPUT_LABELS: Record<string, string> = {
  symbol: '标的',
  symbols: '标的',
  strategy: '策略',
  start_date: '开始',
  end_date: '结束',
  lookback: '窗口',
  top_n: '数量',
  source: '来源',
};

function StatusIcon({ status }: { status: AgentToolCall['status'] }) {
  if (status === 'running') {
    return <Spin indicator={<LoadingOutlined spin />} size="small" />;
  }
  if (status === 'done') {
    return <CheckCircleOutlined style={{ color: '#0ecb81' }} />;
  }
  return <WarningOutlined style={{ color: '#f6465d' }} />;
}

function formatPercent(value: unknown) {
  if (typeof value !== 'number' || Number.isNaN(value)) return null;
  return `${(value * 100).toFixed(2)}%`;
}

function formatNumber(value: unknown) {
  if (typeof value !== 'number' || Number.isNaN(value)) return null;
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function formatText(value: unknown) {
  if (value === null || value === undefined || value === '') return '-';
  if (Array.isArray(value)) return value.slice(0, 4).join('、');
  if (typeof value === 'object') return '已设置';
  return String(value);
}

function toneFromNumber(value: unknown): ToolFact['tone'] {
  return typeof value === 'number' && !Number.isNaN(value)
    ? value >= 0
      ? 'good'
      : 'bad'
    : 'neutral';
}

function toolFacts(tool: string, result?: Record<string, unknown>): ToolFact[] {
  if (!result || result.error || result.detail) return [];
  if (tool === 'run_backtest_tool') {
    const metrics = result.metrics as Record<string, unknown> | undefined;
    if (!metrics) return [];
    return [
      { label: '总收益', value: formatPercent(metrics.total_return) ?? '-', tone: toneFromNumber(metrics.total_return) },
      { label: '年化', value: formatPercent(metrics.annual_return) ?? '-', tone: toneFromNumber(metrics.annual_return) },
      { label: '最大回撤', value: formatPercent(metrics.max_drawdown) ?? '-', tone: 'bad' },
      { label: '胜率', value: formatPercent(metrics.win_rate) ?? '-' },
      { label: '交易次数', value: formatText(result.trade_count ?? metrics.trade_count ?? 0) },
    ];
  }
  if (tool === 'screen_stocks_tool') {
    return [
      { label: '扫描股票', value: formatNumber(result.total_scanned) ?? '0' },
      { label: '命中', value: formatNumber(result.match_count) ?? '0', tone: Number(result.match_count ?? 0) > 0 ? 'good' : 'neutral' },
      { label: '策略', value: formatText(result.strategy) },
      { label: '日期', value: formatText(result.scan_date) },
    ];
  }
  if (tool === 'get_kline_data_tool') {
    return [
      { label: '标的', value: formatText(result.name || result.symbol) },
      { label: 'K 线', value: formatNumber(result.bar_count) ?? '0' },
      { label: '开始', value: formatText(result.start_date || result.start) },
      { label: '截至', value: formatText(result.data_as_of || result.end_date || result.end) },
    ];
  }
  if (tool === 'analyze_technicals_tool') {
    const trendSignals = Array.isArray(result.trend_signals)
      ? result.trend_signals.length
      : 0;
    return [
      { label: '标的', value: formatText(result.name || result.symbol) },
      { label: '收盘价', value: formatText(result.latest_close ?? result.close) },
      { label: '趋势信号', value: `${trendSignals} 条` },
      { label: '截至', value: formatText(result.data_as_of || result.end_date) },
    ];
  }
  if (tool === 'list_cached_stocks_tool') {
    return [
      { label: '缓存股票', value: formatNumber(result.total_cached) ?? '0', tone: Number(result.total_cached ?? 0) > 0 ? 'good' : 'neutral' },
      { label: '最早日期', value: formatText(result.earliest_start || result.start) },
      { label: '最新日期', value: formatText(result.latest_end || result.end) },
    ];
  }
  if (tool === 'get_all_a_stock_list_tool') {
    return [
      { label: 'A 股数量', value: formatNumber(result.total) ?? '0' },
      { label: '来源', value: formatText(result.source) },
    ];
  }
  if (tool === 'resolve_stock_symbol_tool') {
    return [
      { label: '代码', value: formatText(result.symbol) },
      { label: '名称', value: formatText(result.name) },
      { label: '市场', value: formatText(result.market) },
    ];
  }
  return [];
}

function summarizeResult(tool: string, result?: Record<string, unknown>) {
  if (!result) return '正在调用工具...';
  if (result.error || result.detail) {
    return `工具执行失败：${String(result.error ?? result.detail)}`;
  }
  if (tool === 'run_backtest_tool') {
    const metrics = result.metrics as Record<string, unknown> | undefined;
    if (!metrics) return '回测已完成，但没有返回核心指标';
    return [
      `总收益 ${formatPercent(metrics.total_return) ?? '-'}`,
      `年化 ${formatPercent(metrics.annual_return) ?? '-'}`,
      `最大回撤 ${formatPercent(metrics.max_drawdown) ?? '-'}`,
      `交易 ${result.trade_count ?? metrics.trade_count ?? 0} 次`,
    ].join(' · ');
  }
  if (tool === 'compare_backtests_tool') {
    const rows = Array.isArray(result.result) ? result.result : undefined;
    return rows ? `已完成 ${rows.length} 组回测对比` : '回测对比已完成';
  }
  if (tool === 'screen_stocks_tool') {
    const matchCount = Number(result.match_count ?? 0);
    return [
      `扫描 ${formatNumber(result.total_scanned) ?? 0} 只`,
      matchCount > 0 ? `命中 ${formatNumber(matchCount) ?? 0} 只` : '暂无命中',
      result.scan_date ? `日期 ${result.scan_date}` : null,
    ].filter(Boolean).join(' · ');
  }
  if (tool === 'get_kline_data_tool') {
    const bars = Number(result.bar_count ?? 0);
    return bars === 0
      ? `${result.name || result.symbol || '标的'} 暂无可用 K 线数据`
      : `${result.name || result.symbol || '标的'} · ${formatNumber(bars) ?? 0} 条 K 线`;
  }
  if (tool === 'analyze_technicals_tool') {
    const signals = Array.isArray(result.trend_signals)
      ? result.trend_signals.slice(0, 2).join('，')
      : '';
    return `${result.name || result.symbol || '标的'} 技术指标已计算${signals ? ` · ${signals}` : ''}`;
  }
  if (tool === 'list_cached_stocks_tool') {
    return `本地已缓存 ${formatNumber(result.total_cached) ?? 0} 只股票`;
  }
  if (tool === 'get_all_a_stock_list_tool') {
    return `A 股列表 ${formatNumber(result.total) ?? 0} 只`;
  }
  if (tool === 'resolve_stock_symbol_tool') {
    return result.symbol
      ? `${result.name || ''} ${result.symbol}`.trim()
      : '股票识别已完成';
  }
  if (tool === 'list_strategies_tool') {
    const rows = Array.isArray(result.result) ? result.result : undefined;
    return rows ? `读取到 ${rows.length} 个策略` : '策略库已读取';
  }
  return '工具执行完成';
}

function inputSummary(input: Record<string, unknown>) {
  const entries = Object.entries(input)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .slice(0, 5);
  if (!entries.length) return '';
  return entries
    .map(([key, value]) => `${INPUT_LABELS[key] ?? key}：${formatText(value)}`)
    .join(' · ');
}

export default function ToolCallCard({ toolCall }: { toolCall: AgentToolCall }) {
  const info = TOOL_LABELS[toolCall.tool] ?? {
    label: toolCall.tool,
    icon: null,
  };
  const status = STATUS_LABELS[toolCall.status];
  const facts = toolFacts(toolCall.tool, toolCall.result);
  const input = inputSummary(toolCall.input);

  return (
    <div className={`agent-tool-card ${toolCall.status}`}>
      <div className="agent-tool-title">
        <StatusIcon status={toolCall.status} />
        <span>{info.icon}</span>
        <strong>{info.label}</strong>
        <Tag className="agent-tool-status" color={status.color}>
          {status.label}
        </Tag>
        {toolCall.agent && (
          <Tag>{AGENT_LABELS[toolCall.agent] ?? toolCall.agent}</Tag>
        )}
      </div>
      <div className="agent-tool-summary">
        {summarizeResult(toolCall.tool, toolCall.result)}
      </div>
      {facts.length > 0 && (
        <div className="agent-tool-facts" aria-label="工具关键结果">
          {facts.map((fact) => (
            <div key={`${fact.label}-${fact.value}`} className={fact.tone ?? 'neutral'}>
              <span>{fact.label}</span>
              <strong>{fact.value}</strong>
            </div>
          ))}
        </div>
      )}
      {input && <div className="agent-tool-input">{input}</div>}
    </div>
  );
}
