# QuantLab 部署指南

本文档说明当前代码库的本地验证和容器化部署方式。默认部署形态适合单机或小团队内网使用；公网生产环境还需要 HTTPS、认证、权限、审计、备份和监控。

## 1. 服务组成

容器化部署包含：

- `backend`：FastAPI + Uvicorn，默认端口 `8001`。
- `frontend`：Nginx 托管 React 构建产物，并反代 `/api`。
- `quantlab-data`：Docker volume，保存行情缓存、研究资产和数据任务记录。

本地开发启动使用：

- 后端：`uvicorn server.main:app`
- 前端：`npm run dev`

## 2. 配置准备

本地体验：

```bash
cp config/quant.env.example config/quant.env
```

容器化部署：

```bash
cp config/quant.prod.env.example config/quant.env
```

按需填写：

- `TUSHARE_TOKEN`
- `DEEPSEEK_API_KEY` 或 `ANTHROPIC_API_KEY`
- `FUTU_HOST` / `FUTU_PORT`
- `QUANT_CORS_ORIGINS`
- `QUANT_BACKEND_HOST`
- `QUANT_BACKEND_PORT`

不要把 `config/quant.env` 提交到 Git。

## 3. 本地校验

安装依赖后执行：

```bash
python scripts/verify_clone_start.py
python scripts/verify_deployment_config.py
python scripts/verify_runtime_smoke.py
python scripts/verify_fresh_clone_smoke.py
```

`verify_clone_start.py` 会检查后端导入、后端测试和前端生产构建。

`verify_deployment_config.py` 会检查 Docker、Nginx、Compose 和生产环境模板。

`verify_runtime_smoke.py` 会用本地真实数据检查核心 API 链路和数据准备度，适合在上线前最后执行。

开发中可以单独运行：

```bash
python -m pytest
cd web && npm run lint && npm run build
```

## 4. 启动容器

```bash
docker compose up --build -d
```

访问：

- 控制台：`http://127.0.0.1:8080`
- 健康检查：`http://127.0.0.1:8080/api/health`
- API 文档：`http://127.0.0.1:8080/docs`

## 5. 运维命令

```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f frontend
docker compose restart backend
docker compose down
```

保留数据并停止：

```bash
docker compose down
```

清空 volume 数据：

```bash
docker compose down -v
```

## 6. 上线前检查

1. `python scripts/verify_clone_start.py` 通过。
2. `python scripts/verify_deployment_config.py` 通过。
3. 控制台能访问，`/api/health` 返回 `status: ok`。
4. 系统总览没有必需项 `ERROR`。
5. 数据平台能看到全市场股票池和本地缓存状态。
6. 回测研究能完成一次回测，研究资产能看到新记录。
7. 因子研究能运行因子挖掘。
8. 智能选股能扫描本地缓存标的。
9. 风险控制能新建规则并运行评估。
10. 交易运行页面只展示人工启动命令，不允许 Web UI 自动下单。

## 7. 公网生产边界

当前 Compose 骨架适合单机或内网环境。公网生产环境还需要补齐：

- HTTPS 证书与域名网关。
- 登录、权限和审计。
- 数据库与行情缓存备份策略。
- 指标监控、日志采集和告警。
- 多实例任务锁和任务队列。
- API 限流和访问控制。

实盘交易仍应由人工在受控环境中启动，不应从 Web UI 直接解锁账户或自动下单。
