# AI ChatBot

基于 **FastAPI + MySQL + Redis** 的 AI 聊天后端，面向多用户、多会话场景，提供 OpenAI 兼容的 SSE 流式对话、上下文摘要、会话管理与用量配额。业务 API 统一使用 `/api/v1`。

## 主要能力

- **认证与安全**：注册/登录、JWT 刷新与撤销、邮箱验证、密码找回、登录锁定、Redis 限流、安全响应头。
- **AI 对话**：OpenAI 兼容接口、SSE 流式输出、`Last-Event-ID` 断点续传、上下文滚动摘要。
- **会话管理**：标题搜索、置顶、归档、keyset 分页、消息历史、JSON/Markdown 导出。
- **用量与配额**：按日统计 prompt/completion token，可配置每日额度并强制拦截。
- **运维基础**：健康检查、请求追踪、结构化日志、优雅停机、MySQL 备份/恢复。
- **可选能力**：读写分离、消息归档、写队列（默认关闭，按需启用）。

## 快速开始

### 1. 环境要求

- Python 3.12+
- Redis 6.2+
- 生产环境 MySQL 8+（`utf8mb4`）；本地开发可使用 SQLite
- OpenAI 兼容的 LLM 服务

### 2. 安装与配置

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # PowerShell: Copy-Item .env.example .env
```

至少配置：

- `DATABASE_URL`：MySQL 连接需带 `charset=utf8mb4`；SQLite 仅建议本地开发。
- `REDIS_URL`：会话、令牌、限流、缓存与流式事件依赖 Redis。
- `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。
- 邮箱验证/密码找回需配置 `SMTP_*` 与 `PUBLIC_API_BASE_URL`。

### 3. 初始化数据库

```bash
alembic upgrade head
```

### 4. 启动服务

```bash
# API（终端 1）
python -m uvicorn app.main:app --reload

# 后台任务：邮件、摘要、归档、写队列消费（终端 2）
python -m arq app.workers.settings.WorkerSettings
```

Swagger：<http://127.0.0.1:8000/docs> · ReDoc：<http://127.0.0.1:8000/redoc> · OpenAPI：`/openapi.json`

接口细节与错误码见 [docs/API_CONTRACT.md](docs/API_CONTRACT.md)。

### 5. 开发校验

安装开发依赖后运行：

```bash
pip install -r requirements-dev.txt
python -m pytest -q
ruff check .
mypy app
```

## 运维

- **生产启动**：`scripts/start.sh` 会先执行迁移，再启动 Uvicorn（POSIX shell）。
- **健康检查**：`GET /health`、`GET /health/live`、`GET /health/ready`。
- **数据库备份/恢复**（需要 `mysqldump`/`mysql` 客户端）：

```bash
scripts/backup_db.sh --keep-days 7
scripts/restore_db.sh <backup.sql.gz> <target_db> --yes
```

备份默认写入 `backups/`，保留期可通过 `--keep-days` 或 `BACKUP_KEEP_DAYS` 设置。

可选能力默认关闭：`DB_REPLICA_ENABLED`、`MESSAGE_ARCHIVE_ENABLED`、`WRITE_QUEUE_ENABLED`；启用后需确保 ARQ worker 正常运行。配额统计始终可用，`QUOTA_ENFORCE=true` 时才会拦截超额请求。

## 生产注意

- `APP_ENV=production` 会校验并拒绝默认 JWT 密钥、`DEBUG=true`、`CORS_ORIGINS=*`、`ALLOWED_HOSTS=*`、空 LLM Key、SQLite 数据库等不安全配置。
- 使用至少 32 位的随机 `JWT_SECRET_KEY`，并正确设置 `ALLOWED_HOSTS`、`CORS_ORIGINS`。
- 反向代理部署时配置 `TRUSTED_PROXIES` 与 `RATE_LIMIT_TRUST_FORWARDED_HEADERS`，并为 SSE 关闭代理缓冲。
- 建议配置 SMTP、定期备份与健康检查监控，并根据业务设置限流、日志与配额。
