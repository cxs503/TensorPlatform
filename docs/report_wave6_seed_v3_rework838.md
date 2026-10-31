# 冰区航行战役·第六波 播种报告（返工修正版 v3）

> **报告谱系与返工声明**：本文件是**报告 id=838 的返工修正版（v3）**，原工作单
> `mission_id=1036`（AUTONOMY-ARBITER 仲裁播种者），返工工单 `mission_id=1067`。
> 本次返工**只重写报告文本与报告 POST，未改任何业务代码**；新增的
> `artifacts/audit_wave6_dag_provenance.py`（只读 SELECT 的依赖边来源审计）与
> `artifacts/build_report_v3.py`（报告/图件生成器）均为 `artifacts/` 内审计工件，不触碰服务端与业务逻辑。
>
> 数据来源：机器门与溯源数字取自 `artifacts/audit_wave6_seed.json`（指纹 `907819bb461cd720`）；
> 依赖边来源取自 `artifacts/audit_wave6_dag_provenance.json`（指纹 `bf327a8852a043e1`，
> 复算于 2026-10-31T22:12:00+00:00）。报告内所有计数/等级由 `artifacts/build_report_v3.py` 程序化注入，零手抄。

## 0 缺陷清单逐条对应（RESUBMIT-1 / RESUBMIT-2 / MINOR-1）

| 缺陷编号 | 返工要求 | 本版处置（逐条闭环） | 证据位置 |
|---|---|---|---|
| RESUBMIT-1 | §1.3 依赖边断言与审计工件矛盾：须标注为「播种时声明」，或删除断言并注明 DB dependencies 字段为空 | §1.3 正文改标「**播种时声明**」的 5 条边，并新增**四层来源表**（L1 播种工件声明 / L2 计划元数据 / L3 节点 ARRAY 列 / L3b 节点元数据）+ 逐层 Kahn 复算。返工时点只读实测：L2 计划元数据**未持久化 `dag_edges`**（键仅有 arbiters, new_plan_id, nodes, sources, wave）；L3b 节点元数据**无 `dependencies` 键**（命中 0/10 节点）——即**元数据层读数为空**，这正是「把声明当实测」的机制；L3 列当前 5 条且与 L1 逐条一致，但与之同源（`created_at == updated_at` = True，无独立来源），**仍不作实测证据**，等级 WITHHELD。 | §1.3；artifacts/audit_wave6_dag_provenance.json |
| RESUBMIT-2 | §5.3 DAG 门须显式标「真空通过」，证据等级判 WITHHELD，与报告 837 口径一致 | §5.3 表中 DAG 门（G4）的「证据等级」列改为 **WITHHELD（真空/条件通过）**，正文显式写出「真空通过（vacuous pass）」及其成立条件（元数据层边数 0 ⇒ Kahn 必然全序）；§6.2 误差表、§8 证据台账同口径登记，不再出现「DAG 已实测无环」类越界断言。 | §5.3、§6.2、§8 |
| MINOR-1 | 审计图件缺失（m1036/artifacts/wave6_dag.png 存在但未引用） | §4 提供两图：图 1 = 既有声明依赖图 `artifacts/wave6_dag.png`（由 m1036 播种工件复制入本 worktree 归档并引用）；图 2 = 本版程序化生成的审计矩阵图 `artifacts/wave6_audit_matrix.png`（机器门×证据等级 / 依赖边四层来源 / 溯源计数），由 `artifacts/build_report_v3.py` 生成。 | §4；figures 内嵌 base64 |

## 1 案例介绍

### 1.1 物理背景与战役定位

第六波战役（Wave 6, campaign plan `6`，审计时点状态 `active`）面向**极地/冰区船舶
总体性能**：把冰载荷（DEM 离散冰场）、结构响应（FEM）、推进-尾流碎化（LBM）、操纵-耐波（Cummins 时域）
与规范符合性（ISO 19906 / IACS 极值统计）串成可机算判据链。本报告是**播种（seed）阶段**成果：把三位
提案者的 18 条提案聚合去重为可派发的战役 DAG（10 节点 / 3 里程碑），
并给出可复算的机器门证据与**证据等级**。

### 1.2 输入与参数表（本波无船型几何，故无 Cb/L/B 校核）

战役为规划层任务，不含具体船型几何输入，因此不涉及 Cb/L/B 几何验证（该纪律适用于后续节点级阻力/推进
计算任务）。

| 项 | 值 |
|---|---|
| 提案池目录 | /srv/tensor/agent/proposals/p5/w1 |
| 提案者任务 | 1026, 1027, 1028 |
| 提案总数 | 18 |
| 目标战役计划 | plan 6（审计时点 status=active） |
| 父计划 | 5 |
| 里程碑数 | 3 |
| 节点数 | 10 |
| 审计指纹（机器门/溯源） | `907819bb461cd720` |
| 审计指纹（依赖边来源） | `bf327a8852a043e1` |

### 1.3 战役结构（里程碑-节点-DAG）与**依赖边的来源分层**

| 里程碑 id | 代号 | 主题 | 节点数 |
|---|---|---|---|
| 20 | M20 | 冰-结构-推进耦合与编队水动力深化 (Ice-Structure-Propulsion Coupling & Formation Hydrodynamics) | 3 |
| 21 | M21 | 操纵性/耐波性/航线优化 (Maneuvering/Seakeeping/Route Optimization) | 3 |
| 22 | M22 | 规范符合性/精度闸门/数字孪生/声学成熟化 (Code Compliance/Accuracy Gate/Digital Twin/Acoustics Maturity) | 4 |

**依赖边（前驱→后继）：播种时声明的 5 条** —— `W6-N01→W6-N02, W6-N04→W6-N05, W6-N04→W6-N06, W6-N05→W6-N06, W6-N07→W6-N09`。

> ⚠️ **证据口径声明（本版修正核心）**：上列 5 条边是**播种（seed）阶段写入时声明的依赖关系**，
> **不是**独立实测得到的依赖拓扑，**不得**表述为「DB 实测」。各存储层的可核查状态如下
> （`artifacts/audit_wave6_dag_provenance.py` 于 2026-10-31T22:12:00+00:00 只读取数 + 逐层 Kahn 复算）：

| 层 | 取数来源 | 节点 | 边数 | Kahn | 证据等级 | 说明 |
|---|---|---|---|---|---|---|
| L1 | 播种工件声明 artifacts/m1036_seed_report.json | 10 | 5 | 全序出队 | WITHHELD | 有条件通过：边数与声明同源，无独立第三方来源可证 |
| L2 | 计划元数据声明 campaign_plans.metadata->seed_report | 10 | 0 | 全序出队 | WITHHELD | 真空通过（vacuous pass）：边数 0，Kahn 必然全序 |
| L3 | 节点表 ARRAY 列 campaign_nodes.dependencies | 10 | 5 | 全序出队 | WITHHELD | 有条件通过：边数与声明同源，无独立第三方来源可证 |
| L3b | 节点元数据 JSONB 键 campaign_nodes.metadata->'dependencies' | 10 | 0 | 全序出队 | WITHHELD | 真空通过（vacuous pass）：边数 0，Kahn 必然全序 |

**关键读数（如实列出）**：

1. **L2 计划元数据未持久化 `dag_edges`**：`campaign_plans.metadata->'seed_report'` 的键仅有
   `arbiters, new_plan_id, nodes, sources, wave`，**不含** `dag_edges`（`plan_meta_dag_edges_present=False`）。
   只从计划元数据取依赖边 ⇒ **0 条**。
2. **L3b 节点元数据无 `dependencies` 键**：10 个节点 `metadata` 中命中该键者 0/10。
   只从节点元数据取依赖边 ⇒ **0 条**。这一层正是原报告把「声明」写成「实测」的直接机制。
3. **L3 节点表 ARRAY 列 `campaign_nodes.dependencies` 当前确有 5 条边**，且与 L1 声明逐条一致
   （`L1==L3` = True）。但该列与声明**同源**：播种同批 INSERT 写入，
   全部节点 `created_at == updated_at`（True），无后续 UPDATE 痕迹，
   故**不构成独立实测证据**，仅作「声明已落库且一致」的证据（等级 WITHHELD）。
4. 结论：**依赖边一律标注为「播种时声明」**；DAG 门证据等级 **WITHHELD**（详见 §5.3）。

节点清单（`deps（声明）` 列为**声明**的前驱，`—` 表示无前驱声明；末列为 `acceptance_criteria` 条数）：

| id | ext_id | repo | milestone | deps（声明） | 验收判据数 |
|---|---|---|---|---|---|
| 65 | W6-N01 | TensorDEM | 20 | —（无前驱声明） | 6 |
| 66 | W6-N02 | TensorDEM | 20 | 65 | 6 |
| 67 | W6-N03 | TensorFEM | 20 | —（无前驱声明） | 6 |
| 68 | W6-N04 | ship-platform | 21 | —（无前驱声明） | 6 |
| 69 | W6-N05 | ship-platform | 21 | 68 | 7 |
| 70 | W6-N06 | ship-platform | 21 | 68, 69 | 5 |
| 71 | W6-N07 | ship-platform | 22 | —（无前驱声明） | 9 |
| 72 | W6-N08 | ship-platform | 22 | —（无前驱声明） | 7 |
| 73 | W6-N09 | ship-platform | 22 | 71 | 5 |
| 74 | W6-N10 | TensorLBM | 22 | —（无前驱声明） | 7 |

## 2 怎么做（方法 / 流程 / 工具链）

### 2.1 聚合去重与播种流程

1. **装配输入**：读三位提案者（1026, 1027, 1028）的提案池
   `/srv/tensor/agent/proposals/p5/w1`，共 18 条，保留 `id/repo/physics_domain/why_frontier`。
2. **语义重叠判定**：按「物理对象 × 数值方法」二元组比对；可共享同一场交换接口者合并为单一节点并吸收
   两家可取细节。
3. **互补保留**：重叠判据不成立者按提案原样成节点，不改物理目标。
4. **元数据契约**：每节点 `metadata` 含 `ext_id`（全库唯一前缀 `W6-N..`）/`repo`（白名单）/
   `goal`（≥80 字任务书：物理目标 + 机器可算判据 + 纪律）/`acceptance_criteria`（数组）/`proposal_src`。
5. **播种**：只 INSERT/UPDATE 数据，不改表结构（禁 ALTER）：`campaign_plans` 1 行 +
   `campaign_milestones` 3 行 + `campaign_nodes` 10 行。
6. **审计复检**：独立脚本从 DB 复算机器门 + 溯源/去重三类审计；本版追加**依赖边来源专项审计**
   （四层取数 + 逐层 Kahn）。

### 2.2 审计工具链（可复跑）

| 环节 | 实现 | 判据 |
|---|---|---|
| 取数（只读） | `psql -At -c` + `json_agg(row_to_json(t))` | 与报告数字同源，禁止手抄 |
| 机器门复算 | `artifacts/audit_wave6_seed.py`：计数/集合运算 + Kahn 拓扑排序判环 | 10 项，实测 10/10 通过 |
| 提案级溯源 | 字符二元组 Jaccard(`node.name+goal[:1500]`, `proposal.title`)，阈值 0.10 | 可证实 11/18；未达阈值判 WITHHELD |
| 归因对账 | 解析原报告 832 自述，与 DB 逐条比对 | 证实 11/13，保留 2，矛盾 0 |
| **依赖边来源审计（本版新增）** | `artifacts/audit_wave6_dag_provenance.py`：L1/L2/L3/L3b 四层取数 + 逐层 Kahn | 层间一致性 + 每层证据等级；元数据层边数 0 ⇒ 真空通过 ⇒ WITHHELD |
| 报告与图件生成 | `artifacts/build_report_v3.py` 读两个审计 JSON 注入 + 画审计矩阵图 | 数字零手抄，可由 `artifacts/test_report_v3_consistency.py` 反查 |

## 3 计算结果（数字表，程序化注入）

### 3.1 机器门复算结果（10/10 PASS，其中 G4 判 WITHHELD）

| 门 | 判据 | 实测 | 状态 | 证据等级 |
|---|---|---|---|---|
| G1 节点数 5-12 | 5 ≤ n ≤ 12 | 10 | PASS | REAL（可复算） |
| G2 ext_id 全库唯一 | 重复数=0 | 0 | PASS | REAL（可复算） |
| G3 repo 在白名单 | 越界仓库=0 | 0 | PASS | REAL（可复算） |
| G4 DAG 无环 | 拓扑可全序 | 残留=0 | PASS | **WITHHELD**（真空/条件通过） |
| G5 dependencies 只引本 plan | 越界依赖=0 | 0 | PASS | REAL（可复算） |
| G6 每里程碑 ≥1 节点 | min(覆盖) ≥1 | min=3 | PASS | REAL（可复算） |
| G7 成熟化单 ≥2 | count ≥2 | 3 | PASS | REAL（可复算） |
| G8 goal ≥80 字 | 不达标=0 | 0 | PASS | REAL（可复算） |
| G9 acceptance_criteria 非空 | 空数组=0 | 0 | PASS | REAL（可复算） |
| G10 节点必填字段齐全 | 缺字段=0 | 0 | PASS | REAL（可复算） |

> **G4（DAG 无环）口径**：其 PASS 的前提是**采用播种声明的依赖图**；在元数据层（L2/L3b）边数 0 时
> Kahn 必然把所有节点出队，该 PASS 属**真空通过（vacuous pass）**，**证据等级 WITHHELD**，
> 与同源报告 837 口径一致（§5.3 详述）。其余 9 门是对 DB 实测值的直接计数/集合判定，等级 REAL。

### 3.2 溯源与去重记账

| 项 | 实测 | 分母/说明 |
|---|---|---|
| A1 任务级 `proposal_src` 合规 | 10/10 | 非空、全整数、⊆ 三来源任务 |
| A1 各来源任务节点覆盖 | 1026:4, 1027:5, 1028:4 | 每个提案者均有节点落地 |
| A2 提案级可机读证实 | 11/18 | 相似度阈值 0.1；未达阈值判 WITHHELD |
| A3 原报告归因自述对账 | 证实 11 / 保留 2 / 矛盾 0（共 13 条声明） | 全部归因被证实的节点 8 个 |
| 提案记账平衡 | 11 + 7 = 18 | 平衡=True（总数 18） |
| 去重合并组 | 3 | 原报告自述 2，不一致 |

### 3.3 依赖边来源复算（本版新增）

| 层 | 边数 | 与 L1 一致 | Kahn 残留 | 证据等级 |
|---|---|---|---|---|
| L1 | 5 | — | 无 | WITHHELD |
| L2 | 0 | False | 无 | WITHHELD |
| L3 | 5 | True | 无 | WITHHELD |
| L3b | 0 | n/a | 无 | WITHHELD |

## 4 结果图

**图 1｜播种声明的战役 DAG**（10 节点 / 5 条**声明**边；来源 `artifacts/wave6_dag.png`，
由原播种任务工件 `m1036/artifacts/wave6_dag.png` 复制入本 worktree 归档后引用）。
注意：该图展示的是**声明拓扑**，其证据等级随 L1 = 声明（WITHHELD），不是实测拓扑。

![wave6_dag](artifacts/wave6_dag.png)

**图 2｜审计矩阵图**（本版程序化生成 `artifacts/wave6_audit_matrix.png`）：(a) 机器门 10 项 × 实测 × 证据等级
（G4 单独着色为 WITHHELD）；(b) 依赖边四层来源的边数（L2/L3b 为 0 ⇒ 真空通过）；
(c) 溯源/记账计数（A1/A2/A3/记账平衡）。图中全部数字由两个审计 JSON 注入，无手绘数字。

![audit_matrix](artifacts/wave6_audit_matrix.png)

> **图件性质说明**：本战役为规划层文档，**无场量云图、无与解析解/文献的比较曲线**（本层无连续物理场
> 计算）；故图件为**审计证据图**（拓扑图 + 判据矩阵）。物理场云图与基线比对曲线将由 Wave 6 的
> 节点级任务（W6-N01…N10，含 W6-N08 冰区阻力精度闸门）交付。

## 5 审计（独立节）

### 5.1 审计 A：提案来源可追溯性

- **A1（任务级契约层）**：非合规 0 项 / 10 节点；各来源任务覆盖
  1026:4, 1027:5, 1028:4。→ 等级 REAL。
- **A2（提案级重建层）**：DB 未持久化提案 id，故以标题相似度重建：可证实 11/18
  （61.1%）；未达阈值侧与未归因清单见
  `audit_wave6_seed.json → proposal_level_reconstruction`（`rows / orphans / unresolved_sides`）。
  → 等级 REAL（阈值 0.10 属方法设定，非实测基准）。
- **A3（对账层：原报告自述 vs 实测）**：13 条声明中证实 11、
  保留（WITHHELD）2、矛盾 0：

| 节点 | 原报告声明来源 | 复算证实 | 保留(WITHHELD) | 状态 |
|---|---|---|---|---|
| W6-N01 | prop-1026-01 + P5W1-01 | P5W1-01 | prop-1026-01 | PARTIAL |
| W6-N02 | prop-1028-01 + P5W1-03 | P5W1-03 | prop-1028-01 | PARTIAL |
| W6-N03 | P5W1-02 | P5W1-02 | — | CONFIRMED |
| W6-N04 | prop-1026-02 | prop-1026-02 | — | CONFIRMED |
| W6-N05 | P5W1-05 | P5W1-05 | — | CONFIRMED |
| W6-N06 | prop-1026-05 | prop-1026-05 | — | CONFIRMED |
| W6-N07 | P5W1-04 + prop-1028-04 | P5W1-04, prop-1028-04 | — | CONFIRMED |
| W6-N08 | prop-1028-06 | prop-1028-06 | — | CONFIRMED |
| W6-N09 | prop-1026-06 | prop-1026-06 | — | CONFIRMED |
| W6-N10 | prop-1028-05 | prop-1028-05 | — | CONFIRMED |

### 5.2 审计 B：去重合并一致性

| 检查 | 结果 |
|---|---|
| 多源节点数（任务级 ≥2 来源） | 3 个 |
| 提案记账平衡 | 归因 11 + 未归因 7 = 18 / 18（balanced=True） |
| 合并消耗提案数 | 4 |
| 种子元数据 vs DB 一致 | plan_id_matches=True, node_count_matches=True, sources_match=True, parent_plan_matches=True |
| 原报告计数自述 vs 实测 | proposals_total: 自述 18 vs 实测 18（一致）; nodes_after_dedupe: 自述 10 vs 实测 10（一致）; merged_groups: 自述 2 vs 实测 3（不一致）; plan_status: 自述 draft vs 实测 active（不一致）; complementary_kept: 自述 16 vs 实测 14（不一致） |

### 5.3 审计 C：机器门校验结果（DAG 门口径 = WITHHELD）

复算口径与任务书一致但彼此独立（不读原报告结论）：节点数区间、`ext_id` 全库唯一（重复 0）、
仓库白名单（越界 0）、**Kahn 拓扑排序判环（在播种声明依赖图上残留 0）**、
依赖仅引本 plan（越界 0）、每里程碑 ≥1 节点（最小 3）、
成熟化单 ≥2（实测 3）、goal ≥80 字（不达标 0）、
`acceptance_criteria` 非空（空 0）、必填字段齐全（缺 0）。
合计 **10/10 PASS**。

> **DAG 门（G4）证据等级 = WITHHELD（真空通过 / vacuous pass）**
> ① 按**元数据层**取依赖（L2 计划元数据缺 `dag_edges`、L3b 节点元数据无 `dependencies` 键）时，
> 边数 = 0，Kahn 必然全序出队 ⇒ 通过属**真空通过**；
> ② 按 **L3 ARRAY 列**取依赖时边数 = 5，Kahn 残留 0（非真空，但为稀疏图），
> 然而该列与播种声明**同源**（同批 INSERT、无后续 UPDATE），**不构成独立实测**；
> ③ 因此无论按哪层取数，DAG 门都不满足 REAL 证据标准 ⇒ 判 **WITHHELD**，与报告 837 口径一致。
> 本报告**不**把 DAG 无环写成「已实测验证」。

### 5.4 审计发现清单（如实列缺陷，不粉饰）

| 编号 | 发现 | 级别 | 处置 |
|---|---|---|---|
| F1 | 提案级溯源未入 DB（仅任务 id），致 7/18 提案无机器证据归因 | 中 | 量化并判 WITHHELD；建议节点 metadata 增记 `proposal_id` |
| F2 | 原报告 §1 自述「合并 2 组 / 16 条互补保留」与 DB 实测冲突（实际多源节点 3 个，按其自述推算互补保留应为 14 条） | 中 | 以实测为准，原自述作废 |
| F3 | 原报告称 plan `status=draft`，审计时点实测 `active` | 低 | 记录审计时点真实状态，引用状态须带时点 |
| F4 | 原报告 838 缺陷 RESUBMIT-1 / RESUBMIT-2 / MINOR-1（本返工对象） | 高 | 本版 §1.3 / §5.3 / §4 逐条修补 |
| F5 | 依赖边在计划元数据层未持久化（L2 键 arbiters, new_plan_id, nodes, sources, wave 无 `dag_edges`），元数据层无法自证 DAG | 中 | 新增 provenance 审计脚本；建议播种时将 `dag_edges` 同步写入 plan metadata（本单禁改业务代码，未实施，列 R1） |

## 6 误差判定（逐项显式回答「是否 <3%」）

### 6.1 关键量的性质声明

本报告为**战役规划（seed）文档**：关键量全部是**布尔校验项与整数计数**（机器门 10 项、溯源归因数、
记账平衡），**不存在连续数值量**（无阻力、无功率、无运动响应、无应力），因此**没有可与实验/文献/解析解
逐项比对的数值基准**。精度纪律中「>15% 不可接受」「关键量 vs 基准误差 <3%」两条判据的定义域为物理连续量；
对布尔/计数项，相对误差**不成立（分母无物理意义）**。本节因此报告**可机读的等价判据**：自述值 vs 独立复算值的
**一致性**（不一致项数 = 0 为最强结论）。

### 6.2 逐项误差判定

| 关键量 | 判据 | 实测 | 量纲 | 数值基准 | 是否<3% |
|---|---|---|---|---|---|
| 节点数 | 5 ≤ n ≤ 12 | 10 | 整数计数（区间包含） | 无连续基准 | 不适用 |
| ext_id 全库唯一 | 重复=0 | 0 | 布尔 | 无连续基准 | 不适用 |
| repo 白名单 | 越界=0 | 0 | 布尔 | 无连续基准 | 不适用 |
| **DAG 无环** | 拓扑残留=0 | 声明图层残留 0（等级 WITHHELD） | 布尔（Kahn） | 无连续基准，且无独立图源 | 不适用（证据不足） |
| 依赖闭包 | 越界=0 | 0 | 布尔 | 无连续基准 | 不适用 |
| 里程碑覆盖 | min≥1 | 3 | 整数计数 | 无连续基准 | 不适用 |
| 成熟化单 | ≥2 | 3 | 整数计数 | 无连续基准 | 不适用 |
| goal 长度 | ≥80 字 | 不达标 0 | 布尔 | 无连续基准 | 不适用 |
| 验收判据非空 | 空=0 | 空 0 | 布尔 | 无连续基准 | 不适用 |
| 必填字段 | 缺=0 | 0 | 布尔 | 无连续基准 | 不适用 |
| 依赖边 声明 vs L3 列 | 逐条一致 | 5/5 一致 | 一致性计数 | 无连续基准 | 不适用 |
| 提案级溯源 | ≥阈值 0.10 才证实 | 11/18 | 相似度阈值判定（非物理量） | 阈值 0.10 为方法设定 | 不适用 |
| 归因自述 vs 实测 | 逐条一致 | 11/13 证实，矛盾 0 | 一致性计数 | 无连续基准 | 不适用 |

### 6.3 结论（显式回答）

- **是否 <3%？** —— **不适用（N/A）**：本报告关键量均为布尔/计数项，无数值基准，相对误差不成立；
  本报告不作「<3% 达标」的虚假主张。
- **等价替代判据**：机器门 10/10 通过（DAG 门单独判 WITHHELD）；依赖边「声明 vs L3 列」
  5/5 逐条一致；归因证实 11/13 条、矛盾 0；
  提案记账 11+7=18 平衡。**计数类不一致项 3 / 5 项受检**，全部来自
  原报告 832 自述错误（`merged_groups`、`plan_status`、`complementary_kept`），已在 §3.2 与 §5.4 披露。
- **数值基准将于何处出现**：Wave 6 物理节点（如 W6-N08「≥3 条入仓基线盲算 ≤10% 实测」、W6-N05「直航推力
  误差 <3%」）自带参考值与门槛，届时按 15%/3% 纪律逐项判定；本规划报告不对其作任何精度承诺。

## 7 注意点（踩过的坑 / 局限 / 后续建议）

1. **「声明」不等于「实测」**（本版核心教训）：播种期写入的 `dag_edges` 只存在于播种工件（L1）与 L3 列，
   计划元数据（L2）与节点元数据（L3b）**均无该字段**。任何「DAG 已实测无环」的表述都超出证据。
   建议 R1：播种时把 `dag_edges` 同步写入 `campaign_plans.metadata->seed_report`，并在节点 metadata 记录
   `proposal_id`（本单禁改业务代码，未实施）。
2. **稀疏图的 Kahn 通过信息量极低**：10 节点仅 5 条声明边，即使非真空也只验证了局部
   无环性，不能替代对真实作业依赖的核查。
3. **提案级溯源天花板**：节点标题被仲裁改写为通用物理描述后，与原提案标题的词汇锚点丢失，导致
   7/18 提案只能判 WITHHELD（不追认、不否认）。
4. **状态漂移**：审计时点 plan `status=active`，与原报告写作时点的 `draft` 不同——引用状态必须带时点。
5. **图件性质**：图 1/图 2 均为审计证据图（拓扑 + 判据矩阵），非物理场云图，不得当作流场结果解读。

## 8 证据台账（数字 → 来源 → 等级）

| 数字/断言 | 值 | 来源键 | 证据等级 |
|---|---|---|---|
| 节点数 | 10 | audit_wave6_seed.json:nodes | REAL |
| 里程碑分布 | 20:3, 21:3, 22:4 | audit_wave6_seed.json:milestones | REAL |
| 机器门 | 10/10 | audit_wave6_seed.json:machine_gate | REAL（G4 除外） |
| **DAG 无环（G4）** | 声明图层残留 0 | audit_wave6_dag_provenance.json:layers[L3] | **WITHHELD（真空/条件通过）** |
| 依赖边（声明） | 5 条：W6-N01→W6-N02, W6-N04→W6-N05, W6-N04→W6-N06, W6-N05→W6-N06, W6-N07→W6-N09 | audit_wave6_dag_provenance.json:layers[L1] | 声明（WITHHELD） |
| 依赖边元数据层 | L2=0 / L3b=0 | audit_wave6_dag_provenance.json:layers[L2,L3b] | REAL（负证据：字段缺失） |
| 提案级溯源 | 11/18 | audit_wave6_seed.json:provenance_audit.proposal_level_reconstruction | REAL（阈值判定） |
| 归因对账 | 11/13 | audit_wave6_seed.json:provenance_audit.report_832_crosscheck | REAL |
| 记账平衡 | 11+7=18 | audit_wave6_seed.json:dedupe_audit.balance | REAL |
| 图 1 拓扑 | 声明依赖图 | artifacts/wave6_dag.png | 声明（随 L1 同源） |
| 图 2 审计矩阵 | 程序化生成 | artifacts/wave6_audit_matrix.png | REAL（由审计 JSON 注入） |

---
*本版为报告 838 的返工修正版（v3）；改动仅限 `docs/` 与 `artifacts/`，未改业务代码。
复跑：`python3 artifacts/audit_wave6_dag_provenance.py && python3 artifacts/build_report_v3.py && python3 -m pytest artifacts/test_report_v3_consistency.py -q`*
