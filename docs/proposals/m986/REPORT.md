# Ice Navigation Campaign Wave 4 - Autonomous Proposer: 5 Novel Task Proposals for Wave 5 [RESUBMIT v2]

- 工作单：mission **973**（原报告 **805** 的返工重发；本版标题加注 `[RESUBMIT v2]`）
- 分支：`agent/m986`；改动范围：仅 `docs/`、`artifacts/`（**未改任何业务代码**）
- 结论等级：**REAL（可复算机审）**，适用范围严格限定「本语料（A/B/C 共 744 单元）× 本词表（70 词）× as-of 2026-10-31 20:38:25 UTC」；
  语义级同义改写维度记 **WITHHELD**（词边界匹配不具该能力）
- 一句话结论：5 条提案中 **P1/P2 经关键词判别评估为 NEW**；**P3/P4 与既有工作重叠需收窄重做**；
  **P5 判为与已交付的 W4-N05（报告 787）重复，撤回**。原报告 805 的 “zero coverage / verified NEW” 表述经本版审计不成立，已全部改写并给出证据。

## 0. 对质检 5 项缺陷的逐条闭环

| # | 缺陷 | 本版闭环动作 | 可机器验证的方式 |
|---|------|--------------|------------------|
| 1 | 缺审计节（情报扫描完整性） | 新增 **§7 情报扫描审计**：三层语料 A/B/C 全量冻结为工件、70 词的对象+上下文双词判别、50 条限定命中**逐条人工裁定**（5 类互斥，脚本断言无未裁定项）、覆盖率/假阳性抽检/人工复核一致性三组指标 | `pytest`：`test_adjudication_complete`、`test_scan_recompute`、`test_manifest_sha256`；工件 `keyword_audit.json`、`adjudication.json` |
| 2 | 缺局限节 | 新增 **§5 注意点与局限**：语义遗漏、未扫描语料（GitHub 分支/提案库清单实际只覆盖 `proposals/**`）、时间截止点与 as-of 语义 | 局限条目与 `adjudication_candidates.json` 的候选清单一一对应，可复核 |
| 3 | 缺误差判定节 | 新增 **§6 误差判定**：显式声明“提案型任务无计算基准，误差判定不适用”，并把 α∈[2.0,3.5]、σ_f 相对误差 20%、载荷 20% 三个数字**逐项**交代其来源性质（前瞻验收阈值，非计算结果）与文献基准关系（未实读校准 → WITHHELD） | `test_error_section_declares_na` |
| 4 | 缺图件豁免声明 | 新增 **§4 结果图**：声明“本报告为纯提案/情报汇总任务，无计算仿真结果，不适用场量云图与比较曲线”；同时给出 1 张**审计元数据图**（非场量云图） | `test_figure_exemption_statement`、`test_png_valid` |
| 5 | 结论措辞过强 | 全文弃用 “zero coverage / verified NEW”；改为 **“no coverage found by keyword scan”**、**“assessed NEW by keyword discrimination”**；并由审计结果主动下调 3 条提案的新颖性判定 | `test_no_overclaim_wording`（断言旧措辞零出现） |

## ① 案例介绍（物理背景 / 语料 / 参数表）

### 1.1 物理背景
冰区航行战役 plan 1–4 逐波交付后，任务池面临清零，平台按 owner 常设规则派自主提案者并行提出下一波（Wave 5）前沿任务。
原报告 805 声称在「主计划节点 + 同伴提案 + 已验证案例」三源扫描下发现 5 个 *零覆盖* 前沿。本返工单不改物理提案本身，
而是**把“零覆盖”这一结论做成可复算的审计**，并按审计结果修正结论。

### 1.2 语料参数表（三层，全部机器现读并按字节落盘）

| 语料 | 内容 | 单元数 | 字符数 | 取证源 |
|------|------|--------|--------|--------|
| A | 战役主计划节点全文（title+goal+physics_domain+acceptance+milestone） | 27 | 10065 | `psql campaign_plan_nodes WHERE campaign_plan_id=1` |
| B | 同伴提案（逐条 proposal JSON，排除本单自己的 `prop-973.json`） | 35 | 55486 | `/srv/tensor/agent/proposals/*/*/prop-*.json`（只读） |
| C | 平台报告库全文（已剥离内嵌 figure base64） | 682 | 3064095 | `psql tensor_reports WHERE id<>805` |
| 合计 | — | **744** | **3129646** | `corpus/`（nodes.json / peer_proposals.json / reports.json.gz） |

> 语料 B 的单元数 35 与原文“30 条同伴提案”不同：本版按 `proposals/**/prop-*.json` **实测**为 35 条
> （prop-724=6、prop-725=6、prop-726=6、prop-776=5、prop-777=6、prop-975=6，合计 35；已排除本源 prop-973=5）。
> 原报告 805 的“30”无法复算，本版以 35 为准并保留差异说明。

### 1.3 待审 5 条提案（物理对象量）

| 编号 | 仓库 | 对象量（判新依据） | 原判 |
|------|------|--------------------|------|
| P1 | TensorDEM | 舵叶/舵杆冰载荷与 IACS UR F12 强度校核 | NEW |
| P2 | TensorDEM | 破冰裂纹拓扑与碎块尺度分布律（α、d_mean/h_ice） | NEW |
| P3 | TensorDEM | 冰材强度 σ_f(T) 随温度退化（非界面、非摩擦） | NEW |
| P4 | TensorDEM | 船体下压实碎冰层 h_crush/h_ice 与载荷折减 | NEW |
| P5 | ship-platform | 应变数据反演冰载荷幅值与位置 | NEW |

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

1. **语料冻结**：`build_audit.py` 从 psql 与 `proposals/**` 现读三层语料，剥掉报告内嵌 base64（随机碱基串会污染词计数），落盘到 `corpus/`。
2. **双词判别（object + context）**：每主题给一组对象词（如 P1 的 `舵/rudder/steering gear/F12`）与一组上下文词（`冰/ice/载荷/load`），
   **二者同现**才记“限定命中”。理由：单靠对象词会把“舵面积 A_R”“Fujii 舵力”之类静水操纵内容误判为覆盖。词表共 70 词，全文见 `keyword_audit.json:lexicon`。
3. **逐条人工裁定**：对每条限定命中取对象词上下文 snippet（±90 字符），人工判入 5 类互斥结论之一；
   脚本 `adjudicate.py` **断言每个候选必须有裁定**，未裁定即构建失败（禁止默认放行）。
4. **时序核对**：用 `tensor_reports.created_at` 比较“重叠证据”与报告 805 的发布时刻，判断该重叠在原提案作出时是否已存在。
5. **工具链**：Python 3 + psql（只读）+ matplotlib(Agg)；无网络调用、无业务代码改动。

## ③ 计算结果（数字表，全部工件注入）

### 3.1 关键词扫描（对象词命中单元 / 限定命中单元）

| 主题 | 对象词命中 A / B / C | 限定命中 A | 限定命中 B | 限定命中 C |
|------|----------------------|-----------|-----------|-----------|
| P1 舵/操舵装置冰载荷 (IACS UR F12) | 1 / 0 / 10 | 1 | 0 | 4 |
| P2 破冰模式与碎块尺度分布 | 0 / 1 / 12 | 0 | 1 | 7 |
| P3 温度相关冰强度退化 | 0 / 3 / 11 | 0 | 3 | 11 |
| P4 碎冰层/磨蚀层颗粒力学 | 0 / 3 / 6 | 0 | 3 | 5 |
| P5 应变反演冰载荷识别 | 0 / 2 / 25 | 0 | 2 | 13 |

（A=主计划节点 27，B=同伴提案 35，C=平台报告 682）

### 3.2 逐条裁定结果（50 条限定命中，全部裁定）

| 提案 | OVERLAP_TRUE | OVERLAP_PARTIAL | ADJACENT | GAP_ACK | DIFF_DOMAIN | 合计 | 机审结论 |
|------|--------------|-----------------|----------|---------|-------------|------|----------|
| P1 舵/操舵装置冰载荷（IACS UR F12 舵杆校核） | 0 | 0 | 1 | 0 | 4 | 5 | **no coverage found by keyword scan** |
| P2 破冰模式与碎块尺度分布 | 0 | 0 | 4 | 0 | 4 | 8 | **no coverage found by keyword scan** |
| P3 温度相关冰强度退化 | 1 | 1 | 3 | 1 | 8 | 14 | **overlap confirmed（不新）** |
| P4 碎冰层颗粒力学 | 5 | 1 | 0 | 0 | 2 | 8 | **overlap confirmed（不新）** |
| P5 应变反演冰载荷识别 | 7 | 0 | 2 | 0 | 6 | 15 | **overlap confirmed（不新）** |

- 合计：OVERLAP_TRUE **13**、OVERLAP_PARTIAL **2**、ADJACENT **10**、
  GAP_ACKNOWLEDGED **1**、DIFFERENT_DOMAIN **24**，合计 50 条（无未裁定项）。
- 关键词扫描覆盖率（口径见 §7.1）：限定命中总数 / 扫描单元总数 = 50/744；
  其中 **DIFFERENT_DOMAIN 的同形词假阳性 24 条**（占 24/50），
  即双词判别后仍需人工再筛掉约 **48%** 的假阳性。

### 3.3 5 条提案的最终判定（本版取代报告 805 的“5/5 NEW”）

| 提案 | 机审结论 | 本版动作 |
|------|----------|----------|
| P1 舵/操舵装置冰载荷（IACS UR F12 舵杆校核） | no coverage found by keyword scan | 保留（assessed NEW by keyword discrimination；范围收窄至舵叶/舵杆冰载荷与 F12 校核） |
| P2 破冰模式与碎块尺度分布 | no coverage found by keyword scan | 保留（assessed NEW by keyword discrimination；须与 I03 碎块交接界限写清） |
| P3 温度相关冰强度退化 | overlap confirmed（不新） | 降级为 RESCOPE（与 prop-725#1 多年冰强度退化重叠，须改做“连续破冰升温导致的强度退化”并给出与 MYI 静态退化的差别） |
| P4 碎冰层颗粒力学 | overlap confirmed（不新） | 降级为 RESCOPE（与 W4-N07 碎冰航道阻力 / prop-975-02 brash 均匀化重叠，须改做“船体下压实磨蚀层 h_crush/h_ice”而非航道阻力） |
| P5 应变反演冰载荷识别 | overlap confirmed（不新） | 撤回（W4-N05 报告 787 已交付冰载荷反演+SHM，判为重复提案） |

### 3.4 重叠证据的时序核对（as-of 报告 805 发布时刻）

| 报告 id | created_at (UTC) | 相对 805 |
|---------|------------------|----------|
| 787 | 2026-10-31 18:11:10 | 早于 805 ✓ |
| 802 | 2026-10-31 20:29:53 | 早于 805 ✓ |
| 803 | 2026-10-31 20:31:18 | 早于 805 ✓ |
| 799 | 2026-10-31 18:56:46 | 早于 805 ✓ |
| 793 | 2026-10-31 18:26:46 | 早于 805 ✓ |
| 808 | 2026-10-31 20:41:27 | 晚于 805 ✗ |

- 报告 805 的 created_at = 2026-10-31 20:38:25 UTC；**5/6 份重叠证据早于 805**，
  故 P5（W4-N05，报告 787）与 P4（W4-N07，报告 802/803）在提案作出时**已属既有交付**，不属于“未来工作”，原判 NEW 不成立。
- 报告 808（Wave-5 另一路提案者）晚于 805，仅作为“同波次冗余”旁证，不用于推翻原判。

## ④ 结果图

**图件豁免声明**：本报告为纯提案/情报汇总任务，无计算仿真结果，**不适用场量云图与解析解/文献比较曲线**（无场变量、无收敛解、无基准量）。
按报告纪律仍提供 1 张**审计元数据图**（数据全部来自 `keyword_audit.json` 与 `adjudication.json`，非仿真结果）：

- **图 1** `fig_keyword_audit.png`：(a) 5 主题在 A/B/C 三层语料的限定命中单元数（含各层单元总数图例）；
  (b) 50 条限定命中的裁定分布堆叠柱（红系=重叠，灰=同形词假阳性），柱顶标注该主题机审结论。

## ⑤ 注意点（局限 / 适用范围 / 踩过的坑）

1. **语义遗漏风险（最大局限）**：判别基于字面词边界匹配，**同义改写、跨语言表述、机制等价但术语不同**的覆盖会被漏判 →
   因此本版只声明 “no coverage found by keyword scan”，**不声明“世界范围内首创”**；语义级维度记 WITHHELD。
2. **假阳性残余**：双词判别后仍有 24/50 条需人工剔除（舵面积、inverse-distance weighting、质检转述表格等）；
   人工裁定本身非双盲，存在主观性，已通过 snippet 全量落盘（`adjudication.json:rows`）供复核。
3. **未扫描语料**：仅覆盖 `proposals/**/prop-*.json`、`campaign_plan_nodes(plan=1)`、`tensor_reports` 三源；
   **未扫描** GitHub 分支代码、TensorDEM/FEM/LBM 仓库源码、外部文献库、以及 plan 2/3/4 的节点表（只以报告文本形式间接覆盖）。
   原报告 805 声称的“20 GitHub branches”本单无法复算，属 **WITHHELD**。
4. **时间截止点**：结论 as-of 2026-10-31 20:38:25 UTC（报告 805 发布时刻）。此后新增报告（如 808）或新交付节点会改变判定；
   复现需固定语料快照（`corpus/` 已冻结）。
5. **裁定口径的边界**：P1 的唯一沾边项 M01 是“MMG 框架下的舵力冰修正”（水动力修正项），与“舵杆冰载荷 F12 强度校核”不是同一对象量，
   故记 ADJACENT_SCOPE 而非 OVERLAP；若后续把 M01 扩展为舵系强度校核，P1 应同步降级。此类“看起来像”的边界已在 §3.2 逐条标注。
6. **踩坑记录**：(a) 报告 markdown 内嵌 base64 会污染词计数，需先剥离；(b) 同伴提案的权威源是 `proposals/**/prop-*.json`，
   用报告标题正则切块只能切出 6/35 条，会系统性低估覆盖率；(c) 未裁定候选若默认放行，会重演原报告“结论超证据”的错。

## ⑥ 误差判定

**显式声明：本报告为提案型任务，无计算基准，误差判定不适用（N/A）**——无场解、无实验数据反算、无参考解，故“计算结果 vs 基准的相对误差”一类判定（含“是否 <3%”）在本报告中不予回答，也不应被解读为“通过验收”。
不存在“是否 <3%”可回答的量；凡属可机上复算的**计数类**量（单元数、命中数、裁定分布），均已在 §3 与工件中给出**逐项可重算**的口径与来源，
其一致性由 `pytest` 与第二源复算脚本保证（见 §7.3）。

对报告中出现的三个验收数字，逐项交代其性质与文献基准关系（不掩饰未校准的部分）：

| 验收数字 | 出现处 | 性质 | 与文献基准的关系 | 本版判定 |
|----------|--------|------|------------------|----------|
| 碎块尺度分布幂律指数 α ∈ [2.0, 3.5] | P2 判据 | **前瞻验收阈值**（proposal 时设定的目标带），非计算结果 | 该带源自碎块/碎冰尺度分布文献常见拟合区间，**本单未实读文献核对** | WITHHELD（执行单须先做文献读取并冻结基准值，再据此定阈值） |
| σ_f(T)/σ_f(-20 °C) 与 Schulson (2001) 相对误差 < 20% | P3 判据 | 前瞻验收阈值（容差） | 需实读 Schulson(2001) 曲线并数字化；本单未做 | WITHHELD（执行单第一步=基准数字化 + 复现曲线） |
| 反演载荷幅值误差 < 20%（90% 案例）、位置误差 < 1 m | P5 判据 | 前瞻验收阈值 | **W4-N05（报告 787）已给出同类量 3.49% 的实现级数字**，说明 20% 阈值偏松 | 提案已判重复 → 阈值一并作废 |

> 结论：本报告**不含**任何“预测 vs 实验/解析”的误差数字，故无一项能满足或违反 <3% 判据；原报告 805 未作此声明即为本次质检缺陷 3 的成因。

## ⑦ 情报扫描审计（缺陷 1 的正文落实）

### 7.1 审计指标与结果
| 指标 | 定义 | 本单取值 |
|------|------|----------|
| 语料规模 | 三层语料单元数（A/B/C） | 27 / 35 / 682，合计 744 |
| 词表规模 | 对象词 + 上下文词总数 | 70（5 主题） |
| 限定命中数 | 对象词与上下文词同现的单元±主题对 | 50 |
| 裁定完整性 | 已裁定候选 / 全部候选 | 50/50 = 100%（脚本断言，未裁定即构建失败） |
| 假阳性率（人工复核后） | DIFFERENT_DOMAIN / 限定命中 | 24/50 ≈ 48% |
| 语义覆盖率 | 同义改写维度的漏判率 | **不可测 → WITHHELD**（字面匹配不具备该能力，不做补偿性宣称） |
| 与人工复核一致性 | 原判 5/5 NEW vs 本版 2/5 NEW | **3/5 一致、2/5 推翻**（P3/P4 收窄、P5 撤回；证据见 §3.3/§3.4） |

### 7.2 假阳性抽检（结论可推翻性）
- 抽检对象：**全部** 50 条限定命中（非抽样），逐条记录 snippet、裁定类别与理由（`adjudication.json:rows`）。
- 结果：24 条纯同形词误判、10 条“沾边非同一对象量”、1 条“单元自陈缺失”（反向支持新颖性）。
- 可推翻性：任一条裁定若被反证为 OVERLAP_TRUE，对应提案即应降级；`adjudicate.py` 的裁定表是唯一入口，改动会直接反映到 §3.2/§3.3。

### 7.3 复算与验证
```
python3 artifacts/m986/build_audit.py      # 语料冻结 + 关键词扫描 → keyword_audit.json
python3 artifacts/m986/adjudicate.py       # 逐条裁定（含未裁定断言）→ adjudication.json
python3 artifacts/m986/make_figures.py     # 图 1 → fig_keyword_audit.png
python3 artifacts/m986/build_report.py     # 本文件 + key_numbers.json + MANIFEST.json
pytest docs/proposals/m986/test_report_artifacts.py -q
```
第二源（独立复算）：`test_scan_recompute` 直接从 `corpus/` 重新计词，不复用 `keyword_audit.json` 的中间结果；
`test_manifest_sha256` 逐件重算 sha256。全绿才发布。

## 附录 A · 工件清单与复现
| 路径 | 内容 |
|------|------|
| `artifacts/m986/build_audit.py` / `adjudicate.py` / `make_figures.py` / `build_report.py` | 构建链（语料→扫描→裁定→图→报告） |
| `artifacts/m986/corpus/nodes.json` / `peer_proposals.json` / `reports.json.gz` | 冻结语料快照（as-of 2026-10-31） |
| `artifacts/m986/keyword_audit.json` | 词表 + 每主题每语料命中数/命中 id |
| `artifacts/m986/adjudication_candidates.json` / `adjudication.json` | 候选 snippet + 逐条裁定 + 时序核对 |
| `artifacts/m986/key_numbers.json` | 正文全部数字及 `from` 溯源 |
| `artifacts/m986/fig_keyword_audit.png` | 图 1（审计元数据图） |
| `artifacts/m986/MANIFEST.json` | 全部工件 sha256 |
| `docs/proposals/m986/REPORT.md` / `test_report_artifacts.py` | 本报告 + 验收测试 |

## 附录 B · 与报告 805 的差异摘要（供质检快速比对）
1. “zero coverage” → **no coverage found by keyword scan**（并给语料/词表/口径三重限定）；
2. “verified NEW” → **assessed NEW by keyword discrimination**，且仅 P1/P2 保留；
3. 同伴提案 30 条 → 实测 **35 条**（差异已说明）；
4. 5/5 NEW → **2/5 NEW、2/5 收窄、1/5 撤回**（3 份重叠证据早于 805）；
5. 新增审计节、局限节、误差判定节、图件豁免声明，全部可机器验证。
