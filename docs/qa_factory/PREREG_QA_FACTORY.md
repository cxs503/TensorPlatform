# QA 产线化 预注册（PREREG）— FEAT-QA-FACTORY 第 1 轮

> 纪律（experimental-design 技能）：**判据先于动手**。本文件在跑全量语料与抽样深检**之前**写定，
> 之后的分档阈值、抽样方式、停止规则、验收门不再因结果好坏修改；若必须调整，须在本文件追加
> 「调整记录」段并说明理由，且调整只允许发生一次、必须披露（不得静默调参）。

## 1. 决策（Decision）

是否把「程序化三查机器门」作为 qa_status 清账的**授权器**（machine-pass → passed，带 machine_audit 标记），
即：机器门能否替代/大幅削减 10033 篇 pending 的逐篇人工质检串行段。

竞争方案（alternatives）：
- **A 机器门授权清账**（本单）：机器三查分档，pass 档批量清账，flag/suspect 档进深检队列。
- **B 现状**：全部 pending 逐篇人工/Agent 质检（实测 ~1 篇/分钟量级的串行段，Amdahl 瓶颈）。
- **C 仅机器门做预筛、清账仍需人工**：安全但不解决吞吐（本次不采纳，作为回退）。

## 2. 假设（Hypothesis）

H1：机器三查的 pass 档中，独立深检判 pass 的比例足够高，使得三分类一致性 ≥ 90%（门限见 §5）。
H0：机器判与深检判一致性 < 90% → 机器门**不予投用**，qa_status 清账保持人工路径，如实报（honest FAIL）。

## 3. 三查定义（metrics，判据不可事后改写）

每篇报告三项检查各出 `ok / flag / fail`，证据带行号或计数，全部落 JSON（可复算）。

- **A 结构完整**（technical 报告）：复用 `tools/report_corpus_audit.py` 的六要素确定性判定：
  `PASS→ok`、`MINOR→flag`、`RESUBMIT→fail`。
  **meta 报告**（`scenario_key in {report-qa,qa-review}` 或标题命中质检元报告模式）：
  纪律形态不同（判定表而非物理案例节），改用 `verdict_table_present`：
  有「判定表 + 正文 ≥600 字符」→ ok；有表但过薄 → flag；无判定表 → fail。
- **B 证据指针可解析**：从正文抽取
  ①相对工件路径 `artifacts|docs|runs|tools|src|tests|scripts|references/…`、
  ②绝对路径 `/root/tp-agent/work/m<N>/…`、③报告引用 `报告 #N` / `/tensor/reports/N`、
  ④工作单引用 `mission #N` / `m<N>`；
  逐条判定 `resolved / contradicted / unverifiable`。
  **关键**：指针只在**根可用**时才算可判。根 = 该报告所属 mission 的 worktree `/root/tp-agent/work/m<mid>`
  （存在才可判）+ PG（报告 id / mission id 存在性）。根不可用 → `unverifiable`，**不冤枉报告**。
  `contradicted_ratio = contradicted / (resolved + contradicted)`（分母 0 = 无可判指针）。
  - fail：ratio ≥ 0.5（宣称的工件过半不存在）
  - flag：0 < ratio < 0.5，或分母 0 且未声明「无工件/不可复算」
  - ok：ratio == 0 且 resolved ≥ 1；或分母 0 且**显式声明**工件缺失/无工件
- **C 关键数字可复算或标注不可复算**：抽取「精度承载数字」（含小数 ≥3 位有效、或科学计数、或带 %），
  排除路径/引用/年份/章节编号；每个数字在**同行 ±2 行**内须有溯源线索（工件路径、`L<n>` 行号、
  sql/psql/复算/recompute、程序化/注入、sha256、DOI/文献/Table/参考值/解析解/基线）
  或**显式不可复算标注**（不可复算/手抄/人工输入/假设值/估计）。
  `bound_ratio = bound / key_numbers`。
  - fail：key_numbers == 0（全文无精度数字=无内容），或 bound_ratio < 0.2 且无不可复算声明
  - flag：bound_ratio < 0.6
  - ok：bound_ratio ≥ 0.6
  - meta 报告：数字多为判定计数，C 以「判定表存在」为 ok，否则 flag（口径写入 JSON reason）。

**分档（tier）规则（单调、可红队）**：
`任一 fail → suspect`；`无 fail 但有 flag → machine-flag`；`三项全 ok → pass`。

## 4. 抽样与深检（sample / analysis）

- 分层抽样：`pass / machine-flag / suspect` 三层，各层 `n = 15`（合计 45；层内按
  `sha256(f"{seed}:{report_id}")` 排序取前 n，确定性可复现）。
- 深检执行器：本轮由本单执行者以**独立仪器**逐篇判（读原文摘要+关键表+指针/数字证据，按 §3 同判据
  独立给 tier），并把逐篇判定、证据引文、局限写入 `docs/qa_factory/deep_review_round1.jsonl`。
  规模化路径 = `tools/qa_factory/dispatch.py`（复用 factory 工序 04 硬门：reviewer mission ≠ producer
  mission，复用 report_qa 单形），本轮不批量派发（人员/预算）。
- 一致性统计：三分类精确一致率（主判据）+ 二分类「pass vs 非 pass」一致率（清账安全性判据）
  + Cohen's κ（机会修正，报告但不作门）。

## 5. 门限（predeclared thresholds）

- **G1 主门**：三分类一致率 ≥ **0.90**。
- **G2 安全门**：二分类（pass vs 非 pass）一致率 ≥ **0.90**
  （G2 失败意味着可能把该 suspect 的报告清成 passed，风险不可接受）。
- G1 ∧ G2 都过 → 允许机器门授权清账；任一不过 → **机器门不投用**，清账不执行，如实报 FAIL。
- 统计功效披露：n=45 时 p=0.90 的 95% CI 半宽约 ±0.09。因此本门只做「不投用」的否决器与
  首轮试点放行；要**认证** ≥0.90 须累计到 n≈140（下轮扩样）。

## 6. 停止规则

- 全量扫描 = 一次性 O(批) 作业，无随机性；异常（psql 失败、报告解码失败）即中止并报错，不吞。
- 深检抽样固定 45 篇，**不做序贯加样**（避免"抽到满意为止"的偏倚）。
- 清账批量 = 每批 ≤2000，按 `created_at ASC`（存量优先老报告）；清账前必须存在新鲜（≤24h）
  且 G1∧G2 通过的 consistency JSON，否则拒绝执行（`--force` 需显式声明并写入审计）。

## 7. 环境与可复现

PG `tensorplatform.tensor_reports`（只读扫描）；写：`qa_machine_tier/qa_machine_audit/qa_machine_at/
qa_audit_source`（迁移 `backend/migrations/122_report_qa_machine.sql`）+ `qa_status` 清账。
语料快照以 `corpus_sha256`（id+markdown 的 sha256 聚合）钉扎；机器判定与产物同 sha 绑定。

## 8. 混淆与局限（confounders / limits）

- 存量 worktree 只有 2783 个（mission 数 8233），老报告工件根多已回收 → B 查大量 `unverifiable`，
  这会**系统性**把老报告推向 machine-flag（而非 suspect，规则已如此设计以免冤枉）。
- 静态文本判定 ≠ 内容正确性：机器门只判"纪律形态与证据可追溯性"，不判物理对错；
  物理对错仍归深检/复算环节（本单不声称覆盖）。
- 深检由本单执行者完成，非盲（能看到机器判定）→ 存在确认偏倚；已用「先读原文再对机器理由」的
  顺序缓解，并在报告中披露该局限。
---

## 调整记录（允许一次，必须披露）

### 调整 #1（2026-11-12，全量扫描前，实现修正非判据放宽）
发现两处实现与其声明的意图不一致，按本条一次性修正并披露；**阈值（§3 的 0.5 / 0.2 / 0.6）与门限
（§5 的 0.90）一个都没动**。

1. **数字绑定的窗口**：原实现只认「同行 ±2 行」有溯源线索。但合法数值表的出处常写在**表头**
   （`| 量 | 计算值 | 参考值 |`）或该**章节开头**的溯源声明（「以下数值由 artifacts/x.json 程序化注入」），
   ±2 行窗口会把这类表判成"未绑定"——是实现缺陷。改为三级绑定 `strong(±2行) / table(整表块) /
   section(本章节≤1500字符含溯源声明)`，任一命中即算绑定。
2. **判据常量不算被复算对象**：`<3%`、`≤ 1e-6`、`小于 0.05` 这类以判据形式出现的阈值是实现判据的
   常量，不是测量值，要求其溯源是噪声。改为：数字前 6 字符内有比较符（`< ≤ > ≥ ~ ± 小于 大于 不超过 …`）
   则不计入 key_numbers。

**影响量化（200 篇同集、同判据，修正前后）**：`numbers_unbound` 98→68 篇；pass 档 37→46 篇（18.5%→23.0%）；
全量 10042 篇最终 pass 1730 篇（17.2%）。

### 未调整项（勿再调）
- 分档单调规则、一致性门限 G1/G2=0.90、最小样本量 30、清账按 `created_at ASC`。
- 深检发现 #9977 的指针规则弱点（`resolved≥1 且 contradicted=0` 可被"1 可解析 + 大量不可判"稀释）
  **本轮不改**——改判据须升门版本 v2 并重跑全量，否则等于事后调参。
