# 空白域雷达（Whitespace Radar）—— 池覆盖索引 + 可复算零命中证据链

> 节点：P33-W2-N11 · 落库 TensorPlatform · 零领域语义（话题由调用方注入，池由 JSON manifest 描述）
> 代码：`tools/whitespace_radar.py`（CLI） + `tools/whitespace_radar_core/`（库，纯标准库）
> 数据：`data/whitespace_radar/`（pool manifest / 18 题回放基准 / 域词表）
> 契约：`schemas/whitespace-radar.v1.schema.json`（输出）、`schemas/whitespace-radar-query.v1.schema.json`（输入）
> 产物：`artifacts/whitespace_radar/`（sealed 回放报告 + 性能报告 + 4 图 + 去重门演示）

## 1. 为什么要它

「凭什么判断没人做过」过去是人肉检索：翻提案、翻战役节点、翻各仓模块清单，结论无法复算、
无法审计、也无法被别人重跑。结果是两类错误：

- **重复提案**：某主题其实已在池里（文档/模块/战役节点），提案人不知道 → 平台空转；
- **假空白**：检索式太窄，实际有相邻工作，提案被仲裁门判「重」→ 返工。

雷达把这件事变成一个**纯函数**：`主题文本 → {covered | partial | blank} + 命中引用 + 零命中证据`。
证据封成不可手改的 JSON（规范化编码 + sha256），同输入重跑**逐字节一致**；
blank 裁决自带「我实际执行了哪些检索式、每个都命中 0」——审稿人可原样复算。

## 2. 输入 / 输出契约（radar/v1）

```bash
# 单题
python3 tools/whitespace_radar.py --topic "引导回路 评价牵引 派发 接线" \
        --json-out artifacts/whitespace_radar/example.json
# 批量（仲裁门调用形态；含去重门决策）
python3 tools/whitespace_radar.py --query-json /tmp/q.json --json-out /tmp/out.json
# 回放基准 / 阈值网格
python3 tools/whitespace_radar.py --bench data/whitespace_radar/replay_bench.json --calibrate
# 复核一份证据
python3 tools/whitespace_radar.py --verify artifacts/whitespace_radar/example.json
```

输出关键字段（`radar/v1`，全部在 `schemas/whitespace-radar.v1.schema.json` 里受约束）：

| 字段 | 含义 |
|---|---|
| `verdict` | `covered / partial / blank` |
| `thresholds` | 本次裁决用的全部阈值（逐字段写死进证据，重放不会漂） |
| `query.probes` | 实际执行的**逐字**检索探针（CJK 4 字窗口 / 拉丁词）+ 各自 `df`、是否判别性 |
| `query.existing_specific_probes` | 命中的判别性探针（covered/partial 的引用来源） |
| `hits.docs[]` | BM25 排名命中：`doc_id / ref / score / title` —— 审稿人点开就读 |
| `zero_hit_proof` | blank 的证据：规则、窗口/步长、每个判别性探针及其 `df`、`existence_hits`、一行 statement |
| `pool_digest` / `as_of` | 池内容摘要 / 内容派生时钟（**不用 wall clock**） |
| `digest.sha256` | 除 `digest` 字段外的规范化 JSON 的 sha256（自校验信封） |

**可复算性**由三件事保证：内容派生 `as_of`、`sort_keys` + 固定 separators/indent、
浮点统一 `round(…, 6)`、列表按 `(score desc, doc_id)` 显式排序；计时数据一律**不进**密封信封
（写到 `--stats-out`）。

## 3. 方法：三态裁决到底怎么算

1. **池（pool）** 由 `data/whitespace_radar/pool/manifest.json` 描述，四类源全部是数据：
   ```
   提案 JSON (agent_work/rmg/proposals/*.json)     → 3 docs
   文档 Markdown (docs/**/*.md)                    → 509 docs
   模块清单 (module_manifest.json，多仓)           → 1365 docs
   战役导出 (campaign_export.json，本部署为空)      → 0 docs
   ```
   当前池：**1877 文档 / 859650 token / 108541 词项**。

2. **分词（域中立）**：CJK 一律 2-gram（单字 run 退化为 unigram），拉丁数字串小写且 `len>=3`
   用于探针（`len>=2` 用于索引）；只去掉自然语言虚词（`the/of/的…`），**没有任何领域词表**。

3. **索引**：BM25（`k1=1.2, b=0.75`）倒排，仅供**排序与强度**。

4. **存在性探针（关键设计）**：CJK run 切成 4 字窗口（步长 2），拉丁词 `len>=3` 直接作探针。
   探针「存在」= 池中**确有文档逐字包含它**（`Bm25Index.contains`：最稀有 2-gram 取候选 → 逐字校验）。
   > 为什么不用 n-gram df 当存在性证据：跨词边界 2-gram（如「播数」「法并」）会在无关文档里偶然出现，
   > 让真正全新的主题看起来「部分覆盖」。4 字窗口足够长，命中即共享实质内容。

5. **判别性**：`specific ⇔ df/N ≤ rare_frac(=0.02)`。它是**比例阈值**，随池规模自适应，不写死任何词。

6. **裁决**（阈值全部进证据）：
   | 条件 | 裁决 |
   |---|---|
   | 无判别性探针（查询全是通用词） | `partial`（拒绝判 blank；宁可说「判不了」） |
   | 判别性探针**全部** `df=0` | `blank`（附零命中证据） |
   | `top_score ≥ 10` ∧ 命中判别性探针 `≥2` ∧ `specific_coverage ≥ 0.5` | `covered` |
   | 其余（有相邻工作但核心不在池里） | `partial` |

7. **去重门接口**：`dedup_gate(env) → radar-gate/v1`
   `covered→block_duplicate`（附引用）、`partial→narrow_claim`（须说明相对引用文档新在哪）、
   `blank→allow`（零命中证据原样随提案走）。blank 时**不返回 BM25 邻居引用**——避免把噪声当先例。

## 4. 回放基准（18 题，标签冻结）

`data/whitespace_radar/replay_bench.json`：12 覆盖 + 3 相邻 + 3 空白。

- **12 题 covered**：`random.Random(20261108).sample` 在池内 509 篇 `kind=doc` 上抽样 12 篇，
  逐篇读完写题（题目必须与原文**逐字共享 ≥2 个内容短语**），故每题都自带出处 `provenance`；
- **3 题 partial**：主题=「池中存在的机制词 + 池中不存在的核心词」（如 `Merkle 包含证明 零知识电路 压缩`）；
- **3 题 blank**：机器逐条校验**每个判别性探针 `df=0`**（探针清单写进题目 note）。

验收（`artifacts/whitespace_radar/bench_report.json`，密封）：

| 判据 | 要求 | 实测 |
|---|---|---|
| 三态正确率 | ≥16/18 | **18/18** |
| blank 假阳 | 0 | **0** |
| 重放逐字节一致 | sha256 相同 | `3d038f2d…` 两次运行相同（含 `--no-cache`） |
| killer 测试 | covered→blank | 见 `tests/test_whitespace_radar.py::test_killer_index_scrub_flips_covered_to_blank` |

**阈值稳健性**（`--calibrate` 网格，36 格）：`18/18` 不是一个孤立点——
`s_covered ∈ [2,10] × rare_frac ∈ [0.02,0.05]` 共 8 格都是 18/18；
敏感参数是 `rare_frac`：`0.01` 时降到 16/18（部分真实内容词被误判为非判别性）。

> 诚实声明：本基准是**回归护栏**，不是独立测试集——题目与阈值都在同一批数据上定过。
> 它证明「裁决机械正确、可复算、killer 能翻面」，不证明「语义理解」。局限见 §7。

## 5. Killer 测试（真删索引条目）

```python
needles = covered_env["query"]["existing_specific_probes"]      # 该题命中的判别性探针
scrubbed = Bm25Index.build(scrub(docs, needles))                # 从**池文档正文与标题**里抹掉
analyze(scrubbed, topic)["verdict"] == "blank"                   # 必须翻面
```
同时验证查询侧等价形态：`--drop-term` 删掉同一组探针后**永不返回 covered**（`existence_hits=0`）。
两种形态都在 pytest 里，避免「只改查询不动索引」的假 killer。

## 6. 多仓扩展 + 性能

- 多仓：`--build-manifest --repo TensorPlatform=. --repo TensorFEM=/path` 生成模块清单；
  `--include tools/ --include backend/` 只把「能力面」纳入清单（一次性脚本 `m1234/run_once.py`
  不是能力证据）。清单确定性（按 repo/module 排序），带 digest。当前库存 1365 个模块文档。
- 性能（`artifacts/whitespace_radar/perf_report.json`）：冷建索引 **1.64 s / 1877 文档**（1141 docs/s），
  查询 **3.2 ms/题**（310 题/s）；pickle 缓存热载 **0.12 s，比重建快 13.9×**；
  合成池 500/2000/8000 文档：build 0.018/0.073/0.311 s，查询 0.52/0.58/0.78 ms/题 —— 近线性。
  缓存键=`pool_digest`，池变则失效（`--no-cache` 可强制重建）。

## 7. 注意点 / 局限（必须读）

1. **纯词面**：同义改写（「破冰能力」vs「冰区阻力」）会漏；反过来说，blank 的含义是
   「**这些判别性字符串在池里 0 命中**」，不是「这个概念世界上没人做」。提案被 allow 时，
   把 `zero_hit_proof` 原样贴进提案，让审稿人看得见检索边界。
2. **partial 是保守兜底**：池里存在通用方法词（`反演`、`不确定性`）时，即便主题本身是全新的，
   也只会判 partial，不会判 blank —— 这牺牲召回换「blank 零假阳」，是刻意的。
3. **长句不分词会降低 covered 率**（实测，`artifacts/whitespace_radar/topic_form_sensitivity.json`）：
   5 组同义题目里，连写长句的 specific coverage 均值 **0.64**、1/5 跌到 `partial`（0.38）；
   空格分隔关键词均值 **1.00**、5/5 `covered`。原因：4 字窗口跨越真实词边界会产生必然缺失的探针。
   推荐输入形态=空格分隔的关键词。
4. **池的边界就是结论的边界**：池当前 = TensorPlatform 的文档/模块 + 3 份提案；
   战役节点与其它 Tensor* 仓**未接入**（本部署 `campaign.db` 为空、兄弟仓未 checkout）。
   接入方式已备好（`campaign_export.json` / `--build-manifest --repo`），但**未验证真实战役数据**。
5. **`rare_frac` 是敏感参数**：池规模变化 >10× 时应重跑 `--calibrate` 复核。
6. **池自污染（真实踩过的坑）**：本 README 与基准文件会**引用**题目原文，而 `docs/**/*.md` 本来是池的一部分
   —— 于是雷达在自己的文档里「找到」了它自己制造的证据，`P02` 由 `partial` 翻成 `covered`。
   修法：`pool/manifest.json` 的 `exclude_globs`（排除 `docs/whitespace_radar/*`、`artifacts/whitespace_radar/*`），
   加回归钉子 `test_pool_does_not_contain_bench_topics`（任何池文档逐字包含基准题目即失败）。
   接新池时先问一句：**这份语料会不会引用雷达自己的题？**
7. **中文无词边界**：4 字窗口是工程折中（窗口越大越准但召回越低），参数在 `Thresholds` 里可调且进证据。

## 8. 维护手册（自续航）

| 场景 | 动作 |
|---|---|
| 池内容变了 | 重跑 `--bench … --calibrate`；`pool_digest` 变化会自动换缓存键；比对 `n_correct` 是否仍 ≥16/18 |
| 新增仓 | `--build-manifest --repo NAME=PATH --include <能力面前缀>`，再跑一次回放 |
| 代码面变了 | 模块清单是**时点快照**（`manifest.snapshot_note` 记了刷新命令）：刷新会改 `pool_digest`，必须先重跑 `--bench --calibrate` 再合 |
| 新增战役池 | 导出 `{"schema":"radar-campaign/v1","plans":[…],"nodes":[…]}` 覆盖 `campaign_export.json` |
| 阈值调整 | 改 `Thresholds` 默认值 → 跑 `--calibrate` → 报告里记录新网格，别只报单点 |
| 判据回归 | `python3 -m pytest tests/test_whitespace_radar.py -q`（20 项） |

## 9. 判据对照表

| 战役判据 | 落地位置 | 结果 |
|---|---|---|
| (a) 18 题 ≥16/18，blank 零假阳 | `bench_report.json` / `test_replay_bench_acceptance` | 18/18，FP=0 |
| (b) 重跑逐字节一致 | `test_rerun_is_byte_identical`、`test_cli_is_deterministic_and_verifiable` | sha256 相同 |
| (c) killer：删关键词 → covered 翻 blank | `test_killer_index_scrub_flips_covered_to_blank` | 通过 |
| (d) 域中立：代码里域词 grep=0 | `data/whitespace_radar/domain_terms.json` + `test_code_is_domain_neutral` | 0 命中（10 个文件 × 30 个域词） |
| (e) 版本化 schema + 兼容性测试 | `schemas/*.v1.schema.json`、`test_evidence_matches_committed_schema`、`test_query_schema_rejects_unknown_major`、`test_schema_compatibility` | 通过 |
| (f) pytest ≥8 全绿 | `tests/test_whitespace_radar.py` | **20 passed** |