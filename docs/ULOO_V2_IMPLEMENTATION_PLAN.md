# ULOO V2 源码开发实施方案

## 1. 最终产品定义

ULOO V2 不是“Dify Docker + 外挂 Demo”，而是以下统一产品：

```text
Dify 源码 Web（唯一 UI）
  ├─ Dify Studio：Start → ULOO / Agno Team → End
  └─ ULOO：Agents / Teams / Runs / Memory / Settings
                    │
Dify 源码 API（登录、workspace、BFF、安全代理）
                    │
ULOO Core API（Agent、Team、Run、Event、Memory）
                    │
Agno Runtime（真实模型、Agent、Team、Tool、流式事件）
                    │
PostgreSQL + pgvector
```

产品必须让用户仅通过 Dify 页面完成：新增 Agent、组成 Team、选择协同模式、拖入工作流、执行、观察成员状态、查看结果、复用跨 Agent 记忆。

## 2. 技术与部署边界

### 2.1 源码策略

- `vendor/dify/`：Dify 官方源码的受控 fork，固定稳定 tag/commit。
- Git remote 使用 `origin` 指向自己的 fork，`upstream` 指向官方仓库。
- 创建 `uloo/integration` 分支；所有 Dify patch 形成真实 commit。
- `vendor/dify/web` 和 `vendor/dify/api` 必须从当前源码启动或构建自有镜像。
- 禁止通过 `docker cp` 修改运行容器。

### 2.2 MVP 最小进程

开发阶段只保留：

1. Dify Web（源码开发进程）。
2. Dify API（源码开发进程）。
3. Dify Plugin Daemon（允许使用官方镜像）。
4. ULOO Core（Python 3.12 + FastAPI，内部直接调用 Agno）。
5. PostgreSQL/pgvector 与 Redis（Docker 基础依赖）。

Stage 0–6 不引入 NATS、MinIO、独立 ULOO Worker。同步执行配合 SSE 已足够验证产品价值。可靠队列在主链路稳定后作为 Stage 7 引入。

### 2.3 目录结构

```text
uloo/
├─ vendor/dify/                    # 固定版本 Dify 源码
├─ services/uloo-core/             # FastAPI + Agno + Memory
│  ├─ src/uloo/
│  ├─ migrations/
│  └─ tests/
├─ extensions/dify-agno-strategy/  # 合法 Dify Agent Strategy 插件
├─ contracts/openapi.yaml          # 唯一外部契约
├─ deploy/
│  ├─ compose.dev.yaml             # 仅基础依赖
│  └─ compose.release.yaml         # 自有源码构建镜像
├─ scripts/
└─ docs/
```

## 3. 核心数据对象

### AgentDefinition

```json
{
  "id": "uuid",
  "key": "researcher",
  "name": "研究员",
  "description": "检索并整理资料",
  "role": "Research specialist",
  "instructions": ["优先引用可靠来源"],
  "model_ref": "openai-compatible:gpt-5-mini",
  "tool_refs": ["web_search"],
  "knowledge_refs": [],
  "output_schema": null,
  "enabled": true,
  "version": 1
}
```

`model_ref` 只引用服务端模型配置，接口和浏览器永不返回 API Key。

### TeamDefinition

```json
{
  "id": "uuid",
  "key": "content-team",
  "name": "内容生产团队",
  "mode": "coordinate",
  "leader_agent_id": "uuid",
  "member_agent_ids": ["uuid", "uuid"],
  "instructions": ["先研究，再写作，最后审校"],
  "memory_policy": {
    "read_scopes": ["project", "team"],
    "write_scope": "team"
  },
  "limits": {
    "max_iterations": 8,
    "timeout_seconds": 120,
    "max_tokens": 20000
  },
  "enabled": true,
  "version": 1
}
```

### Run 与 Event

- Run 保存 `task_id/run_id/trace_id/team_version/status/input/output/usage/error`。
- Event 保存递增 `sequence`，类型至少包括：
  - `run.started`
  - `memory.retrieved`
  - `team.started`
  - `agent.started`
  - `agent.output`
  - `agent.completed`
  - `team.completed`
  - `memory.proposed`
  - `memory.committed`
  - `run.completed`
  - `run.failed`
- Event 只能记录安全摘要，不保存隐藏思维链。

## 4. ULOO Core API 契约

所有接口以 `/api/v1` 为前缀。第一版 OpenAPI 必须由服务端生成并锁定到 `contracts/openapi.yaml`，Dify BFF 和插件都以此为准。

### 4.1 健康与能力

| 方法与路径 | 输入 | 输出 | 达到的效果 |
|---|---|---|---|
| `GET /health/live` | 无 | `{"status":"ok"}` | 证明进程存活 |
| `GET /health/ready` | 无 | DB、Agno、模型配置状态 | 依赖不可用时返回 503，不能假 ready |
| `GET /capabilities` | 无 | 支持的 mode、model、tool、streaming | Dify UI 动态展示可用能力 |

### 4.2 Agent Registry

| 方法与路径 | 主要字段 | 达到的效果 |
|---|---|---|
| `POST /agents` | AgentDefinition 创建字段 | 创建一个可被 Team 引用的 Agent |
| `GET /agents` | `q/enabled/model_ref/offset/limit` | 搜索和分页展示 Agent |
| `GET /agents/{agent_id}` | UUID | 查看完整配置但不暴露密钥 |
| `PATCH /agents/{agent_id}` | 可更新字段 + `expected_version` | 乐观锁更新并产生新版本 |
| `DELETE /agents/{agent_id}` | UUID | 未被 Team 使用时软删除 |
| `POST /agents/{agent_id}/validate` | 可选测试输入 | 验证模型和工具配置，不创建正式 Run |
| `POST /agents/{agent_id}/test-runs` | `input` | 使用真实模型执行单 Agent 测试 |

Agent 测试响应：

```json
{
  "status": "succeeded",
  "runtime_type": "agno",
  "is_mock": false,
  "run_id": "uuid",
  "trace_id": "uuid",
  "output": {"content": "...", "structured_output": {}},
  "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
  "error": null
}
```

### 4.3 Team 管理

| 方法与路径 | 主要字段 | 达到的效果 |
|---|---|---|
| `POST /teams` | TeamDefinition 创建字段 | 创建 Agno Team 定义 |
| `GET /teams` | `q/mode/enabled/offset/limit` | Team 列表与过滤 |
| `GET /teams/{team_id}` | UUID | 查看 Team 和成员快照 |
| `GET /teams/by-key/{team_key}` | 稳定 key | 插件按稳定标识解析 Team |
| `PATCH /teams/{team_id}` | 配置 + `expected_version` | 更新 Team 并保留版本 |
| `DELETE /teams/{team_id}` | UUID | 软删除，不破坏历史 Run |
| `POST /teams/{team_id}/validate` | 无 | 验证 Leader、成员、模型、工具与 limits |

规则：

- `coordinate` 和 `tasks` 必须有 `leader_agent_id`。
- Leader 必须属于成员集合。
- 不允许 Team 没有成员。
- 不允许删除仍被启用 Team 引用的 Agent。
- mode 映射只调用 Agno 官方 Team mode，不实现自写调度循环。

### 4.4 Team 执行

规范入口：

```http
POST /api/v1/teams/by-key/{team_key}/runs
```

请求：

```json
{
  "input": {
    "query": "调查并形成报告",
    "variables": {"language": "zh-CN"}
  },
  "execution_mode": "streaming",
  "session_id": "optional",
  "task_id": "optional-uuid",
  "trace_id": "optional-uuid",
  "parent_run_id": null,
  "call_chain": ["dify:workflow:xxx", "dify:node:yyy"],
  "memory": {
    "project_id": "demo",
    "read_scopes": ["project", "team"],
    "write_scope": "team"
  },
  "limits": {
    "max_iterations": 8,
    "timeout_seconds": 120,
    "max_tokens": 20000
  },
  "metadata": {
    "source": "dify",
    "dify_workflow_id": "xxx",
    "dify_node_id": "yyy",
    "conversation_id": "zzz"
  }
}
```

blocking 响应：

```json
{
  "status": "succeeded",
  "runtime_type": "agno",
  "is_mock": false,
  "task_id": "uuid",
  "run_id": "uuid",
  "trace_id": "uuid",
  "team": {"key": "content-team", "version": 3},
  "output": {"content": "最终报告", "structured_output": {}},
  "member_results": [
    {"agent_key": "researcher", "status": "succeeded", "summary": "完成资料整理"},
    {"agent_key": "writer", "status": "succeeded", "summary": "完成报告撰写"}
  ],
  "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": 0},
  "error": null
}
```

流式方式：创建 Run 后返回 `202`：

```json
{
  "status": "running",
  "task_id": "uuid",
  "run_id": "uuid",
  "trace_id": "uuid",
  "stream_url": "/api/v1/runs/{run_id}/stream"
}
```

后续接口：

| 方法与路径 | 效果 |
|---|---|
| `GET /runs` | 按 Team、状态、trace、时间查询运行 |
| `GET /runs/{run_id}` | 获取运行快照、成员结果、usage 和错误 |
| `GET /runs/{run_id}/events?after_sequence=10` | 获取可恢复的历史事件 |
| `GET /runs/{run_id}/stream` | SSE 实时流，支持 `Last-Event-ID` |
| `POST /runs/{run_id}/cancel` | 请求取消并产生 `run.cancelled` |

错误码固定为：`TEAM_NOT_FOUND`、`AGENT_NOT_FOUND`、`MODEL_CONFIG_MISSING`、`MODEL_ERROR`、`TIMEOUT`、`CANCELLED`、`BUDGET_EXCEEDED`、`RECURSION_BLOCKED`、`RUNTIME_UNAVAILABLE`。

### 4.5 Memory

| 方法与路径 | 主要字段 | 达到的效果 |
|---|---|---|
| `POST /memories/search` | query、scopes、project/team/agent、limit | Run 开始前召回相关长期记忆 |
| `POST /memory-proposals` | content、scope、source run/agent、confidence | Agent 只能先提出写入建议 |
| `POST /memory-proposals/{id}/commit` | 审核策略结果 | 校验后正式写入 Memory |
| `GET /memories` | scope、source、版本、时间过滤 | Memory 页面查询和审计 |
| `GET /memories/{id}` | UUID | 查看来源、版本与引用关系 |
| `DELETE /memories/{id}` | UUID | 软删除或撤销错误记忆 |

每次正式 Team Run：先检索并产生 `memory.retrieved`，结束后处理 proposal，成功写入产生 `memory.committed`。第二次同 project/team Run 必须能读取第一次提交的内容。

## 5. Dify 集成接口

### 5.1 Dify Agent Strategy 插件

插件展示名称：`ULOO / Agno Team`。

参数：

- `query`：Dify 变量选择器。
- `team_key`：Team 稳定 key。
- `execution_mode`：blocking/streaming。
- `project_id`、`memory_read_scopes`、`memory_write_scope`。
- `max_iterations`、`timeout_seconds`、`max_tokens`。
- ULOO endpoint 与凭据属于 provider credential，不出现在普通节点参数中。

插件输出必须包含：`content`、`structured_output`、`task_id`、`run_id`、`trace_id`、`member_results`、`usage`、`status`。

### 5.2 Dify Console BFF

Dify Web 只能调用同源 BFF，不能直接访问 ULOO Core：

```text
/console/api/uloo/agents/**    → ULOO /api/v1/agents/**
/console/api/uloo/teams/**     → ULOO /api/v1/teams/**
/console/api/uloo/runs/**      → ULOO /api/v1/runs/**
/console/api/uloo/memories/**  → ULOO /api/v1/memories/**
```

BFF 必须：

- 从当前 Dify workspace 构造 ULOO workspace 映射。
- 注入服务间凭据，浏览器不可见。
- 转发请求 ID、trace ID 和错误码。
- 对 SSE 保持流式转发，不把完整响应缓存后再返回。
- 使用允许列表转发路径，不能实现任意 URL 代理。

## 6. Dify 页面达到的效果

### `/uloo`

Dashboard 显示 Core/Agno/模型 readiness、24 小时 Run 成功率、运行中任务、最近失败和用量。

### `/uloo/agents`

列表、新建、编辑、启停、模型与工具选择、真实测试运行。保存后能立即被 Team 选择。

### `/uloo/teams`

创建 Team、选择 Leader/成员/mode、配置 Memory 与 limits、执行验证。不得另建工作流画布。

### `/uloo/runs/{run_id}`

显示 Team 开始、成员委派、每个成员开始/完成/失败、安全摘要、最终输出、usage 和 trace；刷新页面后历史事件仍在。

### `/uloo/memory`

按 scope、Team、Agent、Run 查询，显示来源和版本链，允许撤销错误记忆。

### Dify Studio

原有 Agent Node 策略列表出现 `ULOO / Agno Team`。用户可以完成 Start → Team → End，支持单节点调试、整图调试、保存、发布、导出和重新导入。

## 7. 从零实施步骤与验收门

### Stage 0：干净源码基线

实施：初始化根 Git；将 Dify 稳定版本克隆到 `vendor/dify`；配置 fork/upstream；创建集成分支；只用源码方式启动未修改 Dify；记录基线测试。

接口：只验证 Dify 原生 `/health`、Console 和 Studio。

达到效果：看到的页面与本地源码完全一致；修改一段测试文案后热更新可见；删除容器重建后改动仍存在。

验收门：根仓库和 Dify 都有 commit；无 `docker cp`；Dify API/Web 不是官方成品镜像。

### Stage 1：ULOO Core 最小骨架

实施：FastAPI、配置、结构化日志、Alembic、PostgreSQL；完成 health/capabilities。

接口：`GET /health/live`、`GET /health/ready`、`GET /capabilities`。

达到效果：Dify 外能够明确判断 Core、数据库、Agno 和模型是否可用。

验收门：真实 PostgreSQL upgrade/downgrade/upgrade；依赖断开时 ready 返回 503。

### Stage 2：Agent 与 Team 配置闭环

实施：Agent/Team 表、版本、CRUD、校验；Dify BFF；Dify Agents/Teams 页面。

接口：第 4.2、4.3 节全部接口。

达到效果：用户只在 Dify 页面即可创建两个 Agent，并组成有真实 Leader 的 Team。

验收门：页面刷新不丢配置；无手写假数据；密钥不出现在浏览器网络响应。

### Stage 3：真实 Agno 单次执行

实施：真实模型 provider；Agent/Team factory；Agno Team `arun/stream`；事件映射；超时与取消。

接口：`POST /teams/by-key/{key}/runs`、Run 查询/事件/SSE/cancel。

达到效果：两个 Agent 使用真实模型完成一次可观察协同，返回不同成员结果。

验收门：手工验收中 `is_mock=false`；没有模型配置时明确失败；FakeModel 只能由 test fixture 注入。

### Stage 4：Dify Studio 插件闭环

实施：按固定 Dify/SDK 版本建立插件；Plugin Daemon 验证；参数和事件映射；示例 DSL。

接口：插件只调用规范 Team Run 和 SSE 接口。

达到效果：Studio 中拖入一个 Team 节点即可执行，不需要 curl 或手改数据库。

验收门：选择、保存、重开、调试、发布、导出、导入全部通过；普通 Dify Workflow 不受影响。

### Stage 5：Runs 可观察性

实施：持久 Run/Event；Dify Runs 列表和详情；SSE 断线恢复。

接口：`GET /runs`、`GET /runs/{id}`、events、stream。

达到效果：用户能看到哪个 Agent 做了什么、成功或失败、耗时和 token 使用量。

验收门：刷新和断线重连不丢事件；不展示思维链；trace ID 全链路一致。

### Stage 6：跨 Agent Memory

实施：检索、proposal、策略校验、commit；接入 Agno Tool/上下文；Memory 页面。

接口：第 4.5 节全部接口。

达到效果：Agent A 在 Run 1 产生记忆，Agent B 在 Run 2 实际读取并使用。

验收门：测试断言记忆内容进入模型上下文；事件完整；过期、版本和撤销生效。

### Stage 7：可靠异步执行（确认有需要后再做）

实施：在同步/SSE 链路稳定后引入 NATS JetStream、Outbox relay、durable Worker、幂等键和恢复。

接口保持不变，内部将 Run 从进程内执行切换到队列执行。

达到效果：API/Worker 重启后 Run 不丢失、不重复成功，两个 API 实例仍能流式观察。

验收门：故障注入和重启测试通过后，才允许默认启用 NATS。

### Stage 8：源码发布

实施：从固定源码构建 `uloo-dify-api`、`uloo-dify-web`、`uloo-core`；SBOM、升级说明、回归测试。

达到效果：一条 release compose 命令启动的就是修改后源码产品，而非运行时复制文件。

验收门：在全新机器从 Git commit 构建；不依赖本机残留容器、卷、插件或手工步骤。

## 8. 每阶段统一完成标准

每一阶段必须同时具备：

1. 有真实 Git commit。
2. 状态文档列出修改文件和验证命令。
3. 单元测试、契约测试和该阶段 E2E 通过。
4. 页面或接口具有可复现的验收证据。
5. 删除容器和缓存后可从源码重新构建。
6. 未完成项明确标为未完成，不得用“骨架”“对象实例化”冒充业务可用。

## 9. 明确禁止

- 官方 `langgenius/dify-api`、`langgenius/dify-web` 镜像不得作为 ULOO 源码成果运行。
- 禁止 `docker cp`、进入容器手工改源码或依赖本机残留状态。
- 禁止默认 FakeModel、静默 fallback、固定字符串冒充多 Agent 协同。
- 禁止创建独立 Workflow Canvas。
- 禁止先堆 NATS、MinIO、多个向量库和复杂微服务。
- 禁止在没有 E2E 证据时把阶段标为 Completed。
- 禁止修改 Dify 自动生成 compose 文件；应修改模板或维护独立 overlay。
- 禁止扩展自建用户系统；复用 Dify workspace，上线前再补授权映射。

