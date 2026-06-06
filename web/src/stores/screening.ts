import { create } from 'zustand';
import dayjs from 'dayjs';
import type {
  CompositeCondition,
  CompositeGroup,
  CompositeMetricDef,
  CompositeScanResult,
  FactorDef,
  FactorStrategy,
  FactorStrategyDraft,
  KlineBar,
  ScoreResult,
  ScreenResult,
  StrategyInfo,
} from '../types';
import {
  createFactorStrategy,
  deleteFactorStrategy,
  fetchFactorDefs,
  fetchFactorStrategies,
  fetchCompositeMetrics,
  fetchKline,
  fetchStrategyList,
  runCompositeScreening,
  runScoring,
  runScreening,
  updateFactorStrategy,
} from '../api/client';

type ScreeningMode = 'composer' | 'signal' | 'score';

const makeId = () =>
  globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;

const cloneDraft = (draft: FactorStrategyDraft): FactorStrategyDraft =>
  JSON.parse(JSON.stringify(draft)) as FactorStrategyDraft;

const newCondition = (
  metric = 'ma5',
  operator = 'above_metric',
  compareMetric: string | null = 'ma20',
): CompositeCondition => ({
  id: makeId(),
  metric,
  operator,
  value: operator === 'above_metric' ? null : 0,
  value2: null,
  compare_metric: compareMetric,
  params: {},
  periods: 3,
  weight: 1,
  required: true,
  enabled: true,
});

const defaultDraft = (): FactorStrategyDraft => ({
  name: '趋势量能组合',
  description: '趋势方向由均线和 BBI 确认，量能作为评分增强项。',
  logic: 'all',
  groups: [
    {
      id: makeId(),
      name: '趋势确认',
      logic: 'all',
      conditions: [
        newCondition(),
        newCondition('close', 'above_metric', 'bbi'),
        {
          ...newCondition('volume_ratio_5', 'gte', null),
          value: 1.2,
          required: false,
          weight: 1.5,
        },
      ],
    },
  ],
  min_score: 60,
  top_n: 100,
  lookback: 250,
});

interface ScreeningStore {
  // Mode
  mode: ScreeningMode;
  setMode: (mode: ScreeningMode) => void;

  // Strategy list
  strategies: StrategyInfo[];
  loadStrategies: () => Promise<void>;

  // Form state
  strategy: string;
  strategyParams: Record<string, number>;
  scanDate: string;
  setField: (field: string, value: unknown) => void;
  setStrategyParam: (name: string, value: number) => void;

  // Signal result
  result: ScreenResult | null;
  loading: boolean;
  error: string | null;
  scan: () => Promise<void>;

  // Multi-factor scoring
  factorDefs: FactorDef[];
  weights: Record<string, number>;
  exclude_centipede: boolean;
  min_sandglass: number;
  min_amount: number;
  use_patterns: boolean;
  topN: number;
  scoreResult: ScoreResult | null;
  loadFactorDefs: () => Promise<void>;
  runScore: () => Promise<void>;
  setWeight: (key: string, val: number) => void;

  // Visual factor strategy composer
  composerMetrics: CompositeMetricDef[];
  factorStrategies: FactorStrategy[];
  activeFactorStrategyId: string | null;
  composerDraft: FactorStrategyDraft;
  composerResult: CompositeScanResult | null;
  composerRunning: boolean;
  composerSaving: boolean;
  composerDirty: boolean;
  loadComposer: () => Promise<void>;
  newFactorStrategy: () => void;
  selectFactorStrategy: (id: string) => void;
  updateComposerDraft: (patch: Partial<FactorStrategyDraft>) => void;
  addComposerGroup: () => void;
  updateComposerGroup: (id: string, patch: Partial<CompositeGroup>) => void;
  removeComposerGroup: (id: string) => void;
  addComposerCondition: (groupId: string) => void;
  updateComposerCondition: (
    groupId: string,
    conditionId: string,
    patch: Partial<CompositeCondition>,
  ) => void;
  removeComposerCondition: (groupId: string, conditionId: string) => void;
  saveFactorStrategy: () => Promise<void>;
  duplicateFactorStrategy: () => void;
  removeFactorStrategy: () => Promise<void>;
  runComposer: () => Promise<void>;

  // Selected symbol for chart
  selectedSymbol: string | null;
  klineData: KlineBar[] | null;
  klineLoading: boolean;
  selectSymbol: (symbol: string) => Promise<void>;
}

export const useScreeningStore = create<ScreeningStore>((set, get) => ({
  mode: 'composer',
  setMode: (mode) => set({ mode, selectedSymbol: null, klineData: null }),

  strategies: [],
  loadStrategies: async () => {
    const strategies = await fetchStrategyList();
    set({ strategies });
    if (strategies.length > 0) {
      const first = strategies[0];
      const params: Record<string, number> = {};
      first.params_schema.forEach((p) => {
        params[p.name] = p.default;
      });
      set({ strategy: first.name, strategyParams: params });
    }
  },

  strategy: 'ma_cross',
  strategyParams: { fast_period: 5, slow_period: 20 },
  scanDate: dayjs().format('YYYY-MM-DD'),
  setField: (field, value) => set({ [field]: value } as Partial<ScreeningStore>),
  setStrategyParam: (name, value) =>
    set((s) => ({ strategyParams: { ...s.strategyParams, [name]: value } })),

  result: null,
  loading: false,
  error: null,
  scan: async () => {
    const s = get();
    set({ loading: true, error: null, selectedSymbol: null, klineData: null });
    try {
      const result = await runScreening({
        strategy: s.strategy,
        strategy_params: s.strategyParams,
        scan_date: s.scanDate,
      });
      set({ result, loading: false });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '选股失败';
      set({ error: msg, loading: false });
    }
  },

  // ── Multi-factor scoring ──
  factorDefs: [],
  weights: {},
  exclude_centipede: true,
  min_sandglass: 0,
  min_amount: 0,
  use_patterns: true,
  topN: 100,
  scoreResult: null,
  loadFactorDefs: async () => {
    const factorDefs = await fetchFactorDefs();
    const weights: Record<string, number> = {};
    factorDefs.forEach((f) => {
      weights[f.key] = Math.round(f.default_weight * 100);
    });
    set({ factorDefs, weights });
  },
  runScore: async () => {
    const s = get();
    set({ loading: true, error: null, selectedSymbol: null, klineData: null });
    try {
      const total = Object.values(s.weights).reduce((a, b) => a + b, 0) || 1;
      const weights = {
        trend: (s.weights.trend ?? 0) / total,
        momentum: (s.weights.momentum ?? 0) / total,
        volume: (s.weights.volume ?? 0) / total,
        dip: (s.weights.dip ?? 0) / total,
        risk: (s.weights.risk ?? 0) / total,
      };
      const scoreResult = await runScoring({
        scan_date: s.scanDate,
        weights,
        exclude_centipede: s.exclude_centipede,
        min_sandglass: s.min_sandglass,
        min_amount: s.min_amount,
        use_patterns: s.use_patterns,
        top_n: s.topN,
      });
      set({ scoreResult, loading: false });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '评分选股失败';
      set({ error: msg, loading: false });
    }
  },
  setWeight: (key, val) => set((s) => ({ weights: { ...s.weights, [key]: val } })),

  composerMetrics: [],
  factorStrategies: [],
  activeFactorStrategyId: null,
  composerDraft: defaultDraft(),
  composerResult: null,
  composerRunning: false,
  composerSaving: false,
  composerDirty: false,
  loadComposer: async () => {
    try {
      const [composerMetrics, factorStrategies] = await Promise.all([
        fetchCompositeMetrics(),
        fetchFactorStrategies(),
      ]);
      const current = get().activeFactorStrategyId;
      const selected =
        factorStrategies.find((strategy) => strategy.id === current) ?? factorStrategies[0];
      if (selected && !get().composerDirty) {
        const { id, created_at, updated_at, ...draft } = selected;
        void id;
        void created_at;
        void updated_at;
        set({
          composerMetrics,
          factorStrategies,
          activeFactorStrategyId: selected.id,
          composerDraft: cloneDraft(draft),
        });
      } else {
        set({ composerMetrics, factorStrategies });
      }
    } catch (e: unknown) {
      set({ error: e instanceof Error ? e.message : '加载组合策略失败' });
    }
  },
  newFactorStrategy: () =>
    set({
      activeFactorStrategyId: null,
      composerDraft: defaultDraft(),
      composerResult: null,
      composerDirty: false,
      selectedSymbol: null,
      klineData: null,
    }),
  selectFactorStrategy: (id) => {
    const strategy = get().factorStrategies.find((item) => item.id === id);
    if (!strategy) return;
    const { id: strategyId, created_at, updated_at, ...draft } = strategy;
    void created_at;
    void updated_at;
    set({
      activeFactorStrategyId: strategyId,
      composerDraft: cloneDraft(draft),
      composerResult: null,
      composerDirty: false,
      selectedSymbol: null,
      klineData: null,
    });
  },
  updateComposerDraft: (patch) =>
    set((state) => ({
      composerDraft: { ...state.composerDraft, ...patch },
      composerDirty: true,
    })),
  addComposerGroup: () =>
    set((state) => ({
      composerDraft: {
        ...state.composerDraft,
        groups: [
          ...state.composerDraft.groups,
          {
            id: makeId(),
            name: `条件组 ${state.composerDraft.groups.length + 1}`,
            logic: 'all',
            conditions: [newCondition()],
          },
        ],
      },
      composerDirty: true,
    })),
  updateComposerGroup: (id, patch) =>
    set((state) => ({
      composerDraft: {
        ...state.composerDraft,
        groups: state.composerDraft.groups.map((group) =>
          group.id === id ? { ...group, ...patch } : group,
        ),
      },
      composerDirty: true,
    })),
  removeComposerGroup: (id) =>
    set((state) => ({
      composerDraft: {
        ...state.composerDraft,
        groups: state.composerDraft.groups.filter((group) => group.id !== id),
      },
      composerDirty: true,
    })),
  addComposerCondition: (groupId) =>
    set((state) => ({
      composerDraft: {
        ...state.composerDraft,
        groups: state.composerDraft.groups.map((group) =>
          group.id === groupId
            ? { ...group, conditions: [...group.conditions, newCondition()] }
            : group,
        ),
      },
      composerDirty: true,
    })),
  updateComposerCondition: (groupId, conditionId, patch) =>
    set((state) => ({
      composerDraft: {
        ...state.composerDraft,
        groups: state.composerDraft.groups.map((group) =>
          group.id === groupId
            ? {
                ...group,
                conditions: group.conditions.map((condition) =>
                  condition.id === conditionId ? { ...condition, ...patch } : condition,
                ),
              }
            : group,
        ),
      },
      composerDirty: true,
    })),
  removeComposerCondition: (groupId, conditionId) =>
    set((state) => ({
      composerDraft: {
        ...state.composerDraft,
        groups: state.composerDraft.groups.map((group) =>
          group.id === groupId
            ? {
                ...group,
                conditions: group.conditions.filter(
                  (condition) => condition.id !== conditionId,
                ),
              }
            : group,
        ),
      },
      composerDirty: true,
    })),
  saveFactorStrategy: async () => {
    const state = get();
    set({ composerSaving: true, error: null });
    try {
      const saved = state.activeFactorStrategyId
        ? await updateFactorStrategy(state.activeFactorStrategyId, state.composerDraft)
        : await createFactorStrategy(state.composerDraft);
      const factorStrategies = await fetchFactorStrategies();
      const { id, created_at, updated_at, ...draft } = saved;
      void created_at;
      void updated_at;
      set({
        factorStrategies,
        activeFactorStrategyId: id,
        composerDraft: cloneDraft(draft),
        composerSaving: false,
        composerDirty: false,
      });
    } catch (e: unknown) {
      set({
        error: e instanceof Error ? e.message : '保存组合策略失败',
        composerSaving: false,
      });
    }
  },
  duplicateFactorStrategy: () =>
    set((state) => ({
      activeFactorStrategyId: null,
      composerDraft: {
        ...cloneDraft(state.composerDraft),
        name: `${state.composerDraft.name} 副本`,
      },
      composerDirty: true,
      composerResult: null,
    })),
  removeFactorStrategy: async () => {
    const id = get().activeFactorStrategyId;
    if (!id) return;
    try {
      await deleteFactorStrategy(id);
      const factorStrategies = await fetchFactorStrategies();
      set({ factorStrategies });
      if (factorStrategies.length > 0) {
        get().selectFactorStrategy(factorStrategies[0].id);
      } else {
        get().newFactorStrategy();
      }
    } catch (e: unknown) {
      set({ error: e instanceof Error ? e.message : '删除组合策略失败' });
    }
  },
  runComposer: async () => {
    const state = get();
    set({
      composerRunning: true,
      error: null,
      selectedSymbol: null,
      klineData: null,
    });
    try {
      const composerResult = await runCompositeScreening({
        definition: state.composerDraft,
        scan_date: state.scanDate,
      });
      set({ composerResult, composerRunning: false });
    } catch (e: unknown) {
      set({
        error: e instanceof Error ? e.message : '组合策略运行失败',
        composerRunning: false,
      });
    }
  },

  selectedSymbol: null,
  klineData: null,
  klineLoading: false,
  selectSymbol: async (symbol: string) => {
    const s = get();
    set({ selectedSymbol: symbol, klineData: null, klineLoading: true });
    try {
      const endDate = s.scanDate;
      const startDate = dayjs(endDate).subtract(6, 'month').format('YYYY-MM-DD');
      const data = await fetchKline(symbol, startDate, endDate);
      set({ klineData: data, klineLoading: false });
    } catch {
      set({ klineLoading: false });
    }
  },
}));
