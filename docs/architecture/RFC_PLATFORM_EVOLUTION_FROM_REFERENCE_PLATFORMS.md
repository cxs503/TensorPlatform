# RFC：参考平台驱动的 TensorPlatform 架构演进

> **状态：Draft / Proposed。** 本文仅用于讨论和规划；owner 确认前不进入实施。本文是架构提案，不代表文中目标能力已实现。

| 项目 | 内容 |
|---|---|
| 日期 | 2026-10-30 |
| 作者 | TensorPlatform contributors |
| 范围 | 工程门禁、项目上下文、科研流水线执行抽象、Agent 接口、特征一致性 |
| 决策 | 待团队确认 |

## 1. 背景与动机

TensorPlatform 的定位是 Tensor 生态的共享数据与 AI 平台，提供身份与授权、数据目录和血缘、实验与模型生命周期、平台集成等能力；README 明确由 TensorPhysics 负责科研工作流执行、求解器适配和数值验收，TensorPlatform 不应变成第二套求解器执行引擎。README 中列出的能力和生态边界是目标定位，不表示每项集成均已生产验证（见 `README.md`）。

当前后端采用 FastAPI。`backend/main.py` 将 IAM、ops、data、model、deployment、IoT/edge、observability、platform、tools、misc、tensor、public 等领域路由聚合后注册；tensor 域提供科研运行台入口。执行实现分布在服务中：例如 `services/ray_trainer.py` 管理 Ray 训练，`services/scheduler_service.py` 维护调度状态并为任务启动 daemon thread；`main.py` 另以 asyncio polling task 驱动定时调度和实时传感器监控。当前并非统一的、可替换的执行后端契约；调度器重启时会把未完成执行标记为失败，而非恢复执行。

工程门禁方面，`.github/workflows/ci.yml` 中 backend suite 只在非 PR 事件运行，并设置 `continue-on-error: true`；前端 type-check 也为 advisory。2026-10-30 检查到的 main CI run 在 contract tests 收集阶段失败：`pgserver` 缺失，另有测试因未安装 `fastapi` 而无法导入（[Actions run](https://github.com/cxs503/TensorPlatform/actions/runs/40994263484)）。这说明需要同时修复测试环境和真实失败、逐步恢复阻断门禁；仅删除 `continue-on-error` 而不先明确、修复测试路径，可能把环境问题变成噪声失败。正在进行的 CI 修复工作应先与本提案对齐。

特征能力也应准确描述：README 已列出特征工程；后端存在 PostgreSQL 特征组/在线值 API（`backend/api/feature_store_v2.py`），并有可选 Feast 集成代码（`backend/services/feast_integration.py`）。因此问题不是“完全没有 Feature Store”，而是目前源码不足以证明特征定义、离线训练取数与在线推理服务之间已经具备统一版本、同一计算语义和可验证的一致性闭环。

Agent 相关方面，仓库已有 resident-community、任务/事件和 Soul Runtime 提案与审计，但未发现平台 MCP Server 实现。Issue #21 定义的是 Claude↔GPT 协作频道及基于 GitHub Issue 的沟通约定；该治理/评审流程与一个面向平台数据的标准机器接口并非同一层，MCP 可减少私有协议式的运行时协作，但不应自动取代审查、授权或证据留存。

本 RFC 的动机是利用成熟平台的实践，明确“当前已有什么、要补什么、先做什么”，而不是以架构愿景替代实现证据。

## 2. 对标分析

| 参考平台 | 关键设计 | TensorPlatform 可借鉴点 | 不照搬的边界 |
|---|---|---|---|
| [ZenML](https://github.com/zenml-io/zenml) | Pipeline 与 Stack（执行/基础设施后端）分离；编排既有工具；提供面向 Agent 的 MCP 交互入口；Agent 评估可独立分层 | 先定义稳定的工作流与运行后端契约，复用 Ray、现有调度服务及外部求解器；以 MCP 暴露有权限边界的平台查询能力 | 不把 MCP 等同于业务能力契约，也不把 TensorPhysics 的求解器职责迁入平台 |
| [MLRun](https://github.com/mlrun/mlrun) | 以可复用的 function / execution 抽象统一运行；强调 Feature Store 与训练/服务特征一致性；将 ML 生命周期纳入 CI/CD | 统一提交、状态、资源、日志和结果记录；把训练/推理特征一致性作为有测试和证据的能力；使 CI 结果真正阻断不合格变更 | 不为“统一”而强制把所有后台任务迁成 serverless，也不将现有功能宣传为已具备一致性保证 |
| [CAIPE](https://github.com/caipe-io/ai-platform-engineering) | Spec-Driven Development；以 `.specify/` 等项目上下文承载宪章、架构和测试约束；规格可衔接 issue；强调 Agent 治理 | 建立可供人和 Agent 稳定读取的项目约束与设计来源；让需求、决策、实现任务之间有可追溯关系 | `.specify/` 文档不能替代运行时记忆、数据库任务状态、权限系统或测试验证 |

## 3. 改进提案（按优先级）

每项均为 Proposed；除 P0 说明与在途工作衔接外，不授权本 RFC 直接实施。

### P0：工程门禁改造

**现状**
`.github/workflows/ci.yml` 的 backend suite 仅在 push、schedule 或手动触发时运行，PR 不运行该 suite；该 job 及前端 type-check 有 `continue-on-error: true`。Backend job 注释记录了待修复的测试缺失/导入问题。最近一次 main CI 的 contract tests 又暴露了测试收集所需依赖未安装的问题。仓库已有 [Agent Engineering Quality Gates](../engineering/AGENT_ENGINEERING_QUALITY_GATES.md) 和实现审计；本提案不是重写它们。

**目标**
让 PR 与 main 上的必需检查给出可解释、可重复、能阻断真实回归的信号。明确区分依赖/服务配置错误、测试失败、跳过和未运行；消除无 owner 或无退出条件的 advisory 门禁。保留耗时或已知遗留项的透明分类，而非用整体绿色状态掩盖。

**迁移路径**
1. 与当前 CI 修复 PR/工作对齐，记录基线 SHA、失败用例、环境依赖和适用触发器；先修复 contract-test 收集依赖（包括 `fastapi`、`pgserver` 所需策略）并验证目标测试。
2. 将无需外部服务的 contract tests 与依赖数据库/服务的 tests 分开配置，确保收集边界清晰；避免只靠降低覆盖范围取得通过。
3. 修复或隔离真实 backend suite 失败后，将经过验证的必需 job 纳入 PR branch protection，并逐项移除 `continue-on-error`；每一项应有责任人、依据和完成条件。
4. 前端 type-check 作为独立债务处理：记录基线，分批修复/收敛后再从 advisory 升为必需门禁。不得把现有遗留告警归因于无关 PR。
5. 报告测试数量、跳过项、失败类型与 SHA；单元测试、部署验证和科学有效性仍分别报告。

**风险**
未经分类就立即启用阻断会造成环境型误报和开发阻塞；过度拆分测试又可能漏掉集成回归。门禁只在代表性、稳定且有维护责任时升级，不能通过删除测试或弱化断言实现“绿灯”。

### P1：建立 `.specify/` 项目上下文目录

**现状**
现有白皮书及 Tensor Soul Contract 都明确区分提案和实现；Soul 审计已指出现有 Agent 身份、记忆、事件与 checkpoint 的具体缺口。项目约束分布在 README、架构文档、审计和 issue 中，尚无一个面向开发 session 的规范化上下文入口。

**目标**
建立小而稳定、由团队维护的 Agent 可读上下文入口，优先涵盖项目边界、术语、证据/测试规则、架构决策索引和工作流。文档只陈述可核实事实，并标注目标与未验证状态。

**迁移路径**
1. 先采用最小 `.specify/` 结构，例如 `memory/constitution.md`（治理约束）、`ARCHITECTURE.md`（职责与边界）、`TESTING.md`（门禁和证据约定）；仅在确有维护需求时再加规格模板。
2. 以 README、`docs/architecture/`、质量门禁文档和 Soul 审计为来源，避免复制整份文档；每条事实链接至权威来源。
3. 明确维护人、审阅时机、事实/提案标记及变更流程；先让 resident Agent 读取并通过 review 验证可用性，再考虑自动加载。
4. 与 Issue #72/#73/#74 的工作协调：`.specify/` 是仓库治理/开发上下文，不是持久 Agent memory kernel 的实现，也不取代其中的数据模型或 benchmark。

**风险**
上下文过长、重复或过时会让 Agent 获得相互矛盾的指令。应保持短小、带来源、定期核对；未经 owner 批准不得把提案写成强制政策。

### P1：Pipeline / Backend 解耦抽象

**现状**
`backend/main.py` 的 12 个领域聚合路由中包含 tensor 科研运行台；后台任务的调度、Ray 训练等实现散布在 services。README 将 TensorPhysics 定义为科研 workflow 执行、solver adapters 和数值验收的 owner。现有 `scheduler_service` 的定时任务类型与 `ray_trainer` 并未构成一个通用且可互换的 pipeline/backend 合同。

**目标**
让工作流定义（步骤、依赖、输入/输出、状态和验证要求）与实际执行后端（例如现有本地/调度/Ray/外部 TensorPhysics）分离；复用现有工具，不重写求解器。平台负责合同、授权、运行登记和可观察性；求解器及科学验收仍归相应领域项目。

**迁移路径**
1. 从 tensor 域科研运行台的一个窄场景盘点现有 API、数据模型、运行状态、产物引用和 `services/ray_trainer.py` / `scheduler_service.py` 调用链。
2. 先定义版本化、可序列化的 Pipeline / Run / Backend capability 合同，覆盖提交、状态、取消（若支持）、日志/事件、产物引用、错误和验证元数据；标记各后端不支持的操作。
3. 用 adapter 包装一个现有执行路径，保持 API 兼容；对比旧路径和 adapter 的运行状态、权限、错误及产物记录。
4. 通过测试和真实环境验收后，再逐步接入其他后端；不在首次迁移中引入新的编排平台或移动 TensorPhysics solver 代码。

**风险**
抽象过早可能冻结不成熟的任务语义，接口泄漏后端特性也会产生伪统一。任务重试、取消、幂等和进程重启恢复具有数据/科学副作用，应逐项定义语义，不能假设任意求解任务可安全重放。

### P2：平台 MCP Server

**现状**
当前 backend 未见 MCP Server 实现。仓库已有 HTTP API、认证/权限体系以及 run、dataset、model 等领域资源；Issue #21 记录的是基于 GitHub Issue 的跨模型协作约定，不等于一个平台工具协议。

**目标**
以标准 MCP 接口提供受授权的 Agent 平台查询入口，优先覆盖 run 状态、数据集元信息、模型版本及验证状态；返回来源和权限可见范围。它是传输/工具适配层，不替代 API、领域合同或协作审查流程。

**迁移路径**
1. 先对照现有 API 与 IAM/RBAC，选定只读、低风险查询工具；明确定义身份传递、租户隔离、审计、分页、限流和错误响应。
2. 编写 threat model 与契约测试，验证未授权、跨租户、敏感字段过滤和资源越权；在首版不提供任意 SQL、shell 或无边界写操作。
3. 实现薄 MCP adapter，复用现有 API/service 与鉴权策略，不复制业务逻辑；以一个受控 Agent client 做集成验收。
4. 若后续需要变更类工具，逐项审批权限、幂等语义、审计和回滚方式。

**风险**
MCP server 会扩大工具可达面和凭据暴露风险；若在 adapter 中绕开现有授权，可能造成跨租户访问。首版应限权、只读并默认拒绝无法证明授权的请求。MCP 的可用性也不能被误认为协作渠道、评审和证据流程已被替代。

### P2：补齐 Feature Store 训练/推理一致性

**现状**
平台已有 `feature_store_v2` API 和 Feast 集成代码，不应从零重复建设。但现有接口提供特征组元数据和在线值读写；代码存在默认/示例 Feast repo 和异常 fallback。仅凭这些源码，尚不能确认生产特征定义版本、离线 point-in-time 取数、线上物化和训练/推理计算语义一致。

**目标**
基于现有实现评估并补齐可验证的一致性能力：特征定义与版本可追溯，训练取数可重现，线上服务使用兼容定义；清楚标记数据时效、缺失值和 fallback，避免把不可用的特征读取误当成功。

**迁移路径**
1. 审计 `feature_store_v2`、相关 schema/migrations、训练数据加载、在线推理和 Feast 可选依赖配置，确认实际调用链及环境启用情况。
2. 定义最小 Feature/FeatureGroup 版本合同与 lineage：实体键、事件时间、数据源、转换版本、owner、训练快照/引用和在线版本。
3. 选一个现有训练与推理用例建立离线/在线 parity 测试，覆盖时间点正确性、缺失/过期特征、版本升级和 Feast 不可用；测试不通过时明确失败或降级状态。
4. 在证据证明缺口且团队确认后扩展 API/持久化；评估 Feast/Feathr 等后端的可插拔性，避免将可选集成误报为默认能力。

**风险**
存量数据与 schema 迁移、在线/离线时间语义、性能和兼容性均可能引入回归；错误的 parity 测试也可能给出虚假保证。需先选定代表性用例并验证原始数据来源，渐进迁移且保留旧消费者兼容。

## 4. 非目标

- 不重写 TensorPlatform 或 TensorPhysics 求解器，不把科学执行职责从 TensorPhysics 转移给平台。
- 不引入新的基础模型训练，不实现通用 Agent 自动修复或无监督共享学习。
- 不在本 RFC 中新增生产代码、配置、MCP 服务、`.specify/` 内容或数据库迁移。
- 不以本 RFC 宣称现有 Feature Store、pipeline、Agent runtime、部署或科学验证已经生产就绪。
- 不以 MCP 取代 HTTP API、IAM/RBAC、Issue/PR 审查、owner 决策或证据链。
- 不承诺特定平台/框架选型、一次性大迁移或未验证的性能/可靠性指标。

## 5. 与现有 Issue 的关系

| Issue | 主题 | 本 RFC 的关系 |
|---|---|---|
| [#21](https://github.com/cxs503/TensorPlatform/issues/21) | Claude Code ↔ GPT 协作频道和证据/审查约定 | MCP 可替代部分私有运行时查询/工具交互，但不替代 Issue/PR 中的角色约定、评审、SHA 绑定和证据要求；属于互补，不应直接关闭该 Issue。 |
| [#45](https://github.com/cxs503/TensorPlatform/issues/45) | 跨仓库质量基线与自主科研闭环 | P0 门禁和 Pipeline/Backend 合同为其提供工程支撑；#45 的实际验证与证据交付范围更广，本 RFC 不替代它。 |
| [#60](https://github.com/cxs503/TensorPlatform/issues/60) | 原生科研 Agent 部署、后台运行和重启恢复验收 | P1 可为执行边界提供设计方向；#60 仍需按真实环境逐项验收，RFC 文档和单元测试都不能替代。 |
| [#72](https://github.com/cxs503/TensorPlatform/issues/72) | 跨 Session、跨模型的 Tensor Soul Runtime | `.specify/` 提供静态项目约束，上下文目录与 runtime identity/session/memory/checkpoint 互补，不替代实现。MCP 是可选调用接口，也不是 Soul Runtime。 |
| [#73](https://github.com/cxs503/TensorPlatform/issues/73) | AIL 上下文语言与记忆内核原型（中文规格） | 与 `.specify/` 在“上下文”措辞上有交集，但关注动态、可检索、有证据的记忆与 benchmark；应明确边界，必要时在规划阶段交叉引用而非合并成同一实现。 |
| [#74](https://github.com/cxs503/TensorPlatform/issues/74) | AIL 上下文语言与记忆内核原型（英文规格） | 与 #73 内容高度对应，建议 owner 确认其双语/跟踪关系；本 RFC 不决定关闭或合并。`.specify/` 不能替代其 memory schema、检索、测量与验收。 |

## 6. 决策状态与开放问题

**状态：Draft / Proposed，等待 owner 和团队确认。** 进入实施前需要决定：

1. P0 是否纳入正在进行的 CI 修复 PR，具体按什么条件将 backend suite 纳入 PR 必需检查？前端 type-check 的清理和升级门槛由谁负责？
2. `.specify/` 的最小文件集、审批 owner 和更新责任人是什么？是否仅由人维护，还是 resident Agent 可提交更新提案？
3. Pipeline/Backend 合同的 owner 应属于 TensorPlatform 还是由 TensorPlatform 与 TensorPhysics 共同维护？首个迁移用例选什么，如何处理失败后重试与部分产物？
4. MCP 首版使用何种认证/身份委托方式？只读工具的资源边界和审计保留策略是什么？Issue #21 哪些流程仍明确保留？
5. Feature Store 现有 API/Feast 集成的实际启用范围、训练读路径和在线推理消费者分别是什么？先补 parity contract，还是先评估并选择实现后端？
6. #73 与 #74 是否为同一工作的双语 Issue？#72 的 runtime、`.specify/` 和 AIL memory 工作由哪些负责人分别维护？
7. 各 P1/P2 项的优先级、owner、验收环境和实施是否需要独立 ADR/RFC？

Owner 批准后，应为具体提案拆分实施 Issue/PR；每项均需有 owner、测试/验收标准和回滚路径。任何实现状态与本文描述不同时，先更新事实基线，再进入相应阶段。
