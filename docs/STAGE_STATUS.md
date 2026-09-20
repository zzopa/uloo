# ULOO V2 状态文档

本文件是 `docs/ULOO_V2_IMPLEMENTATION_PLAN.md` 第 8 节要求的阶段状态记录：列出每个阶段的
修改文件、验证命令和未完成项。**未完成项一律标为未完成，不用"骨架可用"冒充业务可用。**

最后更新：2026-09-20

## 1. 当前位置

| 阶段 | 状态 | 说明 |
|---|---|---|
| Stage 0 干净源码基线 | 部分完成，未过验收门 | Dify 1.16.1 本机源码存在，但不是 submodule/subtree，ULOO patch 未提交且全新 clone 无法取得 |
| Stage 1 ULOO Core 最小骨架 | 已完成 | health / truthful capabilities / Alembic / structlog / 标准错误信封 |
| Stage 2 Agent 与 Team 配置闭环 | 部分完成 | CRUD 与 BFF 已有；仍缺 workspace 隔离、并发正确性和全部 Dify 前端页面 |
| Stage 3 真实 Agno 单次执行 | 未开始 | 无 Agent/Team factory，无 Run 表，无事件映射 |
| Stage 4 Dify Studio 插件闭环 | 未开始 | `extensions/dify-agno-strategy/` 不存在 |
| Stage 5 Runs 可观测性 | 未开始 | 无 `runs` / `events` 表与接口 |
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

`services/uloo-core` 测试：`22 passed`；OpenAPI contract 单独执行 `3 passed`。

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
2. **未提交任何 Git commit**：本轮的 vendor 与根仓库改动都还在工作区，方案第 8 节
   "每阶段必须有真实 Git commit"尚未满足。
3. `vendor/dify` 的团队 fork URL 未记录，`origin` remote 待配置。
4. `vendor/DIFY_VERSION` 中 pin 的上游 ref 未经联网核对（本机无网络访问）。
5. 开发库中存在历史脏数据：`agent_definitions` 8 行、`team_definitions` 3 行，
   其中 6/2 行是早期非隔离测试遗留（key 形如 `test-*`），2/1 行是本次端到端验证产生的
   `smoke-*`。已不影响测试（用例自带唯一 key），是否清理由团队决定。
6. `vendor/dify` 的 `api/.venv` 未安装 `pytest-cov`，而 `api/pytest.ini` 的 `addopts`
   默认带 `--cov`，因此跑 Dify 测试需追加 `-o addopts=""`。
7. Stage 3 起全部未开始：无 Run/Event/Memory 表、无 Agno factory、无插件、无 SSE 事件映射。

## 7. 2026-09-20 真实性与错误契约修正

- 新增根目录 `TODO.md`，以 P0～P7、任务 ID、接口、效果和验收门管理后续优化。
- `GET /api/v1/capabilities` 改为有类型的能力响应；当前明确报告 agent/team CRUD 可用，
  agent test run、team run、streaming 和 memory 不可用。
- `POST /agents/{id}/test-runs` 不再用 `is_mock=false` 包装占位结果；现返回
  `501 NOT_IMPLEMENTED`，待真实 Agno Runtime 完成后再开放。
- 所有 Core 错误统一为 `code/message/request_id/trace_id/details`，并把 request/trace ID
  写入响应头。请求校验错误也使用相同信封。
- 新增 `CapabilitiesResponse`、`FeatureCapabilities`、`ErrorResponse` schema，并重新生成
  `contracts/openapi.yaml`。
- 验证：Core `22 passed`，OpenAPI contract `3 passed`，重新导出 OpenAPI 为 `unchanged`。
