# p457w2 战役集成交叉核对门（n09）· 跨域耦合验证 + 终报 + 能力台账增量

- 战役 `p457w2` · schema `tp.integration.seed_report/v1` · 生成 2026-11-11T07:09:36+00:00
- 基线 commit `bce00c4eb2df0e8e29ebfaf6b48dfd25ba336818` · 报告内容 sha256 `c466f40ceb8b6810e736aa3a0fbca46b006955494c4c75caec1313dc2335f855`
- 台账内容 sha256 `413eccc0d1fb5c520933816d255a24eba328c59a789b155a41aa0322d69124e7` · 等级分布 `{"MOCK": 0, "NOT_SUPPORTED": 0, "REAL": 11, "STUB": 0, "WITHHELD": 6}`
- 机器自检 I1–I8：**8/8 满足**

## 1 案例介绍（物理背景 / 战役几何 / 参数表）

战役 p457w2（plan 9388，9 节点）把 8 个交付面放进同一窗口：3 个船体工程面（n01 谱疲劳 S-N/窄宽带/Miner、n02 破舱稳性与渐进进水 + SOLAS 分舱指数、n05 管土接触全流程含 hogbend 与 TDZ 疲劳寿命带）、3 个物理/接触面（n03 冰脊 3D 接触互锁、n04 曝气缓冲去标定链、n06 双桨单轴失效非对称推力操纵）、2 个平台成熟面（n07 21 能力证据标注器、n08 跨求解器时钟同步与蒸馏）。n09 不再造物理模型，而是回答一个集成问题：**这 8 个面的输出能不能互相对接、共享假设有没有互相打脸、能力台账能不能据实更新**。

因此本门的三把尺子是：①接口契约（数据交换的量、单位、认证区间、字段）；②假设一致性（同一标准/同一常数/同一条经验律在不同节点里是不是同一套数）；③证据定级（节点自证只作输入，判据结论按数字重算，自述 FAIL/无证据一律不得抬高）。三条尺子都是**机器可复算**的：契约与假设在 data/ 里声明，闸代码零领域语义，域词条由 data/campaigns/*/domain_vocab.json 注入并有 pytest 反查。

上游节点运行时状态（本门如实记录，不粉饰）：n02/n04/n07 有机器可读验收工件（分别提交在 agent/m13986、agent/m13329 分支与 agent/m13991 工作树中）；n01/n05 只有未提交实现（无验收数值工件），但其 S-N 参数可由 extract_assumptions.py 从源码机器抽取；n03/n06/n08 本门运行时无可核验收工件 ⇒ 其能力条目必须登记为 unattested（WITHHELD），不允许「漏登记」当「没能力」。

互裁面选择原则：每一对必须让**同一个量被两种口径表达**，否则互裁没有信息量。①混合声速：生产方实现 × 独立 Wood 闭式（不共享任何代码/常量，故通过即证明无偏）；②同一 DNV-RP-C203 D 曲线：结构疲劳侧 × 管土疲劳侧（两侧都自称同一条曲线）；③GM_eff：同一节点的手工装配式 × added-weight 累积式两条代码路径。

| 节点 | 仓 | 判据统计 (P/F/W) | 通过率 | 总体判定 | 能力等级 | 基线态 |
|---|---|---|---|---|---|---|
| `p457w2-n01-structural-fatigue` | ship-performance-platform | 0/0/0 | — | **WITHHELD** | WITHHELD | BRANCH_ONLY |
| `p457w2-n02-damage-stability` | ship-performance-platform | 13/0/0 | 100.0% | **PASS** | WITHHELD, REAL | BRANCH_ONLY |
| `p457w2-n03-ice-ridge-3d` | TensorDEM | 0/0/0 | — | **WITHHELD** | WITHHELD | BRANCH_ONLY |
| `p457w2-n04-aeration-decalibration` | TensorSPH | 10/1/0 | 90.9% | **FAIL** | REAL | BRANCH_ONLY |
| `p457w2-n05-pipe-seabed` | ship-performance-platform | 0/0/0 | — | **WITHHELD** | WITHHELD | BRANCH_ONLY |
| `p457w2-n06-twin-screw-shaft-failure` | ship-performance-platform | 0/0/0 | — | **WITHHELD** | WITHHELD | BRANCH_ONLY |
| `p457w2-n07-capability-annotator` | TensorPlatform | 3/0/2 | 100.0% | **PASS** | REAL | BRANCH_ONLY |
| `p457w2-n08-cross-solver-clock-sync` | TensorPlatform | 0/0/0 | — | **WITHHELD** | WITHHELD | BRANCH_ONLY |

## 2 怎么做的（方法 / 流程 / 工具链）

**门骨架（平台机制，零领域语义）**：复用 p158w2 建立的 tools/integration_gate（sampler/compare/baseline/ledger/report/docgen），本波新增三个模块：`coupling.py`（接口契约 + 假设一致性，含受限 AST 求值的「律自洽」检查）、`archive.py`（逐文件 sha256 快照 + 独立复算）、`ledger.py` 的 `compute_delta`/`platform_ledger_delta`（战役台账增量与平台 21 能力观测差，含 R2 honest-FAIL 抬高保护）。报告的 I1–I8 机器自检全部由数字判定。

**接口契约五步核验**：①契约文档存在（doc_ref 指向的文件必须真的存在）；②生产方/消费方都在战役节点表里；③两侧单位归一致（无量纲别名归一）；④消费方需求区间是否被生产方**认证**区间覆盖——不覆盖时只有 `fallback=clamp_with_status` 且载荷字段含 `domain_status` 才算「有护栏的通过」，否则记 CONTRACT_MISMATCH；⑤两侧是否有可核证据（验收工件或本门成功执行过的轨迹）。任一步不满足都不会判 VALIDATED。

**假设一致性三类检查**：`edition`（版本串逐字一致，>1 个取值即冲突）、`value`（同一量的相对偏差 vs 声明容差）、`law`（同一条双段幂律的多参数声明：除了共享参数逐项比，还要过**律自洽**——由 (log a1, m1, m2, 拐点) 依连续性条件 log a2 = log a1 + (m2 − m1)·log10(拐点) 推出的尾段参数必须与声明值一致，容差 0.01 decades）。冲突必须带 `mitigation`；只有被引用的互裁对真的判 DISAGREE/DEGENERATE（或受影响节点均无可核自证）时，冲突才算「已处置」，否则计入未处置数并使 I7 不通过。

**零手抄证据链**：所有进入判据的数字来自 (a) 节点验收工件快照（raw/*.json，sha256 记入 attestation 的 source）、(b) extract_assumptions.py 从被测源码机器抽取的参数（含源文件 sha256）、(c) 本门自己跑出的五条轨迹（runner 记录被测模块 sha256、Python 版本与 stdout sha256）。判据结论由 `compute_verdict` 按 op/threshold 重算，**声明结论与数字矛盾时以数字为准**（reconcile）。

**可复现性**：`archive_spec.json` 声明快照路径 → 逐文件 sha256 + 清单内容 sha256，并在同一次运行内独立复算（missing/changed 必须为空）。综述文档不入快照（它由 seed_report 生成并内嵌 seed_report 的 sha，纳入会形成自指哈希环），这一点在 archive_spec 的 note 里明写。

复现命令：

```bash
# 1) 机器抽取跨节点共享假设（只读兄弟 worktree 源码，记录 sha256）
TP_SIBLING_ROOT=/root/tp-agent/work python3 data/campaigns/p457w2/extract_assumptions.py
# 2) 从节点验收工件机器摄取自证（判据结论按数字重算）
python3 -c "from pathlib import Path; from tools.integration_gate.ingest import ingest_campaign; ingest_campaign(Path('data/campaigns/p457w2'))"
# 3) 跑整门：轨迹 → 互裁 → 耦合闸 → 台账增量 → 播种报告 → 图 → 工件快照
python3 -m tools.integration_gate.cli --campaign p457w2 --data data/campaigns/p457w2 \
  --out artifacts/campaigns/p457w2 --repo-root . --main main --self-repo TensorPlatform \
  --base-ledger artifacts/capability_ledger/capability_ledger.json \
  --interfaces data/campaigns/p457w2/interfaces.json \
  --assumptions data/campaigns/p457w2/assumptions.json \
  --archive-spec data/campaigns/p457w2/archive_spec.json \
  --platform-observed data/campaigns/p457w2/platform_observed_levels.json \
  --pytest-summary artifacts/campaigns/p457w2/pytest_summary.json --figures --strict
# 4) 生成本报告（数字全部从 seed_report.json 注入）
python3 -m tools.integration_gate.docgen --report artifacts/campaigns/p457w2/seed_report.json \
  --prose data/campaigns/p457w2/doc_prose.json --out docs/P457W2-N09_INTEGRATION_GATE.md \
  --figures artifacts/campaigns/p457w2/figures
# 5) 测试（含 1 个「矛盾假设必须被检出」的负例控制）
python3 -m pytest tests/test_campaign_coupling_gate.py tests/test_integration_gate.py -q
```

## 3 计算结果（程序化注入，零手抄）

### 3.1 互裁结论与逐检查数字

### ADJ-1-aeration-mixture-wood · 混合声速口径：生产方实现 × 独立 Wood 闭式（含两极限）

- 类型 `cross_implementation` · 结论 **AGREE** · 能力等级上限 `REAL` · 对照侧 `INDEPENDENT_REFERENCE：data/campaigns/p457w2/runners/mixture_c_wood_reference.py（不 import 生产方任何代码/常量）`
- 生产方 = p457w2-n04 分支 tensorsph.aeration.mixture_acoustics（模块 sha256 随轨迹 provenance 记录）；参考侧 = Wood(1955) 闭式独立重写。若两侧一致，说明实现无偏；若不一致，生产方自证的 1e-6 判据不可跨实现复现。

| 检查 | 结论 | 严重度 | 关键数字 |
|---|---|---|---|
| `trace_agreement` | AGREE | blocking | max_rel_dev=1.556e-16; n_points=10; tol=1.000e-09 |
| `endpoint_agreement` | AGREE | blocking | rel_dev=0; b=1483; param=0; tol=1.000e-09 |
| `degenerate` | AGREE | blocking | driver=0.5006; response=23.81 |

### ADJ-2-sn-curve-n01-vs-n05 · 同一 DNV-RP-C203 D 曲线：结构疲劳侧 × 管土疲劳侧 寿命律互裁

- 类型 `convention` · 结论 **DISAGREE** · 能力等级上限 `MOCK` · 对照侧 `BOTH_BRANCH_ONLY：两侧实现分别位于 agent/m13988、agent/m13989 工作树（未提交/未合并）`
- 两侧都自称 DNV-RP-C203 Table 2-1「D curve in air」，但尾段参数与拐点不同。互裁比对 log10 N(Δσ)，并把两侧声明的 S-N 标量并排比。结论按数字给出，不按自述。 注：两侧节点本门运行时均无验收数值工件（unattested），本对不降级任何已登记 REAL 声明；冲突以假设键 fatigue.sn_curve.D 的 IMPLIED_MISMATCH/CONTRADICTED 落地，写入缺口台账交回头部节点复核。

| 检查 | 结论 | 严重度 | 关键数字 |
|---|---|---|---|
| `trace_agreement` | DISAGREE | blocking | max_rel_dev=0.0575; n_points=9; tol=0.001 |
| `metric_agreement` | DISAGREE | blocking | rel_dev=0.02953; a=15.82; b=15.35; tol=1.000e-06 |
| `metric_agreement` | DISAGREE | blocking | rel_dev=0.3633; a=53.4; b=34; tol=1.000e-06 |

### ADJ-3-damage-stability-two-paths · GM_eff 口径：手工装配式 × added-weight 累积式（同节点两条实现路径）

- 类型 `cross_implementation` · 结论 **AGREE** · 能力等级上限 `REAL` · 对照侧 `SAME_NODE_TWO_PATHS：backend/modules/damage_stability_flooding/hydrostatics.py`
- 节点自证声称 GM_eff 互证误差 1e-9；本门用同一模块的两条独立代码路径在 5 个进水液位重算，避免只信它挑的那一个点。

| 检查 | 结论 | 严重度 | 关键数字 |
|---|---|---|---|
| `trace_agreement` | AGREE | advisory | max_rel_dev=0; n_points=5; tol=1.000e-12 |
| `metric_below` | AGREE | blocking | value=0; threshold=1.000e-09; ratio_to_threshold=0 |
| `metric_below` | AGREE | blocking | value=0; threshold=1.000e-09; ratio_to_threshold=0 |
| `metric_below` | AGREE | blocking | value=0; threshold=1.000e-09; ratio_to_threshold=0 |

### 3.2 能力台账增量（证据定级）

| 能力 | 节点 | 等级 | 状态 | 合并态 | honest_FAIL | 备注 |
|---|---|---|---|---|---|---|
| `p457w2-n01-structural-fatigue:unattested` | `p457w2-n01-structural-fatigue` | **WITHHELD** | PROVISIONAL | BRANCH_ONLY | — |  |
| `damage_stability.compartment_geometry` | `p457w2-n02-damage-stability` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `damage_stability.hydrostatics_gm_eff` | `p457w2-n02-damage-stability` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `damage_stability.mass_conservation` | `p457w2-n02-damage-stability` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `damage_stability.orifice_flow` | `p457w2-n02-damage-stability` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `damage_stability.progressive_flooding` | `p457w2-n02-damage-stability` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `damage_stability.residual_gz` | `p457w2-n02-damage-stability` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `damage_stability.solas_index_A_R` | `p457w2-n02-damage-stability` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `damage_stability.solas_p_i` | `p457w2-n02-damage-stability` | **WITHHELD** | PROVISIONAL | BRANCH_ONLY | — |  |
| `p457w2-n03-ice-ridge-3d:unattested` | `p457w2-n03-ice-ridge-3d` | **WITHHELD** | PROVISIONAL | BRANCH_ONLY | — |  |
| `aeration.impact_pressure_law` | `p457w2-n04-aeration-decalibration` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `aeration.mixture_acoustics` | `p457w2-n04-aeration-decalibration` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `aeration.pbm_moments` | `p457w2-n04-aeration-decalibration` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `p457w2-n05-pipe-seabed:unattested` | `p457w2-n05-pipe-seabed` | **WITHHELD** | PROVISIONAL | BRANCH_ONLY | — |  |
| `p457w2-n06-twin-screw-shaft-failure:unattested` | `p457w2-n06-twin-screw-shaft-failure` | **WITHHELD** | PROVISIONAL | BRANCH_ONLY | — |  |
| `platform.capability_annotator_scan` | `p457w2-n07-capability-annotator` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `p457w2-n08-cross-solver-clock-sync:unattested` | `p457w2-n08-cross-solver-clock-sync` | **WITHHELD** | PROVISIONAL | BRANCH_ONLY | — |  |

### 3.3 基线对齐（I4）

| 节点 | 分支 | commit | 领先 main | fork 后 main 漂移 | 契约必需/已有 |
|---|---|---|---|---|---|
| `p457w2-n01-structural-fatigue` | agent/m13988 | `956afa0eca8a` | 1 | 0 | Y/— |
| `p457w2-n02-damage-stability` | agent/m13986 | `d6b9ccfae9be` | 1 | 0 | Y/Y |
| `p457w2-n03-ice-ridge-3d` | agent/m13987 | `c66bb8997e70` | 87 | 0 | Y/— |
| `p457w2-n04-aeration-decalibration` | agent/m13329 | `881256482557` | 2 | 12 | Y/Y |
| `p457w2-n05-pipe-seabed` | agent/m13989 | `788c8f3ff0dd` | 1 | 0 | Y/— |
| `p457w2-n06-twin-screw-shaft-failure` | agent/m13990 | `563be725e844` | 1 | 0 | Y/— |
| `p457w2-n07-capability-annotator` | agent/m13991 | `0e5e2ae9f896` | 1 | 16 | Y/Y |
| `p457w2-n08-cross-solver-clock-sync` | agent/m13992 | `acec30b89fd5` | 2 | 16 | Y/— |

### 3.4 遗留缺口与下一波播种提案

| 缺口 | 类型 | 说明 |
|---|---|---|
| `gap:p457w2-n01-structural-fatigue:no_attestation` | missing_evidence | 节点在本门运行时没有可机器复算的验收自证工件 |
| `gap:p457w2-n03-ice-ridge-3d:no_attestation` | missing_evidence | 节点在本门运行时没有可机器复算的验收自证工件 |
| `gap:p457w2-n04-aeration-decalibration:honest_fail` | case_verdict_fail | 工程判决 FAIL（算例结论，非实现缺陷）: C5_out_of_domain_cases |
| `gap:p457w2-n05-pipe-seabed:no_attestation` | missing_evidence | 节点在本门运行时没有可机器复算的验收自证工件 |
| `gap:p457w2-n06-twin-screw-shaft-failure:no_attestation` | missing_evidence | 节点在本门运行时没有可机器复算的验收自证工件 |
| `gap:p457w2-n08-cross-solver-clock-sync:no_attestation` | missing_evidence | 节点在本门运行时没有可机器复算的验收自证工件 |
| `gap:p457w2-n01-structural-fatigue:contract_missing` | interface_contract_missing | BRANCH_ONLY 节点未声明接口契约 |
| `gap:p457w2-n03-ice-ridge-3d:contract_missing` | interface_contract_missing | BRANCH_ONLY 节点未声明接口契约 |
| `gap:p457w2-n05-pipe-seabed:contract_missing` | interface_contract_missing | BRANCH_ONLY 节点未声明接口契约 |
| `gap:p457w2-n06-twin-screw-shaft-failure:contract_missing` | interface_contract_missing | BRANCH_ONLY 节点未声明接口契约 |
| `gap:p457w2-n08-cross-solver-clock-sync:contract_missing` | interface_contract_missing | BRANCH_ONLY 节点未声明接口契约 |
| `gap:ADJ-2-sn-curve-n01-vs-n05:trace_agreement` | inter_adjudication_conflict | trace_agreement 结论 DISAGREE: 9 个应力幅点 log10 N 相对偏差（>0.1% 即判不一致） |
| `gap:ADJ-2-sn-curve-n01-vs-n05:metric_agreement` | inter_adjudication_conflict | metric_agreement 结论 DISAGREE: 尾段 log10(A) 直接比对 |
| `gap:ADJ-2-sn-curve-n01-vs-n05:metric_agreement` | inter_adjudication_conflict | metric_agreement 结论 DISAGREE: 两段交接拐点应力幅直接比对 |

| seed | 类型 | 理由 | 验收提示 |
|---|---|---|---|
| `attest:p457w2-n01-structural-fatigue` | attestation_backfill | 无自证 → 本门无法定级（WITHHELD） | acceptance.json 含逐条判据 measured/threshold/op + 输入 sha256 |
| `attest:p457w2-n03-ice-ridge-3d` | attestation_backfill | 无自证 → 本门无法定级（WITHHELD） | acceptance.json 含逐条判据 measured/threshold/op + 输入 sha256 |
| `attest:p457w2-n05-pipe-seabed` | attestation_backfill | 无自证 → 本门无法定级（WITHHELD） | acceptance.json 含逐条判据 measured/threshold/op + 输入 sha256 |
| `attest:p457w2-n06-twin-screw-shaft-failure` | attestation_backfill | 无自证 → 本门无法定级（WITHHELD） | acceptance.json 含逐条判据 measured/threshold/op + 输入 sha256 |
| `attest:p457w2-n08-cross-solver-clock-sync` | attestation_backfill | 无自证 → 本门无法定级（WITHHELD） | acceptance.json 含逐条判据 measured/threshold/op + 输入 sha256 |
| `contract:p457w2-n01-structural-fatigue` | interface_contract | BRANCH_ONLY 节点未声明接口契约 | 声明导出符号/端口单位/稳定性承诺 + 兼容性测试 |
| `contract:p457w2-n03-ice-ridge-3d` | interface_contract | BRANCH_ONLY 节点未声明接口契约 | 声明导出符号/端口单位/稳定性承诺 + 兼容性测试 |
| `contract:p457w2-n05-pipe-seabed` | interface_contract | BRANCH_ONLY 节点未声明接口契约 | 声明导出符号/端口单位/稳定性承诺 + 兼容性测试 |
| `contract:p457w2-n06-twin-screw-shaft-failure` | interface_contract | BRANCH_ONLY 节点未声明接口契约 | 声明导出符号/端口单位/稳定性承诺 + 兼容性测试 |
| `contract:p457w2-n08-cross-solver-clock-sync` | interface_contract | BRANCH_ONLY 节点未声明接口契约 | 声明导出符号/端口单位/稳定性承诺 + 兼容性测试 |
| `adjudicate:ADJ-2-sn-curve-n01-vs-n05` | cross_domain_reconcile | trace_agreement 结论 DISAGREE: 9 个应力幅点 log10 N 相对偏差（>0.1% 即判不一致） | 冲突两侧给出统一口径推导 + 数值复算 ≤1e-9 |

### 3.6 跨节点数据交换接口契约

- 接口 5 条 · 已文档化 5 · 契约核验通过 2 · 契约不符 0 · 证据不足 3 · 无文档 0

| 接口 | 生产方 → 消费方 | 载荷(单位) | 生产方认证区间 | 消费方需求区间 | 状态 |
|---|---|---|---|---|---|
| `IF-1-aeration-impact-law` | `p457w2-n04-aeration-decalibration` → `p457w2-n01-structural-fatigue` | g_load(alpha_patch, u_impact) (-) | [1e-08, 0.05] | [0, 0.5] (-) | **VALIDATED** |
| `IF-2-fatigue-life-curve` | `p457w2-n05-pipe-seabed` → `p457w2-n01-structural-fatigue` | log10_N(D_curve, delta_sigma) (decade) | [6, 10.5] | [6, 10.5] (decade) | **VALIDATED** |
| `IF-3-ice-ridge-load` | `p457w2-n03-ice-ridge-3d` → `p457w2-n01-structural-fatigue` | ice_load_amplitude(ridge, speed) (MPa) | [0, 30] | [0, 20] (MPa) | **UNATTESTED** |
| `IF-4-flooding-state-exchange` | `p457w2-n02-damage-stability` → `p457w2-n08-cross-solver-clock-sync` | flooding_state(t) (m) | [0, 8] | [0, 8] (m) | **UNATTESTED** |
| `IF-5-loading-condition-after-flooding` | `p457w2-n02-damage-stability` → `p457w2-n06-twin-screw-shaft-failure` | GM_eff_m, KG_m (m) | [-1, 5] | [-0.5, 3] (m) | **UNATTESTED** |

- `IF-1-aeration-impact-law` 契约备注：生产方认证域 = [ALPHA_MIN_RESOLVABLE=1e-8, ALPHA_VW_MAX=0.05]（van Wijngaarden 近似域，见轨迹 meta）；消费端需求上限 0.5 来自生产方自身算例集 alpha_patch 最大 0.4349（notebook 行 C5），即生产方已在其自证域外使用该律 3/6 算例；契约要求消费方接受 clamp_with_status（域外返回值标 OUT_OF_DOMAIN，不外推）。
- `IF-2-fatigue-life-curve` 契约备注：两侧都要覆盖 15–150 MPa 应力幅（log10 N 6.85–9.47，见轨迹）；参数同一性由 assumptions 键 fatigue.sn_curve.D 另行互裁（本门实测不一致）。
- `IF-3-ice-ridge-load` 契约备注：生产方（冰脊 3D）本门运行时无验收数值工件，适用域按战役任务书登记；接口状态必然为 UNATTESTED。
- `IF-4-flooding-state-exchange` 契约备注：舱高 8 m 为生产方自证算例几何（C1/C2 15x10x8 m）；交换需带 dt 与质量账本，供 n08 时钟同步校验守恒<1%。消费方 n08 无验收工件 ⇒ UNATTESTED。
- `IF-5-loading-condition-after-flooding` 契约备注：生产方实测 GM_eff 1.529–1.718 m（正浮态）与 -0.618 m（负例失稳），见轨迹 meta；操纵性侧需知失稳工况不可用。消费方 n06 无验收工件 ⇒ UNATTESTED。
- `IF-3-ice-ridge-load` 未通过：producer_evidenced=WITHHELD
- `IF-4-flooding-state-exchange` 未通过：consumer_evidenced=WITHHELD
- `IF-5-loading-condition-after-flooding` 未通过：consumer_evidenced=WITHHELD
- `IF-1-aeration-impact-law` 区间缺口 0.45 由 `domain_status` 护栏承接（clamp_with_status，禁止沉默外推）

### 3.7 跨节点共享假设一致性

- 假设键 4 · 一致 2 · 跨节点冲突 0 · 律自洽冲突 1 · 声明不全 1 · 未处置冲突 0 · 口径洁净 `False`

| 假设键 | 类型 | 状态 | 关键数字 | 处置 |
|---|---|---|---|---|
| `fatigue.sn_curve.D` | law | **IMPLIED_MISMATCH** | implied_max_abs_dev=0.197917; shared_max_rel_dev=0.363296; tol=1e-06 | 已处置: pair 'ADJ-2-sn-curve-n01-vs-n05' verdict DISAGREE; 受影响节点均无可核能力自证 ⇒ 冲突以 WITHHELD 落地，不存在被抬高的声明 |
| `fatigue.sn_curve.D.condition` | edition | **AGREED** | editions=['in air'] | — |
| `constant.gravity` | value | **AGREED** | max_rel_dev=0.000341488; tol=0.001 | — |
| `solas.edition` | edition | **UNDETERMINED** | editions=None | — |

- 冲突 `fatigue.sn_curve.D`（IMPLIED_MISMATCH）：节点 p457w2-n01-structural-fatigue, p457w2-n05-pipe-seabed；处置 = pair 'ADJ-2-sn-curve-n01-vs-n05' verdict DISAGREE; 受影响节点均无可核能力自证 ⇒ 冲突以 WITHHELD 落地，不存在被抬高的声明
- 律自洽 `fatigue.sn_curve.D` / p457w2-n01-structural-fatigue：由 `log_a1 + (m2 - m1) * log10(knee)` 推得 15.619083，声明 15.817000，差 0.197917（容差 0.01）→ FAIL
- 律自洽 `fatigue.sn_curve.D` / p457w2-n05-pipe-seabed：由 `log_a1 + (m2 - m1) * log10(knee)` 推得 15.226958，声明 15.350000，差 0.123042（容差 0.01）→ FAIL

### 3.8 工件快照与可复现性

- 快照 54 个文件 / 935334 字节；清单内容 sha256 `973c6e760dc541d18077db5e39d25d02bfdc6d6b5554e8995154d4e3052e401d`；独立复算 通过（checked=54, missing=[], changed=[]）

### 3.9 台账增量（战役条目 vs 平台基础台账）

- 基础台账能力 21 个 · 本战役新条目 17 · 其中新 REAL **11** · 提升 0 · 降级 0 · 持平 0

| 等级 | 新条目数 |
|---|---|
| REAL | 11 |
| WITHHELD | 6 |
| MOCK | 0 |
| STUB | 0 |
| NOT_SUPPORTED | 0 |

- 新 REAL：`damage_stability.compartment_geometry`（p457w2-n02-damage-stability，PROVISIONAL）
- 新 REAL：`damage_stability.hydrostatics_gm_eff`（p457w2-n02-damage-stability，PROVISIONAL）
- 新 REAL：`damage_stability.mass_conservation`（p457w2-n02-damage-stability，PROVISIONAL）
- 新 REAL：`damage_stability.orifice_flow`（p457w2-n02-damage-stability，PROVISIONAL）
- 新 REAL：`damage_stability.progressive_flooding`（p457w2-n02-damage-stability，PROVISIONAL）
- 新 REAL：`damage_stability.residual_gz`（p457w2-n02-damage-stability，PROVISIONAL）
- 新 REAL：`damage_stability.solas_index_A_R`（p457w2-n02-damage-stability，PROVISIONAL）
- 新 REAL：`aeration.impact_pressure_law`（p457w2-n04-aeration-decalibration，PROVISIONAL）
- 新 REAL：`aeration.mixture_acoustics`（p457w2-n04-aeration-decalibration，PROVISIONAL）
- 新 REAL：`aeration.pbm_moments`（p457w2-n04-aeration-decalibration，PROVISIONAL）
- 新 REAL：`platform.capability_annotator_scan`（p457w2-n07-capability-annotator，PROVISIONAL）

### 3.10 平台 21 能力台账：独立观测 vs 基础台账

- 观测来源：data/campaigns/p457w2/platform_observed_levels.json
- 判定分布：corroborated=3, downgrade_candidate=15, upgrade_candidate=3

| 能力 | 基础等级 | 观测等级 | 判定 |
|---|---|---|---|
| `ai.predict` | NOT_SUPPORTED | WITHHELD | **upgrade_candidate** |
| `calc.resistance` | REAL | REAL | **corroborated** |
| `calc.stability` | REAL | REAL | **corroborated** |
| `dev.gate_5415` | REAL | WITHHELD | **downgrade_candidate** |
| `dev.git` | REAL | WITHHELD | **downgrade_candidate** |
| `dev.logs` | REAL | WITHHELD | **downgrade_candidate** |
| `dev.nginx_audit` | REAL | WITHHELD | **downgrade_candidate** |
| `dev.probe` | REAL | WITHHELD | **downgrade_candidate** |
| `dev.restart` | REAL | WITHHELD | **downgrade_candidate** |
| `dev.services` | REAL | WITHHELD | **downgrade_candidate** |
| `geometry.generate` | REAL | WITHHELD | **downgrade_candidate** |
| `geometry.hydro_curves` | REAL | WITHHELD | **downgrade_candidate** |
| `geometry.list_models` | REAL | WITHHELD | **downgrade_candidate** |
| `geometry.verify_official` | REAL | WITHHELD | **downgrade_candidate** |
| `gov.stats` | REAL | WITHHELD | **downgrade_candidate** |
| `knowledge.docs` | REAL | WITHHELD | **downgrade_candidate** |
| `methods.computation` | NOT_SUPPORTED | WITHHELD | **upgrade_candidate** |
| `misc.stubs` | STUB | WITHHELD | **upgrade_candidate** |
| `propeller.open_water` | REAL | REAL | **corroborated** |
| `shiphull.generate` | REAL | WITHHELD | **downgrade_candidate** |
| `stdmodels.lookup` | REAL | WITHHELD | **downgrade_candidate** |

### 3.11 机器自检 I1–I8

| 判据 | 是否满足 | 数字 | 判据文字 |
|---|---|---|---|
| I1_capability_registration | PASS | `{"levels_complete": 8, "nodes": 8, "registered": 8}` | 7 节点能力条目全部登记，证据等级标注完整 |
| I2_inter_adjudication_pairs | PASS | `{"pairs": 3, "verdicts": {"ADJ-1-aeration-mixture-wood": "AGREE", "ADJ-2-sn-curve-n01-vs-n05": "DISAGREE", "ADJ-3-damage-stability-two-paths": "AGREE"}, "with_verdict": 3}` | 3 对互裁面一致性报告生成 |
| I3_seed_report | PASS | `{"gaps": 14, "nodes": 8, "schema": "tp.integration.seed_report/v1"}` | 结构化 JSON 报告含节点验收状态/能力增量/遗留缺口 |
| I4_baseline_alignment | PASS | `{"contracts_present": 3, "contracts_required": 8, "nodes": 8, "states": {"p457w2-n01-structural-fatigue": "BRANCH_ONLY", "p457w2-n02-damage-stability": "BRANCH_ONLY", "p457w2-n03-ice-ridge-3d": "BRANCH_ONLY", "p457w2-n04-aeration-decalibration": "BRANCH_ONLY", "p457w2-n05-pipe-seabed": "BRANCH_ONLY", "p457w2-n06-twin-screw-shaft-failure": "BRANCH_ONLY", "p457w2-n07-capability-annotator": "BRANCH_ONLY", "p457w2-n08-cross-solver-clock-sync": "BRANCH_ONLY"}}` | 所有节点基线/合并态判定完成，未合并分支接口契约状态已注明 |
| I5_pytest | PASS | `{"command": "python3 -m pytest tests/test_campaign_coupling_gate.py tests/test_integration_gate.py -q --tb=no", "failed": 0, "files": ["tests/test_campaign_coupling_gate.py", "tests/test_integration_gate.py"], "note": "summary_line 去掉耗时字段，保证同一测试集生成逐字节相同的摘要（工件快照纪律）", "passed": 53, "python": "3.11.6"}` | pytest ≥5 全绿 |
| I6_interface_contracts | PASS | `{"documented": 5, "interfaces": 5, "mismatch": 0, "mismatch_ids": [], "unattested": 3, "undocumented": 0, "validated": 2}` | 全部跨节点接口契约有文档且单位/区间/节点声明机械核验通过（无 CONTRACT_MISMATCH） |
| I7_assumption_consistency | PASS | `{"agreed": 2, "clean": false, "contradicted": 0, "implied_mismatch": 1, "keys": 4, "mitigated": 1, "undetermined": 1, "unmitigated": 0, "violations": 1}` | 跨节点共享假设全部机械检查：冲突必须显式降级/引用仲裁（零未处置冲突），声明不全记 UNDETERMINED 不算一致 |
| I8_archive_snapshot | PASS | `{"changed": [], "checked": 54, "content_sha256": "973c6e760dc541d18077db5e39d25d02bfdc6d6b5554e8995154d4e3052e401d", "missing": [], "n_bytes": 935334, "n_files": 54, "verified": true}` | 全部工件快照逐文件 sha256 + 清单内容 sha256，可独立复算通过 |

## 4 结果图

![图 1 跨节点依赖图：生产者→消费者，边按接口校验状态着色（VALIDATED / UNATTESTED / CONTRACT_MISMATCH），边上标注载荷与单位。冰脊（n03）、破舱进水（n08 消费端）、单轴失效（n06）因当班无可核验收工件而呈 UNATTESTED。](artifacts/campaigns/p457w2/figures/fig4_dependency_map.png)

*图 1 跨节点依赖图：生产者→消费者，边按接口校验状态着色（VALIDATED / UNATTESTED / CONTRACT_MISMATCH），边上标注载荷与单位。冰脊（n03）、破舱进水（n08 消费端）、单轴失效（n06）因当班无可核验收工件而呈 UNATTESTED。*

![图 2 能力台账增量：左=本战役新条目按证据等级分布（新 REAL 计数），右=新 REAL/提升/降级/持平/新条目相对平台基础台账的计数。](artifacts/campaigns/p457w2/figures/fig5_ledger_delta.png)

*图 2 能力台账增量：左=本战役新条目按证据等级分布（新 REAL 计数），右=新 REAL/提升/降级/持平/新条目相对平台基础台账的计数。*

![图 3 互裁对逐检查结论热图（AGREE/DISAGREE/DEGENERATE/WITHHELD），用于一眼看出哪一对把争议落成了数字。](artifacts/campaigns/p457w2/figures/fig2_pair_checks.png)

*图 3 互裁对逐检查结论热图（AGREE/DISAGREE/DEGENERATE/WITHHELD），用于一眼看出哪一对把争议落成了数字。*

![图 4 轨迹层一致性：混合声速（实现 × 独立 Wood 闭式，10 个孔隙率点）与寿命律（两侧 log10 N，9 个应力幅点）的点对点比对。](artifacts/campaigns/p457w2/figures/fig1_trace_agreement.png)

*图 4 轨迹层一致性：混合声速（实现 × 独立 Wood 闭式，10 个孔隙率点）与寿命律（两侧 log10 N，9 个应力幅点）的点对点比对。*

![图 5 逐节点能力等级栈 + 判据通过率（右轴）。](artifacts/campaigns/p457w2/figures/fig3_ledger_levels.png)

*图 5 逐节点能力等级栈 + 判据通过率（右轴）。*

## 5 注意点（踩坑 / 局限 / 适用范围）

**本门最重要的发现是它检出的矛盾，不是它通过的条目**：结构疲劳侧（n01）与管土疲劳侧（n05）都声明使用「同一条 DNV-RP-C203 D 曲线」，但尾段参数与两段交接拐点不同——n01 声明 log a2=15.817、拐点 53.4 MPa；n05 声明 log a2=15.350、拐点 34.0 MPa。按双段幂律连续性条件，由各自 (log a1, m1, m2, 拐点) 推出的尾段参数应为 15.619（n01）与 15.227（n05），与声明值分别相差 0.198 与 0.123 decades（约 1.6×/1.3× 寿命）。互裁对在 9 个应力幅点上判 DISAGREE（log10 N 最大相对偏差 5.75%，拐点相差 36.3%）。处置：该冲突以假设键 `fatigue.sn_curve.D` 的 IMPLIED_MISMATCH 落地，两个节点当班均无验收自证（unattested ⇒ WITHHELD），因此冲突不会抬高任何声明；缺口已交回头部节点按标准表格复核并加回归测试。

**接口覆盖率不足是真实缺口，不是报告瑕疵**：5 条接口全部有文档、契约形状 5/5 机械核验通过（无 CONTRACT_MISMATCH），但只有 2 条两侧都有可核证据；另 3 条因生产方或消费方当班无可核验收工件而判 UNATTESTED。其中 IF-1（曝气减载律 → 砰击/疲劳链）还暴露一个实质问题：生产方认证域只到 alpha=0.05（van Wijngaarden 近似域），而它自己的 6 个算例里 alpha_patch 最大到 0.435、有 3 个算例落在声学域外——域外调用是常态，闸因此只允许 `clamp_with_status` 护栏下的调用，禁止沉默外推。

**证据等级的读法**：本门 11 条新 REAL 中，7 条来自 n02（判据数字全部以任务书门限重算，并用同一模块的两条独立代码路径在 5 个液位复算了 GM_eff，偏差 0）、3 条来自 n04（Wood 闭式与 60 位 mpmath 对拍、两极限残差、PBM 矩闭合；混合声速另由本门独立闭式实现逐点复算，10 点最大相对偏差见 §3.1）、1 条来自 n07（21 能力扫描口径）。所有条目 status=PROVISIONAL：它们位于未合并分支/工作树，不能从 main 复现，这是 R3 规则的结果，不是降级。

**n04 的设计包线结论**：其自证 C5 的「G 载荷减载率落在 m9319 [6%,12%] 带内」按 p95 读法成立（0.109），但 6 个算例中只有 1 个落在带内（均值 4.7%），且 3 个算例落入声学域外。本门把「域外算例数」与「带内算例数」登记为 case_verdict（工程结论），不压实现等级；也就是说：实现是真实算法，但把它直接当全域设计律用是**未验证**的。

**局限**：①本门只核接口形状（单位/区间/字段/文档/证据可得性）与假设一致性，不核物理正确性；物理正确性由互裁对与节点自身的解析锚承担。②兄弟工作树的 git 事实（领先/漂移）在运行时读取，事后若他人继续提交则数值会变，故基线事实以报告记录为准。③n03/n06/n08 当班未交付验收工件，本门无法把它们的物理结论纳入裁决，只能如实登记缺口。④平台 21 能力观测（n07 扫描）只做静态代码/测试信号判定，其准确率与漏报率在工件内无字段，故登记为 case_verdict WITHHELD，不当作已验指标。

**对上一轮尝试的修正**（本节点为第 2 次尝试）：上一轮的主要失败模式是「直接转述自证 + 无机器核验」，本轮改为三处硬约束：①所有判据由 op/threshold 重算，声明与数字冲突时数字优先；②假设一致性用双段幂律的连续性条件做律自洽检查（可脱离任何标准表格独立复算）；③台账增量与平台观测差都走 R2（基础台账里存在抗辩证据的条目不得被静态扫描抬高）——本轮实测 21 条平台能力里 15 条 downgrade_candidate、3 条 upgrade_candidate、3 条 corroborated、0 条 blocked_upgrade（本轮数据没有触发该分支，该分支由单元测试 `test_platform_delta_blocks_upgrade_over_contradicted_evidence` 守住）。

**基础台账自身的一处内部矛盾（本轮顺带查出）**：平台 `artifacts/capability_ledger/capability_ledger.json` 里 `propeller.open_water`、`calc.resistance`、`calc.stability` 三条的等级为 REAL，但其 evidence 列表里含 stance=contradicts 的审计记录（对该条目而言是 honest MOCK 裁决）。本门按 R2 只会阻止「被抬高」，不会替基础台账降级；因此把这三条标 `ledger_internal_conflict` 记入 §3.10，交平台能力台账负责人按 evidence 复裁。这是台账的口径问题，不是 n07 扫描器的缺陷。

## 6 误差判定（关键量 vs 基准，逐项回答是否 <3%）

| 互裁检查 | 关键量 | 参考/容差 | 相对偏差 | 是否 <3% |
|---|---|---|---|---|
| `trace_agreement` | `max_rel_dev` | 1.000e-09 | 1.556e-16 | 是 |
| `endpoint_agreement` | `rel_dev` | 1.000e-09 | 0 | 是 |
| `degenerate` | 定性检查 | None | — | 不适用（定性） |
| `trace_agreement` | `max_rel_dev` | 0.001 | 0.0575 | 否 |
| `metric_agreement` | `rel_dev` | 1.000e-06 | 0.02953 | 是 |
| `metric_agreement` | `rel_dev` | 1.000e-06 | 0.3633 | 否 |
| `trace_agreement` | `max_rel_dev` | 1.000e-12 | 0 | 是 |
| `metric_below` | `ratio_to_threshold` | 1.000e-09 | 0 | 是 |
| `metric_below` | `ratio_to_threshold` | 1.000e-09 | 0 | 是 |
| `metric_below` | `ratio_to_threshold` | 1.000e-09 | 0 | 是 |

说明：`max_ratio` 是**分支切换处的跳变比**（不是数值误差），1 表示完全连续；`ratio_to_threshold` = 实测值/约定阈值，<1 表示优于约定门。未达门的 honest FAIL 由下文逐条列出，不粉饰。

逐项回答「是否 <3%」：`ADJ-1` 混合声速两侧 10 点最大相对偏差 1.56e-16（<<3%），成立——但两侧都是 Wood 闭式，只能证明实现无偏，不证明模型适用；`ADJ-3` GM_eff 两路径最大绝对偏差 0 m、几何闭式 vs 舱容积分量相对偏差 0（<3% 成立）。`ADJ-2` 寿命律 9 点 log10 N 相对偏差最大 5.75%（对应寿命因子约 2.9×）→ **否**；两条独立标量也否：尾段 log a2 相差 2.95%、两段交接拐点相差 36.3%。这不是「数值精度不足」，而是**同一标准下两侧参数不一致**，按 DISAGREE 处理并在 §3.7 立案，不粉饰。

混合声速的 3% 判据本身不构成强验收：两侧都用 Wood 闭式（参考侧是本门独立重写、不共享常量文件），一致只说明生产方实现无偏、不含拟合；Wood 模型对真实曝气砰击的适用性需要实测或 CFD 对照，本门不做，也不声称做过。

台账 11 条 REAL 的置信度分层：n02 的 7 条最强（判据重算 + 两条代码路径独立复算）；n04 的 3 条次之（有 60 位 mpmath 对拍与独立闭式复算，但设计包线只有 p95 读法成立）；n07 的 1 条最弱（只验扫描口径，未验准确率）。三档在 §3.2 的备注列与 §3.10 中如实分开。

读表提示：§6 的「是否 <3%」列用的是**物理精度**尺子（残差/偏差 vs 3%），而每个检查自己的判据容差写在「参考/容差」列（例如参数同一性容差是 1e-6）。因此 ADJ-2 的 2.95% 在 3% 尺子下显示「是」，但它相对 1e-6 的**同一性**要求已经判 DISAGREE——两列回答的是不同问题，不能混读。

