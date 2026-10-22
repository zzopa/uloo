# ULOO 界面与功能设计稿（基于 ui-ux-pro-max 技能）

更新时间：2026-10-21。范围：以已安装的 `ui-ux-pro-max` 技能引擎推导的设计系统为锚，对 `vendor/dify/web/features/uloo/` 现有界面做功能与视觉设计。设计稿不是改代码的指令，是「改之前先看设计依据」的起点。

## 0. 设计系统锚点（技能引擎输出）

引擎命令：
`engine.py generate "ULOO collaborative AI agent team workspace SaaS dashboard for enterprise, tasks teams agents runs skills integrations" --stack nextjs --mode light`

引擎匹配：产品类型 **SaaS / B2B**（置信分 11.133）。落地设计取以下结论：

| 维度 | 结论 | 依据 |
|---|---|---|
| 风格 | Minimalism 为主，Bento Grid 承载任务卡片与运行列表 | `products "b2b collaboration platform"`：SaaS/B2B 推荐 Minimalism + Bento；`search "bento grid control panel admin workspace"`：Bento 8.4 分命中 |
| 配色 | Calm Triadic #13：primary `#4475A7`、accent `#337FCC`、bg `#F6F7F9`、surface `#FFFFFF`、border `#D7E0EA`、text `#16181D` | 引擎自动选基础色相最接近者，正文/背景对比度 16.57:1 过 WCAG AA（>=4.5） |
| 字体 | Inter（标题 600/700，正文 400/500） | 引擎 `type_mood=neutral-modern` 匹配 |
| 关键效果 | bento cards、subtle gradients、clean tables | 引擎 Minimalism + Data/Dashboard 风格叠加 |
| 反模式 | 勿「feature overload」、勿 generic blue、须保留单一主 CTA | 引擎 SaaS/B2B 反模式清单 |
| P0 门禁 | UX-001/002/003/004/005（可访问名、对比度、不单靠颜色、键盘可达、焦点环） | 引擎 guidelines accessibility |

暗色校准（引擎 `palettes --mode dark`）：Calm Complementary #157，primary `#759CC7`、accent `#337CCC`、bg `#12171C`、text `#EDEFF3`——ULOO 现有 dark 主题可参照此组做暗色 token，不必另造。

> 注：引擎是本地方法论推导，非审美终审。配色/字体是「推荐起点」，落地前按 Dify 既有 design token（`components-panel-border`、`text-*`、`badge-status-*` 等）做映射，不破坏 Dify 主题系统。

## 1. 现有界面状态盘点（已读代码）

- **导航壳** `features/uloo/shell.tsx`（255 行）：桌面侧边栏 + 窄屏 header 横滚 nav；四组「工作（开始新任务/全部任务）/智能体团队/智能体/集成」+ 底部「运行环境」。窄屏只渲染 `allItems`（不含组内重复），与桌面侧边栏有重复项（组 label 在窄屏丢失）。
- **任务中心** `features/uloo/dashboard.tsx`（387 行）：`/uloo` 根路由。左侧 hero（任务输入 + 团队选择 + 约束 + 示例 chips），右侧 aside（4 状态计数 + 流程 4 步），下方面板「最近任务」bento 卡片。预览态用 `ULOO_PREVIEW_TASKS`。
- **任务详情** `features/uloo/task-detail.tsx`：四阶段进度（团队规划→等待确认→多 Agent 执行→汇总交付）+ 运行记录列表 + 任务 meta。
- **运行详情**（`runs/[run_id]`）：成员执行与结果、事件流。
- **资源页** `agents/teams/tools/skills`：列表 + 编辑 dialog（`agent-form-dialog.tsx` model_ref 已改 select，技能/工具勾选器 `tools/picker.tsx`）。
- **集成设置** `integrations/settings`：复用 Dify 原生模型/工具/MCP/知识/工作流入口。

**功能覆盖良好，主要槽点在视觉一致性与信息密度**：
1. dashboard 右侧 aside 与下方「最近任务」信息重叠（4 状态计数在 aside 和主区都出现）。
2. 状态色靠 `STATUS_TONE` 映射 Dify 现成 token，但 `cancelled` 用 `bg-background-section-burn` 与正常空态色易混。
3. 窄屏 nav 丢失分组语义（组 label 消失），「运行环境」浮在最末，权重与「开始新任务」不同但视觉同级。
4. 无深色 token 映射（ULOO 自身无 dark 色值，全依赖 Dify `dark:` 前缀）。
5. 运行详情事件流若成员多会「feature overload」——引擎反模式第一条。

## 2. 功能设计（按「协作智能体工作台」重排，不改主线）

### 2.1 导航/信息架构
- 桌面侧边栏保持四组 + 运行环境；**窄屏 header 增加组分组徽标**（开始新任务、全部任务归「工作」；运行环境降权为 footer 图标入口，不占 nav 槽位）。
- 「运行环境」改为**悬浮状态胶囊**固定在内容区右上角（显示 Core 健康 + 当前 provider），与 nav 解耦，权重匹配其真实重要性（诊断入口）。

### 2.2 任务中心 `/uloo`（对应 S4 体验主线）
- 左侧 hero 区：保留任务输入 + 团队 + 约束；**示例 chips 改 2×2 bento**（引擎 Bento 命中），每个 chip 带产物预览缩略（价格调研表、来源列表等），降低「填什么」的冷启动成本。
- 右侧 aside：**合并 4 状态计数进「进行中」单卡**（运行中 N · 待确认 N · 成功 N · 失败 N 一行），下方 4 步流程改成**可点击跳转**（点「多 Agent 执行」直接跳最近一个 running 的 run）。
- 下方「最近任务」卡片：保留 bento，但**状态色规范**——`cancelled` 改用 `text-text-quaternary` 灰，不再用 `section-burn`；`succeeded/running/failed` 维持 Dify badge token，加 `aria-label`（P0 UX-001）。

### 2.3 任务详情（对应 S5 可追溯运行）
- 四阶段进度条：阶段点可点击直接定位到对应事件段；**失败阶段红色徽标 + 可操作处理按钮**（重试/补查/取消），呼应 S5「可操作失败」。
- 运行记录列表：每条带「成员 N · 耗时 · 结果状态」摘要行，**超过 3 条折叠**（防 overload），展开才看成员产出。
- 结果区：报价表/日期/单位/来源 用 clean table 呈现（引擎「clean tables」），不确定事项灰字标注。

### 2.4 资源页（智能体/团队/工具/技能）
- 列表统一 bento 卡片，每卡右上角状态开关；编辑 dialog 保留现 `UlooToolPicker` 勾选模式。
- 智能体编辑 model 选择器（已实现）下方加一行**凭据状态提示**（provider 是否有 key），缺口给修复入口（呼应 S1「缺配置给出修复入口」）。
- 工具/技能库：停用项灰显但保留（避免静默丢配置），缺失依赖红字阻断保存。

### 2.5 集成设置
- 复用 Dify 原生入口，**只读预览**避免正式写操作；每项能力（模型/工具/MCP/知识/工作流）展示「已配置/缺配置/不可用」三态徽标 + 修复链接。

## 3. 视觉落地清单（最小改动、保留 Dify 主题）

| 项 | 动作 | 依据 |
|---|---|---|
| 配色 | 不新增色值，仅把 `STATUS_TONE.cancelled` 改 `text-text-quaternary`；hero 光晕 `bg-util-colors-blue-blue-500` 保留 | 引擎反模式「勿 generic blue」已满足（Dify 用主题色非硬蓝） |
| 字体 | 沿用 Dify 默认（Inter 系），不引入新字体 | 引擎 Inter 与 Dify 现有一致 |
| 对比度 | 正文 16.57:1 过 AA；小字 `text-[10px]` 计数标签需复核（引擎 P0 对比度须真机实测） | UX-002 |
| 可访问名 | 给 `taskCards` 的 Link 加 `aria-label={任务标题 + 状态}`；示例 chips 已有 `sr-only` 可补 | UX-001 |
| 焦点环 | dashboard 任务卡 hover 已有 `focus-visible:outline`，保留；aside 流程步骤补 `focus-visible` | UX-005 |
| 窄屏 | nav 补分组徽标；运行环境改 footer 图标 | UX-023/024 |
| 动效 | 任务卡 `hover:-translate-y-0.5` 保留；尊重 `prefers-reduced-motion` | UX-008 |

## 4. 交付前 QA 门禁（引擎 P0，对应 ULOO 现有代码）

- [ ] 所有交互元素有可访问名（`taskCards` Link、示例 chips、aside 流程、资源卡开关）
- [ ] 正文对比度 >= 4.5:1（`#16181D` on `#FFFFFF` = 16.57 过；小字 10px 标签需 axe 复核）
- [ ] 状态不单靠颜色（`STATUS_TONE` 旁已带文字 label，过；`cancelled` 色改灰后确认与空态区分）
- [ ] 键盘可达（tab 序：hero 输入→示例 chips→团队→约束→卡片）
- [ ] 焦点指示（任务卡、chips、aside 流程）
- [ ] 窄屏无横向滚动、触控目标 >=44px

## 5. 与 TODO 主线的对应

| 设计项 | TODO 主线 |
|---|---|
| 任务中心 bento + 示例预览 | S4 连续任务体验 |
| 任务详情可操作失败 + 事件折叠 | S5 可追溯运行与可操作失败 |
| 资源页凭据状态修复入口 | S1/S2 智能体岗位与技能 |
| 集成设置三态徽标 | S0 集成设置 |
| 视觉 token 映射（不改 Dify 主题） | 不可变原则（Dify 唯一壳） |

> 设计稿是「改之前的依据」，不是代码改动本身。若要落到 `dashboard.tsx`/`shell.tsx`/`task-detail.tsx` 改代码，需逐文件做、跑 ULOO 24 项前端测试 + 类型检查 + 重新构建，并按 S4/S5 验收门留浏览器证据。
