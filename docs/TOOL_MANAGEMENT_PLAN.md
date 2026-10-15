# Dify 与 Agno 集成决策（替代原工具管理提案）

用户已确认：以 Dify 为管理平台，以 Agno 补齐智能体协同。下面原调研保留作参考，其中独立 MCP 服务管理、凭据管理、工具调试台的建设提案不再执行。

## 当前分工与实施顺序

| 能力 | 复用与补齐方式 |
|---|---|
| 模型、工具、插件、MCP、凭据、知识库、固定工作流 | 复用 Dify 的管理页面、服务和运行器 |
| 智能体与团队定义 | ULOO 保存成员、职责、提示词、工具引用和技能引用；Agno 构建 Agent/Team |
| 动态分工、交接、协作与汇总 | Agno Team 执行；ULOO 展示执行事件和成员产出 |
| 技能 | 保留现有技能绑定，组合指令与工具引用，不复制工具或凭据 |
| 会话、记忆、审批、失败处理和评估 | 接入现有运行记录与任务审批，逐项验收；不能把框架支持当成产品已实现 |

第一步已增加 `tool_type=dify` 的运行时适配：复用仓库已有 `/inner/api/agent/tools/invoke`，覆盖 plugin/builtin/api/workflow/mcp。Dify 负责凭据解密和工具执行，Core 只持有引用及参数 schema。

服务配置：`ULOO_DIFY_API_URL` 指向 Dify API；`ULOO_DIFY_INNER_API_KEY` 与 Dify 的 `INNER_API_KEY_FOR_PLUGIN` 匹配；`ULOO_DIFY_WORKSPACE_CALLERS` 是服务器配置的工作区到 Dify app_id/user_id 映射。不得从模型参数或浏览器任意传入调用身份。Dify 原生接口验证 app 的工作区归属。该服务上下文代表集成账号，不是浏览器用户的登录态。

2026-10-12 已完成本期集成：Dify 工具目录、schema 查看、服务器端导入引用、现有智能体与技能选择器绑定、Dify 原生调用适配和本地工作区服务上下文配置。工具管理页 `/uloo/resources/tools` 提供跳转到 Dify 原生插件、MCP、API、工作流管理页面的入口。某类提供者故障不会阻断其余工具目录；必填手动参数未配置的工具明确提示通过 Dify 工作流封装后使用。

验证：Core 112 项、Dify 目录/导入/BFF 38 项、ULOO 前端 22 项通过；正式前端构建成功，源码 TypeScript 检查通过。真实 DeepSeek 运行了研究员与复核员组成的 Agno Team，团队实际调用 Dify API 工具并返回成员产出；另通过实际任务接口验证生成计划、批准、执行、查询任务和运行事件。任务验收使用真实 PostgreSQL 事务，验收后回滚临时记录；临时 Dify 工具和 HTTP 服务验收后清理。

验收报告：`tmp/dify-agno-team-report.json`（含团队实际工具调用）、`tmp/dify-task-api-report.json`（计划、执行和事件）。本次未自动登录浏览器，未进行登录后的人工操作验收；真实网络工具验收使用本地 HTTP 服务，不能替代外网搜索质量验收。MCP、插件与工作流使用同一个 Dify 原生调用适配，本次真实端到端覆盖 API 工具，其他提供者仍需各自配置后验收。

运行配置可通过 Dify 虚拟环境执行 `scripts/configure_dify_tools.py <workspace-uuid>` 建立；从 `vendor/dify/api` 运行并设置该目录为 `PYTHONPATH`。脚本复用 Dify 原生应用服务创建或复用“ULOO 工具运行上下文”，保留其他工作区映射，不输出服务密钥。调用身份是本地工作区集成账号；新增工作区需另行配置映射。

后续能力仍按需要逐项接入：Dify 知识检索、协作记忆、运行取消与恢复、人工干预、评估。本期未增加这些新功能；原有任务审批和执行记录保持现有流程。

## 原调研（供参考，非实施清单）

日期：2026-10-12。状态：待用户决定是否执行。本次只读取本地代码和 GitHub 来源，未修改业务代码、服务配置或启动外部 MCP 服务。

## 1. 目标与当前状态

目标：用户能在界面完成「接入服务 → 发现工具 → 查看参数 → 独立测试 → 绑定智能体/技能 → 执行 → 查看实际调用记录」。工具描述用于解释用途，参数结构和实际适配器负责调用；技能组织方法、指令及所需工具。

当前源码具备：

- 工具/技能资源 CRUD、版本冲突检查、启停、工作区隔离和删除引用保护。
- web-search 实际搜索适配器；Agent 的 tool_refs/skill_refs 和团队成员执行加载。
- 新增的 MCP SSE/stdio 配置、`/tools/mcp/discover` 以及 Agno Function 调用适配器。它是已有基础，不能再把 MCP 一概称为未实现。
- MCP 测试主要使用模拟 SDK 响应。本次没有确认这层代码是否已在运行服务中部署，也没有运行真实 MCP 验收。

主要缺口：

- 工具库表单仍是搜索配置，没有 MCP 服务连接、发现和导入的界面。
- 没有独立的服务实体；每个 MCP 工具重复保存地址、headers/env。
- ResourceDefinition.to_dict 返回完整 config，运行快照也保存配置。若 headers/env 包含凭证，它们可能回到浏览器、进入快照。需要凭证写入与公开读取分离。
- 只对 list_tools/call_tool 使用 wait_for，连接与初始化没有统一的整体截止时间。
- 缺少 Streamable HTTP、发现分页、参数校验、独立测试、schema 变更提示及工具级持久调用记录。
- 工具函数名只根据 tool_name 生成，同名工具来自不同服务时需要服务命名空间。

本地已安装 Agno 1.8.4、MCP SDK 1.30.0；该 Agno 安装已有 MCPTools 和 Streamable HTTP 支持。不能直接复制依赖新版本 SDK 的上游 main 实现。

## 2. 开源项目与借鉴内容

| 项目 | 已查看的源码/文档 | 借鉴内容 | 在本项目的使用方式 |
|---|---|---|---|
| Dify | [MCP 服务模型](https://github.com/langgenius/dify/blob/main/api/models/tools.py)、[管理服务](https://github.com/langgenius/dify/blob/main/api/services/tools/mcp_tools_manage_service.py) | 服务与工具分开管理；连接配置、凭证加密、重连与工具同步 | 优先复用仓库已有 Dify UI 组件、BFF、工作区和适用的凭证能力；不建立第二套登录系统 |
| MCP Inspector | [ToolsScreen](https://github.com/modelcontextprotocol/inspector/blob/main/clients/web/src/components/screens/ToolsScreen/ToolsScreen.tsx)、[SchemaForm](https://github.com/modelcontextprotocol/inspector/blob/main/clients/web/src/components/groups/SchemaForm/SchemaForm.tsx) | 工具列表、参数表单、执行状态、结果/错误分开展示、目录刷新 | 借鉴调试台交互；第一版保留 JSON 参数编辑，支持常见字段的表单输入 |
| Agno | [MCPTools](https://github.com/agno-agi/agno/blob/main/libs/agno/agno/tools/mcp/mcp.py) | 工具筛选、命名空间、连接生命周期与清理 | 先验证本地 1.8.4 的兼容接口；能直接复用就复用，仅补资源配置映射 |
| Open WebUI | [工具连接实现](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/utils/tools.py)、[项目说明](https://github.com/open-webui/open-webui/blob/main/README.md) | MCP/OpenAPI 多来源工具、统一连接认证、模型绑定 | 借鉴统一工具目录和来源展示；OpenAPI 接入放在后续扩展 |
| MCP Python SDK | [官方说明](https://github.com/modelcontextprotocol/python-sdk/blob/main/README.md) | stdio、SSE、Streamable HTTP 标准传输 | 使用已安装 SDK，不手写 JSON-RPC/MCP 协议 |

这些是基于与本项目需求匹配度的选择，不是星数排名。借鉴功能与边界；直接复用源码前核对选定版本的许可证及依赖兼容性。

## 3. 功能清单

| 功能 | 具体行为 | 优先级 |
|---|---|---|
| 工具目录 | 搜索、按来源/类型/启停/连接状态筛选；展示用途及所属服务 | 一期 |
| MCP 服务管理 | 名称、地址、传输类型、连接测试、编辑、启停、错误原因、最后检测时间 | 一期 |
| 认证与凭证 | 无认证、Bearer/API Key、自定义请求头；密钥只写不回显，可替换/清除；运行快照和日志脱敏 | 一期 |
| 工具发现与同步 | 分页发现、勾选导入、刷新；新增/移除/schema 变更有明确提示，已有绑定不静默丢失 | 一期 |
| 工具详情 | 原始工具名、服务、描述、输入 schema、结果格式、关联智能体和技能 | 一期 |
| 工具独立测试 | 填写参数、调用后端、展示结果/耗时/错误；不必先创建智能体 | 一期 |
| 智能体和技能绑定 | 按服务选择工具；技能所需工具自动合并；停用/缺失引用明确阻止执行 | 一期完善 |
| 工具调用记录 | 保存 run_id、agent_id、工具/服务、配置版本、脱敏参数、结果摘要、耗时和错误；可定位失败步骤 | 二期 |
| 运行可靠性 | 覆盖连接、初始化、调用和清理的超时；服务命名空间；结果大小限制；会话随运行释放 | 一/二期 |
| 本地 stdio 服务 | 受控命令与参数、工作目录、环境变量引用、Windows 子进程清理；不向普通表单开放任意命令执行 | 二期 |
| 配置导入导出 | 导出不含密钥；导入检查冲突、连接引用及 schema | 三期 |
| OpenAPI 工具 | 导入 OpenAPI，选择 operation、凭证、参数映射，进入相同目录和绑定流程 | 三期 |
| OAuth | 需要用户授权的 MCP 服务、token 刷新和失效提示 | 按首批服务需求纳入三期 |
| 工具市场与复杂权限 | 插件商店、组织分级权限、配额/计费、全局任务队列 | 暂不建设 |

不先建设复杂权限体系，但保留现有工作区隔离和必要的凭证/执行边界。对有副作用的工具只允许显式开启；不能对写操作失败盲目自动重试。

## 4. 实施顺序与验收

### 一期：可接入、可测试、可绑定

1. 固定兼容版本，确定首个真实、只读 MCP 测试服务；验证本地 Agno/MCP SDK 的可复用范围。
2. 将共享 MCP 连接与具体工具定义分离。工具引用服务标识和 tool_name；保留原 web-search key 及已有 Agent/技能引用。
3. 处理凭证安全读取和旧 MCP config 的迁移；密钥不进入发现响应、目录、导出及运行快照。
4. 接入 Streamable HTTP，保留已有 SSE 兼容；补 URL/连接边界、完整操作超时、发现分页和同名工具处理。
5. 工具管理新增「服务连接」与「工具目录」，补发现导入、详情和独立测试，完善绑定界面。
6. 更新 Core/BFF 契约、前端类型、测试和文档，构建并部署正式 Web。

验收：界面连接一个真实 MCP 服务并导入工具；无需模型即可测试工具；再绑定 DeepSeek 智能体并完成实际 tools/call。错误参数、无效凭证、服务断开均给出明确错误。密钥不回传；现有 web-search 和技能绑定回归通过。正式浏览器登录后的操作需实测，不能只用源码编译代替。

### 二期：执行可追查、运行可恢复清理

1. Agent/Team 测试与正式运行统一记录工具调用，运行详情可展开查看。
2. 为每次运行绑定配置版本，处理连接释放、超时、取消和安全重试；优先运行期间复用连接，暂不建全局连接池。
3. 在受控配置下验证 stdio，覆盖 Windows 子进程退出及环境变量脱敏。
4. 用市场调研案例验证「搜索 → 读取来源正文（所选服务具备该工具时）→ 标注报价日期/单位 → 审核」，同时展示证据不足的情况。

验收：成功、超时、服务断开、模型未调用工具均能在记录里区分；跨服务同名工具不冲突；连续运行没有遗留进程。报价必须可追溯到来源，不以搜索摘要冒充已核实的现价。

### 三期：按实际接入需求扩展

增加 OpenAPI 导入、无密钥配置导入导出，以及首批服务确实需要的 OAuth；接入具体业务工具后按同一套端到端用例验收。待一期/二期稳定后再决定范围。

## 5. 范围、代价与执行选择

一期涉及 Resource 配置/迁移、资源 API、MCP adapter/registry、Agent/Team callers、Dify BFF/契约、工具管理/绑定 UI、测试、正式构建与文档。二期再涉及 RunEvent、执行器和运行详情。优先复用现有层，不新增调度器，不替换 Dify/Agno。

粗估工作量（不是交付承诺）：一期 3–5 个开发工作日；二期 2–4 个开发工作日；三期根据 OpenAPI/OAuth 服务需求再评估。真实服务认证、Windows stdio 兼容和旧凭证迁移会影响工作量。若由当前智能体执行，按阶段持续完成并报告验收结果，不按此估算承诺日历日期。

建议先批准一期。它解决工具管理的主要可用性缺口，并设置明确的停止点；验收后再决定二期。

可选决定：执行一期；执行一期和二期；修改功能清单后再执行。本提案不构成已实现或已验收的声明。
