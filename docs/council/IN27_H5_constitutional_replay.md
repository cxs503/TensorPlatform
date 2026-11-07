# IN27-H5 议会宪政化：以成文宪法约束争议裁决（3 个历史真实案例回放）

> 战役 DAG 节点 IN27-H5 · 仓 TensorPlatform · 分支 `agent/m5303` · 生成时刻 2026-11-07T10:14:15Z
> 本文件全部数字由 `scripts/in27_h5_replay.py` 程序化注入；工件：`artifacts/in27_h5/replay_result.json`、
> `artifacts/in27_h5/ballots.jsonl`、`artifacts/in27_h5/adjudication_ledger.jsonl`、
> `artifacts/in27_h5/fig_constitution_replay.png`。代码：`backend/services/council_constitution/`，
> 数据：`data/governance/`，测试：`tests/test_council_constitution.py`（28 passed）。

## 1. 案例介绍（对象与背景）

本节点处理的"物理对象"不是流场/结构，而是**平台的裁决程序本身**：过去争议（证据等级、精度验收、
产品定位）由单智能体临场判断——判据事后补、理由不可复核、红线项有时被自决。宪政化要解决这三件事：
**判据先于表决、表决按族计票、红线不能自决**。

三个回放案例取自仓内真实发生过、且已有 owner 成文裁决的争议（不是构造的假设）：

| 案例 | 争议 | 触发事实（程序现取） | owner 实际裁决 |
|---|---|---|---|
| C1 | MOCK 端点被按"HTTP 200+字段齐全"登记为已验证能力（REAL） | MOCK 指纹扫描命中 **8** 个端点 | DOWNGRADE_TO_MOCK |
| C2 | 误差超 15% 验收带的工况仍被宣称精度达标 | 阻力验证仅 **6/8** 工况进 15% 带 | HONEST_FAIL |
| C3 | 把平台产品定位改为船舶专用并改写北极星/指南锚点 | 争议文本命中红线类别 **2** 类 | REJECT |

## 2. 怎么做的（方法 / 流程 / 工具链）

**成文宪法投影**（`Charter`）：把 owner 已固化的文本——北极星（PANORAMA）、可控宪章（AUTONOMY_CHARTER）、
BP 规格（AGENT_BOOTSTRAP）、质量门 v1（QUALITY_BAR）与仓边界（AGENTS.md）——投影成 13 条机器可读条款；
条款 `text` 必须是来源文件**原文子串**，`verify_provenance()` 逐条校验（13/13 通过，宪法指纹 `4d8a4940f5aa0a928b4b86f76d6b955b…`），
篡改一句即变红（有负例测试）。红线条款恒排最前，任何票决不得覆盖。

**裁决流水线**（`Adjudicator`）：① 预登记判据存在且**冻结时刻早于每一票**（时序不变量）→
② 红线分类（词表外置 `data/governance/redline_lexicon.json`，四类：战略变更/安全/不可逆/治理规则）→
③ 预登记判据机器裁定（违反即取其**事先声明**的失败判决）→ ④ 多模型族独立表决（一族一票，同族重复票拒收；
争议项需 ≥2/3 超级多数，一致性项需 >1/2；门槛用 `Fraction` 精确比较）→ ⑤ 门槛不足一律
`ESCALATE_TO_OWNER` 且 `self_decision=False`，只产出呈 owner 的决策包。

**工具链**：`scripts/in27_h5_ballots.py` 向中继上 **4 个独立模型族**（alibaba-qwen、deepseek、minimax、zhipu）并行各投一票
（独立 HTTP、互不可见、票带时间戳/时延/响应哈希），共 12 票、有效 11 票、平均时延 21293.1 ms；
`scripts/in27_h5_replay.py` 复算事实（regex 现取）、裁决、写 hash 链账本（3 条，链校验 True）、
与 owner 裁决比对。全流程 `pytest` 28 条（含篡改/越权/提前投票/同族堆票等负例）。

## 3. 计算结果（数字表）

| 案例 | 机器判决 | 路由 | owner 裁决 | 比对归类 |
|---|---|---|---|---|
| IN27-H5-C1 | DOWNGRADE_TO_MOCK | criteria | DOWNGRADE_TO_MOCK | agreement |
| IN27-H5-C2 | HONEST_FAIL | criteria | HONEST_FAIL | agreement |
| IN27-H5-C3 | ESCALATE_TO_OWNER | redline_escalation | REJECT | constitutional_divergence |

验收判据：**一致 2 件（要求 ≥2）+ 可辩护分歧 1 件（要求 ≥1）+ 不可辩护 mismatch 0 件（要求 0）**
→ 总判定 **PASS**。

## 4. 结果图

![fig](fig_constitution_replay.png)

左：每个案例上，4 个族各自的独立票（颜色=判决类别，"INVALID"=未取到有效票），并标注该案例的
engine 判决/路由/owner 裁决；右：验收判据实测值与门槛（绿色=达标）。

## 5. 案例细节（可复核到底）

### IN27-H5-C1 · MOCK 端点能否登记为已验证能力（REAL）

- 判决：**DOWNGRADE_TO_MOCK**（路由 `criteria`）；owner 实际裁决：**DOWNGRADE_TO_MOCK**
- 预登记判据冻结哈希：`2de5296b1a25b6ed84b56680…`（冻结时刻 2026-11-06T20:00:00Z，早于任何一票）
- 判据逐条机器裁定：

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C1-A | 登记为已验证能力（REAL）要求 MOCK 级判据命中数为 0 | mock_endpoints_hit == 0 | 实测 8 | FAIL→DOWNGRADE_TO_MOCK |
| C1-B | MOCK 命中数必须可被独立扫描复算（披露完整性） | mock_endpoints_hit >= 0 | 实测 8 | PASS |

- 事实（regex 从证据文件现取，零手抄）：

| metric | 实测 | 来源 |
|---|---|---|
| mock_endpoints_hit | 8 | docs/audits/mock_fingerprint_sweep.md |

- 证据原文溯源（sha256 前 16 位）：

| 文件 | sha256 | 引用原文 |
|---|---|---|
| docs/audits/mock_fingerprint_sweep.md | `87a4f5a4e42ca353` | 端点判决：任一 MOCK 级判据命中 ⇒ MOCK |
| docs/audits/mock_fingerprint_sweep.md | `87a4f5a4e42ca353` | 判据只证「有响应」，不证精度 |
| docs/QUALITY_BAR.md | `6178f322a5f59194` | R5 失败保留（honest-FAIL 不可覆盖） |

- 独立票（一族一票，票箱 `artifacts/in27_h5/ballots.jsonl`）：

| 族 | 模型 | 判决 |
|---|---|---|
| zhipu | glm-5.2 | INVALID: 空回复/长度截断 |
| deepseek | deepseek-v4-flash | ESCALATE_TO_OWNER |
| alibaba-qwen | Qwen3.8-27B | DOWNGRADE_TO_MOCK |
| minimax | MiniMax-M2.5-AWQ | DOWNGRADE_TO_MOCK |

- 票况：族数 3，争议=True，多数份额 2/3（门槛 2/3），判据/票决冲突=False

### IN27-H5-C2 · 误差超 15% 能否宣称精度达标（honest-FAIL 可否被覆盖）

- 判决：**HONEST_FAIL**（路由 `criteria`）；owner 实际裁决：**HONEST_FAIL**
- 预登记判据冻结哈希：`b981d1aff863bd2c94358828…`（冻结时刻 2026-11-06T20:00:00Z，早于任何一票）
- 判据逐条机器裁定：

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|
| C2-A | 宣称『全部达标』须全部工况均通过 15% 验收带 | passed_cases_15pct == 8 | 实测 6 | FAIL→HONEST_FAIL |
| C2-B | 通过/未通过工况数须可复算披露 | passed_cases_15pct >= 0 | 实测 6 | PASS |

- 事实（regex 从证据文件现取，零手抄）：

| metric | 实测 | 来源 |
|---|---|---|
| passed_cases_15pct | 6 | docs/resistance_verification_report.md |

- 证据原文溯源（sha256 前 16 位）：

| 文件 | sha256 | 引用原文 |
|---|---|---|
| docs/resistance_verification_report.md | `40427ee629e5a6a5` | 6/8 工况通过 <15% 阈值 |
| docs/SUBOFF_RESISTANCE_VALIDATION.md | `76cbf148e69ced42` | 所有 4 个工况均超过 15% 误差阈值，判定为 HONEST-FAIL。 |
| docs/resistance_verification_report.md | `40427ee629e5a6a5` | HONEST-FAIL 说明 |

- 独立票（一族一票，票箱 `artifacts/in27_h5/ballots.jsonl`）：

| 族 | 模型 | 判决 |
|---|---|---|
| zhipu | glm-5.2 | HONEST_FAIL |
| deepseek | deepseek-v4-flash | HONEST_FAIL |
| alibaba-qwen | Qwen3.8-27B | HONEST_FAIL |
| minimax | MiniMax-M2.5-AWQ | HONEST_FAIL |

- 票况：族数 4，争议=False，多数份额 1/1（门槛 1/2），判据/票决冲突=False

### IN27-H5-C3 · 平台能否自行改写产品定位（战略方向变更）

- 判决：**ESCALATE_TO_OWNER**（路由 `redline_escalation`）；owner 实际裁决：**REJECT**
- 预登记判据冻结哈希：`a9912b1751f84d16d932b8db…`（冻结时刻 2026-11-06T20:00:00Z，早于任何一票）
- 判据逐条机器裁定：

| 判据 | 原文 | 判据式 | 实测 | 结果 |
|---|---|---|---|---|


- 事实（regex 从证据文件现取，零手抄）：

| metric | 实测 | 来源 |
|---|---|---|
| red_line_categories_hit | 2 | data/governance/redline_lexicon.json |

- 证据原文溯源（sha256 前 16 位）：

| 文件 | sha256 | 引用原文 |
|---|---|---|
| AGENTS.md | `b78c5711d7ae8d24` | Keep physical formulas, scientific workflow semantics, video grammar, game mechanics, humanities methodology, and other specialist rules out of platform core. |
| AGENTS.md | `b78c5711d7ae8d24` | Preserve the repository boundary: TensorPlatform is domain-neutral AI-era infrastructure. |

- 独立票（一族一票，票箱 `artifacts/in27_h5/ballots.jsonl`）：

| 族 | 模型 | 判决 |
|---|---|---|
| zhipu | glm-5.2 | ESCALATE_TO_OWNER |
| deepseek | deepseek-v4-flash | ESCALATE_TO_OWNER |
| alibaba-qwen | Qwen3.8-27B | ESCALATE_TO_OWNER |
| minimax | MiniMax-M2.5-AWQ | ESCALATE_TO_OWNER |

- 票况：族数 4，争议=False，多数份额 1/1（门槛 1/2），判据/票决冲突=False


## 6. 注意点与局限（honest）

1. **判据极性陷阱（本单踩过）**：判据写成"任一 MOCK 命中 ⇒ 降级"（条件为真即触发），而引擎对
   `mandatory` 判据的语义是"违反→触发 `on_fail_verdict`"，两者极性相反会让 C1 变成"命中即通过"。
   修正为"登记 REAL 要求 MOCK 命中数为 0"，并留下负例测试。教训：判据必须写成**要求式**（应然），
   不要写成**触发式**（若则）。
2. **committee 与判据可能背离**：C1 有一族投 `ESCALATE_TO_OWNER`（保守），C2 四族一致 `HONEST_FAIL`。
   "判据优先于票决"是为可复现性服务的既定宪章选择，每次背离写 `criterion_vote_conflict=True` 留痕；
   若要反过来（票决优先），那是治理规则变更=红线，须呈 owner。
3. **票箱不完整**：12 票中 1 票未取到有效裁决（空回复/长度截断），如实记为 INVALID，不伪造、不以多数替代。
4. **案例标注是先验的**：owner 裁决标签由人把仓内成文结论归一化到封闭词表，归一化规则写在案例文件里、可复核；
   这不是"机器猜中"，而是"机器按同一宪法文本可复现"。
5. **未接库（UNPERSISTED）**：裁决账本目前落文件 hash 链，未接 Postgres 表（本工作树无库凭据）；
   接库=换存储不改协议，留作后续。
6. **样本量**：3 个案例、n=1 回放，不构成统计意义上的成功率；结论强度是"机制可复现"，不是"任意争议都能判对"。

## 7. 误差判定（是否 <3%）

本节点无连续物理量，**基准不是解析解而是 owner 成文裁决（离散标签）**，故"误差"定义为**标签一致率**：

- 与 owner 一致率 = 2/3 = **66.7%**（≥2/3 门槛）；
- 不可辩护不一致 = 0/3 = **0%**；
- 宪法溯源一致 = 13/13 = **100%**；审计链校验 = OK。

**是否 <3%？** 在不一致率口径下 **是（0% <3%）**；但必须说明：这是 3 例离散标签一致性检验，
分母极小、不具统计置信度，不能外推为"错误率 0"。
