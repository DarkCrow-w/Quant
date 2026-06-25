import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Input,
  Progress,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'antd';
import {
  CaretRightOutlined,
  CloudDownloadOutlined,
  DatabaseOutlined,
  FieldTimeOutlined,
  PauseOutlined,
  SearchOutlined,
  StopOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import {
  cancelDataJob,
  fetchCacheList,
  fetchCacheStatus,
  fetchIndicators,
  fetchUniverse,
  getCurrentDataJob,
  pauseDataJob,
  refreshUniverse,
  resumeDataJob,
  startDownloadAll,
  updateMarketData,
  type CacheInfo,
  type CacheStatusInfo,
  type DataJob,
  type DataSource,
  type IndicatorInfo,
  type UniverseItem,
} from '../api/client';

const statusLabel: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  paused: '已暂停',
  cancelling: '取消中',
  cancelled: '已取消',
  completed: '已完成',
  failed: '失败',
  interrupted: '已中断',
  idle: '空闲',
};

function StatTile({ label, value, sub }: { label: string; value: React.ReactNode; sub: string }) {
  return (
    <div className="data-stat-tile">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{sub}</small>
    </div>
  );
}

export default function DataPage() {
  const { message, modal } = AntdApp.useApp();
  const [cache, setCache] = useState<CacheInfo[]>([]);
  const [cacheStatus, setCacheStatus] = useState<CacheStatusInfo[]>([]);
  const [indicators, setIndicators] = useState<IndicatorInfo[]>([]);
  const [universe, setUniverse] = useState<UniverseItem[]>([]);
  const [job, setJob] = useState<DataJob | null>(null);
  const [source, setSource] = useState<DataSource>('tdx');
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(true);
  const [controlLoading, setControlLoading] = useState(false);
  const [universeLoading, setUniverseLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const observedJobIdsRef = useRef<Set<string>>(new Set());
  const lastCompletedJobRef = useRef<string | undefined>(undefined);

  const stopPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);

  const refreshCatalog = useCallback(async () => {
    const [nextCache, nextCacheStatus, nextIndicators, nextUniverse] = await Promise.all([
      fetchCacheList(),
      fetchCacheStatus(),
      fetchIndicators(),
      fetchUniverse(),
    ]);
    setCache(nextCache);
    setCacheStatus(nextCacheStatus);
    setIndicators(nextIndicators);
    setUniverse(nextUniverse);
  }, []);

  const pollJob = useCallback(async () => {
    try {
      const next = await getCurrentDataJob();
      setJob(next);
      if (next.id && next.running) {
        observedJobIdsRef.current.add(next.id);
      }
      if (!next.running && next.id && lastCompletedJobRef.current !== next.id) {
        lastCompletedJobRef.current = next.id;
        if (next.status === 'completed') {
          await refreshCatalog();
        }
        if (observedJobIdsRef.current.has(next.id)) {
          if (next.status === 'failed') {
            message.error(next.error || '数据任务失败');
          }
          if (next.status === 'cancelled') {
            message.info('数据任务已取消');
          }
        }
        stopPolling();
      }
    } catch {
      stopPolling();
    }
  }, [message, refreshCatalog, stopPolling]);

  const startPolling = useCallback(() => {
    stopPolling();
    void pollJob();
    pollRef.current = setInterval(pollJob, 800);
  }, [pollJob, stopPolling]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await Promise.all([refreshCatalog(), pollJob()]);
      startPolling();
    } catch (e) {
      setError(e instanceof Error ? e.message : '数据平台状态读取失败');
    } finally {
      setLoading(false);
    }
  }, [pollJob, refreshCatalog, startPolling]);

  useEffect(() => {
    void load();
    return () => stopPolling();
  }, [load, stopPolling]);

  const startUpdate = async (symbols?: string[]) => {
    try {
      const response = await updateMarketData(symbols, source);
      setJob(response.job);
      if (response.job.id) {
        observedJobIdsRef.current.add(response.job.id);
      }
      message.success(response.status === 'busy' ? '已有数据任务运行中' : '已提交增量更新任务');
      startPolling();
    } catch {
      message.error('启动更新任务失败');
    }
  };

  const startDownload = async () => {
    try {
      const response = await startDownloadAll(source);
      setJob(response.job);
      if (response.job.id) {
        observedJobIdsRef.current.add(response.job.id);
      }
      message.success(response.status === 'busy' ? '已有数据任务运行中' : '已提交全市场下载任务');
      startPolling();
    } catch {
      message.error('启动下载任务失败');
    }
  };

  const refreshStockPool = async () => {
    setUniverseLoading(true);
    try {
      const result = await refreshUniverse(source);
      message.success(`股票池已刷新：${result.symbols} 只`);
      await refreshCatalog();
    } catch {
      message.error('刷新股票池失败，请切换数据源或检查网络');
    } finally {
      setUniverseLoading(false);
    }
  };

  const togglePause = async () => {
    if (!job?.id) return;
    setControlLoading(true);
    try {
      const response = job.status === 'paused'
        ? await resumeDataJob(job.id)
        : await pauseDataJob(job.id);
      setJob(response.job);
      if (response.job.id) {
        observedJobIdsRef.current.add(response.job.id);
      }
      startPolling();
    } catch {
      message.error('任务控制失败');
    } finally {
      setControlLoading(false);
    }
  };

  const cancelJob = () => {
    if (!job?.id) return;
    modal.confirm({
      title: '取消当前数据任务？',
      content: '已写入的数据会保留，未处理的标的将停止下载。',
      okText: '取消任务',
      okButtonProps: { danger: true },
      cancelText: '继续运行',
      onOk: async () => {
        setControlLoading(true);
        try {
          const response = await cancelDataJob(job.id!);
          setJob(response.job);
          if (response.job.id) {
            observedJobIdsRef.current.add(response.job.id);
          }
          startPolling();
        } catch {
          message.error('取消任务失败');
        } finally {
          setControlLoading(false);
        }
      },
    });
  };

  const filteredCache = useMemo(() => {
    const query = keyword.trim().toLowerCase();
    if (!query) return cache;
    return cache.filter((row) => row.symbol.toLowerCase().includes(query));
  }, [cache, keyword]);

  const cacheBars = useMemo(
    () => cache.reduce((sum, row) => sum + (Number(row.bars) || 0), 0),
    [cache],
  );
  const latestDate = useMemo(() => {
    const dates = cache.map((row) => row.end).filter(Boolean).sort();
    return dates.at(-1) || '-';
  }, [cache]);
  const busy = Boolean(job?.running);
  const activeSymbol = job?.current_symbol || job?.result?.requested_symbols?.[0];

  const cacheColumns = [
    {
      title: '代码',
      dataIndex: 'symbol',
      width: 110,
      render: (value: string) => <span className="mono accent-text">{value}</span>,
    },
    { title: 'K 线数', dataIndex: 'bars', width: 100, align: 'right' as const },
    { title: '起始日期', dataIndex: 'start', width: 130 },
    { title: '最新日期', dataIndex: 'end', width: 130 },
    {
      title: '操作',
      width: 86,
      fixed: 'right' as const,
      render: (_: unknown, row: CacheInfo) => (
        <Tooltip title={busy ? '任务运行中' : '更新该标的'}>
          <Button
            type="text"
            icon={<SyncOutlined spin={activeSymbol === row.symbol && busy} />}
            disabled={busy}
            onClick={() => startUpdate([row.symbol])}
            aria-label={`更新 ${row.symbol}`}
          />
        </Tooltip>
      ),
    },
  ];

  const indicatorColumns = [
    { title: '指标', dataIndex: 'name', width: 110, render: (v: string) => <span className="mono">{v}</span> },
    { title: '回看', dataIndex: 'lookback', width: 80, align: 'right' as const },
    { title: '输出列', dataIndex: 'columns', render: (v: string[]) => v.join(', ') },
    { title: '版本', dataIndex: 'version', width: 90 },
  ];

  return (
    <div className="data-page">
      <div className="workspace-heading">
        <div>
          <h1>市场数据平台</h1>
          <p>统一管理本地行情缓存、股票池、指标目录和批量数据任务</p>
        </div>
        <Space>
          <Segmented
            value={source}
            disabled={busy}
            onChange={(value) => setSource(value as DataSource)}
            options={[{ label: '通达信', value: 'tdx' }, { label: 'TuShare', value: 'tushare' }]}
          />
          <Button icon={<SyncOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      {error && <Alert type="error" showIcon title="读取失败" description={error} />}

      <div className="data-stat-grid">
        <StatTile label="缓存标的" value={`${cache.length} 只`} sub={`${cacheBars.toLocaleString()} 根 K 线`} />
        <StatTile label="股票池" value={`${universe.length} 只`} sub="本地 universe 表" />
        <StatTile label="指标目录" value={`${indicators.length} 个`} sub="可物化到缓存" />
        <StatTile label="最新日期" value={latestDate} sub="按缓存结束日期统计" />
      </div>

      {job && job.status !== 'idle' && (
        <div className="data-job-panel">
          <div className="data-job-header">
            <div>
              <DatabaseOutlined />
              <strong>{job.kind === 'download' ? '全市场下载' : '增量更新'}</strong>
              <Tag color={job.running ? 'processing' : job.status === 'completed' ? 'success' : 'default'}>
                {statusLabel[job.status] || job.status}
              </Tag>
              {job.current_symbol && <span className="mono">{job.current_symbol}</span>}
            </div>
            <span className="mono">{job.completed} / {job.total}</span>
          </div>
          <Progress
            percent={job.percent}
            size="small"
            status={job.status === 'failed' ? 'exception' : job.status === 'completed' ? 'success' : 'active'}
          />
          <div className="data-job-footer">
            <span>更新 {job.updated || 0}</span>
            <span>跳过 {job.skipped || 0}</span>
            <span>失败 {job.failed || 0}</span>
            {job.elapsed_s ? <span><FieldTimeOutlined /> {job.elapsed_s}s</span> : null}
            {job.running && job.id && (
              <Space size={6}>
                {job.status !== 'cancelling' && (
                  <Button
                    size="small"
                    loading={controlLoading}
                    icon={job.status === 'paused' ? <CaretRightOutlined /> : <PauseOutlined />}
                    onClick={togglePause}
                  >
                    {job.status === 'paused' ? '继续' : '暂停'}
                  </Button>
                )}
                <Button
                  danger
                  size="small"
                  loading={controlLoading}
                  disabled={job.status === 'cancelling'}
                  icon={<StopOutlined />}
                  onClick={cancelJob}
                >
                  取消
                </Button>
              </Space>
            )}
          </div>
        </div>
      )}

      <div className="data-actions-panel">
        <div>
          <strong>数据初始化</strong>
          <span>新 clone 项目后，先下载全市场或维护常用股票缓存，再进行回测和选股。</span>
        </div>
        <Space wrap>
          <Button icon={<SyncOutlined />} disabled={busy} onClick={() => startUpdate()}>
            更新全部缓存
          </Button>
          <Button
            icon={<DatabaseOutlined />}
            loading={universeLoading}
            disabled={busy}
            onClick={refreshStockPool}
          >
            刷新股票池
          </Button>
          <Button type="primary" icon={<CloudDownloadOutlined />} disabled={busy} onClick={startDownload}>
            下载全市场
          </Button>
        </Space>
      </div>

      <div className="data-work-grid">
        <div className="surface-panel">
          <div className="panel-heading">
            <DatabaseOutlined />
            本地行情缓存
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索代码"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              style={{ width: 210, marginLeft: 'auto' }}
            />
          </div>
          <Table
            dataSource={filteredCache}
            columns={cacheColumns}
            rowKey="symbol"
            size="small"
            loading={loading}
            pagination={{ pageSize: 50, showSizeChanger: false }}
            scroll={{ y: 430, x: 560 }}
          />
        </div>
        <div className="data-side-stack">
          <div className="surface-panel">
            <div className="panel-heading">指标目录</div>
            <Table
              dataSource={indicators}
              columns={indicatorColumns}
              rowKey="name"
              size="small"
              loading={loading}
              pagination={false}
              scroll={{ y: 220 }}
            />
          </div>
          <div className="surface-panel">
            <div className="panel-heading">股票池样本</div>
            <div className="cache-status-list">
              {universe.slice(0, 8).map((row) => (
                <div key={row.symbol}>
                  <span className="mono accent-text">{row.symbol}</span>
                  <span>{row.name || '-'}</span>
                  <span>{row.market || '-'}</span>
                  <small>{String(row.list_date || '') || '-'}</small>
                </div>
              ))}
              {!universe.length && <div className="empty-inline">暂无股票池，请先刷新股票池</div>}
            </div>
          </div>
          <div className="surface-panel">
            <div className="panel-heading">缓存状态样本</div>
            <div className="cache-status-list">
              {cacheStatus.slice(0, 8).map((row) => (
                <div key={`${row.symbol}-${row.freq}`}>
                  <span className="mono accent-text">{row.symbol}</span>
                  <span>{row.freq}</span>
                  <span>{row.last_dt || '-'}</span>
                  <small>{row.source || 'local'}</small>
                </div>
              ))}
              {!cacheStatus.length && <div className="empty-inline">暂无缓存状态</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
