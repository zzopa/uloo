# ULOO 主流程实施提示词

> 用途：把本文件完整交给 AI 编程编辑器，让它基于当前仓库继续开发。
>
> 唯一产品主流程：**任务入口 → 团队规划 → 多 Agent 实时执行 → 汇总交付**。

---

## 一、你的身份与最终目标

你是本项目的高级产品架构师、全栈工程师和质量负责人。请直接检查并修改当前仓库中的真实源码，不要新建一个与现有项目割裂的 Demo。

你要把 ULOO 做成一个真正可用的多智能体工作操作系统，而不是 Agent CRUD 管理后台。

最终用户进入产品后，应当完成下面这条连续流程：

```text
输入一个业务任务
  → 系统选择或推荐团队
  → 主管 Agent 生成可读、可修改的执行计划
  → 用户确认执行
  → 多个 Agent 并行或顺序协作
  → 页面实时显示成员状态、事件、产物、错误和用量
  → 主管 Agent 汇总结果
  → 用户查看、复制、下载和继续追问
  → 可审计地沉淀跨 Agent 记忆
```

技术定位必须保持不变：

- Dify 源码 Web 是唯一用户界面。
- Dify API/BFF 复用现有登录、workspace 和权限边界。
- ULOO Core 负责 Task、Plan、Run、Event、Artifact 和 Memory。
- Agno 是真实 Agent/Team 执行内核。
- 浏览器只访问 `/console/api/uloo/**`，不得直接访问 ULOO Core 8200 端口。
- Dify Studio 的 ULOO 节点和 ULOO 任务工作台必须调用同一套 Core 执行服务。

---

## 二、必须先遵守的约束

1. 先阅读并遵守：
   - `docs/ULOO_V2_IMPLEMENTATION_PLAN.md`
   - `docs/STAGE_STATUS.md`
   - `TODO.md`
   - `contracts/openapi.yaml`
   - `vendor/dify/AGENTS.md` 及修改目录下更深层的 `AGENTS.md`
2. 基于 `vendor/dify/` 的真实 Dify 源码开发，不重新拉取 Dify，不以官方成品 Web/API 镜像冒充源码成果。
3. 不开发第二套用户系统，不新建第二套通用工作流画布。
4. 不执行 `docker cp`，不进入容器手工覆盖源码。
5. Docker 仅用于 PostgreSQL、Redis、pgvector、Plugin Daemon 等基础依赖。
6. 正式功能禁止硬编码假数据、FakeModel、固定回答和静默降级。
7. 演示数据只能存在于明确标注的 `/uloo-preview/**`，且必须显示“演示数据/未调用模型”。
8. 模型未配置时显示 `MODEL_CONFIG_MISSING`，不得自动生成看似真实的回答。
9. 不保存或展示隐藏思维链。事件只记录安全摘要、状态和结构化产物。
10. 不要先堆 NATS、MinIO、新向量库或复杂微服务；先完成同步执行加持久事件和 SSE。
11. 所有新增接口先更新服务端 Schema/OpenAPI，再生成 Dify TypeScript contracts；禁止长期维护手写重复 DTO。
12. 每完成一步都必须包含：实现、测试、页面验收、状态文档更新。没有证据不得标为完成。
13. 保留用户现有修改，不覆盖无关代码，不使用 `git reset --hard`。

---

## 三、先调整产品信息架构

目前产品像开发者配置后台。请调整为任务优先的信息架构。

### 3.1 主导航

主导航按用户使用频率排列：

1. `开始任务`：默认首页和主要入口。
2. `任务记录`：历史任务及运行状态。
3. `交付成果`：报告、文件、结构化输出。
4. `共享记忆`：记忆查询、来源和审核。
5. `资源配置`：Agents、Teams、模型、工具等低频配置。

Agents 和 Teams 不再是产品第一入口，可以保留现有页面，但应归入“资源配置”。

### 3.2 核心页面

- `/uloo`：任务中心，而不是系统架构介绍页。
- `/uloo/tasks/{task_id}`：贯穿计划、执行、交付的统一任务工作台。
- `/uloo/tasks`：任务历史列表。
- `/uloo/artifacts`：交付成果列表。
- `/uloo/runs/{run_id}`：运行详情，可从任务工作台打开。
- `/uloo/memory`：共享记忆与审核。
- `/uloo/resources/agents`、`/uloo/resources/teams`：资源配置；若暂时保留旧 URL，必须提供兼容跳转。

### 3.3 页面视觉原则

- 页面默认回答“我现在能让智能体做什么”，而不是展示技术架构。
- 主任务输入框必须处于首页首屏视觉中心。
- 技术健康状态收进小型状态区域，不占据核心位置。
- 所有运行状态必须来自真实 API。
- 用户首先看到自然语言、进度和结果；Run ID、Trace ID、Token 等放在可展开的技术详情中。

---

## 四、总体数据模型与接口

请先检查现有表和接口，复用已有字段。缺少时通过 Alembic migration 增加，不得直接手改数据库。

### 4.1 Task

建议最小字段：

```json
{
  "id": "uuid",
  "workspace_id": "trusted-workspace-id",
  "title": "任务标题",
  "query": "用户原始需求",
  "status": "draft|planning|awaiting_approval|running|succeeded|failed|cancelled",
  "team_id": "uuid|null",
  "session_id": "uuid",
  "current_plan_version": 1,
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

### 4.2 TaskPlan

```json
{
  "id": "uuid",
  "task_id": "uuid",
  "version": 1,
  "summary": "对任务的简洁理解",
  "assumptions": ["假设"],
  "questions": ["真正影响执行的问题"],
  "recommended_team_id": "uuid",
  "steps": [
    {
      "id": "stable-step-id",
      "title": "调研现有方案",
      "description": "步骤目标和边界",
      "assigned_agent_id": "uuid",
      "depends_on": [],
      "expected_output": "调研摘要",
      "status": "pending"
    }
  ],
  "approval_status": "pending|approved|rejected",
  "created_at": "datetime"
}
```

### 4.3 Run、RunMember、RunEvent

Run 必须绑定 `task_id`、`plan_id`、Team 版本快照和 Agent 版本快照。

RunEvent 至少包含：

```json
{
  "run_id": "uuid",
  "sequence": 12,
  "type": "agent.started",
  "timestamp": "datetime",
  "agent_id": "uuid|null",
  "plan_step_id": "string|null",
  "status": "running",
  "summary": "研究员开始收集资料",
  "payload": {},
  "trace_id": "uuid"
}
```

`(run_id, sequence)` 必须唯一且严格递增。事件入库后再推送 SSE，确保刷新和重连能够恢复。

### 4.4 Artifact

交付结果不能只塞进一段聊天文本。至少支持：

```json
{
  "id": "uuid",
  "task_id": "uuid",
  "run_id": "uuid",
  "type": "markdown|json|file|link",
  "name": "最终实施方案",
  "content": "...",
  "mime_type": "text/markdown",
  "source_agent_id": "uuid|null",
  "created_at": "datetime"
}
```

### 4.5 规范接口

在 ULOO Core 实现并写入 `contracts/openapi.yaml`：

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/v1/tasks` | 创建任务，保存原始需求 |
| `GET` | `/api/v1/tasks` | 按状态、时间、关键词分页查询 |
| `GET` | `/api/v1/tasks/{task_id}` | 返回任务、当前计划、当前 Run 和成果摘要 |
| `POST` | `/api/v1/tasks/{task_id}/plan` | 使用真实规划 Agent 生成计划 |
| `PATCH` | `/api/v1/tasks/{task_id}/plan` | 用户调整团队、成员或步骤，使用版本锁 |
| `POST` | `/api/v1/tasks/{task_id}/plan/approve` | 确认当前计划并创建正式 Run |
| `POST` | `/api/v1/tasks/{task_id}/plan/reject` | 驳回计划并附修改意见 |
| `POST` | `/api/v1/tasks/{task_id}/messages` | 对任务继续追问、补充约束或要求返工 |
| `GET` | `/api/v1/tasks/{task_id}/artifacts` | 查询任务交付成果 |
| `POST` | `/api/v1/teams/by-key/{team_key}/runs` | Studio/API 的规范 Team Run 入口 |
| `GET` | `/api/v1/runs/{run_id}` | Run 快照、成员状态、usage 和错误 |
| `GET` | `/api/v1/runs/{run_id}/events` | 使用 `after_sequence` 恢复事件 |
| `GET` | `/api/v1/runs/{run_id}/stream` | SSE 实时事件，支持 `Last-Event-ID` |
| `POST` | `/api/v1/runs/{run_id}/cancel` | 取消运行 |
| `POST` | `/api/v1/runs/{run_id}/retry` | 从失败步骤或整个 Run 重试 |

Dify API 必须提供对应的同源 BFF：

```text
/console/api/uloo/tasks/**
/console/api/uloo/runs/**
/console/api/uloo/artifacts/**
```

BFF 负责注入当前 Dify workspace、服务凭据、request ID 和 trace ID；不得接受浏览器传入 workspace 覆盖可信值。

### 4.6 统一错误码

至少支持：

- `TASK_NOT_FOUND`
- `PLAN_NOT_FOUND`
- `PLAN_VERSION_CONFLICT`
- `PLAN_NOT_APPROVED`
- `TEAM_NOT_FOUND`
- `AGENT_NOT_FOUND`
- `MODEL_CONFIG_MISSING`
- `MODEL_ERROR`
- `TIMEOUT`
- `CANCELLED`
- `BUDGET_EXCEEDED`
- `RECURSION_BLOCKED`
- `RUNTIME_UNAVAILABLE`

响应统一包含 `code/message/request_id/trace_id/details`。

---

## 五、按顺序实施主流程

不要同时铺开所有页面。严格按照下面的 Step 0 → Step 8 顺序推进。前一步验收失败，不得宣称后一步完成。

## Step 0：现状审计和实施基线

### 要做什么

1. 检查当前 Git 状态，区分用户已有修改与本轮修改。
2. 运行现有 Core、Dify BFF、Web 的最小测试，记录真实基线。
3. 核对现有 Agent/Team Factory、test-runs、capabilities 和 BFF 契约。
4. 列出能复用的代码与真正缺少的部分。
5. 在 `docs/STAGE_STATUS.md` 增加“主流程实施”章节，但此时不要把未实现功能标为完成。

### 达到的效果

形成一份基于代码事实的缺口清单，确认不会重新实现已有 Agent CRUD 和 Agno Factory。

### 验收

- 给出修改前测试结果。
- 给出将要新增的 migration、接口和页面清单。
- `git diff` 不包含与主流程无关的大规模改写。

---

## Step 1：把首页改成真正的任务入口

### 要做什么

1. 将 `/uloo` 的主体改为任务中心。
2. 首屏提供大型任务输入框，支持多行文本。
3. 提供三个可点击的示例任务，但点击后只填充输入框，不直接伪造结果。
4. 提供团队选择：
   - “自动选择团队”为默认项。
   - 也可以选择一个已启用 Team。
5. 提供必要的高级选项：项目/会话、最大执行时间、预算上限；默认折叠。
6. 点击“规划任务”时调用 `POST /tasks`，随后调用 `POST /tasks/{id}/plan`。
7. 成功后导航到 `/uloo/tasks/{task_id}`。
8. 首页下方显示真实最近任务，而不是能力矩阵或假统计。

### 页面应当看到

```text
今天需要智能体团队完成什么？
[ 输入任务................................ ]
[ 自动选择团队 v ]                 [规划任务]

最近任务
● 运行中  某项目技术方案
✓ 已完成  竞品分析报告
```

### 达到的效果

第一次使用产品的人不用先创建 Agent，也能清楚知道从哪里开始。

### 验收

- 空输入不能提交。
- 创建失败显示业务错误和 Trace ID。
- 刷新后最近任务来自数据库。
- 正式页面没有本地硬编码任务。
- 组件测试覆盖提交、失败和重复点击。

---

## Step 2：实现真实团队规划

### 要做什么

1. 为规划建立明确的 Planner/Leader 运行路径，调用真实 Agno Agent，不使用字符串模板冒充规划结果。
2. 自动团队推荐只能从当前 workspace 中已启用、验证通过的 Team 选择。
3. Planner 输入至少包含：用户需求、可用 Team 摘要、成员能力、限制和可读取的项目记忆摘要。
4. Planner 输出必须经过 Pydantic/JSON Schema 校验，生成 `TaskPlan`。
5. 如果模型输出不合法，允许有限次数的结构化修复；最终失败必须显示 `MODEL_ERROR`，不能偷偷返回默认计划。
6. 计划页面展示：
   - 系统对目标的理解。
   - 关键假设。
   - 真正阻断执行的澄清问题。
   - 推荐 Team 及推荐理由。
   - 步骤、负责人、依赖和预期产物。
7. 用户可以修改 Team、负责人、顺序和步骤描述。
8. 保存修改必须提交 `expected_version`，冲突返回 409 并提示刷新比较。

### 页面应当看到

```text
阶段 1/4  团队规划

任务理解：……
推荐团队：产品研发组

1. 需求澄清        需求分析师       无依赖
2. 技术设计        架构师           依赖 1
3. 风险审查        审核员           依赖 2

[修改计划] [确认并开始执行]
```

### 达到的效果

用户在花费大量模型调用之前知道系统准备怎么做、由谁做，并且可以纠正计划。

### 验收

- 计划来自真实模型，响应记录 `runtime_type=agno`、`is_mock=false`。
- 推荐 Team 和 Agent 必须真实存在且属于当前 workspace。
- 删除或停用成员后不能批准包含该成员的旧计划。
- 刷新页面后计划仍存在。
- 计划版本冲突测试通过。

---

## Step 3：计划确认和 Run 创建

### 要做什么

1. 点击“确认并开始执行”调用 `/plan/approve`。
2. 服务端在同一事务内：
   - 校验计划版本和审批状态。
   - 校验 Team、Leader、成员、模型、工具和知识引用。
   - 保存 Team/Agent 配置快照。
   - 创建 Task Run、RunMember 和第一批 RunEvent。
   - 把 Task 状态更新为 `running`。
3. 防止重复点击创建多个 Run：使用幂等键或 Task 状态约束。
4. 创建成功后，任务工作台自动进入执行视图并连接 SSE。
5. 不要求用户离开当前任务页面。

### 达到的效果

规划与执行之间有清晰确认点，且重复提交不会重复扣费或创建重复任务。

### 验收

- 双击确认只产生一个 Run。
- 配置缺失时不创建半成品 Run。
- Run 保存 Team/Agent 版本快照。
- Task、Plan、Run 的状态转换有自动化测试。

---

## Step 4：实现真实 Agno 多 Agent 执行

### 要做什么

1. 使用现有 Agent/Team Factory 构建真实 Agno Team。
2. 只使用当前 Agno 版本正式支持的 `coordinate`、`route`、`collaborate` mode。
3. 将 TaskPlan 步骤映射为 Team 指令、成员任务或 Agno 支持的委派机制；不得自行编写一套伪多 Agent 循环并声称是 Agno Team。
4. 每个成员必须有可区分的角色、输入、状态和输出摘要。
5. 实现：
   - 超时。
   - 用户取消。
   - Token/预算限制。
   - 最大迭代次数。
   - 递归调用保护。
   - 模型和工具错误映射。
6. Agent 产生的中间结果保存为 RunMember 结果或 Artifact。
7. 执行失败不得返回“成功但内容为空”。
8. 测试可使用显式 FakeModel fixture；正式运行禁止 fallback。

### 达到的效果

至少两个不同职责的 Agent 对同一任务产生真实、可区分的协作结果，最终由 Leader 汇总。

### 验收

- 真实 E2E 中 `runtime_type=agno`、`is_mock=false`。
- 可以证明至少两个成员被实际调用。
- 成员输出不同，并能在最终汇总中找到引用关系。
- 模型缺配置、超时、取消、预算超限均有稳定错误码。
- 修改 Team 后，旧 Run 仍使用自己的配置快照。

---

## Step 5：Run/Event/SSE 实时执行界面

### 要做什么

1. 建立并迁移 `runs`、`run_members`、`run_events`。
2. 事件至少包含：
   - `run.created`
   - `run.started`
   - `plan.step.started`
   - `team.started`
   - `agent.assigned`
   - `agent.started`
   - `agent.output`
   - `agent.completed`
   - `artifact.created`
   - `team.completed`
   - `memory.retrieved`
   - `memory.proposed`
   - `run.completed`
   - `run.failed`
   - `run.cancelled`
3. `GET /runs/{id}/stream` 使用标准 SSE：
   - `id` 等于事件 sequence。
   - `event` 等于事件类型。
   - `data` 为 JSON。
   - 支持 `Last-Event-ID`。
   - 定期发送心跳。
4. Dify BFF 必须流式转发，禁止先缓存完整响应。
5. Web 首次打开先查询 Run 快照和历史事件，再连接 SSE。
6. SSE 断线后从最后 sequence 继续，不重复渲染。
7. 任务工作台同时提供：
   - 左侧执行计划。
   - 中间实时事件时间线。
   - 右侧成员状态、耗时和产物。
8. 不显示隐藏思维链；只显示“开始调研”“形成摘要”等安全事件。

### 页面应当看到

```text
阶段 3/4  正在执行                     [取消任务]

计划                    实时动态                   团队
✓ 需求澄清              10:01 主管开始执行          主管 ●
● 技术设计              10:02 分配给架构师          架构师 ●
○ 风险审查              10:03 研究员产出资料摘要     研究员 ✓

已用时 01:42 · Token 8,420 · Trace ID（展开查看）
```

### 达到的效果

用户不用看日志，就能理解任务现在进行到哪一步、哪个 Agent 在工作、产生了什么以及哪里失败。

### 验收

- 刷新页面后历史时间线不丢失。
- 主动断开再连接后事件不重不漏。
- sequence 严格递增。
- 同时打开两个浏览器能看到一致状态。
- SSE 失败时降级为有限频率轮询并明确提示，不伪装成实时连接。

---

## Step 6：主管汇总与交付成果

### 要做什么

1. 所有必要步骤完成后，由 Team Leader 执行最终汇总。
2. 汇总输入必须包括成员产物引用和计划验收目标，不能只重新回答原始问题。
3. 结果页面至少分为：
   - 最终结论。
   - 关键发现。
   - 可执行建议。
   - 风险和未解决问题。
   - 附件/结构化成果。
   - 来源 Agent 和对应产物。
4. 将结果持久化为 Artifact。
5. 支持复制 Markdown、下载已有文件、查看 JSON；不要伪造文件下载。
6. 提供“继续追问”“要求修改”“基于此结果创建新任务”。
7. 继续追问必须沿用 `task_id/session_id`，并保存为新一轮 Run 或修订版本，而不是覆盖旧结果。
8. Run 成功后更新 Task 状态；失败时保留已完成成员产物，允许从失败步骤重试。

### 页面应当看到

```text
阶段 4/4  已完成

最终交付：多智能体平台实施方案
[预览] [复制 Markdown] [下载已有文件]

关键结论 ……
建议行动 ……
引用产物：研究员/调研摘要、架构师/技术设计

[继续追问] [要求修改] [创建后续任务]
```

### 达到的效果

用户得到的是可继续使用的工作成果，而不是一串底层事件或 Agent 聊天记录。

### 验收

- 刷新后最终成果仍存在。
- Artifact 能追溯到 Run 和来源 Agent。
- 继续追问产生新 Run，不覆盖旧 Run。
- 失败重试不会重复已经确认的外部副作用。

---

## Step 7：跨 Agent 记忆闭环

### 要做什么

1. Run 开始前按 workspace/project/team/agent scope 检索相关记忆。
2. 只把经过裁剪、有来源的记忆注入模型上下文。
3. Agent 不能直接写长期记忆，只能生成 MemoryProposal。
4. Proposal 保存内容、scope、来源 Run/Agent、置信度、过期时间和引用。
5. 按策略自动批准低风险事实，敏感或冲突内容进入人工审核。
6. 提交后产生 `memory.committed` 事件。
7. `/uloo/memory` 支持搜索、来源、版本、撤销和恢复。
8. 记忆撤销后不能继续被召回。

### 达到的效果

Agent A 在第一次任务中沉淀的信息，Agent B 在后续同项目任务中能够真实读取并使用，同时用户能够知道它从哪里来并撤销错误内容。

### 验收

- 测试证明记忆内容进入后续模型上下文，而不只是数据库存在记录。
- 跨 workspace 永不召回。
- 冲突记忆不被静默覆盖。
- 撤销、过期和版本策略生效。

---

## Step 8：Dify Studio 节点闭环

### 要做什么

1. 在 `extensions/dify-agno-strategy/` 创建合法的 Dify Agent Strategy 插件。
2. Studio 中显示 `ULOO / Agno Team`。
3. 参数至少包括：
   - `query`
   - `team_key`
   - `execution_mode`
   - `project_id`
   - memory scopes
   - limits
4. 输出至少包括：
   - `content`
   - `structured_output`
   - `task_id`
   - `run_id`
   - `trace_id`
   - `member_results`
   - `artifacts`
   - `usage`
   - `status`
5. 节点调用和任务工作台使用同一执行服务，不复制 Agno 编排逻辑。
6. 完成 Start → ULOO / Agno Team → End：单节点调试、整图运行、保存、重开、发布、导出和导入。

### 达到的效果

非技术用户通过任务首页使用 ULOO；高级用户通过 Dify Studio 把同一多 Agent 能力嵌入自动化流程。

### 验收

- Studio 节点真实执行 Agno Team。
- 保存、关闭重开后参数不丢。
- 导出再导入后仍可运行。
- 普通 Dify 工作流和原有 Agent 节点回归通过。

---

## 六、演示页要求

`/uloo-preview` 可以保留，目的是让未登录用户看懂产品流程，但必须遵守：

1. 演示页完整展示四阶段状态：任务入口、团队规划、实时执行、汇总交付。
2. 所有模拟内容都显示固定标签：`演示数据 · 未调用真实模型`。
3. 演示执行可以使用确定性事件序列，但不得写入正式数据库，不得显示伪造的 Token 费用或 `is_mock=false`。
4. 页面提供“进入正式工作空间”入口。
5. 正式页面绝不引用演示状态作为真实结果。

---

## 七、测试与质量门

每一步至少补充下面对应测试：

### ULOO Core

- Pydantic Schema 单元测试。
- Task/Plan 状态机测试。
- workspace 隔离测试。
- 乐观锁和幂等测试。
- Agent/Team Factory 契约测试。
- Run/Event 持久化测试。
- SSE 断线恢复测试。
- 取消、超时、预算和错误码测试。
- Memory 检索、版本和撤销测试。
- OpenAPI drift 测试。

### Dify API/BFF

- 强类型路由注册测试。
- workspace 和服务凭据注入测试。
- 路径 allow-list 和 traversal 测试。
- JSON 错误透传测试。
- SSE 不缓冲测试。
- 超大响应和超时测试。

### Dify Web

- 任务创建和错误状态组件测试。
- 计划编辑和版本冲突测试。
- 批准重复点击测试。
- SSE reducer 去重、顺序和重连测试。
- 执行中、成功、失败、取消页面测试。
- Artifact 展示和继续追问测试。
- 浏览器 E2E 覆盖完整主流程。

### 真实 E2E

至少准备一个明确的测试 provider 或专用真实模型配置，完成：

```text
创建任务
→ 真实 Planner 生成计划
→ 用户批准
→ 至少两个真实 Agent 执行
→ SSE 显示成员事件
→ Leader 汇总
→ 产出 Artifact
→ 第二次任务读取第一次记忆
```

E2E 证据必须包含 `runtime_type=agno`、`is_mock=false`、真实 Run ID、Trace ID、成员结果和 usage。

---

## 八、每轮开发的工作方式

每次只领取一个可在本轮完整验收的纵向切片。例如：

```text
Task 创建表和接口
→ Dify BFF contract
→ 首页输入框
→ 创建成功跳转
→ 单测和浏览器验收
→ 更新状态文档
```

不要出现“后端表建完但页面不可用”或“页面做好但接口是假数据”的横向半成品。

每轮结束必须输出：

1. 本轮完成的用户可见效果。
2. 修改文件清单。
3. migration 和接口变化。
4. 运行过的测试及结果。
5. 浏览器验收路径和结果。
6. 未完成项和下一纵向切片。
7. `docs/STAGE_STATUS.md` 和 `TODO.md` 的同步更新。

如果遇到模型密钥、团队 fork URL、外部账号等确实需要用户提供的条件，可以继续完成不依赖该条件的代码和测试；到真实联网验收前再明确列出阻断，不要用假结果替代。

---

## 九、最终完成定义

只有同时满足以下条件，才可以说主流程完成：

- 用户在首页输入任务后可以一路完成到最终交付，不需要 curl、手改数据库或进入容器。
- 规划结果来自真实 Planner Agent。
- 至少两个 Agno Agent 完成真实协作。
- 运行事件持久化并通过 SSE 实时显示。
- 刷新和断线不会丢失运行状态。
- 最终结果作为 Artifact 保存并可追溯来源。
- 后续任务能够真实使用先前提交的跨 Agent 记忆。
- Dify Studio 节点复用同一运行能力。
- 所有正式能力均无 FakeModel、硬编码结果或静默 fallback。
- 不显示思维链，不泄漏 API Key，不跨 workspace 串数据。
- 所有 migration、契约、单测、E2E 和状态文档均通过并可在干净环境复现。

---

## 十、现在立即开始的第一批任务

请现在执行以下顺序，不要先继续美化 Agents CRUD：

1. 完成 Step 0 现状审计。
2. 设计并实现 Task/TaskPlan 最小数据模型和 migration。
3. 实现 `POST /tasks`、`GET /tasks/{id}`、`GET /tasks`。
4. 更新 OpenAPI、生成 Dify contracts、增加 BFF。
5. 将 `/uloo` 重做为任务输入首页。
6. 打通“输入任务 → 创建 Task → 跳转任务工作台”的第一个真实纵向切片。
7. 完成自动化测试和浏览器验收后，再进入真实 Planner 实现。

第一批任务的完成界面应该是：用户打开 `/uloo`，输入一个任务，点击“规划任务”，系统创建真实 Task 并进入 `/uloo/tasks/{task_id}`；即使 Planner 尚未实现，也必须明确显示“等待生成计划”，不能显示伪造计划。
