export interface ParamSchema {
  name: string;
  type: string;
  default: number;
  min: number;
  max: number;
  label: string;
}

export interface StrategyInfo {
  name: string;
  display_name: string;
  params_schema: ParamSchema[];
}

export interface StrategyAssetDraft {
  name: string;
  description: string;
  base_strategy: string;
  params: Record<string, number>;
  tags: string[];
  enabled: boolean;
}

export interface StrategyAsset extends StrategyAssetDraft {
  id: string;
  created_at: string;
  updated_at: string;
}

export interface PerformanceMetrics {
  initial_cash: number;
  final_equity: number;
  total_return: number;
  annual_return: number;
  max_drawdown: number;
  trade_count: number;
  total_commission: number;
  win_rate: number | null;
  sharpe_ratio: number | null;
  profit_loss_ratio: number | null;
}

export interface TradeRecord {
  dt: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  commission: number;
}

export interface EquityPoint {
  dt: string;
  equity: number;
}

export interface KlineBar {
  dt: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface BacktestRequest {
  symbols: string[];
  start_date: string;
  end_date: string;
  strategy: string;
  strategy_params: Record<string, number>;
  initial_cash: number;
  max_position_pct: number;
  max_drawdown: number;
  commission_rate: number;
}

export interface BacktestResult {
  metrics: PerformanceMetrics;
  equity_curve: EquityPoint[];
  trades: TradeRecord[];
  kline_data: Record<string, KlineBar[]>;
}

export type BacktestGridSortKey =
  | 'total_return'
  | 'annual_return'
  | 'max_drawdown'
  | 'sharpe_ratio'
  | 'win_rate'
  | 'final_equity';

export interface BacktestGridRequest {
  base: BacktestRequest;
  parameters: Record<string, Array<number | string | boolean | null>>;
  max_runs: number;
  sort_by: BacktestGridSortKey;
  sort_order: 'asc' | 'desc';
}

export interface BacktestGridItem {
  status: 'completed' | 'failed';
  strategy_params: Record<string, number | string | boolean | null>;
  request: BacktestRequest;
  metrics: PerformanceMetrics | null;
  run_id: string | null;
  error: string | null;
}

export interface BacktestGridResult {
  requested: number;
  completed: number;
  failed: number;
  sort_by: BacktestGridSortKey;
  sort_order: 'asc' | 'desc';
  best: BacktestGridItem | null;
  results: BacktestGridItem[];
}

// ── Screening ──

export interface ScreenRequest {
  strategy: string;
  strategy_params: Record<string, number>;
  scan_date?: string;
  lookback?: number;
}

export interface ScreenMatch {
  symbol: string;
  signal_date: string;
  close: number;
  volume: number;
  amount: number;
  strength: number;
}

export interface ScreenResult {
  strategy: string;
  scan_date: string;
  total_scanned: number;
  matches: ScreenMatch[];
  elapsed_seconds: number;
}

// ── Multi-Factor Scoring ──

export interface FactorWeights {
  trend: number;
  momentum: number;
  volume: number;
  dip: number;
  risk: number;
}

export interface FactorScoreItem {
  trend: number;
  momentum: number;
  volume: number;
  dip: number;
  risk: number;
}

export interface ScoredStock {
  symbol: string;
  score: number;
  rating: string;
  factors: FactorScoreItem;
  reasons: string[];
  warnings: string[];
  signal_date: string;
  close: number;
  pct_chg: number;
  volume: number;
  amount: number;
  sandglass: number;
  wave: string;
  kirin: string;
}

export interface ScoreResult {
  scan_date: string;
  total_scanned: number;
  total_matched: number;
  returned: number;
  stocks: ScoredStock[];
  elapsed_seconds: number;
}

export interface ScoreRequest {
  scan_date?: string;
  lookback?: number;
  weights?: FactorWeights;
  exclude_centipede?: boolean;
  min_sandglass?: number;
  min_amount?: number;
  min_price?: number;
  use_patterns?: boolean;
  top_n?: number;
  max_symbols?: number;
}

export interface FactorDef {
  key: string;
  label: string;
  default_weight: number;
  desc: string;
}

export interface ManagedFactorDraft {
  key: string;
  label: string;
  category: string;
  description: string;
  expression: string;
  default_weight: number;
  enabled: boolean;
}

export interface ManagedFactor extends ManagedFactorDraft {
  id: string;
  source: 'builtin' | 'custom';
  created_at: string;
  updated_at: string;
}

export interface FactorMiningRequest {
  symbols?: string[] | null;
  lookback: number;
  forward_days: number;
  min_samples: number;
}

export interface FactorMiningItem {
  key: string;
  label: string;
  category: string;
  samples: number;
  ic: number | null;
  abs_ic: number | null;
  coverage: number;
  direction: string;
}

export interface FactorMiningResult {
  symbols: number;
  lookback: number;
  forward_days: number;
  items: FactorMiningItem[];
  warnings: string[];
}

export interface RiskRuleDraft {
  name: string;
  description: string;
  max_position_pct: number;
  max_drawdown: number;
  max_single_order_pct: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  max_symbols: number;
  enabled: boolean;
}

export interface RiskRule extends RiskRuleDraft {
  id: string;
  created_at: string;
  updated_at: string;
}

export interface RiskEvaluationCheck {
  key: string;
  label: string;
  passed: boolean;
  message: string;
  severity: string;
}

export interface RiskEvaluationResult {
  passed: boolean;
  rule: RiskRuleDraft;
  checks: RiskEvaluationCheck[];
}

// ── Agent ──

export type AgentMode =
  | 'auto'
  | 'quant'
  | 'market'
  | 'screening'
  | 'backtest';

export interface AgentModeOption {
  key: AgentMode;
  label: string;
  agent: string;
}

export interface AgentToolCall {
  id: string;
  tool: string;
  agent?: string;
  input: Record<string, unknown>;
  result?: Record<string, unknown>;
  status: 'running' | 'done' | 'error';
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  images?: string[];
  toolCalls?: AgentToolCall[];
  agentName?: string;
  timestamp: number;
}

export interface AgentStatus {
  name: string;
  displayName: string;
  status: 'working' | 'done';
  task?: string;
}

export interface ServerFrame {
  type: string;
  session_id?: string;
  agent?: string;
  content?: string;
  tool?: string;
  tool_call_id?: string;
  input?: Record<string, unknown>;
  data?: Record<string, unknown>;
  usage?: Record<string, unknown>;
  error?: string;
}

export interface SessionSummary {
  session_id: string;
  message_count: number;
  created_at: string;
  last_active: string;
}

export interface AgentRuntimeStatus {
  enabled: boolean;
  provider: string;
  model: string;
  configured: boolean;
  reason?: string | null;
  modes?: AgentModeOption[];
}

export interface CompositeMetricParam {
  key: string;
  label: string;
  default: number;
  min: number;
  max: number;
  step: number;
}

export interface CompositeMetricDef {
  key: string;
  label: string;
  category: string;
  description: string;
  unit: string;
  value_type: 'number' | 'category';
  operators: string[];
  params: CompositeMetricParam[];
  options: string[];
  source: 'kline' | 'daily_basic';
}

export interface CompositeCondition {
  id: string;
  metric: string;
  operator: string;
  value: number | string | null;
  value2?: number | null;
  compare_metric?: string | null;
  params: Record<string, number>;
  periods: number;
  weight: number;
  required: boolean;
  enabled: boolean;
}

export interface CompositeGroup {
  id: string;
  name: string;
  logic: 'all' | 'any';
  conditions: CompositeCondition[];
}

export interface FactorStrategyDraft {
  name: string;
  description: string;
  logic: 'all' | 'any';
  groups: CompositeGroup[];
  min_score: number;
  top_n: number;
  lookback: number;
}

export interface FactorStrategy extends FactorStrategyDraft {
  id: string;
  created_at: string;
  updated_at: string;
}

export interface CompositeStock {
  symbol: string;
  matched: boolean;
  score: number;
  passed_conditions: number;
  available_conditions: number;
  total_conditions: number;
  signal_date: string;
  close: number;
  pct_chg: number;
  volume: number;
  amount: number;
  turnover_rate?: number | null;
  reasons: string[];
  failures: string[];
  values: Record<string, {
    metric: string;
    value: number | string | null;
    target: number | string | null;
    passed: boolean;
    available: boolean;
  }>;
}

export interface CompositeScanResult {
  strategy_id?: string | null;
  strategy_name: string;
  scan_date: string;
  total_scanned: number;
  total_matched: number;
  returned: number;
  stocks: CompositeStock[];
  elapsed_seconds: number;
  warnings: string[];
}
