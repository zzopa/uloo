# ULOO V2 状态文档

本文件是 `docs/ULOO_V2_IMPLEMENTATION_PLAN.md` 第 8 节要求的阶段状态记录：列出每个阶段的
修改文件、验证命令和未完成项。**未完成项一律标为未完成，不用"骨架可用"冒充业务可用。**

最后更新：2026-09-24

## 0. 界面蓝图状态

已冻结 [`ULOO_UI_BLUEPRINT.md`](ULOO_UI_BLUEPRINT.md)，统一定义产品信息架构、规范路由、16 类界面、任务四阶段工作台、全局状态、接口依赖和 UI-0～UI-6 实施顺序。

UI-0 已部分实施：完成分组二级导航、窄屏导航、统一 route context、正式/预览路由隔离和旧路由重定向；预览区已补齐 Runs、Artifacts、Memory、Teams、Agents、Runtime、Studio Integration 的列表/详情静态界面及主要对象回链。新增静态页面回归测试锁定预览路由不会串入正式页面，并已逐路由完成桌面浏览器验收。

这不代表所有对应后端能力已经实现。真实 Team Run 与持久 Event 已完成首条纵向链路；SSE、Artifact、Memory 与 Studio 节点仍按未完成记录。UI-0 尚需把旧页面完全收敛到统一标题/面包屑组件、补齐全链路双向对象引用，并完成窄屏与刷新恢复验收。

UI-1 已开始：正式任务详情与预览任务详情现共用四阶段进度组件，阶段固定为“团队规划 → 等待确认 → 多 Agent 执行 → 汇总交付”；正式页按真实 Task 状态定位阶段，预览页继续提供明确标识的可重复演示。

## 1. 当前位置

| 阶段 | 状态 | 说明 |
|---|---|---|
| Stage 0 干净源码基线 | 部分完成，未过验收门 | Dify patch 已本地提交，但不是 submodule/subtree 且无团队 fork，全新 clone 无法取得 patch |
| Stage 1 ULOO Core 最小骨架 | 已完成 | health / truthful capabilities / Alembic / structlog / 标准错误信封 |
| Stage 2 Agent 与 Team 配置闭环 | 部分完成 | Agents 页面与 BFF 已完成；Teams 页面和真实登录工作空间 E2E 尚缺 |
| Stage 3 真实 Agno 单次执行 | 部分完成 | Provider Registry、Agent/Team Factory、Agent test-run 与已审批 Task 的 Team Run 已完成；真实供应商联网验收待配置密钥 |
| Stage 4 Dify Studio 插件闭环 | 未开始 | `extensions/dify-agno-strategy/` 不存在 |
| Stage 5 Runs 可观测性 | 部分完成 | `runs` / `run_events`、列表/详情/游标事件接口和正式 Web 页面已完成；SSE、取消、成员快照与异步恢复未完成 |
| Stage 6 跨 Agent Memory | 未开始 | 无 `memories` 表与接口 |
| Stage 7 可靠异步执行 | 未开始 | 按方案仅在主链路稳定后再引入 |
| Stage 8 源码发布 | 未开始 | 无 release 镜像构建 |

## 2. 开发环境与验证命令

基础依赖（PostgreSQL/pgvector、Redis）在 Docker 中，`plugin_daemon` 由 compose 管理：

```bash
docker compose -f deploy/compose.dev.yaml --env-file deploy/.env up -d
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

`services/uloo-core` 测试：`30 passed`；OpenAPI contract 包含在完整测试中。

Dify BFF 单测：`26 passed`。

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
7. Memory 表、Studio 插件和 SSE 事件映射仍未开始；Run/Event 持久化与 Task Team Run 已在后续阶段完成。

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
