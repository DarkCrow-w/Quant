# quant 项目架构文档

本文档是后续一起开发 `quant` 项目的工作地图，记录当前代码事实、核心链路、扩展点和需要小心的地方。

## 1. 项目定位

`quant` 是一个面向 A 股的量化研究与交易平台，当前包含：

1. 事件驱动回测引擎。
2. 实盘执行入口，当前通过 Futu OpenAPI 下单。
3. 本地 Parquet 行情数据仓库与指标预计算。
4. 策略信号选股与多因子评分选股。
5. FastAPI 后端服务。
6. React + Vite 前端控制台。
7. 预留的 LangGraph Agent 能力。

整体可以理解为：

```text
web/ 或 app.py
  -> server/ API 或本地脚本
    -> quant/ 内核
      -> data/market/*.parquet 本地行情与指标数据
```

## 2. 顶层目录

```text
quant/
  app.py                 Streamlit 本地回测页面
  run_backtest.py        命令行回测入口
  run_live.py            实盘入口
  quant.sh               前后端启停脚本
  pyproject.toml         Python 依赖与包配置
  configs/               回测与实盘 YAML 配置
  data/                  本地行情、指标、元数据与历史备份
  docs/                  项目文档
  quant/                 量化内核
  server/                FastAPI 后端
  tests/                 pytest 测试，目前主要覆盖 screening
  web/                   React 前端
```

## 3. 技术栈

后端与量化内核：

- Python 3.10+
- pandas、numpy、pyarrow
- FastAPI、uvicorn
- loguru、rich
- akshare、tushare、baostock、pytdx
- futu-api
- apscheduler
- langchain、langgraph、langchain-anthropic，当前 Agent 入口未启用

前端：

- React 19
- TypeScript
- Vite
- Ant Design
- Zustand
- ECharts
- Axios

## 4. 核心分层

### 4.1 量化内核：`quant/`

`quant/core/` 定义交易领域对象：

- `Bar`：单根 K 线。
- `MarketEvent`：行情事件。
- `SignalEvent`：策略信号。
- `OrderEvent`：订单。
- `FillEvent`：成交回报。
- `Portfolio` 与 `Position`：现金、持仓、权益和手续费。

`quant/strategy/` 定义策略接口与示例策略：

- `Strategy.on_bar(ctx)` 是策略唯一核心入口。
- `Context` 提供当前 bars、历史 bars、组合快照和当前日期。
- 已注册到后端的策略主要在 `server/services/backtest_service.py` 的 `STRATEGY_REGISTRY` 中维护：
  - `ma_cross`
  - `vol_kdj_bbi`
  - `bbi_kdj_trend`
  - `dip_buy`

`quant/risk/` 负责信号到订单：

- `RiskManager.approve(signal, portfolio, price)` 返回 `OrderEvent | None`。
- `BasicRiskManager` 按最大仓位比例和最大回撤做控制。

`quant/execution/` 负责订单到成交：

- `SimulatedBroker` 用于回测，按给定价格立即成交。
- `FutuBroker` 用于实盘，封装 Futu OpenAPI。

`quant/engine/` 负责调度：

- `BacktestEngine` 是事件驱动回测引擎。
- `LiveEngine` 是实盘 tick 引擎，通过 APScheduler 定时运行。

### 4.2 数据层：`quant/data/`

数据层目前有新旧两套能力并存。

推荐的新路径是 DataStore v2：

```text
quant/data/
  store.py          DataStore 统一读写门面
  schema.py         频率、标准列、Parquet metadata 读写
  indicators.py     指标注册表与计算
  updater.py        update_universe、refresh_calendar、derive_week_month
  symbols.py        股票代码格式归一
  feeds/
    store_feed.py   基于 DataStore 的 DataFeed 实现
    tdx.py          TDX 数据源
    akshare.py      AKShare 数据源
    tushare.py      Tushare 数据源
    csv.py          CSV 数据源
```

磁盘布局：

```text
data/
  market/
    day/{symbol}.parquet
    week/{symbol}.parquet
    month/{symbol}.parquet
  meta/
    symbols.parquet
    trade_calendar.parquet
    last_update.parquet
```

当前本地数据大致状态：

- `data/market/day`：5212 个 parquet。
- `data/market/week`：5205 个 parquet。
- `data/market/month`：5205 个 parquet。
- `data/meta`：已有 `symbols.parquet`、`trade_calendar.parquet`、`last_update.parquet`。

DataStore 的职责：

- 读取 K 线：`get_store().get_kline(symbol, freq, start, end, with_indicators=True)`。
- 读取单个指标：`get_indicator(symbol, name, freq, start, end)`。
- 写入增量 K 线：`upsert_kline()`。
- 根据指标版本 metadata 按需重算指标。
- 提供股票池、交易日历、最近更新时间。

旧路径仍在使用：

- `quant/data/akshare_feed.py`
- `quant/data/tushare_feed.py`
- `quant/data/baostock_feed.py`
- `quant/data/tdx_feed.py`
- `quant/data/csv_feed.py`
- `quant/data/cache.py`

注意：当前 Web 回测服务仍使用旧 feed 回退链；行情查询和选股服务主要使用 DataStore。

### 4.3 选股层：`quant/screening/`

`quant/screening` 是多因子评分选股核心：

- `patterns.py`：高级形态识别，例如沙漏、蜈蚣图、三浪、麒麟阶段。
- `factors.py`：五维因子评分，分别是 trend、momentum、volume、dip、risk。
- `scoring.py`：`MultiFactorScorer` 加权汇总因子分，叠加形态调整，并输出评级、理由、风险提示和过滤标记。

服务层入口在 `server/services/screening_service.py`：

- `run_screening()`：策略信号选股，逐只股票回放策略，只看最后一根 K 线是否有 BUY 信号。
- `run_scoring()`：多因子评分选股，读取 DataStore 中带指标的 K 线，并行评分，按过滤结果和综合分排序。

## 5. 后端结构

`server/main.py` 创建 FastAPI 应用，当前挂载：

- `server.routers.backtest`
- `server.routers.strategy`
- `server.routers.market`
- `server.routers.screening`

Agent 路由存在于 `server/agent/router.py`，并由 `server/main.py` 尝试挂载。没有安装 Agent 依赖或没有配置模型 API Key 时，`/api/agent/status` 会返回不可用原因，核心量化 API 不受影响。

### 5.1 主要 API

健康检查：

- `GET /api/health`

策略：

- `GET /api/strategy/list`

回测：

- `POST /api/backtest/run`

行情与数据：

- `GET /api/market/kline`
- `GET /api/market/indicator/{name}`
- `GET /api/market/indicators`
- `GET /api/market/universe`
- `GET /api/market/calendar`
- `GET /api/market/cache`
- `GET /api/market/cache/status`
- `POST /api/market/update`，旧缓存更新接口
- `GET /api/market/stocks`
- `POST /api/market/download-all`
- `GET /api/market/download-all/progress`
- `POST /api/market/v2/update`，DataStore 增量更新
- `POST /api/market/v2/resample`，day 转 week 或 month
- `POST /api/market/v2/refresh-calendar`
- `POST /api/market/v2/refresh-universe`

选股：

- `POST /api/screening/scan`
- `POST /api/screening/score`
- `GET /api/screening/factors`

### 5.2 服务层职责

`server/services/backtest_service.py`：

- 维护策略注册表。
- 构造数据源、策略、风控、模拟经纪商和回测引擎。
- 计算收益率、年化收益、最大回撤、胜率、夏普、盈亏比等指标。
- 返回前端需要的权益曲线、交易记录、K 线数据。

`server/services/market_service.py`：

- 从 DataStore 读取 K 线、指标、股票池、交易日历和缓存状态。

`server/services/screening_service.py`：

- 支持策略信号选股和多因子评分选股。
- 通过 `ThreadPoolExecutor(max_workers=8)` 并行扫描本地股票池。
- 单票异常会被吞掉，不中断整体扫描。

## 6. 前端结构

`web/src/App.tsx` 是当前页面切换壳，页面有：

- `BacktestPage`
- `ScreeningPage`
- `AgentPage`

布局：

- `components/layout/AppLayout.tsx`
- `components/layout/Header.tsx`
- `components/layout/Sidebar.tsx`，回测侧边栏。
- `components/layout/ScreeningSidebar.tsx`，选股侧边栏。

状态：

- `stores/backtest.ts`
- `stores/screening.ts`
- `stores/agent.ts`

API：

- `api/client.ts`：回测、策略、行情、选股 REST API。
- `api/agent.ts`：Agent WebSocket 与会话 API。

注意：

- `ScreeningStore` 当前默认 `mode: "score"`，也就是多因子评分选股是默认入口。
- `AgentPage` 和相关前端组件已经接入后端 Agent 路由；未配置模型 API Key 时页面会展示未启用状态。

## 7. 关键执行链路

### 7.1 Web 回测链路

```text
BacktestPage / Sidebar
  -> web/src/stores/backtest.ts
  -> POST /api/backtest/run
  -> server/services/backtest_service.py
  -> 旧 feed 回退链：TuShareFeed -> BaostockFeed -> AKShareFeed
  -> Strategy.on_bar()
  -> BasicRiskManager.approve()
  -> SimulatedBroker.submit_order()
  -> Portfolio.update_on_fill()
  -> BacktestResult
```

`BacktestEngine` 的核心时序：

1. `feed.update()` 推进到下一批 bars。
2. 用当前 bar 的 open 成交上一轮 pending orders。
3. 构造 `Context`。
4. 调用策略 `on_bar()`。
5. 风控把信号转为订单。
6. 订单进入 pending，下一根 bar 再成交。
7. 记录权益曲线。

### 7.2 命令行回测链路

```text
python run_backtest.py
  -> 读取 CLI 参数或 configs/*.yaml
  -> build_feed()
  -> BacktestEngine.run()
  -> results/trades.csv
  -> results/report.html
```

目前 `run_backtest.py` 的策略映射只包含 `ma_cross`，比后端策略注册表少。

### 7.3 多因子选股链路

```text
ScreeningPage / ScreeningSidebar
  -> web/src/stores/screening.ts
  -> POST /api/screening/score
  -> run_scoring()
  -> get_store().list_symbols("day")
  -> get_store().get_kline(symbol, with_indicators=True)
  -> MultiFactorScorer.score()
  -> ScoreResult
```

### 7.4 策略信号选股链路

```text
POST /api/screening/scan
  -> run_screening()
  -> get_store().list_symbols("day")
  -> 每只股票构造 Bar 列表
  -> 回放 Strategy.on_bar()
  -> 最后一根 K 线出现 BUY 信号则命中
```

### 7.5 行情更新链路

推荐使用 v2：

```text
POST /api/market/v2/update
  -> update_universe()
  -> TDXSource -> AKShareSource -> TushareSource 回退链
  -> DataStore.upsert_kline()
  -> 自动重算指标
  -> 更新 last_update
```

周线和月线不走外部数据源：

```text
POST /api/market/v2/resample?target_freq=week
  -> derive_week_month()
  -> 从 day parquet 重采样
```

### 7.6 实盘链路

```text
python run_live.py configs/live_ma_cross.yaml
  -> AKShareFeed
  -> MACrossStrategy
  -> BasicRiskManager
  -> FutuBroker
  -> LiveEngine.run()
  -> APScheduler 定时 tick()
```

实盘目前只在入口映射了 `ma_cross`，数据源也固定为 `AKShareFeed`。

## 8. 扩展指南

### 8.1 新增策略

1. 在 `quant/strategy/examples/` 或新的策略目录实现 `Strategy` 子类。
2. 实现 `on_bar(ctx) -> list[SignalEvent]`。
3. 在 `server/services/backtest_service.py` 的 `STRATEGY_REGISTRY` 注册策略和参数 schema。
4. 如果需要命令行支持，同步更新 `run_backtest.py` 的 `STRATEGY_MAP`。
5. 如果需要实盘支持，同步更新 `run_live.py` 的 `STRATEGY_MAP` 和配置文件。

### 8.2 新增指标

1. 在 `quant/data/indicators.py` 中添加指标计算函数与 `IndicatorSpec`。
2. 更新输出列、lookback、version。
3. 读取 `get_kline(..., with_indicators=True)` 时，DataStore 会根据版本与缺失列自动重算。

### 8.3 新增选股因子

1. 在 `quant/screening/factors.py` 添加评分函数。
2. 更新 `FACTOR_DEFS`。
3. 在 `quant/screening/scoring.py` 合并权重、调用评分、输出字段。
4. 更新 `server/models/screening.py` 和 `web/src/types/index.ts`。
5. 更新 `web/src/stores/screening.ts` 与筛选页面展示。

### 8.4 统一回测数据路径

这是后续值得优先做的工程改进。目标是让 `server/services/backtest_service.py` 从旧 feed 回退链迁移到 `StoreFeed`：

```python
from quant.data import StoreFeed
```

这样回测、行情和选股都能基于同一套 DataStore 数据与指标版本。

## 9. 运行命令

一键启动前后端：

```bash
./quant.sh start
```

后端：

```bash
uvicorn server.main:app --host 0.0.0.0 --port 8001 --reload
```

前端：

```bash
cd web
pnpm install
pnpm dev --host 0.0.0.0
```

命令行回测：

```bash
python run_backtest.py
python run_backtest.py -s 600519 000858
python run_backtest.py -c configs/backtest_ma_cross.yaml
```

实盘：

```bash
python run_live.py configs/live_ma_cross.yaml
```

测试：

```bash
python -m pytest tests/screening -q
```

前端检查：

```bash
cd web
pnpm build
pnpm lint
```

## 10. 当前注意点

1. 外层 `quantlab` 不是 Git 仓库，`quant/` 自己是 Git 仓库；在 Windows/WSL UNC 路径下执行 `git status` 可能触发 safe.directory 限制。
2. Agent 后端已挂载；模型 Key 未配置时仅 Agent 对话不可用，其他模块可继续使用。
3. 回测服务仍走旧 feed，选股与行情主要走 DataStore，数据路径尚未完全统一。
4. `run_backtest.py` 与 `run_live.py` 的策略映射比后端少。
5. 部分源码注释或中文标签在当前终端读取时可能出现乱码，修改中文文案前应先确认文件真实编码。
6. `screening_service.py` 会吞掉单票异常，这保证批量扫描不中断，但调试时可能需要临时加日志。
7. `update_universe(force=True)` 会触发更重的数据刷新；增量更新和复权漂移需要结合数据源行为谨慎处理。

## 11. 后续协作默认认知

之后在这个项目上工作时，默认采用以下判断：

1. 新行情能力优先基于 DataStore v2。
2. 新前端功能优先走 FastAPI 服务层，而不是直接绕到内核。
3. 新策略先保证可回测，再考虑选股和实盘。
4. 改数据结构时同时检查 Python model、TypeScript type、store、页面表格和图表。
5. 涉及行情数据或选股性能时，优先考虑本地 Parquet 批量读取和指标预计算，不轻易引入数据库。
