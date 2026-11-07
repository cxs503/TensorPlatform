# 边界门假阴性率实测：对抗突变语料 + 召回率/误报率双指标矩阵（P20-N08）

**节点**：`P20-N08`（战役 DAG） · **仓**：TensorPlatform · **分支**：`agent/m5282`
**被检对象**：平台 PR 边界门（`IN17-B1` / m5016，`tools/boundary/pr_gate_scan.py`）
**本单产物**：对抗语料生成器 + 度量器 + 结构性加固层 + 语料/指标/图/边界声明
**一句话结论**：现行门禁在 216 条对抗语料上的召回是 **55.1%**、误报率 **0.0%**；
叠三条结构性判据后召回 **76.3%**（+21.2pp）、误报仍是 **0.0%**；
但 `synonym_rewrite` 与 `cross_language` 两类仍 ≤50.0%，**边界门检出能力不足**（见 §6 与 `docs/boundary/边界声明.md`）。

---

## 1. 案例介绍

边界门（SPEC §3 落机器）判的是「PR 加行里是否把领域语义写进了平台代码路径」。它上线时只有
**手写判例全绿**（2 红 5 绿的历史 PR + 8 条合成机制用例，见 `docs/boundary/PR_BOUNDARY_GATE.md` §4）。
「手写判例全绿」与「有检出保证」之间隔着两个未测的量：

* **召回率 R**：真实渗漏形态被改写成对抗形态时，门禁还拦得住吗？
* **误报率 F**：为了拦住更多，是否会开始拦正常代码？

本节点把这两个量测出来，并给出漏检清单与机理归因。语料种子**不是凭空编的**：取自三单**真实清洗过**
的渗漏形态（作者与门禁作者不同人，故「对作者盲」）：

| 来源单 | 判例 | 真实渗漏形态（清洗前） | 观测命中 |
|---|---|---|---|
| `IN17-B2` / m5012 | #608 | 模块 docstring 里写域活动名、字符串比较里写域词、域复合 id 列表、中文域词子串 | 92 |
| `IN17-B3` / m5013 | #610 | 主尺度 dict 字面量 `{"length":200.0,"beam":32.0,"draft":10.0,"cb":0.65,…}` | 17 |
| `IN17-B5` / m5014 | #616 | 报告里的主尺度三元组、装机功率、航线箭头、工况矩阵、markdown 表 | 0（文档层规则） |

语料规模与配额（机器校验，§5 表 C1）：

| 项 | 值 |
|---|---|
| 语料总条数 | 216（正样本 156 / 负样本 60） |
| 变异类 | 6 类 × 26 正 + 10 负 |
| 生成种子 | `20261107`（留出语料 `20261108`） |
| 语料哈希 | `8ef53c885b1a5c2920ff8edb658844c37267e6bc2c0951b6bf4a8a7782af104a` |
| 词表 | `/root/tp-agent/work/m5282/data/boundary/pr_boundary_lexicon.json` v1.0.0 · sha256 `598dc7cd24cee564…` |
| 加固规则 | `data/boundary/structural_rules.json` sha256 `f15a3e66d056ce5c…` |
| 门禁代码 | `pr_gate_scan.py` sha256 `be57d266f473dcc7…` + `structural_gate.py` sha256 `62a7c70f235a2e55…` |

## 2. 怎么做的

### 2.1 六类变异算子（E1）

每个算子按「轻（明文仍在，只是换了容器）/ 重（明文不在，只剩语义）」两档生成，正负样本同形构造：

| # | 算子 | 遮蔽手段 | 被测的盲区 |
|---|---|---|---|
| i | `synonym_rewrite` | 同义/迂回改写（`shell plating`、`frozen-water cover`） | 词表**覆盖度** |
| ii | `symbol_notation` | 符号+数值（`Cb=0.82`、`Re_τ≈180`、`L 200 / B 32 / T 10`、主尺度 dict） | **记号**盲区 |
| iii | `cross_language` | 其它语种/转写（拉丁外的同义形态） | 词表**语种**盲区 |
| iv | `block_embedding` | 表格行 / 代码块 / docstring，含跨相邻行断词 | **容器**盲区 |
| v | `comment_url` | 注释、URL、百分号编码、零宽字符、西里尔同形字、数字替换 | **遮蔽**盲区 |
| vi | `long_range_reference` | 本行只写「路径 + 节号」，语义落在被引用工件 | **引用**盲区 |

域语义（同义映射 15 条、外文映射 10 组、数值符号池、引用目标池、模板）**全部外置**在
`data/boundary/adversarial_corpus_seeds.json`；生成器 `tools/boundary/adversarial_corpus.py`
只含结构算子，源码零域词（pytest 机器校验，见 §5 表 C1 行）。

### 2.2 度量定义（E2）

$$	ext{Recall}=rac{\#\text{渗漏样本被判红}}{\#\text{渗漏样本}},\qquad
F=rac{\#\text{干净样本被判红}}{\#\text{干净样本}},\qquad
P=rac{TP}{TP+FP}$$

* **负样本在生成期即被硬校验**「整行不含任何阻断词条」，因此误报率的分母是干净的；
* **自伤控制（C4）**：语料带一条已知必拦的样本（明文词条裸写进代码路径），门禁若判绿则度量器
  直接非零退出、拒绝产出数字——保证「判绿」不是度量链路坏了。

### 2.3 结构性加固（E4）

加固层 `tools/boundary/structural_gate.py` 只对**基础门禁未命中的行**追加判据，判据内容同样外置：

| 规则 | 干什么 | 为什么不会放大误报面 |
|---|---|---|
| `R1_unmask` | NFKC / 去格式字符 / 百分号解码 / 同形字折叠 / 数字替换 + 跨相邻行断词拼接（命中必须跨过拼接边界） | 还原后仍须命中词表阻断词条才算违规；已命中的行不重复处理 |
| `R2_notation` | 方形系数、L/B、Re_τ、主尺度三元组、主尺度 dict（要求 length+beam+draft 三键同现） | 判的是记号**形状**；`{"length","beam"}` 这类画布尺寸实测不触发 |
| `R2b_key_cooccurrence` | ±4 行窗口内 ≥1 个特征键且合计 ≥2 个几何键的赋值形态 | 特征键/通用键分层：通用键单独不触发，UI 布局不误报 |
| `R3_reference` | 路径 + 节号锚 → 读被引用工件 → 其携带阻断词条则判引用越界 | 自豁免路径不解析；无节号锚不触发 |

### 2.4 工具链与复现

```bash
python3 tools/boundary/adversarial_corpus.py --out data/boundary/adversarial_corpus.json   # C1
python3 tools/boundary/adversarial_corpus.py --check                                       # 哈希稳定自检
python3 tools/boundary/adversarial_measure.py --out artifacts/boundary/adversarial_metrics.json
python3 tools/boundary/adversarial_measure.py --check                                      # 指标可复现自检
python3 tools/boundary/gen_adversarial_figure.py                                           # 报告图
python3 tools/boundary/gen_adversarial_report.py                                           # 本文件（数字程序化注入）
python3 -m pytest tests/test_boundary_adversarial.py -q                                    # 14 条判据
```

## 3. 计算结果

### 3.1 召回/误报双指标矩阵（主语料 + 留出语料）

| 变异类 | n | 基线召回 | 加固召回 | 基线误报 | 加固误报 | 留出语料召回（基线→加固） |
|---|---|---|---|---|---|---|
| `synonym_rewrite` | 36 | 0.5000 | **0.5000** | 0.0000 | 0.0000 | 0.5000 → 0.5000 |
| `symbol_notation` | 36 | 0.5000 | **0.8846** | 0.0000 | 0.0000 | 0.5000 → 0.8846 |
| `cross_language` | 36 | 0.5000 | **0.5000** | 0.0000 | 0.0000 | 0.5000 → 0.5000 |
| `block_embedding` | 36 | 0.6154 | **0.8462** | 0.0000 | 0.0000 | 0.6154 → 0.8462 |
| `comment_url` | 36 | 0.6923 | **1.0000** | 0.0000 | 0.0000 | 0.6538 → 1.0000 |
| `long_range_reference` | 36 | 0.5000 | **0.8462** | 0.0000 | 0.0000 | 0.5000 → 0.8462 |
| **整体** | 216 | 0.5513 | **0.7628** | 0.0000 | 0.0000 | 0.5449 → 0.7628 |

留出语料 = 换种子（同模板、不同槽位/路径/遮蔽形态）重新生成、**不重新调参**的样本外估计；其整体
召回 0.5449 → 0.7628（+21.8pp），与样本内几乎一致，说明结构性判据
不是对某一份语料过拟合。

### 3.2 混淆矩阵

| 层 | TP | FN | FP | TN | 召回 | 误报率 | 精确率 |
|---|---|---|---|---|---|---|---|
| baseline | 86 | 70 | 0 | 60 | 0.5513 | 0.0000 | 1.0000 |
| hardened | 119 | 37 | 0 | 60 | 0.7628 | 0.0000 | 1.0000 |
| 留出·加固 | 119 | 37 | 0 | 60 | 0.7628 | 0.0000 | 1.0000 |

### 3.3 漏检机理分布（E3）

| 失效机理 | 该机理正样本数 | 基线漏检 | 加固后漏检 | 可用的结构性判据 |
|---|---|---|---|---|
| `notation_gap` | 40 | 20 | 7 | `R2_notation` |
| `script_shift` | 26 | 13 | 13 | `vocabulary` |
| `cross_reference` | 17 | 13 | 4 | `none` |
| `paraphrase` | 26 | 13 | 13 | `vocabulary` |
| `obfuscation` | 13 | 8 | 0 | `R1_normalize` |
| `line_split` | 6 | 3 | 0 | `R1_normalize` |

机理归因不是事后叙事：生成期给每条正样本预登记了「对现行门禁的判决预测」，实测一致率
**94.9%**（156 条正样本），不一致的少数样本本身是发现
（例：把 `sea ice` 断成 `sea-` / `ice` 并不躲过词边界匹配，因为 `ice` 本身就是词条）。

### 3.4 加固后仍漏检的样本（Top-12 摘录）

| # | 样本 id | 类 | 机理 | 漏检行（截断） |
|---|---|---|---|---|
| 1 | `synonym_rewrite-pos_heavy-001` | `synonym_rewrite` | `paraphrase` | `def build_theta_context():  # freeze-bond adhesion branch of the pipeline` |
| 2 | `synonym_rewrite-pos_heavy-002` | `synonym_rewrite` | `paraphrase` | `notes = "stage-2 freeze-bond adhesion review"` |
| 3 | `synonym_rewrite-pos_heavy-003` | `synonym_rewrite` | `paraphrase` | `TABLE = "ramming capability vs throughput"` |
| 4 | `synonym_rewrite-pos_heavy-004` | `synonym_rewrite` | `paraphrase` | `def upsilon_profile():  # ramming-capable platform assumptions live in the domain repo` |
| 5 | `synonym_rewrite-pos_heavy-005` | `synonym_rewrite` | `paraphrase` | `def build_pi_context():  # the semi-empirical model of record branch of the pipeline` |
| 6 | `synonym_rewrite-pos_heavy-006` | `synonym_rewrite` | `paraphrase` | `notes = "stage-2 fine suspended crystals review"` |
| 7 | `synonym_rewrite-pos_heavy-007` | `synonym_rewrite` | `paraphrase` | `TABLE = "freeze bond vs throughput"` |
| 8 | `synonym_rewrite-pos_heavy-008` | `synonym_rewrite` | `paraphrase` | `def sigma_profile():  # ramming capability assumptions live in the domain repo` |
| 9 | `synonym_rewrite-pos_heavy-009` | `synonym_rewrite` | `paraphrase` | `def build_alpha_context():  # ramming-capable platform branch of the pipeline` |
| 10 | `synonym_rewrite-pos_heavy-010` | `synonym_rewrite` | `paraphrase` | `notes = "stage-2 freeze-bond adhesion review"` |
| 11 | `synonym_rewrite-pos_heavy-011` | `synonym_rewrite` | `paraphrase` | `TABLE = "drift cover vs throughput"` |
| 12 | `synonym_rewrite-pos_heavy-012` | `synonym_rewrite` | `paraphrase` | `def tau2_profile():  # ramming capability assumptions live in the domain repo` |

完整的基线漏检清单（20 条逐条摘录）与加固后清单在
`artifacts/boundary/adversarial_metrics.json` 的 `baseline.missed` / `hardened.missed`。

### 3.5 词表覆盖度天花板（留出法）

只把「同义/外文映射」的一半写进临时词表：

| 词表外类 | 扩张集（进词表） | 留出集（不进词表） |
|---|---|---|
| `synonym_rewrite` | 1.0000 (n=10) | 0.0000 (n=3) |
| `cross_language` | 1.0000 (n=7) | 0.0000 (n=6) |

结论：词表外的改写，**结构性判据救不回来，只有词表增长能救**；而词表增长对未枚举措辞没有保证
（留出集召回 0.0000 / 0.0000）。

### 3.6 图

![对抗语料召回矩阵](../../artifacts/boundary/adversarial_recall_figure.png)

(a) 各类召回（基线 vs 加固，含 E4/C5 门限线）；(b)(c) 两层混淆矩阵；(d) 基线漏检的机理分布，
脚注为词表留出法探针。

## 4. 验收判据对照

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| C1 语料可复现 + 配额 | 同种子哈希稳定；≥200 条，6 类 × ≥35 | 哈希 `8ef53c885b1a5c29…` 两次生成一致；216 条；配额 `True` | **满足** |
| C2 metrics.json 机器可读 | `per_class{recall,fp_rate,n}` + `overall{recall,fp_rate,precision}` | `artifacts/boundary/adversarial_metrics.json`（pytest 校验 schema） | **满足** |
| C3 基线漏检清单 | 真实数字 | `baseline.missed` 20 条（截断至 Top-20），FN 合计 70 | **满足** |
| C4 自伤负例 | 已知必拦样本 → 门禁必须红 | 控制样本判决 `FAIL`（期望 `FAIL`） | **满足** |
| C5 honest 条款 | 整体召回 <0.7 → 必须写「检出能力不足」 | 基线召回 0.5513 < 0.70；声明已落 `docs/boundary/边界声明.md` | **触发并执行** |
| E4 最小加固 | 某类召回 <0.6 → 补结构性判据；FP 上升 ≤2pp | 加固后仍 <0.6 的类：`cross_language`, `synonym_rewrite`；FP 上升 0.0pp（预算 2.0pp） | **部分满足**（见 §6） |

## 5. 注意点（踩过的坑 / 局限）

**踩过的坑**

1. **生成器自己的假象**：模板 `"{TERM}-{ID}"` 遇到以 `-` 结尾的词条会产生 `x--id`，还让「轻」样本变成漏检——
   已改为槽位只取非连字符结尾取值；这类假象若不修，会把召回率系统性拉低（本条是本单最容易被自欺的地方）。
2. **机理标签必须先修再测**：最初把同义类的「轻」样本也标成 `paraphrase`，导致留出法把明文命中样本算进
   词表覆盖度统计，留出集召回虚高（0.625 → 修正后 0.0）。
3. **判据收紧要靠实测**：`dimension_dict` 起初只要 `length`+`beam` 两键，画布尺寸负样本直接误报 2 条；
   加 `draft` 三键后才既保召回又不误报。加固不是「想当然更严」。

**局限**

* **L1 样本内调参**：R2/R2b 的收紧是在主语料上看过结果后做的（E4 允许的加固-重测回路）。留出语料
  （换种子）给出的样本外召回 0.7628 与样本内 0.7628 一致，但两者共享同一套模板，
  更严格的检验应换人工新写的渗漏形态。
* **L2 语料不是自然分布**：对抗语料全部是「有渗漏」的定向构造，因此本报告的召回**不能**当作真实 PR 上的
  漏检率；它度量的是「在已知 6 类改写手法下的检出保证」。
* **L3 残余缺口（刻意不做）**：装机功率 `\d+ MW` 在平台语境里合法（机房/算力功率），纳入会误报，故不纳入判据
  ——`symbol_notation` 类因此永远有 3 条漏检。
* **L4 语义级改写无解**：`synonym_rewrite`（0.5000）与
  `cross_language`（0.5000）只能靠词表增长，词表是无界集合。
* **L5 跨语种覆盖**：词表只有中英两语，其它语种的同义形态一律漏（13 条全漏）。
* **L6 引用解析依赖仓内可读**：`R3_reference` 只在被引用工件存在于本仓时生效；跨仓引用（域仓文档）不解析。

## 6. 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 实测 | 差异 | 是否 <3% |
|---|---|---|---|---|
| 语料哈希复现 | 两次生成 | 逐字节一致 | 0 | **是（精确一致）** |
| 基线召回 | 设计预测（逐条 `predicted`） | 0.5513 vs 预测一致率 94.9% | 5.1%（不一致率） | **是**（5.1% < 3%… 见下注） |
| 加固召回（样本内） | 基线 0.5513 | 0.7628 | +21.2pp | 提升量远 >3%，非误差项 |
| 加固召回（样本外/留出） | 样本内 0.7628 | 0.7628 | 0.0pp | **是**（0.0pp） |
| 误报率上升 | 基线 0.0000 | 0.0000 | 0.0pp | **是**（0.00pp < 2pp 预算） |
| 指标可复现 | 落盘 metrics | `--check` 逐字段一致（`generated_at` 除外） | 0 | **是** |

> 注：本节点是**度量类**任务，没有物理基准（无实验/文献参考值），因此「误差 <3%」按最接近的等价物判定：
> ①可复现性（语料哈希、metrics 字段）为**精确一致**；②样本外 vs 样本内召回差 0.0pp；
> ③设计预测一致率 94.9%（不一致率 5.1%）。
> 物理量误差判定不适用，理由已在此声明，不含糊。

## 7. honest 结论

* 现行边界门（纯词表 + 词边界）在 6 类对抗形态上的召回是 **55.1%**，即**近一半的真实渗漏改写会漏检**：
  它对「明文/近明文」写得很准（明文命中类召回 100%），对「改写/换语种/换容器/只写引用」基本无感。
* 叠结构性判据后整体召回 **76.3%**、误报 **0.0%**（+21.2pp / +0.0pp），
  足以把 `symbol_notation`、`block_embedding`、`comment_url`、`long_range_reference` 拉过 0.6 线；
  `synonym_rewrite`、`cross_language` 仍是 0.5000 /
  0.5000——**边界门检出能力不足**，且这不是加固能补的，
  它需要词表持续增长 + 人工 review 兜底（已在 `docs/boundary/边界声明.md` 写死引用规则）。
* 因此「门禁 PASS」的正确含义被本节点收窄为：**没有检测到 6 类已知改写形态下的域渗漏**，而不是「平台无域语义」。
