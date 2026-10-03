# AI ChatBot

一个以 AI 聊天机器人为核心的后端服务：基于 **FastAPI + MySQL + Redis**，支持多用户、多会话、SSE 流式对话，并内置上下文滚动摘要、原子限流与请求追踪。


## 快速开始

### 1. 准备环境

```bash
conda create -n ai_chatbot python=3.12 -y
conda activate ai_chatbot
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt   # 测试依赖（可选）
```

### 3. 配置环境变量

复制模板并按需修改（数据库、Redis、LLM Key 等）：

```bash
cp .env.example .env
```

关键项：`DATABASE_URL`（MySQL 需 `charset=utf8mb4`）、`REDIS_URL`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。

### 4. 初始化数据库

```bash
alembic upgrade head
```

### 5. 启动服务

```bash
# API 服务
python -m uvicorn app.main:app --reload

# 后台 worker（上下文摘要任务，另开一个终端）
python -m arq app.workers.settings.WorkerSettings
```

打开 <http://127.0.0.1:8000/docs> 查看 Swagger 文档。

### 6. 运行测试

```bash
python -m pytest -q
python -m pytest -q --cov=app --cov-report=term-missing
```

## 项目结构

```
app/
├── main.py            # 应用入口（CORS、异常、中间件、健康探活）
├── api/v1/            # 路由：auth / users / conversations / chat
├── services/          # 业务逻辑：auth / user / conversation / chat / llm / summary / cache / token
├── repositories/      # 数据访问
├── models/            # SQLAlchemy 模型
├── schemas/           # Pydantic 模型
├── core/              # 配置、数据库、Redis、安全、限流、日志、中间件
├── dependencies/      # 认证依赖
└── workers/           # ARQ 后台任务（摘要）
alembic/               # 数据库迁移
tests/                 # Pytest 测试
```

架构分层：`Router → Service → Repository → Database`；配置集中在 `app/core/config.py`，敏感信息一律走环境变量。

## 接口前缀

所有业务接口位于 `/api/v1`，例如：

- `POST /api/v1/auth/register`、`POST /api/v1/auth/login`
- `GET /api/v1/users/me`
- `GET/POST /api/v1/conversations`
- `GET /api/v1/conversations/{id}/messages`
- `POST /api/v1/chat`（SSE 流式）
- `GET /api/v1/chat/stream/{request_id}`（SSE 断点续传，重连时带 `Last-Event-ID`）

> 公开接口：注册、登录、刷新令牌、邮箱验证与重发验证；其余需请求头 `Authorization: Bearer <access_token>`。
