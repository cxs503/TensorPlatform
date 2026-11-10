# 能力台账三源自动对账引擎（成熟化）：p305w1-n09 交付报告

**一句话**：把「能力台账自述等级」与「代码 / 测试 / 文档三源机器证据」做成可复算
对账，输出一致性报告、过度声明检测、等级调整建议与 sha256 链式审计锚；在合成金
标准语料上四项判据全绿，在真实 21 项台账上覆盖率/可复现/schema/零域语义四项通过，
**过度声明准确率与等级一致率未达标（honest FAIL，见 §6）**。

## ① 案例介绍（对象与输入）

| 项 | 内容 |
|---|---|
| 对账对象 | 平台能力台账 `artifacts/capability_ledger/capability_ledger.json`（21 项能力，schema `2.0`） |
| 判定范围 | 被扫描工作树 `.`（树外实现不可见，见 §5 局限 1） |
| 三源 | 代码（AST）/ 测试（AST + 可选 junit 执行结果）/ 文档（精度声明、适用范围、失败语义） |
| 外部裁决 | `artifacts/capability_ledger/capability_ledger_resolved.json` 的 `reconciled_level`（证据合同口径） |
| 客观量 | 扫描覆盖率、过度声明准确率/召回率、等级一致率、审计锚 sha256 |

本任务是**平台机制**建设（零领域语义）：输入的域标签与能力 id 全部来自台账/绑定
配置，引擎源码内不含任何能力 id 或域标签字面量（机器判据见 `test_zero_domain_hardcoding_*`）。

## ② 怎么做的（方法 / 流程 / 关键设置）

1. **解析层**（`resolver.py`）：台账形状归一化（dict / list / 嵌套三形态）→ 能力 id
   分词 → 在代码文件名词表上用 **idf 加权**匹配实现文件（抑制 `generate`/`models`
   这类泛词），引用命中的文件降级为旁证；测试与文档按路径结构分桶；显式 `--bindings`
   优先于自动解析。单文件引用 ≥8 项能力时视作「注册表聚合器」，不计入实质实现。
2. **扫描层**（`scanners.py`）：代码走 AST —— 空壳函数体（`pass` / `...` /
   `NotImplementedError` / 空返回）、常量直返、未播种随机、**代码行**上的硬编码标记
   （docstring / 注释里的 "placeholder" 不算）、输入校验与边界检查密度；测试走 AST
   —— 用例数、断言数、数值断言（比较式含数值常量或调用/算术）、占位断言占比、解析锚
   词命中，并可选吃 junit 汇总判执行成败；文档走文本 —— 精度声明数值、适用范围词、
   弱等级自述、验证工件里的失败语义。
3. **判定层**（`engine.py`）：显式级联规则（每条分支写进 `reasons`，可回溯）：
   无绑定 → `NOT_SUPPORTED`；空壳占优 → `STUB`；硬编码/未播种随机 → `MOCK`；
   验证工件判失败 → `WITHHELD`（文档级失败只能否决 REAL，不足以直接判 MOCK）；
   三源齐全但无数值验证 → `WITHHELD`；全部通过 → `REAL`。
   默认口径 `policy=evidence` 要求「数值误差 ≤ 3.0%」的
   正向验证；`policy=presence` 退回任务书原口径（三源齐全即 REAL）用于口径对照。
4. **对账层**：`rank(derived) < rank(declared)` → OVERCLAIM，其中
   derived ∈ {NOT_SUPPORTED, STUB, MOCK} 记 **HARD**（有正面反证），
   derived = WITHHELD 记 **SOFT**（证据不足），两者分列；反向为 UNDERCLAIM。
5. **建议层**：每个能力给出方向（DOWNGRADE/HOLD/UPGRADE）+ 可执行动作码
   （`REPLACE_STUB` / `REMOVE_UNSEEDED_RANDOM` / `ADD_NUMERIC_TESTS` /
   `ADD_TEST_ASSERTIONS` / `FIX_FAILING_TESTS` / `ADD_DOC` / `FIX_DOC_CONFLICT` /
   `BIND_SOURCES` / `ADD_INPUT_VALIDATION`），并附动作释义。
6. **审计层**：逐能力记录 sha256 链（`payload_sha256` → `record_sha256(prev,payload,seq)`），
   报告锚 = 语义载荷（剔除 `generated_at`/`validation`/`reproducibility`/`anchor`）
   的 sha256；因此**换生成时间重跑锚不变**，改任何一个判定字段锚即变（有篡改检测单测）。

## ③ 计算结果（数字表）

| 判据 | 目标 | 实测 | 判定 |
|---|---|---|---|
| (a) 扫描覆盖率 | ≥95% | 1.0000（21/21） | PASS |
| (b) 过度声明准确率（合成金标准 12 项） | ≥90% | 1.0000（P=1.0, R=1.0） | PASS |
| (b') 过度声明准确率（真实台账，vs reconciled_level） | ≥90% | P=0.1538, R=1.0 | FAIL |
| (c) 等级一致率（合成金标准） | ≥85% | 1.0000 | PASS |
| (c') 等级一致率（真实台账） | ≥85% | 0.3333（仅 impl 已绑定子集 0.4375） | FAIL |
| (d) sha256 审计锚可复现 | 是 | True | PASS |
| (e) 报告 JSON schema 校验 | 通过 | True（jsonschema Draft7） | PASS |
| (f) 零领域语义硬编码 | 通过 | True（真实台账 id/域标签字面量零命中） | PASS |

真实台账的派生分布：`{'NOT_SUPPORTED': 5, 'STUB': 0, 'MOCK': 0, 'WITHHELD': 9, 'REAL': 7}`；声明分布：`{'NOT_SUPPORTED': 2, 'REAL': 18, 'STUB': 1}`；
过度声明 13 项（硬 4 / 软 9，
率 0.6190）；未绑定实现 5 项
（`dev.git`, `dev.logs`, `dev.restart`, `dev.services`, `methods.computation`）。

**审计锚**：`bd7549672a500c4cd4b54c776f740ab37e628a3867a45d8408094706f23b55e7`（链长 21，链头见 `artifacts/capability_reconcile/audit_log.json`）。

## ④ 结果图

- `fig1_levels.png`：声明 vs 派生等级分布（21 项台账）
- `fig2_confusion.png`：派生 vs 外部裁决混淆矩阵（对角线=一致）
- `fig3_deltas.png`：逐能力等级偏差（负=过度声明，红=降级建议）

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **引用命中 ≠ 实现**：首版按「文件里出现能力 id」定位实现，单项能力一次卷进 20+ 个
   文件（注释/表格里提到 id 的都算），MOCK 假阳性爆表。改为 idf 加权文件名匹配 +
   聚合器剔除后，硬过度声明从 17 项降到 4 项。
2. **docstring 里的 "placeholder" 不是硬编码**：首版在模块说明文字上判 MOCK；
   改为 tokenize 剔除注释/字符串行后才正确。
3. **文档级失败语义只能否决 REAL**：验证工件写「误差 30%、honest FAIL」时首版直接判
   MOCK，与「MOCK 需代码级反证」不符，改判 WITHHELD（软过度声明）。
4. **判定范围**：树外实现（如外部 agent harness 的工具能力）在本引擎视野外，只能记
   `NO_SOURCE_BINDING` 并要求 `--bindings` 显式绑定；这不是「能力不存在」的证据。
5. **一次性口径差异**：真实台账 `reconciled_level` 的证据合同口径要求「注册证据行」，
   与三源存在性口径不是同一把尺，直接对比会系统性偏低（见 §6）。

## ⑥ 误差判定（关键量 vs 基准，逐项显式回答）

| 关键量 | 基准 | 实测 | 是否达 3%/判据内 |
|---|---|---|---|
| 合成语料等级一致率 | 人工金标准 12/12 | 1.0000 | 是（100%） |
| 合成语料过度声明准确率 | 人工金标准 | 1.0000 | 是（100%） |
| 真实台账等级一致率 | reconciled_level | 0.3333 | **否（差 85% 目标 0.5167）** |
| 真实台账过度声明准确率 | reconciled_level/divergence | 0.1538 | **否（差 90% 目标 0.7462）** |
| 审计锚复现 | 两次不同时间戳重跑 | 一致 | 是（0 差异） |

**honest FAIL 声明**：真实台账上的 (b)(c) 未达标（0.1538 / 0.3333）。
分歧共 14 项，按方向归类（派生→裁决）：

- `REAL->WITHHELD`：7 项（ai.predict, geometry.generate, geometry.hydro_curves, geometry.verify_official, misc.stubs, shiphull.generate, stdmodels.lookup）
- `NOT_SUPPORTED->WITHHELD`：5 项（dev.git, dev.logs, dev.restart, dev.services, methods.computation）
- `WITHHELD->REAL`：1 项（calc.stability）
- `WITHHELD->MOCK`：1 项（propeller.open_water）

根因不是实现缺陷而是**口径与视野**：(i) `derived=REAL -> truth=WITHHELD` 的项，引擎在树内
找到了 ≤3% 的数值验证表述，而台账尚未为其注册证据行（引擎发现的是台账缺口，不是虚报）；
(ii) `derived=NOT_SUPPORTED -> truth=WITHHELD` 的项是树外 harness 工具，属绑定缺失。
两类的正确处置都是**进台账/补绑定后复判**，不应把本引擎的派生等级直接当作最终裁决；
本引擎的定位是「过度声明**筛查器**」（硬过度声明 4 项全部指向台账 REAL 而树内无实现，
召回 1.0），终裁仍归证据合同。
