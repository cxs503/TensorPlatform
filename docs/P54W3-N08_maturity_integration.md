# 战役成熟化整合与交付（p54w3-maturity-integration）

- `gate_schema_version`: `1.0`
- `input_digest`: `a87788fa72843adcb942fba2bafc856b0012d3ce5d6cba08b65a8a86ad0b502e`（规格 + 全部被读证据文档 sha256 的 sha256）
- 战役裁定：**WITHHELD**；节点 7 个，已交付证据 0 个，绿线 0.8，自述/复算冲突 0 处

## ① 案例介绍

战役收尾节点的物理目标：把各上游节点**自己验证过**的能力整合成一份可交付的工程输出。难点不在算一个数，而在「凭什么相信上游的数」——报告里的形容词、绿色的测试、以及并进去的 PR 都不能直接当证据。故本文的案例对象是**整合链本身**：

| 环节 | 输入 | 输出 | 机器判据 |
|---|---|---|---|
| 规格 | `data/maturity_gate/p54w3_campaign.spec.json` | 节点×判据×检查树 | 未知键/未知比较器/重复 id 一律拒绝 |
| 证据 | 各仓 `p54w3_evidence` 交付文档 / 已交付 study 文档 | 复算数值 + sha256 | 无证据不 PASS |
| 裁定 | 节点判据通过率 | PASS/FAIL/WITHHELD | 三值，缺证据停在 WITHHELD |
| 台账 | 判定体 | 节点↔能力条目 | 双向双射 |
| 契约 | 交付文档与端点 | 完整性分数 | 必含章节/字段/端点逐个匹配 |

参与整合的上游节点（规格工件注入，非代码硬编码）：

| 节点 | 仓库 | mission | 判据数 | 通过 | 存疑(WITHHELD) | 通过率 | 证据成熟度 |
|---|---|---|---|---|---|---|---|
| `p54w3-n01-my-ice-resistance` | TensorDEM | 8410 | 5 | 0 | 5 | 0 | WITHHELD |
| `p54w3-n02-brash-ice-evolution` | TensorDEM | 8411 | 6 | 0 | 6 | 0 | WITHHELD |
| `p54w3-n03-energy-ledger-convergence` | TensorDEM | 8412 | 6 | 0 | 6 | 0 | WITHHELD |
| `p54w3-n04-ice-load-pod` | ship-performance-platform | 8413 | 6 | 0 | 6 | 0 | WITHHELD |
| `p54w3-n05-plate-limit-analysis` | ship-performance-platform | 8414 | 6 | 0 | 6 | 0 | WITHHELD |
| `p54w3-n06-ridge-pipeline-dem` | TensorDEM | 8415 | 6 | 0 | 6 | 0 | WITHHELD |
| `p54w3-n07-energy-optimal-route` | ship-performance-platform | 8416 | 6 | 0 | 6 | 0 | WITHHELD |

## ② 怎么做的

命令：`python3 -m tools.maturity_gate.cli --work-root <work_root>`。工具链：`tools/maturity_gate/`（spec → evidence → gate → ledger → contracts → report 六个模块）+ `backend/api/maturity_gate.py`（同一判定的 HTTP 无状态面）+ pytest（合成域夹具 + 跨仓集成链）。

关键设置：

1. **判据是分母，检查不是**——一条验收清单常是复合命题（`α∈[0.05,0.2]` 且 `单调`），故一个判据由多个 check 合取；通过率按判据算，拆句刷分无效。
2. **三值裁定**：任一已交付节点低于绿线 → FAIL；全部已交付且达标 → PASS；否则 WITHHELD（缺证据≠通过≠不过）。
3. **钉桩 + 互裁**：证据文档可带 sha256，文档被改动即 FAIL(integrity)；上游自述 `pass` 与本引擎复算不一致记 claim_mismatch 并进台账。
4. **零领域语义**：代码内无学科词条；本战役的内容全部来自工件。证据交付协议：节点把可复算数字写进 `artifacts/p54w3_evidence/<node_key>.json`（结构见 `contracts/maturity-evidence.v1.schema.json`），`criteria.<id>.values.<name>` 供判据取值、`criteria.<id>.pass` 供互裁，`pytest.{returncode,passed,failed}` 供『全链测试』判据取值。

## ③ 计算结果

战役级数字（`artifacts/p54w3_maturity/gate.json` 程序化注入）：

| 量 | 值 |
|---|---|
| campaign.nodes_total | 7 |
| campaign.nodes_delivered | 0 |
| campaign.nodes_absent | ['p54w3-n01-my-ice-resistance', 'p54w3-n02-brash-ice-evolution', 'p54w3-n03-energy-ledger-convergence', 'p54w3-n04-ice-load-pod', 'p54w3-n05-plate-limit-analysis', 'p54w3-n06-ridge-pipeline-dem', 'p54w3-n07-energy-optimal-route'] |
| campaign.nodes_green | [] |
| campaign.nodes_red | [] |
| campaign.min_pass_ratio | — |
| campaign.mean_pass_ratio | — |
| campaign.criteria_pass_threshold | 0.8 |
| campaign.criterion_a_all_nodes_ge_threshold | false |
| campaign.claim_mismatch_total | 0 |
| campaign.verdict | WITHHELD |
| ledger.one_to_one | True |
| ledger.entries_total | 7 |
| contracts.completeness | 1 |
| contracts.complete | True |
| report.six_elements_complete | True |

整合节点自身六条交付判据（本表即评审面对照表）：

| 判据 | 结论 | 依据 |
|---|---|---|
| a upstream criteria >= threshold | NOT-PASS | delivered 0/7, green 0/7, min pass_ratio None, absent ['p54w3-n01-my-ice-resistance', 'p54w3-n02-brash-ice-evolution', 'p54w3-n03-energy-ledger-convergence', 'p54w3-n04-ice-load-pod', 'p54w3-n05-plate-limit-analysis', 'p54w3-n06-ridge-pipeline-dem', 'p54w3-n07-energy-optimal-route'] |
| b cross-repo integration pytest green | PASS | rc=0 passed=29 failed=0 errors=0 |
| c ledger one-to-one with nodes | PASS | entries 7 vs nodes 7, levels {'WITHHELD': 7} |
| d API contract doc complete | PASS | completeness 1.0000, missing [] |
| e report six elements | PASS | all present |
| f zero domain semantics | PASS | scanned 12 files, hits 0 |

存疑（WITHHELD）明细——**不知道**的项逐条列名，绝不计入通过：

| 节点 | 判据 | 陈述 | 具名原因 |
|---|---|---|---|
| `p54w3-n01-my-ice-resistance` | a | MY冰阻力系数 vs 文献实验数据误差 < 15% | evidence_doc_missing |
| `p54w3-n01-my-ice-resistance` | b | 混合冰场阻力随MY冰比例单调递增(Spearman相关系数=+1) | evidence_doc_missing |
| `p54w3-n01-my-ice-resistance` | c | 纯FY冰工况退化到已有模型误差 < 5% | evidence_doc_missing |
| `p54w3-n01-my-ice-resistance` | d | pytest全链通过含反向测试 | evidence_doc_missing |
| `p54w3-n01-my-ice-resistance` | e | 能量守恒闭合 < 5% | evidence_doc_missing |
| `p54w3-n02-brash-ice-evolution` | a | 烧结颈生长 vs Frenkel理论指数拟合误差 < 10% | evidence_doc_missing |
| `p54w3-n02-brash-ice-evolution` | b | 孔隙率下降 vs 再冻结理论拟合误差 < 15% | evidence_doc_missing |
| `p54w3-n02-brash-ice-evolution` | c | 碎冰场宏观强度增长 vs 文献实验数据 < 25% | evidence_doc_missing |
| `p54w3-n02-brash-ice-evolution` | d | 阻力衰减曲线单调递增且α∈[0.05,0.2] | evidence_doc_missing |
| `p54w3-n02-brash-ice-evolution` | e | 能量守恒闭合 < 10% | evidence_doc_missing |
| `p54w3-n02-brash-ice-evolution` | f | pytest全链通过含无烧结退化测试 | evidence_doc_missing |
| `p54w3-n03-energy-ledger-convergence` | a | 通量口径残差 < 1e-9 (SI归一化/E0) | evidence_doc_missing |
| `p54w3-n03-energy-ledger-convergence` | b | 显式口径斜率 /slope-1/ < 0.2 且梯形口径 /slope-2/ < 0.2 | evidence_doc_missing |
| `p54w3-n03-energy-ledger-convergence` | c | 恢复系数实测 vs 目标 < 1e-3 | evidence_doc_missing |
| `p54w3-n03-energy-ledger-convergence` | d | 5球Newton摆总动量守恒 < 1e-12 | evidence_doc_missing |
| `p54w3-n03-energy-ledger-convergence` | e | 给出m5404的5.111e-07显式判定(口径vs实现) | evidence_doc_missing |
| `p54w3-n03-energy-ledger-convergence` | f | 判据全部pytest化含失败回归守卫 | evidence_doc_missing |
| `p54w3-n04-ice-load-pod` | a | POD模态正交性误差 < 1e-10 | evidence_doc_missing |
| `p54w3-n04-ice-load-pod` | b | 能量谱单调递减 E_1 > E_2 > ... | evidence_doc_missing |
| `p54w3-n04-ice-load-pod` | c | K_95% < 20 (前20阶模态捕获>95%能量) | evidence_doc_missing |
| `p54w3-n04-ice-load-pod` | d | 空间相关性长度与冰厚正相关 l_c ∝ h^α, α∈[0.5,1.5] | evidence_doc_missing |
| `p54w3-n04-ice-load-pod` | e | POD重构应力 vs 全载荷场应力 < 10% | evidence_doc_missing |
| `p54w3-n04-ice-load-pod` | f | pytest全链通过含均匀载荷退化测试 | evidence_doc_missing |
| `p54w3-n05-plate-limit-analysis` | a | 上限≥下限且相对间隙 < 5% | evidence_doc_missing |
| `p54w3-n05-plate-limit-analysis` | b | 均布载荷简支方板上限 vs 经典解 < 2% | evidence_doc_missing |
| `p54w3-n05-plate-limit-analysis` | c | 固支方板Pu与简支解之比≈2倍(容差10%) | evidence_doc_missing |
| `p54w3-n05-plate-limit-analysis` | d | 离散加密后Pu变化 < 1% | evidence_doc_missing |
| `p54w3-n05-plate-limit-analysis` | e | 面积效应指数beta附95%置信区间 | evidence_doc_missing |
| `p54w3-n05-plate-limit-analysis` | f | 负控测试通过(Mp→0时Pu→0) | evidence_doc_missing |
| `p54w3-n06-ridge-pipeline-dem` | a | DEM竖向载荷 vs ISO19906 N_q理论 < 20% | evidence_doc_missing |
| `p54w3-n06-ridge-pipeline-dem` | b | 水平向载荷 vs ISO19906 N_c理论 < 25% | evidence_doc_missing |
| `p54w3-n06-ridge-pipeline-dem` | c | 管道覆土失稳判据与理论预测一致 | evidence_doc_missing |
| `p54w3-n06-ridge-pipeline-dem` | d | 能量守恒闭合 < 5% | evidence_doc_missing |
| `p54w3-n06-ridge-pipeline-dem` | e | 网格无关性(颗粒数翻倍)载荷变化 < 5% | evidence_doc_missing |
| `p54w3-n06-ridge-pipeline-dem` | f | pytest全链通过含无土壤退化测试 | evidence_doc_missing |
| `p54w3-n07-energy-optimal-route` | a | DP与穷举(≤2000节点小图)最优值相对差 < 1e-6 | evidence_doc_missing |
| `p54w3-n07-energy-optimal-route` | b | 账本各项非负总能耗闭合 < 1% | evidence_doc_missing |
| `p54w3-n07-energy-optimal-route` | c | 冰浓度升⇒最优航速降油耗升(Spearman单调性) | evidence_doc_missing |
| `p54w3-n07-energy-optimal-route` | d | ≥20点Pareto前沿非支配性逐点检验通过 | evidence_doc_missing |
| `p54w3-n07-energy-optimal-route` | e | 时间桶离散收敛(桶数加倍)最优油耗变化 < 2% | evidence_doc_missing |
| `p54w3-n07-energy-optimal-route` | f | 全敞水退化经典最省航速误差 < 5% | evidence_doc_missing |

逐判据明细（每行一次取值 + 一次谓词判定；`WITHHELD` 行的 reason 见 gate.json）：

| 节点 | 判据 | 陈述 | 取值指针 | 谓词 | 实测 | 判定 |
|---|---|---|---|---|---|---|
| p54w3-n01-my-ice-resistance | a | MY冰阻力系数 vs 文献实验数据误差 < 15% | criteria.a.values.rel_error | le 0.15 | — | WITHHELD |
| p54w3-n01-my-ice-resistance | b | 混合冰场阻力随MY冰比例单调递增(Spearman相关系数=+1) | criteria.b.values.spearman | eq 1 | — | WITHHELD |
| p54w3-n01-my-ice-resistance | c | 纯FY冰工况退化到已有模型误差 < 5% | criteria.c.values.rel_error | le 0.05 | — | WITHHELD |
| p54w3-n01-my-ice-resistance | d | pytest全链通过含反向测试 | pytest.returncode | eq 0 | — | WITHHELD |
| p54w3-n01-my-ice-resistance | d | pytest全链通过含反向测试 | pytest.failed | le 0 | — | WITHHELD |
| p54w3-n01-my-ice-resistance | e | 能量守恒闭合 < 5% | criteria.e.values.closure | le 0.05 | — | WITHHELD |
| p54w3-n02-brash-ice-evolution | a | 烧结颈生长 vs Frenkel理论指数拟合误差 < 10% | criteria.a_sintering_frenkel.exponent_rel_error | le 0.1 | — | WITHHELD |
| p54w3-n02-brash-ice-evolution | b | 孔隙率下降 vs 再冻结理论拟合误差 < 15% | criteria.b_refreezing_exponential.max_rel_error | le 0.15 | — | WITHHELD |
| p54w3-n02-brash-ice-evolution | c | 碎冰场宏观强度增长 vs 文献实验数据 < 25% | criteria.c_strength_literature.rel_error | le 0.25 | — | WITHHELD |
| p54w3-n02-brash-ice-evolution | d | 阻力衰减曲线单调递增且α∈[0.05,0.2] | criteria.d_resistance_growth.alpha | between [0.05, 0.2] | — | WITHHELD |
| p54w3-n02-brash-ice-evolution | d | 阻力衰减曲线单调递增且α∈[0.05,0.2] | criteria.d_resistance_growth.monotone | eq true | — | WITHHELD |
| p54w3-n02-brash-ice-evolution | e | 能量守恒闭合 < 10% | criteria.e_energy_closure.closure | le 0.1 | — | WITHHELD |
| p54w3-n02-brash-ice-evolution | f | pytest全链通过含无烧结退化测试 | pytest.returncode | eq 0 | — | WITHHELD |
| p54w3-n03-energy-ledger-convergence | a | 通量口径残差 < 1e-9 (SI归一化/E0) | criteria.a.values.residual | le 1.000e-09 | — | WITHHELD |
| p54w3-n03-energy-ledger-convergence | b | 显式口径斜率 /slope-1/ < 0.2 且梯形口径 /slope-2/ < 0.2 | criteria.b.values.slope_explicit_minus1_abs | le 0.2 | — | WITHHELD |
| p54w3-n03-energy-ledger-convergence | b | 显式口径斜率 /slope-1/ < 0.2 且梯形口径 /slope-2/ < 0.2 | criteria.b.values.slope_trapezoid_minus2_abs | le 0.2 | — | WITHHELD |
| p54w3-n03-energy-ledger-convergence | c | 恢复系数实测 vs 目标 < 1e-3 | criteria.c.values.rel_error | le 0.001 | — | WITHHELD |
| p54w3-n03-energy-ledger-convergence | d | 5球Newton摆总动量守恒 < 1e-12 | criteria.d.values.momentum_residual | le 1.000e-12 | — | WITHHELD |
| p54w3-n03-energy-ledger-convergence | e | 给出m5404的5.111e-07显式判定(口径vs实现) | criteria.e.values.adjudicated | eq true | — | WITHHELD |
| p54w3-n03-energy-ledger-convergence | f | 判据全部pytest化含失败回归守卫 | pytest.returncode | eq 0 | — | WITHHELD |
| p54w3-n03-energy-ledger-convergence | f | 判据全部pytest化含失败回归守卫 | pytest.failed | le 0 | — | WITHHELD |
| p54w3-n04-ice-load-pod | a | POD模态正交性误差 < 1e-10 | criteria.a.values.orthogonality_error | le 1.000e-10 | — | WITHHELD |
| p54w3-n04-ice-load-pod | b | 能量谱单调递减 E_1 > E_2 > ... | criteria.b.values.monotone | eq true | — | WITHHELD |
| p54w3-n04-ice-load-pod | c | K_95% < 20 (前20阶模态捕获>95%能量) | criteria.c.values.k95 | lt 20 | — | WITHHELD |
| p54w3-n04-ice-load-pod | d | 空间相关性长度与冰厚正相关 l_c ∝ h^α, α∈[0.5,1.5] | criteria.d.values.alpha | between [0.5, 1.5] | — | WITHHELD |
| p54w3-n04-ice-load-pod | e | POD重构应力 vs 全载荷场应力 < 10% | criteria.e.values.rel_error | le 0.1 | — | WITHHELD |
| p54w3-n04-ice-load-pod | f | pytest全链通过含均匀载荷退化测试 | pytest.returncode | eq 0 | — | WITHHELD |
| p54w3-n05-plate-limit-analysis | a | 上限≥下限且相对间隙 < 5% | criteria.a.values.upper_ge_lower | eq true | — | WITHHELD |
| p54w3-n05-plate-limit-analysis | a | 上限≥下限且相对间隙 < 5% | criteria.a.values.rel_gap | le 0.05 | — | WITHHELD |
| p54w3-n05-plate-limit-analysis | b | 均布载荷简支方板上限 vs 经典解 < 2% | criteria.b.values.rel_error | le 0.02 | — | WITHHELD |
| p54w3-n05-plate-limit-analysis | c | 固支方板Pu与简支解之比≈2倍(容差10%) | criteria.c.values.ratio | between [1.8, 2.2] | — | WITHHELD |
| p54w3-n05-plate-limit-analysis | d | 离散加密后Pu变化 < 1% | criteria.d.values.change | le 0.01 | — | WITHHELD |
| p54w3-n05-plate-limit-analysis | e | 面积效应指数beta附95%置信区间 | criteria.e.values.beta_ci | nonempty — | — | WITHHELD |
| p54w3-n05-plate-limit-analysis | f | 负控测试通过(Mp→0时Pu→0) | criteria.f.values.negative_control_pass | eq true | — | WITHHELD |
| p54w3-n06-ridge-pipeline-dem | a | DEM竖向载荷 vs ISO19906 N_q理论 < 20% | criteria.a.values.rel_error | le 0.2 | — | WITHHELD |
| p54w3-n06-ridge-pipeline-dem | b | 水平向载荷 vs ISO19906 N_c理论 < 25% | criteria.b.values.rel_error | le 0.25 | — | WITHHELD |
| p54w3-n06-ridge-pipeline-dem | c | 管道覆土失稳判据与理论预测一致 | criteria.c.values.consistent | eq true | — | WITHHELD |
| p54w3-n06-ridge-pipeline-dem | d | 能量守恒闭合 < 5% | criteria.d.values.closure | le 0.05 | — | WITHHELD |
| p54w3-n06-ridge-pipeline-dem | e | 网格无关性(颗粒数翻倍)载荷变化 < 5% | criteria.e.values.change | le 0.05 | — | WITHHELD |
| p54w3-n06-ridge-pipeline-dem | f | pytest全链通过含无土壤退化测试 | pytest.returncode | eq 0 | — | WITHHELD |
| p54w3-n07-energy-optimal-route | a | DP与穷举(≤2000节点小图)最优值相对差 < 1e-6 | criteria.a.values.rel_diff | le 1.000e-06 | — | WITHHELD |
| p54w3-n07-energy-optimal-route | b | 账本各项非负总能耗闭合 < 1% | criteria.b.values.all_nonnegative | eq true | — | WITHHELD |
| p54w3-n07-energy-optimal-route | b | 账本各项非负总能耗闭合 < 1% | criteria.b.values.closure | le 0.01 | — | WITHHELD |
| p54w3-n07-energy-optimal-route | c | 冰浓度升⇒最优航速降油耗升(Spearman单调性) | criteria.c.values.spearman_speed | eq -1 | — | WITHHELD |
| p54w3-n07-energy-optimal-route | c | 冰浓度升⇒最优航速降油耗升(Spearman单调性) | criteria.c.values.spearman_fuel | eq 1 | — | WITHHELD |
| p54w3-n07-energy-optimal-route | d | ≥20点Pareto前沿非支配性逐点检验通过 | criteria.d.values.dominated_points | le 0 | — | WITHHELD |
| p54w3-n07-energy-optimal-route | d | ≥20点Pareto前沿非支配性逐点检验通过 | criteria.d.values.front_size | ge 20 | — | WITHHELD |
| p54w3-n07-energy-optimal-route | e | 时间桶离散收敛(桶数加倍)最优油耗变化 < 2% | criteria.e.values.change | le 0.02 | — | WITHHELD |
| p54w3-n07-energy-optimal-route | f | 全敞水退化经典最省航速误差 < 5% | criteria.f.values.rel_error | le 0.05 | — | WITHHELD |

## ④ 结果图

![fig1_node_status.png](artifacts/p54w3_maturity/fig1_node_status.png)

*每节点判据 P/N 堆叠（蓝=PASS，红=FAIL，灰=WITHHELD）+ 通过率折线与 0.8 绿线。灰柱意味着『不知道』，不是『不过』。*

![fig2_delivery_criteria.png](artifacts/p54w3_maturity/fig2_delivery_criteria.png)

*整合节点六条交付判据看板（a 上游判据≥80%、b 跨仓集成 pytest、c 台账双射、d 契约完整、e 报告六要素、f 零领域语义）。*

## ⑤ 注意点

- 上游多数节点仍在飞：7 个节点零可复算证据 → 战役裁定停在 WITHHELD，本报告不把『在飞』写成『通过』。
- 证据协议是**新契约**：既有上游交付（如 study.json）字段命名各异，只有显式映射进规格的节点能被取值复算；未映射的字段一律 WITHHELD，不做模糊猜测。
- 判据通过率分母是**声明判据数**：上游若少声明一条判据，分母同步变小，这是刻意的（少声明即少交付），故报告中逐条列出判据总数供审阅。
- 跨仓集成 pytest 只覆盖平台侧判定链（合成域夹具 + 真实战役工件）；不代跑上游仓库测试——上游测试执行属于其 own mission 的验收，平台不越权。
- 本战役多数节点无实验基准（残差/正交性/闭合量），故『误差』按阈值余量口径给出，不作跨学科的 3% 统一宣称。
- 既有机制分工：`scripts/maturity_gate.py` 管平台模块 maturity 标签证据、`tools/criteria_governance/maturity.py` 管判据集四维治理，本包只管战役节点交付裁定；三者不重叠，后续若合并须先统一证据指纹口径（本包用 canonical_json 指纹以便磁盘/内存两路同值）。

## ⑥ 误差判定

判据不是「误差<3%」型的连续量对标，而是**阈值谓词**；故本节逐条给出「实测 vs 阈值」的相对余量，并显式回答是否<3%。

| 判据 | 实测 | 阈值 | 相对阈值余量 | 判定 |
|---|---|---|---|---|
| `p54w3-n01-my-ice-resistance::a` | — | 0.15 | — | WITHHELD |
| `p54w3-n01-my-ice-resistance::b` | — | 1 | — | WITHHELD |
| `p54w3-n01-my-ice-resistance::c` | — | 0.05 | — | WITHHELD |
| `p54w3-n01-my-ice-resistance::d` | — | 0 | — | WITHHELD |
| `p54w3-n01-my-ice-resistance::d` | — | 0 | — | WITHHELD |
| `p54w3-n01-my-ice-resistance::e` | — | 0.05 | — | WITHHELD |
| `p54w3-n02-brash-ice-evolution::a` | — | 0.1 | — | WITHHELD |
| `p54w3-n02-brash-ice-evolution::b` | — | 0.15 | — | WITHHELD |
| `p54w3-n02-brash-ice-evolution::c` | — | 0.25 | — | WITHHELD |
| `p54w3-n02-brash-ice-evolution::d` | — | [0.05, 0.2] | — | WITHHELD |
| `p54w3-n02-brash-ice-evolution::d` | — | true | — | WITHHELD |
| `p54w3-n02-brash-ice-evolution::e` | — | 0.1 | — | WITHHELD |
| `p54w3-n02-brash-ice-evolution::f` | — | 0 | — | WITHHELD |
| `p54w3-n03-energy-ledger-convergence::a` | — | 1.000e-09 | — | WITHHELD |
| `p54w3-n03-energy-ledger-convergence::b` | — | 0.2 | — | WITHHELD |
| `p54w3-n03-energy-ledger-convergence::b` | — | 0.2 | — | WITHHELD |
| `p54w3-n03-energy-ledger-convergence::c` | — | 0.001 | — | WITHHELD |
| `p54w3-n03-energy-ledger-convergence::d` | — | 1.000e-12 | — | WITHHELD |
| `p54w3-n03-energy-ledger-convergence::e` | — | true | — | WITHHELD |
| `p54w3-n03-energy-ledger-convergence::f` | — | 0 | — | WITHHELD |
| `p54w3-n03-energy-ledger-convergence::f` | — | 0 | — | WITHHELD |
| `p54w3-n04-ice-load-pod::a` | — | 1.000e-10 | — | WITHHELD |
| `p54w3-n04-ice-load-pod::b` | — | true | — | WITHHELD |
| `p54w3-n04-ice-load-pod::c` | — | 20 | — | WITHHELD |
| `p54w3-n04-ice-load-pod::d` | — | [0.5, 1.5] | — | WITHHELD |
| `p54w3-n04-ice-load-pod::e` | — | 0.1 | — | WITHHELD |
| `p54w3-n04-ice-load-pod::f` | — | 0 | — | WITHHELD |
| `p54w3-n05-plate-limit-analysis::a` | — | true | — | WITHHELD |
| `p54w3-n05-plate-limit-analysis::a` | — | 0.05 | — | WITHHELD |
| `p54w3-n05-plate-limit-analysis::b` | — | 0.02 | — | WITHHELD |
| `p54w3-n05-plate-limit-analysis::c` | — | [1.8, 2.2] | — | WITHHELD |
| `p54w3-n05-plate-limit-analysis::d` | — | 0.01 | — | WITHHELD |
| `p54w3-n05-plate-limit-analysis::e` | — | — | — | WITHHELD |
| `p54w3-n05-plate-limit-analysis::f` | — | true | — | WITHHELD |
| `p54w3-n06-ridge-pipeline-dem::a` | — | 0.2 | — | WITHHELD |
| `p54w3-n06-ridge-pipeline-dem::b` | — | 0.25 | — | WITHHELD |
| `p54w3-n06-ridge-pipeline-dem::c` | — | true | — | WITHHELD |
| `p54w3-n06-ridge-pipeline-dem::d` | — | 0.05 | — | WITHHELD |
| `p54w3-n06-ridge-pipeline-dem::e` | — | 0.05 | — | WITHHELD |
| `p54w3-n06-ridge-pipeline-dem::f` | — | 0 | — | WITHHELD |
| `p54w3-n07-energy-optimal-route::a` | — | 1.000e-06 | — | WITHHELD |
| `p54w3-n07-energy-optimal-route::b` | — | true | — | WITHHELD |
| `p54w3-n07-energy-optimal-route::b` | — | 0.01 | — | WITHHELD |
| `p54w3-n07-energy-optimal-route::c` | — | -1 | — | WITHHELD |
| `p54w3-n07-energy-optimal-route::c` | — | 1 | — | WITHHELD |
| `p54w3-n07-energy-optimal-route::d` | — | 0 | — | WITHHELD |
| `p54w3-n07-energy-optimal-route::d` | — | 20 | — | WITHHELD |
| `p54w3-n07-energy-optimal-route::e` | — | 0.02 | — | WITHHELD |
| `p54w3-n07-energy-optimal-route::f` | — | 0.05 | — | WITHHELD |

- 误差口径：判据多为「≤阈值」型谓词（相对误差/残差/闭合量），『相对阈值余量』列 = |实测|/|阈值|，小于 1 即满足；这不是与实验值的对标误差。
- 有实验/理论锚的对标项按各自阈值（2%–25%）表述，不统一吹成 3%；无基准项显式声明原因。

**是否<3%？** **不能宣称**：本战役仍有多数节点零证据（WITHHELD），此时给出任何<3%的结论都是无依据的过度声明。

---

审计链：`data/maturity_gate/p54w3_campaign.spec.json` → 证据文档 sha256 → `gate.json`（input_digest `a87788fa72843adc…`）→ `p54w3_maturity_ledger.json` → `p54w3_maturity_report_check.json`。每个数字都可用同一命令复算，逐字节一致。
