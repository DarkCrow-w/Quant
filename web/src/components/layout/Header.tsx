import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  App as AntdApp,
  Button,
  Input,
  Layout,
  Modal,
  Progress,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'antd';
import {
  AlertOutlined,
  BranchesOutlined,
  CloudDownloadOutlined,
  CaretRightOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FunctionOutlined,
  MenuOutlined,
  PauseOutlined,
  ProfileOutlined,
  RadarChartOutlined,
  RobotOutlined,
  DashboardOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SyncOutlined,
  StopOutlined,
  ThunderboltFilled,
} from '@ant-design/icons';
import {
  cancelDataJob,
  fetchCacheList,
  getCurrentDataJob,
  pauseDataJob,
  resumeDataJob,
  startDownloadAll,
  updateMarketData,
} from '../../api/client';
import type { CacheInfo, DataJob, DataSource } from '../../api/client';

interface HeaderProps {
  activePage: string;
  onPageChange: (page: string) => void;
  hasSidebar?: boolean;
  onOpenSidebar?: () => void;
}

const navItems = [
  { value: 'dashboard', label: '系统总览', icon: <DashboardOutlined /> },
  { value: 'data', label: '数据平台', icon: <DatabaseOutlined /> },
  { value: 'trading', label: '交易运行', icon: <SafetyCertificateOutlined /> },
  { value: 'research', label: '研究资产', icon: <ProfileOutlined /> },
  { value: 'strategy', label: '策略管理', icon: <BranchesOutlined /> },
  { value: 'factors', label: '因子研究', icon: <FunctionOutlined /> },
  { value: 'risk', label: '风险控制', icon: <AlertOutlined /> },
  { value: 'backtest', label: '回测研究', icon: <ExperimentOutlined /> },
  { value: 'screening', label: '智能选股', icon: <RadarChartOutlined /> },
  { value: 'agent', label: 'AI 研究员', icon: <RobotOutlined /> },
];

const statusLabel: Record<string, string> = {
  queued: '任务排队中',
  running: '任务运行中',
  paused: '任务已暂停',
  cancelling: '正在取消',
  cancelled: '任务已取消',
  completed: '任务已完成',
  failed: '任务失败',
  interrupted: '任务被中断',
  idle: '暂无任务',
  updated: '已更新',
  skipped: '已是最新',
};

export default function Header({
  activePage,
  onPageChange,
  hasSidebar,
  onOpenSidebar,
}: HeaderProps) {
  const { message, modal } = AntdApp.useApp();
  const [open, setOpen] = useState(false);
  const [cache, setCache] = useState<CacheInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [controlLoading, setControlLoading] = useState(false);
  const [job, setJob] = useState<DataJob | null>(null);
  const [source, setSource] = useState<DataSource>('tdx');
  const [keyword, setKeyword] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const observedJobIdsRef = useRef<Set<string>>(new Set());
  const lastCompletedJobRef = useRef<string | undefined>(undefined);

  const stopPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);

  const refreshCache = useCallback(async () => {
    setCache(await fetchCacheList());
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
        await refreshCache();
        if (observedJobIdsRef.current.has(next.id)) {
          if (next.status === 'completed' && next.failed) {
            message.warning(`数据任务完成，其中 ${next.failed} 只失败`);
          } else if (next.status === 'completed') {
            message.success('数据任务已完成');
          }
          if (next.status === 'failed') message.error(next.error || '数据任务失败');
          if (next.status === 'cancelled') message.info('数据任务已取消，已完成的数据已保留');
        }
        stopPolling();
      }
    } catch {
      stopPolling();
    }
  }, [message, refreshCache, stopPolling]);

  const startPolling = useCallback(() => {
    stopPolling();
    void pollJob();
    pollRef.current = setInterval(pollJob, 600);
  }, [pollJob, stopPolling]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const openPanel = async () => {
    setOpen(true);
    setLoading(true);
    try {
      await Promise.all([refreshCache(), pollJob()]);
      startPolling();
    } catch {
      message.error('读取本地数据状态失败');
    } finally {
      setLoading(false);
    }
  };

  const startUpdate = async (symbols?: string[]) => {
    try {
      const response = await updateMarketData(symbols, source);
      setJob(response.job);
      if (response.job.id) {
        observedJobIdsRef.current.add(response.job.id);
      }
      if (response.status === 'busy') {
        message.info('已有数据任务运行中，请等待完成');
      } else {
        message.success(symbols?.length === 1 ? `已开始更新 ${symbols[0]}` : '已开始增量更新');
      }
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
      if (response.status === 'busy') {
        message.info('已有数据任务运行中，请等待完成');
      } else {
        message.success('已开始下载全市场数据');
      }
      startPolling();
    } catch {
      message.error('启动下载任务失败');
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
      message.success(response.status === 'resumed' ? '下载已继续' : '下载已暂停');
      startPolling();
    } catch {
      message.error(job.status === 'paused' ? '继续任务失败' : '暂停任务失败');
    } finally {
      setControlLoading(false);
    }
  };

  const cancelJob = () => {
    if (!job?.id) return;
    modal.confirm({
      title: '取消当前数据任务？',
      content: '已经下载并写入的数据会保留，尚未处理的股票将停止下载。',
      okText: '取消任务',
      okButtonProps: { danger: true },
      cancelText: '继续下载',
      onOk: async () => {
        setControlLoading(true);
        try {
          const response = await cancelDataJob(job.id!);
          setJob(response.job);
          if (response.job.id) {
            observedJobIdsRef.current.add(response.job.id);
          }
          message.info('正在取消，当前请求完成后停止');
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

  const activeSymbol = job?.running
    ? job.current_symbol || job.result?.requested_symbols?.[0]
    : undefined;
  const busy = Boolean(job?.running);

  const cacheColumns = [
    {
      title: '代码',
      dataIndex: 'symbol',
      width: 100,
      render: (value: string) => <span className="mono accent-text">{value}</span>,
    },
    { title: 'K 线数', dataIndex: 'bars', width: 90, align: 'right' as const },
    { title: '起始日期', dataIndex: 'start', width: 120 },
    { title: '最新日期', dataIndex: 'end', width: 120 },
    {
      title: '操作',
      width: 72,
      fixed: 'right' as const,
      render: (_: unknown, row: CacheInfo) => (
        <Tooltip title={busy ? '已有数据任务运行中' : '单独更新该股票'}>
          <Button
            type="text"
            size="small"
            icon={<SyncOutlined spin={activeSymbol === row.symbol && busy} />}
            disabled={busy}
            onClick={() => startUpdate([row.symbol])}
            aria-label={`更新 ${row.symbol}`}
          />
        </Tooltip>
      ),
    },
  ];

  return (
    <>
      <Layout.Header className="topbar">
        <div className="topbar-left">
          {hasSidebar && (
            <Tooltip title="研究参数">
              <Button
                className="mobile-sidebar-trigger"
                type="text"
                icon={<MenuOutlined />}
                onClick={onOpenSidebar}
              />
            </Tooltip>
          )}
          <div className="brand-mark"><ThunderboltFilled /></div>
          <div className="brand-copy">
            <strong>QuantLab</strong>
            <span>RESEARCH TERMINAL</span>
          </div>
          <nav className="primary-nav" aria-label="工作区导航">
            {navItems.map((item) => (
              <button
                key={item.value}
                type="button"
                className={activePage === item.value ? 'active' : ''}
                onClick={() => onPageChange(item.value)}
              >
                {item.icon}
                <span className="nav-label">{item.label}</span>
              </button>
            ))}
          </nav>
        </div>
        <div className="topbar-actions">
          <div className="market-status"><i /> 数据服务就绪</div>
          <Button icon={<DatabaseOutlined />} onClick={openPanel}>数据中心</Button>
          <span className="version-label">v0.1.0</span>
        </div>
      </Layout.Header>

      <Modal
        title={<Space><DatabaseOutlined className="accent-text" />市场数据中心</Space>}
        open={open}
        onCancel={() => setOpen(false)}
        width={760}
        footer={
          <Space>
            <Button onClick={() => setOpen(false)}>关闭</Button>
            <Button icon={<SyncOutlined />} disabled={busy} onClick={() => startUpdate()}>
              更新全部缓存
            </Button>
            <Button
              type="primary"
              icon={<CloudDownloadOutlined />}
              disabled={busy}
              onClick={startDownload}
            >
              下载全市场
            </Button>
          </Space>
        }
      >
        <div className="data-center-toolbar">
          <span>数据源</span>
          <Segmented
            value={source}
            disabled={busy}
            onChange={(value) => setSource(value as DataSource)}
            options={[{ label: '通达信', value: 'tdx' }, { label: 'TuShare', value: 'tushare' }]}
          />
          <small>并发参数由统一配置管理，连续错误会自动停止</small>
        </div>

        {job && job.status !== 'idle' && (
          <div className="download-progress">
            <div>
              <span>
                {job.kind === 'download' ? '全市场下载' : '增量更新'}
                {' · '}
                {statusLabel[job.status] || job.status}
                {job.current_symbol ? ` · ${job.current_symbol}` : ''}
              </span>
              <span className="mono">{job.completed} / {job.total}</span>
            </div>
            <Progress
              percent={job.percent}
              size="small"
              status={
                job.status === 'failed'
                  ? 'exception'
                  : job.status === 'completed'
                    ? 'success'
                    : job.running && job.status !== 'paused'
                      ? 'active'
                      : 'normal'
              }
            />
            <div className="job-summary">
              <Tag color="success">更新 {job.updated || 0}</Tag>
              <Tag>跳过 {job.skipped || 0}</Tag>
              <Tag color={job.failed ? 'error' : 'default'}>失败 {job.failed || 0}</Tag>
              {job.elapsed_s ? <span>耗时 {job.elapsed_s}s</span> : null}
              {job.running && job.speed ? <span>{job.speed} 只/秒</span> : null}
              {job.running && job.eta_s ? <span>预计剩余 {Math.ceil(job.eta_s / 60)} 分钟</span> : null}
              {job.running && job.id && (
                <Space size={4}>
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

        <div className="data-table-tools">
          <div className="table-caption">本地已缓存 {cache.length} 个标的</div>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索股票代码"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            style={{ width: 220 }}
          />
        </div>
        <Table
          dataSource={filteredCache}
          columns={cacheColumns}
          rowKey="symbol"
          size="small"
          pagination={{
            pageSize: 50,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 只`,
          }}
          loading={loading}
          scroll={{ y: 380, x: 560 }}
        />
      </Modal>
    </>
  );
}
