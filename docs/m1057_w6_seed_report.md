# 冰区航行战役·第六波 播种报告（重发版·含独立审计）
## Ice Navigation Campaign Wave 6 Seed Report — Re-issued with Independent Audit

**本报告替代报告 835（Wave 6 Seed Report），并合并报告 832 的全部有效内容。**
**This document supersedes report 835 (Wave 6 Seed Report) and merges report 832.**

| 项 / Item | 值 / Value |
|---|---|
| 工作单 / Mission | m1057（返工报告 835）· 原仲裁单 m1036 |
| 原报告 / Superseded | report id=835（同源合并：id=832） |
| 战役计划 / Campaign plan | plan_id=6《冰区航行战役·第六波 (Ice Navigation Campaign Wave 6)》，审计时 status=active |
| 提案来源 / Proposers | missions 1026 (qwen3.7-plus), 1027 (glm-5.1), 1028 (deepseek-v4-flash) |
| 提案数 → 节点数 / proposals → nodes | 18 → 10 |
| 审计时点 / Audit timestamp (UTC) | 2026-10-31T22:02:08（本报告所有数字取自此时刻的只读快照） |
| 审计工件 / Audit artefacts | `artifacts/m1057/seed_audit.py` → `artifacts/m1057/seed_audit.json`；图件 `artifacts/m1057/fig_w6_seed_audit.png` |
| 证据等级 / Evidence grade | 机器门与追溯 = **REAL**（可复现脚本 + 只读库查询）；DAG 门 = **WITHHELD**（库中依赖边为空）；repo 白名单 = **WITHHELD**（ship-platform 放行依赖 worktree 外 profile） |

### 0. 缺陷清单逐条对应 / Defect → fix mapping (rework of report 835)

| # | 原报告缺陷 / Defect in report 835 | 本报告处置 / Fix in this report | 章节 / Section |
|---|---|---|---|
| 1 | 缺目的节（无仲裁目的与背景） | 新增仲裁目的与谱系背景，含"聚合同侪之长"的仲裁定位 | §1 |
| 2 | 缺方法节（无聚合去重策略与合并逻辑） | 新增方法节：数据源、语义去重规则、合并组处置、播种步骤、审计工具链 | §2 |
| 3 | 缺审计节 | 新增三项审计：提案来源可追溯性 / 去重合并一致性 / 机器门校验结果，均附程序化工件 | §5 |
| 4 | 缺局限与待办 | 新增局限与待办（含本次审计新发现的 6 条行动项） | §8 |
| 5 | 缺误差判定节且未声明原因 | 新增误差判定节，**显式声明本报告为战役规划文档、无数值基准可比对**并给出三条原因 | §7 |
| 6 | 缺图件且无豁免声明 | 提供审计图件 1 幅；并显式声明"纯规划文档可豁免场量云图/基线比较曲线"的理由 | §4 |

---

## 1. 目的与背景 / Purpose & Background

**中文.** 本报告记录一次**仲裁播种（arbitration seeding）**：战役 plan 5《冰区航行战役·第五波》8 个节点全部到达终态后，平台按"任务不应只靠外部推动"的自治设计，派出 3 位独立提案者（missions 1026 / 1027 / 1028，三家不同模型并行、**允许重叠**，冗余交由仲裁吸收）各自提出 6 条"从未做过"的前沿提案，共 18 条；随后由仲裁任务 m1036 把这些提案**聚合成一个可派发的下一波战役计划（plan 6）**。

仲裁的目的不是"挑选赢家"（淘汰式评审），而是**聚合同侪之长**：语义重叠的提案合并为一条并吸收各家细节，互补的提案全部保留，并在其中挑出足够数量的**成熟化单**（把已有能力推向可验收的精度/规范级门槛）。这一取舍直接决定了下游 10 个开发任务的物理方向，因此播种结果必须可审计、可追溯、可复现——这也是本次返工的核心动因。

**English.** This report documents one *arbitration seeding* round. After all 8 nodes of campaign plan 5 (Wave 5) reached terminal state, the platform dispatched three independent proposers (missions 1026 / 1027 / 1028 — three different models, run in parallel, **overlap explicitly allowed**, redundancy to be absorbed by arbitration). Each proposer supplied 6 never-done frontier proposals (18 in total). Arbitration mission m1036 then aggregated them into a dispatchable next-wave campaign plan (plan 6).

The purpose of arbitration is not winner-picking but **composing peer strengths**: semantically overlapping proposals are merged into one node that absorbs the details of each contributor; complementary proposals are all retained; and a sufficient number of *maturity* nodes is selected to push existing capabilities to verifiable accuracy/code-level thresholds. Because this choice fixes the physics direction of 10 downstream development missions, the seed must be auditable, traceable and reproducible — the reason for this rework.

**谱系 / Lineage**

```
plan 5 (W5-N01..W5-N08, 收官 / closed)
  └─ missions 1026 / 1027 / 1028  (3 提案者 × 6 提案 = 18)
       └─ m1036 arbitration  →  plan 6 (id=6, 10 nodes, 3 milestones)  →  missions 1044,1045,1046,1047,1051,1052,1049,1050,1053,1048..
```

---

## 2. 方法 / Method

### 2.1 数据源与工具链 / Data sources and tool chain

| 环节 | 数据源 / Source | 读取方式 |
|---|---|---|
| 提案池 / proposal pool | `/srv/tensor/agent/proposals/p5/w1/prop-1026.json`、`prop-1027.json`、`prop-1028.json` | 逐文件 `json.load`，校验字段完整性 |
| 播种结果 / seeded state | PG `campaign_plans` / `campaign_milestones` / `campaign_nodes` (`plan_id=6`) | 只读 `psql -tA -U postgres -d tensorplatform`（`row_to_json` 聚合） |
| 机器门代码基线 / gate baseline | `backend/api/tensor_missions.py::REPO_WHITELIST`、m1036 任务书 ③ | 源码直读 |
| 审计引擎 / audit engine | `artifacts/m1057/seed_audit.py` | 可重复执行，输出 `seed_audit.json`（本报告数字的唯一来源） |
| 图件 / figure | `artifacts/m1057/make_figures.py` | 仅从 `seed_audit.json` 作图（零手抄） |

### 2.2 聚合去重策略 / Aggregation & de-duplication rules

提案池按以下**判定次序**处理（规则在播种前确定，不对个案反调）：

1. **同域同器优先合并**：若两条提案 (a) 物理对象相同（同一载荷/同一流固耦合机制），(b) 目标仓库相同或可共仓，(c) 验收判据集合有 ≥1 条可直接共用 → 判为语义重叠，合并为**单一节点**，节点 goal 同时吸收两家细节（"吸收式合并"而非"择一淘汰"）。
2. **互补保留**：不同物理域或不同失效模式 → 各自成节点，不因"看起来相近"而砍掉。
3. **能力缺口优先**：提案若能直接补上 guide/KPI 缺口（精度闸门、规范符合性），优先排入**成熟化单**。
4. **成熟化单下限**：硬门要求 ≥2 条成熟化单（goal 含"成熟化"），本次播种实际 4 条。
5. **仓库白名单约束**：节点目标仓库必须落在派发白名单内（详见 §5.3 的 WITHHELD 说明）。

### 2.3 合并逻辑与合并组 / Merge logic and merge groups

**合并组 1（→ W6-N01 多船编队协同破冰与水动力干涉）**
- 来源 A：`P5W1-01`（1027）冰航道护航编队阻力与多体尾流–碎冰交互，贡献 DEM 多船体场 + 通道再冻结率；
- 来源 B：任务书 `proposal_src` 记为 mission 1026；
- 合并后节点吸收：多体冰–船干涉 + 编队干涉系数矩阵 + Stefan 通道再冻结，并新增验收判据 (a) N=1 退化 <3%、(b) 干涉系数物理性 d→∞ 时 C_ij→0、(c) 再冻结 vs Stefan 解析解 <5%。

**合并组 2（→ W6-N02 冰–船体–推进器三场耦合与尾流冰碎化热侵蚀）**
- 来源 A：`P5W1-03`（1027）螺旋桨尾流冰块碎化与热侵蚀（LBM 湍流尾流 + 融化）；
- 来源 B：任务书 `proposal_src` 记为 mission 1028；
- 合并后节点吸收：DEM 船体–冰接触 + LBM 尾流湍流 + 碎冰热力学融化（Ranz–Marshall 热侵蚀）。

> ⚠️ **审计保留项**：报告 832 把上述两组的具体来源提案写成 `prop-1026-01` / `prop-1028-01`，与提案目录实体的主题不符（见 §5.1）。本重发版按**目录实体**更正叙述，并保留 mission 级 `proposal_src`（DB 字段未改动，未做任何写库操作）。

**互补保留（8 条）**：W6-N03 `P5W1-02`（斜面失效模式）、W6-N04 `prop-1026-02`（冰区操纵性–耐波性）、W6-N05 `P5W1-05`（吊舱推进操纵性，成熟化）、W6-N06 `prop-1026-05`（集合冰预报航线优化）、W6-N07 `P5W1-04 + prop-1028-04`（极值统计 + 压力–面积，成熟化）、W6-N08 `prop-1028-06`（阻力精度闸门，成熟化）、W6-N09 `prop-1026-06`（数字孪生贝叶斯校准）、W6-N10 `prop-1028-05`（冰盖下声学）。

**未入波 / not seeded（5 条，本次审计补声明）**：`prop-1026-03` LBM-DEM 双向耦合冰 floe 输运仿真、`prop-1026-04` 冰致振动下设备/管路疲劳评估、`P5W1-06` 冰盖热应力与压力脊萌生、`prop-1028-02` 冰粘聚断裂参数的可识别性-贝叶斯标定闭环、`prop-1028-03` 冰-船摩擦的摩擦生热-融膜-滑动弱化闭环。
理由（本次审计归纳）：或归属仓库未在派发白名单内（`TensorPhysics` 系列），或与前序节点在物理机制上高度耦合、需上游接口契约先行，或未通过"从未做过"的新颖性自检。**原报告未声明淘汰理由，属本次补齐的审计缺口。**

### 2.4 播种与机器门 / Seeding and machine gates

播种通过 SQL `INSERT`（`campaign_plans` 1 行 status=`draft`、`campaign_milestones` 3 行、`campaign_nodes` 10 行）完成；每节点 `metadata` 必含 `ext_id` / `repo` / `goal`（≥80 字任务书：物理目标 + 机器可算判据 + 纪律）/ `acceptance_criteria` / `proposal_src`，并归属 `milestone_id`。播种后按 m1036 任务书 ③ 的 7 项硬门自检，派发器再独立硬校验；本次返工**用独立脚本重跑这 7 项门**（§5.3），而不是复述当时自检结论。

---

## 3. 计算结果：播种结构 / Results — seeded structure

### 3.1 战役计划头 / Plan header

| 字段 | 值 |
|---|---|
| plan_id | 6 |
| title | 冰区航行战役·第六波 (Ice Navigation Campaign Wave 6) |
| status（审计时） | **active** |
| metadata.parent_plan | 5 |
| metadata.seed_report | {"wave": 1, "nodes": 10, "sources": [1026, 1027, 1028], "arbiters": [1036], "new_plan_id": 6} |

### 3.2 里程碑 / Milestones

| 里程碑 | 名称 | 节点数 |
|---|---|---|
| M20 | M20: 冰-结构-推进耦合与编队水动力深化 (Ice-Structure-Propulsion Coupling &  | 3 |
| M21 | M21: 操纵性/耐波性/航线优化 (Maneuvering/Seakeeping/Route Optimization | 3 |
| M22 | M22: 规范符合性/精度闸门/数字孪生/声学成熟化 (Code Compliance/Accuracy Gate/Di | 4 |

### 3.3 节点表 / Node table

| ext_id | DB id | 仓库 / Repo | 里程碑 | 白名单 | proposal_src | 派发单 mission | 状态 |
|---|---|---|---|---|---|---|---|
| W6-N01 | 65 | TensorDEM | M20 | ok | [1026, 1027] | 1044 | in-flight |
| W6-N02 | 66 | TensorDEM | M20 | ok | [1027, 1028] | 1045 | in-flight |
| W6-N03 | 67 | TensorFEM | M20 | ok | [1027] | 1046 | in-flight |
| W6-N04 | 68 | ship-platform | M21 | WITHHELD | [1026] | 1047 | in-flight |
| W6-N05 | 69 | ship-platform | M21 | WITHHELD | [1027] | 1051 | in-flight |
| W6-N06 | 70 | ship-platform | M21 | WITHHELD | [1026] | 1052 | in-flight |
| W6-N07 | 71 | ship-platform | M22 | WITHHELD | [1027, 1028] | 1049 | in-flight |
| W6-N08 | 72 | ship-platform | M22 | WITHHELD | [1028] | 1050 | in-flight |
| W6-N09 | 73 | ship-platform | M22 | WITHHELD | [1026] | 1053 | in-flight |
| W6-N10 | 74 | TensorLBM | M22 | ok | [1028] | 1048 | in-flight |

### 3.4 机器门重跑结果 / Gates re-run

| 门 / Gate | 要求 | 结果 | 备注 |
|---|---|---|---|
| 节点数 / node count | 5–12 | ✅ PASS | 实际 10 |
| ext_id 全库唯一 | 跨 plan 无重复 | ✅ PASS | 重复 0 |
| repo 白名单 | 落在派发白名单 | ⚠️ 部分 WITHHELD | `ship-platform` 不在代码常量内 → **WITHHELD** |
| DAG 无环 | Kahn 全节点出队 | ⚠️ 真空通过 | 依赖边 0 条 → **真空通过** |
| dependencies 仅本 plan | 无越界引用 | ✅ PASS | 越界 0 |
| 每里程碑 ≥1 节点 | M20/M21/M22 全覆盖 | ✅ PASS | M20=3 / M21=3 / M22=4 |
| 成熟化单 ≥2 | ≥2 | ✅ PASS | 实际 4 |

**汇总 / Summary**：7/7 项门的 pass 布尔为真，但含 **2 项条件通过**——DAG 门"真空通过"（依赖边 0 条，无依赖可环）与 repo 白名单门对 `ship-platform` 部分 WITHHELD；口径为 **硬通过 5/7 + 条件通过 2/7**，**不作"全门 REAL 通过"的宣称**。

---

## 4. 图件 / Figures

![fig_w6_seed_audit](artifacts/m1057/fig_w6_seed_audit.png)

**图 1（中文）** 左：18 条提案到节点的播种状态矩阵（绿=映射且主题一致，橙=映射但提案级归因不符，灰=未入波且原报告未声明理由）；右：7 项机器门在当前库态的重跑结果（绿=通过，橙=真空/不可验证）。数字全部由 `artifacts/m1057/make_figures.py` 从 `seed_audit.json` 渲染。

**Figure 1 (English).** Left: proposal→node seeding matrix over all 18 proposals (green = mapped and topically consistent, amber = mapped but proposal-level attribution mismatched, grey = not seeded with no stated rejection reason). Right: the 7 machine gates re-run against live DB state (green = pass, amber = vacuous or not verifiable). All numbers are rendered programmatically from `seed_audit.json`.

### 4.1 图件豁免声明 / Figure-exemption declaration

**中文.** 本报告是**战役规划/仲裁文档**：不运行任何场求解器，不产生速度场/应力场/波浪场等场量数据，也没有"本文计算结果 vs 解析解/实验/基线"的曲线可比对。因此论文级报告模板中的**场量云图**与**基线比较曲线**在本报告中**不适用（NOT_APPLICABLE）**，按"纯规划文档可豁免图件要求"处理。为避免"无图=缺交付"，本报告仍提供 1 幅由审计数据程序化生成的**审计图件**（图 1），其数据源与 §5 的审计数字同源，可被独立复算。下游每个节点的开发报告则**不得**引用本豁免，必须自附场量图与基线比较曲线。

**English.** This is a *campaign-planning / arbitration* document: no field solver is run, no velocity/stress/wave fields are produced, and there is no "computed value vs analytical/experimental/baseline" curve to plot. Hence the standard **field-contour** and **baseline-comparison-curve** figure requirements are **NOT_APPLICABLE** here and are exempted under the "pure planning document" rule. To avoid an under-delivered report, one **audit figure** (Fig. 1) is still provided, generated programmatically from the same JSON that backs §5, hence independently recomputable. Downstream node reports must **not** reuse this exemption; each must carry its own field figures and baseline comparisons.

---

## 5. 审计 / Audit

审计由 `artifacts/m1057/seed_audit.py` 在 2026-10-31T22:02:08 (UTC) 对只读快照执行，输出 `artifacts/m1057/seed_audit.json`。拓扑：**只读查询，全程未写库**（无 INSERT/UPDATE/ALTER）。

### 5.1 提案来源可追溯性审计 / Proposal-source traceability audit

- 提案总数 **18**（1026/1027/1028 各 6 条，逐文件 `json.load` 计数，不是引用旧报告）；
- 报告 832 的提案级映射表覆盖 **13/18** 条提案，**5** 条未入波（见 §2.3）；
- 节点级 mission 一致性：**10/10** 节点的"声称提案所属 mission"与 DB `metadata.proposal_src` 完全一致；
- **提案级不符 2 处**（下表 ⚠️）：mission 标签自洽，但所声称的具体提案实体与节点主题不符 → 说明 DB 的 `proposal_src` 只到 **mission 粒度**（m1036 任务书本就如此规定），提案级条目属报告作者**反推**，反推错误无机器门可拦。

| ext_id | DB proposal_src (mission) | 报告 832 声称的提案 | 目录实体主题 | 归因判定 |
|---|---|---|---|---|
| W6-N01 | [1026, 1027] | prop-1026-01, P5W1-01 | prop-1026-01 = 主动冰管理系统仿真 (active ice management)；P5W1-01 = 冰航道护航编队阻力与多体尾流-碎冰交互 (convoy channel resistance) | ⚠️ 主题不符 (topic mismatch) |
| W6-N02 | [1027, 1028] | prop-1028-01, P5W1-03 | prop-1028-01 = 非球形 DEM 接触引擎与形状标定 (non-spherical DEM contact engine)；P5W1-03 = 螺旋桨尾流冰块碎化与热侵蚀 (propeller wake fragmentation) | ⚠️ 主题不符 (topic mismatch) |
| W6-N03 | [1027] | P5W1-02 | P5W1-02 = 斜面结构冰载荷弯曲-压碎失效模式 (sloping structure ice load) | ✅ 一致 (consistent) |
| W6-N04 | [1026] | prop-1026-02 | prop-1026-02 = 离散冰场船舶运动时域仿真 (6-DOF in discrete ice fields) | ✅ 一致 (consistent) |
| W6-N05 | [1027] | P5W1-05 | P5W1-05 = 吊舱推进冰区操纵性模型 (podded-propulsion maneuvering) | ✅ 一致 (consistent) |
| W6-N06 | [1026] | prop-1026-05 | prop-1026-05 = 集合冰预报鲁棒航线优化 (robust route optimization) | ✅ 一致 (consistent) |
| W6-N07 | [1027, 1028] | P5W1-04, prop-1028-04 | P5W1-04 = 冰载荷极值统计与 IACS 设计载荷 (extreme value statistics)；prop-1028-04 = 冰压-面积效应标准化 (pressure–area standardization) | ✅ 一致 (consistent) |
| W6-N08 | [1028] | prop-1028-06 | prop-1028-06 = 冰区阻力精度闸门与样本库 (resistance accuracy gate) | ✅ 一致 (consistent) |
| W6-N09 | [1026] | prop-1026-06 | prop-1026-06 = 冰载荷数字孪生贝叶斯校准 (digital-twin calibration) | ✅ 一致 (consistent) |
| W6-N10 | [1028] | prop-1028-05 | prop-1028-05 = 冰盖下声-弹波导传播 (under-ice acoustics) | ✅ 一致 (consistent) |

**结论（中文）**：mission 级追溯 **REAL 通过**；提案级追溯为"反推 + 人工核对"，证据等级 **WITHHELD**（无提案级外键），并已发现 2 处实质不符与 5 条未声明淘汰。
**Conclusion (English)**: mission-level traceability passes on live data; proposal-level traceability is a *reconstruction* with no DB foreign key — graded WITHHELD, with 2 substantive attribution mismatches and 5 undeclared rejections found.

### 5.2 去重合并一致性审计 / De-duplication & merge consistency audit

| 审计项 | 方法 | 结果 |
|---|---|---|
| 物理域唯一领取 | 11 个物理域标签 → 节点归属映射，检查是否被 ≥2 节点重复领取 | 11 个域单节点领取，重复领取 **0** → PASS |
| 合并组是否真合并 | 检查同域提案是否落在同一节点（而非并列两节点） | 2 组合并组（N01/N02）在计划层各自成为**单一节点** → PASS |
| goal 文本重复 | 节点 goal 前 40 字符两两比对，检测复制粘贴式重复节点 | 冲突 **0** → PASS |
| 合并组"吸收细节"可验证性 | 核对合并节点 goal 是否同时包含两家机制关键词 | N01 含"多体冰–船 + 干涉系数 + Stefan 再冻结"；N02 含"DEM 接触 + LBM 尾流 + 热侵蚀" → PASS（机制层面）；但**来源提案主体（哪个提案贡献哪条机制）不可机器验证** → WITHHELD |
| 计数一致性 | 报告 832 的"18 条 → 2 合并组 + 16 互补保留 = 10 节点"算术核对 | **不一致**：映射表实际可追溯 13 条提案，5 条未入波 → 报告口径的"16 条互补保留"高估，已在本版更正 |

**结论**：去重结构本身（11 域 / 2 合并组 / 0 重复节点）**REAL 通过**；但 832 的**计数与提案级归属**存在 3 处口径错误，本版逐条更正。

### 5.3 机器门校验结果审计 / Machine-gate validation audit

审计脚本对 m1036 任务书 ③ 的 7 项硬门**独立重跑**（不采信当时自检结论）：

| 节点数 / node count | 5–12 | ✅ PASS | 实际 10 |
| ext_id 全库唯一 | 跨 plan 无重复 | ✅ PASS | 重复 0 |
| repo 白名单 | 落在派发白名单 | ⚠️ 部分 WITHHELD | `ship-platform` 不在代码常量内 → **WITHHELD** |
| DAG 无环 | Kahn 全节点出队 | ⚠️ 真空通过 | 依赖边 0 条 → **真空通过** |
| dependencies 仅本 plan | 无越界引用 | ✅ PASS | 越界 0 |
| 每里程碑 ≥1 节点 | M20/M21/M22 全覆盖 | ✅ PASS | M20=3 / M21=3 / M22=4 |
| 成熟化单 ≥2 | ≥2 | ✅ PASS | 实际 4 |

**逐门说明（中文）**
- **节点数 5–12**：10 → REAL PASS。
- **ext_id 全库唯一**：以 10 个 `ext_id` 跨 plan 全库分组查询，重复 0 → REAL PASS（此门为唯一性断言，可机器复现）。
- **repo 白名单**：`TensorDEM` / `TensorFEM` / `TensorLBM` 在 `backend/api/tensor_missions.py::REPO_WHITELIST` 内；**`ship-platform` 不在该常量内**，其放行依赖运行期 factory profile 的 `project_names`（位于 worktree 外 `.runtime/agent`，本次不可读）。6/10 节点落在 `ship-platform` 上 → 该子项判 **WITHHELD**，不宣称已验证。
- **DAG 无环**：当前库中 **0 条依赖边（全空）**，Kahn 拓扑排序"通过"属**真空通过**；报告 832 声称的 `65→66, 68→69→70, 71→73` 依赖边**在当前库态不可复现**（可能由派发/激活流程改写，本次无法确认） → **WITHHELD**。
- **dependencies 仅引用本 plan**：依赖为空 → 越界引用 0，**真空通过**。
- **每里程碑 ≥1 节点**：M20=3、M21=3、M22=4 → REAL PASS。
- **成熟化单 ≥2**：按 goal/name 含"成熟化"程序化计数 = 4（W6-N01,W6-N05,W6-N07,W6-N08）→ PASS；报告 832 声明 3 条，口径差异见 §6。

---

## 6. 注意点 / Caveats (honest findings)

以下 6 条为本次独立审计**新发现**，与原报告结论不同的地方已如实记录（不粉饰）：

1. DAG/dependencies: 库中 10 节点 dependencies 全为空（declared_edges=0），故 Kahn 判定'无环'为真空通过；报告 832 声称的 '65→66, 68→69→70, 71→73' 依赖边在当前库态不可复现 → DAG 门证据等级判 WITHHELD。
2. 提案级归因不符（2 处）：W6-N01 声称吸收 prop-1026-01，但目录实体为《主动冰管理系统仿真》，与 N01 的编队水动力干涉主题不符；W6-N02 声称吸收 prop-1028-01，目录实体为《非球形（凸多面体/超二次曲面）DEM 接触引擎与形状效应三锚标定》，与冰-船体-推进器三场耦合主题不符。mission 级 proposal_src=[1026, 1027]/[1027, 1028] 自洽，不符发生在报告自行反推的提案级表。
3. 淘汰未声明：18 条提案中 13 条出现在映射表，5 条未入波（prop-1026-03、prop-1026-04、P5W1-06、prop-1028-02、prop-1028-03）；报告原文只写'16 条互补保留'，与可追溯计数不一致。
4. 成熟化单计数口径不一：按 goal/name 含'成熟化'硬门统计为 4 条（W6-N01,W6-N05,W6-N07,W6-N08），报告 832 声明 3 条（N05/N07/N08）；W6-N01 名称即含'成熟化'，≥2 的硬门在两种口径下都通过，但报告数字应改用程序化计数。
5. plan.status 现为 active、10/10 节点 in-flight 且已绑 mission_id 1044,1045,1046,1047,1051,1052,1049,1050,1053,1048：报告 832 的 'status=draft' 是播种时刻快照，已不反映当前库态，重发报告须标注时点。
6. repo 白名单：ship-platform 不在 backend REPO_WHITELIST 常量内（TensorLBM,TensorPhysics,TensorDEM,TensorFEM,TensorFVM,TensorPlatform），其放行依赖运行期 factory profile（worktree 外）；6/10 节点落在该仓库 → 该子项判 WITHHELD，不宣称已验证。

补充说明（中文）：`status=draft` → `active` 的漂移**不是错误**，而是平台在播种后自动激活并派发；本版报告的作用是把"播种时刻快照"与"审计时刻库态"分开陈述，避免引用化石状态做决策（此处与入场纪律一致）。

---

## 7. 误差判定 / Error criteria

**中文（结论先行）**：本报告**无任何"计算值 vs 基准"的数值对比**，因此 **3% / 15% 精度判据在本报告中 NOT_APPLICABLE**；"是否 <3%" 的回答是 **不适用（N/A）**，而非"未达标"。三条显式理由：

1. **本报告不产生物理量**：全文未运行 TensorDEM/FEM/LBM/ship-platform 中的任何求解器，无量纲阻力系数、无应力、无幅值谱可对标；本报告的对象是**计划结构与元数据**，不是流场结果。
2. **播种期无数值基准可比对**：节点 `acceptance_criteria` 中的 "<3%"、"<5%"、">=1000 工况"等是**下发给下游节点的阈值**，阈值本身不能当作"本报告的计算结果"来对标——否则即构成自证。这些阈值的实测对标义务由各节点开发报告承担（例如 W6-N01 的 N=1 退化判据、W6-N08 的 ≥3 条盲算基线）。
3. **可判定的量是离散计数，误差恒为 0**：§5 的审计量均为**离散计数/集合比较**（提案 18 条、节点 10 个、门 7 项、重复数 0），与库内快照**精确相符**（|Δ|=0），不存在百分比误差；其可信度约束是"数据源可读 + 脚本可复现"，而非数值精度。

**English.** This report contains **no computed-vs-baseline numerical comparison**, so the 3%/15% accuracy criterion is **NOT_APPLICABLE** here; the answer to "is error <3%?" is *N/A*, not "failed". Three explicit reasons: (i) no solver was run and no physical quantity is produced — the object under report is plan structure and metadata; (ii) the "<3% / <5% / >=1000 cases" strings are **thresholds delegated to downstream nodes**, and using them as this report's own results would be self-证; (iii) every audited quantity is a discrete count or set comparison, so |Δ|=0 exactly, and the relevant credibility constraint is reproducibility (script + read-only snapshot), not numeric accuracy.

**对标义务移交 / Delegation of the benchmarking duty**：plan 6 的精度要求由以下节点在其开发报告中逐项实测对标，本报告仅登记阈值来源——W6-N01（N=1 退化 <3%、Stefan 再冻结 <5%）、W6-N04（6-DOF 与 MMG 基准）、W6-N07（Gumbel/Weibull 与 IACS UR I / ISO 19906 设计压力）、W6-N08（≥3 条基线盲算 ≤10% 实测）、W6-N10（Pekeris 锁模解析锚）。

---

## 8. 局限与待办 / Limitations & TODO

**局限 / Limitations**

1. **DAG 依赖缺失**：plan 6 的 10 个节点在库中 `dependencies` 全空（0 条边），五波战役的依赖结构实际**未落到数据层**；任何"依赖驱动调度"的推断当前无据。
2. **提案级不可追溯**：`campaign_nodes.metadata.proposal_src` 仅记录提案者 **mission id**，没有提案版本号/文件哈希，提案池文件被改写后无法回溯当时输入。
3. **淘汰过程无留痕**：5 条提案未入波且当年未记录理由，本次只能事后归纳，属**过程证据缺口**（历史事实不可追补）。
4. **白名单口径不确定**：`ship-platform` 的放行依赖运行期 factory profile，仓库内常量与运行期配置**不同源**，审计无法闭环（6/10 节点受影响）。
5. **快照时效性**：本报告结论只对 2026-10-31T22:02:08 (UTC) 库态成立；plan/nodes 状态此后会继续漂移。

**待办 / TODO（按优先级）**

| # | 待办项 | 判据（可机器裁） |
|---|---|---|
| T1 | 让播种流程把 `dependencies` 真正持久化，并加"依赖非空"硬门（否则 DAG 门恒为真空通过） | plan 6 节点 `metadata.dependencies` 非空比例 = 100%，且 Kahn 全节点出队 |
| T2 | `proposal_src` 升级为 `{mission, proposal_id, file_sha256}` 结构，建立提案级外键 | 随机抽 3 节点，提案级可回指到池文件哈希 |
| T3 | 淘汰提案写入 `metadata.rejected = [{proposal_id, reason}]`，报告中禁止"无理由消失" | 18 条提案全部有去向（入波/合并/淘汰+理由） |
| T4 | 把 repo 白名单统一到单一真源（仓库常量 or 运行期 profile 二选一），并让审计可读 | 审计脚本能对 10/10 节点判定白名单，无 WITHHELD |
| T5 | 报告模板增加"规划文档豁免"显式字段（`figures_exempt: {reason}`、`error_criteria: NOT_APPLICABLE + reason`），避免返工 | 规划类报告提交时自动带豁免字段，不再触发缺图/缺误差门 |
| T6 | 报告数字改为构建期注入（本报告已示范：`build_report.py` 全部数字取自 `seed_audit.json`） | 报告内 0 处手抄数字，可由工件重放生成 |

---

## 9. 复现方式 / Reproducibility

```bash
# 1) 独立审计（只读，重跑机器门 + 追溯 + 去重一致性）
python3 artifacts/m1057/seed_audit.py
#   -> artifacts/m1057/seed_audit.json

# 2) 由图件脚本生成审计图（仅读 seed_audit.json）
python3 artifacts/m1057/make_figures.py
#   -> artifacts/m1057/fig_w6_seed_audit.png

# 3) 构建并（可选）发布报告（数字全部注入自 seed_audit.json）
python3 artifacts/m1057/build_report.py            # 只生成 docs/ + payload
python3 artifacts/m1057/build_report.py --publish  # 额外 POST /tensor/reports
```

**不写库、不改表结构**：审计脚本只有 `SELECT`；播种数据在 m1036 中已写入，本次返工未做任何 DB 写入（`plan.status=active` 的漂移由平台派发流程造成，非本单改动）。

---

*报告生成：`artifacts/m1057/build_report.py`（数字零手抄，全部注入自 `artifacts/m1057/seed_audit.json`，审计时点 2026-10-31T22:02:08 UTC，报告构建时点 2026-10-31T22:04:40+00:00 UTC）。*