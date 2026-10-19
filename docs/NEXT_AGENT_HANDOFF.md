# ULOO 后续开发交接清单

更新时间：2026-10-19

本文件交给下一位智能体继续开发。目标不是继续增加演示页面，而是把已经存在的 Dify 产品能力与 Agno 多智能体执行真正收口成可稳定使用、可验收、可复现的 ULOO。

## 1. 必须先理解的产品边界

- Dify 是唯一的产品外壳：登录、工作空间、模型、知识库、工作流、工具和页面尽量复用 Dify。
- Agno 是唯一的多智能体协同运行时：`coordinate`、`route`、`collaborate` 不得再由 ULOO 自建第二套调度循环。
- ULOO Core 负责 Agent、Team、Task、Plan、Run、Event、快照和后续 Memory。
- 不开发第二套 Workflow Canvas，不新建独立用户体系，不用静态演示或 FakeModel 冒充真实业务完成。
- 完成标准是“正式登录页面 + 真实模型/工具 + 可回看的持久记录”，只有接口或测试不能直接宣称产品完成。

## 2. 当前真实状态

当前可演示业务闭环约为 75%，生产可用程度约为 55%–60%。

已完成并有证据：

- Dify 登录、工作空间和 ULOO 正式导航可用。
- Agent、Team、工具、技能和模型配置已有页面与 BFF。
- DeepSeek-V3.1 真实调用已通过。
- Agno 单 Agent 以及 `coordinate`、`route`、`collaborate` 三种 Team 模式已有真实执行记录。
- 任务支持“直接执行”和“先规划”；计划可编辑团队、步骤、负责人、依赖和预期产物。
- 直接执行已合并为一次服务端请求：同一请求准备计划并创建后台 Run，页面切换不会漏掉启动请求。
- Run、Event、成员输出、配置快照、最终结果、取消/重试的基础链路已存在。
- 最近一次完整回归记录为 Core 249 项、Dify 集成 38 项、Web 29 项通过；Ruff、mypy、迁移升降级、OpenAPI 一致性和生产构建通过。

不能宣称完成：

- S1–S5 仍有验收缺口，S6 进行中，S7 未开始。
- 木板市场价格调研尚未完成真实来源、条件补充、交叉核验和补查闭环。
- SSE 真实浏览器断线/多进程并发、异步恢复和外部副作用幂等缺少完整验收；后台执行使用创建时配置快照已完成代码与自动化验证。
- `continue` 目前仍偏事件标记，不是真正暂停等待人工输入后继续。
- 跨任务 Memory、Dify Studio 中的 Agno Team 节点和可复现发布尚未完成。
- 完整 Web `tsc` 仍有 Dify 原有路由类型错误；生产构建跳过类型检查，二者不能混为同一验收证据。

不要用 `TODO.md` 的勾选比例直接计算产品完成率。该文件包含历史记录、未来能力和重复验收项，应按本文件的验收门逐项关闭。

## 3. 当前工作区注意事项

工作区包含尚未提交的用户代码。开始前必须执行：

```powershell
git status --short
git -C vendor/dify status --short
```

禁止使用 `git reset --hard`、`git checkout -- .` 或覆盖已有修改。

`vendor/dify` 是固定版本的 Dify 源码工作区。它的最终改动还必须导出到根仓库：

```powershell
services/uloo-core/.venv/Scripts/python.exe tmp/export_current_dify_patch.py
```

导出后必须确认：

- `patches/dify-uloo.patch` 已更新。
- `vendor/DIFY_VERSION` 的 SHA256 已同步。
- 补丁可以从 `vendor/DIFY_VERSION` 记录的 Dify 基线干净应用。

不要把密码、模型密钥、Cookie 或服务令牌写入代码、文档、日志和测试快照。使用现有本地 `.env`，验收报告只能保留脱敏配置。

## 4. P0：先完成可用主链路

### P0-1 一键启动和依赖健康

现状：2026-10-19 启动时，Docker Desktop 已运行，但 `my-redis` 没有启动，导致登录请求和 plugin daemon 失败；Next 开发服务器首次 webpack 编译还出现过长时间无响应。已做最小修复：

- `deploy/compose.dev.yaml` 的 `REDIS_HOST` 支持环境变量覆盖。
- `deploy/.env.example` 使用 `REDIS_HOST=host.docker.internal`，不依赖手工接入 compose 网络。
- `scripts/dev-up.ps1` 会自动启动已有 PostgreSQL/Redis 容器和 plugin daemon，并逐项等待健康检查。

下一步：完成“三应用进程停止 → 一条命令冷启动 → 登录 `/uloo`”人工验收；脚本代码与复用栈健康验收已通过。

验收标准：

- 从三个应用进程都停止的状态执行一个明确命令即可启动。
- PostgreSQL、Redis、plugin daemon、Core、Dify API、Web 都有真实健康检查。
- Redis 或数据库未启动时快速失败并给出具体修复动作。
- `http://localhost:3000/uloo` 能登录并显示任务首页。
- 页面没有 `Failed to request plugin daemon`、网络错误或无限加载。
- `scripts/dev-down.ps1` 能只停止本项目进程，不误杀其他项目。
- 必须分别验证开发模式和生产构建模式；不能只用旧 `.next/standalone` 冒充当前源码已生效。

### P0-2 完成真实互联网价格调研场景

使用一个需要互联网搜索的真实任务完成端到端验收，例如木板市场价格调研。缺少地区、材质、厚度、尺寸、含税/运费等关键条件时，系统必须请求补充，或在执行计划和结果中明确假设，不能静默猜测。

验收标准：

- 正式页面创建任务并选择/自动选择市场调研团队。
- 计划中能看到调研、整理、交叉核验和汇总职责，不是单节点假协同。
- 实际调用 `web-search`，至少获得 3 个可访问来源。
- 每条报价包含产品规格、价格、币种、计价单位、报价日期、来源链接和抓取时间。
- 不同规格不能直接混合比较；无法确认的信息标为未知。
- 核验成员能发现来源不足或冲突并触发有限次数的补查。
- 最终 Run 成功，成员产出、工具摘要、来源、用量、计划快照和最终报告均可回看。
- 刷新页面后结果仍存在；导出内容与页面一致。

建议重点检查：

- `services/uloo-core/src/uloo/runtime/web_search.py`
- `services/uloo-core/src/uloo/runtime/review.py`
- `services/uloo-core/src/uloo/runtime/models.py`
- `services/uloo-core/src/uloo/api/runs.py`
- `vendor/dify/web/features/uloo/`

### P0-3 复核、补查和交接闭环

现状：review policy、completion conditions 和 handoff context 已进入部分执行逻辑，但缺少真实失败→补查→再次复核的完整证据。

验收标准：

- 复核成员收到上一成员的结构化交接信息。
- 来源不足、成员缺失、结果为空或完成条件不满足时不得标记成功。
- 补查次数有上限，达到上限后进入明确失败状态并展示原因。
- 补查意见、成员响应、最终判定和 token 用量都写入事件并可查询。
- `route` 模式允许只选必要成员；`coordinate`/`collaborate` 的实际成员执行与计划一致。
- 增加一个最小自动化测试，并完成一次真实 DeepSeek 验收。

### P0-4 Run 可靠性

验收标准：

- SSE 支持鉴权、游标续传、断线重连、终态关闭和并发事件顺序。
- 取消能中断活动调用并稳定落到 cancelled，不出现取消后又 succeeded。
- 重试创建新 Run，保留旧 Run 和原始快照。
- Core 或 API 重启后，活动 Run 有明确恢复或失败策略，不永久停在 running。
- 同一启动请求重复提交不会产生不可控的重复副作用。
- 计划版本、Agent/Team/技能/工具定义在启动时形成不可变快照。

## 5. P1：关闭 S1–S6 的验收缺口

### P1-1 工作空间和定义真实性

- 真实验证两个 workspace 的模型、Agent、Team、技能和工具隔离。
- 旧任务必须继续显示当时的定义与配置，不能被后续编辑覆盖。
- 验证 Dify 模型默认配置变更、调用身份、旧引用兼容。
- 逐类验证 API、Dify Workflow、知识库、插件、MCP 和互联网搜索；未支持的类型必须如实报告。

### P1-2 产品页面闭环

- 新建任务、补充条件、编辑计划、执行、失败处理、结果查看和导出都从正式页面完成。
- 运行详情展示清楚的成员状态、正在执行的工具、错误原因和下一步动作。
- 验证并发编辑冲突、刷新恢复、空状态、错误状态、窄屏和移动端。
- 清理重复 Team/Agent 测试数据，但不得删除有验收价值的历史 Run。

### P1-3 质量门

- 完整 `tsc` 的路由类型问题必须定位：若为上游 Dify 问题，提供最小复现和隔离检查；若由 ULOO 路由触发，必须修复。
- 生产构建、源代码类型检查和运行时验收分别记录，不得互相替代。
- 固定真实场景回归：基础计算、互联网调研、失败补查、取消、重试、重启恢复。

## 6. P2：一期后续能力

仅在 P0/P1 验收通过后继续：

- 跨任务 Memory：范围、来源、查看、纠正、删除和事实确认。
- Dify Studio 的 Agno Team 节点：Workflow 只负责外层编排，一个节点对应一次 Agno Team Run。
- 长任务续跑、共享预算、完成率/证据质量/耗时/成本评估。
- S7 干净 clone、固定 Dify 补丁、完整配置、构建和发布复现。

不要提前引入 NATS、MinIO、独立 Worker 或第二套画布。只有当前后台执行确实无法满足可靠性验收时，才用证据推动架构升级。

## 7. 验证命令

完整验证入口：

```powershell
uv run --project services/uloo-core python scripts/verify.py
```

Core 快速验证：

```powershell
cd services/uloo-core
.venv/Scripts/python.exe -m ruff check src tests migrations
.venv/Scripts/python.exe -m mypy src
.venv/Scripts/python.exe -m pytest -q
```

Dify ULOO 集成测试：

```powershell
cd vendor/dify/api
.venv/Scripts/python.exe -m pytest `
  tests/unit_tests/controllers/console/test_uloo_proxy.py `
  tests/unit_tests/controllers/console/test_uloo_tool_import.py `
  tests/unit_tests/services/test_uloo_tool_catalog.py `
  -o addopts="" -q
```

Web ULOO 测试与构建：

```powershell
cd vendor/dify/web
pnpm test --run features/uloo
pnpm exec tsc --noEmit
pnpm exec next build --webpack
```

OpenAPI 发生变化时：

```powershell
services/uloo-core/.venv/Scripts/python.exe scripts/export_openapi.py
```

真实模型验收会产生模型和互联网搜索用量，必须只在明确的验收任务上执行。不得在自动化单测中调用真实模型。

## 8. 每次交付必须更新

- `TODO.md`：只勾选有代码和验收证据的项目。
- `docs/STAGE_STATUS.md`：记录修改文件、执行命令、结果、Run ID 和仍未完成项。
- `README.md`：只维护当前启动方法和真实状态，不堆叠历史描述。
- `contracts/openapi.yaml` 及 Dify 生成契约：接口变化时同步。
- `patches/dify-uloo.patch` 与 `vendor/DIFY_VERSION`：Dify 目录变化后同步。

交付报告必须明确区分：自动化通过、正式页面通过、真实模型通过、尚未验证。任何失败、跳过或环境限制都要写明，不能用“基本完成”“骨架可用”替代事实。

## 9. 建议执行顺序

1. 保存当前 `git status`，确认现有修改归属。
2. 修复并验证一键启动，确保 plugin daemon、Redis 和 Web 不再随机失败。
3. 跑自动化基线，失败先修复回归，不直接开发新功能。
4. 完成真实互联网价格调研场景。
5. 根据场景暴露的问题补齐复核/补查、SSE、取消、重试和恢复。
6. 关闭 workspace、定义快照和页面异常路径验收。
7. 解决或隔离完整 `tsc` 问题。
8. 更新文档、重新导出 Dify 补丁，在干净环境复现。
9. P0/P1 全部通过后再开发 Memory 和 Studio 插件。

