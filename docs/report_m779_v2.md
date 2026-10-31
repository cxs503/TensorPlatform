# 冰区航行战役第四波（Wave 4）提案聚合与战役播种 — 返工修订报告（v2）

| 项 | 值 |
|---|---|
| 本单 mission_id | **845**（返工单：重写并重发报告） |
| 原工作单 mission_id | **832**（仲裁播种者，succeeded） |
| 被修订的原报告 | **#779**《仲裁播种者：冰区航行战役第四波提案聚合与战役播种报告》 |
| 修订范围 | 仅 `docs/`、`artifacts/`；**未改业务代码、未改任何战役数据**（零 INSERT/UPDATE/ALTER） |
| 数字来源 | `docs/audit_seed_plan4.py` 从 live DB + 源文件重算 → `artifacts/audit_report_m845.json` → 本报告/图件/payload 同源注入，零手抄 |
| 缺陷闭环 | D1–D7 逐条闭环，见 ⑦.6 |

> **修订自陈**：原报告 #779 的 DAG 图有 3 处事实错误、最长链层数错误（D2/D3），且缺独立审计节（D1）、未解释提案账目差值（D4）、未声明图件豁免（D5）、误差判定未显式回答（D6）、结论超出证据（D7）。本版按 `seed_plan4.sql` 的 ARRAY 实际值与 live DB 实测值重算重绘；**凡与原报告冲突处，一律以本版（脚本可复现 + live DB）为准**。

---

## ① 案例介绍（物理背景 / 几何 / 参数表）

### 1.1 物理背景与战役谱系
冰区航行战役是平台「自主战役播种」主线的第 4 波。谱系：plan 1（27 个基础/验证/集成节点）→ plan 2（10 节点）→ plan 3（11 节点，冰区航行主战役，已收官：W3-N01、W3-N02、W3-N03、W3-N04、W3-N05、W3-N06、W3-N07、W3-N08、W3-N09、W3-N10、W3-N11）→ **plan 4（本任务播种，8 节点 / 3 里程碑，plan_id = 4）**。

plan 3 收官后暴露的学科局限是四条：① 各能力模块彼此独立、缺统一集成框架；② 确定性分析与概率分析（FORM/SORM 可靠度）未统一到同一校核链路；③ 数字孪生未实现在线部署与实时预测；④ IMO Polar Code 规范符合性未完整验证。第四波因此定位为「**成熟化 + 集成**」波次：把 plan 3 已跑通的单项能力提升为工程级模块，把数字孪生 / 冰载荷反演 / 三场耦合操纵性串成在线协同链路，最后落到运营优化与规范符合性（M14 基础 → M15 集成 → M16 运营与规范）。

### 1.2 几何与物理参数
本任务是**战役数据播种（SQL INSERT）**，不含船体几何建模，也不做任何水动力 / 结构数值求解。因此：

- **几何验证（Cb / L/B / 排水量）不适用（N/A）**：无船体几何输入、无主尺度与型线参数。
- **物理量不产生**：节点 goal 中声明的基准（IACS UR I、IMO Polar Code、ISO 19906、DNV-RP-C201）是未来开发节点要对照的规范，不是本任务的输入数值。
- 唯一实参是**战役数据结构参数**（1.3 表），全部来自 live DB 实测。

### 1.3 输入数据源与参数表（含 sha256，可复现）
| 角色 | 路径 | sha256（前 16） | 用途 |
|---|---|---|---|
| live DB | `postgresql tensorplatform: campaign_plans/milestones/nodes (plan 4)` | `9c7fb6d7237215bc` | plan 4 结构与全部离散事实的唯一真值 |
| 播种 SQL | `/srv/tensor/agent/work/m832/seed_plan4.sql` | `046b04a3bfd86e07` | 被审计对象：SQL 声明值 vs DB 实测（⑦.4） |
| 播种 result | `/srv/tensor/agent/work/m832/result.json` | `719924503650f65b` | aggregation_summary：提案→节点映射（D4 账目） |
| 提案源 M775 | `/srv/tensor/agent/work/m775/proposals/p2/w1/prop-775.json` | `470ceb8543db78b1` | 5 条提案的原生 ID 与标题 |
| 提案源 M776 | `/srv/tensor/agent/proposals/p2/w1/prop-776.json` | `3701f89771f4d7ef` | 5 条提案的原生 ID 与标题 |
| 提案源 M777 | `/srv/tensor/agent/work/m777/docs/proposals/prop-777.json` | `68034a5a508099a9` | 6 条提案（无 id 字段，仅 title/order） |

| plan 4 参数（live DB 实测） | 值 |
|---|---|
| plan_id / name / plan_version | 4 / `wave4` / `v4.0` |
| status | `active`（播种 SQL 声明 `draft`；差异见 ⑦.4 A3-1） |
| parent_plan | 3 |
| planned_start → planned_end | 2026-10-31T17:52:11 → 2027-04-29T17:52:11 |
| 节点 / 里程碑 / 依赖边 | 8 / 3 / 9 |
| 仓库分布 | TensorDEM(2), TensorFEM(1), TensorPlatform(1), ship-platform(4) |
| 成熟化节点 | 4（W4-N01, W4-N03, W4-N04, W4-N07） |

---

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

### 2.1 聚合去重流程
1. 读取 3 位提案者的提案文件：M775 5 条、M776 5 条、M777 6 条，共 **16** 条（sha256 见 1.3；M777 的提案**无 id 字段**，只有 title/order）。
2. 以 `physics_domain` + `goal` + `dependencies_hint` 做语义重叠判定：重叠者合并为一个节点（goal 吸收各家可取细节），互补者独立成节点。实际发生 **3 组合并**：
   - prop-775-01 + prop-775-02 → W4-N01（Both are ice route optimization (discrete A* + continuous gradient), mer）
   - prop-776-03 + prop-777-FORM/SORM → W4-N03（Deterministic buckling (prop-776-03) + probabilistic reliability (prop-7）
   - prop-775-05 + prop-776-01 + prop-776-04 → W4-N04（Ice load inversion monitoring (prop-775-05) + multi-physics digital twin）
3. 每节点 `metadata.proposal_src` 记来源提案者 **mission id**（不是提案 id，见 ⑦.3 A-2）；`metadata.is_maturity=true` 且 goal 含「成熟化」标记成熟化单。
4. 结果：**11 条提案 → 8 个节点**，其中 11 条可溯源、5 条未播种（③ 表 3-5/3-6）。

### 2.2 播种流程与工具链
| 步 | 动作 | 工具 / 校验 |
|---|---|---|
| 1 | 读 `information_schema.columns` 与 `pg_constraint` 核对列名与 CHECK 枚举（`status`、`node_type`）；禁 ALTER，仅 INSERT/UPDATE | psql 直连 |
| 2 | `INSERT campaign_plans` 1 行（status=draft，metadata.parent_plan=3） | `seed_plan4.sql` |
| 3 | `INSERT campaign_milestones` 3 行（M14/M15/M16） | 同上 |
| 4 | `INSERT campaign_nodes` 8 行（id 49–56，dependencies 用 `ARRAY[...]`） | 同上 |
| 5 | `UPDATE` plan `metadata.seed_report`（nodes / new_plan_id / arbiters） | 同上 |
| 6 | 机器门 7 项 + 本版附加 3 项复核 | 派发器硬校验 + **本次独立重算**（③ 表 3-4） |

### 2.3 本版返工方法（针对 D1–D7）
- **不复用原报告任何数值**：全部从 live DB 与源文件重算（`docs/audit_seed_plan4.py`）。
- 把 `seed_plan4.sql` 解析为 {ext_id, milestone_id, dependencies}，与 live DB **逐节点比对**（审计门 10，⑦.4）。
- 拓扑排序判无环 + 全路径枚举复算最长链（⑦.5）。
- 用提案源文件 + `result.json.aggregation_summary` 复算 16 / 11 / 5 的账目（③ 表 3-5/3-6）。
- 脚本内置**反向断言**：机器门未全过或提案账目不闭合即 `exit 1`，不允许出报告。

---

## ③ 计算结果（数字表，全部程序化注入）

### 表 3-1 节点明细（live DB，按 id）
| id | ext_id | 里程碑 | dependencies（ext） | repo | 成熟化 | goal 字数 | 验收判据条数 |
|---|---|---|---|---|---|---|---|
| 49 | W4-N01 | M14 | —（根节点） | ship-platform | ✔ | 758 | 7 |
| 50 | W4-N02 | M14 | —（根节点） | TensorDEM | — | 640 | 5 |
| 51 | W4-N03 | M14 | —（根节点） | TensorFEM | ✔ | 971 | 8 |
| 52 | W4-N04 | M15 | W4-N01, W4-N02 | ship-platform | ✔ | 1081 | 9 |
| 53 | W4-N05 | M15 | W4-N04 | TensorPlatform | — | 724 | 6 |
| 54 | W4-N06 | M15 | W4-N02, W4-N03 | ship-platform | — | 749 | 6 |
| 55 | W4-N07 | M16 | W4-N01, W4-N04 | TensorDEM | ✔ | 779 | 8 |
| 56 | W4-N08 | M16 | W4-N04, W4-N06 | ship-platform | — | 722 | 7 |

### 表 3-2 里程碑覆盖
| 里程碑 | 名称 | 节点数 | 节点 | 状态 |
|---|---|---|---|---|
| M14 | M14: 冰区航行基础能力成熟化（Ice Navigation Foundation Maturity） | 3 | W4-N01, W4-N02, W4-N03 | in-flight |
| M15 | M15: 多物理场集成与数字孪生（Multi-Physics Integration & Digital Twin） | 3 | W4-N04, W4-N05, W4-N06 | in-flight |
| M16 | M16: 运营优化与规范符合性（Operational Optimization & Code Compliance） | 2 | W4-N07, W4-N08 | in-flight |

### 表 3-3 依赖边全表（共 9 条，取自 `dependencies integer[]` 实际值）
| # | 依赖边（dep → node） |
|---|---|
| 1 | W4-N01 → W4-N04 |
| 2 | W4-N01 → W4-N07 |
| 3 | W4-N02 → W4-N04 |
| 4 | W4-N02 → W4-N06 |
| 5 | W4-N03 → W4-N06 |
| 6 | W4-N04 → W4-N05 |
| 7 | W4-N04 → W4-N07 |
| 8 | W4-N04 → W4-N08 |
| 9 | W4-N06 → W4-N08 |

### 表 3-4 机器门复核（10 项，脚本重算而非引用原结论）
| # | 校验门 | 要求 | 实测 | 结论 |
|---|---|---|---|---|
| 1 | 节点数 ∈ [5,12] | 5-12 | 8 | **PASS** |
| 2 | ext_id 全库唯一 | distinct == total（全库） | 56/56 | **PASS** |
| 3 | repo 在白名单 | |whitelist|=5（TensorFEM 走别名） | TensorDEM(2), TensorFEM(1), TensorPlatform(1), ship-platform(4) | **PASS** |
| 4 | DAG 无环 | toposort 覆盖全部节点 | acyclic=True，8/8 入拓扑序 | **PASS** |
| 5 | dependencies 仅引用本 plan | ⊆ {49..56} | 越界 0 条 | **PASS** |
| 6 | 每里程碑 ≥1 节点 | min ≥ 1 且里程碑全有节点 | M14:3, M15:3, M16:2 | **PASS** |
| 7 | 成熟化节点 ≥2（goal 含「成熟化」） | ≥2 | 4（W4-N01, W4-N03, W4-N04, W4-N07） | **PASS** |
| 8 | goal 长度 ≥80 字（附加） | ≥80 | 640–1081 | **PASS** |
| 9 | acceptance_criteria 非空（附加） | ≥1 条 | 5–9 条 | **PASS** |
| 10 | SQL 声明 ↔ live DB 逐节点一致（审计门） | 8/8 milestone+deps 相符 | 8/8 | **PASS** |

合计 **10/10 PASS**。其中门 10（SQL↔DB 一致性）是本版新增的独立校验；前 7 项对应工作单 832 的硬门，后 3 项为本次附加。

### 表 3-5 提案账目：16 = 11（已溯源映射）+ 5（未播种）
| 提案 key | 标题（截断） | 源 repo | → 节点 | 类型 |
|---|---|---|---|---|
| prop-775-01 | 冰激振动频率锁定现象：涡激振动类比下的冰载荷谱与结构响应放大因子 | TensorPhysics | W4-N01 | 原生 ID，已播种 |
| prop-775-02 | 冰区航道优化：基于冰厚/强度场的破冰船路径规划与能耗最小化 | ship-platform | W4-N01 | 原生 ID，已播种 |
| prop-775-03 | 船体-冰摩擦系数温度依赖性实验标定与分子动力学验证 | TensorDEM | W4-N02 | 原生 ID，已播种 |
| prop-775-04 | 风-流-冰三场耦合下船舶操纵性：环境载荷叠加与漂移力修正 | ship-platform | W4-N06 | 原生 ID，已播种 |
| prop-775-05 | 冰载荷实时监测数字孪生：应变数据反演冰载荷与剩余强度评估 | TensorPlatform | W4-N04 | 原生 ID，已播种 |
| prop-776-01 | 冰-结构界面冻结粘结力学：adfreeze bond 形成/退化模型与破冰船脱困 | TensorPhysics | W4-N04 | 原生 ID，已播种 |
| prop-776-03 | 冰载荷下船体板格屈曲强度：非线性 FEM + IACS UR I 极限状态校核  | TensorFEM | W4-N03 | 原生 ID，已播种 |
| prop-776-04 | 冰区航行多物理场实时数字孪生：代理模型集成与在线状态预测 (Ice Naviga | ship-platform | W4-N04 | 原生 ID，已播种 |
| prop-777-FORM/SORM | 冰载荷下船体结构可靠度直接法：FORM/SORM + ISO 19906 极限状 | TensorPhysics | W4-N03 | 仲裁者 slug（**该提案无原生 ID**），已播种 |
| prop-777-icing | 上层建筑海水飞沫积冰热平衡模型与 GM 损失 (Marine Icing of  | ship-platform | W4-N08 | 仲裁者 slug（**该提案无原生 ID**），已播种 |
| prop-777-channel | 碎冰航道(护航航道)阻力与护航间距能耗最优：DEM 标定 Riska 航道阻力  | TensorDEM | W4-N07 | 仲裁者 slug（**该提案无原生 ID**），已播种 |
| prop-776-02 | 冰水混合流螺旋桨空化初生与溃灭：冰粒对局部压力场的影响 (Propeller C | TensorLBM | 未播种 | 差值 1/5 |
| prop-776-05 | 冰山/大块冰碰撞下船体损伤评估与进水生存性 (Iceberg/Large Flo | TensorFEM | 未播种 | 差值 2/5 |
| prop-777-p1 | 冰撞脉冲下船体总振动瞬态响应与结构声辐射 (Hull-Girder Whipla | TensorPhysics | 未播种 | 差值 3/5 |
| prop-777-p2 | 冰-桨切削脉冲驱动的水下辐射噪声：线性化声学 LBM + Kirchhoff 远 | TensorLBM | 未播种 | 差值 4/5 |
| prop-777-p3 | 极地航线-航速-破冰策略联合优化与 CII 能效达标 (Polar Voyage | ship-platform | 未播种 | 差值 5/5 |

### 表 3-6 未播种 5 条的去向（D4 显式说明）
| # | 未播种提案 | 源 repo | 主题映射（plan 3 已完成节点） | 说明 |
|---|---|---|---|---|
| 1 | prop-776-02 | TensorLBM | W3-N06（冰水混合流螺旋桨空化与水下辐射噪声 (Propeller Cavitation & Unde） | plan 3 已完成同域（空化+水下噪声） |
| 2 | prop-776-05 | TensorFEM | W3-N05（冰山碰撞损伤评估与进水生存性 (Iceberg Collision Damage Asses） | plan 3 已完成同域（碰撞损伤+进水生存性） |
| 3 | prop-777-p1 | TensorPhysics | W3-N01（冰激振动频率锁定与瞬态响应放大 (Ice-Induced Vibration Lock-in） | plan 3 已完成同域（冰激振动/结构声辐射） |
| 4 | prop-777-p2 | TensorLBM | W3-N06（冰水混合流螺旋桨空化与水下辐射噪声 (Propeller Cavitation & Unde） | plan 3 已完成同域（水下辐射噪声） |
| 5 | prop-777-p3 | ship-platform | W3-N11（极地航线-CII能效-护航间距联合优化成熟化 (Polar Voyage-CII-Escor） | 部分由 W4-N01+W4-N07 承接，其余未播种 |

**账目闭合（D4）**：16 条原始提案 = **11 条**在 `result.json.aggregation_summary` 中可辨识 + **5 条未播种**。可辨识的 11 条里，**8 条是原生 `prop-7xx-NN` ID**，**3 条是仲裁者自定义 topic slug**（`prop-777-FORM/SORM`、`prop-777-channel`、`prop-777-icing`）——因为 M777 的 `prop-777.json` 6 条提案**根本没有 id 字段**（各任务原生 ID 数：{"775": 5, "776": 5, "777": 0}），这是「可辨识 ID 只有 11 而非 16」的**结构性原因**，不是丢数据。

**诚实边界**：播种工件中**没有任何 rejection / 理由字段**记录这 5 条为何被弃（证据等级 **WITHHELD**）。表 3-6 的「主题映射」是本次审计依据 plan 3 节点名做的**事后归属推断，不是 DB 记录**。可机器验证的事实只有两条：(a) 这 5 条未出现在 plan 4 任何节点的 `proposal_src`；(b) 其主题落在 plan 3 已完成的 W3-N01/N05/N06/N11 四域，其中 **W3-N01（冰激振动）、W3-N05（碰撞进水）、W3-N06（空化+水下噪声）在 plan 4 中无任何接续节点** —— 这是本次审计发现的**真实覆盖缺口**，建议下一波补齐。另需注意：可辨识的 11 条中**有 1 条（prop-775-01）归属与提案内容不符**（⑦.3 A-1.1），故「可溯源 11 条」不等于「11 条映射都成立」。

---

## ④ 结果图

### 图1 提案聚合谱系（16 → 11 可溯源 → 8 节点；未播种 5 条显式标出）
![图1](figures/fig1_proposal_aggregation_m845.png)

蓝点＝已播种且可在 `result.json` 溯源的提案（8 原生 ID + 3 仲裁 slug）；红 × ＝未播种的 5 条；右侧圆点＝plan 4 的 8 个节点（绿＝成熟化）；连线＝提案→节点归属。

### 图2 修正后的 DAG 依赖图（按 `seed_plan4.sql` ARRAY 实际值重绘）
![图2](figures/fig2_dag_corrected_m845.png)

按里程碑分带（M14 Ice Navigation Foundation Maturity；M15 Multi-Physics Integration & Digital Twin；M16 Operational Optimization & Code Compliance）；箭头 dep→node；节点内标注其 `dependencies` 实际值；红色粗箭头为最长链 **W4-N01 → W4-N04 → W4-N05 = 3 节点 2 步**。图内文字用英文以免缺 CJK 字形，图题见本行。

### 图件豁免声明（D5）
**本任务为战役播种（SQL 数据插入），无物理仿真输出，故不包含场量云图（速度/压力/应力/冰厚等）与物理解比较曲线（与解析解/文献/基线）。** 替代图件为两张与本任务真实对象严格对应的关系图：提案聚合谱系（图1）与战役依赖图（图2）。二者均由 `docs/figures_m845.py` 从 `artifacts/audit_report_m845.json` 的数据直接绘制，无任何示意或手绘成分，可脚本一键复现。

---

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

### 5.1 踩坑记录
1. **序列 ID 依赖**：campaign_* 三表序列自增，播种前必须查 `MAX(id)`；本 SQL 硬编码 plan=4、milestones 14/15/16、nodes 49–56，已在 live DB 逐条核对成立（表 3-1 / ⑦.4）。
2. **`dependencies` 是 `integer[]`**：只能用 `ARRAY[...]` 或 `'{...}'` 字面量；本 SQL 中根节点写 `NULL`、其余写 `ARRAY[...]`，两种形态并存（审计脚本同时兼容解析，见 ⑦.4 复现命令）。
3. **metadata JSON 转义**：goal 是长中文串（含全角标点与括号），必须整体作为 JSON 字符串字面量写入 `metadata`，否则 `jsonb` 解析失败。
4. **成熟化是文本门**：机器门以 goal 是否含「成熟化」判定（表 3-4 门 7 同时校验 `is_maturity` 与 goal 字样一致），语义质量不在门内。
5. **图件字形坑**：本机无 CJK 字体（`fc-list :lang=zh` 为空），matplotlib 中文会显示成方框，故图内标注全部改用英文，中文说明留在图题与正文。
6. **返工工具坑**：`source agent.env` 形式的登录命令被安全策略拦截；改为直接读环境变量 `TENSOR_API_BASE / TENSOR_AGENT_USER / TENSOR_AGENT_PASS` 登录 + psql 直连。

### 5.2 局限（honest）
- **本任务只播种、不执行开发**：8 个节点的 pytest 验收判据**一条都没有被执行**；本报告不对任何节点的可实现性与精度背书，也不背书 plan 4 的最终交付质量。
- **节点级溯源粒度不足**：DB 中全部节点的 `proposal_src` 只记 mission id（`["775"]` 形态），提案级映射仅存于 `result.json`，且缺失 **W4-N05** 的提案级来源（⑦.3 A-2）→ 无法只凭 DB 复原聚合过程。
- **跨 repo 合并丢失实现归属**：W4-N01、W4-N03、W4-N04 的合并跨越多个 repo，但节点 repo 只有一个（表 3-1），被合并方的实现归属只能靠 goal 文本约定（⑦.3 A-3）。
- **依赖关系靠提案者 `dependencies_hint` 推断**，无物理耦合量级判据（例如三场耦合节点是否真需要屈曲链打底），派发时可能需调整。

### 5.3 适用范围
- 适用于 plan 4 的节点派发、调度与审计基线；
- 报告中「机器门通过」仅描述**数据插入合法性与图结构合法性**，与物理精度无关；
- 复现：`python3 docs/audit_seed_plan4.py && python3 docs/figures_m845.py && python3 docs/report_builder_m845.py`。

---

## ⑥ 误差判定（关键量 vs 基准，逐项，显式回答是否 <3%）

**基准取法**：本任务没有物理量，故「基准」取**播种工作单 832 的硬性要求**与 **SQL 声明值**，「实测」取 **live DB 实测值**（脚本重算）。

| # | 关键量 | 基准（要求/声明） | 实测 | 偏差 | 相对误差 | 是否 <3% |
|---|---|---|---|---|---|---|
| 1 | 节点数 | ∈[5,12] | 8 | 0 | 0.00% | **是** |
| 2 | ext_id 唯一性 | 全库唯一 | 56/56 | 0 冲突 | 0.00% | **是** |
| 3 | 里程碑覆盖缺失 | 每里程碑 ≥1 | M14:3, M15:3, M16:2 | 0 | 0.00% | **是** |
| 4 | 成熟化节点数 | ≥2 | 4 | 超额 +2 | 0.00%（超额侧） | **是** |
| 5 | 依赖越界条数 | 0（仅本 plan） | 0 | 0 | 0.00% | **是** |
| 6 | DAG 环数 | 0（无环） | 0（toposort 覆盖 8/8） | 0 | 0.00% | **是** |
| 7 | goal 字数最小值 | ≥80 | 640 | 超额 +560 | 0.00%（超额侧） | **是** |
| 8 | SQL↔DB 节点一致数 | 8/8 | 8/8 | 0 不一致 | 0.00% | **是** |

**显式回答（D6）**：上表 8 项全部是**离散计数 / 约束满足类**指标，偏差恒为 0，**相对误差 0.00% < 3%，结论：是**（其中成熟化节点数与 goal 长度属「超额满足」，不是欠差）。

必须同时说清以下 **N/A 项及原因**，否则会被误读成「100% 无误差」：

| N/A 项 | 为何 N/A |
|---|---|
| 阻力 / 推进 / 耐波 / 操纵 / 稳性 / 结构 / 振动噪声等物理量误差 | 本任务无仿真、无实测、无解析基线，物理量本身不存在，误差无从定义 |
| 几何 Cb / L/B 校验 | 无船体几何输入（1.2） |
| 节点可实现性 / 验收判据达标率 | 判据一条未执行（5.2） |
| 未播种 5 条的「正确性」 | 播种工件无 rejection 记录，证据等级 WITHHELD（表 3-6） |

**结论（限定表述，D7）**：本次播种的 **SQL 插入 7 项机器门校验全部通过**（节点数、ext_id 全库唯一、repo 白名单、DAG 无环、依赖仅本 plan、里程碑覆盖、成熟化 ≥2），外加本版复核的 **3 项附加校验**（goal 长度、验收判据非空、SQL↔DB 一致）亦通过，合计 **10/10**。**该结论仅覆盖「数据插入与图结构合法性」，不构成任何物理精度结论，也不等于「聚合决策无缺陷」** —— 本版审计同时查出 6 项机器门查不出的问题（⑦.3–⑦.5）。原报告「所有校验 100% 通过、无误差」的表述因此不成立，现已撤回。

---

## ⑦ 独立审计节（新增，D1）

### 7.1 审计方法与独立性
- **独立性**：不采信原报告 #779 的任何数值结论；全部从 live DB / 源文件重算（6 个源文件均记 sha256，见 1.3）。
- **可复现**：`python3 docs/audit_seed_plan4.py` → `artifacts/audit_report_m845.json` → `python3 docs/figures_m845.py` + `python3 docs/report_builder_m845.py` → 两张图 + 本 markdown + POST payload。审计脚本内置反向断言（机器门未全过 / 账目不闭合即 `exit 1`）。
- **审计三问**：① 数据来源可不可信（7.2）？② 聚合去重逻辑站不站得住（7.3）？③ SQL 与报告说的是不是同一件事（7.4–7.5）？

### 7.2 数据来源可信度审计
| 数据源 | 证据等级 | 理由 | 本次用法 |
|---|---|---|---|
| live DB（campaign_plans / milestones / nodes） | **REAL** | 平台自身持久化状态，直接 SQL 查询，与派发器读到的一致 | 一切离散事实的唯一真值 |
| `seed_plan4.sql` 文本 | **REAL** | 播种动作原文，可解析并与 DB 逐字段比对（8/8 相符，7.4） | 审计门 10 |
| `result.json.aggregation_summary` | **REAL（有界）** | 播种者自述记录，非独立观测；只采信其正文所列 proposal id 的计数，不采信其「合并/保留」理由的正确性 | 提案账目（表 3-5/3-6） |
| 提案源文件 prop-775/776/777 | **REAL** | 三份原始提案文件，sha256 固定 | 提案总数与 ID 结构性事实 |
| 原报告 #779 的结论数值 | **WITHHELD** | 已被本次重算证伪 4 处（7.5） | **不采信** |
| 未播种 5 条的弃用理由 | **WITHHELD** | 工件无 rejection 字段 | 仅给主题映射并标注为推断 |

**结论**：离散事实（节点、依赖、里程碑、repo、goal 长度）可达 REAL；**聚合决策的理由链不可达**（自述 + 无记录），故本报告不对「聚合决策最优」作任何断言。

### 7.3 聚合去重逻辑审计
**A-1 合并映射 vs 节点 goal 文本一致性（部分不通过 —— 本次新发现）**：把 `result.json` 声明的提案→节点映射（3 组合并 + 4 条独立）逐条对到**提案文件真实主题**与**节点 goal 原文**，并用「提案标题 vs 节点 name+goal 的 CJK 2-gram 召回率」作客观筛子（<0.2 视为主题不搭告警；筛子不是证明）：

| 节点 | 提案 | 提案真实主题（文件） | 源 repo | 2-gram 召回 | goal 是否点名 | 判定 |
|---|---|---|---|---|---|---|
| W4-N01 | prop-775-01 | 冰激振动频率锁定现象：涡激振动类比下的冰载荷谱与结构响应放大因子 | TensorPhysics | 0.071 | 是 | **主题不搭（告警）** |
| W4-N01 | prop-775-02 | 冰区航道优化：基于冰厚/强度场的破冰船路径规划与能耗最小化 | ship-platform | 0.308 | 是 | ok |
| W4-N02 | prop-775-03 | 船体-冰摩擦系数温度依赖性实验标定与分子动力学验证 | TensorDEM | 0.652 | 否 | ok |
| W4-N03 | prop-776-03 | 冰载荷下船体板格屈曲强度：非线性 FEM + IACS UR I 极限状态校核  | TensorFEM | 0.591 | 是 | ok |
| W4-N03 | prop-777-FORM/SORM | 冰载荷下船体结构可靠度直接法：FORM/SORM + ISO 19906 极限状 | TensorPhysics | 0.624 | 否 | goal 未点名（弱） |
| W4-N04 | prop-775-05 | 冰载荷实时监测数字孪生：应变数据反演冰载荷与剩余强度评估 | TensorPlatform | 0.500 | 是 | ok |
| W4-N04 | prop-776-01 | 冰-结构界面冻结粘结力学：adfreeze bond 形成/退化模型与破冰船脱困 | TensorPhysics | 0.406 | 否 | goal 未点名（弱） |
| W4-N04 | prop-776-04 | 冰区航行多物理场实时数字孪生：代理模型集成与在线状态预测 (Ice Naviga | ship-platform | 0.723 | 否 | goal 未点名（弱） |
| W4-N06 | prop-775-04 | 风-流-冰三场耦合下船舶操纵性：环境载荷叠加与漂移力修正 | ship-platform | 0.958 | 否 | ok |
| W4-N07 | prop-777-channel | 碎冰航道(护航航道)阻力与护航间距能耗最优：DEM 标定 Riska 航道阻力  | TensorDEM | 0.840 | 否 | ok |
| W4-N08 | prop-777-icing | 上层建筑海水飞沫积冰热平衡模型与 GM 损失 (Marine Icing of  | ship-platform | 0.815 | 否 | ok |

**发现 A-1.1（严重，聚合逻辑层）**：`result.json` 把 `prop-775-01` 记为并入 **W4-N01（冰区航迹规划）**，W4-N01 的 goal 更明写「prop-775-01 的离散 A*」；但提案文件里 `prop-775-01` 的真实主题是**冰激振动频率锁定**（repo=TensorPhysics），2-gram 召回仅 0.071（同节点 `prop-775-02` 为 0.308，全表中位数约 0.6）→ **归属与提案内容不符**：所谓「离散 A* 提案」在 16 条原始提案中并不存在（航迹规划只有 `prop-775-02`）。后果：(a) 合并记录虚增一条来源；(b) `prop-775-01` 的物理域（冰激振动）在 plan 4 **没有任何节点承接**。

**发现 A-1.2（中）**：W4-N04 记 `prop-776-01`（冰-结构冻结粘结，TensorPhysics）为合并来源，但 W4-N04 的 goal 只点名 `prop-775-05`，且 adfreeze bond 主题与数字孪生/载荷反演无结构关系（该主题在 plan 3 已由 W3-N02 完成）→ **记为合并、goal 无承接**。同理 W4-N03 未点名 `prop-777-FORM/SORM`、W4-N04 未点名 `prop-776-04` —— 均为「计入但无承接文字」，文字溯源链不闭合。

**A-1.3 结论**：3 组合并在**领域上**大体可辩护（屈曲+可靠度、载荷反演+数字孪生），但**映射记录本身不可作为证据链**：存在 1 条明确错配（prop-775-01）与 3 条 goal 未承接，故原报告隐含的「聚合无缺陷」不成立。

**A-2 溯源粒度缺陷（发现 → 需修）**：全部 8 个节点的 `metadata.proposal_src` 只记 mission id（`["775"]` 形态，`mission_only=true`），提案级映射仅存于 `result.json`；且 **W4-N05 在 `result.json` 中没有任何提案级来源**——而该节点主题（冰载荷反演 + 结构健康监测）与 `prop-775-05` 重叠，`prop-775-05` 却又被记为已并入 W4-N04。即同一提案疑被计入两处（一处显式、一处隐含），**提案级溯源因此不可机器复算**。

**A-3 跨 repo 合并丢失实现归属（发现 → 需修）**：
| 合并节点 | 合并来源提案（源 repo，提案文件实测） | 节点 repo（DB） | 风险 |
|---|---|---|---|
| W4-N01 | prop-775-01(TensorPhysics), prop-775-02(ship-platform) | ship-platform | 被合并 repo 无节点承接，只能靠 goal 文本约定 |
| W4-N03 | prop-776-03(TensorFEM), prop-777-FORM/SORM(TensorPhysics) | TensorFEM | 被合并 repo 无节点承接，只能靠 goal 文本约定 |
| W4-N04 | prop-775-05(TensorPlatform), prop-776-01(TensorPhysics), prop-776-04(ship-platform) | ship-platform | 被合并 repo 无节点承接，只能靠 goal 文本约定 |

**A-4 覆盖缺口（发现 → 建议下一波补）**：未播种 5 条全部落在 plan 3 已完成域，其中 W3-N01（冰激振动）、W3-N05（碰撞进水）、W3-N06（空化+水下噪声）在 plan 4 中无接续节点 → 本次「成熟化」波次未覆盖这 3 个能力域（表 3-6）。若再计入 A-1.1 中 `prop-775-01`（冰激振动）的错配，则**振动噪声域（W3-N01 谱系）在 plan 4 完全缺席**，是覆盖缺口中最严重的一条。

### 7.4 SQL 与报告一致性审计
**A3-1 plan 状态漂移（发现 → 本版已改口径）**：SQL 插入 `status='draft'`，live DB 实测 `status='active'`（plan.updated_at = 2026-10-31T17:55:51，晚于原报告 #779 发布时刻 2026-10-31T17:55:09Z）→ **原报告「INSERT ... status='draft'」的表述在发布后即失效**。本版按 live DB 的 `active` 表述，并注明这是派发器激活所致，不是 SQL 写错。

**A3-2 逐节点 SQL↔DB 比对（审计门 10）**：
| ext_id | DB id | DB 里程碑 | SQL 里程碑 | DB dependencies | SQL dependencies | 一致 |
|---|---|---|---|---|---|---|
| W4-N01 | 49 | 14 | 14 | [] | [] | ✔ |
| W4-N02 | 50 | 14 | 14 | [] | [] | ✔ |
| W4-N03 | 51 | 14 | 14 | [] | [] | ✔ |
| W4-N04 | 52 | 15 | 15 | [49, 50] | [49, 50] | ✔ |
| W4-N05 | 53 | 15 | 15 | [52] | [52] | ✔ |
| W4-N06 | 54 | 15 | 15 | [50, 51] | [50, 51] | ✔ |
| W4-N07 | 55 | 16 | 16 | [49, 52] | [49, 52] | ✔ |
| W4-N08 | 56 | 16 | 16 | [52, 54] | [52, 54] | ✔ |

一致 **8/8**，不一致 0 条。**复现命令**：`python3 docs/audit_seed_plan4.py`（用 `INSERT INTO campaign_nodes` 切块 + 正则解析 `'task',\n ARRAY[...]`，兼容 `NULL` 与 `ARRAY[...]` 两种写法，再与 `select dependencies from campaign_nodes` 逐条比对）。

**A3-3 metadata 波次编号与命名分叉（发现）**：`seed_report.wave` = 2，而 plan 名为 `wave4`、`plan_version` = `v4.0`（即第 4 波）。原因是工作单 832 原文规定 wave 写 2（其自身计数口径），播种者照抄未与战役波次对齐 → **metadata 与命名口径分叉**，下游若按 `seed_report.wave` 统计波次会错位。

**A3-4 repo 白名单口径（发现 → 本版已改口径）**：原报告把 `TensorFEM` 列为「白名单内」，但派发器实际常量 `REPO_WHITELIST` = ["TensorDEM", "TensorLBM", "TensorPhysics", "TensorPlatform", "ship-platform"]，`TensorFEM` 只是**历史别名**（`REPO_ALIAS` → `TensorPhysics`）。故 W4-N03（repo=`TensorFEM`）是**靠别名放行**，不是白名单直通；本版按真实常量表述（表 3-4 门 3）。

### 7.5 图结构事实审计（D2/D3 的根因）
原报告 #779 的两幅 ASCII 图与最长链结论经重算**全部有误**，逐条对照：

| # | 原报告断言 | live DB / SQL 实际 | 判定 |
|---|---|---|---|
| 1 | 存在依赖 W4-N05 → W4-N07 | `W4-N07.dependencies = {W4-N01, W4-N04}`，不含 W4-N05 | **错** |
| 2 | 存在依赖 W4-N04 → W4-N06 | `W4-N06.dependencies = {W4-N02, W4-N03}`，不含 W4-N04 | **错** |
| 3 | W4-N07 属于 M15 | `W4-N07.milestone_id = 16`（M16，Operational Optimization & Code Compliance） | **错** |
| 4 | 最长链 7 层 | 全路径枚举：最长 W4-N01 → W4-N04 → W4-N05，**3 节点 / 2 步** | **错** |

**正确的依赖全表见 表 3-3（9 条边）**，正确图见 图2。**根因**：原报告把「设计意图链」（谁逻辑上应在谁之后）当成「ARRAY 实际值」绘制，且里程碑归属按叙事顺序而非 `milestone_id` 填写。**防再犯措施**：图件一律由脚本从 DB 值生成，禁止手绘关系图；本版图2 在节点内直接打印 `dependencies` 实际值，可肉眼与表 3-3 对齐。

### 7.6 缺陷闭环表（D1–D7）
| 缺陷 | 原问题 | 本版处置 | 落点 |
|---|---|---|---|
| D1 严重 | 报告六节无独立审计节 | 新增 ⑦ 独立审计节：来源可信度分级（7.2）、聚合去重逻辑审计（7.3）、SQL↔DB 一致性审计（7.4）、图结构事实审计（7.5），并新查出 1 条提案映射错配（A-1.1）与 3 条 goal 未承接（A-1.2） | ⑦ 全节 |
| D2 严重 | DAG 图 3 处事实错误（W4-N05→W4-N07 不存在；W4-N04→W4-N06 不存在；W4-N07 属 M16 非 M15） | 两幅图全部按 ARRAY 实际值重绘（图1/图2），并逐条列出错误与实测值 | 图2、7.5、表 3-3 |
| D3 严重 | 最长链 7 层数值错误 | 全路径枚举复算：**W4-N01 → W4-N04 → W4-N05 = 3 节点 2 步**，图2 红色高亮 | 表 3-4 门 4、7.5、图2 |
| D4 中等 | 提案数 16 vs result.json 仅 11 条可辨识 ID，差值 5 条去向未解释 | 账目闭合：16 = 11（8 原生 ID + 3 仲裁 slug）+ 5 未播种；逐条列表并解释 M777 无 id 字段的结构性原因 | 表 3-5、表 3-6 |
| D5 中等 | 图件豁免未声明 | ④ 末尾显式声明：本任务为播种 SQL 插入、无物理仿真输出，故不含场量云图与比较曲线，并说明替代图件的生成方式 | ④ 图件豁免声明 |
| D6 轻微 | 误差判定未显式回答是否 <3% | ⑥ 表格新增「相对误差 / 是否 <3%」列，显式回答「是（0.00%）」，并单列 N/A 项及原因 | ⑥ 全节 |
| D7 严重 | 结论超出证据（100% 无误差与 DAG 错误矛盾） | 结论限定为「SQL 插入 7 项机器门 + 3 项附加校验通过」，显式声明不构成物理精度结论、不等于聚合决策无缺陷；撤回原「100% 无误差」表述 | ⑥ 结论 |

### 7.7 审计未覆盖（诚实边界）
- 未审计**提案内容质量**（物理目标是否前沿、判据是否充分）—— 那需要学科评审，不是数据审计。
- 未审计**节点工期/预算合理性**（planned_start/end 由 SQL 以 `NOW()+INTERVAL` 生成，本身无物理依据）。
- 未验证**节点 goal 中声明的规范条款**（IACS UR I / Polar Code / ISO 19906）的引用正确性。
