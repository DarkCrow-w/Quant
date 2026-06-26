import { useEffect, useState } from 'react';
import { App as AntdApp, Button, Drawer, Form, Input, InputNumber, Popconfirm, Space, Switch, Table, Tag, Tooltip } from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined, SafetyCertificateOutlined, SaveOutlined } from '@ant-design/icons';
import {
  createRiskRule,
  deleteRiskRule,
  evaluateRisk,
  fetchRiskRules,
  updateRiskRule,
} from '../api/client';
import type { RiskEvaluationResult, RiskRule, RiskRuleDraft } from '../types';

const emptyRule: RiskRuleDraft = {
  name: '',
  description: '',
  max_position_pct: 0.3,
  max_drawdown: 0.2,
  max_single_order_pct: 0.1,
  stop_loss_pct: 0.08,
  take_profit_pct: 0.25,
  max_symbols: 10,
  enabled: true,
};

export default function RiskPage() {
  const { message } = AntdApp.useApp();
  const [rules, setRules] = useState<RiskRule[]>([]);
  const [editing, setEditing] = useState<RiskRule | null>(null);
  const [draft, setDraft] = useState<RiskRuleDraft>(emptyRule);
  const [open, setOpen] = useState(false);
  const [evaluation, setEvaluation] = useState<RiskEvaluationResult | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      setRules(await fetchRiskRules());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function startCreate() {
    setEditing(null);
    setDraft(emptyRule);
    setOpen(true);
  }

  function startEdit(row: RiskRule) {
    setEditing(row);
    setDraft({ ...row });
    setOpen(true);
  }

  async function save() {
    if (!draft.name.trim()) {
      message.warning('请填写风控规则名称');
      return;
    }
    if (editing) await updateRiskRule(editing.id, draft);
    else await createRiskRule(draft);
    message.success('风控规则已保存');
    setOpen(false);
    await load();
  }

  async function remove(row: RiskRule) {
    await deleteRiskRule(row.id);
    message.success('风控规则已删除');
    await load();
  }

  async function runEvaluation(row: RiskRule) {
    setEvaluation(await evaluateRisk(row.id));
  }

  return (
    <div className="asset-page">
      <div className="workspace-heading">
        <div>
          <h1>风险控制</h1>
          <p>独立管理仓位、回撤、单笔订单、止损止盈和持仓数量规则</p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={startCreate}>新建风控规则</Button>
      </div>

      <div className="asset-grid">
        <div className="surface-panel">
          <div className="panel-heading">风控规则</div>
          <Table
            rowKey="id"
            loading={loading}
            dataSource={rules}
            pagination={{ pageSize: 10 }}
            columns={[
              { title: '名称', dataIndex: 'name', render: (v, row) => <Space><strong>{v}</strong>{row.enabled ? <Tag color="success">启用</Tag> : <Tag>停用</Tag>}</Space> },
              { title: '最大仓位', dataIndex: 'max_position_pct', render: (v) => `${Math.round(v * 100)}%` },
              { title: '最大回撤', dataIndex: 'max_drawdown', render: (v) => `${Math.round(v * 100)}%` },
              { title: '单笔订单', dataIndex: 'max_single_order_pct', render: (v) => `${Math.round(v * 100)}%` },
              { title: '持仓数', dataIndex: 'max_symbols' },
              {
                title: '操作',
                width: 170,
                render: (_, row) => (
                  <Space>
                    <Tooltip title="运行样例评估">
                      <Button icon={<SafetyCertificateOutlined />} onClick={() => runEvaluation(row)} />
                    </Tooltip>
                    <Tooltip title="编辑规则">
                      <Button icon={<EditOutlined />} onClick={() => startEdit(row)} />
                    </Tooltip>
                    <Popconfirm
                      title="删除这条风控规则？"
                      description="删除后无法在当前规则库中继续使用。"
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      onConfirm={() => remove(row)}
                    >
                      <Tooltip title="删除规则">
                        <Button danger icon={<DeleteOutlined />} />
                      </Tooltip>
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
          />
        </div>
        <div className="surface-panel">
          <div className="panel-heading">规则评估样例</div>
          <div className="risk-check-list">
            {(evaluation?.checks || []).map((check) => (
              <div key={check.key}>
                <Tag color={check.passed ? 'success' : 'error'}>{check.passed ? 'PASS' : 'BLOCK'}</Tag>
                <strong>{check.label}</strong>
                <span>{check.message}</span>
              </div>
            ))}
            {!evaluation && <div className="empty-inline">点击规则行的盾牌按钮运行样例评估</div>}
          </div>
        </div>
      </div>

      <Drawer title={editing ? '编辑风控规则' : '新建风控规则'} open={open} size="large" onClose={() => setOpen(false)}>
        <Form layout="vertical">
          <Form.Item label="名称"><Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Form.Item>
          <Form.Item label="说明"><Input.TextArea rows={3} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Form.Item>
          <Form.Item label="最大仓位比例"><InputNumber style={{ width: '100%' }} min={0.01} max={1} step={0.01} value={draft.max_position_pct} onChange={(v) => setDraft({ ...draft, max_position_pct: Number(v ?? 0.3) })} /></Form.Item>
          <Form.Item label="最大回撤"><InputNumber style={{ width: '100%' }} min={0.01} max={1} step={0.01} value={draft.max_drawdown} onChange={(v) => setDraft({ ...draft, max_drawdown: Number(v ?? 0.2) })} /></Form.Item>
          <Form.Item label="单笔订单上限"><InputNumber style={{ width: '100%' }} min={0.01} max={1} step={0.01} value={draft.max_single_order_pct} onChange={(v) => setDraft({ ...draft, max_single_order_pct: Number(v ?? 0.1) })} /></Form.Item>
          <Form.Item label="止损比例"><InputNumber style={{ width: '100%' }} min={0.01} max={1} step={0.01} value={draft.stop_loss_pct} onChange={(v) => setDraft({ ...draft, stop_loss_pct: Number(v ?? 0.08) })} /></Form.Item>
          <Form.Item label="止盈比例"><InputNumber style={{ width: '100%' }} min={0.01} max={5} step={0.01} value={draft.take_profit_pct} onChange={(v) => setDraft({ ...draft, take_profit_pct: Number(v ?? 0.25) })} /></Form.Item>
          <Form.Item label="最大持仓标的数"><InputNumber style={{ width: '100%' }} min={1} max={500} value={draft.max_symbols} onChange={(v) => setDraft({ ...draft, max_symbols: Number(v ?? 10) })} /></Form.Item>
          <Form.Item label="启用"><Switch checked={draft.enabled} onChange={(enabled) => setDraft({ ...draft, enabled })} /></Form.Item>
          <Button type="primary" icon={<SaveOutlined />} block onClick={save}>保存规则</Button>
        </Form>
      </Drawer>
    </div>
  );
}
