# QuantLab 架构说明

本文档记录当前代码库的实际架构，作为继续开发和上线检查的工程地图。功能使用说明见 `docs/QUANTLAB_GUIDE.md`，部署说明见 `docs/DEPLOYMENT.md`。

## 1. 系统定位

QuantLab 是面向 A 股研究和本地回测的量化平台。当前能力包括：

1. 行情缓存、股票池和指标查询。
2. 策略资产管理。
3. 单次回测和参数网格回测。
4. 研究资产沉淀、收藏、标签、备注、对比和报告导出。
5. 因子库管理和基础 IC 因子挖掘。
6. 策略组合、因子评分和经典信号选股。
7. 独立风控规则管理和评估。
8. 实盘配置只读检查。
9. 可选 AI 研究员入口。

## 2. 顶层结构

```text
web/                 React + Vite 控制台
server/              FastAPI API 与业务服务
quant/               量化内核
data/                行情缓存和 SQLite 元数据
config/              环境配置模板
scripts/             验证、迁移和部署检查脚本
docs/                使用、架构和部署文档
app.py               Streamlit 快速回测入口
run_backtest.py      命令行回测入口
run_live.py          人工实盘启动入口
```

## 3. 前端

目录：`web/`

技术栈：

- React
- TypeScript
- Vite
- Ant Design
- Zustand
- ECharts
- Axios

主要页面：

- `DashboardPage.tsx`：系统总览。
- `DataPage.tsx`：数据平台。
- `StrategyPage.tsx`：策略管理。
- `BacktestPage.tsx`：回测研究。
- `ResearchPage.tsx`：研究资产。
- `FactorPage.tsx`：因子研究。
- `ScreeningPage.tsx`：智能选股。
- `RiskPage.tsx`：风险控制。
- `TradingPage.tsx`：交易运行。
- `AgentPage.tsx`：AI 研究员。

API 封装：

- `web/src/api/client.ts`：核心 REST API。
- `web/src/api/agent.ts`：AI 研究员 WebSocket 和会话 API。

主要状态：

- `web/src/stores/backtest.ts`
- `web/src/stores/screening.ts`
- `web/src/stores/agent.ts`

## 4. 后端

目录：`server/`

技术栈：

- FastAPI
- Pydantic
- SQLite
- pandas

入口：

- `server/main.py`

路由层：

- `server/routers/backtest.py`
- `server/routers/market.py`
- `server/routers/strategy.py`
- `server/routers/strategy_assets.py`
- `server/routers/screening.py`
- `server/routers/research.py`
- `server/routers/factors.py`
- `server/routers/risk.py`
- `server/routers/system.py`
- `server/routers/trading.py`
- `server/agent/router.py`

服务层：

- `backtest_service.py`：回测、策略 schema、参数网格。
- `market_service.py`：K 线、指标、缓存和股票池查询。
- `data_job_service.py`：数据更新和下载任务。
- `strategy_asset_service.py`：策略资产持久化。
- `factor_strategy_service.py`：策略组合选股配置。
- `screening_service.py`：经典信号、因子评分和策略组合扫描。
- `research_service.py`：研究资产、元数据和报告。
- `factor_service.py`：因子库和基础 IC 挖掘。
- `risk_service.py`：风控规则和评估。
- `system_service.py`：系统状态和上线检查。
- `trading_service.py`：实盘配置只读检查。

模型层：

- `server/models/*.py` 使用 Pydantic 定义请求和响应结构。

## 5. 量化内核

目录：`quant/`

核心模块：

- `quant/core/`：Bar、Signal、Order、Fill、Position、Portfolio 等领域对象。
- `quant/data/`：数据源、缓存、股票池、指标和迁移工具。
- `quant/engine/`：回测引擎和实盘引擎。
- `quant/execution/`：模拟经纪商和 Futu 经纪商。
- `quant/risk/`：风控接口和基础风控实现。
- `quant/strategy/`：策略基类、基础策略注册表和示例策略。

基础策略：

- `ma_cross`
- `vol_kdj_bbi`
- `bbi_kdj_trend`
- `dip_buy`
- `swing_dip_buy`

## 6. 数据和元数据

```text
data/
├─ cache / market data files        行情缓存
└─ meta/
   ├─ symbols.parquet               股票池
   ├─ strategies.sqlite3            策略资产
   ├─ factor_strategies.sqlite3     组合选股策略
   ├─ research.sqlite3              研究资产
   ├─ factors.sqlite3               因子库
   ├─ risk.sqlite3                  风控规则
   └─ data_jobs.sqlite3             数据任务
```

运行配置：

- `config/quant.env.example`：本地配置模板。
- `config/quant.prod.env.example`：容器化部署配置模板。
- `config/quant.env`：本地实际配置，不提交 Git。

## 7. 主要调用链

回测：

```text
BacktestPage / app.py / run_backtest.py
  -> BacktestRequest
  -> server.services.backtest_service.run_backtest
  -> DataFeed
  -> Strategy
  -> BasicRiskManager
  -> SimulatedBroker
  -> BacktestEngine
  -> BacktestResult
  -> 研究资产可选持久化
```

选股：

```text
ScreeningPage
  -> screening router
  -> screening_service
  -> 本地缓存行情
  -> 策略组合 / 因子评分 / 经典信号
  -> 筛选结果和 K 线联动
```

数据任务：

```text
DataPage
  -> market router
  -> data_job_service
  -> market_service / data updater
  -> data/meta/data_jobs.sqlite3
  -> 前端轮询进度
```

因子挖掘：

```text
FactorPage
  -> POST /api/factors/mine
  -> factor_service.mine_factors
  -> 本地缓存行情
  -> Spearman IC / 样本数 / 覆盖率
```

## 8. 验证入口

常用命令：

```powershell
.\.venv\Scripts\python.exe -m pytest
cd web
npm run lint
npm run build
```

clone 级验证：

```powershell
.\.venv\Scripts\python.exe scripts\verify_clone_start.py
.\.venv\Scripts\python.exe scripts\verify_deployment_config.py
.\.venv\Scripts\python.exe scripts\verify_runtime_smoke.py
.\.venv\Scripts\python.exe scripts\verify_strategy_consistency.py
```

上线前应同时完成：

1. 后端测试通过。
2. 前端 lint 和 build 通过。
3. 数据平台能看到全市场股票池。
4. 回测研究能完成一次回测并写入研究资产。
5. 因子研究能返回候选因子 IC。
6. 智能选股能扫描本地缓存标的。
7. 风险控制能执行规则评估。
8. 交易运行页面保持只读，不从 Web 自动下单。
