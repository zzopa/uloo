# ULOO V2

Unified Logic Orchestration & Operations：以 Dify 源码作为唯一可视化产品壳，以 Agno 作为唯一多 Agent 协同运行时，以 ULOO Core 管理 Agent、Team、Run、Event 与跨 Agent Memory。

当前状态：规划与干净基线阶段，尚未宣称任何业务能力完成。

实施与接口定义见 [docs/ULOO_V2_IMPLEMENTATION_PLAN.md](docs/ULOO_V2_IMPLEMENTATION_PLAN.md)。

## 不可变原则

- 必须从固定版本的 Dify 源码运行和构建 Web/API，不能用官方成品镜像冒充源码开发成果。
- Dify Workflow 负责外层可视化流程；一个 ULOO Team 节点对应一次明确的 Agno Team Run。
- Agno 是唯一的 Team 成员调度器，不再自建第二套 route/coordinate/collaborate 循环。Team mode 取值以 Agno 运行时的 `Team.mode` 为准（当前 Agno 1.8.4 为 `coordinate`/`route`/`collaborate`），单一事实源见 `services/uloo-core/src/uloo/constants.py`。
- FakeModel 只允许存在于自动化测试，生产和手工验收不得静默使用假模型。
- MVP 不先引入 NATS、MinIO、独立 Worker 等非必要组件；主链路通过后再按可靠性需求增加。
- 不开发第二套 Workflow Canvas，不扩展独立用户体系，复用 Dify 的登录与 workspace 上下文。

