# IN28-G25 继承门裁决报告（跨里程碑一致性 + 继承就绪度）

- 战役：plan 83 — IN28-求解器验证与治理深化
- 节点：IN28-G25（mission 5519，node 539）
- 裁决：**NOT_READY**；综合评分 **0.000**（阈值 0.7）
- 输入快照摘要 sha256：`497b2f5d454420978e0441029b95e715c114e05221f4bb20424626f725930071`
- 生成时间（UTC）：2026-11-07T13:01:21+00:00（仅落报告，裁决本身不取时钟）

## 一、案例介绍（物理与工程背景）

IN28 战役把「求解器验证面」与「平台治理成熟化」并行推进，共 4 个里程碑 9 个节点：
M1 求解器验证基础（N01 跨求解器船型阻力、N02 湍流模型矩阵、N03 共轭传热）、
M2 船舶性能验证面（N04 真实船型阻力闭合、N05 双船水动力干扰）、
M3 平台治理成熟化（N06 影子决策成熟化、N07 议会裁决账本、N08 知识库引导）、
M4 继承门 G25（本节点）。

继承门的物理含义：**下游战役可以继承上游能力，前提是上游的每个数字都有一条可复算的证据链，
且不同求解器对同一物理量的判断彼此不矛盾**。因此 G25 不是"汇总报告"而是**裁决器**：
它把每个节点的验收判据、测量值、互裁容差作为机器可算输入，输出继承就绪度评分与阻塞清单。

几何/参数表：本节点为**流程与证据治理节点**，没有几何体；其"参数表"是
8 个节点 × 49 条验收判据 × 4 条硬门 × 4 个评分分量（见下）。

## 二、怎么做的（方法 / 流程 / 工具链）

1. **把门做成纯函数**：`ops/taskplane/inheritance_gate.py`
   `evaluate_inheritance_gate(snapshot, config) -> dict`。snapshot = 战役 plan/milestones/nodes/
   missions/reports 的只读快照；config = 阈值/权重/容差/治理节点集合。同一输入必得逐字节相同
   报告（`input_digest` 由内容派生，不取 wall clock），可离线复算、可进 CI。
2. **零领域语义**：模块内不含任何领域词条/判据/算例。判据文本从
   `campaign_nodes.metadata.acceptance_criteria` 解析（通用语法 `<key> <op> <number>[%]`），
   测量值来自节点自带证据块 `inheritance_evidence`，容差与治理节点集合由 config 注入。
3. **反伪造三条纪律**：
   (i) 有判据文本却拿不出测量值 → `unproven`，**不算通过**；
   (ii) 百分点判据缺 unit → `unproven`，绝不猜量纲（6.1 与 0.061 的静默换算是造假通道）；
   (iii) `reproduction_verdict=fail` 或缺失（默认要求 pass）→ 节点不算验收，
   对齐平台红队规则「伪造完成节点（无复现判决）→ 快照不算 done」。
4. **工具链**：`python3 ops/taskplane/inheritance_gate.py --api <base> --plan 83`
   只读拉快照 → `artifacts/in28_g25/snapshot.json`；再用 `--snapshot/--config` 复算 →
   `artifacts/in28_g25/inheritance_gate.json`；`tools/gen_in28_g25_report.py` 出图与本文。
5. **验收测试**：`tests/test_inheritance_gate.py` — **39 passed**（判据解析/求值、
   全绿 ready、在飞 not_ready、伪造完成三连、一致性越限、治理硬门、权重重归一化、
   确定性、CLI 退出码、证据来源三通道）。

## 三、计算结果（数字全部程序化注入，无手抄）

| 指标 | 数值 |
|---|---|
| 节点总数 / 验收通过 | 8 / 0（0.0%） |
| 验收判据 声明 / 已测 / 通过 | 49 / 0 / 0 |
| 里程碑 完成 / 覆盖 | 0 / 3 |
| 跨节点互裁量 收集 / 交叉裁决 | 0 / 0 |
| 治理节点 验收 / 总数 | 0 / 3 |
| 综合评分 | **0.000000**（阈值 0.7） |
| 阻塞项 | 134 条 |

**分量分解**（权重为 config 注入、不适用分量自动重归一化）：

| 分量 | 得分 | 权重 | 加权贡献 | 适用 |
|---|---|---|---|---|
| milestone | 0.0000 | 0.400 | 0.0000 | True |
| criteria | 0.0000 | 0.250 | 0.0000 | True |
| consistency | 0.0000 | 0.200 | 0.0000 | True |
| governance | 0.0000 | 0.150 | 0.0000 | True |

**硬门裁决**：

| 硬门 | 判据 | 结果 |
|---|---|---|
| all_covered_milestones_complete | M1–M3 覆盖里程碑内全部节点验收通过 | FAIL |
| governance_nodes_accepted | 治理类节点（N06/N07/N08）全部验收 | FAIL |
| cross_consistency_within_tolerance | 跨节点关键量互裁偏差在容差内 | FAIL |
| score_above_min | 综合评分 ≥ 0.7 | FAIL |

**对照实验（判别力证明）**：真实快照裁决 not_ready 只说明上游未交付，不能证明门有判别力。
故用同一份门的**阳性/阴性对照**（`tools/in28_g25_gate_controls.py`，快照在内存合成、
标注 synthetic、不写库）：阳性对照（全部判据给达标测量值 + 布尔判据给通过声明+工件指针 +
共享互裁量 spread≈0.2% < 容差 10%）→ verdict=`ready`、score=1.0000；
阴性对照各只注入一个缺陷，全部门内拦截：

| 对照 | 注入缺陷 | 期望 | 实际裁决 | 评分 | 命中阻塞项 | 断言 |
|---|---|---|---|---|---|---|
| REAL | node_not_done | not_ready | not_ready | 0.0000 | — | PASS |
| POSITIVE_CONTROL_all_evidence | —（应放行） | ready | ready | 1.0000 | — | PASS |
| T1_measurement_out_of_limit | criterion_failed | not_ready | not_ready | 0.8616 | criterion_failed(measurement_out_of_limit):531:C_T_LBM_vs_FVM < 8% | PASS |
| T2_pct_without_unit | pct_criterion_requires_unit | not_ready | not_ready | 0.8616 | criterion_unproven(pct_criterion_requires_unit):531:C_T_LBM_vs_FVM < 8% | PASS |
| T3_consistency_violation | consistency_violation:q_shared | not_ready | not_ready | 0.9000 | consistency_violation:q_shared(spread=0.5547>tol=0.1) | PASS |
| T4_governance_evidence_gap | governance_node_not_accepted | not_ready | not_ready | 0.7810 | governance_node_not_accepted:536 | PASS |

对照还暴露了两处真实语法缺口并已修：本战役 16/49 条判据是**质性/布尔式**
（如 `regression_suite_green`、`db_row_match`），旧实现一律 `criterion_unparsed` 直接判死；
现按「通过声明 + 工件指针」裁决（只要声明不要工件仍 `unproven`）。另有 `pass_rate >= 2/3`
这类**分数阈值**判据，现按比值求值。二者均有 pytest 断言防回退。

**节点排除**：门自身节点（M4/G25，mission 5519）由 config `exclude_node_ids` 排除，
不进评分分母（排除项在报告 `excluded_nodes` 留痕）——门不给自己打分。

**节点验收摘要**（含每条判据的 pass/declared）：

| 节点 | 名称 | 里程碑 | 节点状态 | 复现判决 | mission/状态 | 判据 pass/declared | 验收 |
|---|---|---|---|---|---|---|---|
| IN28-N01 | IN28-N01 跨求解器Wigley验证 | M206 | in-flight | None | 5520/running | 0/6 | FAIL |
| IN28-N02 | IN28-N02 湍流模型验证矩阵 | M206 | in-flight | None | 5515/running | 0/5 | FAIL |
| IN28-N03 | IN28-N03 共轭传热验证 | M206 | in-flight | None | 5521/running | 0/6 | FAIL |
| IN28-N04 | IN28-N04 真实船型阻力验证闭合 | M207 | in-flight | None | 5522/running | 0/8 | FAIL |
| IN28-N05 | IN28-N05 双船水动力相互作用 | M207 | in-flight | None | 5523/running | 0/8 | FAIL |
| IN28-N06 | IN28-N06 SR1影子决策成熟化与v0退役 | M208 | in-flight | None | 5516/running | 0/7 | FAIL |
| IN28-N07 | IN28-N07 议会裁决账本接库与语料扩充 | M208 | in-flight | None | 5517/running | 0/5 | FAIL |
| IN28-N08 | IN28-N08 ship_samples知识库引导 | M208 | in-flight | None | 5518/running | 0/4 | FAIL |

**里程碑**：

| 里程碑 | 名称 | 节点完成 | 判据通过 | 完成 | 阻塞节点 |
|---|---|---|---|---|---|
| M206 | M1 求解器验证基础 | 0/3 | 0/17 | False | 531, 532, 533 |
| M207 | M2 船舶性能验证面 | 0/2 | 0/16 | False | 534, 535 |
| M208 | M3 平台治理成熟化 | 0/3 | 0/16 | False | 536, 537, 538 |

## 四、结果图

- **fig1_node_acceptance_matrix.png**：节点验收矩阵 —— 6 项检查（节点 done / 复现判决 / mission 状态 /
  证据可溯源 / 判据全通过 / 最终验收）逐节点二值呈现，一眼看出卡在哪一项。
- **fig2_score_components.png**：继承就绪度分量分解 —— 柱=分量得分，填充=加权贡献，虚线=阈值，
  实线=综合评分。当前四项全 0，故综合评分为 0.000。
- **fig3_milestone_progress.png**：里程碑完成度（a: 节点验收；b: 判据通过）——
  判据缺口与节点缺口分离呈现，避免"节点 done 但判据没测"被掩盖。
- **fig4_blocker_taxonomy.png**：阻塞项分类 —— 门只等不放、绝不写 failed；分类后可直接派单。

![Fig.1](../../artifacts/in28_g25/fig1_node_acceptance_matrix.png)
![Fig.2](../../artifacts/in28_g25/fig2_score_components.png)
![Fig.3](../../artifacts/in28_g25/fig3_milestone_progress.png)
![Fig.4](../../artifacts/in28_g25/fig4_blocker_taxonomy.png)

## 五、注意点（踩坑 / 局限 / 适用范围）

1. **本裁决是 snapshot 时刻的诚实读数，不是判决上游永久失败**。快照取自
   http://127.0.0.1:8010/api/v1 plan 83：
   9 个节点中 9 个非 done、9 个 mission 中
   9 个仍在 running/queued、报告数 0 —— 上游确实还在执行，
   因此 G25 的正确动作是**等**（门不放行、不写 failed），等上游交付后重跑同一命令即可复裁。
2. **"节点 done"不等于"判据通过"**：本门要求每条判据都有测量值且落在限内，
   `unproven` 一律不算通过。这一条会让"done 但没测量值"的节点在门这里掉下来 —— 有意为之。
3. **量纲不猜**：百分点判据必须带 `unit`（`%` 或 `fraction`）。缺 unit 直接 `unproven`，
   因为 6.1/0.061 这类换算错误在历史上造成过 493× 量级误判，静默纠正会掩盖缺陷。
4. **容差来源**：跨求解器互裁容差默认 0.10（外包络），逐量收紧由证据条目自带 `tol`
   或 config `consistency.tolerance_by_key` 注入；本战役各节点判据容差并不相同
   （8%/10%/15% 等），逐量容差须由节点在证据块声明，门不替节点拍容差。
5. **单源量不算互裁**：只有一个节点给出的量记为"无交叉裁决"（fail），
   防止"只报一家数"就拿到一致性满分；`min_shared_keys` 保证至少存在真实互裁。
6. **适用范围**：本模块只管"节点 status/verdict/证据/判据/一致性/治理"六类可机器裁的量；
   物理正确性本身由各节点的解析解/实验对标判据承载，门只核对"是否真的被测过、测值是否达标"。

## 六、误差判定（显式回答是否 <3%）

本节点是**门/裁决类节点**，其产出是裁决布尔值与评分，不存在"物理量 vs 基准"的连续误差，
因此不适用 3% 误差判据。可类比的"误差"指标是**裁决可复算性**：

| 项目 | 基准 | 本实现 | 误差 |
|---|---|---|---|
| 同输入重复裁决 | 期望逐字节相同 | 两次裁决 JSON 完全相同（pytest 断言的确定性用例） | 0（精确相等） |
| 判据求值正确性 | 解析式算符语义（<,<=,>,>=,=） | 39 条 pytest 覆盖含边界与越限 | 0（逐条断言） |
| 快照溯源 | `input_digest` = 快照内容 sha256 | 内容变更 → digest 变更（pytest 断言） | 0（精确相等） |

**结论**：无物理基准可对标，故不声明"<3%"；上表三项为可机器验证的**精确相等**判据（误差 0）。
honest-FAIL 声明：**继承门裁决 = NOT_READY，评分为 0.000 < 0.7，
G25 不予放行**；上游 0/8 节点验收通过、0/49 条判据有可裁测量值、0 条互裁量。
本单完成的是**门机制 + 诚实裁决 + 阻塞清单**，不是"让上游通过"。

