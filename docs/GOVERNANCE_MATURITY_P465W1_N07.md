# 平台治理成熟化：度量 × 契约源 × 场交换 的端到端集成验证（节点 p465w1-N07）

> **一句话结论**：把 N01（度量工具 pulse/chronicle/portrait）、N02（三契约源：任务状态词表 /
> 事件流词表 / 运行登记词表）、N03（跨库场交换契约 v1→v2 + 迁移表）接成一条可跑的治理链路后，
> 契约一致性 16 项审计 **diff 全 0**，100 agent × 1000 心跳的端到端链路 **丢失率 0、
> 画像一致性误差 0%、P99 单 tick 延迟 1.792 ms**；并用故障注入正对照证明
> "0 丢失"不是检测器失灵。同时如实登记 3 条能力缺口（见 §⑧）。
> 全部数字由 `scripts/run_governance_maturity.py` 程序化注入，工件在
> `artifacts/governance-maturity/acceptance.json`（sha256 可复核）。

## ① 案例介绍：要治理的是什么

**机制背景（非学科）**。平台有两条"事实流"：一条是**词表**（任务状态、会话事件种类、运行登记
状态，各自有正典 JSON 契约），一条是**量**（pulse 的饱和快照、chronicle 的报表 token、portrait 的
worker 画像）。两条流此前各自演进：词表改动靠人脑同步，度量工具的事件种类散落在 SQL 字符串里，
跨库场交换的端口契约有 v1/v2 两代却没人把"接口信封"与"契约端口"对过账。治理成熟化的判据因此
不是"文档写得好"，而是**能不能机器算出差集**。

**被集成的三个面**（本节点不改它们的内部实现，只做集成与对拍）：

| 面 | 组件 | 本次集成的接入点 |
|---|---|---|
| N01 度量 | `tools/pulse/saturation_pulse.py`、`tools/chronicle/**`、`tools/worker_portrait/portrait.py` | 源码 kind/表名扫描 + 运行时真跑 `compute()`/`aggregate()` |
| N02 契约源 | `backend/contracts/{mission_status_vocab, session_event_kinds, research_run_status_vocab}.v1.json` | 统一装载面（含 sha256 指纹）、事件流 kinds/governance_members |
| N03 场交换 | `contracts/coupling/v1,v2/*.json`、`coupling-map.v2.json`、`migrations.v1_to_v2.json`、`frames.v1.json` | 版本化解析、端口量纲翻译、信封构造、迁移可执行性 |

**规模与参数表**（端到端链路，全部合成数据，无实测几何）：

| 参数 | 值 | 说明 |
|---|---|---|
| agent 数 | 100 | 合成 agent id `agent-0000…` |
| 心跳次数 | 1000 | 每 agent 每 tick 一个 `note/heartbeat` 事件 |
| 事件总数 | 108000 | = 心跳 100000 + 工单生命周期 8000 |
| 工单节奏 | 每 50 tick 开一个，长度 4 | 覆盖 run_registered→execution_started→validation_seen→decision |
| 场交换插入 | 每 100 tick | 量纲契约校验 + 同网格转写 |
| 丢包注入（正对照） | 0.0%, 0.2%, 0.5%, 1.0%, 2.0%, 5.0% | 检测器灵敏度曲线 |
| 随机种子 | 20261111 | 同种子结果逐位一致（有测试钉住） |

## ② 怎么做的

1. **统一契约装载面**（`src/tensorplatform/governance_maturity/contracts.py`）：三契约源 +
   度量事件声明 + 场交换契约/帧/迁移表一次读入，每个文件记 sha256。缺失或损坏 → `ContractError`
   **fail-closed**，绝不降级成"没有契约"（否则审计会假绿）。
2. **度量-契约审计 M1–M8**（`metrics_audit.py`）：把"度量工具输出的事件类型与契约词表一致"
   实现成**源码字面量 vs 声明 vs 契约**三方对拍——正则扫出 `kind=…`、`payload->>'subtype'=…`、
   表名字面量，与 `contracts/metrics_emission.v1.json` 双向 diff，再要求声明的会话事件种类
   ⊆ `session_event_kinds.kinds`、治理消费者 ⊆ `governance_members`；外加**运行时真跑**
   `pulse.compute()` 校验 payload/row 键集。声明由 `bootstrap_declaration()` 从现状生成、
   人工审后提交，其价值是把**未来漂移**变成可算 diff。
3. **场交换-契约审计 F1–F8**（`field_audit.py`）：jsonschema 校验 9 个版本化契约；自建单位
   解析器把 72 个端口单位翻成量纲（`kW→M·L²·T⁻³`、`kn→L·T⁻¹` …），逐条耦合边比两端口量纲；
   版本化解析（同 id 取最高版本）后检查端口存在性；`tensor_order>=1` 的端口必须落在 frames
   登记表；每个端口都真构造 `FieldMessage` 并过 `validate()`；最后检查迁移表 rename 条目在
   v1/v2 端口集合里**真实可执行**。
4. **端到端链路**（`e2e.py`）：一个 tick 内完成 投递判定 → 逐事件契约校验 → 画像流式更新 →
   （按节奏）场交换量纲校验 + 转写积分一致性；`perf_counter` 包住真链路取延迟分位。
5. **正对照**（关键）：同一链路注入 5% 丢包，检测器必须报出
   同量级丢失且画像误差必须变大——否则"0 丢失 / 0 误差"没有判别力。
6. **工具真跑回归**：把账本重放成工单行交给真 `portrait.aggregate()`，用链路末态交给真
   `pulse.compute()`，比对工单数/领取数/在线数/忙碌数。

## ③ 计算结果（数字表，全部程序化注入）

**判据 (a) 度量-契约一致性**：总 diff = **0**

| 审计项 | 说明 | diff | 状态 |
|---|---|---|---|
| `M1_declared_tools_exist` | 声明中的度量工具目录必须存在 | 0 | PASS |
| `M2_event_kinds_in_contract` | 工具声明触及的会话事件种类必须全部在契约词表内 | 0 | PASS |
| `M3_event_kinds_scan_parity` | 源码扫出的契约命名空间 kind 字面量必须与声明一致（双向） | 0 | PASS |
| `M4_internal_kinds_parity` | 工具自有 kind 命名空间必须与声明一致（双向） | 0 | PASS |
| `M5_payload_subtypes_parity` | payload subtype 字面量必须与声明一致（双向） | 0 | PASS |
| `M6_table_surface_parity` | 源码触及的表名字面量必须与声明一致（双向） | 0 | PASS |
| `M7_pulse_runtime_payload` | 运行时 pulse.compute 的 payload/row 键必须与声明一致（双向） | 0 | PASS |
| `M8_governance_membership` | 治理消费者工具的事件种类必须是契约 governance_members 的子集 | 0 | PASS |

**判据 (b) 场交换-契约一致性**：总 diff = **0**

| 审计项 | 说明 | diff | 状态 |
|---|---|---|---|
| `F1_schema_conformance` | 每个版本化耦合契约必须通过契约 schema（错误数=0） | 0 | PASS |
| `F2_unit_parse` | 全部端口单位必须可翻译成量纲（失败数=0） | 0 | PASS |
| `F3_edge_dimension_parity` | 耦合边两端端口量纲必须相等（失配数=0） | 0 | PASS |
| `F4_edge_port_resolution` | 已发布契约的耦合边端口在版本化解析后必须存在（缺失数=0） | 0 | PASS |
| `F5_frame_registry` | 端口声明的 frame 必须在 frames 登记表内（违规数=0） | 0 | PASS |
| `F6_envelope_build` | 每个端口都能构造通过 FieldMessage.validate() 的信封（失败数=0） | 0 | PASS |
| `F7_version_monotonic` | 同一 contract_id 的新版本号必须严格大于旧版本（违规数=0） | 0 | PASS |
| `F8_migration_rename` | 已发布契约的迁移 rename 条目必须在 v1/v2 端口集合里真实可执行（违规数=0） | 0 | PASS |

**判据 (c)(d) 端到端链路（nominal，100×1000）**

| 量 | 值 | 判据 | 结论 |
|---|---|---|---|
| 事件丢失率 | 0.000000 | = 0 | **PASS** |
| 画像一致性误差（逐 agent 最大） | 0.0000% | < 5% | **PASS** |
| 画像分项（events/missions/claims/closed） | 0.0000% / 0.0000% / 0.0000% / 0.0000% | — | 一致 |
| 画像工具对拍（真 `portrait.aggregate`） | 不一致 worker = 0，最大计数差 = 0 | = 0 | **PASS** |
| pulse 运行时对拍 | online=100 / busy=10 / running=10 | 与期望一致 | **PASS** |
| 事件种类契约违规 | 0 / 校验 108000 条 | = 0 | **PASS** |
| 链路内场交换 | 10 次，量纲违规 0，同网格积分误差 0.0e+00 | = 0 | **PASS** |
| 单 tick 延迟 P50/P99/Max | 0.099 / 1.792 / 3.637 ms | P99 < 5000 ms | **PASS** |

**正对照：故障注入扫描**

| 注入丢包率 [%] | 检出丢失率 [%] | 检出/注入 | 画像误差 [%] | 受影响 agent 数 |
|---|---|---|---|---|
| 0.00 | 0.000 | 1.000 | 0.000 | 0 |
| 0.20 | 0.211 | 1.056 | 5.000 | 91 |
| 0.50 | 0.489 | 0.978 | 10.000 | 99 |
| 1.00 | 0.985 | 0.985 | 10.000 | 100 |
| 2.00 | 2.049 | 1.025 | 15.000 | 100 |
| 5.00 | 4.988 | 0.998 | 25.000 | 100 |

**判据 (f) 测试与 API 面**

| 项 | 值 |
|---|---|
| pytest 用例数（`tests/governance_maturity`） | 57 |
| API 端点 | 4 条路由（/contracts、/audit、/e2e-run、/report） |
| 非法输入 422 | True（`scope=["bogus"]`、`num_agents=0` 均 422） |
| API 实打状态码 | {"GET /contracts": 200, "POST /audit": 200, "POST /e2e-run": 200, "GET /report": 200, "POST /audit[bad scope]": 422, "POST /e2e-run[agents=0]": 422} |

## ④ 结果图

![图1 丢包检测灵敏度](../artifacts/governance-maturity/figures/fig1_loss_detection.png)

*图 1*：注入丢包率 vs 检出丢失率，与 y=x 参考线重合 → 检测器既不漏报也不虚报。

![图2 画像一致性退化](../artifacts/governance-maturity/figures/fig2_portrait_error.png)

*图 2*：画像一致性误差随丢包单调退化，5% 判据线在 2% 丢包时被突破——这正是"nominal 下 0%"
有意义的原因（有判据、有退化、有正对照）。

![图3 延迟分布](../artifacts/governance-maturity/figures/fig3_latency.png)

*图 3*：单 tick 链路延迟 P50/P99/Max 与 5 s 预算（对数轴）。

![图4 契约一致性 diff 矩阵](../artifacts/governance-maturity/figures/fig4_audit_matrix.png)

*图 4*：16 项审计的 diff 全 0（M1–M8 + F1–F8）。

![图5 链路漏斗](../artifacts/governance-maturity/figures/fig5_chain_funnel.png)

*图 5*：nominal 链路漏斗——发出 = 落账 = 画像可解释，工具对拍行数一致。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **"conservative" 名不副实（STUB，本节点首次量化）**：
   `FieldExchange._conservative_map` 直接回落 nearest 映射（源码注释 "For now"）。
   实测 4×4→3×3 面量交换的积分量（Σ value·area）相对误差
   **9.80%**
   （8.5000 →
   7.6667）。
   因此本节点**不**把场交换的"守恒"标签挂在它身上：链路内只验证接线（量纲契约 + 同网格恒等
   转写），守恒语义需要 N02 的保守重映射实现另行背书。
2. **上游契约缺口（MEDIUM ×2）**：`coupling-map.v2.json` 引用了仓库内**未发布**的
   `solver.seakeeping` / `solver.manoeuvring` 契约；`migrations.v1_to_v2.json` 为
   `solver.seakeeping` 声明了 rename 迁移但仓库既无其 v1 也无 v2 文件。处理方式：把这两类
   **未发布引用**与**契约内违规**分开计数（F4/F8 的 diff 只统计已发布契约），并把缺口集合钉死
   在测试里（`test_upstream_gaps_are_pinned`）——缺口只能缩小，不能悄悄变大。
3. **静态扫描的边界**：M3–M6 只扫 `*.py`，`tools/**/*.sql` 与运行时动态拼接的 kind 不在覆盖内；
   声明是从现状 bootstrap 的，它的作用是**防漂移**而不是"证明历史正确"。
4. **零丢失是设计属性，不是运气**：链路是本仓内的同步总线（无网络、无重试），所以"丢失率 0"
   证明的是**记账完整性**（逐个 seq 连续、无重复），不证明真实 MQ/DB 部署下的端到端投递。
   真实部署的丢包检测器灵敏度由本节点的正对照给出上界参考（检出/注入 ≈
   0.998）。
5. **延迟口径**：P99 是**单 tick 的进程内处理时间**（含契约校验、含按节奏插入的场交换），
   不含 API 网络往返与数据库 IO；与"API P99"不可混用。
6. **同名模块风险（本节点踩过的坑，已加护栏）**：`backend/api/governance_maturity.py` 在本节点
   之前已存在（m8339 的报表门 / 证据链 / 能力台账 / 自愈**库**，没有 router）。第一版实现直接
   写了同名文件，会把这 843 行的库整体覆盖，而仓库里没有任何测试 import 它 → 全绿掩盖删除。
   现改为 `backend/api/governance_maturity_integration.py`，并加回归护栏
   `tests/governance_maturity/test_api.py::test_library_module_coexists_with_integration_api`
   （同时断言库模块的四个能力仍在、集成模块不是它）。教训：**新增"面"之前先 grep 同名"库"**。
7. **`backend/tools` 影子包（既有环境问题，非本节点引入）**：`backend/tools/` 与仓库根 `tools/`
   同名，pytest 把 `backend` 插入 `sys.path` 前部时 `import tools.contracts` 会解析到
   `backend/tools` 而失败（`backend/tests/test_status_contracts.py` 的 4–6 例因此红）。本节点未改
   该结构（改动面太大且属既有缺陷），登记为观察项供后续平台任务处理。
8. **零领域语义纪律**：本节点代码只认识契约名/端口字段/量纲，不认识任何学科量；耦合契约里的
   学科端口名是**数据**，由契约文件承载。

## ⑥ 误差判定（逐项回答"是否 < 3%"）

| 关键量 | 基准 | 误差 | 是否 < 3% |
|---|---|---|---|
| agent 在线数（pulse vs 期望） | 100 | 0 人（绝对） | **是** |
| 忙碌数（pulse vs 末态真值） | 10 | 0 人（绝对） | **是** |
| 画像工单数（真工具 vs 流式） | 逐 agent 20 | 0（最大计数差 0） | **是** |
| 事件丢失率 | 0 | 0.000000 | **是** |
| 画像一致性误差 | 0 | 0.0000% | **是** |
| 同网格转写积分误差 | 0 | 0.0e+00 | **是** |
| conservative 映射积分误差（finding，非本节点交付） | 0 | 9.80% | **否（已登记为缺陷）** |

> 说明：**保守映射的 9.8% 级偏差不粉饰为 PASS**。它是本节点发现的既有缺陷（STUB），
> 已作为 `conservative_map_is_stub` finding 入账；修复归属跨库耦合模块，不在本节点改动范围内。

## ⑧ 能力缺口登记（findings，如实入账不粉饰）

| finding | 严重度 | 说明 |
|---|---|---|
| `migration_without_v2_file` | MEDIUM | 迁移表声明了 v1→v2 路径但仓库未发布对应 v2 契约文件，跨仓消费者无法按版本化解析获得新端口名（迁移条目在本仓不可执行） |
| `coupling_map_refs_unpublished_contract` | MEDIUM | 耦合边表引用了仓库内未发布的求解器契约（版本化解析无解），这些边无法参与端到端端口一致性验证 |
| `conservative_map_is_stub` | HIGH | FieldExchange._conservative_map 当前直接回落 nearest 映射（源码注释 'For now'），实测积分量不守恒；场交换的「守恒」标签不能由该实现背书 |

* 这三条不是"报告里的免责声明"，而是被测试钉住的**可回归事实**：
  `tests/governance_maturity/test_field_audit.py::test_conservative_map_stub_finding` 要求
  conservative 映射的实测积分偏差始终 > 机器精度（修好了会红，必须同步改判据）；
  `::test_upstream_gaps_are_pinned` 要求未发布契约集合恰为
  `{solver.manoeuvring, solver.seakeeping}`——缺口只能缩小、不能悄悄变大。

## ⑦ 审计链与复现

* 运行：`python3 scripts/run_governance_maturity.py`（工件写入 `artifacts/governance-maturity/`）
* 验收 JSON：`artifacts/governance-maturity/acceptance.json`（本报告所有数字的机器可读源）
* 测试：`python3 -m pytest tests/governance_maturity -q` → 57 例
* 契约指纹（sha256，前 16 位）：mission_status_vocab=b370c1b4a59c997c, session_event_kinds=b455dc043bba28b9, research_run_status_vocab=6882bbb7b1f7578b, mission_state_machine=31e74859ea8a20dc, mission_task_status_map=48704a9618984800
* 场交换契约指纹：schema=bdf4bd5bbfe0f0f3、
  frames=2d6fc496531d83ef、migrations=0e5fd794032a4e4c
* 生成时 HEAD：`f6ece862f6ceb26d294644e7209bdc728d1b53f2`（**不含本节点的提交**——工件与本节点的代码在同一次 commit 里
  入库，因此这里记录的是其父提交；数字与工件内容由 acceptance.json 的 sha256 自证），
  生成时间 2026-11-11T07:02:04.839253+00:00
