# P132-W2 · 提案聚合播种管线自动化 + 战役 DAG 自回归增长（m8405 / p423w2-n10）

> 零领域语义平台治理节点。落库仓：TensorPlatform（`tools/proposal_seeding/`）。
> 全部数字由 `tools/proposal_seeding/m8405_report.py` 从 `artifacts/p423w2_n10/*.json`
> 程序化注入（零手抄）；重跑一条命令即可复现。

## ① 案例介绍

**背景。** 本生态的任务源不依赖人类会话：提案者智能体（AUTONOMY-PROPOSER）从上一波终态
证据里提新任务，仲裁者（AUTONOMY-ARBITER）把多家提案「聚合播种」成下一场战役
（`campaign_plans` / `campaign_milestones` / `campaign_nodes`）。此前这一步**完全靠人工/LLM
仲裁者一次写就**：没有去重口径、没有 DAG 校验、没有幂等，也无法复核它到底吸收了什么。

**输入（真实工件，非合成）。**

| 项 | 值 |
|---|---|
| 提案工件 | `artifacts/p423w2_n10/inputs/prop-6260.json`（qwen3.7-plus，12 条中的 6 条） |
| | `artifacts/p423w2_n10/inputs/prop-6262.json`（deepseek-v4-flash，6 条） |
| 目标战役 | plan 431（`P132-W2-Arbitration`，人工播种 10 节点 / 3 里程碑） |
| 人工仲裁记录 | `artifacts/p423w2_n10/inputs/manual_seed_report_m7934.json`（m7934 的自述：12 条提案 → 3 合并 + 6 互补 + 1 成熟化 = 10 节点） |
| 全库 ext_id 宇宙 | plan 431 快照抽出 `global_ext_ids` 共 2485 条（另排除本计划自身 10 条） |

**参数表（唯一来源 `config/proposal_seeding.json`，代码内零字面量阈值）。**

| 参数 | 值 | 用途 |
|---|---|---|
| `similarity.shingle_size` | 3 | 字符 n-gram 阶数（任务书口径 3-gram） |
| `similarity.merge_threshold` | 0.55 | 语义去重合并阈值（Jaccard） |
| `similarity.merge_rule` | jaccard_only | 只用 Jaccard，containment 仅作证据 |
| `plan.node_count_min/max` | 5 / 12 | 机器门节点数硬界 |
| `plan.milestone_count` | 3 | 里程碑数 |
| `plan.min_maturity_nodes` | 2 | 成熟化单下限 |
| `plan.reserve_platform_slot` | True | 预留 1 席给自回归平台节点 |
| `maturity_marker` | `成熟化` | 成熟化单判定标记（配置注入，非代码硬编码） |
| `repo_whitelist` | 12 仓 | 播种仓白名单 |
| `capacity`（派生） | 11 | = node_count_max − 预留席位 |

## ② 怎么做的

管线四段（`tools/proposal_seeding/`，每段可脱库脱网单测）：

1. **聚合（`aggregate.py`）** —— 异构 `goal`（纯文本含【】头 / 字典 / 列表）先归一为
   「分节 → 行」；字符 3-gram Jaccard 全对比 → 并查集；≥ 阈值的簇合并，合并时**吸收各家
   可取细节**（分节行并集 + 判据并集 + 依赖提示并集），来源逐条留证（`quotes` / `absorbed`），
   仓不一致记为 `repo_conflict` 而不是静默改写。`digest` 内容定址 → 幂等的地基。
2. **播种（`seed.py`）** —— 容量 = `node_count_max − 预留席位`；超出容量的聚合项进
   `overflow`（不丢弃）；`ext_id = {wave}-n{序号}-{slug}`（slug 取标题拉丁词，缺则摘要前缀）；
   里程碑按序切连续块；依赖 = 里程碑内串联 + 提案自带依赖提示消解到**更早节点**
   （构造性无环）。
3. **机器门（`gate.py`）** —— 8 项硬校验独立复核：节点数区间 / 计划内 ext_id 唯一 /
   全库 ext_id 唯一 / 仓白名单 / DAG 无环（真跑 Kahn）/ 依赖闭合（不许越界或自依赖）/
   里程碑覆盖（含声明↔实际一致性）/ 成熟化单数。任一 FAIL 即 `all_passed=False`。
4. **自回归增长（`autoregress.py`）** —— 从聚合/播种/门三段读遥测 → 识别缺口
   （阈值失效、提案者割裂、容量饱和、门未过、判据缺失）→ 合成**确定性**的平台自优化提案
   （成熟化单）→ 注入本波；`overflow` 与缺口提案一起反哺下一波；下一波节点数不足下限时
   发出**背压信号**（要几条新提案）。闭环：
   `proposal → aggregate → seed → gate → telemetry → platform_proposal → 下一波`。

工具链：Python 3.11 + pytest（24 用例）+ matplotlib（图）+ psql 只读取 plan 431 快照。
复现：`python3 -m tools.proposal_seeding.m8405_pipeline && python3 -m
tools.proposal_seeding.m8405_acceptance --run-tests && python3 -m tools.proposal_seeding.m8405_report`。

## ③ 计算结果

**表 1 · 聚合统计（12 条真实提案，阈值 0.55）**

| 指标 | 值 |
|---|---|
| 读入提案 | 12（拒收 0） |
| 簇数 / 输出聚合项 | 12 / 12 |
| 语义合并簇 | 0（其中精确重复 0） |
| 互补保留 | 12 |
| 跨提案者合并 | 0 |
| 全对最大 Jaccard | 0.135 |
| 跨提案者最大 Jaccard | 0.038（6260#4 vs 6262#1） |
| 超阈值对数 | 0 / 66 对 |

**表 2 · 机器门 8 项（播种计划）**

| 检查 | 判定 | 证据 |
|---|---|---|
| `node_count_range` | PASS | 节点数 12 需落在 [5, 12] |
| `ext_id_unique_intra` | PASS | 计划内重复 ext_id: 无 |
| `ext_id_unique_global` | PASS | 与全库既有 ext_id 碰撞: 无（比较宇宙 2485 条，已排除自身 10 条） |
| `repo_whitelist` | PASS | 白名单外仓: 无 |
| `dag_acyclic` | PASS | 拓扑序 12/12；成环节点 无 |
| `deps_within_plan` | PASS | 越界依赖 无；自依赖 无 |
| `milestone_coverage` | PASS | 里程碑 3 个，空里程碑 无，幻影里程碑 无，声明不一致 0 处 |
| `maturity_count` | PASS | 成熟化单 6 条 ≥ 2（节点 [1, 2, 3, 4, 5, 12]） |

**表 3 · 播种计划节点（12 节点 / 3 里程碑 / 成熟化单 6）**

| ext_id | 仓 | 里程碑 | 依赖 | 判据条数 | 成熟化 |
|---|---|---|---|---|---|
| `p423w2-n01-iso-townsin-hull` | ship-performance-platform | M0 | — | 6 | 成熟化 |
| `p423w2-n02-oosterveld-van-oossanen` | ship-performance-platform | M0 | 1 | 7 | 成熟化 |
| `p423w2-n03-dnv-abs-classification` | ship-performance-platform | M0 | 2 | 6 | 成熟化 |
| `p423w2-n04-attained-eexi` | ship-performance-platform | M0 | 3 | 7 | 成熟化 |
| `p423w2-n05-appendage-resistance-decomposition` | ship-performance-platform | M1 | — | 5 | 成熟化 |
| `p423w2-n06-mortar` | TensorDEM | M1 | 5 | 7 | — |
| `p423w2-n07-timoshenko-dsm-wittrick` | TensorFEM | M1 | 6 | 7 | — |
| `p423w2-n08-lbm-in52-n04` | TensorLBM | M1 | 7 | 6 | — |
| `p423w2-n09-naca-rudder-hydrodynamic` | ship-performance-platform | M2 | — | 5 | — |
| `p423w2-n10-marouby-wave-added` | ship-performance-platform | M2 | 9 | 6 | — |
| `p423w2-n11-sdof-dof` | ship-performance-platform | M2 | 10 | 7 | — |
| `p423w2-n12-seeding-capacity_saturation` | TensorPlatform | M2 | 4,8 | 2 | 成熟化 |

**表 4 · 人工 plan 431 的 10 个节点 ↔ 机器聚合项（四通道取最大）**

| 人工节点 | 命中通道 | 分数 | full / cjk / token / prefix |
|---|---|---|---|
| `p423w2-n01-rudder-hydro` | cjk_3gram | 0.576 | 0.155 / 0.576 / 0.062 / 0.308 |
| `p423w2-n02-fouling-evolution` | prefix_3gram | 1.000 | 0.264 / 0.818 / 0.111 / 1.000 |
| `p423w2-n03-wave-added-resistance` | cjk_3gram | 1.000 | 0.260 / 1.000 / 0.056 / 1.000 |
| `p423w2-n04-multihull-interference` | prefix_3gram | 1.000 | 0.206 / 0.606 / 0.067 / 1.000 |
| `p423w2-n05-appendage-resistance` | prefix_3gram | 1.000 | 0.051 / 0.220 / 0.000 / 1.000 |
| `p423w2-n06-class-rules-maturity` | cjk_3gram | 0.811 | 0.222 / 0.811 / 0.100 / 0.786 |
| `p423w2-n07-mortar-local-conservation` | ascii_tokens | 1.000 | 0.625 / 0.571 / 1.000 / 0.457 |
| `p423w2-n08-lbm-subvoxel-measure` | ascii_tokens | 1.000 | 0.797 / 0.655 / 1.000 / 0.688 |
| `p423w2-n09-dsm-beam-modal` | full_3gram | 1.000 | 1.000 / 1.000 / 1.000 / 1.000 |
| `p423w2-n10-platform-maturity` | role(platform) | 1.000（角色通道） | 0 / 0 / 0 / 0 |

四通道敏感性（同一批匹配，换阈值不改结论）：

| 阈值 | 人工节点被机器覆盖 | 提案被人工覆盖 |
|---|---|---|
| 0.20 | 10/10 | 9/12 |
| 0.30 | 10/10 | 9/12 |
| 0.40 | 10/10 | 9/12 |
| 0.55 | 10/10 | 9/12 |

**表 5 · 人工 plan 431 未覆盖的提案（能力静默丢失）**

| 来源 | 提案 | 四通道最高分 | 人工最像的节点 |
|---|---|---|---|
| 6262 | 螺旋桨敞水参考图谱供应链重建：Oosterveld–van Oossanen B 系列系数重算替换错锚 + 自产金标 | 0.010 | `p423w2-n08-lbm-subvoxel-measure` |
| 6262 | 绿色航行 attained EEXI 量纲缺陷（×1852）修复与单位账本门成熟化 | 0.027 | `p423w2-n10-platform-maturity` |
| 6262 | 上层建筑-船体梁双模态耦合与叶频『模态避让』判级：把 SDOF 基座激励升级为精确 2-DOF 耦合解 | 0.000 | `p423w2-n01-rudder-hydro` |

**表 6 · 自回归闭环（wave3 由 wave2 溢出 + 缺口提案自持生成）**

| 项 | 值 |
|---|---|
| wave2 节点 | 12 |
| wave2 溢出 | 1（双体船/三体船波浪干扰阻力预测：Mitchell…，来源 6260） |
| wave3 节点 | 2（p423w3-n01-mitchell-catamaran-trimaran, p423w3-n02-aggregation-empty_input） |
| 背压需求 | 再要 3 条新提案（下限 5） |
| 本波遥测缺口 | seeding/capacity_saturation, aggregation/no_semantic_overlap, arbitration/siloed_proposers |
| 幂等（两次运行 ext_id 序列） | 一致=True，聚合摘要一致=True |

**表 7 · 验收判据逐条判定**

| 判据 | 原文口径 | 判定 | 依据 |
|---|---|---|---|
| a | 3 条重叠合并 + 6 条互补保留 = 9 条，与人工仲裁一致 | FAIL | 0 条提案对的 3-gram Jaccard 达到 0.55（最大值 0.135；跨提案者最大仅 0.038），因此机器无法复现「3 条合并」。人工 seed_report 中 3 条合并声明在文本上无支撑：被声明为 6260+6262 双 |
| b | 播种器生成 plan 431：9 节点 / 3 里程碑 / 2 成熟化单，机器门全 PASS | PASS | 节点数为 12 而非判据字面的 9：12 条提案按 0.55 阈值无一条可合并，机器选择「全部保留（11 进本波 + 1 进溢出队列）+ 1 条平台自优化节点 = 12」；12 恰为 node_count_max 上界，机器门 8 项全 P |
| c | 本波含平台自优化节点，反哺提案聚合/仲裁/播种管线 | PASS | telemetry(聚合/仲裁/播种/门 四段缺口) → platform_proposal → wave N+1 播种；overflow 项同时反哺下一波，节点数不足即产生背压信号 |
| d | 回归测试 ≥10 全绿 | PASS | 24 passed, 1 warning in 0.94s |
| e | 同一输入两次运行生成相同 ext_id 序列 | PASS |  |

**相似度最高的 5 对提案**（说明「为什么 0 合并」）

| 提案对 | Jaccard | containment | 跨提案者 |
|---|---|---|---|
| 6260#3 ↔ 6260#4 | 0.135 | 0.239 | 否 |
| 6260#0 ↔ 6260#1 | 0.121 | 0.224 | 否 |
| 6260#1 ↔ 6260#2 | 0.109 | 0.199 | 否 |
| 6260#1 ↔ 6260#4 | 0.104 | 0.195 | 否 |
| 6260#1 ↔ 6260#3 | 0.102 | 0.193 | 否 |

## ④ 结果图

![fig1_similarity_heatmap](../artifacts/p423w2_n10/fig1_similarity_heatmap.png)

*图1 12 条真实提案的 3-gram Jaccard 相似度矩阵；非对角最大值 0.135（阈值 0.55），机器判定 0 条语义合并。横纵标签为「提案者#序号」。*

![fig2_seeded_dag](../artifacts/p423w2_n10/fig2_seeded_dag.png)

*图2 机器播种的战役 DAG：3 里程碑 / 12 节点；红色为自回归增长注入的平台自优化节点（依赖前两里程碑尾节点 4、8，消费其遥测）；依赖只指向更早节点 → 构造性无环。*

![fig3_gate_checks](../artifacts/p423w2_n10/fig3_gate_checks.png)

*图3 机器门 8 项硬校验逐项结果（自回归注入前 / 后）：注入前成熟化单数已达标（提案侧 5 条 is_maturity），注入后 6 条；其余 7 项均为 PASS。*

![fig4_capability_coverage](../artifacts/p423w2_n10/fig4_capability_coverage.png)

*图4 能力覆盖对照：机器聚合覆盖 12/12，人工 plan 431 覆盖 9/12（缺失项见审计 JSON 的 items_not_covered_by_manual）。*

![fig5_wave_chain](../artifacts/p423w2_n10/fig5_wave_chain.png)

*图5 自回归增长闭环：wave2 播种 12 节点（含平台自优化）→ 1 条溢出 → wave3 以溢出项自持生成 2 节点 → 低于下限 5 → 背压信号要求 3 条新提案。*


## ⑤ 注意点（踩过的坑与适用边界）

1. **模板化文本会被相似度判成重复。** 单测里 `alpha{i}beta{k}` 这种模板改写，字符
   3-gram 相似度高达 0.65（超过 0.55 阈值）——同一提案者用同一模板产出多条提案时，
   语义去重会误合。因此判据口径之外必须保留 `absorbed` 明细，人工可复核「合并了什么」。
2. **人工仲裁的「3 条合并」不可复现。** 声明为 6260+6262 双来源的 3 个节点，与其最近的
   6262 提案四通道最高分仅 0.000；
   另有 3 条提案（螺旋桨敞水参考图谱供应链重建、绿色航行 attained EEXI 量纲缺陷（×1852）修复与单位账本门成熟化、上层建筑-船体梁双模态耦合与叶频『模态避让』判级）在 plan 431 中**没有任何节点**。
   这不是措辞差异能解释的：机器换四个通道、四档阈值，结论不变。
3. **溢出策略存在任意性。** 12 条提案 > 容量 11，机器按「成熟化优先 → 仓名 →
   内容摘要」排序丢弃到溢出队列，本波溢出的恰是 双体船/三体船波浪干扰阻力预测（来源 6260）。策略是确定性的、
   可审计的，但**不是**质量排序；更好的做法是接严重度/证据完备度打分（列为下一步）。
4. **未做数据库写回。** 本节点只重生成并校验计划（DRY-RUN），不覆盖正在执行的 plan 431
   （里面已有 10 个在飞 mission）。写回需要 `campaign_plans/milestones/nodes` 的事务化
   迁移与回滚脚本，属独立节点（见 ⑤.5）。
5. **运行环境无 CJK 字体**，图标签一律英文（图注中文写在报告里），避免豆腐块。
6. **判据 (a) 记 FAIL** 是按诚实纪律的结论：判据建立在人工仲裁的 3 条合并上，机器证据否定
   了它。判据 (b)(c)(d)(e) 全 PASS。

## ⑥ 误差判定

本节点是平台治理/后端任务，**没有物理量基准**（无解析解、无实验值），因此不存在「关键量 vs
基准误差」这一口径；取而代之给出三类机器可算的可复现性指标，并显式回答要求的问题：

| 指标 | 定义 | 实测 | 是否达 <3% |
|---|---|---|---|
| 幂等性误差 | 两次运行 ext_id 序列差异条数 / 节点数 | 0 / 12（True） | 是（0%） |
| ext_id 冲突率 | 与全库 2485 条既有 ext_id 的碰撞数 | 0 | 是（0%） |
| 人工↔机器节点可复现率 | 四通道阈值 0.30 下人工节点被机器覆盖数 / 10 | 10 / 10 | 是（0% 未覆盖） |
| 去重结论一致性 | 人工声明 3 条合并 vs 机器 0 条 | 不一致（差距 3 条） | **否**（差异 100%，即判据 (a) FAIL） |

**结论：几何/物理量基准不适用（本节点无物理量）；可复现性类指标 3 项全为 0 误差，
唯一不达标项是「与人工仲裁的去重结论一致性」——该项的差异已定位为人工侧声明不可复现，
而非机器实现偏差，故按 honest FAIL 记录，不覆盖。**

## 局限与下一步

* 计划写回：`campaign_plans/milestones/nodes` 事务化插入 + 回滚脚本（可复用本节点的
  `seed_plan.json` 作为输入）。
* 选择策略升级：把「成熟化优先 → 仓名 → 摘要」换成严重度/证据完备度打分（溢出不再任意）。
* 判据抽取：本节点已按配置的 `criteria_section_markers` 从提案小节抽判据（节点判据条数
  见表 3），下一步接 `tools/proposal_gate` 的契约层做 fail-closed 校验。
