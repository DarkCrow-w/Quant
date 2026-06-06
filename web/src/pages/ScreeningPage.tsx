import { useState } from 'react';
import { Alert, Spin, Table, Tag } from 'antd';
import { FundOutlined, SearchOutlined } from '@ant-design/icons';
import { useScreeningStore } from '../stores/screening';
import type { ScoredStock } from '../types';
import KlineChart, { type SubplotKey } from '../components/chart/KlineChart';
import OverlaySelector, {
  SubplotSelector,
  keysToOverlays,
} from '../components/chart/OverlaySelector';
import FactorStrategyBuilder from '../components/screening/FactorStrategyBuilder';

const RISE = '#f6465d';
const FALL = '#0ecb81';

function EmptyState() {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100%',
        minHeight: 400,
        gap: 16,
      }}
    >
      <div
        style={{
          width: 64,
          height: 64,
          borderRadius: 16,
          background: '#141619',
          border: '1px solid #1e2126',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <SearchOutlined style={{ fontSize: 28, color: '#2b2f36' }} />
      </div>
      <div style={{ color: '#5e6673', fontSize: 14 }}>配置策略后点击开始选股</div>
      <div style={{ color: '#2b2f36', fontSize: 12 }}>扫描全部已缓存股票，找出买入信号</div>
    </div>
  );
}

function PanelCard({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        background: '#141619',
        borderRadius: 8,
        border: '1px solid #1e2126',
        overflow: 'hidden',
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function formatVolume(v: number): string {
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿';
  if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
  return v.toFixed(0);
}

function formatAmount(v: number): string {
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿';
  if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
  return v.toFixed(0);
}

function scoreColor(v: number): string {
  if (v >= 80) return FALL;
  if (v >= 65) return '#f0b90b';
  return '#848e9c';
}

function ScoreBar({ value }: { value: number }) {
  const color = scoreColor(value);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div
        style={{
          flex: 1,
          height: 6,
          background: '#1e2126',
          borderRadius: 3,
          overflow: 'hidden',
          minWidth: 48,
        }}
      >
        <div
          style={{
            width: `${Math.max(0, Math.min(100, value))}%`,
            height: '100%',
            background: color,
            borderRadius: 3,
          }}
        />
      </div>
      <span className="mono" style={{ color, fontWeight: 600, fontSize: 12, width: 32, textAlign: 'right' }}>
        {value.toFixed(0)}
      </span>
    </div>
  );
}

function FactorMini({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 30 }}>
      <span style={{ color: '#5e6673', fontSize: 9 }}>{label}</span>
      <span
        className="mono"
        style={{ color: scoreColor(value), fontSize: 11, fontWeight: 500 }}
      >
        {value.toFixed(0)}
      </span>
    </div>
  );
}

function ChartPanel({
  symbol,
  overlayKeys,
  setOverlayKeys,
  subplots,
  setSubplots,
  klineData,
  klineLoading,
}: {
  symbol: string;
  overlayKeys: string[];
  setOverlayKeys: (v: string[]) => void;
  subplots: SubplotKey[];
  setSubplots: (v: SubplotKey[]) => void;
  klineData: ReturnType<typeof useScreeningStore.getState>['klineData'];
  klineLoading: boolean;
}) {
  return (
    <PanelCard>
      <div
        style={{
          padding: '8px 14px',
          borderBottom: '1px solid #1e2126',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 16,
          color: '#848e9c',
          fontSize: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="mono" style={{ color: '#1890ff', fontWeight: 500 }}>
            {symbol}
          </span>
          <span>K线走势</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <OverlaySelector value={overlayKeys} onChange={setOverlayKeys} />
          <span style={{ color: '#2b2f36' }}>|</span>
          <SubplotSelector value={subplots} onChange={setSubplots} />
        </div>
      </div>
      {klineLoading ? (
        <div style={{ padding: 40, textAlign: 'center' }}>
          <Spin size="small" />
        </div>
      ) : klineData && klineData.length > 0 ? (
        <div style={{ padding: '0 6px' }}>
          <KlineChart
            kline={klineData}
            trades={[]}
            symbol={symbol}
            overlays={keysToOverlays(overlayKeys)}
            subplots={subplots}
          />
        </div>
      ) : (
        <div style={{ padding: 40, textAlign: 'center', color: '#5e6673' }}>暂无K线数据</div>
      )}
    </PanelCard>
  );
}

function StatItem({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div>
      <span style={{ color: '#848e9c' }}>{label}</span>{' '}
      <span className="mono" style={{ color: color ?? '#eaecef', fontWeight: 500 }}>
        {value}
      </span>
    </div>
  );
}

export default function ScreeningPage() {
  const {
    mode,
    result,
    scoreResult,
    loading,
    error,
    selectedSymbol,
    klineData,
    klineLoading,
    selectSymbol,
  } = useScreeningStore();
  const [overlayKeys, setOverlayKeys] = useState<string[]>(['MA5', 'MA20', 'BBI']);
  const [subplots, setSubplots] = useState<SubplotKey[]>(['VOL', 'MACD']);

  if (mode === 'composer') {
    return <FactorStrategyBuilder />;
  }

  if (loading) {
    return (
      <div
        style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100%',
          minHeight: 400,
        }}
      >
        <Spin size="large" tip="正在扫描全部股票..." style={{ color: '#848e9c' }}>
          <div style={{ padding: 50 }} />
        </Spin>
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        message="选股失败"
        description={error}
        showIcon
        style={{ margin: 20, background: '#1a1d21', border: '1px solid #f6465d40' }}
      />
    );
  }

  // ── Score mode ──
  if (mode === 'score') {
    if (!scoreResult) return <EmptyState />;
    const selected = scoreResult.stocks.find((s) => s.symbol === selectedSymbol) || null;

    const tagStyle: React.CSSProperties = { margin: 0, borderRadius: 3, border: 'none', fontSize: 10 };

    const columns = [
      {
        title: '#',
        key: 'rank',
        width: 44,
        align: 'center' as const,
        render: (_: unknown, __: ScoredStock, idx: number) => (
          <span className="mono" style={{ color: '#5e6673', fontSize: 12 }}>
            {idx + 1}
          </span>
        ),
      },
      {
        title: '代码',
        dataIndex: 'symbol',
        key: 'symbol',
        width: 84,
        render: (v: string) => (
          <span className="mono" style={{ color: '#1890ff', fontWeight: 500 }}>
            {v}
          </span>
        ),
      },
      {
        title: '综合分',
        dataIndex: 'score',
        key: 'score',
        width: 140,
        sorter: (a: ScoredStock, b: ScoredStock) => a.score - b.score,
        render: (v: number) => <ScoreBar value={v} />,
      },
      {
        title: '评级',
        dataIndex: 'rating',
        key: 'rating',
        width: 130,
        render: (v: string) => (
          <Tag color="#2b2f36" style={{ ...tagStyle, color: '#f0b90b', fontSize: 11 }}>
            {v}
          </Tag>
        ),
      },
      {
        title: '五因子 (趋/动/量/吸/险)',
        key: 'factors',
        width: 190,
        render: (_: unknown, r: ScoredStock) => (
          <div style={{ display: 'flex', gap: 8 }}>
            <FactorMini label="趋" value={r.factors.trend} />
            <FactorMini label="动" value={r.factors.momentum} />
            <FactorMini label="量" value={r.factors.volume} />
            <FactorMini label="吸" value={r.factors.dip} />
            <FactorMini label="险" value={r.factors.risk} />
          </div>
        ),
      },
      {
        title: '形态',
        key: 'patterns',
        width: 220,
        render: (_: unknown, r: ScoredStock) => (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            <Tag color="#1f2937" style={{ ...tagStyle, color: scoreColor(r.sandglass) }}>
              沙漏 {r.sandglass.toFixed(0)}
            </Tag>
            {r.wave && (
              <Tag color="#1f2937" style={{ ...tagStyle, color: '#1890ff' }}>
                {r.wave}
              </Tag>
            )}
            {r.kirin && (
              <Tag color="#1f2937" style={{ ...tagStyle, color: '#722ed1' }}>
                {r.kirin}
              </Tag>
            )}
          </div>
        ),
      },
      {
        title: '最新价',
        dataIndex: 'close',
        key: 'close',
        width: 80,
        align: 'right' as const,
        sorter: (a: ScoredStock, b: ScoredStock) => a.close - b.close,
        render: (v: number) => <span className="mono">{v.toFixed(2)}</span>,
      },
      {
        title: '涨跌幅',
        dataIndex: 'pct_chg',
        key: 'pct_chg',
        width: 86,
        align: 'right' as const,
        sorter: (a: ScoredStock, b: ScoredStock) => a.pct_chg - b.pct_chg,
        render: (v: number) => (
          <span className="mono" style={{ color: v >= 0 ? RISE : FALL, fontWeight: 500 }}>
            {v >= 0 ? '+' : ''}
            {v.toFixed(2)}%
          </span>
        ),
      },
    ];

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <PanelCard style={{ padding: '10px 16px' }}>
          <div style={{ display: 'flex', gap: 24, alignItems: 'center', fontSize: 13 }}>
            <div>
              <FundOutlined style={{ color: '#1890ff', marginRight: 6 }} />
              <StatItem label="扫描" value={`${scoreResult.total_scanned} 只`} />
            </div>
            <StatItem
              label="命中"
              value={`${scoreResult.total_matched} 只`}
              color={scoreResult.total_matched > 0 ? FALL : '#5e6673'}
            />
            <StatItem label="返回" value={`${scoreResult.returned} 只`} />
            <StatItem label="耗时" value={`${scoreResult.elapsed_seconds}s`} />
            <StatItem label="日期" value={scoreResult.scan_date} />
          </div>
        </PanelCard>

        <PanelCard>
          <Table
            dataSource={scoreResult.stocks}
            columns={columns}
            rowKey="symbol"
            size="small"
            pagination={
              scoreResult.stocks.length > 50 ? { pageSize: 50, size: 'small' } : false
            }
            scroll={{ y: selectedSymbol ? 260 : 520, x: 1100 }}
            onRow={(record) => ({
              onClick: () => selectSymbol(record.symbol),
              style: {
                cursor: 'pointer',
                background: record.symbol === selectedSymbol ? '#1a1d21' : undefined,
              },
            })}
            locale={{ emptyText: '未找到符合条件的股票' }}
          />
        </PanelCard>

        {selected && (selected.reasons.length > 0 || selected.warnings.length > 0) && (
          <PanelCard style={{ padding: '10px 16px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {selected.reasons.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                  <span style={{ color: '#848e9c', fontSize: 11 }}>利好</span>
                  {selected.reasons.map((r, i) => (
                    <Tag
                      key={i}
                      color="#0ecb8120"
                      style={{ margin: 0, borderRadius: 3, border: 'none', color: FALL, fontSize: 11 }}
                    >
                      {r}
                    </Tag>
                  ))}
                </div>
              )}
              {selected.warnings.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                  <span style={{ color: '#848e9c', fontSize: 11 }}>风险</span>
                  {selected.warnings.map((w, i) => (
                    <Tag
                      key={i}
                      color="#f6465d20"
                      style={{ margin: 0, borderRadius: 3, border: 'none', color: RISE, fontSize: 11 }}
                    >
                      {w}
                    </Tag>
                  ))}
                </div>
              )}
            </div>
          </PanelCard>
        )}

        {selectedSymbol && (
          <ChartPanel
            symbol={selectedSymbol}
            overlayKeys={overlayKeys}
            setOverlayKeys={setOverlayKeys}
            subplots={subplots}
            setSubplots={setSubplots}
            klineData={klineData}
            klineLoading={klineLoading}
          />
        )}
      </div>
    );
  }

  // ── Signal mode (existing) ──
  if (!result) return <EmptyState />;

  const columns = [
    {
      title: '代码',
      dataIndex: 'symbol',
      key: 'symbol',
      width: 90,
      render: (v: string) => (
        <span className="mono" style={{ color: '#1890ff', fontWeight: 500 }}>
          {v}
        </span>
      ),
    },
    {
      title: '信号日期',
      dataIndex: 'signal_date',
      key: 'signal_date',
      width: 110,
      sorter: (a: { signal_date: string }, b: { signal_date: string }) =>
        a.signal_date.localeCompare(b.signal_date),
    },
    {
      title: '收盘价',
      dataIndex: 'close',
      key: 'close',
      width: 90,
      align: 'right' as const,
      sorter: (a: { close: number }, b: { close: number }) => a.close - b.close,
      render: (v: number) => <span className="mono">{v.toFixed(2)}</span>,
    },
    {
      title: '成交量',
      dataIndex: 'volume',
      key: 'volume',
      width: 90,
      align: 'right' as const,
      sorter: (a: { volume: number }, b: { volume: number }) => a.volume - b.volume,
      render: (v: number) => <span className="mono">{formatVolume(v)}</span>,
    },
    {
      title: '成交额',
      dataIndex: 'amount',
      key: 'amount',
      width: 90,
      align: 'right' as const,
      sorter: (a: { amount: number }, b: { amount: number }) => a.amount - b.amount,
      render: (v: number) => <span className="mono">{formatAmount(v)}</span>,
    },
    {
      title: '信号强度',
      dataIndex: 'strength',
      key: 'strength',
      width: 80,
      align: 'center' as const,
      sorter: (a: { strength: number }, b: { strength: number }) => a.strength - b.strength,
      render: (v: number) => (
        <Tag
          color={v >= 0.8 ? '#f6465d' : v >= 0.5 ? '#f0b90b' : '#848e9c'}
          style={{ margin: 0, borderRadius: 3, border: 'none', fontSize: 11 }}
        >
          {(v * 100).toFixed(0)}%
        </Tag>
      ),
    },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* Stats Bar */}
      <PanelCard style={{ padding: '10px 16px' }}>
        <div style={{ display: 'flex', gap: 24, alignItems: 'center', fontSize: 13 }}>
          <div>
            <FundOutlined style={{ color: '#1890ff', marginRight: 6 }} />
            <span style={{ color: '#848e9c' }}>扫描</span>{' '}
            <span className="mono" style={{ color: '#eaecef', fontWeight: 500 }}>
              {result.total_scanned}
            </span>{' '}
            <span style={{ color: '#848e9c' }}>只</span>
          </div>
          <div>
            <span style={{ color: '#848e9c' }}>命中</span>{' '}
            <span
              className="mono"
              style={{ color: result.matches.length > 0 ? '#0ecb81' : '#5e6673', fontWeight: 600 }}
            >
              {result.matches.length}
            </span>{' '}
            <span style={{ color: '#848e9c' }}>只</span>
          </div>
          <div>
            <span style={{ color: '#848e9c' }}>耗时</span>{' '}
            <span className="mono" style={{ color: '#eaecef' }}>
              {result.elapsed_seconds}s
            </span>
          </div>
          <div>
            <span style={{ color: '#848e9c' }}>日期</span>{' '}
            <span className="mono" style={{ color: '#eaecef' }}>
              {result.scan_date}
            </span>
          </div>
        </div>
      </PanelCard>

      {/* Results Table */}
      <PanelCard>
        <Table
          dataSource={result.matches}
          columns={columns}
          rowKey="symbol"
          size="small"
          pagination={result.matches.length > 50 ? { pageSize: 50, size: 'small' } : false}
          scroll={{ y: selectedSymbol ? 260 : 500 }}
          onRow={(record) => ({
            onClick: () => selectSymbol(record.symbol),
            style: {
              cursor: 'pointer',
              background: record.symbol === selectedSymbol ? '#1a1d21' : undefined,
            },
          })}
          locale={{ emptyText: '未找到符合条件的股票' }}
        />
      </PanelCard>

      {/* K-line Chart */}
      {selectedSymbol && (
        <ChartPanel
          symbol={selectedSymbol}
          overlayKeys={overlayKeys}
          setOverlayKeys={setOverlayKeys}
          subplots={subplots}
          setSubplots={setSubplots}
          klineData={klineData}
          klineLoading={klineLoading}
        />
      )}
    </div>
  );
}
