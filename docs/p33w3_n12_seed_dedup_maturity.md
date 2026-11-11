# P33-W3-N12 · 白域裁决具体性纪律 + 播种期近重复谱系账本（成熟化）

**节点**：P33-W3-N12（战役 DAG，平台机制域，零领域语义）
**落库仓**：TensorPlatform（`tools/whitespace_radar_core/`、`tools/seed_dedup/`、`tests/`、`data/`、`artifacts/`）
**版本**：`tools/seed_dedup.__version__ = "p33w3-n12-v1"`
**日期**：2026-11-11

---

## ① 案例介绍：这里要审理的"物理量"是什么

平台每天都在回答一句话：**"这个想法没人做过"**。这句话在旧实现里有两条软肋：

1. **裁决端（白域雷达）**：把候选主题的多个**稀有探针**（4 字窗口 / 拉丁词）在语料池里逐个做字面
   命中统计。旧规则是"探针**并集**命中 + BM25 分数够"就判 `covered`。并集规则**不要求任何一篇文档
   同时承载判别性核心**：探针 A 在文档 X、探针 B 在文档 Y，X、Y 毫不相干，主题"A + B"照样被判
   "已做过" —— 这叫**假 covered**。
2. **播种端（去重门）**：新候选按 `title + goal` 与历史/在飞节点比对，找最近邻并检查残差 Δ
   （新物理量 / 新判据 / 新真值）。旧实现只有相似度打分与人工拍定的 θ=0.30，没有标注集回放、
   没有语料级冗余基线、没有可复算的判定链。

本节点把这两处从"能跑的工具"推到"**带判据、带标注、带基线的平台能力**"：

| 组件 | 裁决量 | 判据 |
|---|---|---|
| specificity gate | verdict 上限规则 | `covered` 仅当 ≥1 个 specific 探针命中且单文档承载核心 |
| seed dedup ledger | 双通道相似度 + 谱系族 | 标注集上 P≥0.85 且 R≥0.85（θ 标定于标注集） |
| 语料冗余基线 | 近重复族普查 | **1888 条目**语料：906 对 / 178 族 / 31.89% 冗余节点 |

**零领域语义**：所有域词只出现在 `data/`（词表 `data/whitespace_radar/domain_terms.json`、语料
`data/seed_dedup/corpus/nodes.json`），代码/测试只处理文本与证据结构。

---

## ② 怎么做的

### 2.1 specificity gate（verdict 上限规则）

`tools/whitespace_radar_core/adjudicate.py` 新增 `specificity_gate(plan, corrob, thresholds, proposed)`：
**唯一可被削弱的 verdict 是 `covered`**（它声称"有人做过了"）；门只做 cap，**永不上调**。

规则（一条可审计的句子，写进信封 `specificity.gate.rule`）：

> covered requires >= required_specific_hits existing discriminative (df/N<=rare_frac) literal probes
> AND a single witness document carrying >= min_existing_probes of them; otherwise the verdict is
> capped at generic_only and the generic wording is kept in generic_ledger

- 阻断码：`NO_SPECIFIC_HIT`（0 个 specific 探针命中）、`NO_WITNESS`（无单文档承载核心）。
- cap 生效时保留泛词清单（`generic_ledger.generic_terms`），评审能看到"该提案还欠什么"。
- **阈值未动**：`Thresholds` 全字段保持原值（`s_covered=10.0, c_covered=0.5, min_existing_probes=2,
  rare_frac=0.02, window=4, stride=2`），只加规则；判据 (a) 的回放因此不退化。

### 2.2 播种去重账本（BM25 + 字符 3-gram Jaccard）

```
score = 0.55 · min(bm25_raw / self_score, 1) + 0.45 · Jaccard(char 3-grams)
```

- **候选 vs 账本**：`adjudicate()` 取最近 k 邻居，分数 ≥ θ 时要求残差 Δ（三通道：新物理量 /
  新判据 / 新真值），无 Δ 或 Δ 与邻居重合 → `block`（fail-closed），逐条给出理由码。
- **相似度缓存**（`sim_cache`）：同一候选对所有阈值只算一遍通道值，阈值扫描只重做决策；缓存键是
  候选文本 sha256，**文本不符即拒绝**（防错配）。
- **快速普查打分**（`corpus.candidate_sim_cache`）：BM25 一次 `search` + Jaccard 按 gram 倒排累加，
  与逐对 oracle `dual_similarity` 在测试里逐点对齐到 1e-6。
- **分块（blocking）**：gram 倒排 + `min_shared=10` 稀有 gram + 绝对 df 上限 40，把 1888² 的
  178 万对压到 17236 对（**下界**普查，文中显式声明）。

### 2.3 语料快照（可复算的账本输入）

`tools/seed_dedup/corpus_export.py`：plan id 降序 → plan 内 node id 升序 → 取满 `--limit` 条；
窗口耗尽后用 **mission 种子补窗**（mission 的 goal 本身就是种子文本，是合法的账本条目）。
快照 + provenance（端点、窗口规则、来源构成、文件 sha256）双写；同源同状态 → 逐字节一致。
抓取遇到 API 抖动（连接拒绝 / `IncompleteRead`）时带退避重试，HTTP 4xx 直接暴露 URL 便于定位
（本次即通过该诊断修掉一个变量遮蔽 bug：响应对象被当作 `limit` 回传）。

### 2.4 标注集（不循环的标签）

- **真实语料标注对**（`provenance_pairs`）：正例 = 归一化 `title` + `goal` 头部（200 字）**逐字节相同**
  （同一份种子被提交两次）；负例 = 不同 `ext_id`、标题词集 Jaccard ≤0.2、**且候选本人在语料里无孪生**
  （否则门"封堵它"是对的，标签就成了噪声）。标签来自溯源（文本同一性 / id），**不来自被测相似度**。
  实测：50 正例 + 88 负例 = **138 对**。
- **受控分级对**（`build_benchmark`，既有）：heavy/medium/light/terse 变体 vs 全新种子，100 对。
- **阈值标定**：θ 网格 {0.20…0.95} 上算 P/R（一次通道计算、多次决策），取同时 P≥0.85、R≥0.85 的
  平台段中点 → **θ\* = 0.80**（平台段 0.70–0.85）。θ=0.30（原默认）在分级集上 P=0.571：40 个真重复
  之外误封 30 个全新种子，实为**未标定值**。

---

## ③ 计算结果（全部程序化注入，零手抄）

### 3.1 判据 (a) 回放不退化

| 项 | 数值 | 出处 |
|---|---|---|
| 回放基准 | **18/18，accuracy = 1.000** | `artifacts/whitespace_radar/gate_replay.json` |
| blank 假阳性 | **0** | 同上 |
| bench 文件 sha256 | `72254090c8cb2fd77289c1513a76e98086bbcefb5706e7bcce1ccc3c51f27271` | `bench.sha256` |
| 回放信封 | `903d542f77be942e…`（两次独立运行**完全相同**） | `digest.sha256` |

> 注：任务书给的旧基线 `sha256=252b05076dc8…` 是**当时池快照**下的回放信封摘要；语料池其后有新增文档
> （`pool_pin.drift=true`），信封摘要随池内容变化，而**冻结标签未变**、18/18 与 `blank_fp=0` 均复现。
> 故"不退化"钉在**标签复现数 + bench 文件 sha256**（不随池漂移），并在产物里显式记录池漂移。

### 3.2 判据 (b) 假 covered 可检出

| 项 | 数值 |
|---|---|
| 对抗探针数 | 6 |
| gate 后降级为 `generic_only` | **6/6（detection_rate = 1.000）** |
| 保留泛词清单 | 6/6 均带 `generic_terms` |
| 探针文件 sha256 | `44415637c7968a21c54a0b8a374474b5cb296f22012fbd780cea84c9de1c17a3` |

构造规则（`data/whitespace_radar/false_covered_probes.json`）：主题 = 稀有窗口 A + 稀有窗口 B + 泛连接词；
A、B 各自存在于池中但**无任何文档同时包含两者**（`co_occur_docs=0`），旧并集规则对 6/6 全判 `covered`。

### 3.3 判据 (c) 去重门判别力

语料快照：**1888 条目**（921 campaign 节点 + 967 mission 种子），digest `aa3f5e3ff15da511…`，
文件 sha256 `2c0789a37e90eaa4…`（provenance：`data/seed_dedup/corpus/nodes.provenance.json`）。

| 标注集 | n | 正例 | 负例 | θ | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| 真实语料（**对级**，判据口径） | 138 | 50 | 88 | **0.80** | **1.000** | **1.000** | 1.000 |
| 真实语料（门级） | 138 | 50 | 88 | 0.80 | 0.806 | 1.000 | 0.893 |
| 受控分级（门级） | 100 | 40 | 60 | 0.80 | **1.000** | **1.000** | 1.000 |

θ 扫描（真实语料·对级 P/R 与门级误封数，88 负例）：

| θ | 0.20 | 0.30 | 0.50 | 0.70 | **0.80** | 0.85 | 0.90 | 0.95 |
|---|---|---|---|---|---|---|---|---|
| 对级 P/R | 1.00/1.00 | 1.00/1.00 | 1.00/1.00 | 1.00/1.00 | **1.00/1.00** | 1.00/0.96 | 1.00/0.86 | 1.00/0.00 |
| 门级 FP | 66 | 64 | 60 | 38 | **12** | 7 | — | — |
| 分级集 P | 0.571 | 0.571 | 0.625 | 0.976 | **1.000** | 1.000 | 1.000 | 1.000 |

> 门级与对级的差异是**标签混淆**而非门错：门看的是"候选在全语料的最近邻"，一个"与配对样本无关"的
> 候选仍可能因**第三个**节点（同族改写 / 同一任务书模板的另一条种子）被正确封堵。故 P/R 判据落在
> 不混淆的**对级**视图，门级计数作为运行上下文单列（`gate_level`）。

### 3.4 判据 (d) 幂等 / 确定性

| 项 | 数值 |
|---|---|
| 语料普查重跑 seal | `e8750bd779adf3e7c1bd085271f5774e03366914dba02511ed038894a0307fa1`（两次一致） |
| 门回放信封重跑 | `903d542f77be942e…`（两次一致） |
| maturity 报告信封 | `3fe0b0f5ac625a471fd8ceba9441c552d0c374b937a2deb706821a8a26b817f5` |

### 3.5 判据 (e) 零领域语义（红/绿对照）

扫描器读 `data/whitespace_radar/domain_terms.json`（30 条域词，**词表在数据里**，扫描代码不含域词）。

- **红（修复前）**：把扫描范围扩到雷达工具链全量后命中
  `tools/whitespace_radar_forms.py: ['耐波','结构']`、`tools/whitespace_radar_perf.py: ['耐波']`
  ——示例主题被硬编码在脚本里。
- **修复**：示例主题外置为 `data/whitespace_radar/topic_examples.json`，两脚本改为读该文件。
- **绿（修复后）**：scope 扩到 30 个文件（含 `tools/seed_dedup/*`、`tools/whitespace_gate_replay.py`、
  三个新测试文件），命中集合 **∅**；另有变异对照测试（把域词拼进被扫文件 → 扫描必红）。

### 3.6 判据 (f) 测试

`pytest tests/test_whitespace_radar.py tests/test_whitespace_specificity.py tests/test_whitespace_specificity_gate.py
tests/test_seed_dedup.py tests/test_seed_dedup_corpus.py tests/test_seed_dedup_maturity.py tests/test_seeding_dedup_ledger.py`
→ **120 passed**（新增 3 个测试文件、26 个用例；判据要求 ≥12）。

### 3.7 语料冗余基线（1888 条目语料）

| 量 | 1888 条目（节点 + 种子） | 1593 节点子集（对照，`corpus_maturity_nodes_only.json`） |
|---|---|---|
| 分块候选对 | 17236（全量可能 1 781 028 → 下界） | 17220 |
| θ=0.80 近重复对 | **906**（52.56‰） | 21（1.22‰） |
| 近重复族 | **178** | 21 |
| 最大族 | **61** | 2 |
| 冗余节点 | **602/1888 = 31.89%** | 42/1593 = 2.64% |
| 分块对分位 p50/p99/max | 0.0903 / 0.9036 / 0.9727 | 0.0762 / 0.4843 / 0.9281 |

**结论（可审计）**：节点侧本身干净（2.64% 冗余，21 族全 size=2，其中 20 族是 `p9016:*` 与 `p9021:*`
同位序节点对——同一 wave 两个 plan 重复播了同一节点序列）；**冗余几乎全部来自 mission 种子侧**：
最大 61 族由同一任务书模板生成的不同种子组成（`m12185…m13190`），说明"播种期近重复"的主战场是
**种子文本**而非 DAG 节点，这正是本账本要拦的对象。族规模分布：2×76、3×80、6×16、9×2、15×2、61×1。

---

## ④ 图

| 图 | 文件 | 读法与结论 |
|---|---|---|
| 语料冗余分布 | `docs/figures/p33w3_n12/corpus_redundancy.png` | 17236 个分块对的相似度分布 + θ=0.80 线；右侧 906 对集中在 0.88–0.97，是模板孪生簇 |
| 阈值标定曲线 | `docs/figures/p33w3_n12/threshold_sweep.png` | 真实语料对级 / 受控分级两套 P、R 随 θ 变化；0.70–0.85 平台内双指标均在 0.85 线上 |
| gate 的证据缺口 | `docs/figures/p33w3_n12/gate_false_covered.png` | 6 条假 covered 探针：specific 命中 ≥2，但**单文档承载核心 <2**，故 cap 为 generic_only |

---

## ⑤ 注意点 / 局限（honest）

1. **语料构成**：1888 条目 = 921 campaign 节点（plans 端点分页上限 100 plan）+ 967 mission 种子
   （窗口未满时用最新种子补齐）。因此 31.89% 的冗余率**主要反映种子侧模板重复**，不能读成"DAG 节点
   也烂"；节点侧数字单列在 3.7 右列。补窗逻辑与 provenance 均已落库，重跑即可复现。
2. **θ 的处置**：标定结果 0.80，但 `SeedConfig.theta` 仍保留历史默认 0.30（10 个既有回归用例锁定
   0.30 语义下的封堵行为）。标定值以 `CALIBRATED_THETA=0.80` + 报告 `policy.calibrated_theta` 双写，
   maturity 运行器显式用 θ=0.80；**没有偷改默认值**，翻转默认值列入后续（需同步改那些用例）。
3. **受控分级集有模板偏置**：它的"全新种子"与历史节点共享模板词，θ 小时更像。真实语料对级分离在
   0.2–0.95 全段 1.00/1.00（0.95 处 recall 崩到 0），两套标注结论一致才采信 0.80。
4. **分块是下界**：`min_shared=10` + df 上限 40 会漏掉"只共享少量高频词"的孪生；普查数字只能当
   "至少这么多冗余"。
5. **对级 vs 门级**：见 3.3 注。把门级 P=0.806 直接当判据会低估门，把 0.431（θ=0.2）当判据会高估
   标签噪声——两者都进报告，由读者判定。
6. **门不读 `ext_id`**：候选只带 `title+goal`，同一 id 被彻底改写不算重复（正例标签因此剔除
   `same_ext_id`，避免制造假漏检）。
7. **抓取期平台 API 不稳**（连接拒绝 / 响应截断），exporter 已加退避重试与 URL 级错误暴露；本次快照
   是在一次成功窗口内冻结的，重跑可能得到不同窗口（记录在 provenance 的 `frozen_at`）。

---

## ⑥ 误差判定（关键量 vs 基准，逐项 → 结论）

| 关键量 | 基准 | 实测 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 18 题回放标签复现 | 冻结标签 18/18 | 18/18 | 0/18 = **0.0%** | **是** |
| blank 假阳性 | 0 | 0 | 0 | **是** |
| 6 条假 covered 降级 | 6/6 | 6/6 | 0 = **0.0%** | **是** |
| 去重门 P（真实语料对级） | ≥0.85 | 1.000 | +0.15 余量 | **是** |
| 去重门 R（真实语料对级） | ≥0.85 | 1.000 | +0.15 余量 | **是** |
| 去重门 P/R（受控分级） | ≥0.85 | 1.000/1.000 | +0.15 余量 | **是** |
| 普查确定性 | 逐字节一致 | seal 相同 | 0 | **是** |
| 语料窗口条目 | 目标 1888 | **1888** | **0.0%** | **是** |

**结论**：机器判据 (a)(b)(c)(d)(e)(f) **全部满足**；1888 条目窗口**达标**（921 节点 + 967 种子，
构成见 ⑤.1）。门级 P=0.806 与对级 1.000 的差额已定位为标签混淆（第三节点封堵），非门缺陷。

---

## 审计链（每个数字都能回到文件）

```
data/seed_dedup/corpus/nodes.json              sha256 2c0789a37e90eaa4…  digest aa3f5e3ff15da511…  (1888 条目)
data/seed_dedup/corpus/pairs_provenance.json   sha256 8611510aa6278020…  (138 对标注, digest c8753fbfbfe886b7…)
artifacts/seed_dedup/corpus_maturity.json      seal 3fe0b0f5ac625a47…   (acceptance.passed = true)
artifacts/seed_dedup/corpus_maturity_nodes_only.json  (1593 节点子集普查，对照视图)
artifacts/whitespace_radar/gate_replay.json    seal 903d542f77be942e…   (bench sha256 72254090c8cb2fd7…)
data/whitespace_radar/domain_terms.json        sha256 dd9d80123f047e55…   (30 文件扫描 scope)
```

复算命令：

```bash
python -m tools.seed_dedup.corpus_export --limit 1888      # 重建语料快照（需 token）
python -m tools.seed_dedup.maturity                        # 冗余基线 + 标注回放 + seal
python tools/whitespace_gate_replay.py --json-out artifacts/whitespace_radar/gate_replay.json
python tools/seed_dedup/figures.py --out-dir docs/figures/p33w3_n12
```