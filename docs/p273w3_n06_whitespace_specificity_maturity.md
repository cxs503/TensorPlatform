# p273w3-n06 · 白域裁决具体性纪律 + 播种期近重复谱系账本（成熟化节点）

**节点**：p273w3-n06-whitespace-dedup-maturity（战役 DAG 播种期治理）
**仓**：TensorPlatform（`tools/whitespace_radar_core/`、`ops/taskplane/governance/`）
**基线对照**：`tools/whitespace_radar_core` @ commit `51eb9368`（同一冻结池上运行旧代码）
**证据目录**：`artifacts/p273w3_n06/`（`metrics.json` 为唯一数字源，本文件所有数字引自该文件或其产物）

---

## ① 案例介绍：这一层在裁决什么

白域（whitespace）裁决的回答是「这个题目池子里是不是已经有人做过了」。它决定提案能不能被播种成战役节点：
判 `covered` → 提案按重复关闭；判 `blank` → 允许播种（附零命中证明）；中间态 → 要求缩小主张。

旧规则（`radar/v1` 初版）用**探测词在池中的并集**回答这个问题：把题目切成 4 字窗口 / 拉丁词，
若这些探测词**在某个文档中出现过**、且 BM25 最高分与具体覆盖率过阈值，就判 `covered`。
两个结构性缺陷在本节点被量化：

| 缺陷 | 表现 | 后果 |
|---|---|---|
| **并集语义**（无见证文档） | 每题的具体探测词可能分散在不同文档里，没有任何一个文档谈这个组合 | 把「跨领域关键词拼接」的题目误判成 `covered`（过度封杀真白域） |
| **证据快照漂移** | 池子 = 工作树 `docs/**` 实时扫描；`docs/` 越长越大，而回放标签冻结在旧池上 | 基准静默失真：本节点开工时 `tests/test_whitespace_radar.py` 有 2 个断言已红（16/18） |

播种期的第二个失败是「同一个想法换个说法再来一次」。`ops/taskplane/governance/proposal_dedup.py`
已有提案级去重门，但它面向 ≥60 字提案摘要，且**没有**把「新在哪」结构化成可机器裁决的残差。

参数表（本节点全部阈值，未修改）：

| 参数 | 值 | 说明 |
|---|---|---|
| `s_covered` / `s_floor` | 10.0 / 1.0 | 覆盖门槛 / 相关性下限 |
| `c_covered` | 0.5 | 具体探测词覆盖率门槛 |
| `min_existing_probes` | 2 | 存在探测词数门槛（本次被同时用作**见证文档内共现**门槛） |
| `rare_frac` | 0.02 | df/N 判定「具体」 |
| `window` / `stride` | 4 / 2 | 中文探测窗口与步长 |
| 播种门 `theta` | 0.30 | title+goal 区制的近重阈值（dev 划分选定，见 ③） |

> 纪律：**阈值向量一个都没动**（`test_threshold_vector_is_unchanged` 逐字段断言）。
> 变的是「证据的计量单位」：从「探测词在池里出现过」变成「有一个文档同时承载了判据核心」。

---

## ② 怎么做的

### (a) 具体性纪律：`covered` 必须有见证文档

`tools/whitespace_radar_core/adjudicate.py`

1. 新增第四态 `generic_only`（`LABELS = covered | generic_only | partial | blank`），
   语义 = 「池子里有东西，但只是泛词层面的相似；不允许判 covered」。
2. 新增 `corroboration()`：对每个**具体**探测词取**完整**文档集（修掉旧实现用 `docs[:5]`
   截断列表做锚定的缺陷：`C12` 这类题目的 `matched_specific_probes` 曾假性为空），
   统计「单文档承载了几个不同具体探测词」，取承载最多者作为**见证文档**。
3. 判定链改为：`covered` 要求 `top_score≥s_covered ∧ 存在具体探测词数≥min_existing_probes ∧
   覆盖率≥c_covered ∧ 见证文档承载≥min_existing_probes ∧ 见证文档分≥s_covered`；
   前三条满足而见证条件不满足 → 降级 `generic_only`。
4. `generic_only` 附带**拒绝证据账本** `generic_ledger`：每个具体探测词的 df、指向的最佳文档与其
   得分、机器理由（`absent` / `below_floor` / `lone_mention` / `not_witness`），以及题目自身的
   泛词清单（df/N > `rare_frac` 的 2-gram/拉丁词）。
5. 仲裁桥 `bridge.py`：`generic_only → narrow_claim`，并把账本随 gate 信封下发，
   让「为什么不算覆盖」可复核而不是让人相信。

### (b) 播种期近重复谱系账本：残差 Δ 门（fail-closed）

`ops/taskplane/governance/seeding_dedup.py`（新模块，schema `seeding-dedup/v1`）

* 输入：候选 `{title, goal, deltas}` + 账本（历史节点 **与在飞节点**，`status ∈ {done, inflight}`）。
* 复用 `proposal_dedup` 的五特征相似度（字符 IDF 余弦 / LCS / 骨架 Jaccard / 二元 IDF 余弦），
  IDF 在**账本语料**上重建（不是两文档语料——短语料 IDF 退化会让任意文本都像），输出最近 k 邻居 + 分数分解。
* **残差 Δ** 三通道固定为 `new_quantity`（新物理量）/ `new_criterion`（新判据）/ `new_truth`（新真值）。
  声明项只有在「邻居没声明过 **且** 邻居正文没写过」时才算 Δ；否则视为重复。
* 判定：邻居分 < theta → `NEW_TOPIC`（放行）；邻居分 ≥ theta 且任一通道有可用 Δ → `RESIDUAL_DELTA`（放行，
  附每通道「邻居已占用」清单）；否则 `NO_RESIDUAL_DELTA`（**拒**）。
* fail-closed：缺/短 title、goal 过短、词项过少 → `INSUFFICIENT_INPUT`（拒）；账本不可解析 → `LEDGER_INVALID`（拒）。
* 结果封装 `seal()`：`digest.sha256` 覆盖决策与账本封印（`ledger.seal`），同输入逐字节一致。

### (c) 证据快照冻结：`pool/pin.json`

`tools/whitespace_radar_core/sources.py` 新增 `build_pin / apply_pin / pin_report / load_pin`：
对**实时扫描型**来源（`docs/**`、提案 JSON、campaign 导出）按内容哈希冻结 515 条条目；
`module` 类由自身快照文件冻结。回放时只纳入「被冻结且未被修改」的文档，新增/修改/删除都进 `drift` 报告。
CLI 默认 `--pin data/whitespace_radar/pool/pin.json`，bench 报告内嵌 `pool_pin` 漂移块。

### (d) 对抗探针集（可复现构造）

`tools/whitespace_radar_false_covered.py` → `data/whitespace_radar/false_covered_probes.json`

构造规则（写在数据文件里，机械可复现）：取**标题来源**的 4 字窗口（df ∈ [5, rare_frac·N]）、
两两配对**文档支持不相交**（无任何文档同时含二者）+ 通用连接词；仅当**旧规则**（并集语义）判 `covered` 时入选。
即：探针集的类别由「旧规则的错误」+「无共现」定义，**不看**新规则。
本次 6 条（FC01–FC06）在冻结池上旧代码 6/6 判 `covered`。

### (e) 100 对标注集（三分类对照）

`ops/taskplane/governance/seeding_dedup_pairs.py` → `data/seeding_dedup/pairs_100.json`
账本 400 节点（1/5 在飞），候选 100：`duplicate` 40（换述/加长/极简重播，`light|medium|heavy|terse` 各 10）、
`novel` 30（与 duplicate 同换述机制，**只多**一个新 Δ 标识）、`unique` 30（不同骨架、内容词不交、无 Δ）。
刻意让 `duplicate` / `novel` 共享同一换述分布，把差异压到 Δ 通道上；`terse`/`heavy` 留在正类里，
因为「极简重播同一个想法」是真实失败模式，只放简单复述会让 recall 数字失去意义。
语言为自造 CJK 词 + 通用连接词（零领域语义）。

---

## ③ 计算结果

唯一数字源：`artifacts/p273w3_n06/metrics.json`（由 `artifacts/p273w3_n06/build_artifacts.py` 程序化生成）。

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| C1 回放基准 | 18/18，acc = 1.000 | 18/18，acc = 1.000，blank 假阳 = 0（`replay_bench_pinned.json`） | ✅ |
| C2 假 covered 探针 | 6/6 降级 `generic_only` | 6/6，检测率 1.000（`false_covered_redgreen.json`） | ✅ |
| C3 播种去重门 | P ≥ 0.85，R ≥ 0.85（100 对） | P = 1.000，R = 1.000，F1 = 1.000（holdout 30 对亦 P/R = 1.000） | ✅ |
| C4 确定性 | 同输入逐字节一致 | 4 个 sealed 重跑断言 + 批量报告一致（sha256 相同） | ✅ |
| C5 零领域语义 | 扫描红/绿对照 | 17 个在扫描范围内的文件 0 命中；变异对照（注入 1 个词）→ 检出 | ✅ |

### C1 回放基准（冻结池 digest `16ebe14d…`，1877 文档）

| 项 | 值 |
|---|---|
| 冻结实时扫描条目 | 515 |
| 开工时工作树新增（排除） | 170 |
| 开工时工作树修改（排除） | 3 |
| 删除 | 0 |
| 18 题 label/verdict 一致 | 18/18 |
| `generic_only` 出现次数 | 0（既有标签无一被新纪律误降级） |

**基线对照**：同一基准在**未冻结**的工作树池（2050 文档）上为 **16/18**，两处差异恰好是
`P01`（应为 partial，旧池中因新增文档变成 covered）与 `B02`（应为 blank，旧池中因新增文档变成 partial）。
冻结后在**同一池**上跑旧代码 = 18/18、跑新纪律 = 18/18：漂移是池子长大造成的，不是裁决规则变坏。
这就是把 pin 做进机制的理由——否则基准会随 `docs/` 增长静默失真。

### C2 假 covered 探针（红/绿）

| 探针 | 旧核判 | 旧 top_score | 新判 | 见证文档承载具体探测词数 |
|---|---|---|---|---|
| FC01 | covered | 23.08 | generic_only | 1 |
| FC02 | covered | 20.07 | generic_only | 1 |
| FC03 | covered | 20.85 | generic_only | 1 |
| FC04 | covered | 17.30 | generic_only | 1 |
| FC05 | covered | 13.23 | generic_only | 1 |
| FC06 | covered | 15.28 | generic_only | 1 |

六条探针的 BM25 分数都远高于 `s_covered = 10`、存在具体探测词数都 ≥ 2、覆盖率都 ≥ 0.5 ——
**旧规则的每一条门槛都过**；唯独没有任何文档同时承载 2 个具体探测词。见 fig1。

### C3 播种去重门（100 对，θ = 0.30）

| 规则 | precision | recall | F1 | TP/FP/TN/FN |
|---|---|---|---|---|
| 门（相似度 + 残差 Δ） | **1.000** | **1.000** | 1.000 | 40/0/60/0 |
| 消融：只用相似度 | 0.571 | 1.000 | 0.727 | 40/30/30/0 |
| 消融：只查 Δ 不查相似度 | 0.571 | 1.000 | 0.727 | 40/30/30/0 |

分数分布（`score_spread`）：`duplicate` 0.377–1.000（中位 0.518）、`novel` 0.392–1.000（中位 0.469）、
`unique` 0.175–0.241（中位 0.195）。θ = 0.30 落在 `unique` 上界 0.241 与 `duplicate` 下界 0.377 之间，
平台宽（`similarity_theta_sweep`：θ ∈ [0.24, 0.35] 判别结果不变）。见 fig2 / fig3。

---

## ④ 图

| 图 | 内容 | 文件 |
|---|---|---|
| fig1 | 6 条假 covered 探针：左 = BM25 分数全部过覆盖门槛；右 = 见证文档承载数全部 < 2 | `artifacts/p273w3_n06/fig1_specificity.png` |
| fig2 | 播种门 vs 两个消融的 precision/recall（0.85 验收线） | `artifacts/p273w3_n06/fig2_seeding_gate.png` |
| fig3 | θ 扫描：纯相似度规则的 P/R（dev / holdout）与出厂 θ 位置 | `artifacts/p273w3_n06/fig3_theta_sweep.png` |
| fig4 | 证据快照：冻结条目 vs 新增/修改/删除 | `artifacts/p273w3_n06/fig4_pool_drift.png` |

图全部由 `build_artifacts.py` 用 matplotlib 从 `artifacts/p273w3_n06/*.json` 直接绘制（零手抄）。

---

## ⑤ 注意点（坑与局限）

1. **`docs[:5]` 截断是真 bug**：旧实现用「前 5 个命中文档」列表判断探测词是否落在 top 文档里，
   导致 df > 5 的探测词锚定假阴性（`C12` 类题目 `matched_specific_probes` 为空）。
   修复后见证判定用完整文档集；`docs` 字段仍只存 5 个 id（证据体积），并新增 `n_docs_hit` 显式给出 df。
2. **基准会随仓长大而失真**：`docs/**` 是池的一部分，17 个新文档就能翻掉两个标签。
   本节点的答案是「冻结 + 报告漂移」，不是「重贴标签」——重贴标签等于用模型输出训练模型。
   代价：`pin.json` 只存哈希不存正文，**被修改**的文档无法回到旧文本，只能排除（本次 3 条，未影响 18/18）；
   若将来修改的恰是证据文档，基准会先失败再要求人复核，这是有意的 fail-closed。
3. **探针集是构造集**：FC01–FC06 的类别由「旧规则判 covered + 两探针文档支持不相交」定义，
   与「见证文档不存在」高度相关——因此 6/6 是**检出率**，不是自然分布下的误报率。
   自然分布的红/绿对照只能由 C1（18 题无一误降级）+ 真实播种流量的线上计数来补。
4. **100 对标注集是合成集**：`unique` 类用不同骨架 + 不相交内容词，分离度偏乐观（间隙 0.136）。
   真实播种流里题目常有部分重叠，θ 与 `rare_frac` 需要线上数据复核；`terse`/`heavy` 复述留在正类，
   是本节点对「不要只测简单改写」的自觉约束。θ 与生成器在同一轮迭代中共同确定（先看分再定 θ），
   这是设计上的选择偏差，缓解手段是 dev/holdout 切分 + θ 平台扫描 + 消融三件套，但**不能**取消该偏差。
5. **`theta` 不能沿用提案门的值**：`DedupConfig.theta_clone = 0.24` 是在 ≥60 字提案摘要上标定的；
   title+goal 区制的短文本 + 小账本 IDF 会把所有文本对推到 0.35–0.5，直接套用会全量误封杀。
   故新增 `SeedingConfig(theta=0.30)` 并保留注入点。
6. **`generic_only` 与 `partial` 都会让提案「缩小主张」**：二者在仲裁桥同为 `narrow_claim`，
   区别只在证据侧（前者保留了被拒绝的具体探测词账本）。这避免了新增状态破坏既有下游契约。
7. **覆盖率门槛未动**：本节点没有用「调 `c_covered` / `rare_frac`」来换指标，任何阈值变更都需重跑
   `--bench --calibrate`（README §6 的既有纪律）。

---

## ⑥ 误差判定（逐项 vs 基准）

本节点是**裁决/治理机制**，没有连续物理场量，因此逐项对标对象是「同一池上的旧代码行为」与
「冻结标签」：

| 关键量 | 基准 | 实测 | 相对误差 | 是否 < 3% |
|---|---|---|---|---|
| 回放基准正确题数 | 18（冻结标签） | 18 | 0% | ✅ |
| 回放准确率 | 1.000 | 1.000 | 0% | ✅ |
| blank 假阳数 | 0 | 0 | 0% | ✅ |
| 假 covered 探针检出数 | 6 | 6 | 0% | ✅ |
| 播种门 precision | ≥0.85（验收线） | 1.000 | 超线 17.6% | ✅ |
| 播种门 recall | ≥0.85（验收线） | 1.000 | 超线 17.6% | ✅ |
| 结果确定性 | sha256 相同 | 相同（4 处断言 + 批量报告） | 0% | ✅ |
| 领域词命中数（代码） | 0 | 0（17 文件）；变异对照检出 1 | 0% | ✅ |

**未完成 / 明确不做**：① 未把 `seeding_dedup` 接进真实播种链路（本节点只交门与其判据，
接入需要 campaign dispatcher 侧的 schema 适配，属跨仓改动）；② 探针集与 100 对标注集均为构造/合成数据，
自然分布下的误报率未测；③ `pin.json` 不存正文，被修改文档只能排除不能回滚。

---

## 复现命令

```bash
python3 -m pytest tests/test_whitespace_radar.py tests/test_whitespace_specificity.py \
                 tests/test_seeding_dedup_ledger.py -q          # 61 passed
python3 tools/whitespace_radar.py --bench data/whitespace_radar/replay_bench.json --quiet --json-out /tmp/b.json
python3 tools/whitespace_radar_false_covered.py                  # 重建探针集
python3 -m ops.taskplane.governance.seeding_dedup_pairs --out data/seeding_dedup/pairs_100.json
python3 -m ops.taskplane.governance.seeding_dedup_bench --out artifacts/p273w3_n06/dedup_bench.json
python3 artifacts/p273w3_n06/build_artifacts.py                  # 重算全部数字与图
```