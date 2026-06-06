import { useEffect } from 'react';
import {
  Button,
  Collapse,
  DatePicker,
  InputNumber,
  Popconfirm,
  Segmented,
  Select,
  Slider,
  Space,
  Switch,
  Typography,
} from 'antd';
import {
  CalendarOutlined,
  CaretRightOutlined,
  DeleteOutlined,
  ExperimentOutlined,
  FilterOutlined,
  PlusOutlined,
  SearchOutlined,
  SlidersOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { useScreeningStore } from '../../stores/screening';

const { Text } = Typography;

const labelStyle: React.CSSProperties = {
  color: '#848e9c',
  fontSize: 11,
  textTransform: 'uppercase',
  letterSpacing: 0,
  marginBottom: 4,
  display: 'block',
};

function SectionHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <Space size={6} style={{ color: '#eaecef', fontSize: 13, fontWeight: 500 }}>
      {icon}
      {title}
    </Space>
  );
}

export default function ScreeningSidebar() {
  const store = useScreeningStore();

  useEffect(() => {
    store.loadStrategies();
    store.loadFactorDefs();
    store.loadComposer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentStrategy = store.strategies.find((item) => item.name === store.strategy);
  const isComposer = store.mode === 'composer';
  const isScore = store.mode === 'score';

  const dateItem = {
    key: 'date',
    label: <SectionHeader icon={<CalendarOutlined />} title="选股日期" />,
    children: (
      <div>
        <Text style={labelStyle}>扫描截止日</Text>
        <DatePicker
          style={{ width: '100%' }}
          size="small"
          value={dayjs(store.scanDate)}
          onChange={(value) =>
            value && store.setField('scanDate', value.format('YYYY-MM-DD'))
          }
        />
      </div>
    ),
  };

  const composerItems = [
    {
      key: 'library',
      label: <SectionHeader icon={<ExperimentOutlined />} title="策略库" />,
      children: (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Select
            style={{ width: '100%' }}
            size="small"
            placeholder="选择已保存策略"
            value={store.activeFactorStrategyId ?? undefined}
            options={store.factorStrategies.map((strategy) => ({
              value: strategy.id,
              label: strategy.name,
            }))}
            onChange={store.selectFactorStrategy}
            notFoundContent="还没有已保存策略"
          />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 32px', gap: 6 }}>
            <Button
              size="small"
              icon={<PlusOutlined />}
              onClick={store.newFactorStrategy}
            >
              新建组合策略
            </Button>
            <Popconfirm
              title="删除当前策略？"
              disabled={!store.activeFactorStrategyId}
              onConfirm={store.removeFactorStrategy}
            >
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                disabled={!store.activeFactorStrategyId}
                aria-label="删除当前策略"
              />
            </Popconfirm>
          </div>
        </div>
      ),
    },
    {
      key: 'execution',
      label: <SectionHeader icon={<SlidersOutlined />} title="运行设置" />,
      children: (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div>
            <Text style={labelStyle}>最小综合分</Text>
            <InputNumber
              style={{ width: '100%' }}
              size="small"
              min={0}
              max={100}
              addonAfter="分"
              value={store.composerDraft.min_score}
              onChange={(value) =>
                store.updateComposerDraft({ min_score: value ?? 0 })
              }
            />
          </div>
          <div>
            <Text style={labelStyle}>历史计算窗口</Text>
            <InputNumber
              style={{ width: '100%' }}
              size="small"
              min={30}
              max={1500}
              addonAfter="日"
              value={store.composerDraft.lookback}
              onChange={(value) =>
                store.updateComposerDraft({ lookback: value ?? 250 })
              }
            />
          </div>
          <div>
            <Text style={labelStyle}>返回数量</Text>
            <InputNumber
              style={{ width: '100%' }}
              size="small"
              min={1}
              max={1000}
              addonAfter="只"
              value={store.composerDraft.top_n}
              onChange={(value) =>
                store.updateComposerDraft({ top_n: value ?? 100 })
              }
            />
          </div>
        </div>
      ),
    },
    dateItem,
  ];

  const signalItems = [
    {
      key: 'strategy',
      label: <SectionHeader icon={<ExperimentOutlined />} title="策略配置" />,
      children: (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div>
            <Text style={labelStyle}>策略选择</Text>
            <Select
              style={{ width: '100%' }}
              value={store.strategy}
              onChange={(value) => {
                store.setField('strategy', value);
                const selected = store.strategies.find((item) => item.name === value);
                if (!selected) return;
                store.setField(
                  'strategyParams',
                  Object.fromEntries(
                    selected.params_schema.map((param) => [param.name, param.default]),
                  ),
                );
              }}
              options={store.strategies.map((strategy) => ({
                value: strategy.name,
                label: strategy.display_name,
              }))}
              size="small"
            />
          </div>
          {currentStrategy?.params_schema.map((param) => (
            <div key={param.name}>
              <Text style={labelStyle}>{param.label}</Text>
              <InputNumber
                style={{ width: '100%' }}
                size="small"
                min={param.min}
                max={param.max}
                step={param.type === 'float' ? 0.1 : 1}
                value={store.strategyParams[param.name] ?? param.default}
                onChange={(value) =>
                  value !== null && store.setStrategyParam(param.name, value)
                }
              />
            </div>
          ))}
        </div>
      ),
    },
    dateItem,
  ];

  const scoreItems = [
    {
      key: 'weights',
      label: <SectionHeader icon={<SlidersOutlined />} title="因子权重" />,
      children: (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {store.factorDefs.map((factor) => (
            <div key={factor.key}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'baseline',
                }}
              >
                <Text style={{ ...labelStyle, marginBottom: 0 }} title={factor.desc}>
                  {factor.label}
                </Text>
                <span className="mono" style={{ color: '#eaecef', fontSize: 11 }}>
                  {store.weights[factor.key] ?? 0}
                </span>
              </div>
              <Slider
                min={0}
                max={100}
                value={store.weights[factor.key] ?? 0}
                onChange={(value) => store.setWeight(factor.key, value)}
              />
            </div>
          ))}
        </div>
      ),
    },
    {
      key: 'filters',
      label: <SectionHeader icon={<FilterOutlined />} title="过滤条件" />,
      children: (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="sidebar-switch-row">
            <Text style={{ ...labelStyle, marginBottom: 0 }}>排除蜈蚣图</Text>
            <Switch
              size="small"
              checked={store.exclude_centipede}
              onChange={(value) => store.setField('exclude_centipede', value)}
            />
          </div>
          <div className="sidebar-switch-row">
            <Text style={{ ...labelStyle, marginBottom: 0 }}>启用形态分析</Text>
            <Switch
              size="small"
              checked={store.use_patterns}
              onChange={(value) => store.setField('use_patterns', value)}
            />
          </div>
          <div>
            <Text style={labelStyle}>最低沙漏分</Text>
            <InputNumber
              style={{ width: '100%' }}
              size="small"
              min={0}
              max={100}
              value={store.min_sandglass}
              onChange={(value) => store.setField('min_sandglass', value ?? 0)}
            />
          </div>
          <div>
            <Text style={labelStyle}>最小成交额（万元）</Text>
            <InputNumber
              style={{ width: '100%' }}
              size="small"
              min={0}
              step={1000}
              value={store.min_amount / 1e4}
              onChange={(value) => store.setField('min_amount', (value ?? 0) * 1e4)}
            />
          </div>
          <div>
            <Text style={labelStyle}>返回数量</Text>
            <InputNumber
              style={{ width: '100%' }}
              size="small"
              min={1}
              max={1000}
              value={store.topN}
              onChange={(value) => store.setField('topN', value ?? 100)}
            />
          </div>
        </div>
      ),
    },
    dateItem,
  ];

  const items = isComposer ? composerItems : isScore ? scoreItems : signalItems;
  const activeKeys = isComposer
    ? ['library', 'execution', 'date']
    : isScore
      ? ['weights', 'filters', 'date']
      : ['strategy', 'date'];

  return (
    <div className="sidebar-shell">
      <div className="sidebar-context">
        <small>ALPHA SCANNER</small>
        <strong>智能选股</strong>
        <p>组合技术指标、量价与形态条件，建立可复用的选股策略。</p>
      </div>
      <div style={{ padding: '12px 16px 10px', borderBottom: '1px solid var(--border-light)' }}>
        <Text style={labelStyle}>选股模式</Text>
        <Segmented
          block
          size="small"
          value={store.mode}
          onChange={(value) =>
            store.setMode(value as 'composer' | 'signal' | 'score')
          }
          options={[
            { label: '策略组合', value: 'composer' },
            { label: '因子评分', value: 'score' },
            { label: '经典信号', value: 'signal' },
          ]}
        />
      </div>
      <div className="sidebar-scroll">
        <Collapse
          key={store.mode}
          items={items}
          defaultActiveKey={activeKeys}
          ghost
          size="small"
          expandIcon={({ isActive }) => (
            <CaretRightOutlined
              rotate={isActive ? 90 : 0}
              style={{ color: '#5e6673', fontSize: 10 }}
            />
          )}
          style={{ background: 'transparent' }}
        />
      </div>
      <div className="sidebar-footer">
        <Button
          type="primary"
          size="middle"
          block
          icon={<SearchOutlined />}
          loading={isComposer ? store.composerRunning : store.loading}
          onClick={() =>
            isComposer ? store.runComposer() : isScore ? store.runScore() : store.scan()
          }
        >
          {isComposer ? '运行组合策略' : '开始选股'}
        </Button>
      </div>
    </div>
  );
}
