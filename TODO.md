# ULOO V2 优化与交付清单

> 最后盘点：2026-09-20
>
> 产品目标：以 **Dify 源码 Web 作为唯一 UI**，通过 Dify API/BFF 调用 ULOO Core，使用 **真实 Agno Team** 完成多 Agent 协同，并提供可恢复的 Run/Event 与跨 Agent Memory。
>
> 约束：不另建用户系统、不另建工作流画布、不使用假模型冒充完成、不以官方 Dify API/Web 成品镜像代替源码成果。

## 0. 当前结论

当前项目处于“基础后端可测试、产品闭环尚未形成”的阶段，整体约完成 **15%–20%**。

| 能力 | 当前状态 | 是否可供最终用户使用 |
|---|---|---|
| Dify 1.16.1 源码基线 | 本机存在嵌套仓库；ULOO BFF 改动已暂存但未提交，根仓库新 clone 无法取得该源码 | 否 |
| ULOO Core 健康检查 | 已实现；未配置模型时 ready 正确返回 503 | 部分 |
| Agent/Team CRUD | 已实现并有数据库测试；仍缺 workspace 隔离、原子乐观锁和完整 PATCH 校验 | 仅 API 可试用 |
| Dify Console BFF | 代码与 26 个单测已存在；改动未提交 | 部分 |
| Dify ULOO 页面 | 未开始 | 否 |
| 真实 Agno 执行 | 未开始；`test-runs` 仍为占位响应 | 否 |
| Dify Studio Team 节点 | 未开始 | 否 |
| Run/Event/SSE | 未开始 | 否 |
| 跨 Agent Memory | 未开始 | 否 |
| 源码发布 | 未开始 | 否 |

已验证基线：

- [x] ULOO Core：`20 passed`
- [x] OpenAPI：重新生成结果为 `unchanged`
- [x] Dify BFF：`26 passed`
- [x] Dify API：`uv lock --check` 通过
- [ ] 真实浏览器端到端闭环：尚不存在
- [ ] 真实模型多 Agent 运行：尚不存在

## 1. 执行原则

- 严格按 P0 → P1 → P2 顺序推进；前一验收门未通过，不把后一阶段标为完成。
- 每个任务必须同时包含实现、自动化测试、契约更新、状态记录和可复现验证。
- `capabilities` 只公布已经可工作的能力；未实现接口返回明确的 `501/503` 和固定错误码。
- 浏览器只能访问 Dify 同源 BFF；ULOO Core 的服务令牌、模型密钥永不返回浏览器。
- 复用 Dify workspace，不开发额外用户系统；所有 ULOO 数据仍必须按 workspace 隔离。
- FakeModel 只能在测试 fixture 中注入，生产配置缺失时必须显式失败。
- Event 只保存安全摘要与结构化状态，不保存隐藏思维链。

---

## P0：先修正真实性、可复现性和数据正确性

### ULOO-001 固化 Dify 源码与改动

- [ ] 在 `vendor/dify` 提交当前已暂存的 BFF/config/test/lock 改动。
- [ ] 为团队 fork 配置 `origin`，保留官方仓库为 `upstream`。
- [ ] 推荐将 `vendor/dify` 转为指向团队 fork 固定 commit 的 Git submodule；若决定使用 subtree，则必须把完整源码纳入根仓库，不能继续仅靠 `.gitignore` 中的本机目录。
- [ ] 根仓库记录确切 Dify commit，不只记录版本字符串 `1.16.1`。
- [ ] 在全新目录执行 clone/checkout，确认能取得带 ULOO BFF 的 Dify 源码。
- [ ] 保存 Dify API/Web 源码启动命令和热更新证据。

验收效果：换一台机器克隆仓库后，不依赖当前电脑残留文件即可得到同一份 Dify + ULOO 源码。

阻断信息：需要团队 fork URL；在 URL 确定前可以完成本地 commit，但不能宣称源码基线可复现。

### ULOO-002 修正“能力虚报”和占位接口（已完成）

- [x] 将 `GET /api/v1/capabilities` 改为结构化能力状态；当前 `streaming=false`、`memory=false`、`runs=false`，直到相应验收门通过再开启。
- [x] `POST /agents/{id}/test-runs` 在真实执行完成前返回 `501 NOT_IMPLEMENTED`，不得返回 `is_mock=false`。
- [x] 为所有错误统一响应结构：`code/message/request_id/trace_id/details`。
- [x] 在 OpenAPI 中给 health/capabilities/test-runs 添加明确 response model 和错误响应。
- [x] 补充测试，防止未实现功能再次被标成可用。
- [x] 重新生成并锁定 `contracts/openapi.yaml`。

验收效果：前端和运维看到的状态与真实功能完全一致，不会把占位接口误认为真实 Agno 运行。

### ULOO-003 完成服务边界和 workspace 隔离

- [ ] ULOO Core 校验 BFF 注入的服务令牌；令牌缺失或错误返回 401/403。
- [ ] 读取并校验 `X-ULOO-Workspace`，但不创建额外用户表或登录系统。
- [ ] `agent_definitions`、`team_definitions`、后续 run/event/memory 表增加 `workspace_id`。
- [ ] 唯一键改为 `(workspace_id, key)`；所有查询、更新、删除必须带 workspace 条件。
- [ ] 防止用户通过请求体覆盖 workspace；workspace 只能来自可信服务头。
- [ ] BFF 继续复用 Dify 登录和 workspace，上游 token 不暴露给浏览器。
- [ ] 添加跨 workspace 不可读、不可改、不可引用的集成测试。

验收效果：忽略自建用户体系，但不同 Dify workspace 的 Agent、Team 和运行数据不会串库。

### ULOO-004 修复 CRUD 一致性

- [ ] 把 Agent/Team 乐观锁改为数据库原子条件更新：`WHERE id=? AND version=?`，并以受影响行数判定 409。
- [ ] Team PATCH 合并“数据库现值 + 请求变更”后执行完整校验，禁止无成员、Leader 不在成员中、mode/Leader 不匹配。
- [ ] schema 中成员和 Leader 使用 UUID 类型，非法 UUID 稳定返回 422，不能抛 500。
- [ ] 禁止重复成员 ID，返回业务错误而不是数据库唯一约束 500。
- [ ] `limits` 增加合理上下界；`memory_policy` 使用枚举约束 scope。
- [ ] 决定软删除后 key 是否可复用，并让数据库唯一约束与 API 行为一致。
- [ ] Agent/Team 列表返回统一分页对象：`items/total/offset/limit`，避免前端猜测总数。
- [ ] 添加并发更新、非法 PATCH、重复成员、软删除 key、边界值测试。

验收效果：并发编辑不会静默覆盖，任何可保存的 Team 都满足执行前置条件。

### ULOO-005 建立持续验证

- [ ] 为 ULOO Core 增加 Ruff、类型检查和覆盖率工具，固定在 `pyproject.toml/uv.lock`。
- [ ] 根目录增加统一验证脚本：Core tests、migration、OpenAPI drift、Dify BFF tests、Dify lock check。
- [ ] 增加 CI；PR 必须同时通过两个仓库/工作区的检查。
- [ ] 对 PostgreSQL migration 执行 `upgrade → downgrade → upgrade` 自动验证。
- [ ] 测试使用独立测试数据库或临时 schema，禁止污染开发库。
- [ ] 清理或迁移当前开发库遗留的 `test-*`/`smoke-*` 数据，并记录操作。

P0 验收门：全新检出可构建；能力不虚报；workspace 隔离成立；CRUD/迁移/契约/静态检查全部通过。

---

## P1：完成 Stage 2 的 Dify 可视化配置闭环

### ULOO-101 Dify Web 基础接入

- [ ] 在 Dify Web 增加 `/uloo` 路由与导航入口，沿用现有布局、主题、国际化和权限组件。
- [ ] 建立只访问 `/console/api/uloo/**` 的 TypeScript client；禁止 Web 直连 8200。
- [ ] 由 `/capabilities` 驱动模式与功能开关，不在 UI 硬编码不存在的能力。
- [ ] 增加统一 loading/empty/error/503/409 展示和 request/trace ID。
- [ ] 暂未实现的 Runs/Memory 使用明确“尚未启用”状态，不放假数据。

### ULOO-102 Agents 页面

- [ ] `/uloo/agents`：列表、搜索、分页、新建、编辑、启停、软删除。
- [ ] 模型、工具、知识库字段只选择服务端返回的引用，不接受或显示 API Key。
- [ ] 显示 `version`；409 时提示重新载入并比较变更。
- [ ] 接入 `/validate`；真实测试按钮在 ULOO-301 完成前保持不可用并解释原因。
- [ ] 添加组件测试和 Playwright E2E。

### ULOO-103 Teams 页面

- [ ] `/uloo/teams`：列表、搜索、分页、新建、编辑、启停、软删除。
- [ ] 可选择已启用 Agent、Leader 和 Agno mode；表单动态执行 mode/Leader/member 规则。
- [ ] 配置 instructions、memory policy 和 limits。
- [ ] 接入 `/validate` 并按字段呈现错误。
- [ ] 不创建第二套 Workflow Canvas。
- [ ] 添加刷新后配置不丢失、冲突更新、跨 workspace 隔离 E2E。

P1 验收门：用户只通过 Dify 页面创建两个 Agent、组成 Team、刷新后仍存在；网络响应无密钥；无手写假数据。

---

## P2：真实 Agno 执行、Run/Event 与 SSE

### ULOO-201 服务端模型配置

- [ ] 定义 `model_ref` 解析协议和 provider registry，密钥仅来自服务端环境/密钥存储。
- [ ] 至少完成一个 OpenAI-compatible provider 的真实连通性校验。
- [ ] `/validate` 真正检查模型、工具和知识引用是否可解析，而不只检查字符串非空。
- [ ] 缺失配置返回 `MODEL_CONFIG_MISSING`，调用错误返回 `MODEL_ERROR`，禁止 fallback 到 FakeModel。

### ULOO-202 Agno Agent/Team Factory

- [ ] 将 AgentDefinition 映射为真实 `agno.agent.Agent`。
- [ ] 将 TeamDefinition 映射为真实 `agno.team.Team`，只使用 Agno 1.8.4 官方 mode。
- [ ] 明确 `leader_agent_id` 到 Agno team instructions/coordination role 的映射并写契约测试。
- [ ] 建立工具 allow-list 与适配层，拒绝任意动态 import/执行。
- [ ] 每次运行保存 Agent/Team 配置快照和版本，历史 Run 不受后续编辑影响。

### ULOO-203 Run/Event 数据模型和接口

- [ ] migration：`runs`、`run_members`、`run_events`，包含 workspace、task/run/trace、team version、状态、输入输出、usage、错误和时间字段。
- [ ] Event 使用 `(run_id, sequence)` 唯一递增序号，并保存安全摘要。
- [ ] 实现 `POST /teams/by-key/{team_key}/runs`，支持 blocking 和 streaming。
- [ ] 实现 `GET /runs`、`GET /runs/{id}`、`GET /runs/{id}/events`。
- [ ] 实现 `GET /runs/{id}/stream`，支持 SSE、`Last-Event-ID` 和 `after_sequence` 补发。
- [ ] 实现 `POST /runs/{id}/cancel`、超时和预算限制。
- [ ] 实现 `task_id` 幂等与 `parent_run_id/call_chain` 递归保护。
- [ ] 固定并测试错误码：`TEAM_NOT_FOUND`、`AGENT_NOT_FOUND`、`MODEL_CONFIG_MISSING`、`MODEL_ERROR`、`TIMEOUT`、`CANCELLED`、`BUDGET_EXCEEDED`、`RECURSION_BLOCKED`、`RUNTIME_UNAVAILABLE`。

### ULOO-204 真实执行验收

- [ ] `POST /agents/{id}/test-runs` 调用真实 Agno Agent 并返回有 schema 的结果。
- [ ] 两个角色不同的 Agent 完成一次真实 Team Run，成员结果可区分。
- [ ] 验证 `runtime_type=agno`、`is_mock=false`、usage、run_id、trace_id。
- [ ] 验证模型未配置、模型失败、超时、取消和客户端 SSE 重连。
- [ ] FakeModel 仅用于单元测试 fixture；E2E 使用显式测试 provider 或真实测试账户。

P2 验收门：不使用 curl 伪造结果；真实模型完成多 Agent 协同；刷新和断线重连后事件仍完整。

---

## P3：Dify Studio 插件闭环

### ULOO-301 创建 Dify Agent Strategy 插件

- [ ] 新建 `extensions/dify-agno-strategy/`，锁定兼容的 Dify Plugin SDK 版本。
- [ ] Provider credential 保存 ULOO endpoint/token；普通节点参数不包含凭据。
- [ ] 策略显示名为 `ULOO / Agno Team`。
- [ ] 参数：query、team_key、execution_mode、project_id、memory scopes、limits。
- [ ] 输出：content、structured_output、task_id、run_id、trace_id、member_results、usage、status。
- [ ] blocking/streaming 都只调用规范 Run/SSE 接口。
- [ ] 添加 plugin package 校验、daemon 安装测试和示例 DSL。

### ULOO-302 Studio E2E

- [ ] Start → ULOO / Agno Team → End 单节点调试通过。
- [ ] 整图调试、保存、关闭重开、发布通过。
- [ ] 导出 DSL 后重新导入仍保留全部参数。
- [ ] 普通 Dify Workflow/Agent Node 回归通过。

P3 验收门：用户在 Studio 拖入一个 Team 节点即可运行，不需手改数据库、curl 或容器文件。

---

## P4：Runs 可观测界面

### ULOO-401 Dashboard 与 Runs

- [ ] `/uloo` 展示 Core/DB/Agno/model readiness；未实现统计不得伪造。
- [ ] Dashboard 展示 24 小时成功率、运行中任务、最近失败、token/费用。
- [ ] Runs 列表支持 Team、状态、trace、时间过滤和分页。
- [ ] `/uloo/runs/{run_id}` 展示 Team、成员状态、耗时、usage、安全摘要、输出和错误。
- [ ] SSE 实时更新；刷新或断线后从持久 Event 恢复。
- [ ] UI 不显示 chain-of-thought，只显示事件摘要和结构化产物。

P4 验收门：从 Dify 页面能解释一次运行由哪些 Agent 完成、何时失败、消耗多少，并可用 trace ID 贯穿排查。

---

## P5：跨 Agent Memory

### ULOO-501 Memory 数据和策略

- [ ] migration：memory、memory_version、memory_proposal、memory_citation；启用 pgvector 所需扩展和索引。
- [ ] 实现 search、proposal、commit、list/detail/delete/restore 契约。
- [ ] scope 支持 project/team/agent，并强制 workspace 边界。
- [ ] 实现去重、置信度、审核策略、过期时间、版本链和撤销。
- [ ] 正式写入必须经过 proposal，不允许 Agent 直接写 Memory。

### ULOO-502 执行链与页面

- [ ] Run 前检索并把经过裁剪的记忆注入模型上下文，产生 `memory.retrieved`。
- [ ] Run 后生成 proposal；提交成功产生 `memory.committed`。
- [ ] `/uloo/memory` 支持 scope/Team/Agent/Run/时间查询、来源审计、版本查看和撤销。
- [ ] E2E：Agent A 在 Run 1 产生记忆，Agent B 在 Run 2 实际读取并在输出中可验证地使用。

P5 验收门：记忆不是“数据库里有记录”而已；测试必须证明内容进入后续 Agent 的模型上下文，且撤销后不再召回。

---

## P6：安全、稳定性和源码发布

### ULOO-601 非功能完善

- [ ] 请求体、输出、事件、并发运行数和速率限制。
- [ ] 日志脱敏；禁止 API Key、Authorization、完整模型上下文进入日志/Event。
- [ ] trace/request ID 贯穿 Web → Dify API → ULOO Core → provider。
- [ ] SSRF、代理路径、SSE 慢客户端、超大 payload、异常 JSON 安全测试。
- [ ] 数据库索引、N+1、连接池和大事件列表性能测试。
- [ ] 备份、恢复、migration 回滚和数据保留策略。

### ULOO-602 Release

- [ ] 为 Dify API、Dify Web、ULOO Core 编写从固定源码构建的 Dockerfile。
- [ ] 新增 `deploy/compose.release.yaml`；仅 plugin daemon 可使用约定的官方镜像。
- [ ] 健康检查、启动依赖、环境变量校验和初始化 migration 自动化。
- [ ] 生成 SBOM、版本清单、第三方许可证和升级说明。
- [ ] 在全新机器/空 Docker 环境执行完整构建与 E2E。
- [ ] 明确禁止 `docker cp` 和依赖旧 volume/旧容器的手工修补。

P6 验收门：一条 release compose 命令启动的是修改后的源码产品，全部验收可在干净机器复现。

---

## P7：可选的可靠异步执行（主链路稳定后再评估）

- [ ] 先用压测和故障测试证明当前同步/SSE 架构确有队列需求。
- [ ] 若需要，再引入 NATS JetStream、Outbox relay、durable worker 和幂等恢复。
- [ ] 保持外部 Run API 不变。
- [ ] API/worker 重启后 Run 不丢失、不重复成功，事件序号仍单调。

未满足 P0–P6 前，不启动本阶段。

---

## 2. 建议迭代顺序

1. **迭代 A：可信基线** — ULOO-001～005。
2. **迭代 B：可视化配置** — ULOO-101～103，完成真正的 Stage 2。
3. **迭代 C：真实执行** — ULOO-201～204。
4. **迭代 D：Studio 产品闭环** — ULOO-301～302。
5. **迭代 E：运行可观测** — ULOO-401。
6. **迭代 F：跨 Agent 记忆** — ULOO-501～502。
7. **迭代 G：发布与加固** — ULOO-601～602。
8. **迭代 H：可靠队列** — 只有经过容量验证后才进入。

每个迭代结束时更新本文件与 `docs/STAGE_STATUS.md`，附：commit、修改文件、验证命令、测试结果、页面截图或 API 证据、遗留问题。

## 3. 下一步立即执行

- [x] 已完成 ULOO-002：停止能力虚报并补齐错误契约（Core `22 passed`，OpenAPI contract `3 passed`）。
- [ ] 接着完成 ULOO-003/004：workspace 边界与 CRUD 数据正确性。
- [ ] 同时等待团队 fork URL，用于关闭 ULOO-001 的可复现性阻断。
- [ ] P0 全绿后开始 Dify Agents/Teams 页面；不提前堆 Run UI、Memory UI 或 NATS。
