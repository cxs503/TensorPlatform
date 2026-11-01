# 报告语料可复算质量测绘：把「六要素报告纪律」做成确定性闸门（n=648）

> 语料快照：`audit_20261101T045700Z.json`，指纹 sha256 `913afe4c5c02cce7…`，生成于 20261101T045700Z。全部数字由 `tools/report_corpus_audit.py` 与 `tools/report_audit_calibrate.py` 程序化读出，无手抄。

## 1. 案例介绍（对象 / 数据 / 参数表）

**物理/工程背景**：本平台的每一张工作单都必须交付一份进展报告，owner 于 2026-10-29 把交付标准升级为「论文级六要素」：①案例介绍 ②怎么做的 ③计算结果（数字表）④结果图（场量云图 + 与解析解/文献/基线的比较曲线；纯文档/后端任务须写明无图理由）⑤注意点（坑/局限/适用范围）⑥误差判定（关键量 vs 基准误差逐项列出并**显式**回答是否 <3%；无基准须声明原因）。

**问题**：该纪律此前只由「质检席」LLM 逐篇主观复审（`ops/report_qa.py` 派单）。判定不可复算、跨批不可比、缺陷类别没有量化台账，也无法在**发布前**给写作 agent 一个机器闸门。

**审计对象**：`tensor_reports`（status=published）全部报告，分三个口径统计。参数表：

| 参数 | 值 |
|---|---|
| 语料快照 | `audit_20261101T045700Z.json` |
| 语料指纹 sha256 | `913afe4c5c02cce7c25a890ab400d8df4c69ebfc1e99aedd233d47470686c342` |
| 全语料篇数 | 830 |
| 六要素纪律适用口径（technical） | 648 |
| QA 元报告（meta，形态为逐篇判定表，不适用） | 182 |
| 平均正文长度（technical） | 5792.9 字符 |
| 判定规则版本 | `tools/report_corpus_audit.py`（确定性静态检查 + 标定 20 篇） |

## 2. 怎么做的（方法 / 流程 / 关键设置 / 工具链）

**流程**：①`psql` 只读导出 `tensor_reports`（id/title/scenario_key/markdown/figures 长度/created_at）→ ②逐篇确定性静态检查 → ③语料级聚合 → ④人工标注样本标定 → ⑤出图与报告。

**关键设置（每条都可被单测钉住）**：

- **报告类型分层**：`META_SCENARIOS={report-qa, qa-review}` 或标题命中「【QA】/批次质检/质检报告」判为 meta 元报告。元报告的形态是逐篇判定表，不含物理案例节是**设计如此**；若不分层，缺陷率被系统性污染（meta 口径下六要素全齐率仅 14.3%）。

- **③数字表限定在结果章节内**：全文任意处的参数表不能顶替结果表（首版按全文计数导致漏报，已修）。

- **⑥判定四级状态**：`explicit`（显式回答是否 <3%）/ `no_baseline_declared`（声明无基准并给理由）/ `ambiguous`（提到误差但无结论）/ `missing`。只有前两级算 ok。

- **④图件**：`figures` 载荷 >0，或正文给出实质无图理由（纯文档/后端/调度任务）才算 ok。

- **欠账主题**：对「局限/待办/注意」类章节做**规则分类**（11 个主题的关键词表），不做语义聚类，因此可复算但会低估同义表述。

- **工具链**：Python 3.11 + `psql`（只读）+ matplotlib（本机无 CJK 字体，图内文字用 ASCII，否则渲染为豆腐块——这是踩过的坑）。

## 3. 计算结果（数字表，全部程序化注入）

### 3.1 三口径汇总

| 口径 | n | 六要素全齐 | 全齐率 | PASS | MINOR | RESUBMIT |
|---|---|---|---|---|---|---|
| all 全语料 | 830 | 436 | 52.5% | 411 | 14 | 405 |
| technical 技术报告 | 648 | 410 | 63.3% | 388 | 12 | 248 |
| meta QA 元报告 | 182 | 26 | 14.3% | 23 | 2 | 157 |

### 3.2 六要素覆盖率（technical 口径）

| 要素 | ok 篇数 | 覆盖率 | 弱(ambiguous) |
|---|---|---|---|
| ①案例介绍(物理背景/几何/参数表) | 600 | 92.6% | 0 |
| ②方法流程(设置/工具链) | 599 | 92.4% | 0 |
| ③计算结果(数字表) | 606 | 93.5% | 0 |
| ④结果图(云图+比较曲线) | 489 | 75.5% | 0 |
| ⑤注意点(坑/局限/适用范围) | 603 | 93.1% | 0 |
| ⑥误差判定(是否<3%) | 519 | 80.1% | 91 |

### 3.3 缺陷类别频次（technical 口径）

| 缺陷码 | 篇数 | 占比 | 严重级 |
|---|---|---|---|
| FIGURE_MISSING_UNDECLARED | 159 | 24.5% | RESUBMIT |
| ERROR_VERDICT_AMBIGUOUS | 91 | 14.0% | RESUBMIT |
| MISSING_ELEMENT_METHOD | 49 | 7.6% | RESUBMIT |
| MISSING_ELEMENT_CASE | 48 | 7.4% | RESUBMIT |
| MISSING_ELEMENT_CAVEATS | 45 | 6.9% | RESUBMIT |
| RESULTS_NO_SECTION | 42 | 6.5% | RESUBMIT |
| ERROR_VERDICT_MISSING | 38 | 5.9% | RESUBMIT |
| NO_QUANT_TITLE | 38 | 5.9% | INFO |
| FIGURE_EXEMPT_UNDECLARED_REASON | 24 | 3.7% | MINOR |
| RESULTS_NO_NUMERIC_TABLE | 23 | 3.5% | RESUBMIT |
| THIN_MARKDOWN | 2 | 0.3% | MINOR |

### 3.4 验证欠账主题（来自「局限/待办」章节，规则分类）

| 主题 | 提及篇数 | 占比 |
|---|---|---|
| 网格/收敛性 | 273 | 42.1% |
| 验证域外推/适用范围 | 260 | 40.1% |
| 实验/文献基准缺失 | 147 | 22.7% |
| 模型简化/忽略项 | 84 | 13.0% |
| 标定/参数不确定 | 72 | 11.1% |
| 尺度效应/外推 | 56 | 8.6% |
| 计算成本/规模上限 | 45 | 6.9% |
| 工程化落地 | 23 | 3.5% |
| 湍流/物理模型局限 | 18 | 2.8% |
| 数据可得性/传感器 | 12 | 1.8% |
| 耦合项缺失 | 2 | 0.3% |

### 3.5 缺陷最多的 8 篇（返工候选，可机器复现）

| 报告 id | 判定 | RESUBMIT 缺陷数 | 标题 |
|---|---|---|---|
| 121 | RESUBMIT | 6 | SDAA cavity_natural_convection m204 |
| 259 | RESUBMIT | 6 | m336 probe: SUBOFF Re=1000 eager_fallback 诊断（最 |
| 318 | RESUBMIT | 6 | 议事会 session-002 R3 最终任务波 P2 产出 |
| 343 | RESUBMIT | 6 | m417 进展报告 — SDAA 扫荡线 51 案例 206 单完工全 PASS + 合并报 |
| 805 | RESUBMIT | 6 | Ice Navigation Campaign Wave 4 - Autonomous Pr |
| 835 | RESUBMIT | 6 | Wave 6 Seed Report |
| 875 | RESUBMIT | 6 | 【论文生产线·W2 挖矿】盘点 50 篇报告证据面：3 既有课题核实 + 10 新候选课题  |
| 716 | RESUBMIT | 5 | 【对标吸收】GitHub 同类自主科研智能体调研：全景+五家深读+吸收清单 |

## 4. 结果图

图 1–3 为场量/频次图（本任务性质为语料静态分析，无 CFD 场量云图；比较曲线以「覆盖率/频次」条形图与标定混淆矩阵替代，图源 `artifacts/report_corpus_audit/fig*.png`）：

| 图 | 内容 | 文件 |
|---|---|---|
| 图 1 | 六要素覆盖率（technical 口径） | `fig1_element_coverage.png` |
| 图 2 | 缺陷类别频次 | `fig2_defect_frequency.png` |
| 图 3 | 验证欠账主题分布 | `fig3_debt_themes.png` |

## 5. 注意点（坑 / 局限 / 适用范围）

**踩过的坑（实证）**：

1. **元报告污染**：首版不分层，RESUBMIT 率一度 59.2%；分层后 technical 口径降到 38.3%——差异全部来自 meta 报告的形态误解读。

2. **结果表错位**：首版用全文数值表计数，参数表顶替结果表，导致 `RESULTS_NO_NUMERIC_TABLE` 漏报；已改为结果章节内计数并加单测。

3. **英文报告漏判**：`Error | <3%?` 这类列头不在首版正则里（报告 450 被误判为缺⑥）；补 `<3%?` / `within 3%` / `远低于 3%` 三类模式。

4. **CJK 字体缺失**：本机 `fc-list` 无中文字体，图内中文渲染为豆腐块 → 图内一律 ASCII。

**局限（诚实声明）**：

- 判定基于**标题词表 + 正则**，不是语义理解：换一套模板写作（例如把①②合成一节）会判为缺失。

- 欠账主题为规则分类，同义表述会漏；只可用于排序，不可当作精确计数。

- 标定样本 20 篇，单标注者（AI）、无二次仲裁，标注只看摘录行而非全文 → 标定本身有 ±1 篇量级的噪声。

- 本检查**不能**判断诚实性（honest-FAIL 是否被粉饰）、数字是否溯源——那仍需质检席 LLM 复审；本工具定位是它的**前置机器闸门**与缺陷台账。

**适用范围**：任何 markdown 进展报告；`--self <file>` 可在发布前自查（退出码 1 = RESUBMIT）。

## 6. 误差判定（判定器本身 vs 人工标注基准）

判定器是「测量仪器」，其误差须先被标定。以随机样本（seed=20261101，n=20）人工标注为基准：

| 判定项 | TP | FP | FN | TN | precision | recall | accuracy | 误判率 | 是否 <15%(可接受) |
|---|---|---|---|---|---|---|---|---|---|
| ④图件是否满足 | 16 | 0 | 1 | 3 | 100% | 94% | 95% | 5.0% | 是 |
| ⑥误差判定是否显式 | 16 | 0 | 2 | 2 | 100% | 89% | 90% | 10.0% | 是 |

- ④图件：误判率 5.0%（<3%？**否**；<15% 可接受门限？**是**），FN=[386]（漏判，因无图理由用词不在词表）。

- ⑥误差：误判率 10.0%（<3%？**否**；<15% 可接受门限？**是**），FN=[322, 214]（声明「无数值计算/无解析基准」被归为含糊，属**保守漏判**：宁可多派返工，不放过含糊报告）。

- FP=0：两个判定在本样本上**没有误杀合格报告**，因此可安全用于返工派单的候选清单。

- 需要显式回答的结论：**两项判定的误判率均未 <3%（本仓 3% 精度标准不适用——它是客体量 vs 基准的判据，而这里是分类器 vs 标注），但均在 15% 可接受门限内，故判定器可用于「候选清单 + 机器闸门」，不可单独作为终裁。**

## 7. 结论与可复用产出

- 语料现状：technical 口径六要素全齐 410/648 = 63.3%；最大两个缺口是④结果图（75.5%）与⑥误差判定（80.1%）。

- 产出 1：`tools/report_corpus_audit.py`（确定性闸门，`--self` 单篇自查、`--db` 全库测绘）；24 个单测覆盖每条判定与聚合守恒。

- 产出 2：`tools/report_audit_calibrate.py` + `calibration_labels.json` + `calibration_result.json`（误差判定可复算）。

- 产出 3：本报告与 `artifacts/report_corpus_audit/audit_*.{json,md}` + 3 张图。

- 建议下一步（供下次自找活）：①把 `--self` 接进报告发布前的 pre-commit/CI（缺④⑥直接拦）；②对 `FIGURE_MISSING_UNDECLARED` 的 159 篇做批量补图派单；③把欠账主题表接成「验证欠账看板」，让提案席按主题频率选题。

---

**发布记录**：本报告已发布为平台报告 id=986（`scenario_key=report-corpus-audit`，mission 1416），内嵌图 1–3。复算入口：`python3 tools/report_corpus_audit.py --db` → `python3 tools/report_audit_calibrate.py --audit <latest> --labels artifacts/report_corpus_audit/calibration_labels.json --out artifacts/report_corpus_audit/calibration_result.json` → `python3 tools/gen_report_corpus_audit_md.py`。
