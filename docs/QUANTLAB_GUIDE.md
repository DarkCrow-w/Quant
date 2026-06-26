# QuantLab 功能、使用与架构指南

本文档面向两类用户：想直接体验系统的人，以及准备继续开发量化平台的工程人员。内容以当前代码库的实际能力为准。

## 1. 功能总览

QuantLab 当前是一套本地可运行的量化研究控制台，主入口是 `web/` React 前端，后端是 `server/` FastAPI 服务，底层复用 `quant/` 量化内核。

### 系统总览

入口：顶部导航 `系统总览`

能力：

- 查看 API 健康状态、版本、数据缓存、策略库、指标库、研究资产、AI 配置、实盘配置和部署检查。
- 展示数据准备度和上线前建议。
- 快速跳转到数据平台、回测研究、智能选股、研究资产和 AI 研究员。

接口：

- `GET /api/health`
- `GET /api/system/status`
- `GET /api/agent/status`
- `GET /api/market/jobs/current`

### 数据平台

入口：顶部导航 `数据平台`

能力：

- 查看本地行情缓存、全市场股票池、指标目录和最新数据日期。
- 从通达信或 TuShare 拉取行情。
- 刷新 A 股股票池，避免全市场任务只覆盖已缓存股票。
- 提交全市场下载、已有缓存增量更新、任务进度查看、暂停、恢复和取消。
- 查询日线、周线、月线和技术指标。

接口：

- `GET /api/market/cache`
- `GET /api/market/cache/status`
- `GET /api/market/universe`
- `GET /api/market/kline`
- `GET /api/market/indicators`
- `POST /api/market/update`
- `POST /api/market/download-all`
- `GET /api/market/jobs/current`
- `POST /api/market/jobs/{job_id}/pause`
- `POST /api/market/jobs/{job_id}/resume`
- `POST /api/market/jobs/{job_id}/cancel`

### 策略管理

入口：顶部导航 `策略管理`

能力：

- 管理可复用策略资产，支持新建、编辑、删除、启停、标签和说明。
- 每个策略资产绑定一个基础策略，例如 `ma_cross`、`vol_kdj_bbi`、`bbi_kdj_trend`、`dip_buy`。
- 策略参数表单根据基础策略 schema 生成。
- 策略管理里的策略库会同步到回测研究页面，作为可选回测策略。

存储：

- `data/meta/strategies.sqlite3`

接口：

- `GET /api/strategy/list`
- `GET /api/strategy/assets`
- `GET /api/strategy/assets/{asset_id}`
- `POST /api/strategy/assets`
- `PUT /api/strategy/assets/{asset_id}`
- `DELETE /api/strategy/assets/{asset_id}`

### 回测研究

入口：顶部导航 `回测研究`

能力：

- 从策略库选择策略，配置标的、日期、初始资金、最大仓位和最大回撤。
- 运行单次回测，展示绩效指标、K 线、叠加指标、权益曲线和成交记录。
- 回测成功后自动写入研究资产库。
- 支持参数网格实验。

接口：

- `GET /api/strategy/list`
- `POST /api/backtest/run`
- `POST /api/backtest/grid`

### 研究资产

入口：顶部导航 `研究资产`

能力：

- 管理历史回测实验。
- 查看收益、回撤、交易数、权益曲线和请求参数。
- 收藏实验，维护标签和备注。
- 按收藏或标签过滤实验。
- 选择 2 到 6 条实验进行对比。
- 导出 Markdown 研究报告。

存储：

- `data/meta/research.sqlite3`

接口：

- `GET /api/research/summary`
- `GET /api/research/backtests`
- `GET /api/research/backtests/{run_id}`
- `PATCH /api/research/backtests/{run_id}/metadata`
- `POST /api/research/reports/backtests.md`

### 因子研究

入口：顶部导航 `因子研究`

能力：

- 管理内置因子和自定义因子，支持新建、编辑、删除、启停、分类和默认权重。
- 自定义因子保留表达式字段，例如 `close / close.shift(20) - 1`。
- 因子挖掘会扫描本地缓存行情，计算候选因子的 Spearman IC、样本数、覆盖率和方向。
- 当前候选因子包含动量、波动率、量比、均线偏离和成交额类基础因子。

存储：

- `data/meta/factors.sqlite3`

接口：

- `GET /api/factors`
- `POST /api/factors`
- `PUT /api/factors/{factor_id}`
- `DELETE /api/factors/{factor_id}`
- `POST /api/factors/mine`

### 智能选股

入口：顶部导航 `智能选股`

能力：

- 支持策略组合、因子评分和经典信号三种模式。
- 策略组合模式支持多条件组合、条件权重、必选条件、阈值过滤和保存策略。
- 因子评分模式支持权重调节和过滤条件。
- 经典信号模式复用策略信号进行批量筛选。
- 结果支持表格排序、命中原因展示和 K 线联动查看。

接口：

- `POST /api/screening/scan`
- `POST /api/screening/score`
- `GET /api/screening/factors`
- `GET /api/screening/composer/metrics`
- `GET /api/screening/composer/strategies`
- `POST /api/screening/composer/strategies`
- `PUT /api/screening/composer/strategies/{strategy_id}`
- `DELETE /api/screening/composer/strategies/{strategy_id}`
- `POST /api/screening/composer/scan`

### 风险控制

入口：顶部导航 `风险控制`

能力：

- 独立管理风控规则，支持新建、编辑、删除和启停。
- 规则覆盖最大仓位、最大回撤、单笔订单上限、止损、止盈和最大持仓标的数。
- 页面提供规则评估样例，用于验证持仓、订单和回撤输入是否会被拦截。
- 后续实盘或模拟交易模块可以复用该规则存储与评估服务。

存储：

- `data/meta/risk.sqlite3`

接口：

- `GET /api/risk/rules`
- `POST /api/risk/rules`
- `PUT /api/risk/rules/{rule_id}`
- `DELETE /api/risk/rules/{rule_id}`
- `POST /api/risk/evaluate`

### 交易运行

入口：顶部导航 `交易运行`

能力：

- 只读展示实盘配置、策略参数、标的列表、风控参数、券商通道和调度计划。
- 给出实盘启动命令和仿真回测验证命令。
- 展示人工确认清单。

安全边界：

- Web UI 不连接券商。
- Web UI 不解锁账户。
- Web UI 不自动下单。
- 实盘必须通过人工运行 `python run_live.py configs/live_ma_cross.yaml` 启动。

接口：

- `GET /api/trading/status`

### AI 研究员

入口：顶部导航 `AI 研究员`

能力：

- 提供面向量化研究的对话入口。
- 在依赖和 API Key 可用时，后端会挂载 `/api/agent` 路由。
- 未配置模型 Key 时，页面只展示状态，不影响回测、数据、选股、因子或研究资产模块。

常用配置：

- `DEEPSEEK_API_KEY`
- `ANTHROPIC_API_KEY`
- 其他模型和端点配置见 `config/quant.env.example`

## 2. 快速体验

Windows：

```powershell
cp config\quant.env.example config\quant.env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --prefer-binary -r requirements.txt
cd web
npm ci
cd ..
.\quant.ps1 start
```

访问：

- 控制台：`http://127.0.0.1:5174`
- API 文档：`http://127.0.0.1:8001/docs`
- 健康检查：`http://127.0.0.1:8001/api/health`

停止：

```powershell
.\quant.ps1 stop
```

macOS / Linux：

```bash
cp config/quant.env.example config/quant.env
python -m venv .venv
. .venv/bin/activate
pip install --prefer-binary -r requirements.txt
cd web && npm ci && cd ..
./quant.sh start
```

## 3. 验证

clone 后建议执行：

```powershell
.\.venv\Scripts\python.exe scripts\verify_clone_start.py
.\.venv\Scripts\python.exe scripts\verify_deployment_config.py
.\.venv\Scripts\python.exe scripts\verify_frontend_text_smoke.py
.\.venv\Scripts\python.exe scripts\verify_runtime_smoke.py
.\.venv\Scripts\python.exe scripts\verify_fresh_clone_smoke.py
```

开发中常用验证：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\verify_frontend_text_smoke.py
cd web
npm run lint
npm run build
```

运行时烟测 `verify_runtime_smoke.py` 会先确保离线 demo 数据存在，再使用本地数据检查健康状态、系统总览、股票池、缓存、K 线、策略列表、回测、因子挖掘、智能选股、组合指标、风控评估和研究资产摘要。
前端文本烟测 `verify_frontend_text_smoke.py` 会扫描 React 源码和入口 HTML，拦截常见中文乱码、替换字符和问号乱码。

## 4. 架构

```text
用户
├─ web/                 React + Vite 控制台
├─ app.py               Streamlit 快速实验入口
└─ server/main.py       FastAPI API 入口

后端服务
├─ server/routers/      路由定义
├─ server/services/     业务编排
├─ server/models/       请求与响应模型
└─ server/agent/        可选 AI 研究员

量化内核
├─ quant/core/          Bar、Signal、Order、Fill、Portfolio 等基础对象
├─ quant/data/          数据源、缓存、增量更新、股票池
├─ quant/engine/        回测和实盘引擎
├─ quant/execution/     模拟经纪商和 Futu 经纪商
├─ quant/risk/          风控接口与实现
└─ quant/strategy/      策略基类和示例策略
```

主要调用链：

```text
React 页面
  -> web/src/api/client.ts
  -> FastAPI router
  -> service
  -> quant 内核或 SQLite 元数据
  -> API 响应
  -> 前端状态与图表
```

## 5. 数据和元数据

- 行情缓存：`data/`
- 策略资产：`data/meta/strategies.sqlite3`
- 研究资产：`data/meta/research.sqlite3`
- 因子库：`data/meta/factors.sqlite3`
- 风控规则：`data/meta/risk.sqlite3`
- 数据任务：`data/meta/data_jobs.sqlite3`

`config/quant.env` 不应提交到 Git。首次部署可从 `config/quant.env.example` 创建。

## 6. 上线前检查清单

1. `scripts/verify_clone_start.py` 通过。
2. `scripts/verify_deployment_config.py` 通过。
3. 系统总览没有必需项 `ERROR`。
4. 数据平台能看到全市场股票池，并且本地缓存不是只有一只股票。
5. 回测研究能用策略库中的策略完成一次回测。
6. 回测结果能进入研究资产库。
7. 因子研究能运行因子挖掘并返回样本数和 IC。
8. 智能选股能扫描本地缓存标的。
9. 风险控制能新建规则并运行评估。
10. 交易运行只展示人工启动信息，不通过 Web 自动下单。
