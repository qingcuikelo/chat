# AI ChatBot

一个基于 **FastAPI + MySQL + Redis** 的 AI 聊天后端服务，支持多用户、多会话、SSE 流式对话，并内置上下文滚动摘要、Redis 限流、请求追踪与健康检查。

## 主要能力

- 用户注册/登录、JWT 刷新与撤销、邮箱验证、密码找回
- 多会话管理与消息历史（keyset 分页）
- SSE 流式聊天与 `Last-Event-ID` 断点续传
- 上下文滚动摘要、token 统计与可配置模型
- Redis 原子限流、结构化日志、安全响应头
- 可选：读写分离、消息归档、写队列（默认关闭）

## 快速开始

### 1. 准备环境

需要 Python 3.12+、MySQL 8+（`utf8mb4`）和 Redis 6.2+。

```bash
conda create -n ai_chatbot python=3.12 -y
conda activate ai_chatbot
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
```

至少填写：

- `DATABASE_URL`（MySQL 需带 `charset=utf8mb4`）
- `REDIS_URL`
- `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`

### 3. 初始化数据库

```bash
alembic upgrade head
```

### 4. 启动

```bash
# API（终端 1）
python -m uvicorn app.main:app --reload

# 后台任务：摘要、邮件、归档、写队列消费（终端 2）
python -m arq app.workers.settings.WorkerSettings
```

Swagger 文档：<http://127.0.0.1:8000/docs>

### 5. 运行测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## 说明

- 业务接口统一使用 `/api/v1` 前缀。
- 生产环境请设置 `APP_ENV=production`，并配置 `ALLOWED_HOSTS`、`CORS_ORIGINS`、强 JWT 密钥与 LLM Key。
- 生产启动可使用 `scripts/start.sh`（自动执行迁移后启动）。
- 数据库备份/恢复：`scripts/backup_db.sh`、`scripts/restore_db.sh`。
