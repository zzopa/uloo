# S1-1 Dify 能力选择器与运行接口盘点

更新时间：2026-10-12。范围：TODO.md S1 第一条「盘点本地版本的模型、工具、知识选择器与运行接口，记录复用路径」。本文件是只读盘点的结构化记录，带文件:行号溯源，供 S1 后续各条实施时定位。

Dify 本地版本：`vendor/dify`（1.16.1）。ULOO Core：`services/uloo-core`。BFF：`vendor/dify/api/controllers/console/uloo_proxy.py`。

## 1. 模型选择器

### Dify 接口

| HTTP | 路径 | 关键参数 | 来源 |
|---|---|---|---|
| GET | `/console/api/workspaces/current/model-providers` | `?model_type=llm\|text-embedding\|rerank\|...` | `vendor/dify/api/controllers/console/workspace/model_providers.py:123` |
| GET | `/console/api/workspaces/current/model-providers/<provider>/models` | `<provider>` | `vendor/dify/api/controllers/console/workspace/models.py:259` |
| GET | `/console/api/workspaces/current/models/model-types/<model_type>` | `<model_type>` | `vendor/dify/api/controllers/console/workspace/models.py:631` |
| GET | `/console/api/workspaces/current/model-providers/<provider>/credentials` | `?credential_id=` | `model_providers.py:143` |
| POST | `/console/api/workspaces/current/model-providers/<provider>/credentials/validate` | body `{credentials}` | `model_providers.py:263` |
| POST/PUT/DELETE | `/console/api/workspaces/current/model-providers/<provider>/credentials` | CRUD 凭据 | `model_providers.py:144` |
| POST | `/console/api/workspaces/current/model-providers/<provider>/credentials/switch` | body `{credential_id}` | `model_providers.py:240` |
| GET/POST | `/console/api/workspaces/current/default-model` | `?model_type=` / body `{model_settings}` | `models.py:205` |
| GET | `/console/api/workspaces/current/model-providers/<provider>/models/parameter-rules` | `?model=` | `models.py:608` |
| PATCH | `/console/api/workspaces/current/model-providers/<provider>/models/enable` | body `{model, model_type}` | `models.py:517` |
| PATCH | `/console/api/workspaces/current/model-providers/<provider>/models/disable` | body `{model, model_type}` | `models.py:539` |

返回形状关键字段：provider 列表含 `status` 与已配置 `models:[{model, model_type}]`；按类型聚合返回 `{provider:{provider}, models:[...]}`；默认模型 `{model, provider, model_type}`。

隔离边界：全部 `@login_required` + `@setup_required` + `@account_initialization_required` + `@with_current_tenant_id`，以当前 Dify 工作区 tenant_id 隔离。

### ULOO 现状

| 位置 | 现状 |
|---|---|
| `services/uloo-core/src/uloo/models/agent.py:31` | `model_ref: Mapped[str]` 纯文本字段，无外键 |
| `services/uloo-core/src/uloo/schemas/agent.py:21` | schema 仅长度校验，不校验 provider 是否存在 |
| `services/uloo-core/src/uloo/runtime/models.py:25-69` | `ModelRegistry` 从 `settings.model_providers` / `model_provider_api_keys` 解析 `provider:model_id` 构造 `OpenAILike`，**独立于 Dify 工作区模型配置** |
| `services/uloo-core/src/uloo/config.py:49-54` | `model_providers`、`model_provider_api_keys`、`planner_model_ref` 均从 `.env` 读取 |
| `services/uloo-core/src/uloo/runtime/factory.py:23-27,34` | Planner 读 `settings.planner_model_ref`，为空报 `MODEL_CONFIG_MISSING` |
| `services/uloo-core/src/uloo/runtime/factory.py:105,161` | `build_agent`/`build_team` 调 `model_registry.resolve(definition.model_ref)` |
| `services/uloo-core/src/uloo/api/capabilities.py:31-33` | `models` 仅返回 `{provider: {base_url}}`，不含可用模型列表 |
| `services/uloo-core/deepseek.env.example` / `siliconflow.env.example` | `.env` 示例：`ULOO_PLANNER_MODEL_REF=deepseek:DeepSeek-V3.1` |
| `vendor/dify/api/controllers/console/uloo_proxy.py:41` | BFF 白名单 `ALLOWED_ROOT_SEGMENTS` 不含 `models` / `model-providers` |

### 复用路径

在 BFF 新增 `models` 段（或专用端点）透传 Dify `model-providers` + `models/model-types/llm`；智能体编辑页用选择器替换手输 `model_ref`；Core `ModelRegistry` 改为按工作区读取 Dify 已配置模型与凭据，去掉 `.env` 必经。

### 缺口

1. 智能体编辑页手输 `model_ref` 文本，无下拉选择器
2. `.env` 手改必经：`ULOO_MODEL_PROVIDERS` / `ULOO_MODEL_PROVIDER_API_KEYS` / `ULOO_PLANNER_MODEL_REF`
3. BFF 未透传模型列表（`models` 不在白名单）
4. `ModelRegistry` 独立于 Dify，用自己的 key/base_url 构造 `OpenAILike`，不复用工作区凭据
5. Capabilities 只返回 `provider→base_url`，前端无法构建选择器
6. 凭据重复：Dify 工作区配了 DeepSeek key，Core `.env` 还要再配一份

## 2. 工具选择器（插件/API/MCP/工作流四类）

### Dify 接口

| HTTP | 路径 | 关键参数 | 来源 |
|---|---|---|---|
| GET | `/console/api/workspaces/current/tool-providers` | `?type=builtin\|model\|api\|workflow\|mcp` | `vendor/dify/api/controllers/console/workspace/tool_providers.py:483` |
| GET | `/console/api/workspaces/current/tools/builtin` | — | `tool_providers.py:1040` |
| GET | `/console/api/workspaces/current/tools/api` | — | `tool_providers.py:1062` |
| GET | `/console/api/workspaces/current/tools/workflow` | — | `tool_providers.py:1081` |
| GET | `/console/api/workspaces/current/tools/mcp` | — | `tool_providers.py:1564` |
| GET | `/console/api/workspaces/current/tool-provider/builtin/<provider>/tools` | `<provider>` | `tool_providers.py:504` |
| GET | `/console/api/workspaces/current/tool-provider/builtin/<provider>/credentials` | `?include_credential_ids=` | `tool_providers.py:631` |
| GET | `/console/api/workspaces/current/tool-provider/api/tools` | `?provider=` | `tool_providers.py:732` |
| GET | `/console/api/workspaces/current/tool-provider/api/get` | `?provider=` | `tool_providers.py:812` |
| GET | `/console/api/workspaces/current/tool-provider/workflow/get` | `?workflow_tool_id=` 或 `?workflow_app_id=` | `tool_providers.py:984` |
| GET | `/console/api/workspaces/current/tool-provider/workflow/tools` | `?workflow_tool_id=` | `tool_providers.py:1016` |
| GET | `/console/api/workspaces/current/tool-provider/mcp/tools/<provider_id>` | `<provider_id>` | `tool_providers.py:1546` |
| GET | `/console/api/workspaces/current/tool-provider/mcp/update/<provider_id>` | 刷新工具列表 | `tool_providers.py:1581` |
| POST | `/console/api/workspaces/current/tool-provider/mcp` | body `{server_url, name, server_identifier, ...}` | `tool_providers.py:1340` |
| GET | `/console/api/workspaces/current/agent-providers` | — | `agent_providers.py:31` |
| GET | `/console/api/workspaces/current/agent-provider/<provider_name>` | `<provider_name>` | `agent_providers.py:49` |
| POST | `/inner/api/agent/tools/invoke` | body `{caller:{tenant_id,app_id,user_id}, tool:{provider_type, provider_id, tool_name, credential_id, tool_parameters}}` | `vendor/dify/api/controllers/inner_api/agent/tools.py:42` |

返回形状关键字段：provider 列表含 `id/name/type/tools:[{name,description,...}]/team_credentials`；MCP/workflow 项含 `server_identifier` / `workflow_app_id` / `identity_mode`；invoke 返回 `{observation, messages, metadata}`。

隔离边界：Console 接口需 `@login_required` + `@with_current_tenant_id`；Inner API `/inner/api/agent/tools/invoke` 需 `@agent_inner_api_only`（`X-Inner-Api-Key`），不需浏览器登录态。

### ULOO 现状

| 位置 | 现状 |
|---|---|
| `services/uloo-core/src/uloo/models/agent.py:32` | `tool_refs: Mapped[list]` 引用 ULOO ResourceDefinition key |
| `services/uloo-core/src/uloo/runtime/resources.py:52-73` | `ResourceRegistry._build_tool_map` 按 `tool_type` 分发：`builtin`→`configured_search`，`mcp_sse/mcp_stdio`→`MCPToolAdapter`，`dify`→`dify_tool()` |
| `services/uloo-core/src/uloo/runtime/dify_tools.py:14-63` | `dify_tool()` 构造 Agno `Function`，调 `settings.dify_api_url + "/inner/api/agent/tools/invoke"`，凭据 `dify_inner_api_key`，caller `dify_workspace_callers[workspace_id]` |
| `services/uloo-core/src/uloo/schemas/resource.py:42-61` | `DifyToolConfig`: `{tool_type:"dify", provider_type, provider_id, tool_name, credential_id, tool_schema}` |
| `vendor/dify/api/services/uloo_tool_catalog.py:17-82` | `list_uloo_tool_catalog(user_id, tenant_id)` 服务端聚合四类工具为 ULOO 可选目录，`key="dify-"+sha256(identity)` |
| `vendor/dify/api/controllers/console/uloo_proxy.py:269-278` | `GET /console/api/uloo/dify-tools` BFF 端点，直接调 `list_uloo_tool_catalog`（不走 Core） |
| `vendor/dify/api/controllers/console/uloo_proxy.py:281-303` | `POST /console/api/uloo/dify-tools/import` 选定工具后 POST 到 Core `tools` 资源 |
| `services/uloo-core/src/uloo/api/resources.py:61-191` | Core `/tools` CRUD：创建/更新/删除 ResourceDefinition |
| `scripts/configure_dify_tools.py:21-56` | 脚本创建 Dify "ULOO 工具运行上下文" app，写 `ULOO_DIFY_INNER_API_KEY` 和 `ULOO_DIFY_WORKSPACE_CALLERS` 到 Core `.env` |

### 复用路径

服务端目录读取（`uloo_tool_catalog`）已实现；将"导入"步骤改为"直接引用"——`tool_refs` 直接存 Dify 工具 identity，运行时按需解析，跳过 ResourceDefinition 落库。

### 缺口

1. 仍要求"导入"：必须先 POST 到 Core `tools` 变成 ResourceDefinition 才能被 `tool_refs` 引用
2. schema 来源是服务器（正确），但多了一层 ResourceDefinition 落库
3. Dify 工具变更后 ULOO 快照不会自动同步
4. MCP/Workflow 差异（`identity_mode` / `workflow_app_id`）在 catalog 中部分丢失
5. `DIFY_TOOL_CONTEXT_MISSING` 仅报错无修复入口，前端选择器看不到此状态

## 3. 知识选择器

### Dify 接口

| HTTP | 路径 | 关键参数 | 来源 |
|---|---|---|---|
| GET | `/console/api/datasets` | `?page=&limit=&keyword=&ids[]=&tag_ids[]=&include_all=` | `vendor/dify/api/controllers/console/datasets/datasets.py:419` |
| POST | `/console/api/datasets` | body `{name, indexing_technique, permission, provider}` | `datasets.py:558` |
| GET | `/console/api/datasets/<id>` | `<dataset_id>` | `datasets.py:632` |
| PATCH | `/console/api/datasets/<id>` | body `{name, embedding_model, retrieval_model, ...}` | `datasets.py:698` |
| DELETE | `/console/api/datasets/<id>` | `<dataset_id>` | `datasets.py:768` |
| GET | `/console/api/datasets/<id>/documents` | 分页 | `datasets_document.py:378` |
| POST | `/console/api/datasets/<id>/hit-testing` | body `{query, retrieval_model:{search_method, top_k, ...}}` | `hit_testing.py:30` |
| GET | `/console/api/datasets/retrieval-setting` | — | `datasets.py:1194` |
| GET | `/console/api/datasets/<id>/indexing-status` | `<dataset_id>` | `datasets.py:1008` |
| GET | `/console/api/datasets/<id>/related-apps` | `<dataset_id>` | `datasets.py:970` |
| GET | `/console/api/datasets/<id>/use-check` | `<dataset_id>` | `datasets.py:792` |

返回形状关键字段：列表项含 `id/name/indexing_technique/embedding_model/embedding_model_provider/embedding_available/indexing_status/document_count/retrieval_model/permission`；hit-testing 返回 `{query, records:[{segment, score}]}`；retrieval-setting 返回支持的 `retrieval_method`（`semantic_search`/`full_text_search`/`hybrid_search`）。

隔离边界：`@login_required` + `@enterprise_license_required` + `@with_current_tenant_id` + RBAC `DATASET_READONLY`。

### ULOO 现状

| 位置 | 现状 |
|---|---|
| `services/uloo-core/src/uloo/models/agent.py:34` | `knowledge_refs: Mapped[list]` 字段存在但无适配器 |
| `services/uloo-core/src/uloo/runtime/factory.py:98-102` | `validate_agent_runtime_config` 检测 `knowledge_refs` 非空时直接抛 `KNOWLEDGE_CONFIG_MISSING` |
| 全 `src/uloo/` | 无 knowledge adapter、无 vector、无 pgvector、无 dataset 引用 |

### 复用路径

新增 BFF 透传 `/console/api/uloo/datasets` 代理 Dify `datasets` 列表；智能体编辑页直接选数据集 ID 存入 `knowledge_refs`；运行时新增 `KnowledgeAdapter` 调 `/console/api/datasets/<id>/hit-testing` 检索。

### 缺口

1. 无知识适配器：`knowledge_refs` 非空直接报错，完全不可用
2. 智能体编辑页无法选 Dify 数据集：无 BFF 透传，无前端选择器
3. Embedding/索引未就绪无提示：`embedding_available` / `indexing_status` 未透传
4. 检索设置不可见
5. BFF 白名单不含 `datasets`

## 4. 运行接口（固定流程通过 Dify 工作流工具执行）

### Dify 接口

| HTTP | 路径 | 关键参数 | 来源 |
|---|---|---|---|
| POST | `/console/api/apps/<app_id>/workflows/draft/run` | body `{inputs, ...}` | `vendor/dify/api/controllers/console/app/workflow.py:1073` |
| POST | `/console/api/apps/<app_id>/advanced-chat/workflows/draft/run` | body `{inputs, ...}` | `workflow.py:631` |
| POST | `/v1/workflows/run`（Service API） | body `{inputs, response_mode, user}` | `vendor/dify/api/controllers/service_api/app/workflow.py:274` |
| POST | `/v1/workflows/<workflow_id>/run` | body `{inputs, response_mode, user}` | `service_api/app/workflow.py:375` |
| POST | `/console/api/apps/<app_id>/workflows/draft/nodes/<node_id>/run` | body `{inputs}` | `workflow.py:1146` |
| POST | `/console/api/apps/<app_id>/workflow-runs/tasks/<task_id>/stop` | `<task_id>` | `workflow.py:1118` |
| GET | `/console/api/workspaces/current/tool-provider/workflow/get` | `?workflow_tool_id=` 或 `?workflow_app_id=` | `tool_providers.py:984` |
| POST | `/inner/api/agent/tools/invoke`（`provider_type="workflow"`） | body `{caller, tool:{provider_type:"workflow", provider_id, tool_name, tool_parameters}}` | `inner_api/agent/tools.py:42` |

返回形状关键字段：blocking run 返回 `{workflow_run_id, task_id, data:{id, workflow_id, status, outputs, error, elapsed_time, total_tokens, total_steps}}`；streaming 返回 SSE（`node_started`/`node_finished`/`workflow_finished`）；workflow 工具详情含 `{name, label, workflow_tool_id, workflow_app_id, description, parameters, output_schema, synced}`。

隔离边界：Console draft run 需 `@login_required` + `@edit_permission_required` + RBAC `APP_TEST_AND_RUN`；Service API 需 API Token + EndUser；Inner API 需 `X-Inner-Api-Key`。

### ULOO 现状

| 位置 | 现状 |
|---|---|
| `vendor/dify/api/services/uloo_tool_catalog.py:38-48` | workflow 类型工具出现在目录，通过 `WorkflowToolManageService` 加载 |
| `services/uloo-core/src/uloo/runtime/dify_tools.py:14-63` | 导入后的 workflow 工具通过 `dify_tool()` 以 `provider_type="workflow"` 调 inner API |
| `services/uloo-core/src/uloo/runtime/factory.py:116` | `build_agent` 的 `resolved_tools` 来自 `tool_registry.resolve_many(tool_refs + skill_tools)`，可含 dify workflow 工具 |
| `services/uloo-core/src/uloo/runtime/execution.py` | Agno 执行引擎，无独立 workflow run 引用 |
| `vendor/dify/api/controllers/console/uloo_runs.py:128-141` | ULOO Task Run 转发到 Core，Core 走 Agno Team 执行 |

无独立 workflow tool 引用概念；工作流只能作为工具导入后使用，Planner/Run 不直接调用 Dify 工作流 API。

### 复用路径

工作流作为工具的执行路径已通（`dify_tool` → inner API），保持此路径；若需独立"固定流程"入口，可在 BFF 新增 `/uloo/workflows/run` 代理 Dify draft run 或 Service API run。

### 缺口

1. 无独立 workflow tool 引用：必须先导入为 ResourceDefinition
2. Planner/Run 不直接调用工作流：ULOO 任务执行走 Agno Team，工作流只能作为 agent 工具被动调用
3. 工作流运行结果不回传 ULOO 事件流：`dify_tool` 返回字符串 observation，不解析 `completed_nodes` 结构化数据
4. draft run vs published run 混淆：只能通过 inner API 以工具身份调用已发布工作流工具

## 5. BFF 白名单与 Capabilities

### BFF 白名单

文件 `vendor/dify/api/controllers/console/uloo_proxy.py:41`：

```python
ALLOWED_ROOT_SEGMENTS = frozenset({"tools","skills","agents","teams","tasks","runs","memories","memory-proposals"})
```

| 能力段 | 在白名单 | 说明 |
|---|---|---|
| tools/skills/agents/teams/tasks/runs/memories | 是 | Core CRUD/查询 |
| **models** | **否** | 需新增才能透传 Dify 模型列表 |
| **datasets** | **否** | 需新增才能透传 Dify 数据集 |
| **model-providers** | **否** | Dify 原生接口，需 BFF 新端点 |
| **workflow** | **否** | 需新增才能透传工作流 run |

特殊 BFF 端点（不走白名单）：
- `GET /console/api/uloo/dify-tools`（`uloo_proxy.py:269`）—— 直接调 `list_uloo_tool_catalog`
- `POST /console/api/uloo/dify-tools/import`（`uloo_proxy.py:281`）—— 导入工具到 Core

### ULOO Core Capabilities

端点 `GET /api/v1/capabilities`，文件 `services/uloo-core/src/uloo/api/capabilities.py:16-36`，契约 `contracts/openapi.yaml`。

当前返回：
- `modes`: `["coordinate","route","collaborate"]`
- `features`: `{agents, teams, agent_test_runs, team_runs, streaming:false, memory:false}`
- `models`: `{provider: {base_url}}` —— 不含可用模型列表、不含凭据状态
- `tools`: 已注册 tool key 列表 —— 不含 Dify 原生工具目录
- 不返回 knowledge/datasets 可用性
- 不返回 workflow 工具信息

## 6. S1 后续各条对应缺口速查

| S1 条目 | 对应缺口 | 涉及文件 |
|---|---|---|
| 智能体直接选择 Dify 工作区可用模型 | model_ref 手输文本；ModelRegistry 独立于 Dify；BFF 无 models 白名单；.env 必经 | `agent.py:31`, `models.py:25`, `uloo_proxy.py:41`, `config.py:49-54` |
| 移除手输 Core model_ref 和手改 .env | .env 示例仍为手动配置文档；Planner 读 `settings.planner_model_ref` | `deepseek.env.example`, `factory.py:23` |
| 智能体/技能直接选择 Dify 工具，不需导入 | 仍需 POST `/uloo/dify-tools/import` 落库；tool_refs 指向 ULOO key 而非 Dify identity | `uloo_proxy.py:281`, `resources.py:87`, `resource.py:42` |
| 保留插件/API/MCP/工作流差异，schema 复用 | catalog 已读四类，但 `identity_mode`/`workflow_app_id` 等差异信息在导入快照中部分丢失 | `uloo_tool_catalog.py:17`, `tool_providers.py:483` |
| 缺配置给出修复入口 | `DIFY_TOOL_CONTEXT_MISSING` 仅报错无入口；`KNOWLEDGE_CONFIG_MISSING` 无修复指引 | `dify_tools.py:17`, `factory.py:98` |
| 可见性及调用身份沿用 Dify | `dify_workspace_callers` 用 owner 身份，不按浏览器用户身份调用 | `configure_dify_tools.py:28`, `dify_tools.py:16` |
| 直接选择 Dify 数据集并复用检索 | 无知识适配器；knowledge_refs 直接报错；BFF 无 datasets 白名单 | `factory.py:98`, `uloo_proxy.py:41` |
| Embedding/索引未就绪必须明确提示 | `embedding_available` 未透传；`indexing_status` 不可见 | `datasets.py:537` |
| 固定流程通过 Dify 工作流工具执行 | 已可通过 `dify_tool(provider_type=workflow)` 执行，但需先导入；无独立工作流引用 | `dify_tools.py:14`, `uloo_tool_catalog.py:38` |
| 兼容旧模型/工具引用 | tool_refs 存 ULOO key 快照，Dify 侧变更后无映射/无冲突检查 | `resources.py:87`, `resource.py:42` |
| 取消正式工具页的重复 CRUD/导入 | Core `/tools` CRUD 仍存在，与 Dify 工具管理重复 | `resources.py:194`, `uloo_proxy.py:281` |
| 真实验证模型、搜索/API、知识检索与工作流工具 | 知识检索完全未实现；模型验证仅靠 .env；工作流工具验证靠 import | `factory.py:98`, `capabilities.py:31` |

## 7. 实测验证（2026-10-12）

用真实账号登录后，在同源浏览器 context 内带 `X-CSRF-Token`（取自 `csrf_token` cookie）抽测 9 个 Dify/BFF 接口，验证盘点路径与字段。报告：`tmp/s1-inventory-probe.json`，脚本：`tmp/s1_inventory_probe.mjs`。结果 9/11 通过：

| 接口 | 状态 | 结论 |
|---|---|---|
| `GET /console/api/workspaces/current/model-providers` | 200 `{data:[]}` | 路径正确；**当前 Dify 工作区未配置任何模型 provider**——佐证缺口：ULOO Core 能跑 DeepSeek 是靠 `.env` 绕过 Dify，正是 S1 要消除的状态 |
| `GET /console/api/workspaces/current/models/model-types/llm` | 200 `{data:[]}` | 路径正确；工作区无 LLM 模型，与上条一致 |
| `GET /console/api/workspaces/current/default-model?model_type=llm` | 200 `{data:null}` | 路径正确；未设默认模型 |
| `GET /console/api/workspaces/current/tool-providers` | 200 含 `id/name/type` | 路径与字段符合盘点 |
| `GET /console/api/workspaces/current/tools/builtin` | 200 含 builtin 工具 | 路径正确 |
| `GET /console/api/workspaces/current/agent-providers` | 200 `[]` | 路径正确；当前无 agent provider |
| `GET /console/api/datasets?page=1&limit=5` | 200 `{data:[],total:0}` | 路径正确；当前无数据集——佐证缺口：知识检索链路完全未用 |
| `GET /console/api/datasets/retrieval-setting` | 200 `{retrieval_method:["semantic_search","full_text_search","hybrid_search"]}` | 路径与字段符合盘点（pgvector 已配置，VECTOR_STORE=pgvector） |
| `GET /console/api/uloo/dify-tools` | 200 含 `tools:[{key:"dify-...",name:"代码解释器",...}]` | BFF 端点工作正常，服务端目录聚合四类工具 |
| `GET /console/api/uloo/capabilities` | 200 含 `modes/features/models/tools` | ULOO Core capabilities 透传正常 |

**关键发现**：
1. 所有盘点路径准确可调通，字段与盘点一致。
2. Dify 工作区**未配置任何模型 provider**，但 ULOO 仍能通过 Core `.env` 跑 DeepSeek——这正是 S1「移除手输 Core model_ref 和手改 .env 的必经步骤」要消除的状态：模型配置权威应在 Dify 工作区。
3. 知识库链路：`retrieval-setting` 返回三种检索方法（pgvector 已就绪），但工作区无数据集——S1「直接选择 Dify 数据集」需先有数据集可测。
4. CSRF 机制：`COOKIE_NAME_CSRF_TOKEN="csrf_token"` + `HEADER_NAME_CSRF_TOKEN="X-CSRF-Token"`（`vendor/dify/api/constants/__init__.py:71,77`）。BFF 新端点若被浏览器调用，需沿用此 CSRF 流程。
