# 能力成熟度自治管线：从「人工升账」到机器可判的证据闭环

**节点** p156w2-n07-capability-maturity-pipeline · **仓库** TensorPlatform · **分支** `agent/m10876`
**报告数据** `artifacts/capability_maturity/report.json`（sha256 `4533f271…0779d`，程序化注入零手抄）
**代码** `tools/capability_maturity_pipeline/`（`chain.py` / `contradiction.py` / `ledger.py` / `pipeline.py` / `cli.py`）
**测试** `tests/capability_maturity_pipeline/`（30 项）· **定期执行壳** `ops/capability_maturity_loop.sh`

---

## 1. 案例介绍（物理/工程背景与对象）

科研平台的能力等级（REAL / WITHHELD / MOCK / STUB / NOT_SUPPORTED）此前靠人工审计：
读报告、看误差、拍板升账。人工升账有三个结构性缺陷：

1. **不可复算**——同一个证据集，不同人给出不同等级；
2. **单向偏置**——升账有动力记账，被推翻的旧判定往往没人回去改；
3. **不可追溯**——「凭什么给 REAL」只留在人的记忆与散文里。

本节点把这件事做成**机制**：一个无人工裁量点的闭环，输入是工件目录里的证据，
输出是带哈希链的台账，任何第三方都能重算并验证「这条等级是怎么来的、有没有被改过」。

闭环五段与对应实现：

| 段 | 实现 | 关键设计 |
|---|---|---|
| 证据扫描 | `pipeline.EvidenceScanner` | 受限根目录、大小上限、排除自身输出、单文件异常不中断、按 sha256 去重 |
| 矛盾检测 | `contradiction` | 「同一个量」身份 = (能力, 指标, 参照值)；推翻需 新证据更晚 + 偏离 > 预声明阈值 |
| 自动判定 | `capability_ledger_maturity.AdjudicationEngine`（复用） | 阈值指纹入链，事后不可改判；误差 ≤3%→REAL，>15%→MOCK/FAIL |
| 台账更新 | `ledger.MaturityLedger` | 每能力记录 level/decided_at/event_seq/evidence_refs/rule_fired/thresholds_sha256/history |
| 回滚 + 审计 | `ledger` + `chain` | 推翻→`ROLLBACK`（附 `rollback_of` 变更链）；追加式哈希链 + `<ledger>.head` 锚 |

**参数表（预声明判据，运行前固定）**

| 参数 | 值 | 说明 |
|---|---|---|
| REAL 误差门 | 3.0% | `AdjudicationThresholds.real_error_pct` |
| WITHHELD 上限 / MOCK 门 | 15.0% | `withheld_error_pct` / `mock_error_pct` |
| 矛盾偏离阈值 | 1.0% | `ContradictionPolicy.deviation_threshold_pct` |
| 最大成对比较分组 | 64 | 超限跳过并单独统计（防 O(n²) 误报） |
| 文件大小上限 | 2 MB | 扫描上限 |
| 阈值指纹 | `thresholds_sha256` | 写入每条事件 payload |

---

## 2. 怎么做的（方法/流程/关键设置）

### 2.1 决策函数（机器可判，无人工裁量）

对能力 `c`，记台账当前等级 `L_old`、裁定引擎结果 `L_new = f(证据, 阈值)`、被推翻基座集合 `R`：

```
INIT      台账无 c
UPGRADE   rank(L_new) > rank(L_old)
ROLLBACK  rank(L_new) < rank(L_old) ∧ R ≠ ∅ ∧ R ⊆ 基座证据    ← 「先前判定被推翻」
DOWNGRADE rank(L_new) < rank(L_old) ∧ (R = ∅ ∨ R ⊄ 基座证据) ← 「新证据本身更差」
REBASIS   L_new = L_old ∧ 基座证据集合变化
HOLD      L_new = L_old ∧ 基座不变  → 不写链（幂等）
```

`ROLLBACK` 与 `DOWNGRADE` 的区分是本节点的核心信息量：前者说「我们之前信错了」，
后者说「情况变差了」。把两者混为一谈，回滚就失去意义。

### 2.2 矛盾检测的可比性判据

「同一个量」的身份：有参照值 → `(能力, 指标名, 参照值)`；无参照值 → `(能力, 指标名, 来源文件)`。
同一批证据内部的冲突告警要求**两侧都有参照值**（否则无法判断是否在测同一个量，误报会爆炸——
实跑验证：不加此约束时 692 条证据产生 30700 组「冲突」，加约束后为 0）。

### 2.3 审计链与锚点

```
event.sha256 = SHA256(canonical(seq, event_type, capability_id, payload, prev_sha256, timestamp))
```

台账落盘时同步写 `<ledger>.head`：`{chain_head_sha256, chain_length, generation, ledger_sha256}`。
**为什么需要锚**：仅有 prev 链接时，删掉末尾 k 条事件后的链自身仍然自洽（见 §5 陷阱 1）。
锚把「链长 + 链头」钉在链外，尾部截断才能被发现。

### 2.4 工具链与复现

```bash
python3 -m pytest tests/capability_maturity_pipeline/ -q          # 30 passed
python3 scripts/p156w2_n07_maturity_pipeline_report.py            # 重算全部判据 + 4 张图
python3 -m tools.capability_maturity_pipeline.cli verify --ledger artifacts/capability_maturity/ledger.json
```

时钟注入（`clock=`）用于确定性复现：**证据观测时间统一记为本轮扫描时刻**，
避免文件解析时刻抖动污染指纹；跨轮次观测顺序因此单调，正好是矛盾检测需要的语义。

---

## 3. 计算结果（数字表，全部由 report.json 程序化注入）

### 3.1 实跑：仓库真实工件（`artifacts/ runs/ tensor_reports/ docs/`）

扫描漏斗：**981 个文件被识别 → 217 个文件产出证据 → 692 条证据项 → 672 条可归属
（97.14%）→ 5 个能力被判定 → 5 条台账事件**，未归属 20 条（2.86%），扫描异常 0，
端到端 6.4 s。

| 能力（归因规则给出） | 等级 | 触发规则 | 证据数 | 最大误差 | 置信度 |
|---|---|---|---|---|---|
| `cap.platform.ail_benchmark` | **REAL** | R6_REAL_ACHIEVED | 363 | 1.00% | 1.00 |
| `cap.platform.benchmark_validation` | MOCK | R2_MOCK_SIGNAL | 12 | 68.89% | 0.90 |
| `cap.platform.capability_ledger` | MOCK | R2_MOCK_SIGNAL | 2 | 237.00% | 0.90 |
| `cap.platform.documentation` | MOCK | R4_ERROR_EXCEEDS_MOCK | 286 | 226415.00% | 0.85 |
| `cap.platform.mission_reproduction` | MOCK | R4_ERROR_EXCEEDS_MOCK | 9 | 82.23% | 0.85 |

审计链：`generation = 5`，`chain_length = 5`，`chain_head = 622bebf5…dd3`，
链校验 `ok = true`（含锚点），台账↔链一致性 issues = 0。

### 3.2 消融：全证据 vs 仅结构化证据

| 指标 | 全证据 | 仅 benchmark |
|---|---|---|
| 证据项 | 692 | 486 |
| `cap.platform.mission_reproduction` | MOCK（max 82.23%） | **REAL** |
| `cap.platform.capability_ledger` | MOCK | 无证据（不判） |

→ **文本报告（`.md`）里的数字是散文正则抽取，会把能力压成 MOCK**；同一能力组在
仅结构化证据下变为 REAL。结论：可判证据必须是结构化字段（`error_pct` + `reference` + `tolerance`），
文本数字只能做线索。这也是本节点给平台的一条改进建议（§6）。

### 3.3 受控实验：自动回滚通路（真实工件副本 + **注入**退化，明确标注）

沙箱 `artifacts/capability_maturity/controlled/`，工件结构照抄真实工件，误差字段为注入值。

| 波次 | 注入 | 判定 | 链事件 | rollback_of | 被推翻证据 |
|---|---|---|---|---|---|
| wave1 | error = 1.0%（ref 100） | INIT **REAL** | seq 0 | — | — |
| wave2 | error = 12.0%（同量，更晚） | **ROLLBACK** REAL→WITHHELD | seq 1 | `[0]` | 1 条（偏离 1100.000%） |
| wave3（极性对照） | error = 12.0% 但 ref 250（**不同算例**） | **DOWNGRADE** REAL→WITHHELD | — | `[]` | 0 条 |

极性对照是关键：同样是 12% 误差，**只有同量被推翻才回滚**，换了算例只会降级。

### 3.4 审计链篡改演练（4/4 全部检出）

| 攻击 | 结果 | 断点 |
|---|---|---|
| 改写事件 payload（seq0 to_level→MOCK） | 检出 | broken_at = 0（digest mismatch） |
| **尾部截断**（删最后 1 条） | 检出 | 锚点比对 `1 < anchored 2`；**无锚时不可见** |
| 伪造追加事件 | 检出 | broken_at = 2 |
| 手工改台账等级（链未动） | 检出 | `ledger level REAL != chain to_level WITHHELD` |

### 3.5 幂等、确定性与耗时

| 项 | 值 |
|---|---|
| 第二轮同证据 | 新增事件 **0**，链头与世代不变，动作集 = ∅（HOLD） |
| 确定性（两轮独立工作树） | `core_sha256` = `1e1b8e4c…0310` 完全一致，链头一致，等级一致 |
| 单轮耗时（沙箱） | mean 0.0028 s / max 0.0032 s（5 次） |
| 单轮耗时（全仓实跑） | 6.43 s |

### 3.6 独立复算能力审计（把缺口量化）

对 672 条可归属证据的基座做统计：**带参照值的证据仅 1 条，可做跨来源同量对照的组合 0 组**。
即：平台现有工件中，几乎没有「同一个量被两条独立路径测过」的证据。
这不是检测器的缺陷，而是证据组织的缺陷——它也解释了为什么实跑只出现 INIT、
而矛盾/回滚通路的验证必须靠受控实验。

---

## 4. 结果图

见 `artifacts/capability_maturity/figures/`：

| 图 | 内容 |
|---|---|
| `fig1_evidence_funnel_and_grades.png` | (a) 证据漏斗 981→217→692→672→5→5（symlog）；(b) 各能力机器判定等级（颜色=等级，高度=证据数） |
| `fig2_ablation_text_vs_structured.png` | 全证据（实心）vs 仅结构化（淡色）的等级对比——文本证据如何拉低判定 |
| `fig3_auto_rollback_change_chain.png` | (a) 三波注入误差 vs 3%/15% 判据线与三波判定；(b) 从哈希链重放出的等级变更链（ROLLBACK 标注 `rollback_of`） |
| `fig4_audit_integrity_idempotency.png` | (a) 篡改演练 4/4 检出与断点；(b) 重复扫描链长恒定（幂等）；(c) 证据来源构成 |

> 说明：本节点是平台机制类工作，没有 CFD/FEA 场量云图；图全部为**判定/审计过程的
> 定量可视化**（等级、阈值、链事件、检出率），数据均由 `report.json` 程序化注入。

---

## 5. 注意点（踩坑、局限、适用范围）

**踩过的坑（都已在代码/测试里固化）**

1. **尾部截断哈希链天然无感**：只做 prev 链接时，删掉末尾事件后的链仍然自洽
   （演练里 `detected_without_anchor = false`）。必须加链外锚点 `<ledger>.head`。
2. **空台账锚点校验越界**：`expected_length = 0` 时取 `events[-1]` 抛 `IndexError`
   ——由定期扫描壳（首轮无可归属证据）真实触发后修复，并加回归测试 `test_empty_ledger_anchor_verify_is_safe`。
3. **矛盾检测不加可比性约束会爆炸**：实跑 692 条证据一度产生 30700 组「冲突」，
   根因是不同算例的同名误差被当成互相矛盾。引入 (能力,指标,参照值) 身份键后归零。
4. **复用模块的崩溃点**：`EvidenceCollector._parse_benchmark_content` 遇到顶层为 list 的
   JSON 会 `AttributeError`（真实工件里就有），已在上游修复并让扫描器对单文件异常隔离。
5. **自我引用污染**：管线若不排除自己的输出目录，会把上一轮的台账/工件当成新证据。

**局限（诚实清单，勿外推）**

- **归属是前提**：672 条归属证据中显式 `capability_id` 为 **0** 条，全靠归因规则；
  没有归属就没有升账，管线只能报告缺口。能力名由 `--attribution` 数据注入，代码零领域/能力名。
- **实跑无回滚、无冲突**：真实数据里 0 组可比同量 → 回滚通路只能靠受控实验验证（§3.3）。
- **聚合口径偏保守**：裁定引擎按「该能力全部证据的最大误差」聚合，异构指标混在一起会压低等级
  （`cap.platform.documentation` 最大误差 226415% 来自散文数字）。
- **锚点单点**：`<ledger>.head` 与台账同目录，同时被改则锚失效；上线需把
  `chain_head_sha256` 随提交/平台侧存证固定（运行工件已记录）。
- **阈值本身未被验证**：3%/15%/1% 是预声明约定，本节点只保证「按声明执行且不可事后改判」，
  不主张这些数值对特定领域最优。

---

## 6. 误差判定（逐项对基准，显式回答是否 <3%）

平台机制类工作的「基准」= 不变量与人工/独立复算，而非实验测量值。

| 关键量 | 基准 | 实测 | 误差 | <3%？ |
|---|---|---|---|---|
| 链完整性（篡改检出） | 4 类攻击全检 | 检出 4/4 | 0% | **是（0%）** |
| 重复扫描幂等（新增事件数） | 0 | 0 | 0% | **是（0%）** |
| 判定可复现性（两轮 core_sha256） | 应完全一致 | 完全一致 | 0% | **是（0%）** |
| 台账↔链一致性 | issues = 0 | 0 | 0% | **是（0%）** |
| 回滚极性（同量推翻 vs 换算例） | 4 个场景期望动作全对 | 4/4 正确 | 0% | **是（0%）** |
| 证据归属率 | 100% | 97.14%（672/692） | 2.86% | **是（2.86% < 3%）**，但缺 20 条未归属 |
| 与「人工审计等级」一致率 | 需独立人工基线 | **无基线** | 不可计算 | **不适用**（见下） |

**关于「与人工审计一致率」**：本节点判定的 5 个能力组是本次新引入的归因分组，
平台不存在对应的人工审计基线；用自己构造的标签去比自己的判定没有意义，
因此**不声明一致率**（不粉饰）。替代的客观判据是「可复现性 100% + 极性 4/4 + 幂等 100%」。

**honest FAIL（不可覆盖）**：实跑数据**没有触发任何 ROLLBACK/冲突**，回滚能力在真实工件上的
有效性**未被证实**，只被受控实验证实（真实工件副本 + 明确标注的注入退化）。
结论：机制通路成立，真实数据上的触发频次未知，需在平台工件开始携带
`capability_id` + `reference` 后才能观测。

---

## 7. 交付物与验收判据对照

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| C1 证据扫描闭环 | 扫描全通、异常不中断 | 981 文件 / 692 证据 / 0 异常 / 归属 97.14% | PASS |
| C2 自动判定确定性 | 同输入同结论，无需人工复核 | 两轮 `core_sha256` 一致 | PASS |
| C3 台账更新可追溯 | 等级/时间/证据来源/判定依据齐备 | 5 事件，字段齐备（9 字段），一致性 0 issue | PASS |
| C4 自动回滚 | 证据推翻→回滚+变更链 | wave2 `ROLLBACK` `rollback_of=[0]`；极性对照 `DOWNGRADE` | PASS |
| C5 审计链防篡改 | 篡改可检出并定位 | 4/4 检出，断点定位正确 | PASS |
| C6 幂等 | 重复扫描不写链 | 新增事件 0，链头不变 | PASS |
| C7 端到端耗时 | < 5 min | 沙箱 0.003 s / 全仓 6.43 s | PASS |
| C8 零领域语义 | 模块源码无领域词汇 | pytest 红/绿扫描 0 命中 | PASS |
| C9 pytest | ≥20 项全绿 | 30 passed | PASS |

**建议（下一步，不在本节点范围）**：① 让工件带 `capability_id`；
② 把结构化证据统一为 `{metric, value, reference, tolerance}` 三件套（当前带参照值仅 1 条）；
③ 把 `<ledger>.head` 锚随提交固定，避免单点；
④ 定期壳 `ops/capability_maturity_loop.sh` 上线后，用触发频次数据回头校准阈值。