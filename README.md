# QuantLab

QuantLab 是一个本地可运行的量化研究与回测控制台，包含行情数据管理、策略管理、回测研究、研究资产、因子研究、智能选股、风险控制、交易运行检查和可选 AI 研究员入口。

当前推荐入口是 React 控制台和 FastAPI 后端。旧的 `app.py` Streamlit 页面仅作为本地快速实验入口保留。

## 快速启动

Windows 推荐流程：

```powershell
cp config\quant.env.example config\quant.env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --prefer-binary -r requirements.txt
cd web
npm ci
cd ..
.\quant.ps1 start
```

也可以直接双击：

```text
start-windows.cmd
```

启动后访问：

- 控制台：`http://127.0.0.1:5174`
- API 文档：`http://127.0.0.1:8001/docs`
- 健康检查：`http://127.0.0.1:8001/api/health`

停止服务：

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

## 首次 clone 验证

安装依赖后执行：

```powershell
.\.venv\Scripts\python.exe scripts\verify_clone_start.py
.\.venv\Scripts\python.exe scripts\verify_config_contract.py
.\.venv\Scripts\python.exe scripts\verify_data_integrity.py
.\.venv\Scripts\python.exe scripts\verify_strategy_consistency.py
.\.venv\Scripts\python.exe scripts\verify_deployment_config.py
.\.venv\Scripts\python.exe scripts\verify_frontend_api_contract.py
.\.venv\Scripts\python.exe scripts\verify_frontend_text_smoke.py
.\.venv\Scripts\python.exe scripts\verify_frontend_ui_inventory.py
.\.venv\Scripts\python.exe scripts\verify_launch_scripts.py
.\.venv\Scripts\python.exe scripts\verify_production_frontend_smoke.py
.\.venv\Scripts\python.exe scripts\verify_runtime_smoke.py
.\.venv\Scripts\python.exe scripts\verify_fresh_clone_smoke.py
.\.venv\Scripts\python.exe scripts\verify_windows_launch_smoke.py
```

这会验证后端导入、配置契约、部署配置、后端测试、运行时 API 冒烟、干净 clone 初始化、前端生产构建、前端文案编码，以及真实本地数据下的核心链路。

## 当前模块

- 系统总览：查看 API、数据缓存、策略库、指标库、研究资产、AI 和交易配置状态。
- 数据平台：查看全市场股票池、本地 K 线缓存、指标目录，提交增量更新或全市场下载任务。
- 策略管理：管理可复用策略资产，支持新建、编辑、删除、启停和参数配置。
- 回测研究：从策略库选择策略，运行单次回测或参数网格实验，结果自动写入研究资产库。
- 研究资产：管理历史回测记录，支持收藏、标签、备注、对比和 Markdown 报告导出。
- 因子研究：管理内置与自定义因子，运行基础 IC 因子挖掘。
- 智能选股：支持策略组合、因子评分和经典信号筛选。
- 风险控制：管理风控规则，并用样例持仓、订单和回撤输入进行规则评估。
- 交易运行：只读展示实盘配置和人工启动命令，不通过 Web UI 自动下单。
- AI 研究员：在配置模型 API Key 后提供量化研究对话能力；未配置时不影响其他模块。

## 数据说明

本地行情缓存位于 `data/`。首次 setup 或 clone 验证会离线生成一份 demo 数据：包含全市场规模的股票池元数据和 16 只常用标的的日线缓存，确保不联网也能打开页面、回测、选股和运行烟测。系统会优先使用本地缓存；数据平台可以刷新股票池、增量更新已有缓存，也可以提交全市场下载任务。全市场股票池不应只依赖已缓存标的，当前接口会从通达信或本地 universe 文件读取 A 股股票池。

常用检查接口：

- `GET /api/system/status`
- `GET /api/market/universe`
- `GET /api/market/cache`
- `GET /api/market/jobs/current`
- `POST /api/market/download-all`

## 架构

```text
web/                 React + Vite 控制台
server/              FastAPI API 与业务服务
quant/               量化内核：数据、策略、回测、执行、风控
data/                本地行情缓存和 SQLite 元数据
config/              环境配置模板
scripts/             clone、部署和数据验证脚本
docs/                使用、架构和部署文档
```

详细功能和架构说明见 [docs/QUANTLAB_GUIDE.md](docs/QUANTLAB_GUIDE.md)。

容器化部署说明见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。
