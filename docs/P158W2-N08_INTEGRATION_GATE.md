# p158w2 集成与互裁门（n08）· 战役级验收报告

- 战役 `p158w2` · schema `tp.integration.seed_report/v1` · 生成 2026-11-09T08:52:29.402889+00:00
- 基线 commit `178fd258cd4815f45d560d5788b07e9d3244efaf` · 报告内容 sha256 `bc428bf5ac1bb5fc5476884733ae1aec6539443f21148d6e71f2faff92f30d12`
- 台账内容 sha256 `7262976d719bd7eb3c198ce890fabe73846fe0181fc0b5c6cccf92d831410f8b` · 等级分布 `{"MOCK": 2, "NOT_SUPPORTED": 0, "REAL": 2, "STUB": 0, "WITHHELD": 4}`
- 机器自检 I1–I5：**5/5 满足**

## 1 案例介绍（物理背景 / 战役几何 / 参数表）

战役 p158w2（plan 440，父战役 158）在**同一窗口**内播种 7 个验证面：3 个求解器前沿（TensorLBM 旋转参考系内部流场、TensorDEM 点颗粒两向耦合、TensorFVM 共轭传热 CHT）、2 个船舶工程面（电力推进全链噪声、压载水舱极限强度）、2 个平台治理面（提交写入边界门、NSGA-II 多目标优化框架）。n08 不是再造一个物理模型，而是把这 7 个面**串成一条可机器互裁的链**：把各自的自证折算成同一套证据等级、把跨域口径冲突显式检出、把缺口结构化交给下一波仲裁者。

三对互裁面之所以选它们，是因为每对都牵涉**同一个物理量在两个实现里被两种口径表达**：①水下辐射噪声（URN）传播口径——噪声链的声学出口是否与既有 URN 节点同口径；②Ergun 稠密极限——DEM 侧曳力体密度 f=β·u 与连续侧压力梯度 dp/dz 在稠密堆积端必须满足 Model-A 分组 f=ε·(dp/dz)；③CHT 能量账本——共轭传热的 inflow/outflow/imbalance 必须满足平台既有的 1% 守恒门，并与一维导热的解析热流一致。

工程参数表（全部来自工件，未手抄）：①Ergun 对照工况 u_s=0.05 m/s、d=2 mm、ρ=1000 kg/m³、μ=1e-3 Pa·s，孔隙率 ε∈[0.4,0.9] 共 11 点；②Gidaspow 混合曳力扫描 ε∈[0.4,0.9] 共 13 点（含 0.79/0.80/0.81 三点以分辨切换点）；③CHT 工况 nx=6、ny=4、dx=0.1 m、dy=0.05 m、κ=1e-5、T_west=400 K、T_east=300 K、南北绝热，解析锚 T(x)=400−100x/0.6、Q=κΔT/L·H；④平台守恒约定阈值 1%（backend/services/ice_integration/gate.py:125）。

| 节点 | 仓 | 判据统计 (P/F/W) | 通过率 | 总体判定 | 能力等级 | 基线态 |
|---|---|---|---|---|---|---|
| `p158w2-n01-lbm-rotating-frame` | TensorLBM | 0/0/0 | — | **WITHHELD** | WITHHELD | BRANCH_ONLY |
| `p158w2-n02-dem-two-way-coupling` | TensorDEM | 0/0/0 | — | **WITHHELD** | WITHHELD | BRANCH_ONLY |
| `p158w2-n03-fvm-conjugate-heat` | TensorFVM | 0/0/0 | — | **WITHHELD** | WITHHELD | BRANCH_ONLY |
| `p158w2-n04-electric-propulsion-noise` | ship-performance-platform | 2/2/0 | 50.0% | **FAIL** | MOCK | BRANCH_ONLY |
| `p158w2-n05-ballast-tank-strength` | ship-performance-platform | 5/1/0 | 83.3% | **FAIL** | REAL | BRANCH_ONLY |
| `p158w2-n06-write-boundary-gate` | TensorPlatform | 6/0/0 | 100.0% | **PASS** | REAL | BRANCH_ONLY |
| `p158w2-n07-multi-objective-opt` | TensorPlatform | 0/0/0 | — | **WITHHELD** | WITHHELD | BRANCH_ONLY |

## 2 怎么做的（方法 / 流程 / 工具链）

**门骨架（平台机制，零领域语义）**：`tools/integration_gate/` 五个模块——`sampler`（声明式量测：模块/函数/参数网格/常量参数都是数据，三条轨迹 kind=callable|script|expression，provenance 钉住被测模块 sha256）、`compare`（七类一致性检查：轨迹逐点、极限端点、跳变比、标量阈值、标量互比、剖面偏差、退化检测）、`baseline`（只读 git 事实：fork 点、领先 main 提交数、main 漂移量、是否已合并）、`ledger`（证据定级 + honest FAIL 保护 + 确定性 sha256）、`report`（播种报告 JSON/markdown）。域内叙述、判据阈值、算例工况全部来自 `data/campaigns/p158w2/*.json` 与 `runners/*.py`，代码里没有任何物理域词条（有 pytest 门用注入词表反查）。

**证据链（零手抄）**：节点自证由 `ingest` 从**原始工件**机器抽取——n04 从平台报告 9769 的 JSON 载荷按 `criteria_map` 取数并用 `op/threshold` **重算**判据（不采信报告自述结论），n05/n06 从各自 acceptance.json 按 `groups/numeric` 路径取数；每份自证记录来源 ref + sha256，原始工件快照存 `data/campaigns/p158w2/raw/`。

**定级规则（写进台账 JSON，可被独立复算）**：R1 `level=min(claim, 判据上限, 互裁上限)`；R2 honest FAIL 不可覆盖（capability_gate 角色判据 FAIL ⇒ 能力 ≤ MOCK，基础台账里 honest_fail 条目不得抬高）；R3 未合并分支证据 ⇒ `status=PROVISIONAL`；R4 条目排序 + 内容 sha256；R5 无证据 ⇒ WITHHELD（无自证节点也必须登记为 `:unattested`，不许「漏登记」当「没能力」）。判据角色分 `capability_gate`（验实现）与 `case_verdict`（对算例的工程判决）——后者 FAIL 是结论不是缺陷，不压实现等级。

**基线对齐（I4）**：跨仓节点在**各自 worktree** 里跑同一套只读 git 事实（`merge-base`、`rev-list --count`、`diff --name-only`），因此 7/7 都能给出 fork 点/领先数/main 漂移量；未并入 main 的分支强制要求 `interface_contract`（可由节点声明，或由本门探针派生并标注 derived + 模块 sha256）。

**fail-closed**：工件 schema 违约 → 退出码 2 并写 `gate_error.json`；轨迹导入/脚本失败 → `status=error` → 相关检查 `WITHHELD`（绝不 AGREE）；缺指标同样 WITHHELD。

复现命令：

```bash
# 1) 从原始工件机器摄取出节点自证（报告快照 → attestation）
python3 -c "from pathlib import Path; from tools.integration_gate.ingest import ingest_campaign; ingest_campaign(Path('data/campaigns/p158w2'))"
# 2) 跑整门：轨迹量测 → 互裁 → 台账 → 播种报告 → 图
python3 -m tools.integration_gate.cli --campaign p158w2 --data data/campaigns/p158w2 \
  --out artifacts/campaigns/p158w2 --repo-root . --main main --self-repo TensorPlatform \
  --base-ledger artifacts/capability_ledger/capability_ledger.json \
  --pytest-summary artifacts/campaigns/p158w2/pytest_summary.json --figures --strict
# 3) 生成本论文级报告（数字全部从 seed_report.json 注入）
python3 -m tools.integration_gate.docgen --report artifacts/campaigns/p158w2/seed_report.json \
  --prose data/campaigns/p158w2/doc_prose.json --out docs/P158W2-N08_INTEGRATION_GATE.md \
  --figures artifacts/campaigns/p158w2/figures
# 4) 测试
python3 -m pytest tests/test_integration_gate.py -q
```

## 3 计算结果（程序化注入，零手抄）

### 3.1 互裁结论与逐检查数字

### ADJ-1-urn-propagation · 电力推进噪声链 URN 传播口径 × 既有 URN 节点

- 类型 `convention` · 结论 **DEGENERATE** · 能力等级上限 `MOCK` · 对照侧 `ABSENT_ON_MAIN：既有 URN 节点在 main 基线不可见（平台仓 backend/ 与 ship-performance-platform 主干均无 URN 导出），对照侧缺失`
- n04 报告自述 URN 为 Simplified model；其水下 SPL 恒为 0，而同一次求解的辐射效率 σ>0 且舱室 SPL 非零 ⇒ 水下传播分支是占位实现，无法与任何 URN 口径互裁。

| 检查 | 结论 | 严重度 | 关键数字 |
|---|---|---|---|
| `degenerate` | DEGENERATE | blocking | driver=0.04395; response=0 |
| `metric_below` | DISAGREE | advisory | value=0.01307; threshold=0.01; ratio_to_threshold=1.307 |

### ADJ-2-ergun-dense-limit · DEM 两向耦合 Ergun 稠密极限 × 既有 Ergun 节点（平台基线参考实现）

- 类型 `limit` · 结论 **AGREE** · 能力等级上限 `REAL` · 对照侧 `PRESENT_ON_MAIN：backend/services/ice_integration/nodes_downstream.py::ergun_pressure_gradient（平台主干已合并）`
- 三向对照：①两套独立实现的经典 Ergun 逐点一致；②稠密端（ε=0.4）极限值一致；③固相曳力 f=β·u 在稠密分支必须等于 ε·(dp/dz)，并在 Gidaspow 切换点检出不连续。

| 检查 | 结论 | 严重度 | 关键数字 |
|---|---|---|---|
| `trace_agreement` | AGREE | blocking | max_rel_dev=1.672e-16; n_points=11; tol=1.000e-09 |
| `endpoint_agreement` | AGREE | blocking | rel_dev=0; b=3.105e+04; param=0.4; tol=1.000e-09 |
| `trace_agreement` | AGREE | blocking | max_rel_dev=2.701e-16; n_points=8; tol=1.000e-09 |
| `jump_ratio` | DISAGREE | advisory | max_ratio=2.113; raw_ratio=0.4732; threshold=1.5 |

### ADJ-3-cht-energy-ledger · CHT 能量账本 × 既有热力学/守恒约定（平台 1% 守恒门）

- 类型 `closure` · 结论 **AGREE** · 能力等级上限 `REAL` · 对照侧 `PRESENT_ON_MAIN：backend/services/ice_integration/gate.py:125 的守恒残差门 max_residual < 0.01（平台主干既有约定）`
- n03 的能量账本 (inflow/outflow/imbalance) 必须满足平台既有守恒约定，并与一维导热的解析解（线性剖面 + Q=κΔT/L·H）逐点一致。

| 检查 | 结论 | 严重度 | 关键数字 |
|---|---|---|---|
| `metric_below` | AGREE | blocking | value=8.522e-14; threshold=0.01; ratio_to_threshold=8.522e-12 |
| `metric_agreement` | AGREE | blocking | rel_dev=4.212e-14; a=3.333e-04; b=3.333e-04; tol=1.000e-09 |
| `profile_deviation` | AGREE | blocking | max_abs_dev=1.307e-12; scaled_dev=3.338e-15; n_points=6; tol=1.000e-09 |

### 3.2 能力台账增量（证据定级）

| 能力 | 节点 | 等级 | 状态 | 合并态 | honest_FAIL | 备注 |
|---|---|---|---|---|---|---|
| `p158w2-n01-lbm-rotating-frame:unattested` | `p158w2-n01-lbm-rotating-frame` | **WITHHELD** | PROVISIONAL | BRANCH_ONLY | — |  |
| `p158w2-n02-dem-two-way-coupling:unattested` | `p158w2-n02-dem-two-way-coupling` | **WITHHELD** | PROVISIONAL | BRANCH_ONLY | — |  |
| `p158w2-n03-fvm-conjugate-heat:unattested` | `p158w2-n03-fvm-conjugate-heat` | **WITHHELD** | PROVISIONAL | BRANCH_ONLY | — |  |
| `ship.electric_propulsion_noise_chain` | `p158w2-n04-electric-propulsion-noise` | **MOCK** | PROVISIONAL | BRANCH_ONLY | Y |  |
| `ship.urn_underwater_propagation` | `p158w2-n04-electric-propulsion-noise` | **MOCK** | PROVISIONAL | BRANCH_ONLY | Y |  |
| `ship.ballast_tank_strength` | `p158w2-n05-ballast-tank-strength` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `platform.write_boundary_gate` | `p158w2-n06-write-boundary-gate` | **REAL** | PROVISIONAL | BRANCH_ONLY | — |  |
| `p158w2-n07-multi-objective-opt:unattested` | `p158w2-n07-multi-objective-opt` | **WITHHELD** | PROVISIONAL | BRANCH_ONLY | — |  |

### 3.3 基线对齐（I4）

| 节点 | 分支 | commit | 领先 main | fork 后 main 漂移 | 契约必需/已有 |
|---|---|---|---|---|---|
| `p158w2-n01-lbm-rotating-frame` | agent/m8492 | `30593ddc8c47` | 575 | 0 | Y/— |
| `p158w2-n02-dem-two-way-coupling` | agent/m8493 | `6c2bf51d224a` | 492 | 0 | Y/Y |
| `p158w2-n03-fvm-conjugate-heat` | agent/m8494 | `6cb5639ce3e6` | 76 | 0 | Y/Y |
| `p158w2-n04-electric-propulsion-noise` | agent/m8495 | `9796e719ecb1` | 1 | 0 | Y/— |
| `p158w2-n05-ballast-tank-strength` | agent/m8496 | `7c082a65ab31` | 1 | 0 | Y/— |
| `p158w2-n06-write-boundary-gate` | agent/m8497 | `059686961bdc` | 88 | 0 | Y/— |
| `p158w2-n07-multi-objective-opt` | agent/m8498 | `c429cfc156bb` | 89 | 0 | Y/Y |

### 3.4 遗留缺口与下一波播种提案

| 缺口 | 类型 | 说明 |
|---|---|---|
| `gap:p158w2-n01-lbm-rotating-frame:no_attestation` | missing_evidence | 节点在本门运行时没有可机器复算的验收自证工件 |
| `gap:p158w2-n01-lbm-rotating-frame:in_flight` | node_note | 本门运行时节点仍在写 src/ 与 tests/，无 acceptance 工件可见 |
| `gap:p158w2-n02-dem-two-way-coupling:no_attestation` | missing_evidence | 节点在本门运行时没有可机器复算的验收自证工件 |
| `gap:p158w2-n02-dem-two-way-coupling:in_flight` | node_note | 节点仍在写 two_way_coupling/*（ergun_sweep/added_mass 等），无 acceptance 工件 |
| `gap:p158w2-n03-fvm-conjugate-heat:no_attestation` | missing_evidence | 节点在本门运行时没有可机器复算的验收自证工件 |
| `gap:p158w2-n03-fvm-conjugate-heat:in_flight` | node_note | 节点仍在写 cht/{flatplate,polar}.py，无 acceptance 工件 |
| `gap:p158w2-n04-electric-propulsion-noise:honest_fail` | honest_fail | 验证实现本身的门判据 FAIL（honest FAIL 不可覆盖，等级 ≤ MOCK） |
| `gap:p158w2-n04-electric-propulsion-noise:urn_placeholder` | node_note | 水下辐射 SPL 恒为 0（占位），与 σ>0、舱室 SPL 33.7 dB 自相矛盾 |
| `gap:p158w2-n05-ballast-tank-strength:honest_fail` | case_verdict_fail | 工程判决 FAIL（算例结论，非实现缺陷）: C_buckling_utilisation_reference |
| `gap:p158w2-n07-multi-objective-opt:no_attestation` | missing_evidence | 节点在本门运行时没有可机器复算的验收自证工件 |
| `gap:p158w2-n07-multi-objective-opt:in_flight` | node_note | 本门运行时只有 tests/multiobjective/test_nsga2_framework.py 与模块草稿，无 acceptance 工件 |
| `gap:p158w2-n01-lbm-rotating-frame:contract_missing` | interface_contract_missing | BRANCH_ONLY 节点未声明接口契约 |
| `gap:p158w2-n04-electric-propulsion-noise:contract_missing` | interface_contract_missing | BRANCH_ONLY 节点未声明接口契约 |
| `gap:p158w2-n05-ballast-tank-strength:contract_missing` | interface_contract_missing | BRANCH_ONLY 节点未声明接口契约 |
| `gap:p158w2-n06-write-boundary-gate:contract_missing` | interface_contract_missing | BRANCH_ONLY 节点未声明接口契约 |
| `gap:ADJ-1-urn-propagation:degenerate` | inter_adjudication_conflict | degenerate 结论 DEGENERATE: 驱动量=Maidanik 辐射效率 σ>0；响应=水下 SPL。响应恒零 ⇒ DEGENERATE（占位实现） |
| `gap:ADJ-1-urn-propagation:metric_below` | inter_adjudication_conflict | metric_below 结论 DISAGREE: 同一节点的上游（机电）侧判据实测 1.31% > 1%，作为 advisory 记录：噪声链入口先越界 |
| `gap:ADJ-2-ergun-dense-limit:jump_ratio` | inter_adjudication_conflict | jump_ratio 结论 DISAGREE: Gidaspow 在 ε=0.8 处硬切换 → 曳力跳变；检出并量化（不压等级，作为下一波连续化提案依据） |

| seed | 类型 | 理由 | 验收提示 |
|---|---|---|---|
| `attest:p158w2-n01-lbm-rotating-frame` | attestation_backfill | 无自证 → 本门无法定级（WITHHELD） | acceptance.json 含逐条判据 measured/threshold/op + 输入 sha256 |
| `attest:p158w2-n02-dem-two-way-coupling` | attestation_backfill | 无自证 → 本门无法定级（WITHHELD） | acceptance.json 含逐条判据 measured/threshold/op + 输入 sha256 |
| `attest:p158w2-n03-fvm-conjugate-heat` | attestation_backfill | 无自证 → 本门无法定级（WITHHELD） | acceptance.json 含逐条判据 measured/threshold/op + 输入 sha256 |
| `attest:p158w2-n07-multi-objective-opt` | attestation_backfill | 无自证 → 本门无法定级（WITHHELD） | acceptance.json 含逐条判据 measured/threshold/op + 输入 sha256 |
| `contract:p158w2-n01-lbm-rotating-frame` | interface_contract | BRANCH_ONLY 节点未声明接口契约 | 声明导出符号/端口单位/稳定性承诺 + 兼容性测试 |
| `contract:p158w2-n04-electric-propulsion-noise` | interface_contract | BRANCH_ONLY 节点未声明接口契约 | 声明导出符号/端口单位/稳定性承诺 + 兼容性测试 |
| `contract:p158w2-n05-ballast-tank-strength` | interface_contract | BRANCH_ONLY 节点未声明接口契约 | 声明导出符号/端口单位/稳定性承诺 + 兼容性测试 |
| `contract:p158w2-n06-write-boundary-gate` | interface_contract | BRANCH_ONLY 节点未声明接口契约 | 声明导出符号/端口单位/稳定性承诺 + 兼容性测试 |
| `adjudicate:ADJ-1-urn-propagation` | cross_domain_reconcile | degenerate 结论 DEGENERATE: 驱动量=Maidanik 辐射效率 σ>0；响应=水下 SPL。响应恒零 ⇒ DEGENERATE（占位实现） | 冲突两侧给出统一口径推导 + 数值复算 ≤1e-9 |
| `adjudicate:ADJ-2-ergun-dense-limit` | cross_domain_reconcile | jump_ratio 结论 DISAGREE: Gidaspow 在 ε=0.8 处硬切换 → 曳力跳变；检出并量化（不压等级，作为下一波连续化提案依据） | 冲突两侧给出统一口径推导 + 数值复算 ≤1e-9 |

### 3.5 机器自检 I1–I5

| 判据 | 是否满足 | 数字 | 判据文字 |
|---|---|---|---|
| I1_capability_registration | PASS | `{"levels_complete": 7, "nodes": 7, "registered": 7}` | 7 节点能力条目全部登记，证据等级标注完整 |
| I2_inter_adjudication_pairs | PASS | `{"pairs": 3, "verdicts": {"ADJ-1-urn-propagation": "DEGENERATE", "ADJ-2-ergun-dense-limit": "AGREE", "ADJ-3-cht-energy-ledger": "AGREE"}, "with_verdict": 3}` | 3 对互裁面一致性报告生成 |
| I3_seed_report | PASS | `{"gaps": 18, "nodes": 7, "schema": "tp.integration.seed_report/v1"}` | 结构化 JSON 报告含节点验收状态/能力增量/遗留缺口 |
| I4_baseline_alignment | PASS | `{"contracts_present": 3, "contracts_required": 7, "nodes": 7, "states": {"p158w2-n01-lbm-rotating-frame": "BRANCH_ONLY", "p158w2-n02-dem-two-way-coupling": "BRANCH_ONLY", "p158w2-n03-fvm-conjugate-heat": "BRANCH_ONLY", "p158w2-n04-electric-propulsion-noise": "BRANCH_ONLY", "p158w2-n05-ballast-tank-strength": "BRANCH_ONLY", "p158w2-n06-write-boundary-gate": "BRANCH_ONLY", "p158w2-n07-multi-objective-opt": "BRANCH_ONLY"}}` | 所有节点基线/合并态判定完成，未合并分支接口契约状态已注明 |
| I5_pytest | PASS | `{"command": "python3 -m pytest tests/test_integration_gate.py -q", "exit_code": 0, "failed": 0, "file": "tests/test_integration_gate.py", "passed": 27, "tail": "27 passed, 1 warning in 0.76s", "tool": "pytest"}` | pytest ≥5 全绿 |

## 4 结果图

![图 1 互裁 ADJ-2 的轨迹对照：两套独立 Ergun 实现在 ε∈[0.4,0.9] 全段重合（左轴 log-log 数值，右轴相对偏差 |a−b|/max，点线为容差 1e-9）。最大相对偏差 1.7e-16，即机器精度级一致。](artifacts/campaigns/p158w2/figures/fig1_trace_agreement.png)

*图 1 互裁 ADJ-2 的轨迹对照：两套独立 Ergun 实现在 ε∈[0.4,0.9] 全段重合（左轴 log-log 数值，右轴相对偏差 |a−b|/max，点线为容差 1e-9）。最大相对偏差 1.7e-16，即机器精度级一致。*

![图 2 三对互裁 × 七类检查的结论矩阵（绿色 AGREE / 红色 DISAGREE / 橙色 DEGENERATE / 灰色 WITHHELD），格内标注关键数字。ADJ-1 的退化检测与 ADJ-2 的切换点跳变是本门最重要的两个检出。](artifacts/campaigns/p158w2/figures/fig2_pair_checks.png)

*图 2 三对互裁 × 七类检查的结论矩阵（绿色 AGREE / 红色 DISAGREE / 橙色 DEGENERATE / 灰色 WITHHELD），格内标注关键数字。ADJ-1 的退化检测与 ADJ-2 的切换点跳变是本门最重要的两个检出。*

![图 3 能力台账增量：每节点按证据等级堆叠（REAL/MOCK/WITHHELD），黑虚线为节点判据通过率。4 个节点因无可复算自证停在 WITHHELD（占位登记，不自称也不否定）。](artifacts/campaigns/p158w2/figures/fig3_ledger_levels.png)

*图 3 能力台账增量：每节点按证据等级堆叠（REAL/MOCK/WITHHELD），黑虚线为节点判据通过率。4 个节点因无可复算自证停在 WITHHELD（占位登记，不自称也不否定）。*

## 5 注意点（踩坑 / 局限 / 适用范围）

**1) 门是「证据折算机」，不是「物理裁判」。** 它对 n02/n03 的结论来自**探针量测**（在兄弟 worktree 里跑它们的模块），不是它们的验收自证——所以台账把这两个节点登记为 `:unattested`/WITHHELD，而不是 REAL。量测只证明「这两处接口在其当前态下数值自洽」，不证明其验收判据全部通过。

**2) 兄弟 worktree 证据是本机时限定的。** traces.json 里 n02/n03 的 `import_root` 指向 `/root/tp-agent/work/m849{3,4}/src`，属跨租户只读引用：provenance 已钉住被测模块 sha256 与 `external=True`，路径缺失时轨迹自动 `error → WITHHELD`。因此这两个互裁结论的复现需要该 worktree 存在；报告里已显式标注，不许当成 main 基线内可复现。

**3) URN 侧的「退化」是结构性发现，不是数值误差。** n04 报告自述 URN 为 Simplified model，其 `radiation.SPL_underwater=0`，而同一次求解给出 σ=0.04395 与舱室 SPL=33.67 dB——驱动量非零而响应恒零，任何 URN 口径都无法与之互裁。本门按 R1/互裁上限把它压到 MOCK，并播下 `cross_domain_reconcile` 提案。同时既有 URN 节点在 main 基线不可见（`ABSENT_ON_MAIN`），对照侧缺失本身也要记账。

**4) Gidaspow 切换点跳变是 advisory，不是 FAIL。** ε=0.79→0.80 曳力跳变 2.11 倍（f 由 3264→1544 量级），这是混合律硬切换的已知行为（连续分支内 f=ε·dp/dz 精确成立，偏差 2.7e-16）。本门把它记成 advisory 缺口 + 播种提案（连续化权重函数），不压等级——否则会把「模型的已知近似」错判成「实现错误」。

**5) 判据角色必须显式。** n05 参考工况的屈曲利用率 1.194>1 是**工程判决 FAIL**（不加防倾肘板时该加筋方案确实不满足），不是实现缺陷；本门用 `role=case_verdict` 使其不压实现等级，同时把 honest FAIL 原样列进缺口与报告。设计改进工况屈曲利用率 0.653 证明判据有判别力。若把两类 FAIL 混为一谈，要么误杀实现、要么掩盖真实工程风险。

**6) 适用范围。** 本门只对「已被声明成数据」的量做互裁：没有轨迹规格的量（例如 n01 的 Taylor-Couette 解析锚、n07 的 GD/IGD）无法被第三方复算，只能等节点自证。所以本战役的 I1–I5 达标不代表物理正确性达标，只代表**证据链合规**。

## 6 误差判定（关键量 vs 基准，逐项回答是否 <3%）

| 互裁检查 | 关键量 | 参考/容差 | 相对偏差 | 是否 <3% |
|---|---|---|---|---|
| `degenerate` | 定性检查 | None | — | 不适用（定性） |
| `metric_below` | `ratio_to_threshold` | 0.01 | 1.307 | 否（未达约定门） |
| `trace_agreement` | `max_rel_dev` | 1.000e-09 | 1.672e-16 | 是 |
| `endpoint_agreement` | `rel_dev` | 1.000e-09 | 0 | 是 |
| `trace_agreement` | `max_rel_dev` | 1.000e-09 | 2.701e-16 | 是 |
| `jump_ratio` | `max_ratio` | 1.5 | 2.113 | 见上（非相对量） |
| `metric_below` | `ratio_to_threshold` | 0.01 | 8.522e-12 | 是 |
| `metric_agreement` | `rel_dev` | 1.000e-09 | 4.212e-14 | 是 |
| `profile_deviation` | `scaled_dev` | 1.000e-09 | 3.338e-15 | 是 |

说明：`max_ratio` 是**分支切换处的跳变比**（不是数值误差），1 表示完全连续；`ratio_to_threshold` = 实测值/约定阈值，<1 表示优于约定门。未达门的 honest FAIL 由下文逐条列出，不粉饰。

**结论**：三对互裁的 blocking 检查里，凡有基准的量全部 <3%（实测 1e-16~1e-13 量级，远优于 3%）；唯一 >3% 的是**概念性差异**而非数值误差：①ADJ-2 的 Gidaspow 切换点跳变 2.11 倍（分支切换，非离散误差，advisory）；②n04 G1 判据 1.31% vs 判据 1%（honest FAIL，判据未达标，如实呈报）；③n05 参考工况屈曲利用率 1.194（工程判决，非误差）。

**无基准的项**：ADJ-1 的 URN 传播口径没有可对照的既有节点（`ABSENT_ON_MAIN`），因此不填造误差数字，只给退化判定；四个未自证节点（n01/n02/n03/n07 的验收面）无验收基准可算，等级 WITHHELD——如实申报，不粉饰。

