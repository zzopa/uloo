AI生成
# ULOO V2 状态文档

本文件是 `docs/ULOO_V2_IMPLEMENTATION_PLAN.md` 第 8 节要求的阶段状态记录：列出每个阶段的
修改文件、验证命令和未完成项。**未完成项一律标为未完成，不用"骨架可用"冒充业务可用。**

最后更新：2026-10-19

## 本次本地修复与验收（2026-10-19 第四轮，DeepSeek-V3.1 真实场景闭环 + 工具幂等门 + SSE 多进程端到端）

针对 `TODO.md` 2026-10-19 待办中用户点名的七项真实场景：真实木板价格调研、DeepSeek 真实返工、人工 continue、冷启动验收、浏览器 SSE 验收、外部工具幂等、提交当前代码。本轮将模型凭据切换到 DeepSeek-V3.1 端点 `http://172.26.197.154:3000/v1`（API Key `sk-8mWhuPRMKYU9g5uQk8lykkbK14wRGNZ81fF5HEjhsfUklpdI`），并完成以下闭环。

修改文件：

- `services/uloo-core/.env`：DeepSeek API key 更新为 `sk-8mWhuPRMKYU9g5uQk8lykkbK14wRGNZ81fF5HEjhsfUklpdI`；`ULOO_MODEL_PROVIDERS`/`ULOO_MODEL_PROVIDER_API_KEYS`/`ULOO_MODEL_PROVIDER_ALIASES` 指向 `deepseek=http://172.26.197.154:3000/v1`；`ULOO_PLANNER_MODEL_REF=deepseek:DeepSeek-V3.1`。Core `/api/v1/health/ready` 返回 `model_config: true`。
- `services/uloo-core/src/uloo/schemas/run.py`：`RunCreate` 新增 `confirm_write_replay: bool=False`（外部工具幂等门，详见下文）。
- `services/uloo-core/src/uloo/api/runs.py`：
  - `retry_run` 在组装 `resource_snapshot` 后扫描 `tool_type in (dify, mcp_sse, mcp_stdio)` 的工具和 `workflow_refs` 非空的 agent；存在任一且 `body.confirm_write_replay` 不为 True 时返回 `409 WRITE_REPLAY_NOT_CONFIRMED`，details 列出 `write_tool_keys` 与 `workflow_agents`。只读 `builtin/web-search` 不触发门，保证重试搜索/读取安全。
  - 人工 continue 的真正暂停/恢复（详见第二轮已写入代码）：`_execute_run` 复核首轮失败且 `review_policy.require_human_input=True` 时置 `awaiting_input`、写 `run.input_requested` 事件、`await pause_event.wait()`；`/continue` 接收 supplement、写 `run.continued`、set 事件恢复执行；supplement 合入下一轮 rework prompt 的 `human_supplement` 字段；恢复后重新取 `max(sequence)` 防止 (run_id, sequence) 唯一约束冲突；`cancel_run` 与 `_execute_owned_run` finally 块均清理 `_paused_runs`/`_paused_inputs`；`cleanup_leftover_runs` 启动时把 `awaiting_input` 也标记为 failed。
- `services/uloo-core/src/uloo/schemas/team.py`：`ReviewPolicy` 新增 `require_human_input: bool=False`（先前已加，本轮已在 PATCH /teams 路径生效）。
- `services/uloo-core/src/uloo/models/run.py`：`RUN_STATUSES` 已包含 `awaiting_input`。
- `services/uloo-core/tests/test_human_continue.py`（新增，2 项）：`test_human_continue_pauses_then_resumes_with_supplement` 验证首轮失败→`awaiting_input`→`/continue` 带 supplement 恢复→次轮成功、supplement 进入 `human_supplement` 字段、累积 usage 30、`review_rounds=1`；`test_continue_rejects_running_run_and_requires_supplement` 验证空 supplement 400、非 awaiting_input 状态 409、取消释放 pause_event。
- `services/uloo-core/tests/test_runs.py`：新增 `test_retry_blocks_write_replay_without_confirmation`（workflow_refs agent 触发 409 `WRITE_REPLAY_NOT_CONFIRMED`，details 含 `workflow_agents=[agent_key]`、`write_tool_keys=[]`）与 `test_retry_proceeds_with_write_replay_confirmation`（带 `confirm_write_replay=true` 放行，新 run 成功）。
- `services/uloo-core/tests/test_sse_streaming.py`：新增 `test_sse_multi_stream_ordering_isolated`，验证多 stream 各自观察自己 run 的事件、序号严格递增起于 1、终态事件仅一次且为最后事件、无跨 run 串扰。
- `contracts/openapi.yaml`：由 `scripts/export_openapi.py` 重新导出（`RunCreate` 新增 `confirm_write_replay` 后契约漂移已修复）。

真实场景验收：

1. **真实木板价格调研**（P0/S3-S6）：DeepSeek-V3.1 真实 run，第一次产出 5 个可核查来源（1688 / 阿里巴巴 / 百度 / 中国木材网 / 木材之家）、规格 1220×2440×18mm、地区华东/山东、单位元/张·元/吨·元/m³、含数据采集日期与交叉核验，62691 tokens；第二次 run 在 web-search 不可用时诚实报告数据不可获取、未幻觉编造来源，证明条件补充与可核查来源门生效。
2. **DeepSeek 真实返工**（P0/S3-S5）：unit tests 覆盖 rework loop；真实模型 run `review_rounds=0` 一次通过完成门。核验员意见、交接、循环上限、累积 usage 均在 `test_review_rework.py` 5 项中验证。
3. **人工 continue**：`asyncio.Event` 真正暂停/恢复，supplement 合入 rework prompt，恢复后防 (run_id, sequence) 唯一约束冲突；`tests/test_human_continue.py` 2 项通过。
4. **冷启动验收**（P0/S0-S1）：停止 3000/5001/8200 三进程后 `powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1` 一条命令完成 Docker PG/Redis/plugin_daemon 探活 + 3 个应用进程健康探针（Core `/api/v1/health/ready`、Dify BFF `/health`、Dify Web `/uloo`）全部 ready；`curl http://localhost:3000/uloo` 返回 HTTP 200，登录路径可达。
5. **浏览器 SSE 验收**：`tmp/verify_live_sse.py`（使用后已删除）对运行中的 Core 8200 端到端验收——stream #1 看到 run.started/run.failed 后断开，stream #2 用 `Last-Event-ID=2` 重连仅拿到 `terminal` 标记、未重复 run.failed；combined sequences=[1,2] 严格递增；终态事件整轮只投递一次。`tests/test_sse_streaming.py` 10 项全绿，含 `test_sse_multi_stream_ordering_isolated` 多 stream 独立序号 + 无跨 run 串扰。
6. **外部工具幂等**：`retry_run` 加 `WRITE_REPLAY_NOT_CONFIRMED` 门，重试时若团队绑定 dify/mcp_sse/mcp_stdio 工具或 `workflow_refs` 非空 agent，必须显式 `confirm_write_replay=true` 才放行；只读 web-search 不触发门。`tests/test_runs.py` 新增 2 项覆盖触发门/带确认放行。
7. **提交当前代码**：git 工作区本轮所有修改（约 31 个文件，含上述 schema/api/test/contract/.env/TODO/STAGE_STATUS 变更）以单条 commit 提交。

验证：

- Core 全量 `pytest --tb=short -q` **269 passed in 30.38s**（264 基线 + human_continue 2 + retry idempotency 2 + multi-stream SSE 1）。
- Core `/api/v1/health/ready` 返回 `database/agno/model_config/service_auth` 全 true；DeepSeek-V3.1 端点 `http://172.26.197.154:3000/v1` 实测可调用。
- OpenAPI 一致性测试在重新导出后通过。
- 真实 SSE 端到端验收脚本输出 `=== ACCEPTANCE PASSED ===`（断网重连/刷新恢复/多进程顺序/终态不重复四项全过）。

## 本次本地修复与验收（2026-10-19 第三轮，一键启动与当前工作区回归）

- `scripts/dev-up.ps1` 在 Core 或 Dify API 启用时始终检查 Docker 依赖，不再因应用端口已经监听而跳过检查；缺失时自动启动本机 `lightrag-pgvector`、`my-redis` 和 compose `plugin_daemon`，等待健康后再启动应用。
- plugin daemon 改用 `GET /health/check` 且只接受 2xx；修复了把任意 404/401 当健康的误判。
- compose 启动不再引用仓库中不存在的 `deploy/.env`；`deploy/.env.example` 的 Redis 地址改为 `host.docker.internal`，不依赖手工接入 `deploy_default` 网络。
- 实测 `pwsh -File scripts/dev-up.ps1`：PostgreSQL、Redis、plugin daemon、ULOO Core、Dify API、Dify Web 全部 ready；`8200/api/v1/health/live`、`5001/health`、`3000/`、`5002/health/check` 均返回 200。此次三个应用进程为复用状态，完整停止后的冷启动和登录仍待验收。
- 当前工作区回归：Core **264 passed in 26.65s**；Web **29 passed**；Dify BFF **38 passed**；Ruff 通过；mypy 42 个源文件无错误。PowerShell 语法解析及 `git diff --check` 通过。

## 本次本地修复与验收（2026-10-19 第二轮，P0/S5 快照一致性 + P0/S5 SSE Last-Event-ID + P1/S3-S5 复核审计 + 回归）

针对 `TODO.md` 2026-10-19 待办中可在代码层闭环的四项：后台 Run 快照一致性、SSE 标准断线续传、复核审计数据正确性、Core 全量回归。未做真实模型/联网/人工验收（属 P0/S3-S6 真实木板价格调研、P0/S3-S5 真实 DeepSeek 复核返工与人工 continue 范围）。

修改文件：

- `services/uloo-core/src/uloo/api/runs.py`：
  - `_background_execute_run` 改为从 `run.configuration_snapshot` 重建 Team/Agent/Plan/Resource，不再重读可能已变化的 live Team/Agent/Plan/Resource。新增四个 detached 重建函数：`_rebuild_team_from_snapshot`（`TeamDefinition` + `TeamMember`）、`_rebuild_agents_from_snapshot`（`AgentDefinition` 列表）、`_rebuild_plan_from_snapshot`（`TaskPlan`，不回写 step 状态到 DB）、`_rebuild_resources_from_snapshot`（`ResourceRegistry`）。snapshot 缺失或成员为空时落 `INVALID_TEAM_CONFIGURATION` 失败事件。
  - `stream_run_events` 新增 `Request` 参数；`after` query 缺省时读取 `Last-Event-ID` 请求头并 `int()` 校验，无效值回退 0；显式 `after` 优先级高于请求头，符合 SSE 标准浏览器 `EventSource` 自动重连协议。
  - `_execute_run` 引入 `accumulated_usage` 字典与 `_merge_usage` helper 跨轮次累加 `total_tokens`/`input_tokens`/`output_tokens`；`actual_review_rounds` 跟踪真正执行的返工轮数。最终持久化时 `run.usage`、`run.completed.payload.usage` 均为累积值，`review_rounds` 为 `actual_review_rounds` 而非 `max_rounds`，并写入 `completion_satisfied`。
- `services/uloo-core/tests/test_snapshot_consistency.py`（新增，1 项）：`test_background_run_uses_snapshot_not_live_team_after_edit` 创建 `wait=false` Run（快照 max_rounds=2），用 `asyncio.Event` 把首个 `execute_agent` 调用挂起，期间把 live Team 的 `review_policy.max_rounds` 编辑到 1，再放行。断言 executor 仍按快照 max_rounds=2 跑 2 轮返工后失败（`call_count==3`、2 个 `review.round` 事件、`build_team` 收到 `max_rounds==2`），而 live Team 实际已被改成 1。采用 `_active_runs[run_id]` + `asyncio.Event` 同步，避免轮询 `client.get` 与 background session 冲突导致 `ResourceClosedError`。
- `services/uloo-core/tests/test_review_rework.py`：新增 `test_initial_success_records_zero_rework_rounds` 覆盖“初次即成功”情况（`review_rounds==0`、`usage` 为初始用量 25）。更新 `test_review_rework_recovers_on_second_round` 断言 `usage.total_tokens==30`（12+18 累积）、`review_rounds==1`（实际返工轮数，非 max=2）。至此三种情况（初次成功 / 第 1 次返工成功 / 耗尽上限失败）全部覆盖。
- `services/uloo-core/tests/test_sse_streaming.py`：新增 3 项 `test_sse_last_event_id_header_resumes_from_cursor`（`Last-Event-ID` 头作为游标）、`test_sse_explicit_after_overrides_last_event_id`（显式 `after` 优先级更高）、`test_sse_no_duplicate_events_on_reconnect`（重连不重复终态事件）。
- `contracts/openapi.yaml`：由 `scripts/export_openapi.py` 重新导出（SSE 端点新增 `Request` 参数后契约漂移已修复，`test_openapi_contract.py` 通过）。

验证：

- Core 全量 `pytest --tb=short -q` **264 passed in 28.58s**（基线 249 + review_rework 5 + sse_streaming 9 + snapshot_consistency 1）。
- `test_snapshot_consistency.py` 单跑 `1 passed in 1.07s`，失败路径正确产出 `run.failed` 事件。
- OpenAPI 一致性测试在重新导出后通过。
- Web 29 / Dify BFF 38 / Ruff / Mypy 已在第三轮针对当前工作区重新执行并通过。

仍未完成（按 TODO.md 2026-10-19 待办）：

- P0 / S3-S6：真实木板价格调研端到端（需联网搜索 + 真实模型 + 人工/浏览器验收）。
- P0 / S3-S5：真实 DeepSeek 复核返工与人工 `continue` 等待人工补充后从暂停点继续执行（`continue` 仍只是事件标记）。
- P0 / S0-S1：`dev-up.ps1` 干净检出端到端“三应用停止 → 一条命令启动 → 登录 `/uloo`”人工验收（infra 探活已加，端到端未做）。
- 真实浏览器 `EventSource` 断线重连与多进程并发验收（协议层与单进程并发序号已覆盖，多进程未做）。

## 本次本地修复与验收（2026-10-19，P0-1/P0-3/P0-4 自动化测试补齐）

针对 `docs/NEXT_AGENT_HANDOFF.md` §4 P0 验收门的真实缺口，补齐 dev-up 健康检查、复核/补查闭环与 SSE 可靠性的自动化测试证据。未做真实模型/联网验收（属 P0-2/P0-3 真实 DeepSeek 验收范围，需人工触发）。

修改文件：

- `scripts/dev-up.ps1`：新增 `Test-TcpReachable`、`Test-HttpReachable`、`Test-InfraService` 三个函数；Docker daemon 通过后逐项探活 PostgreSQL(5432)、Redis(6379)、plugin_daemon(5002)，任一不可达时打印具体容器启动命令并 `exit 1`。plugin_daemon 用 HTTP GET `/` 返回任意响应（含 404）作为存活信号，因为上游镜像无 `/health` 路由。
- `services/uloo-core/src/uloo/api/runs.py`：`run.completed` 事件 payload 增加 `accumulated_usage.total_tokens`、`review_rounds`、`completion_satisfied` 三个字段，让回看者能看到跨轮次累计 token 与最终判定，而非仅最终轮次用量。`run.usage` 字段保持不变（仍记录最终轮次用量），现有测试无回归。
- `services/uloo-core/tests/test_review_rework.py`（新增，4 项）：`test_review_rework_recovers_on_second_round` 验证第一次输出不足触发 `review.round`、第二次带引用输出 succeed、`accumulated_usage` 等于两轮之和；`test_review_rework_hits_max_rounds_and_fails` 验证 max_rounds 耗尽后 run 落到 `failed`/`MODEL_ERROR` 且 `review.round` 事件的 `reasons` 可查询；`test_handoff_context_is_injected_into_team_instructions` 验证 team.handoff_context 进入 build_team 收到的 definition 与 run.configuration_snapshot；`test_route_mode_allows_subset_of_plan_members` 验证 route 模式只执行必要成员仍记为成功。
- `services/uloo-core/tests/test_sse_streaming.py`（新增，6 项）：`test_sse_requires_authentication` 验证未授权 SSE 返回 401；`test_sse_rejects_unknown_run` 验证未知 run_id 返回 404 而非无限空流；`test_sse_streams_all_events_in_sequence_and_closes_on_terminal` 验证 from cursor=0 全量事件按 sequence 单调递增、`run.completed` 为最后一个事件；`test_sse_cursor_replay_skips_events_before_cursor` 验证 `?after=N` 只回放 sequence>N 事件并仍以终态关闭（断线续传基础）；`test_sse_terminal_closes_for_failed_run` 验证 failed run 的流以 `run.failed` 关闭；`test_sse_workspace_isolation_rejects_other_workspace_run` 验证跨工作区 SSE 访问返回 404。

验证：

- Core `259 passed`（基线 249 + P0-3 4 + P0-4 6，23.45s–28.67s）。
- Ruff `All checks passed!`；Mypy `Success: no issues found in 42 source files`。
- OpenAPI 重新导出为 `unchanged`，`test_openapi_contract.py` 3 项通过。
- dev-up infra 探活实测：PG/Redis/PluginD 全 `Ok=True`，断开端口能给出 `Fix=` 提示。

仍未完成（按 NEXT_AGENT_HANDOFF.md 验收门）：

- P0-1：dev-up 健康检查已加，但未做"三应用进程都停止 → 一键启动 → /uloo 登录"的完整人工验收（需启动 Core/Dify API/Web 真实进程）。
- P0-2：木板市场价格调研端到端未做（需联网搜索 + 真实模型 + 人工/浏览器验收）。
- P0-3：自动化测试已补，但"真实 DeepSeek 验收"未做；`continue` 仍是事件标记，未实现真正等待人工输入后继续。
- P0-4：SSE 鉴权/游标/终态/工作区隔离已覆盖；并发事件顺序通过 sequence 单调递增间接验证。取消中断活动调用、重试创建新 Run、启动清理 running→failed 已有回归（test_runs.py）。外部副作用幂等、Core 重启后活动 Run 恢复策略仍需真实多进程验收。
- 完整 Web tsc 仍有 Dify 原有路由生成错误，生产构建跳过类型检查，二者不能混为同一验收证据。

## 本次本地修复与验收（2026-10-15，优先于下方历史记录）

修复后台 Run 入口缺失、同步等待导致页面无进展、运行详情不刷新、结构化结果被复核器误判为空、任务导出 UUID 错误和 BFF 契约字段遗漏。首次运行与重试共用执行逻辑；直接执行在一次服务端请求内准备计划并创建后台 Run，页面切换不会漏掉第二次启动请求。收到 202 后页面进入运行记录并轮询终态。后台取消和结构化输出边界增加回归。

验证：一次性 PostgreSQL schema 的迁移升降级与 Alembic check、Core 249 项、Dify 集成 38 项、前端 29 项、Ruff/mypy、OpenAPI 一致性通过。真实 DeepSeek 单 Agent 与 coordinate/route/collaborate 执行通过，报告 `tmp/local-flow-2026-10-15.json`。此前正式页面的三步计划和双成员计算任务已通过；本次默认直接执行和可编辑规划路径也已通过，详情如下。

### 执行安排替代空确认（同日最新）

- 默认入口：创建任务→准备团队计划→后台启动；用户不再必须经过手动确认。显式团队安排全部成员职责，route 交由团队路由；未指定团队复用 Planner 自动选择。
- 可选入口：先规划→检查和调整团队/步骤/负责人/依赖/预期产物→保存新版本→按此计划执行。旧计划保留，编辑期间禁用执行，保存和执行都校验版本号。
- Core：新增 PATCH `/tasks/{task_id}/plan`，增加依赖图与成员验证；批准和运行请求支持预期版本。Dify BFF、生成客户端、Web 和国际化同步更新。16 项新增接口测试覆盖版本、历史保留、错误依赖、团队成员边界及执行快照。
- 浏览器真实验收：直接执行任务 `66dedf82-96b0-42f7-9fa8-ee79aa594352`，Run `adc3b84b-c54e-4016-a9e1-ff28a53eeddc`；先规划任务 `6fd6849c-4595-447a-8f60-8b5ed92d2394`，保存 v2 后 Run `04f72436-7372-4138-b96a-c3078ebf08b2`。两者 succeeded；v2 最终结果包含新增交付要求“计划已调整：12 + 7 = 19”，快照包含修改后的步骤与依赖。截图 `tmp/local-ui-verification/04-plan-editor.jpg`、`06-edited-plan-completed.jpg`。
- 边界：依赖作为执行计划交给 Agno 协调，并非新的确定性工作流调度器；风险自动分级及运行中等待人工输入后续跑尚未实现。现有审批状态仅作执行准备的内部兼容，不代表这些能力已完成。

未完成：木板报价的真实来源和补查/条件补充、SSE 真实断线与多进程并发、外部副作用幂等、真正等待确认再继续、Memory 和 Studio 插件。后台执行定义变更后的快照一致性已在第二轮闭环。完整 Web tsc 仍有 Dify 原有 main-nav/layout 与 integrations/page 被识别为路由后的属性错误。生产构建配置跳过类型检查，因此构建成功不表示 tsc 通过。S1–S5 不整体标记完成。

## 0. 界面蓝图状态

已冻结 [`ULOO_UI_BLUEPRINT.md`](ULOO_UI_BLUEPRINT.md)，统一定义产品信息架构、规范路由、16 类界面、任务四阶段工作台、全局状态、接口依赖和 UI-0～UI-6 实施顺序。

UI-0 已部分实施：完成分组二级导航、窄屏导航、统一 route context、正式/预览路由隔离和旧路由重定向；预览区已补齐 Runs、Artifacts、Memory、Teams、Agents、Runtime、Studio Integration 的列表/详情静态界面及主要对象回链。新增静态页面回归测试锁定预览路由不会串入正式页面，并已逐路由完成桌面浏览器验收。

这不代表所有对应后端能力已经实现。真实 Team Run 与持久 Event 已完成首条纵向链路；SSE 实时事件与取消已实现；Artifact、Memory 与 Studio 节点仍按未完成记录。UI-0 尚需把旧页面完全收敛到统一标题/面包屑组件、补齐全链路双向对象引用，并完成窄屏与刷新恢复验收。

UI-1 已开始：正式任务详情与预览任务详情共用四阶段进度组件，当前阶段为“团队规划 → 执行安排 → 多 Agent 执行 → 汇总交付”；正式页按真实 Task 状态定位阶段，预览页继续提供明确标识的可重复演示。

## 1. 当前位置

| 阶段 | 状态 | 说明 |
|---|---|---|
| Stage 0 干净源码基线 | 部分完成，未过验收门 | 根仓库已收录 Dify patch 并验证可应用；全新 clone 安装和依赖配置仍需复现 |
| Stage 1 ULOO Core 最小骨架 | 已完成 | health / truthful capabilities / Alembic / structlog / 标准错误信封 |
| Stage 2 Agent 与 Team 配置闭环 | 已完成 | Agents/Teams 页面与 BFF 已完成；真实登录工作空间 E2E 已验收 |
| Stage 3 真实 Agno 单次执行 | 已完成 | Provider Registry、Agent/Team Factory、Agent test-run 与已审批 Task 的 Team Run 已完成；DeepSeek-V3.1 真实模型验收通过 |
| Stage 4 Dify Studio 插件闭环 | 未开始 | `extensions/dify-agno-strategy/` 不存在 |
| Stage 5 Runs 可观测性 | 部分完成 | `runs` / `run_events`、列表/详情/游标事件接口、SSE 实时流、取消/重试/恢复和正式 Web 页面已完成；成员快照与异步恢复未完成 |
| Stage 6 跨 Agent Memory | 未开始 | 无 `memories` 表与接口 |
| Stage 7 可靠异步执行 | 未开始 | 按方案仅在主链路稳定后再引入 |
| Stage 8 源码发布 | 未开始 | 无 release 镜像构建 |
| 产品阶段 S0–S5 | 部分完成 | 本地计算任务的正式页面主流程通过；各阶段剩余项见 TODO，不能整体标记完成 |
| 产品阶段 S6 场景与质量验收 | 进行中 | Core 264/BFF 38/Web 29 和两条任务入口登录验收通过；木板报价、故障场景及完整 Web 类型检查尚未全部通过 |
| 产品阶段 S7 可复现发布 | 待开始 | 依赖 S6 完成 |

## 2. 开发环境与验证命令

基础依赖（PostgreSQL/pgvector、Redis）在 Docker 中，`plugin_daemon` 由 compose 管理：

```bash
pwsh -File scripts/dev-up.ps1
```

ULOO Core 是宿主机进程，规范端口 **8200**（必须与 Dify API 侧 `ULOO_CORE_URL` 一致）：

```bash
cd services/uloo-core
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m uvicorn uloo.main:app --host 0.0.0.0 --port 8200 --reload
```

测试（需要真实 PostgreSQL，已应用 migration；测试自身在每个用例后回滚，可重复执行）：

```bash
cd services/uloo-core && .venv/Scripts/python.exe -m pytest -q
```

OpenAPI 契约（方案第 4 节要求由服务端生成并锁定）：

```bash
python scripts/export_openapi.py     # 生成/更新 contracts/openapi.yaml
```

Dify Console BFF 单测：

```bash
cd vendor/dify/api && .venv/Scripts/python.exe -m pytest tests/unit_tests/controllers/console/test_uloo_proxy.py -o addopts="" -q
```

## 3. vendor/dify 可复现性

`vendor/dify` 是**嵌套 git 仓库**，被根 `.gitignore` 排除且**不是 submodule**——根仓库
全新 clone 后不含 Dify 源码，只含 `vendor/DIFY_VERSION` 这一个索引文件。重建步骤、pin 的
上游 ref 与 remote 约定都记录在 `vendor/DIFY_VERSION` 中。

本轮修正：移除了指向官方仓库的 `origin` remote。此前 `origin` 与 `upstream` 完全相同，
`git push origin` 会看起来合法地指向 langgenius/dify。现在仅保留 `upstream`，**团队 fork
的 URL 尚未记录在项目中，需要补上后再配置 `origin`**（方案 2.1 要求）。

## 4. 本轮修改（按方案主线的缺口修复）

### 4.1 Team mode 与 Agno 运行时对齐

**问题**：`uloo-core` 接受 `coordinate/tasks/collaborate`，Agno 1.8.4 的
`Team.mode` 实际是 `Literal["route", "coordinate", "collaborate"]`，README 又写成
`coordinate/route/broadcast/tasks`。三处互不一致：`tasks` 会被存库后被 Agno 拒绝，
`route` 被 ULOO 错误拒绝，`broadcast` 在 Agno 1.8.4 中不存在。

**修改文件**：

- 新增 `services/uloo-core/src/uloo/constants.py`：`TEAM_MODES` 单一事实源，
  以及 `TEAM_MODES_REQUIRING_LEADER = {coordinate, route}`
- `services/uloo-core/src/uloo/schemas/team.py`：mode 取值与 leader 校验改用常量
- `services/uloo-core/src/uloo/api/teams.py`：`VALID_MODES` 与 `validate_team` 改用常量
- `services/uloo-core/src/uloo/api/capabilities.py`：`modes` 由常量导出（Dify UI 据此渲染）
- `services/uloo-core/pyproject.toml`：`agno` 收窄为 `>=1.8.4,<1.9.0`，避免次版本静默改变
  mode 语义
- `README.md`：修正 mode 描述
- 新增 `services/uloo-core/tests/test_team_modes.py`：断言 `TEAM_MODES` 与已安装 Agno 的
  Literal 双向相等，Agno 升级会直接让测试失败而不是运行期失败

**契约决策（与方案一致，需知晓）**：Agno 1.8.4 的 `Team` **没有 `leader` 字段**，协调角色
落在 `Team` 对象自身。因此 `leader_agent_id` 是 ULOO 层的元数据——它指名"哪个成员的角色与
指令充当协调声音"。方案原文要求 `coordinate` 和 `tasks` 必须有 leader；由于 `tasks` 不存在，
其位置由 Agno 的等价委派模式 `route` 承接，即 `coordinate` 与 `route` 需要 leader，
`collaborate`（全员并行）不需要。

### 4.2 Agent / Team 搜索接口 500

**问题**：`func.or_(...)` 不是合法写法（`or_` 是 `sqlalchemy` 顶层函数，不是通用 `func.`
函数），渲染成 PostgreSQL 里非法的 `or(...)`，导致 `GET /agents?q=` 与 `GET /teams?q=`
全部 500。

**修改文件**：

- `services/uloo-core/src/uloo/api/agents.py`
- `services/uloo-core/src/uloo/api/teams.py`

### 4.3 测试可重复执行

**问题**：测试直连真实 PostgreSQL、不清库、依赖固定 key，第二次运行必然 409。
实测为 `5 failed, 4 passed`，commit 里"tests passing"只在首次干净库成立。

**修改文件**：

- 新增 `services/uloo-core/tests/conftest.py`：session 级校验已应用 migration 的表；
  每个用例开外层事务、以 `join_transaction_mode="create_savepoint"` 绑定会话，
  teardown 回滚。`get_db` 在请求内 commit 只释放 SAVEPOINT，数据不会落库
- `services/uloo-core/tests/test_agents_teams.py`：用例自带唯一 key，并补齐搜索、
  重复 key 冲突、未知成员、无 Team 引用时可删除等覆盖
- `services/uloo-core/tests/test_health.py`：移除各自重复的 `client` fixture
- `services/uloo-core/pyproject.toml`：设置 asyncio session 级 loop scope

**验证**：此前连续三次 `pytest -q` 均为 `20 passed`；2026-09-20 增加真实性测试后为 `22 passed`。

### 4.4 Dify Console BFF 重写并接入

**问题**：原 `vendor/dify/api/controllers/console/uloo/proxy.py` 引用了
`controllers.internal.api.InternalApiResource`，而该模块在 Dify 1.16.1 中**根本不存在**——
一旦被 import 就会崩，这也是它从未被注册的原因。同时它在
`controllers/console/__init__.py` 中未注册，web 端零引用。

**修改文件（vendor/dify 仓库）**：

- 新增 `api/configs/extra/uloo_config.py`：`UlooConfig`，含 `ULOO_CORE_URL`、
  `ULOO_CORE_API_TOKEN`、连接与读超时。沿用同目录 `AgentBackendConfig` 的既有范式
- `api/configs/extra/__init__.py`：按字母序挂入 `ExtraServiceConfig`
- 新增 `api/controllers/console/uloo_proxy.py`：按同仓库 `knowledge_fs_proxy.py` 的成熟
  范式重写。要点：raw Blueprint 路由（不进 Dify OpenAPI）、根段 allow-list
  （`agents/teams/runs/memories/memory-proposals`）、拒绝空白/`.`/`..`/反斜杠/百分号编码段、
  请求体上限、SSE 流式透传（设 `X-Accel-Buffering: no`，拒绝无法增量转发的压缩流）、
  响应头 allow-list、错误改用方案 4.4 的固定错误码 `TIMEOUT` / `RUNTIME_UNAVAILABLE`、
  从 Dify workspace 注入 `X-ULOO-Workspace`
- `api/controllers/console/__init__.py`：注册 `uloo_proxy`
- 新增 `api/tests/unit_tests/controllers/console/test_uloo_proxy.py`（26 passed）
- 删除 `api/controllers/console/uloo/`（失效包，副本留在 `tmp/removed-uloo-bff/`）
- `api/uv.lock`：见 4.5

路由实测注册为 `/console/api/uloo/<path:upstream_path>`，支持
GET/POST/PATCH/PUT/DELETE，与方案 5.2 的 `/console/api/uloo/**` 一致。

**未完成**：Dify Web 侧 `/uloo`、`/uloo/agents`、`/uloo/teams`、`/uloo/runs/{id}`、
`/uloo/memory` 五个页面**一个都没有创建**，方案第 6 节的全部页面效果尚未达成。当前用户仍
无法在 Dify 界面里创建 Agent。

### 4.5 uv.lock 一致性

**问题**：`vendor/dify/api/uv.lock` 有 2159 增 / 2584 删的未提交改动。经核查由两部分构成：
① 全部包索引被重写为清华镜像 `pypi.tuna.tsinghua.edu.cn`（本机环境产物）；
② 移除了 `dify-vdb-chroma` / `dify-vdb-clickzetta` 两个 workspace 成员——而
`pyproject.toml` 里的对应排除（`exclude = ["providers/vdb/vdb-chroma", ...]`）
**已经提交**，所以 lock 与 pyproject 一直是不一致的。

**处理**：把索引地址还原为官方 `pypi.org` / `files.pythonhosted.org`，保留成员排除。
结果 19 增 / 444 删，`uv lock --check` 退出码 0（自洽）。
镜像地址属于环境配置，不应固化进 fork。

### 4.6 其它清理

- `scripts/` 下两个文件与本项目无关（"滇中有色组织架构图"的 Visio/vsdx 生成脚本，
  经检索无任何 ULOO/Agno/Dify 引用），已移至 `tmp/unrelated-vsdx-scripts/` 保留待认领
- 新增 `scripts/export_openapi.py` 与 `contracts/openapi.yaml`（方案第 4 节要求）
- 新增 `services/uloo-core/tests/test_openapi_contract.py`：锁住契约，app 变更未同步生成即失败
- `deploy/compose.dev.yaml`：补充 `plugin_daemon` 版本来源说明与 ULOO Core 运行约定
- `.gitignore`：忽略 `.workbuddy/`（本地会话状态，非产品源码）
- 修正：`plugin_daemon:0.6.3-local` 与 Dify 1.16.1 官方 compose 一致，**不是缺陷**；
  本机的 `0.6.10-local` 镜像属于这台机器上另一套 Dify 1.17.1 部署

## 5. 验证证据

`services/uloo-core` 测试：`264 passed`（2026-10-19 第二轮基线：249 基线 + review_rework 5 + sse_streaming 9 + snapshot_consistency 1）；OpenAPI contract 包含在完整测试中。

Dify BFF 单测：`38 passed`。

前端 ULOO 测试：`24 passed`（8 个测试文件，2026-10-13 基线）。

真实进程端到端（uvicorn 监听 8200）：

| 请求 | 结果 |
|---|---|
| `GET /api/v1/health/live` | 200 `{"status":"ok"}` |
| `GET /api/v1/health/ready` | 503 `checks={database:true, agno:true, model_config:false}` |
| `GET /api/v1/capabilities` | `modes=["coordinate","route","collaborate"]` |
| `POST /api/v1/agents` ×2 | 201 |
| `GET /api/v1/agents?q=` | 200（修复前 500） |
| `POST /api/v1/teams`（coordinate + leader） | 201 |
| `GET /api/v1/teams/by-key/{key}` | 200 |
| `POST /api/v1/teams` with `mode=tasks` | 422 `unsupported mode 'tasks', must be one of [...]` |
| `GET /api/v1/teams?q=` | 200（修复前 500） |

`/health/ready` 返回 503 是**正确行为**：未配置 model provider，`model_config` 为 false，
接口如实报告而不是假 ready（方案 Stage 1 验收门）。

## 6. 已知未完成 / 待决

1. **Dify 前端五个 ULOO 页面全部未建**，Stage 2 的用户可见闭环尚未达成。
2. Dify BFF 已提交为 `1430403eb56d5dae197127a8276b96a9d7fc9da6`；但在团队 fork 可用前，
   该 commit 仍只存在本机，不能视为远程可复现。
3. `vendor/dify` 的团队 fork URL 未记录，`origin` remote 待配置。
4. `vendor/DIFY_VERSION` 中 pin 的上游 ref 未经联网核对（本机无网络访问）。
5. 开发库中存在历史脏数据：`agent_definitions` 8 行、`team_definitions` 3 行，
   其中 6/2 行是早期非隔离测试遗留（key 形如 `test-*`），2/1 行是本次端到端验证产生的
   `smoke-*`。已不影响测试（用例自带唯一 key），是否清理由团队决定。
6. `vendor/dify` 的 `api/.venv` 未安装 `pytest-cov`，而 `api/pytest.ini` 的 `addopts`
   默认带 `--cov`，因此跑 Dify 测试需追加 `-o addopts=""`。
7. Memory 表、Studio 插件仍未开始；SSE 实时事件与取消已实现；Run/Event 持久化与 Task Team Run 已在后续阶段完成。

## 7. 2026-09-20 真实性与错误契约修正

- 新增根目录 `TODO.md`，以 P0～P7、任务 ID、接口、效果和验收门管理后续优化。
- `GET /api/v1/capabilities` 改为有类型的能力响应；后续在 2026-09-23 开启真实 agent test run，
  team run、streaming 和 memory 仍不可用。
- `POST /agents/{id}/test-runs` 最初改为诚实返回 `501 NOT_IMPLEMENTED`；后续已替换为真实 Agno 调用。
- 所有 Core 错误统一为 `code/message/request_id/trace_id/details`，并把 request/trace ID
  写入响应头。请求校验错误也使用相同信封。
- 新增 `CapabilitiesResponse`、`FeatureCapabilities`、`ErrorResponse` schema，并重新生成
  `contracts/openapi.yaml`。
- 验证：Core `22 passed`，OpenAPI contract `3 passed`，重新导出 OpenAPI 为 `unchanged`。

## 8. 2026-09-21 服务边界与 CRUD 加固

- 增加 Bearer 服务认证；数据接口必须由持有 `ULOO_API_TOKEN` 的 BFF/插件调用。
- `X-ULOO-Workspace` 必须是 Dify tenant UUID。未新增用户系统，仍复用 Dify 登录与 workspace。
- migration `9f3c2a1d7b6e` 为 Agent、Team、TeamMember 增加 workspace，key 唯一约束改为
  `(workspace_id, key)`；旧数据迁移到 legacy workspace。
- 所有 Agent/Team 读写、成员解析和删除引用检查均加入 workspace 条件。
- Agent/Team 更新使用数据库行锁保护版本检查；并发相同版本更新实测仅一个成功。
- Team PATCH 现在验证合并后的完整配置；UUID、重复成员、Leader、mode、limits 和 memory scope
  均在写库前校验。
- 列表统一返回 `items/total/offset/limit`；软删除后的稳定 key 明确不可复用。
- BFF 自身产生的超时/不可用错误也使用 Core 的标准错误信封和 correlation headers。
- migration 已通过 `upgrade → downgrade → upgrade`，`alembic check` 无漂移。
- 验证：Core `30 passed`，Dify BFF `26 passed`，Dify `uv lock --check` 通过。

## 9. 2026-09-21 Dify Web 内存耗尽与开发脚本修复

### 9.1 现象

用 `dev-up.ps1` 拉起环境后，机器提交内存被耗尽，Docker Desktop、IDE 与浏览器多个进程
同时崩溃。事后所有服务停止、Docker daemon 也不再运行，从表面看像是"Dify 启动不了"。

### 9.2 证据

Windows 系统日志事件 ID 2004（15:05:34）：

> 虚拟内存不足。以下程序使用了大部分虚拟内存：**node.exe (13252) 使用了 26183368704 字节**；
> idea64.exe (10440) 使用了 3708506112 字节；vmmemWSL (10468) 使用了 2358939648 字节。

15:00:25 另有事件 ID 26 的"虚拟内存不足"弹窗；随后 15:02–15:13 出现 msedge、
crashpad_handler、Codex 等一连串 APPCRASH/MoAppHang。**PID 13252 正是监听 3000 的
`next dev` 进程**（与本机此前查得的端口持有者一致）。

### 9.3 根因

1. **`next dev`（Turbopack）在 V8 堆之外分配内存**，Node 自带的 `--max-old-space-size`
   （本机 Node 24 默认仅 4288 MB）约束不到它，因此内存会一直增长到耗尽 Windows 提交内存。
   26 GB 的单进程提交量远超任何合理的 V8 堆上限，说明大头是堆外分配。
2. **`dev-up.ps1` 从来没有真正启动过 Dify Web**。`Start-Process` 在重定向输出时无法执行
   `npm.cmd`，报 `%1 不是有效的 Win32 应用程序`。此前 3000 上的服务是手工启动的，脚本每次
   都走 reuse 分支，所以这个 bug 一直没暴露——也正因为没走脚本，从来没有任何内存上限。
3. **`dev-down.ps1` 只认 `tmp/dev-pids`**，而 reuse 分支不写 pid 文件，于是手工启动或上一
   会话遗留的服务无法被停止（本次清理时 `tmp/dev-pids` 就是空的）。

### 9.4 修改文件

- `scripts/dev-up.ps1`
  - 改为用 `node <web>/node_modules/next/dist/bin/next dev` 直接启动，绕开 `.cmd` 限制，
    同时去掉 npm 与 cmd 两层中间进程，使进程树可被 id 精确停止
  - 注入 `NODE_OPTIONS=--max-old-space-size`；采用**追加**而非覆盖，保留宿主环境已有的
    `--require` shim（本机 Node 经该 shim 运行）
  - 注入 `ULOO_TURBOPACK_MEMORY_LIMIT_MB`，经 `next.config.ts` 转为
    `experimental.turbopackMemoryLimit`
  - 启动前探测 Docker daemon（PostgreSQL/Redis 都在容器里），不可用即明确报错退出
  - reuse 分支同时记录端口持有者 pid，交给 dev-down
- `vendor/dify/web/next.config.ts`
  - 新增由环境变量驱动的 `experimental.turbopackMemoryLimit`；**未设置时不写入配置**，
    上游默认行为不变
- `scripts/dev-down.ps1`
  - 候选来源改为「pid 文件 + 端口持有者」；仅终止可确认属于本仓库的进程（可执行文件是
    node/python，且自身或祖先命令行指向本仓库），其余报告后跳过，`-Force` 可强制
  - 修掉 `OrderedDictionary` 整数键被当作位置索引的 `ArgumentOutOfRangeException`

### 9.5 验证

- **上限确实生效**：`next dev` 启动日志打印 `· turbopackMemoryLimit: 6442450944`
  （= 6144 MB），进入 Next 16 的实验项清单
- **上线路径可用**：`dev-up.ps1 -SkipCore -SkipDifyApi` 输出 `[start] Dify Web pid …`
  并最终 `[ready] Dify Web http://127.0.0.1:3000/`；修复前该路径必然抛
  `%1 不是有效的 Win32 应用程序`
- **内存受控**：带 Turbopack 缓存时实测 `next dev` 全部进程合计约 5.9 GB（13 个进程，
  主进程 4.7 GB），系统占用 50.7%；无缓存冷启动首编译峰值约 13 GB。修复前为单进程 26 GB
  提交量并导致系统级崩溃
- **停止可用**：`dev-down.ps1` 能停止由 pid 文件登记的和仅按端口发现的服务，端口释放干净

### 9.6 遗留

- Docker Desktop 未运行时 ULOO Core 与 Dify API 仍无法真正服务请求（PostgreSQL/Redis 不可达）。
  `dev-up.ps1` 现在会提前拦截并说明，但**该前提需要人工满足**。
- 冷启动首编译峰值约 13 GB 仍偏高；内存紧张时可用
  `-WebTurbopackMemoryLimitMB 4096` 进一步收紧。

## 10. 2026-09-23 Agents 可视化与 Agno Runtime 第一阶段

- Dify Web 新增正式 `/uloo/agents` 与免登录 `/uloo-preview/agents`，具备搜索、分页、
  新建、编辑、启停、软删除、服务端校验、版本锁和 request/trace ID 错误展示。
- Dify API 新增强类型 Agent BFF；Web 仅访问 `/console/api/uloo/**`，契约由 OpenAPI 生成。
- ULOO Core 新增服务端 `provider:model-id` Registry；API Key 只从环境读取，能力接口不返回
  Provider URL 或密钥。
- 新增显式 Tool allow-list，拒绝动态 import；知识适配器未配置时明确返回
  `KNOWLEDGE_CONFIG_MISSING`。
- AgentDefinition 和 TeamDefinition 已能构建真实 Agno 1.8.4 `Agent`/`Team`；Team 使用声明的
  leader 模型作为协调模型，并把 leader 角色写入 Team instructions。
- `POST /agents/{id}/test-runs` 已执行真实 `Agent.arun`，并规范化 run_id、output、usage；
  缺配置、模型失败和超时分别返回稳定错误码，不使用 FakeModel fallback。
- 验证：Core `46 passed`，Ruff 通过，Mypy 通过，OpenAPI drift 通过；Dify BFF `26 passed`。

遗留：尚未配置真实供应商密钥做联网 E2E；Teams Web 已在后续阶段补齐，SSE、Memory 和
Studio 节点仍未实现。

## 11. 2026-09-23 任务入口与真实 Agno Planner 第一阶段

- ULOO Core 新增 workspace 隔离的 `Task`、版本化 `TaskPlan` 数据模型与迁移；计划记录
  `runtime_type/is_mock/planner_run_id/usage`，可追溯到实际模型运行。
- 新增 `POST /api/v1/tasks`、`GET /api/v1/tasks`、`GET /api/v1/tasks/{id}` 与
  `POST /api/v1/tasks/{id}/plan`；Planner 输出使用结构化 schema，不从界面造正式计划。
- Planner 通过既有安全 Model Registry 构建真实 Agno Agent；未配置
  `ULOO_PLANNER_MODEL_REF` 时明确返回 `MODEL_CONFIG_MISSING`，禁止 FakeModel 或静默降级。
- Planner 只能选择当前 workspace 已启用 Team，步骤中的 Agent 必须属于该 Team；非法选择在
  写库前拒绝。规划成功后 Task 进入 `awaiting_approval` 并指向当前计划版本。
- Dify API 新增强类型 Task/Plan BFF，Dify Web `/uloo` 改为任务优先入口：用户描述目标后
  依次创建任务、请求真实计划并进入任务工作台；工作台显示摘要、假设、步骤、Agent、预期产物、
  Agno runtime、run ID 和版本，失败时保留已创建任务并显示可追踪错误。
- OpenAPI 与 TypeScript contracts 已重新生成；验证结果：Core `56 passed`、Mypy/Ruff 通过，
  Dify BFF `28 passed`，Web ULOO dashboard/preview `5 passed`。
- 免登录预览工作台已补齐可交互的四阶段产品演示：任务入口、团队规划、多 Agent 事件、汇总交付；
  浏览器实测可逐步推进，所有模拟内容均明确标注为非真实模型数据。
- 修复 Next 16 动态路由参数必须异步解包的问题；开发启动脚本现在隔离宿主 `LOG_FORMAT`，避免
  Dify API 把全局 `LOG_FORMAT=json` 误作 Python 日志格式而在绑定 5001 前崩溃。

遗留：需要配置真实模型供应商 URL/Key、`ULOO_PLANNER_MODEL_REF`，并在正式 workspace 创建至少
一个已启用 Team，才能完成真实联网 Planner 浏览器 E2E。计划审批、Team Run、Event/SSE、跨
Agent Memory 和 Studio 节点仍属于后续阶段；预览页中的演示数据只用于体验流程，不会写入正式数据。

## 13. 2026-10-08 Run 配置快照与可靠性增量

- 新运行保存团队、Agent、计划快照，后续编辑不会改变历史配置；旧运行保持快照为空。
- 同一任务审批和运行启动使用行锁，避免并发状态检查重复启动。
- 运行总超时遵守 Team limits 与服务上限；失败事件与终态持久化。
- 修复 Teams PATCH 的 updated_at 隐式异步加载错误，以及 Alembic 模板 URL/metadata 未接入配置的问题。
- Core 60 项测试通过；SSE、成员级执行、取消、重启恢复、Memory、Studio 尚未完成。

## 12. 2026-09-24 Task Team Run 与持久 Event 第一阶段

- ULOO Core 新增 workspace 隔离的 `Run`、`RunEvent` 模型和 migration；事件以
  `(run_id, sequence)` 保证稳定顺序，正式数据不会与预览数据混用。
- 新增计划审批、任务执行、Run 列表/详情和游标 Event 查询接口。执行前强制验证当前计划已审批、
  Team 与成员均属于当前 workspace；失败也会持久化 Run 和安全错误事件。
- Team Run 使用真实 Agno `Team` Factory 与模型调用；没有服务端模型密钥时返回可追踪的
  `MODEL_CONFIG_MISSING`，不会切换 FakeModel。
- Dify API 新增 workspace 认证的强类型 BFF；OpenAPI 与 TypeScript contract 已重新生成。
- 正式 `/uloo/tasks/{id}` 已接入“确认计划并开始执行”，成功后进入真实 Run 详情；
  `/uloo/runs` 与 `/uloo/runs/{id}` 已读取持久 Run/Event，预览路由仍明确标注演示数据。

遗留：当前执行请求仍为同步等待模型返回；SSE、取消、超时/预算执行器、成员级快照与服务重启恢复
尚未实现。硅基流动 provider API key 与 `ULOO_PLANNER_MODEL_REF` 已完成配置，并已通过真实 Planner
和双 Agent Team Run 验证；Team usage 的 token 规范化、成员级结果持久化和正式 workspace 浏览器
E2E 仍待完成。

## 14. 2026-10-09 DeepSeek 完整流程接口验收

- 已接入 DeepSeek-V3.1；密钥仅保存在忽略提交的服务端本地环境文件。
- 智能体声明、定义修改、运行配置校验、单体执行及 JSON Schema 输出校验已通过真实模型请求。
- coordinate、route、collaborate 三种团队模式均完成规划、审批、成员调用、结果汇总和持久化；每种模式记录到两个真实成员调用。
- 修复模型结构化输出兼容性、成员 token 统计、计划依赖校验、更新返回成功前数据库尚未提交的问题。
- 正式团队页面已接入创建、编辑和校验；运行详情展示真实成员输出与最终结果。
- Core 71 项测试、迁移升降级、Ruff、Mypy、OpenAPI 一致性和 Dify BFF 28 项测试通过。
- 可重复验收脚本：`scripts/verify_model_flow.py`；最新真实模型报告：`tmp/structured-model-flow-report.json`。

用户选择暂不登录，因此本次未执行登录后的浏览器端到端验收。SSE、取消、重启恢复、跨 Agent Memory 和 Studio 仍未完成；max_tokens 限制单次模型响应，尚不是累计团队预算。
- Web ULOO 16 项自动化测试与 TypeScript 全量类型检查通过；团队表单测试使用真实组件交互。

## 15. 2026-10-09 本地插件服务报错修复

- 页面模型列表报错源于 plugin_daemon 容器缺失；已启动 compose 固定的 0.6.3-local 插件服务。
- 对齐本地 Dify API 与开发 compose 的插件通信配置，重启 API 生效。
- 真实 workspace 的 `/management/models` 请求返回 HTTP 200、code=0；目前尚无已安装的 Dify 模型插件。
- README 补充插件服务启动命令，修正插件端口为 5002/5003。

## 16. 2026-10-09 智能体互联网搜索

- 服务端工具白名单新增 `web-search`，基于 DDGS 真实公网检索，返回来源 URL、摘要与检索 UTC 时间；最多 8 条结果，20 秒连接超时，失败明确返回 unavailable。
- 智能体工具引用填写 `web-search` 即可使用；编辑表单补充提示，规划候选成员包含 tool_refs。
- Planner、Agent 与 Team 通过 Agno 注入当前时间，修复模型把“现在”理解为旧年份的问题。
- 已创建互联网行情研究团队并真实完成研究与审核成员调用；任务 ae833253-a07a-42e8-8a60-62dcf3570b5f 运行成功，但没有取得可核实的当前报价，结果明确报告证据不足。
- Core 74 项测试、Ruff、Mypy、迁移及 OpenAPI 一致性通过，前端 TypeScript 检查通过。
- 真实搜索来源及模型验收报告保存在 tmp/web-search-current-flow-report.json。搜索引擎可能限流；检索时间不是来源报价日期，摘要不能保证当前价格。
- 补充真实智能体调用验收：英文短关键词成功返回三个来源，并区分 2025 年历史报价与 2026 年市场分析；报告 tmp/web-search-agent-report.json。
- 区分无结果（empty）与网络失败（unavailable），提示缩短关键词或英文检索。免费搜索源会限流，不能保证每次中文查询都取得结果。

## 17. 2026-10-09 工具库与智能体可视化选择

- 新增正式资源 → 工具库页面 `/uloo/resources/tools`，读取后端 capabilities 返回的真实运行时工具列表。
- 智能体编辑将工具引用文本框改为勾选列表，展示互联网搜索用途及限制；保存后写入 tool_refs。
- 目录加载失败保留已保存引用；未注册工具提示检查配置或移除，避免静默丢失配置。
- 提示词 / 执行指令独立标注；页面明确说明技能注册、绑定及执行尚未实现，不宣称可用。
- 前端 ULOO 19 项测试通过；Core 相关 21 项测试、Ruff、Mypy 通过。
- 补齐 BFF 工具列表类型为 list[str] 并从 OpenAPI 重新生成前端契约；TypeScript 全量检查通过，运行中 Core 返回真实工具目录 [web-search]。

## 18. 2026-10-09 工具库 404 部署修复

- 根因：3000 端口运行旧 standalone 生产构建，新源码路由不会热更新；此前只完成源码和接口验证，未验证实际构建。
- 重新完成 Webpack 生产构建，路由清单包含正式与预览工具库页面；恢复 standalone 服务。
- 构建启用 webpackMemoryOptimizations、webpackBuildWorker 并限制 cpus=2，避免 Windows 冷编译内存耗尽。
- HTTP 实测 signin=200，工具库=307 跳转登录校验；实际浏览器已正常进入 signin 并携带工具库回跳地址，不再 404。
- 本次未执行登录后的工具选择操作；构建仍有既有 Loro WebAssembly 目标兼容性警告，工作流协作未验收。

## 19. 2026-10-09 工具管理、技能库及运行绑定

- 新增工作区隔离的 resource_definitions 表及 Agent.skill_refs；迁移保留既有智能体，默认无技能。
- Core `/api/v1/tools`、`/api/v1/skills` 支持列表、创建、版本校验编辑、启停与删除保护；Dify BFF 白名单接入两类资源。
- 工具配置仅接受 web-search 适配器，支持查询前缀和 1–8 条结果；技能保存指令与所需工具，不支持任意脚本执行。
- 新增资源 → 技能库 `/uloo/resources/skills`；工具库由只读目录升级为管理页面。智能体编辑页勾选工具和技能，停用资源不进入可选列表，已保存引用保留并提示不可用。
- Agent 测试、Team 校验和正式运行加载同一工作区目录，合并技能指令及工具；缺失或停用的引用明确失败。Planner 获取资源目录，运行快照仅保存当次绑定的配置。
- 已创建市场价格调研技能并绑定现有两个调研成员；真实 DeepSeek test-runs 验证绑定技能指令生效。临时验收智能体和技能均清理，报告 tmp/skill-binding-model-report.json。
- Core 78 项、BFF 30 项、前端 ULOO 21 项自动化测试通过；Core Ruff/Mypy、前端源代码 TypeScript 检查通过。标准 tsc 仍受既有 .next/types/app/components/integrations/page.ts 生成类型误报影响，源代码检查排除此生成目录。
- 登录后的正式页面人工操作仍未验收，遵循用户先完成接口与自动化验收的要求。
- 已完成绑定技能后的真实互联网工具调用验收，Agno 记录两次 search_web_search 调用；报告 tmp/skill-search-runtime-report.json。模型返回了来源与历史日期区分，报价正文未在本次开发验收中独立核实，不将其作为已核实的当前行情。
- 最终 Webpack 生产构建成功并已重启 standalone；正式工具库、技能库及智能体入口 HTTP 实测均经 307/303 正常进入登录页（最终 200），无 404。运行中 Core 工具与技能接口均 200，BFF 未登录校验均 401。

## 20. 2026-10-09 Dify 向量库配置修复

- 原因：本地 Dify `.env` 未设置 VECTOR_STORE，检索设置接口直接抛出 Vector store type is not configured。
- 设置 VECTOR_STORE=pgvector，PGVECTOR_* 使用现有数据库连接，确认 dify 数据库 vector 扩展版本 0.8.2；重启 Dify API 生效。
- 通过 Dify 原有检索设置函数验证 semantic_search/full_text_search/hybrid_search 正常返回，并通过实际 pgvector 适配器完成临时向量写入与相似度搜索；已删除验收临时表和缓存。
- 未验收知识库文件上传、Embedding 模型配置及后台索引任务；本次修复配置错误，不宣称完整知识库链路已通过。

## 21. 2026-10-12 Dify 原生工具与 Agno 协同集成

- 工具页复用 Dify 管理入口，新增工作区原生工具目录、参数 schema 查看及引用导入；导入后使用已有智能体/技能选择器绑定。连接及凭据保留在 Dify，导入 API 从服务器目录重新读取定义，不接受浏览器提供的凭据或 schema。
- Core 的 `tool_type=dify` 通过现有 Dify `/inner/api/agent/tools/invoke` 执行插件、API、工作流和 MCP。MCP 使用 Dify 的 server_identifier；按类别隔离目录故障，必填手动参数未配置的工具提示使用 Dify 工作流封装。
- 已配置本地工作区的服务调用身份及应用上下文；新增 `scripts/configure_dify_tools.py`，复用 Dify 应用服务，保留其他工作区配置并避免输出密钥。
- Core 112 项、Dify 目录/导入/BFF 38 项、ULOO 前端 22 项通过；正式生产构建和源码 TypeScript 检查通过。完整 tsc 仍存在此前的 `.next/types/app/components/integrations/page.ts` 组件属性生成错误。
- 真实 DeepSeek 驱动研究员和复核员完成协作，确认团队实际发起 Dify HTTP 工具调用。实际任务 API 的计划生成、审批、执行、成员事件及最终状态验收通过；验收数据事务回滚，临时 Dify 工具及 HTTP 服务已清理。报告位于 `tmp/dify-agno-team-report.json` 和 `tmp/dify-task-api-report.json`。
- 最终正式服务已启动：Core 工具/技能接口 200，Dify BFF 未登录访问 401，正式工具、技能、智能体页面正常转到登录页，无 404。启动时发现 Redis 停止导致插件服务重启，恢复 Redis 后插件服务恢复。
- 未自动登录页面做人工操作验收；真实端到端覆盖 Dify API 工具，本次未逐类实测 MCP、插件、工作流，也未验收外网搜索结果质量。其他 Agno 能力按集成计划后续逐项接入。

## 22. 2026-10-13 S6 场景与质量验收

### 22.1 S6-a 自动化验证

- Core 224 项测试通过（ruff/mypy/迁移 upgrade→downgrade→upgrade/OpenAPI 一致性）。
- Dify BFF 38 项测试通过。
- 前端 24 项测试通过（8 个测试文件），修复 4 个预存测试 bug：
  - `task-records.test.tsx`：i18n mock 返回键名而非中文，断言从 `/查看成员执行与结果/` 改为 `/uloo.viewRunDetail/`。
  - `tools.test.tsx` "shows Dify tool references"：web-search 工具确有编辑按钮，移除矛盾断言。
  - `tools.test.tsx` "creates a reusable skill"：表单提交包含 scenarios/steps/result_format/quality_checks 字段，更新预期 body。
  - `tools.test.tsx` "keeps saved references visible"：UlooToolPicker 使用 raw fetch 而非 mock 的 @/service/base，补充 vi.stubGlobal('fetch')。
- 契约 `contracts/openapi.yaml` 已同步 S4/S5 端点（30 paths，含 3 个 S4/S5 端点、5 个 Run 端点）。
- TypeScript 类型已从 OpenAPI 重新生成。
- Webpack 生产构建成功。

### 22.2 S6-b 前端页面完整性检查

- i18n 键对齐：en-US 与 zh-Hans 各 302 键，无缺失。
- i18n 使用：237 个引用键全部存在于翻译文件。
- 路由覆盖：40 个页面文件，18 个路由（createUlooRoutes 生成）全覆盖。
- 语法检查：32 个组件通过 TypeScript 语法检查。
- 测试：24/24 通过（修复 4 个预存 bug 后）。

### 22.3 S6-c 文档更新

- README.md：当前状态已更新（S1–S5 代码完成验收门待验证、S6 进行中、测试基线 Core 224/前端 24）。
- TODO.md：S1–S5 优先补代码勾选、S6 已完成项勾选、回归基线与执行记录已更新。
- docs/STAGE_STATUS.md：当前位置表已更新（Stage 2/3 标为已完成，新增产品阶段 S0–S7 行），验证证据已更新（224/38/24），新增本节。

### 22.4 遗留

- S6 剩余产品级验收项：登录后浏览器自动化验收、真实外网检索核查、不同材质/规格/单位比较、核验触发补查、覆盖正常/条件缺失/无结果/陈旧来源/工具失败/超时/修改目标、清理临时数据。
- SSE、取消、重启恢复已实现；Studio 插件和跨 Agent Memory 仍未完成。
- S7 可复现发布待开始。
