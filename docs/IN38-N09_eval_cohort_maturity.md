# IN38-N09 评价体系成熟化：cohort 不变量 · 交叉验证 · 评分器稳健性 · SLO 时效性

> 战役节点：IN38-N09（评价运维 / Evaluation Operations，maturity 节点，无物理量）
> 仓：TensorPlatform ｜ 分支：`agent/m5474` ｜ 计算时刻：`2026-11-07T00:00:00+00:00`
> 全部数字由 `tools/evaluation_ops/*` 程序化产出（`acceptance.json` 为机器裁据）；本文无手抄数字。
> 机器验收：**PASS**（5/5 条判据通过）

---

## 1. 案例介绍（对象与参数表）

**问题陈述（不是物理问题，是度量问题）**：平台此前的「评价覆盖率」是**窗口相关量**——取
最近 N 张 `succeeded` mission 里有多少张有评价记录。N 一变（7 / 30 / 90 / 300 / 3000），
覆盖率就变；同一个 mission 在两次统计里可能从「已覆盖」跳到「未覆盖」，因为它被窗口挤
出去了。这样的数**不能回答「这批交付物到底评了多少」**，也不能作为 SLO 的分子分母。

**本次把它改成队列不变量**：mission 的 `finished_at` 落在第 k 个 ISO 周（UTC，周一 00:00
起），它就永久属于 cohort `YYYY-Www`；覆盖率的分子/分母都在 cohort 内取。窗口只决定
「显示哪些 cohort」，不再参与任何一格的数值。

**观测对象**（真实生产数据，只读；唯一写操作是新建 `eval_cohorts` 与 in-flight 评价行）：

| 参数 | 值 | 来源 |
|---|---|---|
| mission 源表 | `tensor_dev_missions`（`status='succeeded' AND finished_at IS NOT NULL`） | 线上 `tensorplatform` |
| 评价记录表 | `outcome_evaluations`（`confirmed/candidate/pending/rejected`） | 同上 |
| 观测时刻 `as_of` | `2026-11-07T00:00:00+00:00` | 本次运行 |
| 覆盖 mission 数 | 4175 | `capture_prod_post_inflight.json` |
| 已判定 mission 数 | 1745 | 同上 |
| cohort 数 | 2（`2026-W44, 2026-W45`） | 同上 |
| 数据历史跨度 | 约 10 天（2026-10-29 → 2026-11-07）→ 只有 2 个 ISO 周 | `min/max(finished_at)` |
| SLO 阈值 | P90 评价时延 ≤ 24 h；积压 ≤ 20 行 | 任务书第 5 条（工程默认，可注入） |
| 快照规模 | mission 4172 行、评价记录 1683 行、报告 3890 行、能力增量 1490 行 | `export_snapshot.json` |

## 2. 怎么做的（方法 / 流程 / 工具链）

### 2.1 口径定义（钉死，改口径必须同时改 `METRIC_FIELDS` 与 SCHEMA 版本）

- **cohort 成员资格**：只由 `finished_at` 的 ISO 周决定（UTC）；不存在「按天窗口归属」。
- **覆盖率** = `missions_decided / missions_total`；分子只含 `confirmed|candidate`，
  `pending` 是积压、`rejected` 是负例留痕，都不进分子。
- **时延** = `min(decided_at) - finished_at`（小时）；`decided_at < finished_at` 的脏时间戳
  样本剔出分位并单独计数（`latency_anomalies`）。
- **积压年龄** = `as_of - finished_at`（**不是记录 `created_at`**）。
- **分母为 0** 的量（空 cohort 的 coverage、无判定样本的 P90、无积压时的积压年龄）
  返回 `None` 并列入 `undefined_metrics`，**禁止折成 0**。

### 2.2 工具链（新增模块，均不改 dispatcher / 不改既有表）

| 模块 | 职责 |
|---|---|
| `tools/evaluation_ops/cohorts.py` | cohort 定义、逐 cohort 指标、`eval_cohorts` 落库（冻结/漂移/迟到评价） |
| `tools/evaluation_ops/views.py` | 7/30/90 天视图只筛 cohort；旧窗口口径负控 |
| `tools/evaluation_ops/slo.py` | P90 时延 + 积压行数，`ok/breach/undefined` 三态判定 |
| `tools/evaluation_ops/inflight.py` | 已完结无评价 → 单行评价（先 pending 再就地判定；不碰调度面） |
| `tools/evaluation_ops/robustness.py` | 行序/重复/分片不变性、分母语义、交叉验证、外部效度 |
| `tools/evaluation_ops/cli.py` | `capture / stability / replay / export / verify` 五个子命令 |
| `tools/evaluation_ops/plot.py` | 读产物 JSON 出图（无重算、无手抄） |
| `tools/evaluation_ops/acceptance.py` | 五条判据 → `acceptance.json`（机器裁） |
| `backend/migrations/105_eval_cohorts.sql` | 只加表 + 索引 + 两条 CHECK；幂等 |

### 2.3 执行流程（可复现命令序列）

```bash
DSN="postgresql+psycopg://postgres@/tensorplatform?host=/var/run/postgresql"
python3 -m tools.evaluation_ops.cli capture  --dsn "$DSN" --as-of 2026-11-07T00:00:00+00:00 \
        --write --out artifacts/IN38-N09/capture_prod.json                # ① 首拍 + 落库
python3 -m tools.evaluation_ops.cli export   --dsn "$DSN" --out-sqlite /tmp/eval_snap.db \
        --out artifacts/IN38-N09/export_snapshot.json                     # ② 独立快照
python3 -m tools.evaluation_ops.cli verify   --live-dsn "$DSN" \
        --snapshot-dsn sqlite+pysqlite:////tmp/eval_snap.db \
        --out artifacts/IN38-N09/verify_external_validity.json            # ③ 外部效度
python3 -m tools.evaluation_ops.cli replay   --dsn sqlite+pysqlite:////tmp/eval_snap.db \
        --cycles 3 --limit 25 --out artifacts/IN38-N09/replay_cycles_snapshot.json  # ④ 三轮演练
python3 -m tools.evaluation_ops.cli stability --dsn "$DSN" --cycles 5 \
        --out artifacts/IN38-N09/stability_5cycles.json                   # ⑤ 五轮零漂移
python3 -m tools.evaluation_ops.cli replay   --dsn "$DSN" --cycles 3 --limit 25 \
        --out artifacts/IN38-N09/replay_cycles_prod.json                  # ⑥ 线上 in-flight
python3 -m tools.evaluation_ops.cli capture  --dsn "$DSN" --write \
        --out artifacts/IN38-N09/capture_prod_post_inflight.json          # ⑦ 复拍
python3 -m tools.evaluation_ops.plot && python3 -m tools.evaluation_ops.acceptance
```

### 2.4 冻结与漂移的机制（为什么「零漂移」不是口号）

`eval_cohorts` 每行带 `inputs_fingerprint` = mission 集合 + 记录状态/判定时刻的内容哈希。

- ISO 周已结束 ⇒ `frozen=TRUE`，**首写定稿**；
- 指纹不变而重算值变 ⇒ 记 `drift` 并**保留旧值**（这是评分器缺陷，必须当 FAIL）；
- 指纹变（迟到评价）⇒ `revision+1` 覆盖，旧值进 `payload.previous`（可审计的演进，不是漂移）。

## 3. 计算结果

### 3.1 逐 cohort 指标（`eval_cohorts` 落库内容，线上实拍）

| cohort（ISO 周） | 区间 | missions_total（分母） | missions_decided（分子） | missions_pending（积压 mission） | backlog_rows | coverage | P90 时延 [h] | 无定义量 |
|---|---|---|---|---|---|---|---|---|
| 2026-W44 | 2026-10-26 → 2026-11-02 | 1024 | 0 | 0 | 0 | 0.0000 | None（无定义） | p90_latency_hours, backlog_age_hours |
| 2026-W45 | 2026-11-02 → 2026-11-09 | 3151 | 1745 | 0 | 0 | 0.5538 | 67.657 | backlog_age_hours |

> `2026-W44` 的 P90 与积压年龄为 `None`：该周 1024 张 mission 全部无已判定记录、无 pending
> 行 → 分母为 0，按判据单列，**不折成 0 h / 0 行**。

### 3.2 Cohort SLO（多视图同一阈值判定）

| 视图 | 回看（天） | missions_in_scope | P90 时延 [h] | 积压行 | 积压年龄上限 [h] | latency_p90 | backlog_rows |
|---|---|---|---|---|---|---|---|
| 30d | 30 | 4175 | 67.657 | 0 | None（无定义） | breach | ok |
| 7d | 7 | 3855 | 67.657 | 0 | None（无定义） | breach | ok |
| 90d | 90 | 4175 | 67.657 | 0 | None（无定义） | breach | ok |
| all | 全部 | 4175 | 67.657 | 0 | None（无定义） | breach | ok |

结论（不粉饰）：`latency_p90 = **breach**`（67.7 h ≫ 24 h，历史评价滞后，in-flight 只能
提高后续时效、无法追溯缩短已发生的时延）；`backlog_rows = ok`（in-flight 三轮后 55 → 0 行）。
`slo_met = False`。

### 3.3 窗口无关性（判据 1）

| 视图（天） | 含哪些 cohort | 同 cohort 逐字段差异数 | max abs diff |
|---|---|---|---|
| 30 | 2026-W44, 2026-W45 | 0 | 0.000000 |
| 7 | 2026-W44, 2026-W45 | 0 | 0.000000 |
| 90 | 2026-W44, 2026-W45 | 0 | 0.000000 |

同一 cohort 在 7/30/90 三种视图下逐字段比对：**0 处差异，
`max abs diff = 0`**，`identical = True`。

**注意本数据的局限（必须写清）**：线上历史只有约 10 天，恰好跨 2 个 ISO 周，三个视图都
包含同样的两个 cohort，所以「相同」在这批数据上是被「视图未过滤掉任何 cohort」满足的。
判据的**结构性验证**由单元测试（6 个 ISO 周的夹具，视图会真的筛掉 cohort）与
`locality_invariance`（单 cohort 单独算 = 全量算）两条独立证据补足；
压力视图 `days=(2,7,30,90)`（产物字段 `window_independence_stress`）把回看压到 2 天，
`2026-W44` 被真的筛出视图（各视图 cohort 集合：
{"2": ["2026-W45"], "30": ["2026-W44", "2026-W45"], "7": ["2026-W44", "2026-W45"], "90": ["2026-W44", "2026-W45"]}），
而余下 cohort 逐字段差异仍为 **0 处** ——
即「筛选确实发生了，但没改数」。

### 3.4 旧口径负控（为什么必须改）

| 旧口径窗口 N（最近 N 单） | mission 数 | 已判定 | 覆盖率 |
|---|---|---|---|
| 7 | 7 | 4 | 0.5714 |
| 30 | 30 | 27 | 0.9000 |
| 90 | 90 | 75 | 0.8333 |
| 300 | 300 | 159 | 0.5300 |
| 1000 | 1000 | 859 | 0.8590 |
| 3000 | 3000 | 1713 | 0.5710 |

同一批数据、只改「最近 N 单」的 N：覆盖率在
`0.5300` —
`0.9000`
之间跳动，极差 `spread = 0.3700`。这就是原来的「覆盖率」：
不是数据变了，只是窗口 N 变了。

### 3.5 in-flight 增量评价（判据 3）

在**线上数据的独立快照**上连跑 3 轮（每轮最多 25 张新 mission；已有 pending 行全部判定）：

| cycle | 规划（无记录可选 / 本轮取 / pending 可选） | queue 行 | 判定 | 拒收 | 证据不足 | 结束后 pending 行 | 结束后已判定 mission | coverage |
|---|---|---|---|---|---|---|---|---|
| 1 | 2502 / 25 / 55 | 25 | 80 | 0 | 0 | 0 | 1695 | 0.4063 |
| 2 | 2477 / 25 / 0 | 25 | 25 | 0 | 0 | 0 | 1720 | 0.4123 |
| 3 | 2452 / 25 / 0 | 25 | 25 | 0 | 0 | 0 | 1745 | 0.4183 |

- pending 行：`55` → `0`，
  **下降 100%**（判据要求 ≥50%）；
- mission 覆盖率：`0.3871` → `0.4183`
  （+0.0312）；无记录缺口 `2502` → `2427`；
- **调度面未被触碰**：`dispatcher_untouched = True`
  （`tensor_dev_missions` 内容哈希前后相同）；
- 线上同样跑 3 轮（`replay_cycles_prod.json`）：pending `55` → `0`
  （下降 100%），已判定 mission `1615` → `1745`，
  覆盖率 `0.3871` → `0.4183`。

快照演练与线上演练的数值一致（pending `55`→`0`、
已判定 `1745`）：因为快照正是线上起跑前的拷贝，两者互相印证；
产物分别保存在 `replay_cycles_snapshot.json`（SQLite 快照）与 `replay_cycles_prod.json`（线上 PG）。

**写入的全部内容**在产物里可枚举：每轮 `by_status/by_grade` 计数 + 每张 mission 的判定
明细（`items`），判定规则是「仅有平台事实 → E1、执行性成果 → S1」，审查者为
`platform-evidence-scan`（`auto_scan`，非人工/同行评审）。

### 3.6 冻结 cohort 五轮零漂移（判据 4）

| cycle | 重写行 | 迟到评价 | 漂移 | 冻结 cohort 指标变化 |
|---|---|---|---|---|
| 1 | 1 | 0 | 0 | 无 |
| 2 | 1 | 0 | 0 | 无 |
| 3 | 1 | 0 | 0 | 无 |
| 4 | 1 | 0 | 0 | 无 |
| 5 | 1 | 0 | 0 | 无 |

`frozen_cohorts = 1`（`2026-W44`），`zero_drift = True`，
`drifted_cohorts = []`。冻结行的 `revision` 始终为 1、`computed_at` 停在首写时刻，
说明「冻结 = 首写定稿」确实生效（不是每轮重算覆盖）。

### 3.7 交叉验证 / 评分器稳健性 / 外部效度（判据配套）

| 检验 | 对象 | 差异数 / 结论 | 是否通过 |
|---|---|---|---|
| SQL 独立复算 vs Python 聚合 | 2 个 cohort | 0 处差异 | True |
| 外部效度：线上直读 vs SQLite 快照 | 2 个 cohort、4172 行 mission | 0 处差异 | True |
| 行序不变性（5 次随机打乱） | 2 个 cohort | 0 处差异 | True |
| 重复 mission 行（join 膨胀） | 2 个 cohort | 0 处差异 | True |
| 局部=全局（单 cohort 单独算） | 2 个 cohort | 0 处差异 | True |
| 分母语义（None 不折 0） | 2 个 cohort | 0 处违例 | True |

### 3.8 五条验收判据（机器裁，`acceptance.json`）

| 判据 | 实测值 | 阈值 | 结论 |
|---|---|---|---|
| 窗口无关性：同一 cohort 在 7/30/90 天三种视图下数值差异=0 | {"cohorts_per_view": {"30": ["2026-W44", "2026-W45"], "7": ["2026-W44", "2026-W45"], "90": ["2026-W44", "2026-W45"]}, "diffs": 0, "max_abs_diff": 0.0, "pairs_compared": 4, "stress_cohorts_per_view": {"2": ["2026-W45"], " | diffs == 0 且 max_abs_diff == 0 | PASS |
| 分母为 0 时返回 None 并单列（禁止折成 0%） | {"cohorts_with_undefined_metrics": ["2026-W44", "2026-W45"], "denominator_violations": 0, "example": {"2026-W44": ["p90_latency_hours", "backlog_age_hours"], "2026-W45": ["backlog_age_hours"]}, "undefined_cases": ["2026- | violations == 0（None 且列入 undefined_metrics，不折成 0） | PASS |
| 连续3个cycle后pending下降≥50%，P90时延可算并落库 | {"dispatcher_untouched": true, "p90_defined": true, "p90_latency_hours": 67.656718, "pending_after": 0, "pending_before": 55, "pending_drop_share": 1.0, "persisted_coverage": {"2026-W44": 0.0, "2026-W45": 0.553792}, "per | pending_drop_share ≥ 0.5；P90 为有限数值；eval_cohorts 有行 | PASS |
| 冻结cohort跑5个cycle覆盖率与积压零漂移 | {"cycle_meta": [{"cycle": 1, "drift": 0, "late_arrivals": 0, "written": 1}, {"cycle": 2, "drift": 0, "late_arrivals": 0, "written": 1}, {"cycle": 3, "drift": 0, "late_arrivals": 0, "written": 1}, {"cycle": 4, "drift": 0, | zero_drift == true 且 frozen_cohorts ≥ 1 | PASS |
| pytest≥10 | {"files": ["tests/test_eval_cohorts.py", "tests/test_eval_views.py", "tests/test_eval_slo.py", "tests/test_eval_inflight.py", "tests/test_eval_robustness.py"], "tests_collected": 47} | ≥ 10 | PASS |

## 4. 结果图

> 图由 `tools/evaluation_ops/plot.py` 直接读 §3 的同一批 JSON 生成；容器内无中文字体，
> 图上标签用英文，图注在此用中文。

1. `artifacts/IN38-N09/fig1_cohort_coverage.png` — 逐 cohort 的 mission 规模（浅色）、已判定
   mission 数（深色）与覆盖率标注。可见 `2026-W44` 1024 张全部未评价（覆盖率 0.0，真值），
   `2026-W45` 覆盖率约 55%。
2. `artifacts/IN38-N09/fig2_cohort_latency_backlog.png` — 左：逐 cohort P90 评价时延与 24 h
   SLO 线（W44 无样本次轴标注 undefined）；右：积压行数与 20 行 SLO 线。
3. `artifacts/IN38-N09/fig3_window_independence.png` — 左：7/30/90 三种视图下同一 cohort 的
   覆盖率柱**完全重合**（`max abs diff = 0`）；右：旧口径覆盖率随窗口 N 剧烈漂移（spread
   0.370），即被修掉的窗口相关性。
4. `artifacts/IN38-N09/fig4_inflight_cycles.png` — in-flight 三轮：pending 行数 55 → 0，
   已判定 mission 数 1615 → 1745，覆盖率随之抬升。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

**踩坑（都已修复并留测试）**
1. `outcome_evaluations.decided_at` 是 **TEXT** 而非 timestamptz：SQL 侧算时延必须
   `CAST(... AS TIMESTAMPTZ)`，否则 PG 报 `operator does not exist: text - timestamp with time zone`
   （Python 侧早就做了时区归一，只有 SQL 交叉验证路径踩到）。
2. 机器扫描对「无证据」mission 会给出 E0 → `decide()` 判 `E0_NOT_REGISTRABLE`。若直接落库，
   会写一条 `rejected` 行，而成果实际只是「还不能评」；下一轮它又会被当成无有效记录重新入队，
   形成 rejected 行无限增长。现改为：命中 `NOT_READY_CODES` 时**不写判定行**，保持 pending
   并计入 `undecidable`。
3. PG 的 `jsonb_array_length(figures)` 在 SQLite 快照里不存在；跨引擎改成取原值后在 Python
   侧归一成计数（`_normalize`）。
4. SQLite 无 `BOOLEAN`：布尔列按 0/1 落库（`_bool_for`），否则 `frozen` 读到字符串/整数不一致。

**局限（写清，不外推）**
- 线上历史只有 2 个 ISO 周（约 10 天），7/30/90 三视图当前包含同一组 cohort —— 判据 1 在
  线上是「弱满足」，其结构性证明来自 6 周夹具的单元测试与「局部=全局」不变量。
- 旧口径扫描用的是 **mission 张数**窗口（7/30/90/300/1000/3000），与 cohort 视图的**天数**
  窗口同名不同义，报告里必须注明，否则容易被误读成「同一把尺子」。
- SLO 的 P90 时延**无法被 in-flight 追溯改善**：历史积压处置只会让「迟到判定」的时延更长
  （本次 P90 由 66.34 h 抬到 67.66 h 正是此效应）。要真正压到 24 h 内，必须缩短「完结 →
  入队 → 判定」的链路本身，而不是事后补评价。
- 覆盖率只回答「评了没有」，不回答「评得对不对」；正确性由 `outcome_evaluations.decide`
  的结构判据负责，二者不可互相替代。
- `inputs_fingerprint` 是内容哈希、不是签名，只用于漂移判定，不抗篡改。
- 线上表在跑动中仍在增长：快照导出 4172 行 mission，复拍时已
  4175 行（相差 3 行，
  是运行期间新完结的 mission）。外部效度比对因此以同一时刻快照为准，跨时刻的 ±数行差异不是缺陷。
- 机器证据扫描（auto_scan）E 级上限 E2、S4/S5/S-N 只能落候选；本次线上 in-flight 判定全部为
  S1/E1，不代表这些成果的真实价值上限。

## 6. 误差判定（关键量 vs 基准，逐项回答「是否 < 3%」）

### 6.1 表

| 关键量 | 基准/参照 | 观测差异 | 相对差异 | 是否 < 3% |
|---|---|---|---|---|
| cohort 指标（7/30/90 视图） | 同一 cohort 的 7 天视图值 | 0 处字段差异（max 0） | 0.000% | 是 |
| cohort 指标（Python vs SQL 独立复算） | PostgreSQL 聚合实现 | 0 处差异 | 0.000% | 是 |
| cohort 指标（线上 vs 导出快照） | SQLite 快照独立重算 | 0 处差异 | 0.000% | 是 |
| cohort 指标（乱序 / 重复 / 分片） | 基准顺序一次计算结果 | 0 处差异 | 0.000% | 是 |
| P90 评价时延（绝对精度） | 无外部实验/文献基准 | 无法给出 | 无法给出 | 否（无基准，理由见 §6.3；只验证了口径可复算与落库） |

### 6.2 明确回答

- **窗口无关性、SQL 交叉验证、外部效度、乱序/重复/分片不变性**：与各自基准的差异均为
  **0 处字段、0.000%** → **是（< 3%）**，且是「严格相等」而非「落在容差内」。
- **P90 评价时延的绝对精度**：**无外部实验/文献/解析基准可比**，故**不能**声称 <3%；
  本次只证明它「口径可复算（Python 与 PG 独立实现一致到 1e-3 h 以内）、可落库、有明确
  的 ok/breach/undefined 三态」。按纪律记为 **NOT_SUPPORTED（无基准）**，不是 PASS。

### 6.3 honest FAIL / 未完成

- **P90 ≤ 24 h 的 SLO 未达成**（实测 67.66 h，`latency_p90 = breach`）。这是历史评价链路的
  真实滞后，本次不掩盖、不调阈值；只把积压从 55 行降到 0 行、并让时延从今以后可观测。
- `tensor_dev_missions` 仍有 `2418` 张成功 mission 无有效评价记录（覆盖缺口
  58.2%），本次按 `--limit 25/轮` 增量推进，**没有**批量刷库；
  剩余缺口如实留在 `missions_unrecorded` 里。

## 7. 产物路径与复算入口

| 产物 | 路径 |
|---|---|
| 机器裁据（五条判据 + 数字） | `artifacts/IN38-N09/acceptance.json` |
| 首拍 / 复拍（cohort + 视图无关性 + SLO + 稳健性 + SQL 交叉验证） | `artifacts/IN38-N09/capture_prod.json`、`capture_prod_post_inflight.json` |
| 五轮零漂移 | `artifacts/IN38-N09/stability_5cycles.json` |
| in-flight 三轮（快照 / 线上） | `artifacts/IN38-N09/replay_cycles_snapshot.json`、`replay_cycles_prod.json` |
| 独立快照与外部效度 | `artifacts/IN38-N09/export_snapshot.json`、`verify_external_validity.json` |
| 图 | `artifacts/IN38-N09/fig1..fig4*.png` |
| 迁移 | `backend/migrations/105_eval_cohorts.sql` |
| 测试 | `tests/test_eval_cohorts.py`、`test_eval_views.py`、`test_eval_slo.py`、`test_eval_inflight.py`、`test_eval_robustness.py`（合计 47 条） |
