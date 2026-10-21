# AI-Native SDLC 对照审计与 TensorPlatform 落地路线

- 审计日期：2026-10-21
- 目标仓库：`cxs503/TensorPlatform`
- 基线：`main` 分支当前可见源码
- 参考方法：Anthropic Claude Academy, [The AI-Native SDLC Playbook](https://academy.claude.com/courses/ai-native-sdlc-playbook)
- 状态：代码审计与落地计划；**不是生产部署验收报告**

## 1. 结论

TensorPlatform 已具备 AI-Native SDLC 的不少基础构件：持久化开发任务、常驻 Agent 守护、会话事件审计、人工验收、预算约束、研究运行记录、RunManifest、产物哈希和质量门禁文档。当前最重要的工作不是复制一套新的 Harness，也不是再造一个任务系统，而是明确并验证现有系统之间的边界、关联关系和恢复语义。

科研平台必须在传统 SDLC 之外增加独立的数值与科学验收层。软件测试通过、任务进程退出码为零、产物 SHA-256 正确，只能分别证明有限的软件行为或数据完整性，不能单独证明科学结果正确。

## 2. 已核实的现有构件

| 构件 | 当前代码/文档证据 | 审计判断 |
|---|---|---|
| 开发任务状态机 | `backend/api/tensor_missions.py` | 已有 queued/claimed/running/awaiting_acceptance/succeeded/failed/stopped 等状态、心跳、预算、停止、reclaim 和子任务分发 |
| Agent 守护与 worktree | `tools/agent/tensor_agent.py`、`tools/agent/README.md` | 已有任务轮询、隔离 worktree、会话事件桥接、预算看护和人工验收流程 |
| 守护恢复 | `tools/agent/README.md` | 文档明确：daemon 重启后，在飞任务可能被收割为 agent_lost；不做同一会话检查点恢复 |
| 本地真实数值任务 | `backend/api/research_factory.py` | 有 SQLite 持久化的本地 CPU SpringChainBenchmark、CSV 输出和 RunManifest；不是冰破 DEM |
| 本地任务执行方式 | `backend/api/research_factory.py` 的 `POST /tasks/{run_id}/run` | 计算在 HTTP 请求处理路径中同步执行；需要评估长任务超时、请求断开与进程重启的影响 |
| 跨项目运行注册 | `docs/research-loop-integration-contract.md`、`docs/tensorphysics-run-registry.md` | 已有 RunManifest、组织边界、幂等注册和执行状态/验证状态分离；文档指出完整嵌套 schema、远程 artifact 可达性及科学正确性并未由该注册端点证明 |
| 工程质量门禁 | `docs/engineering/AGENT_ENGINEERING_QUALITY_GATES.md` | 已有风险分级、回归、科学 benchmark、证据包、失败归因和语义扫描器接入流程 |

## 3. 核心架构问题

### 3.1 三类状态不可混为一谈

必须分别保留：

1. **开发任务状态**：Agent 是否领取、运行、提交代码、等待人工验收。
2. **科研运行状态**：求解任务是否排队、执行、成功、失败、取消或超时。
3. **科学验证状态**：验证是否未运行、通过、失败、跳过或结论不充分。

它们应通过明确的关联标识关联，而不是强行合并成一个状态字段。建议至少保存 `mission_id`、`research_task_id`、`run_id`、`session_id`、仓库与 commit SHA；没有对应实体时字段应为空，而不是编造 ID。

### 3.2 数据库不应被直接合并

当前可见代码包括 PostgreSQL 的 `tensor_dev_missions` 与 SQLite 的 `research_runs`。它们服务于不同生命周期。第一步应定义跨系统关联与事件/manifest 契约，验证授权和幂等行为；只有在有明确迁移方案、并发模型和回滚策略时才考虑存储合并。

### 3.3 运行完成不等于科学验证通过

SpringChain 当前的 finite-state 检查仅能证明记录状态为有限数值。现有 manifest 使用 `validation_status=not_run` 是诚实且正确的。任何 UI、API、模型训练数据集或资产晋级流程都不得把这种结果展示成“科学验证通过”。

### 3.4 产物完整性与产物有效性是不同属性

SHA-256 与字节数可以帮助发现文件变化，并支持溯源；它们不证明计算方法正确，也不证明文件 URI 一定可访问。完整流程应分别验证：manifest schema、实际字节哈希/大小、访问权限、引用可达性，以及独立的数值验证报告。

### 3.5 GitHub 状态不能替代目标服务器验收

仓库代码、PR 与 CI 只能证明对应 commit 的源码和 CI 状态。要声明后台服务持续运行、重启恢复有效或真实求解器闭环成功，必须有目标服务器的部署 commit、健康检查、任务记录、进程退出码、验证报告和产物证据。本审计未访问用户服务器，因此不对生产部署状态作判断。

## 4. 按风险排序的落地路线

### P0 — 跨系统关联与证据契约

- 明确开发任务、科研运行和科学验证各自的权威状态源。
- 定义可选关联字段、状态词汇、attempt/retry、failure class、退出码、开始/结束时间、复现命令与 artifact manifest。
- 为同一请求重复提交、同一 ID 不同内容、失败/取消状态可查询、未验证数据禁止晋级等场景补充正反测试。
- 不改变现有数据库所有权；通过明确的适配器/契约逐步集成。

### P1 — 后台执行与恢复

- 将长耗时科研任务从同步 HTTP 请求移到持久化 worker。
- 明确 lease、心跳、超时、取消、有限重试、幂等键和 worker 崩溃恢复语义。
- 测试请求断开、worker 被杀、重复 claim、过期 lease、磁盘写入失败及重复注册。
- 将“重新排队”“从头重跑”“从检查点恢复”作为不同策略明确记录，不应把它们称为同一种恢复。

### P2 — RunManifest 与质量门禁

- 服务端执行版本化 JSON Schema 校验，并对状态枚举、必填字段、哈希格式、大小上限及序列化失败进行负例测试。
- 对本地 artifact 检查真实文件、大小与 SHA-256；远程对象由具备权限的存储适配器验证可达性。
- 分开记录软件回归、数值收敛/守恒、公开 benchmark 和应用级验证。
- 验证报告需保留方法、阈值、测量值、基线和产物引用；阈值应在查看新结果前声明。
- `not_run`、`failed`、`incomplete`、`skipped` 或 `inconclusive` 不得被提升为已验证科学数据。

### P3 — 真实部署验收

- 在用户实际部署服务器执行健康检查、worker 重启恢复测试及至少一个真实 solver benchmark。
- 保存部署 commit、命令、环境/资源信息、退出码、验证报告与 artifact SHA-256。
- 服务器访问未建立或证据未读取时，明确写作“未验证”，不得用 mock 状态替代。

### P4 — Agent 安全与运行治理

- 基于威胁模型收紧 Agent 的仓库、文件系统、网络和凭据权限。
- 优先采用短期凭据、最小权限、受控工作区、资源预算与显式审批。
- 处理共享虚拟环境可写、宿主进程级隔离等已知限制；不通过授予无约束 SSH 或生产写权限来解决自动化问题。
- 将新语义扫描器先以 advisory 模式验证精度，再决定是否阻断合并。

## 5. 最小端到端验收定义

一次可信的科研闭环至少应提供：

- 任务目标、约束和验收标准；
- 可追踪的仓库/commit、求解器版本、参数和运行环境；
- 真实执行记录、开始/结束时间、退出码和失败原因；
- 原始结果与机器可读 RunManifest；
- 独立验证方法、预先声明的容差、实测值和验证结论；
- 产物路径/URI、字节数与 SHA-256；
- 失败时可查询、可诊断、可恢复；通过验证后才允许晋级为可复用科研资产。

## 6. 审计限制

本报告基于 GitHub `main` 中可读取的源码与文档。它没有执行服务器命令、没有读取生产数据库、没有运行真实求解器 benchmark，也没有证明用户服务器当前部署版本与 GitHub `main` 一致。上述事项必须由后续目标环境测试补齐。
