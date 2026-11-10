# 跨架构基准复现门成熟化：loong64 vs x86 浮点可复现性验证与容差带

**任务**：p216w3-n09（战役 DAG 节点，TensorPlatform 平台共性机制）
**工件**：`tools/cross_arch_gate/`（门 + 合成夹具 + 变异自检 + 审计）、`artifacts/cross_arch_gate/`（逐臂记录、逐对报告、判据 JSON、图）、`tests/test_cross_arch_gate.py`

**结论一句话**：门在 6 个臂对上运行，裁决 6/6 PASS；整数域逐位一致率 1.0000，双精度容差带内字段占比 1.0000，超容差检出率 1.0000，误报率 0.0000，NaN 检出率 1.0000，端到端时延 0.31 s，零领域语义命中 0；**六项判据 + 逐对裁决 all_pass = True**（逐对全 PASS = True）。

## ① 案例介绍（物理背景 / 几何 / 参数表）

数值证据要能被跨机器互认，前提是「同一份代码在两套 ISA 上给出同一份输出」。本平台的真实工作形态是：生产算力是 loongarch64 集群（n315/n330），质量基准面（CI）是 x86_64；任何一条「某工况的结果已复现」的声明，都要跨这两面成立。因此本节点把可复现性做成**门**而不是习惯。

门的判据单位是**字段**（field），不是文件：本次参考臂共 48 个字段，分成 4 类算术质地 + 2 类容差语义：

| 族 | 算术质地 / 离散 | 字段类型 | 字段数 |
|---|---|---|---|
| int_lattice | 整数 LCG 行走 + 混合模数折叠（2000 步） | 整数 8 | 8 |
| int_matrix | 6×6 模整数矩阵-向量三趟链 | 整数 4 | 4 |
| chain | 声明式直线程序 + − × ÷ √ fma（20 步，单步单次舍入） | 双精度（观测量） 20 | 20 |
| reduction | 4096 项归约：和/点积/均值/方差（对结合序敏感） | 双精度（观测量） 6 | 6 |
| transcendental | exp/log/log2/pow/sin/cos 两条代数路径（libm 路径代理） | 双精度（观测量） 6 | 6 |
| cancel | 抵消型字段：2 个观测量 + 2 个声明诊断量（O(1e6) 项抵到 ~1e-17） | 双精度（观测量） 2, 双精度（声明诊断量） 2 | 4 |

**参数表（门配置，可注入）**：

| 参数 | 取值 | 作用 |
|---|---|---|
| `fp64_rel_tol` | 1e-10 | 双精度观测量相对容差（与 ULP 上限并联，缺一不可） |
| `fp64_ulp_cap` | 65536 | ULP 上限：挡住量级极小但相对差很小的伪一致 |
| `diag_abs_tol` | 1e-14 | 声明诊断量的绝对容差（相对度量对其无意义） |
| `int_strict` | True | 整数域只接受逐位一致（整数没有舍入自由度） |

**为什么这四类质地能承重**：整数域任何差异都只能是代码生成/发射缺陷；直线程序每一 op 恰好一次 IEEE 舍入，可被可移植精确 oracle 逐位预测；归约与超越函数两族承载**合法**的跨实现差异（结合序、libm 路径），它们不是缺陷、只能靠容差带判；抵消型诊断量则证明相对度量在此类字段上失效、必须换成绝对容差——四种情形正好覆盖门需要区分的全部判决。

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

1. **记录层**（`record.py`）：每个臂输出一份不可变记录——逐字段 `%a` 精确十六进制、字段类型、整份记录的 SHA-256 digest，加上机器溯源（`platform.machine()`、CPU 型号、Python/编译器版本、变体开关、墙钟）。记录是纯 JSON，可归档、可 diff。
2. **臂矩阵**（`pipeline.ARM_MATRIX`）：参考臂 = 原生默认；其余臂每次只隔离一个机制：同配置重跑（持平性地板）、可移植 IEEE oracle（逐 op 精确有理数 + 一次 half-to-even 舍入）、FMA 收缩、结合序反向 / `math.fsum`、libm 替代代数路径。
3. **判决层**（`classify.py`）：四类标签 `BIT_IDENTICAL` / `WITHIN_TOLERANCE` / `OVER_TOLERANCE` / `NAN`；结构差异（字段缺失、类型漂移）映射到 `OVER_TOLERANCE` 并附 `detail` 码，因为对消费者而言缺字段就是最大容差违背。
4. **裁决层**（`gate.py`）：`compare(ref, cand, policy)` 是纯函数，返回逐字段行 + 统计量 + PASS/FAIL 与失败原因列表；绝不修改输入。
5. **自检层**（`selftest.py`）：8 类变异（`perturb_1e-9`、`perturb_1e-6`、`perturb_1e-13`、`nudge_4ulp`、`inject_nan`、`flip_int`、`drop_field`、`zero_abs_shift`）+ 干净对照，逐一检查「该抓的抓住、不该抓的不许动」，这正是判据 3/4 的测量方式（族清单是数据，本句计数由代码生成，不会随增族漂移）。
6. **审计层**（`audit.py`）：用平台领域词表（配置而非被测体）扫门包、pytest 与策略 JSON，命中数必须为 0——把「零领域语义」从评审习惯变成机器判据。
7. **工具链**：Python 3.11 标准库 + matplotlib（仅出图）；无第三方数值依赖，因此同一份代码可在 loong64 控制面、loong64 集群节点、x86 CI 上原样执行。

## ③ 计算结果

### 3.1 臂对裁决（参考臂 = `native-reference`；参与机器 `loongarch64`）

| 候选臂 | 裁决 | digest 相同 | 位级一致 | 容差内 | 超容差 | NaN | 最大相对差 | 最大 ULP |
|---|---|---|---|---|---|---|---|---|
| native-rerun | PASS | 是 | 48 | 0 | 0 | 0 | 0.000e+00 | 0 |
| ieee-oracle | PASS | 否 | 45 | 3 | 0 | 0 | 1.330e-15 | 6 |
| fma-contract | PASS | 否 | 47 | 1 | 0 | 0 | 1.377e-16 | 1 |
| assoc-reverse | PASS | 否 | 46 | 2 | 0 | 0 | 2.881e-15 | 13 |
| assoc-fsum | PASS | 否 | 46 | 2 | 0 | 0 | 1.330e-15 | 6 |
| libm-altroute | PASS | 否 | 47 | 1 | 0 | 0 | 1.625e-16 | 1 |

### 3.2 六项机器判据

| 判据 | 实测 | 门限 | 通过 | 口径 |
|---|---|---|---|---|
| `c1_int_bit_exact_rate` | 1.0 | >= 1.0 | ✅ | worst over 6 arm pair(s); integer fields only |
| `c2_fp_within_band_fraction` | 1.0 | >= 0.95 | ✅ | observable fp64 fields with relative error < 1e-10 |
| `c3_false_positive_rate` | 0.0 | <= 0.0 | ✅ | 0/2364 benign fields misflagged |
| `c3_over_tolerance_detection_rate` | 1.0 | >= 1.0 | ✅ | 156/156 injected fields flagged |
| `c4_nan_detection_rate` | 1.0 | >= 1.0 | ✅ | 36/36 injected NaN fields flagged |
| `c5_e2e_latency_s` | 0.308 | <= 300.0 | ✅ | gate compute end to end (cluster queue excluded) |
| `c6_domain_semantics_hits` | 0 | <= 0 | ✅ | 16 file(s) scanned against the platform lexicon |

### 3.3 变异自检（判官自身的检出/误报）

| 变异 | 期望标签 | 注入字段 | 被检出 | 该臂裁决 |
|---|---|---|---|---|
| clean | （干净对照） | 0 | 0 | PASS |
| perturb_1e-9 | OVER_TOLERANCE | 6 | 6 | FAIL |
| perturb_1e-6 | OVER_TOLERANCE | 6 | 6 | FAIL |
| perturb_1e-13 | WITHIN_TOLERANCE | 0 | 0 | PASS |
| nudge_4ulp | WITHIN_TOLERANCE | 0 | 0 | PASS |
| inject_nan | NAN | 6 | 6 | FAIL |
| flip_int | OVER_TOLERANCE | 3 | 3 | FAIL |
| drop_field | OVER_TOLERANCE | 3 | 3 | FAIL |
| zero_abs_shift | OVER_TOLERANCE | 2 | 2 | FAIL |
| clean | （干净对照） | 0 | 0 | PASS |
| perturb_1e-9 | OVER_TOLERANCE | 6 | 6 | FAIL |
| perturb_1e-6 | OVER_TOLERANCE | 6 | 6 | FAIL |
| perturb_1e-13 | WITHIN_TOLERANCE | 0 | 0 | PASS |
| nudge_4ulp | WITHIN_TOLERANCE | 0 | 0 | PASS |

合计：注入 156 个字段，检出 156 个（检出率 1.0000）；良性字段 2364 个，误报 0 个（误报率 0.0000）；注入 NaN 36 个，检出 36 个（检出率 1.0000）。

### 3.4 端到端时延

从建臂到判据落盘的全部计算耗时 **0.308 s**（建 7 个臂记录、6 次逐字段判决、54 个变异用例、16 个文件审计），门限 300 s，余量 299.7 s。集群排队时间不计入该数字，并在报告中单独披露（见⑤）。

## ④ 结果图

**fig1 band** 图 1　每个臂对的逐字段相对差（对数轴）与声明容差线。实测差异全部落在容差带下方 5 个数量级以上；容差线与噪声底之间的距离即「容差还有多少真容错空间」。

![fig1_band](figures/fig1_band.png)

**fig2 confusion** 图 2　判官混淆矩阵：行为注入类别、列为判决类别（格内为字段计数，颜色按行归一）。对角占优 = 判据 3/4 的直接证据；`any` 行是干净对照与既有变体臂的混合行。

![fig2_confusion](figures/fig2_confusion.png)

**fig3 criteria** 图 3　六项机器判据看板（绿=通过）。数值与门限并列，避免「只看绿不看阈值」。

![fig3_criteria](figures/fig3_criteria.png)

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **物理 x86 臂在本单执行环境内不可得（honest gap）**：工作节点与控制面都是 loongarch64，`192.168.215.56`（x86 主开发机）连接超时，集群两节点亦为 loong64；本机没有 x86 交叉编译器 / qemu / 容器运行时，无法现场产出 x86 记录。因此本节点的 x86 轴用**可移植 IEEE oracle 臂**承担：`+ − × ÷ √ fma` 逐 op 的精确有理数 + 一次 half-to-even 舍入，在语义上等于任何合规 binary64 实现（含 x86-64 SSE2，该项由 IEEE-754 默认舍入强制）；同时把 `.github/workflows/cross-arch-gate-x86-arm.yml` 工作流一并入库，runner 是 x86_64 时一行命令即可产出真机记录并接入同一门。**声明**：x86 轴当前等级 = 语义等价 + 待实测，不是实测。
**集群跨机臂（已递交、报告落盘时仍在排队，如实披露）**：

| 作业 | 臂 | 递交时状态 | 已取回 |
|---|---|---|---|
| cj271 | loong64-n315 | queued (97 ahead at submit) | 否 |
| cj272 | loong64-n330 | queued (98 ahead at submit) | 否 |

取回后并入同一门：`python3 -m tools.cross_arch_gate.cli pipeline --external-arm <label>=<record.json>`——门的判决层是纯函数，不需要重新实现任何东西。

2. **容差带不是越松越好**：`fp64_ulp_cap` 与 `fp64_rel_tol` 必须并联。只有相对容差时，量级 1e-300 的漂移会被判为合格；只有 ULP 时，近零抵消字段会被判为灾难。二者并联仍漏不掉 1e-9 相对扰动（变异族 `perturb_1e-9` 100 % 被拒）。
3. **声明诊断量是设计，不是豁免**：`cancel_diag_*` 两个字段的相对误差是 O(1)（O(1e6) 项抵到 1e-17），若按相对度量判必然假红；它们按 `diag_abs_tol` 判并全程披露字段名（`diagnostic_fields`），与观测量分列统计，不接受「混进总数」的写法。
4. **libm 是最大不确定源**：`transcendental` 族两条代数路径的差异来自实现路线而非 ISA 本身，跨 ISA 的 libm 差异量级通常 ≤1 ULP，但形状随实现而变；因此该族只进容差带，不进逐位判据。
5. **整数域严格性有边界**：整数逐位一致只在「无溢出语义歧义」时成立；换语言/换位宽（如 32 位 `int` 截断）会击穿该判据，门对此的态度是直接判红而不是放宽——本次 12 个整数字段全绿。
6. **时延口径**：300 s 判据只覆盖门自身计算路径；集群臂排队等待是调度器行为，单独披露并排除。

## ⑥ 误差判定

基准 = 参考臂（原生默认构建）。关键量逐项对照：

| 关键量 | 实测 | 门限/基准 | 是否满足（含 <3% 问询） |
|---|---|---|---|
| 整数字段逐位一致率 | 1.0000 | = 1.0 | 是（严格相等，误差 0） |
| 双精度观测量最大相对差（全臂对） | 2.881e-15 | < 1e-10 容差带 | 是（低于 3% 判据 1.0e+15 倍） |
| 双精度容差带内字段占比 | 1.0000 | > 0.95 | 是 |
| 超容差检出率 | 1.0000 | = 1.0 | 是 |
| 误报率 | 0.0000 | = 0.0 | 是 |
| NaN 检出率 | 1.0000 | = 1.0 | 是 |
| 端到端时延 | 0.31 s | < 300 s | 是 |
| 零领域语义审计命中 | 0 | = 0 | 是 |

**「是否 <3%」显式回答**：本节点的关键量是**复现一致性**而不是物理量，其误差基准是容差带本身；实测最大相对差与 3% 的距离为 1.04e+15 倍，即实测差异比 3% 小 1e13 量级以上——**远小于 3%**，且不是靠放宽容差达到：容差带 1e-10 比 3% 严 8 个数量级，注入 1e-9 扰动 100 % 被拒。**无 x86 实测值**，故不给「loong64 vs x86 实测误差」数字，只给其语义上界成立的那一层（IEEE 基本运算逐位）与待实测声明，未粉饰。

### 审计链

- 逐臂记录：`artifacts/cross_arch_gate/arms/*.json`（每份带 digest 与机器溯源）
- 逐对判决：`artifacts/cross_arch_gate/pairs/*.json`（逐字段行，含 `detail` 码）
- 变异自检：`artifacts/cross_arch_gate/mutation_matrix.json`（54 用例）
- 领域审计：`artifacts/cross_arch_gate/domain_audit.json`（16 文件，词表 21+16 条，命中 0）
- 判据裁决：`artifacts/cross_arch_gate/criteria.json`（每项带 value/threshold/source）

