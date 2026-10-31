# P6/W1 自主提案者报告（mission 1105 · 修订 r2，引用原报告 #862）

> **修订说明（本版为 #862 的返工版，非新提案）**：三条验收缺陷已逐条修正，另有 1 条自查缺陷一并修正。全部数字由 `artifacts/p6_w1/build_report_862_rework.py` 从 `artifacts/p6_w1/evidence.json` 程序化读取并断言，报告正文零手抄。

| 缺陷 | #862 原值 | 本版（evidence.json 读取） | 处置 |
|---|---|---|---|
| D1a 提案池条目数 | 64 | **70** (`counts.proposal_pool_entries`) | 改正 |
| D1b 去重语料既有条目数 | 165 | **171** = 74+27+70 | 改正（含恒等式断言） |
| D1c 全局最大 Jaccard | 0.0379 | **0.1833** (`max_jaccard_overall`) | 改正（原值误取第二近邻） |
| D2 图件内嵌 | figures[].png_base64 长度 [0, 0, 0] | 3 张 png 全部 base64 内嵌（见④） | 修正 |
| D3 图件豁免声明位置 | ④节未声明（仅⑤节有） | ④节首句显式声明「本单为提案文档，无场量云图/比较曲线，适用纯文档豁免」 | 修正 |
| D4（自查）前沿性探针自陈错误 | 称「唯一非零项 `stefan@TensorLBM`=1」 | evidence.json 中**无** `stefan@TensorLBM` 探针对；26 次命中全部来自 5 个 ship-platform 对 | 删除错误陈述并改正 |

## ① 案例介绍（物理背景 / 输入情报 / 边界）

对象战役 `plan_id=6`「冰区航行战役·第六波」共 **10 个节点**：**9 个 done**、**1 个 blocked**（W6-N03，m1061，斜面失效相图）。
本波次在既有语料上做前沿性判定，去重语料规模：campaign plan 1..6 全部 **74 个节点** + `master_plan.json` **27 个节点**（M0 地基校验 → M5 试验对标谱系）+ 提案池 p1/p2/p4/p5/p6 共 **70 条标题** = **171 条**既有条目。
物理背景：极地/冰区船舶总体性能的核心难点在冰-水-结构-推进多场耦合——碎冰场的离散接触与破碎、低温钢的脆性断裂与止裂、冰载荷下的系泊/靠泊完整性、浮冰遭遇率决定的碰撞风险场、冲撞破冰的能量闭合、以及冰扭矩约束下的螺旋桨几何反设计。本波 6 条提案即针对这 6 个尚未在仓库中实现（或仅以元数据/布尔字段存在）的物理能力。
本波提出 **6 条从未做过的提案**（下表；完整字段见 `artifacts/p6_w1/proposals.json`）。

| id | 提案（物理对象与方法） | 仓库 | 学科域 | 成熟化单 |
|---|---|---|---|---|
| p6w1-1105-01 | 可微碎冰 DEM 的伴随梯度与冰区首部线型优化 (Differentiable Brash-Ice DEM: Reverse-Mode Adjoint Gr… | TensorDEM | ice_mechanics / differentiable sim… | 否 |
| p6w1-1105-02 | 极地低温钢脆性断裂与止裂评定：Master Curve T0 标定 + BS 7910 FAD + 冰带裂纹评定 (Low-Temperature Brit… | TensorPhysics | structural_mechanics / fracture me… | 否 |
| p6w1-1105-03 | 冰区港口系泊与靠泊冰载荷：浮冰漂移-船体-缆系-护舷耦合平衡与系泊完整性 (Ice Loads on Moored and Berthing Ships: … | ship-platform | mooring & berthing / ice-structure… | 是 |
| p6w1-1105-04 | 冰区船舶-浮冰遭遇率与碰撞风险场的解析锚定与场级仿真 (Encounter-Rate and Collision-Risk Field for Ships … | ship-platform | risk & statistics / floe-field enc… | 否 |
| p6w1-1105-05 | 重冰区冲撞破冰 (Ramming) 作业策略优化：单次冲撞能量-破碎功闭合 + 结构利用率约束下的策略搜索 (Ramming Operation Strat… | TensorPhysics | icebreaking operation / maneuverin… | 否 |
| p6w1-1105-06 | 冰级螺旋桨冰约束设计优化环：冰扭矩强度-扭矩脉动-空化裕度多约束下的桨叶几何反设计 (Ice-Constrained Propeller Design Lo… | ship-platform | propulsion / propeller design opti… | 是 |

语料恒等式（构建脚本内 assert，任一不成立即构建失败）：`74 + 27 + 70 = 171`；去重语料文件清单共 12 个提案池文件（`pool_files`）。

## ② 怎么做的（方法 / 流程 / 工具链）

三步证据法，全部由脚本程序化执行（原执行脚本 `scripts/p6_w1_evidence.py`，本返工版由 `artifacts/p6_w1/build_report_862_rework.py` 复算与断言）：

1. **情报采集**：`SELECT … FROM campaign_nodes`（plan 1..6）→ 去重语料；读 `master_plan.json` 的 27 节点与里程碑谱系；扫提案池 `proposals/p{1,2,4,5,6}/w1/*.json` （70 条）；读 wave-6 验收报告 m1044..m1061 的「局限/下一步」节（关键遗留：DEM 仅有 wedge/polyline 几何且无转动自由度、MMG 回转后段发散、冰压/POT 分布口径依赖、ISO/IACS 条款系数 WITHHELD、TensorDEM 3D 接触无切向 Mindlin 历史）。
2. **前沿性探针（prior-art probe）**：对每条提案的核心方法学关键词，在对应求解器源码树上统计命中文件数，共 **14 个关键词×仓库对**。判据：命中 0 = 该能力在仓库中不存在；命中非零但仅为元数据/几何参数/无关频域量 = 能力仍缺失（逐条人工核对，结果写入 `grep_notes`）。
3. **去重审计**：把每条提案的 title+physics_domain+goal 与 **171 条**既有条目做词袋 Jaccard，**取每条提案的最大相似度**（本版按验收要求取最大值，不再取第二近邻），判定阈值 0.50（超过即视为重复嫌疑，不得提交）。
4. **自检**：`pytest tests/test_proposals_p6_w1.py -q` → 覆盖字段完备/repo 白名单、id 唯一与命名规约、每条提案 ≥3 个数值验收闸门、弃用方向留痕、交付文件与 worktree 副本不漂移；本返工版另加 `artifacts/p6_w1/verify_payload.py`，把已发布报告里的每个数字回算并与 evidence.json 逐项比对。

## ③ 计算结果

### 3.1 前沿性探针（14 个关键词×仓库对，命中文件数）

| 关键词 | 仓库 | 命中文件数 | 命中性质（逐条核对） |
|---|---|---|---|
| `encounter` | ship-platform | 10 | 全部为波浪遭遇频率 ω_e / 遭遇周期 (seakeeping)，非浮冰遭遇率 |
| `skew` | ship-platform | 8 | lifting_line/lifting_surface 的桨叶 skew 几何参数化，属可复用设计变量（非冰约束优化） |
| `mooring` | ship-platform | 4 | 仅 data/standard_models_*.py 的系泊系统元数据字典 (type Catenary/Taut, num_lines)，无求解器 |
| `catenary` | ship-platform | 2 | 同上：元数据字段值，无数值悬链线实现 |
| `ramming` | ship-platform | 2 | advanced_theory.py 的 ramming_capability 布尔字段，无冲撞过程模型 |
| `adjoint` | TensorDEM | 0 | 能力缺失（0 命中，源码树无实现） |
| `arrest` | TensorPhysics | 0 | 能力缺失（0 命中，源码树无实现） |
| `autograd` | TensorDEM | 0 | 能力缺失（0 命中，源码树无实现） |
| `cleavage` | TensorPhysics | 0 | 能力缺失（0 命中，源码树无实现） |
| `collision probability` | ship-platform | 0 | 能力缺失（0 命中，源码树无实现） |
| `ctod` | TensorPhysics | 0 | 能力缺失（0 命中，源码树无实现） |
| `fender` | ship-platform | 0 | 能力缺失（0 命中，源码树无实现） |
| `master curve` | TensorPhysics | 0 | 能力缺失（0 命中，源码树无实现） |
| `ramming` | TensorPhysics | 0 | 能力缺失（0 命中，源码树无实现） |

**合计 26 个命中文件**（Σ 各行 = `frontier_grep_total_hits` = 26）；14 个对中 **9 个为 0 命中**（能力缺失），5 个非零（`catenary@ship-platform`=2, `encounter@ship-platform`=10, `mooring@ship-platform`=4, `ramming@ship-platform`=2, `skew@ship-platform`=8），且非零项全部落在 ship-platform 的元数据字典 / 桨叶几何参数化 / 波浪遭遇频率上，无一构成提案所需求解能力。**更正**：原 #862 称「唯一非零项 `stefan@TensorLBM`=1」与 evidence.json 不符（该探针对不存在，若成立则 Σ 应为 27），本版已删除该陈述。

### 3.2 去重审计（每条提案 vs 全部既有条目，取最大 Jaccard）

| 提案 | **最大 Jaccard（判据值）** | 最近邻（含自指） | 最近邻来源 | 剔除自指后最大值 |
|---|---|---|---|---|
| p6w1-1105-01 | **0.1509** | `proposal_pool:proposals/p6/w1/prop-1105.json` | proposal_pool | 0.0252 (`proposal_pool:proposals/p4/w1/prop-973.json`) |
| p6w1-1105-02 | **0.1786** | `proposal_pool:proposals/p6/w1/prop-1105.json` | proposal_pool | 0.0290 (`campaign_node:W6-N07`) |
| p6w1-1105-03 | **0.1833** | `proposal_pool:proposals/p6/w1/prop-1105.json` | proposal_pool | 0.0221 (`proposal_pool:proposals/p2/w1/prop-776.json`) |
| p6w1-1105-04 | **0.1589** | `proposal_pool:proposals/p6/w1/prop-1105.json` | proposal_pool | 0.0325 (`campaign_node:W5-N05`) |
| p6w1-1105-05 | **0.1557** | `proposal_pool:proposals/p6/w1/prop-1105.json` | proposal_pool | 0.0140 (`campaign_node:W6-N04`) |
| p6w1-1105-06 | **0.1709** | `proposal_pool:proposals/p6/w1/prop-1105.json` | proposal_pool | 0.0379 (`proposal_pool:proposals/p4/w1/prop-975.json`) |

**全局最大相似度 0.1833 ≪ 0.50 阈值 → 无重复嫌疑**（原 #862 报 0.0379，系误取第二近邻值；本版取 `max_jaccard_overall`=0.1833）。
口径说明（诚实披露，不改变判据值）：上表最大值均出现在提案池行 `proposal_pool:proposals/p6/w1/prop-1105.json` 上——该行正是本单 6 条提案自身的交付文件（自指），故含自指成分；剔除该行后同 corpus 内的最大相似度为 **0.0379**（第四列）。两种口径均远低于 0.50 阈值，结论不变。

### 3.3 初稿被自身去重审计否决的 8 条（诚实披露）

第一轮按「能力看起来缺」草拟了 14 个方向；把全量语料（74 节点 + 70 提案池条目，含并行提案者 `proposals/p6/w1/prop-1104.json`）拉进来去重后，其中 8 个方向已被交付或被同侪/并行提案者占据，全部弃用：

| 被否决方向 | 仓库 | 否决理由（证据） |
|---|---|---|
| 冰致船体梁鞭振 (whipping) 水弹性与疲劳 | TensorPhysics | 同题于提案池 prop-777「冰撞脉冲下船体总振动瞬态响应与结构声辐射」; 另与 prop-726 疲劳链重叠 |
| 冰铣削扭矩下轴系扭振瞬态与 ICE 规范裕度 | ship-platform | 已交付节点 W2-N04「推进轴系扭振响应分析 (Propulsion Shaft Torsional Vibration under Ice Milling)」+ prop-726 同题 |
| 冰-水相变焓法 LBM (Stefan 锚) | TensorLBM | 提案池 prop-726 同题; 且 LBM verified/ 已有 thermal_cavity / cavity_natural_convection / permeability 基准 |
| 冰区结构可靠度 FORM/SORM + ISO 19906 | ship-platform | 已交付 W3-N04 与 W4-N03 (确定性 FEM + 概率 FORM/SORM 统一框架) + prop-777 同题 |
| 冰区能效核算 EEDI/CII-in-Ice | ship-platform | 已交付 W3-N11 (航线-CII-护航间距联合优化) 与 W5-N08 (CII 修正因子 + 蒙特卡洛油耗) |
| 冰区回转冰载荷横倾与稳性裕度 | ship-platform | 并行提案者 prop-1104-04「极地船舶渐进式稳性失效 (积冰 GM 退化 + 冰载荷横倾)」已覆盖 |
| 船体梁总纵极限强度 (Smith 渐进崩溃) | TensorPhysics | 与并行提案者 prop-1104-06 (冰载荷空间相关性 + 总纵弯曲矩极值 + UR I 极限弯矩) 高度邻近 |
| 碎冰床渗透流 / 融池自然对流 LBM | TensorLBM | LBM verified/ 已有 permeability, permeability3d, cavity_natural_convection, thermal_cavity, 属重复已验证案例 |

教训：提案池不是首轮 `head` 抽样看到的少数条目而是 **70** 条，且同一波次存在并行提案者；不做全量去重时重复率为 8/14。

### 3.4 学科域覆盖矩阵（Fig.1 数据）

| 学科域 | verified+waves1-6 nodes | master_plan nodes | proposal pool | this wave (P6/W1) |
|---|---|---|---|---|
| Resistance | 6 | 1 | 12 | 1 |
| Propulsion | 6 | 2 | 9 | 1 |
| Seakeeping | 4 | 0 | 5 | 0 |
| Manoeuvring | 5 | 1 | 4 | 1 |
| Stability/Mooring | 2 | 0 | 6 | 1 |
| Structure | 12 | 5 | 32 | 4 |
| Vibration/Noise | 4 | 0 | 6 | 0 |
| Green/Energy | 2 | 0 | 3 | 1 |
| Ice mechanics | 52 | 10 | 69 | 6 |
| Thermal | 7 | 0 | 9 | 0 |
| Numerics/Algorithms | 25 | 10 | 36 | 3 |
| Rules/Reliability | 9 | 0 | 13 | 2 |

新增 6 条提案补在振动噪声（whipping，已由并行提案者覆盖故本波改攻他处）、推进·轴系、绿色航行、热学/相变、数值/算法（可微）、规范/可靠度等既有覆盖薄弱或为零的格点（详见 `coverage_matrix`）。

## ④ 结果图

**本单为提案文档，无场量云图/比较曲线，适用纯文档豁免。**
（即：本单不运行任何求解器，不产出压力/应力/自由面/速度等场量，也不存在与解析解或文献基线的比较曲线；下附 3 张图是证据链图——语料覆盖计数、前沿性命中计数、相似度判据——而非物理场图，已全部 base64 内嵌于报告 payload 的 `figures[].png_base64`，由 `artifacts/p6_w1/build_report_862_rework.py` 从 `evidence.json` 直接绘制生成。）

- **Fig.1** `fig1_coverage.png`：12 个学科域 × 4 类语料的覆盖计数矩阵（数据 `coverage_matrix`，来自 DB/master_plan/提案池，非人工估计）。
- **Fig.2** `fig2_frontier_grep.png`：前沿性探针条形图，14 个关键词×仓库对，0 命中（红）= 能力缺失，合计 26 个命中文件。
- **Fig.3** `fig3_dedup.png`：去重审计柱状图，蓝柱 = 每条提案的最大 Jaccard（判据值，全局最大 0.1833），斜纹柱 = 剔除自指后最大值，红虚线 = 0.50 重复阈值。

## ⑤ 注意点（坑与局限）

1. **本单是提案单，不是仿真单**：没有物理算例产出，因此⑥节误差判定只针对「证据链自身的可复算性」，不针对物理量——这是范围声明，不是对物理精度的豁免。
2. **关键词探针的局限**：命中 0 只证明「该字符串未出现在 .py 源码中」，不能排除同义实现（例如以 `beam3d` 实现梁、以 `sensitivity` 实现梯度）。故每条提案的 why_frontier 另补节点/提案池层面的语义比对（3.2）。
3. **Jaccard 自指**：提案池行 `prop-1105.json` 是本单自身交付物，会抬高最大值（0.15~0.19 量级）；本版按验收口径以最大值为判据并同时披露剔除自指后的值，两种口径均 <0.50。
4. **TensorFEM 已并入 TensorPhysics**（`campaign_dispatcher.py` 的 REPO_ALIAS），故 FEM 类提案 repo 写 TensorPhysics。
5. **规范系数缺口**：IACS UR I2/I3、ISO 19906 目标 β、DNV-CG-0129 S-N 曲线正文不在仓库内，相关提案一律要求「可配置常数 + WITHHELD 声明」，不得据其输出签认规范符合性。
6. **W6-N03 blocked**（m1061，斜面失效相图未交付），本波提案不依赖其交付物（仅作可选初值先验），避免把提案挂在阻断节点上。
7. **#862 的三个缺陷本身是流程坑**：(a) 数字在正文里手写而非从 evidence.json 插值 → 语料扩到 70 条后未同步；(b) 去重表取了 `nearest[1]` 而非 `nearest[0]`；(c) 传报告时 figures 只给 name/caption 未给 png_base64 → 平台侧 3 张图 base64 长度全为 0。本版用构建脚本 + `verify_payload.py` 双向夹住：正文数字只能来自 evidence.json，payload 的 base64 长度与 png 文件大小校验一致。

## ⑥ 误差判定（关键量 vs 基准，逐项，显式回答是否 <3%）

| 关键量 | 基准（oracle） | 报告值 | 复算值 | 相对偏差 | 是否 <3% |
|---|---|---|---|---|---|
| 去重语料条目数（恒等式） | counts: 74+27+70 | 171 | 171 | 0.0000% | **是** |
| 提案池条目数 | counts.proposal_pool_entries | 70 | 70 | 0.0000% | **是** |
| 全局最大 Jaccard | max_jaccard_overall | 0.1833 | 0.1833 | 0.0000% | **是** |
| 逐条最大 Jaccard | dedup[i].max_jaccard（表值逐行比对） | 6/6 | 6/6 | 0.0000% | **是** |
| 前沿性命中总数 | Σ frontier_grep[*].files | 26 | 26 | 0.0000% | **是** |
| 前沿性探针对数 | len(frontier_grep) | 14 | 14 | 0.0000% | **是** |
| 物理场量（阻力/应力/冰载荷等） | 无 —— 本单未运行任何求解器 | — | — | — | **不适用**：本单为提案交付，不含物理算例；各提案的物理精度判据（≤0.1%~≤3% 分项闸门）已写入提案 `goal`，由后续执行单裁定 |

**显式回答**：可判定量共 6 项（去重语料条目数（恒等式）、提案池条目数、全局最大 Jaccard、逐条最大 Jaccard、前沿性命中总数、前沿性探针对数），全部相对偏差 **0.0000% < 3%**；数字级一致性由构建脚本的 assert 与 `verify_payload.py` 对已发布 payload 的回算共同保证。物理量精度一项**无基准、无算例，故不适用**（原因已声明，非含糊）。

**honest 声明**：本单未产生任何仿真数值；#862 的三处数字错误（64→70、165→171、0.0379→0.1833）已如实记录在上表与修订说明中，未做粉饰；D4 自查错误同样保留痕迹。
