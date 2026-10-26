# AI ChatBot API 契约（v1）

> 应用版本：`0.3.0`（API 路径版本：`v1`）。最后更新：2026-10-26。
>
> 在线契约：`/openapi.json`、`/docs`（Swagger UI）、`/redoc`。本文档补充 OpenAPI 无法完整表达的 SSE 事件、错误码与版本策略。

## 1. 基础约定

### 1.1 地址与认证

- 业务接口统一前缀：`/api/v1`；健康检查位于 `/health`、`/health/live`、`/health/ready`。
- 除注册、登录、刷新、找回/重置密码和健康检查外，接口均需 `Authorization: Bearer <access_token>`。
- Access Token 默认 60 分钟，Refresh Token 默认 7 天；刷新采用轮换，旧 Refresh Token 立即失效。
- 响应默认 JSON（UTF-8）；导出接口可能返回 JSON 或 Markdown 附件，SSE 接口返回 `text/event-stream`。
- 时间字段使用 UTC，ISO 8601 格式；数据库时间不携带时区偏移。
- 响应头会回显 `X-Request-ID`（可由请求头传入），便于日志排查。

### 1.2 错误信封

所有非 SSE 的业务/校验错误统一为：

```json
{
  "detail": {
    "code": "INVALID_REQUEST",
    "message": "请求参数校验失败",
    "errors": []
  }
}
```

- `code` 是稳定标识，客户端应据此分支；`message` 仅供展示，可能调整。
- `errors` 仅参数校验错误可能出现。
- SSE 建立成功后的错误不使用该信封，而是 `event: error`（见 §3）。
- `429` 可能带 `Retry-After`；配额错误另带 `X-Quota-Limit`、`X-Quota-Used`、`X-Quota-Remaining`。

### 1.3 分页

- 旧式分页：`page`（从 1 开始）、`page_size`（1–100）。
- 会话列表 keyset：`cursor`（不透明字符串）、`limit`（1–100）；`cursor` 优先于 offset，响应 `next_cursor` 为字符串或 `null`。
- 消息历史 keyset：`cursor` 为整数消息 ID；响应 `next_cursor` 为整数或 `null`。
- 会话排序为 `is_pinned DESC, updated_at DESC, id DESC`；默认只返回未归档会话。
- `cursor` 不要解析或拼接；服务端可能变更编码。翻页时应保持 `q`、`archived` 等过滤条件一致。

### 1.4 版本与废弃策略

- `/api/v1` 内只做向后兼容变更：新增可选字段、可选参数、响应字段和端点；客户端应忽略未知字段。
- 破坏性变更（删除/重命名字段、改变语义或状态码）进入 `/api/v2`，v1 至少保留一个 minor 发布周期（建议不少于 90 天）。
- 废弃端点/字段先在 OpenAPI 标记 `deprecated: true` 并在本文档记录，保留期内继续工作；不静默删除。
- 错误码与 SSE 事件名视为稳定契约；新增事件字段属于兼容变更。
- 应用版本（`0.3.0`）与 URL 版本（`v1`）独立演进。

## 2. 端点清单

| 方法 | 路径 | 认证 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/register` | 否 | 注册；201 + `UserRead`；冲突 `USERNAME_TAKEN` / `EMAIL_TAKEN` |
| POST | `/api/v1/auth/login` | 否 | 用户名或邮箱登录；返回 `TokenPair` |
| POST | `/api/v1/auth/refresh` | 否 | 轮换 Refresh Token |
| POST | `/api/v1/auth/logout` | 是 | 撤销当前 Access Token，可同时撤销 Refresh Token；204 |
| POST | `/api/v1/auth/password/forgot` | 否 | 发送重置邮件；202，账号不存在也返回相同结果 |
| POST | `/api/v1/auth/password/reset` | 否 | 一次性重置密码；无效/过期返回 `PASSWORD_RESET_INVALID` |
| POST | `/api/v1/auth/verify-email` | 否 | 验证邮箱 token |
| GET | `/api/v1/auth/verify-email?token=...` | 否 | 邮件链接落地页，返回 HTML |
| POST | `/api/v1/auth/resend-verification` | 否 | 重发验证邮件；202，账号不存在/已验证也返回相同结果 |
| GET | `/api/v1/users/me` | 是 | 当前用户资料 |
| PATCH | `/api/v1/users/me` | 是 | 修改用户名/邮箱/密码 |
| DELETE | `/api/v1/users/me` | 是 | 删除账号及会话/消息；204 |
| GET | `/api/v1/users/me/usage` | 是 | 按日 token 用量（默认最近 7 天，范围 ≤366 天） |
| GET | `/api/v1/users/me/quota` | 是 | 当日配额、已用、剩余与是否强制 |
| POST | `/api/v1/conversations` | 是 | 新建会话；201 |
| GET | `/api/v1/conversations` | 是 | 会话列表；支持 `q/archived/cursor/limit/page/page_size` |
| GET | `/api/v1/conversations/{id}` | 是 | 会话详情 |
| PATCH | `/api/v1/conversations/{id}` | 是 | 修改标题、置顶、归档；至少一个字段 |
| DELETE | `/api/v1/conversations/{id}` | 是 | 删除会话及热/归档消息；204 |
| GET | `/api/v1/conversations/{id}/messages` | 是 | 消息历史；支持 `page/page_size/order/cursor` |
| GET | `/api/v1/conversations/{id}/export` | 是 | 导出整会话；`format=json\|markdown` |
| POST | `/api/v1/chat` | 是 | 发送消息并建立 SSE 流 |
| GET | `/api/v1/chat/stream/{request_id}` | 是 | 按 `Last-Event-ID` 续传 SSE |
| GET | `/health`、`/health/live`、`/health/ready` | 否 | 存活/就绪检查；就绪检查含 DB/Redis |

### 2.1 用量与配额

`GET /api/v1/users/me/usage?from=YYYY-MM-DD&to=YYYY-MM-DD`：

- `from` 默认 `to - 6 天`，`to` 默认今天（UTC）；`from > to` 或跨度超过 366 天返回 `INVALID_REQUEST`。
- 返回 `from`、`to`、`items[]`（`date`、`prompt_tokens`、`completion_tokens`、`request_count`）及三项合计。

`GET /api/v1/users/me/quota` 返回：

```json
{"date":"2026-10-26","limit":100000,"used":2345,"remaining":97655,"enforced":true}
```

`limit=0` 表示未配置配额；`enforced=false` 时只统计不拦截。

### 2.2 会话列表、置顶与归档

`GET /api/v1/conversations` 参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `q` | string | 无 | 标题子串搜索，1–255 字符，通配符按字面转义 |
| `archived` | bool | `false` | `false` 查活跃，`true` 查归档 |
| `cursor` | string | 无 | keyset 游标（≤512 字符），与 `limit` 配合 |
| `limit` | int | 无 | keyset 页大小 1–100；否则回退 `page_size` |
| `page` / `page_size` | int | `1` / `20` | 旧版 offset 分页，继续兼容 |

响应 `items`、`total`、`page`、`page_size`、`next_cursor`。使用游标时 `page` 固定回显 `1`。

`PATCH /api/v1/conversations/{id}` 请求字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | string | 1–255 字符；不能为 `null` |
| `is_pinned` | bool | 置顶；影响列表排序，不修改 `updated_at` |
| `is_archived` | bool | 归档；首次归档写 `archived_at`，取消归档清空；不改 `updated_at` |

空请求体或全空字段返回 `INVALID_REQUEST`。

### 2.3 会话导出

`GET /api/v1/conversations/{id}/export?format=json|markdown`：

- 校验会话归属；合并 `messages` 与 `messages_archive`，按消息 ID 升序返回全部消息。
- 响应头：
  - `Content-Disposition: attachment; filename="conversation-{id}.json"`（或 `.md`）
  - `Cache-Control: no-store`
- JSON 结构：

```json
{
  "conversation": {"id": 1, "title": "...", "is_pinned": false, "is_archived": false, "...": "..."},
  "messages": [{"id": 1, "role": "user", "content": "...", "metadata": null, "...": "..."}],
  "exported_at": "2026-10-26T12:00:00"
}
```

- Markdown 输出会话元信息与按角色分节的消息正文；不做 HTML 渲染。
- 注意：`conversation.is_archived` 是用户可见的会话归档；`messages_archive` 是冷热消息存储迁移，两者独立。

## 3. SSE 契约

### 3.1 建立与事件

`POST /api/v1/chat` 请求体：

```json
{"conversation_id": 1, "message": "你好"}
```

前置校验（认证、配额、限流、会话归属、停机）失败时返回普通 JSON 错误；校验通过后返回 `200 text/event-stream`。事件顺序：

| event | data | 终止 | 说明 |
| --- | --- | --- | --- |
| `start` | `request_id`, `user_message_id` | 否 | 用户消息已分配/落库；`request_id` 用于续传 |
| `delta` | `content` | 否 | 增量文本；服务端可能合并多个 token，不能假定逐 token |
| `done` | `assistant_message_id`, `token_count`, `finish_reason` | 是 | 正常完成，助手消息已落库 |
| `error` | `code`, `message` | 是 | 流内失败；见 §4 |

每个事件可带 `id`（Redis Stream ID）；合成错误事件可能没有 `id`。心跳为 SSE 注释行（约每 `CHAT_STREAM_HEARTBEAT` 秒），不是事件。

帧示例：

```text
id: 1710000000000-0
event: start
data: {"request_id":"req_0123456789ab","user_message_id":41}

id: 1710000000001-0
event: delta
data: {"content":"你好"}

id: 1710000000002-0
event: done
data: {"assistant_message_id":42,"token_count":128,"finish_reason":"stop"}

```

### 3.2 断点续传

`GET /api/v1/chat/stream/{request_id}`：

- 从 `start` 事件中的 `request_id` 续传；仅流的所有者可访问。
- 游标优先级：`Last-Event-ID` 请求头 > `last_event_id` 查询参数。
- 返回从游标之后的事件；已完成/失败的流会重放剩余事件后结束。
- 流不存在、过期或不属于当前用户：HTTP `404 STREAM_NOT_FOUND`。
- 客户端应对 `error` 事件停止重连；对网络中断使用最后收到的 `id` 重连。

## 4. 错误码

| HTTP/通道 | code | 场景 |
| --- | --- | --- |
| 400 | `INVALID_REQUEST` | 参数/请求体校验失败、非法分页游标、日期范围错误、邮箱验证链接无效 |
| 400 | `PASSWORD_RESET_INVALID` | 密码重置 token 无效、过期或已使用 |
| 401 | `UNAUTHORIZED` | 缺少/无效 Access Token，或刷新令牌不可用 |
| 401 | `TOKEN_EXPIRED` | Access Token 过期 |
| 401 | `INVALID_CREDENTIALS` | 用户名/邮箱或密码错误 |
| 403 | `EMAIL_NOT_VERIFIED` | 登录时邮箱未验证 |
| 404 | `CONVERSATION_NOT_FOUND` | 会话不存在或不属于当前用户 |
| 404 | `USER_NOT_FOUND` | 目标用户不存在 |
| 404 | `STREAM_NOT_FOUND` | SSE 流不存在/已过期；也可能是流内 error 事件 |
| 409 | `CONFLICT` | 资源冲突 |
| 409 | `USERNAME_TAKEN` | 用户名已占用 |
| 409 | `EMAIL_TAKEN` | 邮箱已占用 |
| 429 | `RATE_LIMITED` | 请求频率超限，带 `Retry-After` |
| 429 | `ACCOUNT_LOCKED` | 登录失败次数过多，带 `Retry-After` |
| 429 | `QUOTA_EXCEEDED` | 每日 token 配额用尽，带 `Retry-After` 与 `X-Quota-*` |
| 500 | `INTERNAL_ERROR` | 未处理异常；SSE 流内也可能出现 |
| 502 | `LLM_ERROR` | 模型服务失败；通常以 SSE `error` 事件返回 |
| 503 | `SERVER_SHUTTING_DOWN` | 服务停机排空期间拒绝新聊天 |
| SSE | `STREAM_STALE` | 流元数据超过 `CHAT_STREAM_STALE_SECONDS` 未更新 |

## 5. 配置契约

以下为 v3 新增或与契约直接相关的配置；完整默认值见 `.env.example`。

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `ALLOWED_HOSTS` | `*` | TrustedHost 允许列表；生产禁止 `*` |
| `SHUTDOWN_DRAIN_SECONDS` | `30` | 停机等待在途生成完成的上限；期间新聊天返回 503 |
| `CHAT_MAX_MESSAGE_CHARS` | `8000` | 单条用户消息最大字符数 |
| `DB_READ_URL` | `""` | 只读从库连接串 |
| `DB_REPLICA_ENABLED` | `false` | 启用读写分离；GET 列表/消息/导出可走从库 |
| `DB_REPLICA_STICKY_SECONDS` | `5` | 写后读一致性窗口；也可用 `consistent=1` / `X-Consistent-Read: 1` |
| `MESSAGE_ARCHIVE_ENABLED` | `false` | 启用冷消息迁移到 `messages_archive` |
| `MESSAGE_ARCHIVE_AFTER_DAYS` | `180` | 热表消息归档阈值 |
| `MESSAGE_ARCHIVE_BATCH_SIZE` | `1000` | 归档批大小 |
| `WRITE_QUEUE_ENABLED` | `false` | 启用 Redis 写队列 |
| `WRITE_QUEUE_BATCH_SIZE` | `64` | 写队列批量大小 |
| `WRITE_QUEUE_BATCH_MAX_WAIT_MS` | `200` | 批量最大等待 |
| `WRITE_QUEUE_MAX_ATTEMPTS` | `5` | 写队列最大尝试次数 |
| `AUTH_LOCKOUT_THRESHOLD` | `10` | 登录失败锁定阈值；`0` 关闭 |
| `AUTH_LOCKOUT_MINUTES` | `15` | 锁定时长（分钟） |
| `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` | `30` | 密码重置 token 有效期 |
| `RATE_LIMIT_FAIL_MODE` | `open` | Redis 故障时限流放行/拒绝 |
| `RATE_LIMIT_TRUST_FORWARDED_HEADERS` | `false` | 是否信任代理转发 IP |
| `TRUSTED_PROXIES` | `""` | 可信代理 CIDR 列表 |
| `USER_DAILY_TOKEN_QUOTA` | `0` | 每用户每日 token 配额；`0` 关闭 |
| `QUOTA_ENFORCE` | `false` | `true` 时超配额返回 429；`false` 只统计 |
| `USAGE_RETENTION_DAYS` | `365` | 用量明细保留天数，worker 每日清理 |

- `APP_ENV=production` 时启动校验默认密钥、`DEBUG`、`CORS_ORIGINS`、`ALLOWED_HOSTS`、LLM Key、SQLite 与从库配置。
- WP8 的写队列/归档/读写分离默认关闭；写队列需要单独运行 ARQ worker。
- `MESSAGE_ARCHIVE_*` 只影响消息存储位置，不改变会话归档状态；`conversation.is_archived` 由 PATCH 控制。

