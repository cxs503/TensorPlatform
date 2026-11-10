# P143-W2C 战役级验收门 · 跨域集成验证与诚实未就绪裁决（N11）

> 节点：`P143W2C-N11`（gate，TensorPlatform）· 分支 `agent/m9197` · 战役 plan 501 `P143-W2C`
> 产物：`ops/taskplane/campaign_acceptance.py`、`ops/taskplane/interface_contract_lint.py`、
> `tests/test_campaign_acceptance.py`（28 条）、`tests/test_interface_contract_lint.py`（11 条）、
> `artifacts/p143w2c/*`（合约 / 配置 / 三次裁决产物 / 4 张图 + sha256）
> **裁决：`not_ready`（honest not_ready，不粉饰、不覆盖）** —— 4/6 判据通过，阻塞项 = A（7 个 task 节点仍 in-flight）、D（11 节点全 unproven）

---

## 1. 案例介绍：这场战役要被验收的是什么

P143-W2C（plan 501）是 11 节点的跨**三仓**战役：求解器仓（TensorLBM/TensorDEM 2 节点）、
船舶仓（ship-performance-platform 6 节点）、平台仓（TensorPlatform 3 节点，含本门）。
本节点不产生任何领域物理量，它要回答的命题是：

> **在给定时刻，这场战役能否被验收？若不能，阻塞项逐条是什么、由哪条机器判据裁定？**

### 1.1 对象与参数表（判据参数，非几何参数）

| 项 | 取值 | 来源 |
|---|---|---|
| 计划 | plan 501 `P143-W2C`，11 节点（10 task + 1 gate） | `GET /tensor/campaigns/plans/501/nodes` |
| DAG 形状 | 10 个 task 节点互不依赖；gate 节点依赖全部 10 个 | 同上 `dependencies` 字段 |
| 战役验收预算 | `min_pass_nodes=9`，`max_honest_fail_nodes=2`，`allow_unproven=false` | `artifacts/p143w2c/gate_config.json` |
| 状态词汇表 | closed={done,failed,completed,cancelled,verified}；failed={failed,aborted,error}；open={pending,in-flight,in_progress,queued,not_started,blocked} | 同上（注入，非代码硬编码） |
| 接口合约规模 | 11 节点 / 20 条网关需求键 / 1 条依赖边 | `artifacts/p143w2c/integration_contract.json` |
| 治理探针目标 | N03 `tools/evidence_chain/backfill_wiring.py`、N04 `tools/abm/six_dim_power.py` | `artifacts/p143w2c/governance_spec.json` |

### 1.2 为什么"战役验收"不能靠一个总分

战役验收是**六个互相独立的命题的合取**。任何一条被别的手段"顺手覆盖"，就会出现绿皮通道：

| 判据 | 命题 | 若缺失会怎样 |
|---|---|---|
| `a_all_task_nodes_closed` | 节点是否都收口（closed ∪ failed） | 在飞就报完成 |
| `b_failed_nodes_documented` | 失败是否有 `reason`+`evidence` 双字段台账 | 失败被静默洗成通过 |
| `c_closed_nodes_evidenced` | 收口节点是否有平台报告可查 | `done` 但零证据 |
| `d_integration_gate` | 跨域成果是否可组合（形状契约 + 拓扑契约） | 各节点自说自话，下游字段上游没产 |
| `e_governance_integrated` | 成熟化工具是否真进了工具链 | 模块只在某个分支里存在 |
| `f_capability_ledger_registered` | 能力是否已登记且证据可解析 | 做完无人知晓，下波重复发明 |

---

## 2. 怎么做的：三层分工 + 一个汇总裁决

### 2.1 不重算既有判据，只做汇总裁决（关键设计）

平台已有三层机制，本节点**不重写任何一层**：

```
inheritance_gate            → 单节点「是否真验收」+ 跨节点同量互裁          （A/B 面）
campaign_integration_verify → 形状契约 + 拓扑契约 + pass/honest_fail/unproven/fail 四分类
campaign_acceptance（本节点）→ A~F 六判据合取 → verdict + 阻塞项 + 台账登记  （战役面）
interface_contract_lint（本节点）→ 设计期 L1~L6 接口自洽（**不构成验收**）
```

`campaign_acceptance.accept()` 把 `d_integration_gate` 直接**引用**集成门报告的六条子判据
与四分类计数（原样搬运，不二次裁决），因此不存在"两套判据互相打掩护"的通道。

### 2.2 设计期 / 证据期严格分工（本节点新增的第二件东西）

集成门的拓扑判据刻意**不采信合约自填的 `produces`**（否则需求方写什么都自洽，检查恒真），
代价是：战役早期全部节点在飞时它只能输出 `unproven`，**接口面写错了要等证据期才炸**。
`interface_contract_lint` 补上设计期那一格，六条规则：

| 规则 | 内容 |
|---|---|
| L1 `node_resolved` | 合约 `node_id` 必须能在快照里解析到节点 |
| L2 `identity_unique` | `node_id`/`ext_id` 不得重复 |
| L3 `dep_contract_present` | DAG 每条依赖边的上游必须有合约条目 |
| L4 `requires_declared` | 每个需求键必须有某个上游 declare |
| L5 `unit_type_conflict` | 同一键多节点 declare 时类型/量纲必须一致 |
| L6 `orphan_requires` | 需求键不可只由自己 declare（自给自足=写错） |

### 2.3 治理探针的五检（把"进了工具链"变成机裁判据）

`governance_check()` 对每个成熟化模块做五检：**文件在位 → 可导入（包内相对导入走
`import_name`，顶层脚本走 `spec_from_file_location`）→ 导出符号齐 → 有测试文件真实引用
该模块名 → 端到端冒烟**（真跑该模块的 pytest，退出码必须为 0）。冒烟失败会把
`integrated` 降级为 `smoke_failed`——"导入成功"不算"能跑"。

### 2.4 复现命令（三次裁决全部可复算）

```bash
# 1) 只读拉快照（含 plan/milestones/nodes/missions/reports）
python3 ops/taskplane/campaign_acceptance.py --fetch-api --plan 501 \
  --contract artifacts/p143w2c/integration_contract.json \
  --config   artifacts/p143w2c/gate_config.json \
  --governance artifacts/p143w2c/governance_spec.json \
  --ledger-spec artifacts/p143w2c/capability_registration.json \
  --snapshot-out artifacts/p143w2c/plan501_snapshot.json \
  --integration-json-out artifacts/p143w2c/integration_gate.json \
  --json-out artifacts/p143w2c/acceptance_report.json \
  --md-out   artifacts/p143w2c/acceptance_report.md
# 2) 设计期接口自洽
python3 ops/taskplane/interface_contract_lint.py \
  --contract artifacts/p143w2c/integration_contract.json \
  --snapshot artifacts/p143w2c/plan501_snapshot.json \
  --json-out artifacts/p143w2c/contract_lint.json
# 3) 图件（读报告 JSON，零手抄）
python3 tools/p143w2c_n11_figures.py --acceptance ... --integration ... --contract ... \
  --out-dir artifacts/p143w2c/figures
```

退出码纪律：`0 = pass`，`2 = not_ready`，`3 = 输入/网络错误`。**未就绪绝不返回 0。**

---

## 3. 计算结果（全部程序化注入，零手抄）

### 3.1 战役级裁决（快照 input_digest `5ffdc71673cd…`）

| 判据 | 结论 | 关键数字 |
|---|---|---|
| `a_all_task_nodes_closed` | **FAIL** | total=10 closed=3 failed=0 **open=7** unknown=0 |
| `b_failed_nodes_documented` | PASS（空集） | failed=0 documented=0 undocumented=0 ghost=0 |
| `c_closed_nodes_evidenced` | PASS | covered=3 gaps=0（N03→#10767/#10761，N05→#10758，N08→#10782） |
| `d_integration_gate` | **FAIL** | 集成门 verdict=not_ready；pass=0 honest_fail=0 unproven=11 fail=0 |
| `e_governance_integrated` | PASS | 2/2 模块 integrated；冒烟 16 + 103 项 pytest 通过 |
| `f_capability_ledger_registered` | **FAIL**（首跑）→ **PASS**（台账+本文档落盘后复跑） | 首跑 problems=`evidence_unresolved:docs/…`；终跑 5 条台账条目证据全部可解析 |

**本战役裁决：`not_ready`（4/6 判据通过）。阻塞项 = A（7 个 task 节点仍 in-flight）、D（11 节点全 unproven）。**
这不是缺陷，是本门的正确输出：战役此刻**确实**不可验收。

### 3.2 节点状态普查（判据 A 的原始数据）

| 节点 | 仓 | status | bucket | mission | 报告数 |
|---|---|---|---|---|---|
| N01 SC-LBM | TensorLBM | in-flight | open | 9187 | 0 |
| N02 DEM | TensorDEM | in-flight | open | 9188 | 0 |
| N03 回填器 | TensorPlatform | **done** | closed | 9189 | 2（#10767,#10761） |
| N04 统计功效门 | TensorPlatform | in-flight | open | 9190 | 0 |
| N05 波浪增阻/失速 | ship-performance-platform | **done** | closed | 9191 | 1（#10758） |
| N06 时域运动 | ship-performance-platform | in-flight | open | 9192 | 0 |
| N07 航向保持 | ship-performance-platform | in-flight | open | 9193 | 0 |
| N08 STF RAO | ship-performance-platform | **done** | closed | 9194 | 1（#10782） |
| N09 扭振-冰扭矩 | ship-performance-platform | in-flight | open | 9195 | 0 |
| N10 砰击/上浪 | ship-performance-platform | in-flight | open | 9196 | 0 |

### 3.3 设计期接口自洽（判据 B 的"上游面"）

| 指标 | 数值 |
|---|---|
| 合约节点 / 计划节点 | 11 / 11（覆盖率 100%，集成门 `c_contract_coverage` = PASS） |
| L1~L6 违规 | **0 error / 0 warn** |
| 网关需求键 | 20 条，全部有上游 declare 且类型/量纲一致 |
| 量纲抽检 | `lbm.surface_tension_sigma`=N/m、`ship.torsion_natural_freqs`=Hz、`ship.slam_pressure_max`=Pa |

**含义（必须精确表述）**：接口**设计**自洽；**证据**供给 20/20 键仍为 0（见 §4 图 3 右柱）。
设计期检查**不构成任何验收通过**。

### 3.4 跨域集成门（判据 D 的六个子判据）

| 子判据 | 结论 | 数字 |
|---|---|---|
| `a_schema_all_valid` | FAIL | valid=0/11（无节点发布证据包） |
| `b_edges_consistent` | FAIL | n_edges=1（gate←10 上游）consistent=0 **declared_only=1** inconsistent=0 |
| `c_contract_coverage` | PASS | contracted=11/11 |
| `d_acceptance_budget` | FAIL | pass=0 < min_pass_nodes=9；honest_fail=0 ≤ 2 |
| `e_no_undocumented_fail` | PASS | fail=0 |
| `f_no_unproven` | FAIL | unproven=11（零容忍） |

### 3.5 治理工具链五检（判据 E）

| 模块 | 导入 | 导出符号 | 测试引用 | 端到端冒烟 | status |
|---|---|---|---|---|---|
| N03 `tools/evidence_chain/backfill_wiring.py` | ok | ok（4/4） | ok | **16 passed**（0.50 s） | integrated |
| N04 `tools/abm/six_dim_power.py` | ok | ok（4/4） | ok | **103 passed**（61.00 s） | integrated |

合计 119 项 pytest 实跑通过 —— **N03/N04 确实在平台工具链里能导入、能被测试引用、能跑绿**。
但这只证明「工具在位」，**不证明**它们的领域判据（A/A 误报率、FWER、链完整度）已达标；
故台账里二者等级封顶 `WITHHELD`（见 `artifacts/p143w2c/capability_ledger.json`）。

---

## 4. 结果图

| 图 | 内容 | 文件 |
|---|---|---|
| 图 1 | 节点状态普查：bucket 计数 + 9 个未收口节点名单 | `artifacts/p143w2c/figures/fig1_node_census.png` |
| 图 2 | 判据矩阵：战役 A~F + 集成门六子判据的红绿裁决（4/12 通过） | `artifacts/p143w2c/figures/fig2_criteria_matrix.png` |
| 图 3 | 跨域接口覆盖：网关声明所需键（设计期，蓝）vs 上游证据真实供给（证据期，红，当前 0） | `artifacts/p143w2c/figures/fig3_interface_coverage.png` |
| 图 4 | 治理工具链五检矩阵（含冒烟输出尾行） | `artifacts/p143w2c/figures/fig4_governance_checks.png` |

图件均由 `tools/p143w2c_n11_figures.py` 读报告 JSON 生成，`figures_metadata.json` 记录每图 sha256。

---

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **`failed` 既是"收口"也是"失败"**：状态分桶必须先判 failed 再判 closed，否则未记录的失败会被
   洗成干净收口（本节点首版实现即踩此坑，单测 `test_status_bucket_vocabulary` 钉死顺序）。
   `failed` 计入 closed 是刻意的：战役验收允许诚实失败，代价是判据 B 必须给出 `reason`+`evidence`。
2. **空集真空通过必须显式标注**：判据 C 在"零个节点收口"时 pass=True（`vacuous=true`）。
   这是逻辑真空真，但**不是**证据；detail 里显式标注，避免被当成强证据引用。
3. **包内相对导入会让文件级导入失败**：`backfill_wiring.py` 用 `from .evidence_chain import …`，
   首跑被判 `unavailable`。修法是双路径导入（`import_name` 包内导入 + 文件导入兜底），
   单测 `test_governance_package_relative_import` 覆盖。
4. **"测试文件存在"不等于"测试引用该模块"**：探针做内容级 needle 匹配，空壳测试文件判
   `unreferenced`（单测覆盖），防止 `tests/xxx.py` 空文件骗绿。
4b. **`done` ≠ 可验收（本波实测，最重要的一条）**：N03/N05/N08 在战役表里已是 `done` 且各有 1~2 篇
   报告，但集成门仍判 `unproven`，blockers 首条为 `reproduction_verdict_missing` +
   `no_evidence_bundle_published`。原因是**节点侧没有发布 `inheritance_evidence` 证据包**
   （schema 见 `schemas/campaign_node_evidence.schema.json`），报告正文里也没有逐条判据测量值。
   这正是本门要关掉的那条绿皮通道：**状态位不是证据**。修法在节点侧（补证据包 + 逐条判据
   measurement），不在本门放宽判据。
5. **局限一（时间快照性）**：本裁决是**时刻快照**结论。节点后续收口后必须重跑本门；
   `input_digest` 变更即视为新裁决，旧结论不得复用。
6. **局限二（只裁形状与拓扑）**：集成门不评价物理正确性（如 SC-LBM 界面张力是否真达标），
   物理判据在节点继承门；本门 `pass` 只是"可组合 + 有意愿书"，不等于"物理对"。
7. **局限三（治理探针的性能）**：冒烟真跑 119 项 pytest ≈ 62 s，是门的主要耗时；
   若要进 CI，建议把冒烟单独 job 与静态四检分离。

---

## 6. 误差判定（逐项列出，显式回答"是否 <3%"）

本节点是**治理/集成门**，不产生物理量；可比的"基准"是**程序化真值**（API 返回的节点状态、
合约声明、测试退出码），因此判定方式是**精确相等**而非相对误差：

| 关键量 | 本门输出 | 基准（独立来源） | 误差 | 是否 <3% |
|---|---|---|---|---|
| 任务节点总数 / 收口数 | 10 / 3 | 直接来自 `GET …/plans/501/nodes`（无中间换算） | 0（精确相等） | 是（=0%） |
| 未收口节点名单 | 7 个 ext_id | 同上 API 原始 status 字段 | 0 | 是 |
| 收口节点报告数 | N03=2、N05=1、N08=1 | `GET /tensor/reports?mission_id=9191` | 0 | 是 |
| 接口键违规数 | 0 error | 合约 20 条 requires × 上游 produces 逐条比对 | 0 | 是 |
| 治理冒烟结果 | 16 + 103 passed | pytest 自身退出码 + 汇总行 | 0 | 是 |
| 可复现性 | 同快照两次运行 `input_digest` 完全相同（报告全文仅冒烟耗时行不同：0.61s→0.46s、56.19s→54.15s，共 4 行差异） | `5ffdc71673cddd77` | 0 | 是 |
| 战役 pass 节点数 | 0 | min_pass_nodes=9 → 缺口 9 | 精确计数 | — |

**无物理基准量需要做 <3% 对照**：本节点未产生任何力/力矩/压力等物理量（诚实声明，不是遗漏）。
物理量的 3% 判据归各 task 节点的继承门，本门只引用其结果，不改写。

---

## 7. 结论与后续

- **本战役此刻不可验收**（honest `not_ready`）：阻塞项 A（7 节点 in-flight）与 D（11 节点 unproven）。
- 已交付**可复用机制**：战役验收门（A~F）+ 接口设计期 lint（L1~L6）+ 治理工具链五检探针，
  39 条合成域名单测（28 + 11）全绿，零领域词条硬编码。
- **后续触发条件（机器可判，无需人工）**：N01~N10 全部落到 done/failed 且各自发布证据包后，
  重跑 §2.4 命令；若 6 条判据全绿则 `verdict=pass` 退出码 0，`gate.acceptance_verdict` 方可写
  `pass` 供给链；在此之前任何"验收通过"的表述都是造假。