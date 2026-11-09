# 冰区航行战役·第二波聚合 (Ice Navigation Campaign Wave 2 Aggregation) — 成果汇总与知识蒸馏（plan 272）

*快照时点：2026-11-08T13:14:04Z ｜ 账本生成：tools/distill/collect_evidence.py*

## 0 摘要

本报告是战役第二波（plan 272，冰区航行波次）的**机器化汇总与知识蒸馏**产物：输入是平台战役节点账本（8 个节点的任务书与验收判据）+ 各节点工作树在快照时点的证据切片（新增/修改工件逐件 sha256、新增测试的 pytest 探针结果），输出是判据覆盖矩阵、知识图谱、缺口清单与归档清单。

核心发现：**第二波尚未收口**。快照时点没有任何节点提供「逐判据结论 + 来源引用」形式的证据，因此按纪律全部判据落在 `EVIDENCE_ONLY`/`NO_EVIDENCE`（有工件无结论映射 / 无任何证据），节点级结论无一达到 `VERIFIED`。这不是把在途工作判死，而是把「哪些结论现在还不能被引用」变成机器可读的事实：本报告的作用是给下一次波次一张精确的欠账地图，而不是给本波次发合格证。

- 节点数：8；判据条数：49；覆盖状态分布：PASS_CLAIMED 0 / FAIL_CLAIMED 0 / EVIDENCE_ONLY 49 / NO_EVIDENCE 0
- 节点级结论分布：N01=UNVERIFIED_EVIDENCE(0/7)；N02=UNVERIFIED_EVIDENCE(0/8)；N03=UNVERIFIED_EVIDENCE(0/7)；N04=UNVERIFIED_EVIDENCE(0/6)；N05=UNVERIFIED_EVIDENCE(0/6)；N06=UNVERIFIED_EVIDENCE(0/5)；N07=UNVERIFIED_EVIDENCE(0/5)；N08=UNVERIFIED_EVIDENCE(0/5)
- 知识图谱：194 实体 / 477 边（阈值 20/30，达标）
- 遗留缺口：18 条（阈值 ≥3，每条附提案草案）
- 归档：59 个工件，manifest 校验 verified

## 1 战役结构与快照

战役 DAG 为 8 节点：N01（操作能力-推进可行域）、N02（冰致破口进水拓扑）、N03（磨蚀场条件模拟）、N04（全船阻力物理预报链）、N05（时空磨蚀场演化）、N06（多物理场耦合集成门）、N07（不确定性量化与误差传播）、N08（本汇总节点）。依赖关系上，N05 依赖 N03；N06、N07 依赖 N03/N04/N05 三件套；N08 依赖 N06/N07。物理上这是一条「操作能力 → 破损稳性 → 磨蚀场 → 阻力预报 → 多场耦合 → UQ」的链，链上的下游结论必须以上游判据闭合为前提。

快照方法（可复核）：① 节点元数据取自平台战役账本（`/tensor/campaigns/plans/272/nodes`），验收判据逐条解析为 C 编号；② 证据取自各节点工作树的 `git status --porcelain -uall`——只有本节点新增/修改的文件算证据，历史已跟踪文件不算；逐件算 sha256 与字节数；③ 对新增测试文件跑一次 pytest（`PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`，不污染对方工作树），记录退出码与末行摘要；④ 不生成任何结论：采集器不写 `claims`，判据是否达成一律由下游按「结论 + 来源」机裁。

| 节点 | mission | 仓库 | 声明状态 | 判据数 | 覆盖结论 | depends_on |
|---|---|---|---|---|---|---|
| N01 | 7293 | ship-performance-platform | running | 7 | UNVERIFIED_EVIDENCE | — |
| N02 | 7294 | ship-performance-platform | failed | 8 | UNVERIFIED_EVIDENCE | — |
| N03 | 7291 | TensorDEM | running | 7 | UNVERIFIED_EVIDENCE | — |
| N04 | 7292 | TensorLBM | running | 6 | UNVERIFIED_EVIDENCE | — |
| N05 | 7295 | TensorDEM | running | 6 | UNVERIFIED_EVIDENCE | N03 |
| N06 | 7296 | TensorPhysics | running | 5 | UNVERIFIED_EVIDENCE | N03,N04,N05 |
| N07 | 7297 | TensorPlatform | running | 5 | UNVERIFIED_EVIDENCE | N03,N04,N05 |
| N08 | 7298 | TensorPlatform | running | 5 | UNVERIFIED_EVIDENCE | N06,N07 |

| 节点 | 证据文件 | 探针 | 探针结果（退出码 / 摘要） |
|---|---|---|---|
| N01 | 3 | 1 | `exit=0` 25 passed, 1 warning in 5.71s |
| N02 | 2 | 0 | — |
| N03 | 2 | 1 | `exit=1` 2 failed, 18 passed, 1 warning in 21.30s |
| N04 | 3 | 0 | — |
| N05 | 1 | 0 | — |
| N06 | 19 | 1 | `exit=0` 23 passed, 1 warning in 6.23s |
| N07 | 4 | 1 | `exit=0` 29 passed, 1 warning in 13.22s |
| N08 | 18 | 1 | `exit=0` 21 passed, 1 warning in 0.65s |

数据包：`artifacts/p66_w2_distill/inputs/campaign_bundle.json`（bundle_version 1.0）。

## 2 判据覆盖矩阵

矩阵的行是「节点 × 判据」，列给出状态与依据。四档状态的判定逻辑是纯函数：有结论且有来源 → 按结论的 ok/等级判 `PASS_CLAIMED` 或 `FAIL_CLAIMED`；有结论但缺来源 → 一律 `FAIL_CLAIMED`（不可采信）；有工件/探针但无逐判据结论映射 → `EVIDENCE_ONLY`；什么都没有 → `NO_EVIDENCE`。

关键读法：`EVIDENCE_ONLY` 意味着「有人写了代码、可能有测试，但没有人把某条判据的数字与来源写进机器可读结论」，因此该判据在当前状态下**不可被下游引用**。这正是波次汇总最容易被掩盖的一类欠账——看起来进度不差，可复算结论为零。

| 节点 | 判据 | 判据原文 | 状态 | 依据 |
|---|---|---|---|---|
| N01 | C1 | 几何门Cb/L/B反算<1e-12 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N01 | C2 | 边界单调+V→0极限与Lindqvist破冰项<15% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N01 | C3 | h→0退化到KVLCC2@15kn基线<3% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N01 | C4 | 与speed_loss链判定一致率≥90% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N01 | C5 | 双轴加密后F面积变化<5%、D面积变化<10% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N01 | C6 | 功率恒等式残差<1e-9 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N01 | C7 | 输出可行域云机器可读数据+矛盾区清单 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N02 | C1 | 几何门Cb/L/B<1e-12 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N02 | C2 | 多边形GZ vs壁直闭式<0.1%（5/10/15/30°） | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N02 | C3 | box型线vs解析GZ机器精度 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N02 | C4 | s_i(T1)≥s_i(T2)≥s_i(T3)全域成立 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N02 | C5 | 破口面积换算残差<2% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N02 | C6 | R=0.6257与IN37-N06一致、A(b)单调 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N02 | C7 | 时域窗×2后s_i变化<3% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N02 | C8 | b*迁移比带区间，σ_c保持WITHHELD口径 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N03 | C1 | 模拟场自相关/谱保真：lag≤3偏差<5%，能量守恒<2% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N03 | C2 | P95方差vs独立重复真值比值∈[0.7,1.4] | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N03 | C3 | 行均值保真<1e-2（剖面不被破坏） | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N03 | C4 | 19.47%分解闭合：两项和vs实测<10% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N03 | C5 | 白噪声退化负控不劣于iid bootstrap 20% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N03 | C6 | 同种子bitwise复现、异种子CV<5% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N03 | C7 | conditional_simulator API+分辨率选择建议表 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N04 | C1 | DEM冰浓度误差<5%, D50<10% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N04 | C2 | RANS残差下降3个量级, 质量守恒<1e-4 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N04 | C3 | FSI动量交换残差<1% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N04 | C4 | 阻力分解能量守恒<2% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N04 | C5 | 全船阻力vs Riska(1989)<15% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N04 | C6 | 3档网格GCI外推<5% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N05 | C1 | 活动度剖面拟合R²>0.95 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N05 | C2 | 100条件样本统计vs目标<5% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N05 | C3 | 时空演化质量守恒<1e-6 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N05 | C4 | 场提取P95 vs N02 P95<10% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N05 | C5 | 均匀冰场d(t)=a·t误差<1% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N05 | C6 | 1000面元×100步<60s CPU | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N06 | C1 | 耦合界面质量守恒残差<1e-6 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N06 | C2 | 磨蚀场与阻力链参数空间一致性>90% | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N06 | C3 | 跨模块数据接口契约验证通过 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N06 | C4 | 集成测试用例全部通过（≥5个端到端场景） | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N06 | C5 | 耦合仿真端到端时间<单模块时间之和的1.5倍 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N07 | C1 | Sobol指数总和∈[0.95,1.05]（闭合性检验） | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N07 | C2 | 输出95%CI与独立重复统计比值∈[0.8,1.2] | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N07 | C3 | 误差传播链雅可比条件数<1e6 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N07 | C4 | UQ计算加速比≥10 vs蒙特卡洛暴力法 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N07 | C5 | 输出UQ报告含敏感性排序+置信区间图+误差传播路径图 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N08 | C1 | 综合报告覆盖全部7节点验收判据（逐项对照表） | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N08 | C2 | 知识图谱≥20实体节点+≥30关系边 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N08 | C3 | 遗留缺口清单≥3条且每条附提案草案 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N08 | C4 | 综合报告通过自动化格式校验 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |
| N08 | C5 | 全部工件归档verified/并生成sha256校验和 | EVIDENCE_ONLY | 有工件/探针，但无逐判据结论映射 |

状态定义：`PASS_CLAIMED`=有结论且带来源引用并判为达成；`FAIL_CLAIMED`=有结论但未达成或等级越界；`EVIDENCE_ONLY`=有工件/探针但缺逐判据结论映射；`NO_EVIDENCE`=无任何证据。**节点级结论不外推**：`VERIFIED` 要求该节点全部判据 `PASS_CLAIMED`。

## 3 知识图谱

知识图谱由五类实体（节点 / 判据 / 工件 / 仓库 / 缺口）与六类关系（has_criterion / depends_on / evidenced_by / produced / in_repo / raises_gap / proposed_in）构成。实体与边全部由数据包推导，确定性排序；实体表与边表各自算 sha256，落 `knowledge_graph.json`，若有人改动图谱内容，哈希立刻变化。

图谱的用途不是画得好看，而是把「这条结论凭什么成立」变成可遍历路径：判据 → 证据工件（sha256 可回到原文）→ 产出节点 → 所属仓库；缺口则反挂到受影响节点与目标仓库（提案落点）。

- 实体 194 个：artifact 114，criterion 49，gap 18，node 8，repo 5
- 关系边 477 条：depends_on 9，evidenced_by 309，has_criterion 49，in_repo 8，produced 57，proposed_in 18，raises_gap 27
- 实体表 sha256：`96efe855c2dec74b8e2c095d2e122413cf989a69e79227fc246ef5896d60697f`；边表 sha256：`7be1a0881ead7832d97a8970a2ded0ea337056351d1274f6a4122e53d94a384c`

| 实体 id | 类型 | 标签 |
|---|---|---|
| `artifact:056358fe934c` | artifact | /root/tp-agent/work/m7296/artifacts/p66_gate/figures/con |
| `artifact:08288e6fd1d2` | artifact | /root/tp-agent/work/m7292/src/tensorlbm/ice_fullship/__i |
| `artifact:0bfb50a039df` | artifact | /root/tp-agent/work/m7291/tests/test_ice_abrasion_condit |
| `artifact:0efcc738eb14` | artifact | /root/tp-agent/work/m7293/backend/modules/green_navigati |
| `artifact:0f2f2abf0767` | artifact | probe:pytest::tests/test_ice_abrasion_conditional_sim.py |
| `artifact:129e23892748` | artifact | /root/tp-agent/work/m7298/artifacts/p66_w2_distill/input |
| `artifact:13c045df4eda` | artifact | /root/tp-agent/work/m7296/artifacts/p66_gate/figures/tim |
| `artifact:1945e1409a12` | artifact | /root/tp-agent/work/m7297/artifacts/m7297/run_uq_case.py |
| `artifact:19930db07c27` | artifact | /root/tp-agent/work/m7296/artifacts/p66_gate/figures/cha |
| `artifact:1baedbd33c26` | artifact | /root/tp-agent/work/m7297/tests/test_uq_propagation.py#s |
| `artifact:1cc6295acedc` | artifact | /root/tp-agent/work/m7298/artifacts/p66_w2_distill/figur |
| `artifact:1d238f37cce0` | artifact | /root/tp-agent/work/m7298/docs/reports/P66_W2_campaign_d |
| `artifact:1dc89fe1546e` | artifact | /root/tp-agent/work/m7292/src/tensorlbm/ice_fullship/__i |
| `artifact:1e36499c9f2b` | artifact | /root/tp-agent/work/m7296/src/tensorphysics/campaign/p66 |
| `artifact:1e68f93aca42` | artifact | /root/tp-agent/work/m7298/tools/distill/collect_evidence |
| `artifact:21ea09b6efd1` | artifact | /root/tp-agent/work/m7298/artifacts/p66_w2_distill/out/d |
| `artifact:27ca095dd113` | artifact | /root/tp-agent/work/m7298/artifacts/p66_w2_distill/out/g |
| `artifact:297cca4352ff` | artifact | /root/tp-agent/work/m7296/src/tensorphysics/campaign/p66 |
| `artifact:2bed6a233b6e` | artifact | /root/tp-agent/work/m7298/artifacts/p66_w2_distill/figur |
| `artifact:2c821a8a3aa9` | artifact | /root/tp-agent/work/m7298/artifacts/p66_w2_distill/figur |
| `artifact:2d19b47169b8` | artifact | /root/tp-agent/work/m7296/src/tensorphysics/campaign/p66 |
| `artifact:2ef62c3738e9` | artifact | /root/tp-agent/work/m7298/artifacts/p66_w2_distill/out/c |
| `artifact:2fae3917ee61` | artifact | probe:pytest::tests/test_campaign_distill.py |
| `artifact:320c8dab46c8` | artifact | /root/tp-agent/work/m7291/tests/test_ice_abrasion_condit |
| `artifact:32322e8c99fb` | artifact | /root/tp-agent/work/m7296/src/tensorphysics/campaign/p66 |
| `artifact:32eb104fb687` | artifact | /root/tp-agent/work/m7297/artifacts/m7297/run_uq_case.py |
| `artifact:377a0cf5d5bf` | artifact | /root/tp-agent/work/m7294/backend/modules/ice_damage_top |
| `artifact:37a1f2ed7be8` | artifact | /root/tp-agent/work/m7296/artifacts/p66_gate/sweep.json# |
| `artifact:3e1256c93311` | artifact | /root/tp-agent/work/m7296/artifacts/p66_gate/figures/con |
| `artifact:3fef68ee7316` | artifact | /root/tp-agent/work/m7298/alpha_impl.py#sha256:92e78d0b0 |
| `artifact:43d51ab97d39` | artifact | /root/tp-agent/work/m7296/src/tensorphysics/campaign/p66 |
| `artifact:457b3193afd3` | artifact | /root/tp-agent/work/m7296/src/tensorphysics/campaign/p66 |
| `artifact:459c519ff239` | artifact | /root/tp-agent/work/m7295/src/tensordem/ice_abrasion_spa |
| `artifact:463eb9078347` | artifact | /root/tp-agent/work/m7293/artifacts/p66_w2_n01/generate_ |
| `artifact:46ea04089b04` | artifact | /root/tp-agent/work/m7298/artifacts/p66_w2_distill/out/k |
| `artifact:47353ff3a7cf` | artifact | /root/tp-agent/work/m7296/artifacts/p66_gate/figures/cha |
| `artifact:49a9ad3f01d8` | artifact | /root/tp-agent/work/m7296/docs/P66_W2_N06_MULTIPHYSICS_C |
| `artifact:4d85bf1f3472` | artifact | probe:pytest::tests/test_ice_abrasion_conditional_sim.py |
| `artifact:50a7066b569a` | artifact | /root/tp-agent/work/m7294/backend/modules/ice_damage_top |
| `artifact:5461b7cd6b59` | artifact | /root/tp-agent/work/m7298/tools/distill/campaign_distill |
| … | … | 余 154 条见 knowledge_graph.json |

## 4 遗留缺口与提案草案

缺口由五条规则自动识别，每条都附提案草案骨架（title / repo / physics_domain / why_frontier / goal / acceptance_criteria / dependencies / metric）。规则分别是：未证判据（每节点聚合一条）、在途超时（按快照时点相对 actual_start 计）、失败节点（honest FAIL 不覆盖，只记账并提复证）、依赖链未闭合（上游未 VERIFIED 则下游结论不可采信）、结论缺来源（证据完整性缺口）。

提案草案的字段值全部来自节点自身数据（仓库、物理域、未闭合判据编号），不写死任何域结论；它是一张**待仲裁的工单骨架**，不是替下一波拍板。

| 缺口 | 规则 | 严重度 | 摘要 | 影响节点 |
|---|---|---|---|---|
| G1-N01 | unverified_criteria | high | N01 有 7/7 条判据未被结论级证据覆盖 | N01 |
| G1-N02 | unverified_criteria | high | N02 有 8/8 条判据未被结论级证据覆盖 | N02 |
| G1-N03 | unverified_criteria | high | N03 有 7/7 条判据未被结论级证据覆盖 | N03 |
| G1-N04 | unverified_criteria | high | N04 有 6/6 条判据未被结论级证据覆盖 | N04 |
| G1-N05 | unverified_criteria | high | N05 有 6/6 条判据未被结论级证据覆盖 | N05 |
| G1-N06 | unverified_criteria | high | N06 有 5/5 条判据未被结论级证据覆盖 | N06 |
| G1-N07 | unverified_criteria | high | N07 有 5/5 条判据未被结论级证据覆盖 | N07 |
| G1-N08 | unverified_criteria | high | N08 有 5/5 条判据未被结论级证据覆盖 | N08 |
| G3-N02 | failed_node | high | N02 宣告失败，判据真空需独立复证（FAIL 不覆盖） | N02 |
| G4-N05->N03 | dependency_open | medium | N05 依赖的 N03 未达 VERIFIED（UNVERIFIED_EVIDENCE），下游结论不可采信 | N05,N03 |
| G4-N06->N03 | dependency_open | medium | N06 依赖的 N03 未达 VERIFIED（UNVERIFIED_EVIDENCE），下游结论不可采信 | N06,N03 |
| G4-N06->N04 | dependency_open | medium | N06 依赖的 N04 未达 VERIFIED（UNVERIFIED_EVIDENCE），下游结论不可采信 | N06,N04 |
| G4-N06->N05 | dependency_open | medium | N06 依赖的 N05 未达 VERIFIED（UNVERIFIED_EVIDENCE），下游结论不可采信 | N06,N05 |
| G4-N07->N03 | dependency_open | medium | N07 依赖的 N03 未达 VERIFIED（UNVERIFIED_EVIDENCE），下游结论不可采信 | N07,N03 |
| G4-N07->N04 | dependency_open | medium | N07 依赖的 N04 未达 VERIFIED（UNVERIFIED_EVIDENCE），下游结论不可采信 | N07,N04 |
| G4-N07->N05 | dependency_open | medium | N07 依赖的 N05 未达 VERIFIED（UNVERIFIED_EVIDENCE），下游结论不可采信 | N07,N05 |
| G4-N08->N06 | dependency_open | medium | N08 依赖的 N06 未达 VERIFIED（UNVERIFIED_EVIDENCE），下游结论不可采信 | N08,N06 |
| G4-N08->N07 | dependency_open | medium | N08 依赖的 N07 未达 VERIFIED（UNVERIFIED_EVIDENCE），下游结论不可采信 | N08,N07 |

### 提案草案 G1-N01

- **title**: N01 判据收口：7 条未证判据的独立复核与增补验证
- **repo**: ship-performance-platform
- **physics_domain**: 冰区航行·操纵/操作性与推进
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=unverified_criteria），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 ship-performance-platform 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5,C6,C7 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N01
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C6: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C7: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G1-N02

- **title**: N02 判据收口：8 条未证判据的独立复核与增补验证
- **repo**: ship-performance-platform
- **physics_domain**: 冰区航行·破损稳性
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=unverified_criteria），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 ship-performance-platform 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5,C6,C7,C8 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N02
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C6: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C7: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C8: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G1-N03

- **title**: N03 判据收口：7 条未证判据的独立复核与增补验证
- **repo**: TensorDEM
- **physics_domain**: 冰区航行·结构磨损
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=unverified_criteria），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorDEM 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5,C6,C7 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N03
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C6: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C7: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G1-N04

- **title**: N04 判据收口：6 条未证判据的独立复核与增补验证
- **repo**: TensorLBM
- **physics_domain**: 冰区航行·阻力预报
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=unverified_criteria），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorLBM 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5,C6 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N04
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C6: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G1-N05

- **title**: N05 判据收口：6 条未证判据的独立复核与增补验证
- **repo**: TensorDEM
- **physics_domain**: 冰区航行·结构磨损
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=unverified_criteria），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorDEM 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5,C6 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N05
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C6: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G1-N06

- **title**: N06 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPhysics
- **physics_domain**: 冰区航行·多物理场耦合
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=unverified_criteria），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPhysics 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N06
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G1-N07

- **title**: N07 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPlatform
- **physics_domain**: 冰区航行·不确定性量化
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=unverified_criteria），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPlatform 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N07
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G1-N08

- **title**: N08 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPlatform
- **physics_domain**: 冰区航行·知识蒸馏
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=unverified_criteria），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPlatform 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N08
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G3-N02

- **title**: N02 判据收口：8 条未证判据的独立复核与增补验证
- **repo**: ship-performance-platform
- **physics_domain**: 冰区航行·破损稳性
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=failed_node），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 ship-performance-platform 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5,C6,C7,C8 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N02
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C6: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C7: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C8: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G4-N05->N03

- **title**: N05 判据收口：6 条未证判据的独立复核与增补验证
- **repo**: TensorDEM
- **physics_domain**: 冰区航行·结构磨损
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=dependency_open），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorDEM 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5,C6 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N05
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C6: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G4-N06->N03

- **title**: N06 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPhysics
- **physics_domain**: 冰区航行·多物理场耦合
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=dependency_open），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPhysics 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N06
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G4-N06->N04

- **title**: N06 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPhysics
- **physics_domain**: 冰区航行·多物理场耦合
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=dependency_open），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPhysics 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N06
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G4-N06->N05

- **title**: N06 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPhysics
- **physics_domain**: 冰区航行·多物理场耦合
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=dependency_open），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPhysics 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N06
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G4-N07->N03

- **title**: N07 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPlatform
- **physics_domain**: 冰区航行·不确定性量化
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=dependency_open），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPlatform 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N07
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G4-N07->N04

- **title**: N07 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPlatform
- **physics_domain**: 冰区航行·不确定性量化
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=dependency_open），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPlatform 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N07
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G4-N07->N05

- **title**: N07 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPlatform
- **physics_domain**: 冰区航行·不确定性量化
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=dependency_open），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPlatform 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N07
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G4-N08->N06

- **title**: N08 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPlatform
- **physics_domain**: 冰区航行·知识蒸馏
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=dependency_open），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPlatform 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N08
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

### 提案草案 G4-N08->N07

- **title**: N08 判据收口：5 条未证判据的独立复核与增补验证
- **repo**: TensorPlatform
- **physics_domain**: 冰区航行·知识蒸馏
- **metric**: unresolved_criteria_closed / unresolved_criteria_total
- **why_frontier**: 该节点在快照时点仍有未闭合判据（rule=dependency_open），其结论未被机器可复核证据覆盖；补证前任何下游引用都应视为 WITHHELD。
- **goal**: 在 TensorPlatform 内重建可复算证据链：逐条复现未证判据 C1,C2,C3,C4,C5 的度量，落 JSON 结论+来源，并提供负控用例证明判据非恒真。
- **dependencies**: node:N08
- **acceptance_criteria**:
  - C1: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C2: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C3: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C4: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON
  - C5: 复现并给出来源引用（文件 sha256 + 锚点），误差/偏差数字落 JSON

## 5 工件清单与 sha256

归档纪律：先复制再重算哈希，复制前后逐件一致才算「已归档」；目标目录存在同名但内容不同的文件时直接拒绝覆盖（避免静默污染历史归档）。`SHA256SUMS` 供 `sha256sum -c` 直接复核，`manifest.json` 记录源路径、字节数、类型与条目表哈希。

| 引用 | 路径 | 类型 | 字节 | sha256 |
|---|---|---|---|---|
| [E1] | `N03/ice_abrasion_conditional_sim.py` | impl | 28621 | `a92d5d660b26447d4d2c87bd9b391e1a2efd4f71e1808f28448fd82c642f38c1` |
| [E2] | `N03/test_ice_abrasion_conditional_sim.py` | test | 14958 | `0bfb50a039dfa92f653ff4af3970c4298c6b9cf22133d3b59105ed211ea92b0d` |
| [E3] | `N04/__init__.py` | impl | 1277 | `08288e6fd1d2bcdd4b5165f3b9fc29980287c95e131132d73101f32873931811` |
| [E4] | `N04/floe_field.py` | impl | 14179 | `8003ff9aaec76ed8c7b8c1d9b5b8cb6b4c6905ddefddfe5433a33a1319d97633` |
| [E5] | `N04/lbm_porous.py` | impl | 23465 | `587f31e3e9fb5e47382a3f5bfd2ecc0056fcdad64fb31c2066067b449583ac74` |
| [E6] | `N01/generate_artifacts.py` | impl | 12489 | `aefb87f1503b8a1bb90c2b4b5aae77ccba2a1c8f350fe07d0cf3de813eb4be74` |
| [E7] | `N01/feasible_domain.py` | impl | 23527 | `0efcc738eb14d45d80901ee111f54469a7b775ec70e5face0393b8dd2a5cb42e` |
| [E8] | `N01/test_feasible_domain.py` | test | 9340 | `984bfc153f094b6435938e09bfaa152bf4f6344dc2696b0f3a0647598c1a83ad` |
| [E9] | `N02/gz.py` | impl | 8509 | `74232b91c861ba4b83643692596c627d19c5a606781216225393682763be9cb2` |
| [E10] | `N02/hull_lines.py` | impl | 10309 | `50a7066b569a4ed5316d52aafd5a02a5cc2810f73478f82f9023c10bf96692b0` |
| [E11] | `N05/ice_abrasion_spatiotemporal.py` | impl | 87973 | `a357868d93d27a4dd604c5621659762a460ede33125327b2ab5860c54f9c5abc` |
| [E12] | `N06/chain_contours.png` | figure | 53995 | `19930db07c274222f9eacdb10491791d362e7327086ec09fe07b65e59e961993` |
| [E13] | `N06/conservation_residuals.png` | figure | 53994 | `056358fe934c19f4375fa6a961262377bc103ce9e8b84e3b88e2a4a5e9d28b86` |
| [E14] | `N06/interface_fluxes.png` | figure | 118263 | `f4ccf07345c6075223bae0e8699268d51417a2615f07da57ca4ddd26afda1ee0` |
| [E15] | `N06/parameter_space.png` | figure | 58819 | `749467e7fb7e5cccf7363b3388f0e734cc173eff9782b25aff1c165072ae09de` |
| [E16] | `N06/timing.png` | figure | 42432 | `1916bd3498fc834bffc630e085d25523df26a18de7f8fd093f12f3213b30a83d` |
| [E17] | `N06/gate_verdict.json` | artifact | 119965 | `e8a32bbb8b1ae80d1ef1302abc7d961a8218d875b085986a5eb6fed605ae8f0c` |
| [E18] | `N06/scenarios.json` | artifact | 19427 | `70d548ce2f73a92ae196d8bae44791563f8e8906be56a18e35dc4050e7601c91` |
| [E19] | `N06/sweep.json` | artifact | 30589 | `faaf717c20856f35bf1a252e16388377f4fa5a7799c6b606401d61221feda3b6` |
| [E20] | `N06/P66_W2_N06_MULTIPHYSICS_COUPLING_GATE.md` | report | 12406 | `975abb1bb97ce4f77602e113f692f85322dd0ecec12e13e598f641e61689fa93` |
| [E21] | `N06/p66_w2_n06_gate.py` | impl | 30572 | `229796b6c3b75899da926caf14f3f9746d44556b925bdb92d788df90cdee9b71` |
| [E22] | `N06/__init__.py` | impl | 1874 | `dc86fff4301bed60126373df1fb0d586880b186afcd2b99a133b90cb620ccf6c` |
| [E23] | `N06/conservation.py` | impl | 42501 | `297cca4352ffebc87ee672d75bd8f6eebf96a6d76e4cb8f3dee21d779e6f6e6e` |
| [E24] | `N06/contracts.py` | impl | 26258 | `a1daff2a35f5d74ea047edd8f0617b53f8c90fd5982f3502f1ffaeda062b2b5a` |
| [E25] | `N06/gate.py` | impl | 18678 | `fbf1f3c15ee4db5aaae03146fa573b995b344a7854697b4e0545be8abdb64095` |
| [E26] | `N06/parameter_space.py` | impl | 9516 | `6bad0470322ff0992a19c553b9d8a0116d3e7f6eb2d10e4d4f891168574dd2a6` |
| [E27] | `N06/perf.py` | impl | 7712 | `765246fa615a875b60200594de242651d94197fb18822b916e78952c5b5106fd` |
| [E28] | `N06/producers.py` | impl | 19036 | `6a769d8e5717b7d3706e36bc66aac534744208cbe74c5175969ca2d0d6cc6ea9` |
| [E29] | `N06/scenarios.py` | impl | 11769 | `5b91a5c969ad3ffdac222c357a1dd5dd6d3c2cfafd600856e1e4c38b1b494e39` |
| [E30] | `N06/test_p66_multiphysics_gate.py` | test | 10534 | `788380aefed35bbce252ead524d14cc9fcb5943f9102f455db4773f044b9ab47` |
| [E31] | `N07/case_ice_navigation.py` | impl | 18569 | `1b305728444ea984f48481bd113bacfa12688077bd5be865740b56e0d19bb004` |
| [E32] | `N07/run_uq_case.py` | impl | 26585 | `32eb104fb687e2fbd3d40efc6214088cd8e90ae96db04590e4ba459a2c473267` |
| [E33] | `N07/uq_propagation.py` | impl | 56346 | `65fe5d9919fac39f1f244b52dd85619db7ec2b4bee90901a49a2b1d2ebe3fd35` |
| [E34] | `N07/test_uq_propagation.py` | test | 21495 | `ee83489c4b0046baa8eca7f2a500b6c18da88e4e113630e0a4c64cadef339692` |
| [E35] | `N08/alpha_impl.py` | impl | 9 | `92e78d0b032962f47792a9fa95fd981ef63e1e3ef074d536d6304c75eddbe29f` |
| [E36] | `N08/fig1_coverage_and_evidence.png` | figure | 60695 | `598da6c20288bc0ce6a2530ba0218fba7e1324462437e38b032c2e6f7ae5300a` |
| [E37] | `N08/fig2_knowledge_graph.png` | figure | 361779 | `b923c2d35221e6f1f14f95cff94d56025a2f870f8b555d4a2e23144501cc3458` |
| [E38] | `N08/fig3_criteria_vs_threshold.png` | figure | 70878 | `e68aa450a8f08b71b94838ee6478400aa75bc0ff7500438dafcad0a2c2b185e2` |
| [E39] | `N08/campaign_bundle.json` | artifact | 25921 | `282f35188c0a102ea2820563ba78a9d9d88358aa8061f37c1d1ad0d90defd6d0` |
| [E40] | `N08/narrative.json` | artifact | 6530 | `129e2389274811e2070d9787d24741d694d9467394b3534a8ec9f7b7a268115c` |
| [E41] | `N08/coverage_matrix.json` | artifact | 46511 | `341921ae98c38d0dbd8eb093c5c124019838753429702dc12b8660a08b11db0f` |
| [E42] | `N08/distill_summary.json` | artifact | 6711 | `22bc92d56ec045d71c46199fa910c2dd95a905d384d8688e9246a9c80386de01` |
| [E43] | `N08/gaps.json` | artifact | 33359 | `1b37d0361912b1449a0a6420adde6aa08c0f8ba4df549f62d471742e5be0b184` |
| [E44] | `N08/knowledge_graph.json` | artifact | 85214 | `587ad882301d987d406336e474de453052837b78b30e2d2cabf1a0969d01f2fd` |
| [E45] | `N08/missions_raw.json` | artifact | 20704 | `87077f3f7eabebae64c7a0a37bfdf4f6ac794bb3b365ff30ab26beb98c71e6aa` |
| [E46] | `N08/plan272_nodes.json` | artifact | 19261 | `e9b0fc6ef4ca207dda2966b558bf1152435169d349844da440f5708e14b57967` |
| [E47] | `N08/domain_word_denylist.json` | artifact | 1896 | `c5cda62a02d3a86b931075b2437d9f438b807c2b79c35bc548836306ddf354de` |
| [E48] | `N08/P66_W2_campaign_distill.md` | report | 49946 | `1d238f37cce0694f361bb80db0223c9ded771936448da9d8bce3c6ca3e7fc3d6` |
| [E49] | `N08/test_campaign_distill.py` | test | 15480 | `62bd935e9f5b8acaffbda2068af13e28cf4463089174dfe8bfabd23161179d57` |
| [E50] | `N08/campaign_distill.py` | impl | 42516 | `d7e591ffb715969b12169674c027ad84bdf1d0dc829894724d12786b6bc918d3` |
| [E51] | `N08/collect_evidence.py` | impl | 9845 | `870a24fe62f16970c78713894439643573854fca14e703001f02b1e746acb7ee` |
| [E52] | `N08/make_figures.py` | impl | 7586 | `577c5d4f0e543bc9ddbf7604f38bd3c70a1ae7275470c9ab19e17a038ccde5ee` |
| [E53] | `extra/fig1_coverage_and_evidence.png` | figure | 60695 | `598da6c20288bc0ce6a2530ba0218fba7e1324462437e38b032c2e6f7ae5300a` |
| [E54] | `extra/fig2_knowledge_graph.png` | figure | 361779 | `b923c2d35221e6f1f14f95cff94d56025a2f870f8b555d4a2e23144501cc3458` |
| [E55] | `extra/fig3_criteria_vs_threshold.png` | figure | 70878 | `e68aa450a8f08b71b94838ee6478400aa75bc0ff7500438dafcad0a2c2b185e2` |
| [E56] | `campaign_bundle.json` | bundle | 25921 | `282f35188c0a102ea2820563ba78a9d9d88358aa8061f37c1d1ad0d90defd6d0` |
| [E57] | `coverage_matrix.json` | ledger | 46511 | `341921ae98c38d0dbd8eb093c5c124019838753429702dc12b8660a08b11db0f` |
| [E58] | `gaps.json` | ledger | 33359 | `1b37d0361912b1449a0a6420adde6aa08c0f8ba4df549f62d471742e5be0b184` |
| [E59] | `knowledge_graph.json` | ledger | 85214 | `587ad882301d987d406336e474de453052837b78b30e2d2cabf1a0969d01f2fd` |

SHA256SUMS：`verified/p66_w2/SHA256SUMS`；manifest：`verified/p66_w2/manifest.json`。

## 6 局限与误差判定

误差判定说明：本节点是**汇总/蒸馏节点**，不产生新的物理量，因此「关键量 vs 实验/参考值误差是否 <3%」这一判据在物理意义上不适用——没有任何新的水动力/结构量在这里被计算。取而代之的可机算判据是**离散计数一致性与哈希恒等**：判据行数是否等于账本判据总数、图谱实体/边数是否达到阈值、正文声称数字是否等于 JSON 实算值、归档复制前后 sha256 是否逐件相等、同一输入两次运行输出是否逐字节相同。这些判据的误差为**精确 0**（计数相等/哈希相等），逐项见下表。

物理结论层面的局限必须写明：快照时点上游 N01–N07 全部处于在途或失败态，本报告**不为任何物理结论背书**；任何引用本波次结果的论文/下游节点，必须回到对应节点工作树的工件与探针结果自行复核。

探针的局限：pytest 探针只说明「该测试文件在该工作树该时刻的通过情况」，它不等于判据达成（判据要的是带来源的数值结论）；探针失败也不等于物理结论错误（可能是在途中间态）。本报告把两者分列，不做交叉推断。

- 快照语义：所有结论只代表采集时点；上游节点在快照之后继续推进，需要重跑采集器刷新。
- 自评回避：本节点不向上游数据包注入自己的结论（不写 `claims.json`），自己的 5 条判据在矩阵里同样只记 `EVIDENCE_ONLY`，由平台机裁而非自评通过；§6 的自检表只是把可机算数字摆出来供复核。
- 采样范围：证据只取工作树未提交/未跟踪变更，若某节点把结论写进已提交分支而未在账本留痕，本报告会低估其完成度（宁低不高，符合 honest FAIL 纪律）。

| 判据 | 目标 | 实测 | 是否达标 |
|---|---|---|---|
| C1-覆盖 | 全部 49 条判据入矩阵 | 49 行 | 是 |
| C2-图谱 | ≥20 实体 / ≥30 边 | 194 / 477 | 是 |
| C3-缺口 | ≥3 条且每条附提案草案 | 18 条 | 是 |
| C4-格式 | 报告自动校验零违规 | 0 违规 | 是 |
| C5-归档 | 全部工件 sha256 复核通过 | 59 件，错配 0 | 是 |

