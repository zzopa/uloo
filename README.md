# ULOO V2

Unified Logic Orchestration & Operations：以 Dify 源码作为唯一可视化产品壳，以 Agno 作为唯一多 Agent 协同运行时，以 ULOO Core 管理 Agent、Team、Run、Event 与跨 Agent Memory。

当前状态：Agent 定义与调试、Team 创建/编辑/校验、任务规划与审批、真实 Agno Team 执行、
持久 Run/Event 和成员结果已接通。DeepSeek-V3.1 的单 Agent 与三种 Team 模式已有真实模型
接口验收。正式 Teams 与 Run 页面读取后端数据；登录态浏览器操作尚未验收。
SSE、取消、重启恢复、Studio 插件和 Memory 仍未完成，当前尚不是完整可交付产品。

实施与接口定义见 [docs/ULOO_V2_IMPLEMENTATION_PLAN.md](docs/ULOO_V2_IMPLEMENTATION_PLAN.md)，
产品闭环路线见 [docs/ULOO_PRODUCT_COMPLETION_PLAN.md](docs/ULOO_PRODUCT_COMPLETION_PLAN.md)。
当前任务与验收进度见 [TODO.md](TODO.md) 和 [docs/STAGE_STATUS.md](docs/STAGE_STATUS.md)。

## 开发环境启动

基础设施（PostgreSQL 5432、Redis 6379、plugin daemon 5002/5003）由
`deploy/compose.dev.yaml` 或你本机已有实例提供；下面的脚本只负责三个从源码运行的进程。

PostgreSQL 和 Redis 都跑在容器里，所以 **Docker Desktop 必须先处于运行状态**：daemon 不在时
ULOO Core 与 Dify API 仍会启动成功，却让每次查询都失败，看起来像应用 bug。`dev-up.ps1`
会在启动前探测 daemon，不可用就直接报错退出并给出指引。

```powershell
docker compose -f deploy/compose.dev.yaml up -d plugin_daemon # 首次启动插件服务
pwsh -File scripts/dev-up.ps1      # 启动并等待健康检查
pwsh -File scripts/dev-down.ps1    # 停止本项目的服务
```

| 服务 | 地址 | 健康探针 |
|---|---|---|
| Dify Web | http://localhost:3000 | `GET /` |
| Dify API | http://localhost:5001 | `GET /health` |
| ULOO Core | http://localhost:8200 | `GET /api/v1/health/live` |

脚本会在端口已被占用时跳过该服务，因此可以重复执行；被跳过的服务也会记下当前持有端口的
pid，交给 `dev-down.ps1` 收尾。日志写入 `tmp/dev-logs/`。

`dev-down.ps1` 只终止能确认属于本仓库的进程（可执行文件是 node/python，且自身或祖先命令行
指向本目录），端口上别的进程会被报告并跳过，避免误杀数据库或无关服务。

### Next.js dev 服务器有内存上限

Dify Web 跑的是 `next dev`（Turbopack）。Turbopack 在 V8 堆之外分配内存，不受 Node 自身的
堆上限约束：2026-09-21 实测单个 `node.exe` 提交了 **26 GB**，触发 Windows 提交内存耗尽，
连带杀死 Docker 与 IDE（系统日志事件 2004）。因此 `dev-up.ps1` 会为它设置两道上限：

| 参数 | 默认 | 作用 |
|---|---|---|
| `-WebTurbopackMemoryLimitMB` | 6144 | Turbopack 自身分配上限，经 `ULOO_TURBOPACK_MEMORY_LIMIT_MB` 传给 `next.config.ts` |
| `-WebMaxOldSpaceMB` | 4096 | V8 老生代上限（本机 Node 默认即 4288 MB，此处是收紧而非放宽） |

内存紧张或同时开着大型 IDE 时可调低：

```powershell
pwsh -File scripts/dev-up.ps1 -WebTurbopackMemoryLimitMB 4096
```

`next.config.ts` 里该选项由环境变量驱动，未设置时不写入配置，**上游默认行为不变**。

### 不要在 Git-Bash / MSYS 里启动 Dify API

从 MSYS 派生的 shell 启动 `vendor/dify/api` 会得到：

```text
fatal error - Internal error: TP_NUM_C_BUFS too small: 50
```

这是 Cygwin 运行时启动原生 Python 的兼容故障，在 Flask 被导入之前就发生，**与 Dify
和 OpenDAL 都无关**（本机 `import opendal` 正常）。用 PowerShell 或 cmd 启动即可，
`scripts/dev-up.ps1` 已经处理了这一点。

## 验证门

### 真实模型主流程验收

模型配置示例见 [deepseek.env.example](services/uloo-core/deepseek.env.example)。API Key 只放在
`services/uloo-core/.env`，模型引用为 `deepseek:DeepSeek-V3.1`；修改配置后重启 Core。

```powershell
services/uloo-core/.venv/Scripts/python.exe scripts/verify_model_flow.py --workspace <Dify-workspace-UUID>
```

该脚本会在指定工作区创建真实 Agent、三种模式的 Team 和任务，逐一验证定义更新、结构化输出、
规划、审批、成员执行、用量与结果回读，并保存 `tmp/model-flow-report.json`。它会调用真实模型并
保留验收记录，不会修改现有定义。正式页面入口为 `/uloo/resources/agents`、
`/uloo/resources/teams` 和 `/uloo/tasks`。

### 自动化验证

```bash
uv run --project services/uloo-core python scripts/verify.py
```

依次执行 Core 的 ruff / mypy / migration `upgrade→downgrade→upgrade` / pytest+coverage /
OpenAPI drift，以及 Dify BFF 单测与 `uv lock --check`。所有数据库操作都落在一次性
schema 中，不触碰开发库。

## 不可变原则

- 必须从固定版本的 Dify 源码运行和构建 Web/API，不能用官方成品镜像冒充源码开发成果。
- Dify Workflow 负责外层可视化流程；一个 ULOO Team 节点对应一次明确的 Agno Team Run。
- Agno 是唯一的 Team 成员调度器，不再自建第二套 route/coordinate/collaborate 循环。Team mode 取值以 Agno 运行时的 `Team.mode` 为准（当前 Agno 1.8.4 为 `coordinate`/`route`/`collaborate`），单一事实源见 `services/uloo-core/src/uloo/constants.py`。
- FakeModel 只允许存在于自动化测试，生产和手工验收不得静默使用假模型。
- MVP 不先引入 NATS、MinIO、独立 Worker 等非必要组件；主链路通过后再按可靠性需求增加。
- 不开发第二套 Workflow Canvas，不扩展独立用户体系，复用 Dify 的登录与 workspace 上下文。

## 智能体互联网搜索

在资源 → 工具库查看实际注册工具；在智能体编辑页面勾选“互联网搜索”，保存并校验即可启用。
该工具使用 DDGS 获取真实公网搜索结果，无需单独填写搜索 API Key；返回来源链接、摘要及检索时间。
每次最多 8 条结果，搜索连接超时 20 秒。搜索失败或无结果会明确返回状态，不会生成替代数据。
模型应根据来源链接注明报价日期、规格和单位；搜索摘要不保证实时价格，缺失信息必须标注未知。
参考实现与接口说明：[DDGS 官方仓库](https://github.com/deedy5/ddgs)。

如果 3000 端口运行的是 `.next/standalone/web/server.js`，它属于生产构建，新增源码路由不会自动生效。
修改页面后需要重新构建并重启，而不能只重启旧 standalone：

```powershell
cd vendor/dify/web
pnpm exec next build --webpack
pnpm start
```

## 工具管理与技能绑定

- 资源 → 工具库：新增、编辑、启停和删除工具配置。目前支持互联网搜索适配器，可设置查询前缀和结果数量；内置 `web-search` 可编辑和停用，不能删除。
- 资源 → 技能库：保存可复用的执行指令与所需工具。在智能体编辑页勾选技能；智能体测试、团队校验及正式运行都会加载技能指令，并去重合并所需工具。
- 已提供“市场价格调研”技能并绑定现有调研团队的两个成员。可继续在技能库编辑。
- 停用或缺失的绑定资源会使校验及执行失败；删除已绑定资源前需解除智能体/技能引用。编辑使用版本号防止覆盖他人的更新，资源按 Dify 工作区隔离。
- 每次运行保存实际绑定的技能与工具配置快照；后续编辑不会改变旧快照。凭证继续保存在服务端，资源配置不接受任意 Python、Shell、外部地址或密钥。
- 技能当前是复用的指令与工具组合，尚不支持上传脚本、MCP 或任意 HTTP 工具。

本地 Dify 知识库使用现有 PostgreSQL/pgvector 容器：`vendor/dify/api/.env` 必须设置 `VECTOR_STORE=pgvector`。`PGVECTOR_HOST/PORT/USER/PASSWORD/DATABASE` 分别与本地 `DB_HOST/PORT/USERNAME/PASSWORD/DATABASE` 对齐，并在该数据库启用 `vector` 扩展。修改配置后重启 Dify API。缺少 `VECTOR_STORE` 会导致检索设置接口报 `Vector store type is not configured.`。
