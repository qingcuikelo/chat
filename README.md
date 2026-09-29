# AI ChatBot

以 AI 聊天机器人为核心的 Web 项目（v1）。

## 技术栈

- **语言 / 环境**：Python 3.12.4、Conda（环境名 `ai_chatbot`）
- **Web 框架**：FastAPI（异步）
- **ORM / 校验**：SQLAlchemy 2.0、Pydantic v2
- **数据库**：MySQL 8.0（生产）/ SQLite（开发）
- **缓存**：Redis
- **认证**：JWT（PyJWT）
- **AI**：OpenAI API 或兼容 OpenAI SDK 的 LLM API
- **流式传输**：SSE（sse-starlette）
- **迁移 / 测试**：Alembic、Pytest

## 文档

- 设计文档：[version_design/AI ChatBot (v1_version).md](version_design/AI%20ChatBot%20(v1_version).md)

## 本地环境

```bash
conda activate ai_chatbot
```

## 开发状态

项目脚手架与业务代码尚未开始，当前版本仅包含仓库初始化与设计文档。
