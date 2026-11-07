# 评价真实性的红队抽样复算（IN24-E5 · 战役 DAG 评价波 E5）

> 一句话：E1 把 S×E 评价落成了表 + API + 回填，**但评价本身是否可信没有被检验**。
> 本单做「评价的评价」——从 `confirmed` 行分层抽样 12 单，用**独立实现**的证据口径盲序重算
> E 级与 S 级天花板，逐项复算 `grade_reason` 里的数字，再与申报值对照。
> 结论：**抽样 12/12 全对（0 发现）**，12 条复算记录已入案；同时用 46 条受控篡改
> 证明该 0 发现**不是装置空转**（注入故障检出率 46/46）。

## 1. 案件背景与对象（是什么被复核）

- **被复核对象**：`outcome_evaluations` 中 `status='confirmed'` 的 S×E 评价行（E1/m5018 产物）。
  每条记录 = 一个成果（默认 `mission:<id>`）+ 等级 `grade`（S1–S5/S-N）+ 证据级 `e_level`（E0–E4）
  + 理由 `grade_reason`（≥50 字）+ 证据引用 `evidence_refs` + 审查者。
- **本次总体（真实平台库 `tensorplatform`）**：confirmed 30 行，
  分层为 `S1/E1 = 3`、`S1/E2 = 27`（普查口径，非抽样估计）。另有 pending 60 行、rejected 1 行。
- **为什么必须做**：E1 的回填由 `platform-evidence-scan`（机器扫描）产生、`reason_source=rule_derived`。
  机器扫描可能**照着结论找证据**、可能**随事实漂移而过期**、也可能**把执行性成果说成能力增量**。
  评价若形式化，整条「S×E 治理链」（m5018 → m5025）就退化成记账本。

## 2. 方法（怎么做的）

工具链：Python + SQLAlchemy(psycopg) + PostgreSQL 15；单测 pytest；图表 matplotlib。

1. **分层抽样**（`stratified_sample`）：按申报 `(S, E)` 分层，每层至少 1 单，余量按层容量比例 +
   最大余数法分配；同 seed 确定性；抽样上限不超过层容量。`MIN_SAMPLE=10` 写死为验收下限。
2. **盲序**（`blind_payload`）：复算器只吃事实——mission 行 / 报告（是否已发布、正文长度、图数）/
   能力增量（类别、工件数）/ 证据引用可解析性 / 基线声明 / 审查类型。`grade`、`e_level`、
   `grade_reason`、`reviewer`、`reason_codes` **在载荷里结构性地不存在**（键名硬校验，泄漏即 raise）。
   抽样输出按 `sha256(seed:evaluation_id)` 重排 = 盲序；**先重算、后对照**。
3. **独立重算**（不复用回填器代码）：
   - E 级：已发布报告 + 增量记录 → `E2`；仅其一 → `E1`；都无 → `E0`（扫描上限 E2，E3/E4 需外部复现）；
   - S 级天花板：类别映射（独立实现的 `CATEGORY_GRADE`）⊕ 等级门槛（S3 需 E≥2、S5 需 E≥3、
     S4 需独立评审 + 基线、S-N 需 E≥2 + 独立复核）→ 取「可确认」的最高级；
   - 规则漂移：与回填器 `S_BY_CATEGORY` 交叉校验，不一致即报 `RULE_MAPPING_DRIFT`。
4. **理由数字复算**（`parse_reason_numbers`）：从 `grade_reason` 抽出 mission id / 完结时刻 / 贡献者 /
   报告号 / 正文字数 / 图数 / 增量类别 / 工件数，以及「未关联已发布报告」「未登记」等**缺失声明**，
   逐项按事实重算。数字对不上或声明被事实反驳 → `DOWNGRADE_REASON`。
5. **判定策略（只降不升）**：断链证据 → `REJECT_BROKEN_EVIDENCE`；申报超天花板 →
   `REJECT_UPGRADE`；申报 E 高于重算 E → `DOWNGRADE_EVIDENCE`（E 落到重算值）；理由不可复算 →
   `DOWNGRADE_REASON`（降一级，下限 E1）；申报偏低 → `UNDERCLAIM`（只记录，不追认）；全对 → `MATCH`。
6. **入案与回写**：12 条复算记录写 `eval_redteam_rechecks`（迁移 104，`(evaluation_id, run_id)`
   唯一 → 重放幂等）；动作只对 `confirmed` 行生效（reject→`status='rejected'`，
   downgrade→`e_level/grade` 下降 + `review_status='redteam_downgraded'`）。
7. **非空洞性控件**（证明「0 发现」有意义）：
   - **变异控件**：对每张样本注入 4 类单点故障（E 抬一级 / 等级抬到 S5 / 理由字数 +1 / 抽掉增量），
     要求判定器必然报警；
   - **正控**：对平台历史 `rejected` 行重放同一复算器，看能否独立复现否决。

零领域语义：模块只认「记录 / 证据 / 等级」，不含任何学科词；领域词随数据注入（抽样行 `repo` 字段）。

## 3. 计算结果（程序化注入，零手抄）

| 指标 | 数值 |
|---|---|
| confirmed 总体 | 30（`S1/E1 = 3`，`S1/E2 = 27`） |
| 抽样单数 | 12（`S1/E1 = 2`，`S1/E2 = 10`），盲序号 1–12 |
| 覆盖层数 | 2 / 2（总体中所有 (S,E) 层均被抽中） |
| 判定分布 | `MATCH = 12`，其余 0 |
| 不一致案例 | 0；降级 0、否决 0 |
| 规则漂移 | 0（独立映射与回填器映射逐类别一致） |
| 变异控件检出 | 46 / 46 = 100%（M1 12/12、M2 12/12、M3 10/10、M4 12/12） |
| 正控复现 | 1 / 1（历史 rejected 行被独立复算器再次判 reject） |
| 入案记录 | 12 条（`verdict=MATCH, action=none`），重放幂等（第二次跳过 12） |
| pytest | `26 passed`（`tests/test_eval_redteam.py`） |

图 1 见 `artifacts/m5182_eval_redteam/redteam_summary.png`：
（左）分层覆盖：总体 vs 抽样；（中）判定分布 + 注入故障检出率；
（右）盲序下「申报 E」与「重算 E」逐单对照（本单全部重合）。

逐单记录：`artifacts/m5182_eval_redteam/recheck_table.md`、`recheck_run.json`、`rechecks_psql.txt`
（后者为 `psql` 直读 `eval_redteam_rechecks` 的原始输出）。

## 4. 发现的问题（如实入报，含 0 发现也是结论）

1. **本批抽样 0 不一致**：12 单的申报等级、E 级、理由里的每一个数字都与事实对得上，
   证据引用全部可解析（`tensor_capability_deltas:mission=<id>` 与 `/missions/<id>/provenance` 均实存）。
2. **总体退化（重要，属上游问题）**：confirmed 总体只有 S1，S 维无区分度——与 E1/m5018 报告的病一致：
   能力增量账本 100% 申报 `no_substantive_delta`，结构判据只能给执行性下限 S1。
   **因此「S 各级抽样」在物理上不可执行**：S2–S5 无 confirmed 行可抽。本单的诚实表述是
   「按层抽满 2/2 层」，而不是「覆盖 S1–S5」。
3. **正控复现码不同（细节，如实记录）**：唯一 rejected 行（`mission:5025:smoke:rejected:*`）
   平台原否决码是 `REASON_MISSING`（空理由），复算器给出的码是 `BROKEN_EVIDENCE_REF`
   （其 evidence_refs 指向不存在的报告 `1`、且 `grade_reason` 为空）。两者都判 reject，
   但**否决路径不同**——说明「理由缺失」与「证据断链」在审计上是两条独立的把关线，
   也提示该行是冒烟测试留下的合成行，不是真实的 S3 冒升级案例。

## 5. 非空洞性证明（0 发现的可信度从哪来）

| 控件 | 注入 | 期望 | 实测 |
|---|---|---|---|
| M1 | 申报 E 抬一级 | 非 MATCH | 12/12 检出（多为 `DOWNGRADE_EVIDENCE`） |
| M2 | 申报等级抬到 S5 | `REJECT_UPGRADE` | 12/12 |
| M3 | 理由正文字数 +1 | `DOWNGRADE_REASON` + `MISMATCH_REPORT_CHARS` | 10/10（2 单无正文字段，按设计不注入） |
| M4 | 抽掉增量记录 | 非 MATCH | 12/12 |
| GT | 历史 rejected 行重放 | reject | 1/1 |

即：判定器在「申报与事实一致」时给 MATCH，在**任何**单点漂移时必报警；
本批全 MATCH 的原因是数据确实自洽，不是判据空转。

## 6. 局限与适用范围（不粉饰）

1. **样本量**：12/30；S 维只有 S1 一层，故「S 各级+E 各级」实际覆盖为 `S1×{E1,E2}`。
   若要覆盖 S2–S5，须先有 S≥2 的 confirmed 行（上游申报纪律问题，本模块不能自证）。
2. **单仓**：本次 `repo=TensorPlatform`（30 行全部属该仓）；跨仓口径未抽样。
3. **规则口径而非同行评审**：重算用的是 §7.1 结构判据的独立实现；它能查「申报与记录不符」，
   查不出「记录本身造假」（如报告正文内容与结论无关）。后者需人工/跨主体评审。
4. **E3/E4 不可自证**：扫描上限 E2；本次没有 E3/E4 行可复核。
5. **时间点事实**：复算只看「当下平台记录」。若证据在评价之后被删改，本方法会判降级/否决——
   这是**有意的窄口径**（审计要的是当前可复现），但不等价于「评价当时错了」。
6. **正控太薄**：历史 rejected 仅 1 行且为冒烟行；真实的 S3 冒升级案例在库中不存在，
   该类检出能力由变异控件（M2/M4）与单测背书，而非真实案例。

## 7. 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）

| 关键量 | 基准（申报/平台事实） | 复算 | 误差 | <3%？ |
|---|---|---|---|---|
| E 级一致率（12 单） | 12/12 申报值 | 12/12 重算值相同 | 0.00% | **是** |
| S 级一致率（12 单） | 12/12 申报值 | 12/12 重算天花板相同 | 0.00% | **是** |
| 理由数字一致率（10 单含数字字段 × 6 字段） | 逐字段申报值 | 逐字段重算值相同 | 0.00% | **是** |
| 证据引用可解析率 | 全部引用 | 100% 可解析 | 0.00% | **是** |
| 变异控件检出率 | 46 次注入 | 46 次报警（目标 100%） | 0.00% | **是** |

无基准的量：**S 维分布**——总体只有 S1 一层，无可比基准，按要求声明原因（见 §6.1），
不作误差结论。

## 8. 机器验收判据对照

| 判据 | 结果 | 证据 |
|---|---|---|
| 从 E1 confirmed 行分层抽样 ≥10 单 | 12 单，2/2 层（总体仅 2 层） | `recheck_run.json` → `sample=12`, `sample_by_stratum`, `strata_covered` |
| 盲序独立复算（重跑工件/重算关键数字/对基线） | 盲序载荷无申报字段；E/S/数字全部重算 | `blind_payload` 落库列；`tools/eval_redteam.py::judge` |
| 与 `grade_reason` 声明对照 | 每单逐字段对照，12/12 一致 | `numbers_declared` / `numbers_recomputed` 两列 |
| 对不上=降级 + 复算记录入案 | 策略落地（reject/downgrade 动作 + 幂等入案）；本批 0 触发 | 迁移 104；`rechecks_psql.txt` |
| 冒升级即 rejected 并回写 | `REJECT_UPGRADE` 路径 + 变异控件 12/12 检出 | `test_judge_rejects_upgrade_when_s3_has_single_evidence`、M2 |
| 抽样单全数复算记录留档 | 12/12（含 MATCH） | `eval_redteam_rechecks` 12 行 |
| 0 发现也入报 | 本文 §4 + 变异/正控 | `redteam_summary.png`（中图） |
| 不一致案例处置闭环 | 本批 0 不一致；闭环逻辑由单测 + 控件证明 | `test_apply_actions_writes_back_and_is_idempotent` |
| pytest ≥6 | 26 passed | `tests/test_eval_redteam.py` |

## 9. 产物清单

| 路径 | 作用 |
|---|---|
| `tools/eval_redteam.py` | 抽样 / 盲序 / 独立重算 / 数字复算 / 判定 / 入案 / 控件 / CLI（零领域语义） |
| `backend/migrations/104_eval_redteam_rechecks.sql` | 复算记录表（PG 已应用） |
| `tools/plot_eval_redteam.py` | 三联图（覆盖 / 判定 + 灵敏度 / 申报-重算对照） |
| `tests/test_eval_redteam.py` | 26 个判据测试（抽样、盲序、重算、判定矩阵、入案幂等、控件） |
| `artifacts/m5182_eval_redteam/recheck_run.json` | 真实运行汇总（含逐单记录与控件） |
| `artifacts/m5182_eval_redteam/recheck_table.md` | 逐单复算表（盲序） |
| `artifacts/m5182_eval_redteam/rechecks_psql.txt` | `psql` 直读复算记录原始输出 |
| `artifacts/m5182_eval_redteam/redteam_summary.png` | 结果图 |