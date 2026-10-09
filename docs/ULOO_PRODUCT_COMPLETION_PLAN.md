# ULOO 产品闭环实施计划

> 制定日期：2026-09-21  
> 目标：以 Dify 源码 Web 为唯一操作界面，以 ULOO Core 为多智能体运行时，使用真实 Agno Team，完成可恢复 Run/Event/SSE、跨 Agent Memory，以及 Dify Studio 调用闭环。

## 1. 当前事实与实施边界

当前已经具备：

- ULOO Core 的 health、capabilities、Agent/Team CRUD、workspace 隔离、版本冲突处理与 PostgreSQL migration。
- Dify Console BFF：浏览器访问 `/console/api/uloo/**`，BFF 注入 workspace 和服务令牌后转发到 ULOO Core。
- Core 与 BFF 自动测试和统一验证脚本。

当前尚不具备：

- Dify Web 中没有 `/uloo` 路由、导航、Dashboard、Agents、Teams、Runs、Memory 页面。
- 没有真实模型 provider registry、Agno Agent/Team factory 或正式 Run。
- 没有 Run/Event 数据表、SSE、取消、超时与恢复。
- 没有 Memory 数据表、检索、proposal/commit 和执行链注入。
- 没有 Dify Agent Strategy 插件，因此 Studio 不能调用 ULOO Team。
- （已修正，2026-09-21）原判断为“Windows 应用控制阻止加载 OpenDAL 原生 DLL”，实测不成立：本机 `import opendal` 正常，Dify API 的真实失败原因是**从 Git-Bash/MSYS 启动原生 Python 时 Cygwin 运行时在导入 Flask 之前就中止**（`fatal error - Internal error: TP_NUM_C_BUFS too small`）。改用 PowerShell 启动后 `5001` 正常服务。

不可改变的边界：

1. 不创建第二套用户系统；身份与 workspace 继续使用 Dify。
2. 浏览器只访问 Dify 同源 `/console/api/uloo/**`，不直连 `8200`。
3. 不创建第二套工作流画布；Team 配置页面只管理 Agent/Team。
4. 生产路径禁止 FakeModel、假事件和静态演示数据。
5. Event 只保存安全摘要、工具结果和状态，不保存隐藏思维链。
6. Dify API/Web 必须从固定源码运行或构建，不能用官方成品镜像冒充源码成果。

## 2. 正确的交付顺序

```text
可运行的 Dify 源码环境
        ↓
ULOO Dashboard + Agent/Team 可视化配置
        ↓
真实模型注册 + Agno Agent/Team Factory
        ↓
Run/Event 持久化 + SSE + 取消/超时
        ↓
跨 Agent Memory 检索与提交
        ↓
Dify Studio 的 ULOO / Agno Team 策略
        ↓
端到端验收与源码发布
```

每一阶段必须形成可操作的纵向闭环；不得先创建大量空页面或返回假数据。

## 3. Phase 0：恢复可用的 Dify 源码开发环境

### 3.1 要解决的问题

当前 Dify Web 已在 `3000` 运行，但 Dify API 无法在 `5001` 启动，Web 因此永久停留在“加载中”。这不是 ULOO 页面问题，必须先处理。

**结论（2026-09-21 实测）：** 这不是 OpenDAL 问题，也不是 Windows 应用控制问题。
`vendor/dify/api/.venv` 里 `import opendal` 正常返回；真正的失败是从 Git-Bash/MSYS
派生的 shell 启动原生 Python 时，Cygwin 运行时在 Flask 被导入之前就中止：

```text
fatal error - Internal error: TP_NUM_C_BUFS too small: 50
```

用 PowerShell 或 cmd 启动同一条命令即可正常运行。因此**不需要**为了这个故障引入
WSL2 或容器，宿主机的 `scripts/dev-up.ps1` 已经足够。

### 3.2 实施方式（已落地）

宿主原生运行，不引入 WSL2：本机安全策略禁止执行 `wsl.exe`，而该故障并不需要 WSL
才能解决。PostgreSQL、Redis、plugin daemon 继续作为基础设施运行；Dify Web、Dify API
与 ULOO Core 均由 `scripts/dev-up.ps1` 从源码启动。

固定地址：

| 服务 | 地址 | 要求 |
|---|---|---|
| Dify Web | `http://localhost:3000` | 源码热更新 |
| Dify API | `http://localhost:5001` | 源码启动，Console API 可用 |
| ULOO Core | `http://localhost:8200` | 源码启动 |
| PostgreSQL | `localhost:5432` | Dify 与 ULOO 使用不同 database 或 schema |
| Redis | `localhost:6379` | Dify 与 ULOO 使用不同 DB index/prefix |

### 3.3 验收门

- `GET /console/api/setup` 返回正常响应。— **已验证**：`HTTP 200 {"step":"finished","setup_at":"2026-09-14T07:28:46"}`
- 浏览器打开 `3000` 能进入 Dify 登录/Console，而不是无限加载。— **已验证**：`GET /` 返回 `307` 且不再停留在加载页。
- 登录后调用 `/console/api/uloo/capabilities` 能取得 Core 的真实能力。— **路由已验证**：未登录返回 `401 {"code":"unauthorized"}`，对照不存在路径返回 `404`，说明 BFF 已注册且鉴权生效；登录态下的完整调用见 Phase 1 验收。
- 修改 Dify Web 源码文案可热更新；修改 Dify API BFF 后测试与请求均命中新代码。— **部分成立**：Web 走 `next dev`，热更新是内建行为；但 `vendor/dify/api/app.py` **没有启用 reloader**（无 `use_reloader`/debug 自动重载），因此 BFF 改动后必须用 `scripts/dev-down.ps1` + `dev-up.ps1` 重启 API 才会命中新代码。这一点必须写进开发习惯，不能假设改完即生效。
- 保存完整启动命令到 `README.md` 和 `scripts/dev-up.*`，不得依赖人工复制文件。— **已完成**：`scripts/dev-up.ps1` + `scripts/dev-down.ps1`，README 已记录启动方式与 MSYS 陷阱。

## 4. Phase 1：先完成 ULOO 可视化配置闭环

### 4.1 Web 目录与路由

在 `vendor/dify/web` 中新增：

```text
app/(commonLayout)/uloo/layout.tsx
app/(commonLayout)/uloo/page.tsx
app/(commonLayout)/uloo/agents/page.tsx
app/(commonLayout)/uloo/agents/[agentId]/page.tsx
app/(commonLayout)/uloo/teams/page.tsx
app/(commonLayout)/uloo/teams/[teamId]/page.tsx
app/(commonLayout)/uloo/runs/page.tsx
app/(commonLayout)/uloo/runs/[runId]/page.tsx
app/(commonLayout)/uloo/memory/page.tsx
app/components/uloo/**
service/uloo.ts
types/uloo.ts
```

同时修改 `app/components/main-nav/routes.ts`，增加一级导航“ULOO”，路径 `/uloo`。沿用 Dify 的 Common Layout、workspace、主题、权限、Toast、Modal、Table、Form 与 i18n，不创建独立壳应用。

### 4.2 TypeScript Client

`service/uloo.ts` 只能调用以下同源路径：

```text
GET    /uloo/capabilities
GET    /uloo/health/ready
GET    /uloo/agents
POST   /uloo/agents
GET    /uloo/agents/{id}
PATCH  /uloo/agents/{id}
DELETE /uloo/agents/{id}
POST   /uloo/agents/{id}/validate
GET    /uloo/teams
POST   /uloo/teams
GET    /uloo/teams/{id}
PATCH  /uloo/teams/{id}
DELETE /uloo/teams/{id}
POST   /uloo/teams/{id}/validate
```

Dify 请求层会自动加 `/console/api` 前缀，最终浏览器请求为 `/console/api/uloo/**`。响应错误统一解析 `code/message/request_id/trace_id/details`。

### 4.3 页面效果

#### `/uloo`

- 展示 Core、数据库、Agno、模型配置四项真实 readiness。
- 根据 `/capabilities` 显示功能状态；未实现的 Run/Memory 显示“尚未启用”，不能显示虚构统计。
- 提供 Agents、Teams、Runs、Memory 入口和最近错误的 request/trace ID。

#### `/uloo/agents`

- 搜索、分页、新建、编辑、启停和软删除。
- 字段：`key/name/description/role/instructions/model_ref/tool_refs/knowledge_refs/output_schema/enabled`。
- 保存时携带 `expected_version`；409 时显示服务端版本并要求重新加载。
- `/validate` 失败按字段展示；真实模型测试在 Phase 2 完成前禁用并明确说明原因。

#### `/uloo/teams`

- 搜索、分页、新建、编辑、启停和软删除。
- 从已启用 Agent 中选择成员与 Leader。
- mode 只能来自 `/capabilities.modes`，不能在前端另写一套常量。
- 编辑 `instructions/memory_policy/limits`；前端预校验与服务端 `/validate` 同时保留。

### 4.4 Phase 1 验收门

- 仅使用页面创建两个 Agent，再创建一个 Team，刷新后数据仍存在。
- 切换 Dify workspace 后，不能看到前一个 workspace 的对象。
- 浏览器网络请求中不存在 Core token、模型 API Key 或 `8200` 地址。
- 409、422、503 均有可理解的 UI，且展示 request/trace ID。
- Vitest 覆盖 Client、表单规则和错误态；Playwright 覆盖创建、编辑、刷新和 workspace 隔离。

## 5. Phase 2：真实模型与 Agno 多智能体执行

### 5.1 新增运行时模块

在 `services/uloo-core/src/uloo` 中新增：

```text
runtime/model_registry.py
runtime/tool_registry.py
runtime/agent_factory.py
runtime/team_factory.py
runtime/executor.py
runtime/event_mapper.py
schemas/runtime.py
api/runtime.py
```

### 5.2 模型引用协议与 Dify Model Gateway

正式产品不在 ULOO 中再配置一套模型密钥。模型凭据继续由当前 Dify workspace 管理，ULOO 通过受服务间认证保护的 Dify Model Gateway 调用 `ModelManager.for_tenant(...)`，Agno 使用自定义 Model adapter 连接该网关。这样用户在 Dify 配置一次模型即可同时供 Dify 和 ULOO 使用。

`model_ref` 使用 Dify 已有 provider/model 标识：

```text
dify/{provider}/{model-name}
```

例如 `dify/langgenius/openai/openai/gpt-4.1-mini`。数据库只保存引用，不保存凭据。

Dify API 新增仅供 ULOO Core 调用的内部接口：

| 方法与路径 | 效果 |
|---|---|
| `GET /inner/api/uloo/models` | 按服务头中的 workspace 返回可用 LLM/embedding 模型，不返回凭据 |
| `POST /inner/api/uloo/models/probe` | 使用 Dify 当前凭据执行最小连通性检查 |
| `POST /inner/api/uloo/models/invoke` | 使用 Dify ModelManager 执行 blocking/streaming LLM 调用 |
| `POST /inner/api/uloo/embeddings` | 为 Memory 写入与查询生成 embedding |

内部接口必须校验独立的 ULOO→Dify 服务令牌、workspace、请求大小和允许的模型类型；不能复用浏览器 cookie，也不能接受浏览器直接访问。ULOO Core 中实现 `DifyGatewayModel`，负责 Agno message/tool/event 与 Dify model runtime 结构的双向转换。

独立 `openai-compatible/{alias}/{model}` registry 只作为开发和自动化测试回退，默认关闭，不能成为生产必配项。

新增接口：

| 方法与路径 | 效果 |
|---|---|
| `GET /api/v1/runtime/models` | Core 从 Dify Gateway 取得当前 workspace 可用模型，供页面选择 |
| `POST /api/v1/runtime/models/{model_ref}/probe` | Core 通过 Dify Gateway 执行最小连通性测试 |
| `GET /api/v1/runtime/tools` | 返回工具 allow-list 与参数摘要 |
| `GET /api/v1/runtime/knowledge-sources` | 返回可引用知识源；未实现时返回空列表而非假数据 |

### 5.3 Factory 映射

`AgentFactory` 必须将数据库快照映射为真实 `agno.agent.Agent`：

- `agent_id/key/name/role/instructions`
- `DifyGatewayModel` 解析后的真实 Agno Model adapter
- allow-list 解析后的 Tool 实例
- 可选 response model
- `telemetry=False`，生产日志不记录完整 prompt

`TeamFactory` 必须将 TeamDefinition 映射为真实 `agno.team.Team`：

- `members` 为 AgentFactory 创建的成员
- `mode` 直接使用 Agno 1.8.4 的 `route/coordinate/collaborate`
- `team_id/name/instructions`
- `stream_member_events=True`
- `store_events=True`
- limits 由 ULOO Executor 强制执行，不依赖 prompt 自觉遵守

`leader_agent_id` 是 ULOO 协调语义：构建 Team 时把 Leader 的角色和责任加入 Team instructions，并保存映射快照；不能假设 Agno 1.8.4 存在 `leader` 构造参数。

### 5.4 Phase 2 验收门

- `POST /agents/{id}/test-runs` 调用真实 Agent，返回 `runtime_type=agno`、`is_mock=false`。
- 一个真实 Team 至少包含两个角色不同的 Agent，并产生可区分的成员结果。
- 缺失模型配置返回 `MODEL_CONFIG_MISSING`；模型调用失败返回 `MODEL_ERROR`，不得自动退回 FakeModel。
- FakeModel 只能由单元测试 fixture 显式注入。

## 6. Phase 3：Run、Event 与 SSE

### 6.1 数据表

新增 Alembic migration：

#### `runs`

`id/workspace_id/task_id/trace_id/team_id/team_key/team_version/status/execution_mode/input/output/usage/error/metadata/parent_run_id/call_chain/cancel_requested_at/started_at/finished_at/created_at/updated_at`

约束：

- `(workspace_id, task_id)` 唯一，提供幂等。
- status 枚举：`queued/running/succeeded/failed/cancelled/timed_out`。
- 保存 Team 和 Agent 配置快照，后续编辑不能改变历史 Run。

#### `run_members`

`id/workspace_id/run_id/agent_id/agent_key/agent_version/status/input_summary/output_summary/usage/error/started_at/finished_at`

#### `run_events`

`id/workspace_id/run_id/sequence/event_type/agent_key/safe_summary/payload/created_at`

约束 `(run_id, sequence)` 唯一；payload 必须经过字段 allow-list 和大小限制。

### 6.2 Run API

| 方法与路径 | 行为 |
|---|---|
| `POST /api/v1/teams/by-key/{team_key}/runs` | 创建 blocking 或 streaming Run |
| `GET /api/v1/runs` | 按 Team、status、trace、时间分页 |
| `GET /api/v1/runs/{run_id}` | Run、成员、usage、错误快照 |
| `GET /api/v1/runs/{run_id}/events?after_sequence=N` | 返回持久化历史事件 |
| `GET /api/v1/runs/{run_id}/stream` | SSE 历史补发后继续实时推送 |
| `POST /api/v1/runs/{run_id}/cancel` | 标记取消并通知执行器 |

streaming 创建成功返回 `202` 和 `stream_url`。SSE 格式固定为：

```text
id: 17
event: agent.completed
data: {"run_id":"...","sequence":17,"agent_key":"writer","summary":"完成草稿"}
```

支持 `Last-Event-ID` 与 `after_sequence`；每 15 秒发送 comment heartbeat。断开连接不取消 Run。

### 6.3 Event 映射

至少实现：

```text
run.started
team.started
agent.started
agent.output
agent.completed
agent.failed
tool.started
tool.completed
team.completed
run.completed
run.failed
run.cancelled
```

Agno 原始事件不得直接原样入库；`event_mapper.py` 只提取允许字段并截断内容。reasoning step 不进入 UI 和持久 Event。

### 6.4 执行与可靠性

第一版允许单进程后台执行，但必须在启动时把遗留 `queued/running` Run 标成 `failed`，错误码为 `RUNTIME_RESTARTED`，不能永久卡住。待同步/SSE 语义稳定后再切换 durable worker，外部 API 保持不变。

强制实现：

- `asyncio.timeout` 或等价机制控制总超时。
- cancellation token 在成员/工具事件之间检查。
- max tokens、max iterations、递归 call chain 限制。
- task_id 幂等；相同 payload 返回已有 Run，不同 payload 返回 409。

### 6.5 Web 页面

`/uloo/runs` 展示筛选、状态、Team、开始时间、耗时、usage、trace ID。`/uloo/runs/{id}` 使用 SSE 构建时间线，刷新后先加载 events 再续流。

### 6.6 Phase 3 验收门

- 两个 Agent 的真实 Team Run 完成，数据库中 Run、成员和 Event 一致。
- 浏览器能实时看到成员开始、完成、最终输出和 usage。
- 刷新、断网再连接后不丢事件、不重复显示。
- 超时、取消、模型失败和服务重启都有终态与固定错误码。

## 7. Phase 4：跨 Agent Memory

### 7.1 数据模型

新增：

- `memories`：当前有效版本、workspace、scope、project/team/agent 归属、文本、embedding、状态、置信度、来源。
- `memory_versions`：不可变版本链。
- `memory_proposals`：Run/Agent 提出的候选记忆、审核状态与理由。
- `memory_citations`：某次 Run 使用了哪些 Memory 版本。

scope 仅允许 `project/team/agent`。所有表必须包含 workspace_id。向量列使用 pgvector；同时保留 PostgreSQL 全文索引用于 hybrid search。

### 7.2 Memory API

| 方法与路径 | 行为 |
|---|---|
| `POST /api/v1/memories/search` | hybrid search，并执行 workspace/scope 过滤 |
| `POST /api/v1/memory-proposals` | 创建候选记忆，不直接污染长期记忆 |
| `GET /api/v1/memory-proposals` | 查询待审核/已处理 proposal |
| `POST /api/v1/memory-proposals/{id}/commit` | 校验、去重、建立新版本并提交 |
| `POST /api/v1/memory-proposals/{id}/reject` | 拒绝候选并记录理由 |
| `GET /api/v1/memories` | scope、来源、Team、Agent、Run、时间分页 |
| `GET /api/v1/memories/{id}` | 当前内容、版本链、引用链 |
| `DELETE /api/v1/memories/{id}` | 软删除 |
| `POST /api/v1/memories/{id}/restore` | 恢复指定历史版本 |

### 7.3 运行链集成

1. Run 开始前根据 project/team/agent scopes 检索。
2. 对结果去重、裁剪并记录 citation。
3. 以明确的“已验证记忆”上下文注入 Team/Agent，而不是拼入系统密钥或隐藏 prompt。
4. 产生 `memory.retrieved` Event。
5. Run 完成后生成 proposal；按 memory policy 自动提交或进入人工审核。
6. 提交后产生 `memory.proposed`、`memory.committed` Event。

### 7.4 Web 页面

`/uloo/memory` 提供 scope、Team、Agent、Run、状态、时间过滤；详情展示来源 Run、创建 Agent、版本链、引用次数、撤销与恢复。人工审核 proposal 也在此页面完成。

### 7.5 Phase 4 验收门

- Run 1 中 Agent A 产生一条已提交记忆。
- Run 2 中 Agent B 实际检索该版本，citation 与 `memory.retrieved` 可查。
- 使用可判定的问题证明 Agent B 的输出使用了该记忆，而不只是数据库里存在记录。
- 撤销后后续 Run 不再召回；恢复版本后能够再次召回。

## 8. Phase 5：Dify Studio 节点闭环

### 8.1 技术选择

第一版不要侵入式新增 Dify 原生 node type。使用 Dify 官方 Agent Strategy 插件机制，在现有 Agent 节点的策略选择器中提供 `ULOO / Agno Team`。这样可复用 Dify 的节点保存、调试、发布、DSL 导入导出和插件生命周期，升级成本最低。

如果产品验收明确要求画布上显示独立“ULOO Team”节点，再以第二阶段对 Dify Web、workflow schema、node factory、runner 和 DSL 做原生扩展；该工作不应阻塞第一版闭环。

### 8.2 插件目录

新增 `extensions/dify-agno-strategy/`，至少包含：

```text
manifest.yaml
provider/uloo.yaml
provider/uloo.py
strategies/agno_team.yaml
strategies/agno_team.py
requirements.txt
README.md
examples/start-uloo-team-end.yml
tests/**
```

Provider credential 保存 Core endpoint 与 service token；普通节点参数不能出现凭据。

### 8.3 节点输入输出

输入：

- `query`
- `team_key`
- `execution_mode`
- `project_id`
- `memory_read_scopes`
- `memory_write_scope`
- `max_iterations`
- `timeout_seconds`
- `max_tokens`

输出：

- `content`
- `structured_output`
- `task_id`
- `run_id`
- `trace_id`
- `member_results`
- `usage`
- `status`

插件只调用规范 Run/SSE API，不能直接 import ULOO Core 代码或直接访问数据库。`task_id` 由 Dify workflow run ID + node ID 派生，保证节点重试幂等。

### 8.4 Phase 5 验收门

- Studio 策略列表能选择 `ULOO / Agno Team`。
- Start → Agent（ULOO 策略）→ End 的单节点调试和整图调试通过。
- 保存、关闭重开、发布、运行、导出 DSL、重新导入全部保留参数。
- blocking 与 streaming 均返回规范输出。
- ULOO 失败能映射为 Dify 节点失败，保留 run_id/trace_id，普通 Dify Agent Node 不受影响。

## 9. Phase 6：全链路质量门与发布

### 9.1 自动验证

根验证脚本必须依次执行：

1. Core Ruff、Mypy、pytest、coverage。
2. migration `upgrade → downgrade → upgrade`，使用临时 schema。
3. OpenAPI drift。
4. Dify BFF 单测与 lock check。
5. Dify Web lint、type-check、ULOO 组件测试。
6. Plugin package 校验与插件单测。
7. Playwright 真实浏览器 E2E。
8. 一次真实测试 provider 的 Agent、Team、SSE、Memory、Studio smoke test。

### 9.2 必测端到端场景

```text
Dify 登录
→ 创建 Agent A / Agent B
→ 创建 Team
→ 真实 Team Run
→ Runs 页面实时观察
→ Run 产生 Memory proposal 并提交
→ 第二次 Run 由另一 Agent 读取 Memory
→ Studio 中选择 ULOO / Agno Team
→ 调试、发布、导出、导入
```

### 9.3 发布门

- 固定根仓库、Dify fork、Agno、Dify Plugin SDK 的 commit/版本。
- 从源码构建 Dify API、Dify Web、ULOO Core 与插件包。
- 全新机器能从 Git checkout 构建并通过 smoke test。
- 生成 SBOM、第三方许可证、migration/回滚说明与环境变量说明。
- 禁止 `docker cp`、手改运行容器或依赖旧 volume 中的残留代码。

## 10. 建议的任务批次

| 批次 | 任务 | 可见交付结果 |
|---|---|---|
| A | 修复 Dify 源码运行环境 | `3000` 可登录，不再无限加载 |
| B | ULOO 导航、Dashboard、Client | Dify 内出现真实 ULOO 入口与状态 |
| C | Agents 与 Teams 页面 | 页面完成配置闭环 |
| D | 模型 Registry 与 Agno Factory | 单 Agent 和 Team 可真实运行 |
| E | Run/Event/SSE 与 Runs 页面 | 可实时观察且刷新可恢复 |
| F | Memory 数据、执行链与页面 | Agent 间可验证地共享长期记忆 |
| G | Studio Agent Strategy 插件 | 工作流画布可调用 Team |
| H | E2E、源码构建与发布 | 新机器可复现完整产品 |

批次 A～C 完成前，不开始 Studio 插件；批次 D～E 完成前，不宣称“多 Agent 可用”；批次 F 的跨 Run 验收未通过前，不宣称“跨 Agent 记忆完成”。

## 11. 最终用户看到的产品

完成后用户只进入 Dify：

- 左侧导航出现 ULOO。
- 在 Agents 页面定义角色，在 Teams 页面组织 Agno Team。
- 在 Dashboard 查看依赖和能力状态。
- 点击运行后，在 Runs 页面实时查看成员事件、输出、耗时、usage 和错误。
- 在 Memory 页面审核、查询、撤销跨 Agent 记忆。
- 在 Studio 的 Agent 节点中选择 `ULOO / Agno Team`，把 Team 作为工作流能力调用。

全程复用 Dify 登录与 workspace，不需要第二套账号，不需要 curl，不需要手改数据库，也不需要用户接触 Core token 或模型密钥。
