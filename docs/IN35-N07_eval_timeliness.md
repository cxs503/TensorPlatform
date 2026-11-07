# IN35-N07 评价时效性契约成熟化：cohort 窗口无关覆盖率 · in-flight 增量评价 · 积压排空 SLO

> 战役节点：IN35-N07（评价运维 maturity 节点，**无物理量**）｜仓：TensorPlatform｜分支：`agent/m6142`
> 上游：Plan 35 / IN38-N09 的 E4 局限 #1（覆盖率是窗口相关量）与 #3（55 行 pending 积压只能人工清）
> 机器裁据：`artifacts/IN35-N07/acceptance.json`（六条判据 J1–J6，全 PASS）；本文件的数字表由
> `python3 -m tools.evaluation_ops.acceptance_timeliness` 直接注入，**无手抄**。

---

## 1. 案例介绍（对象、口径与参数表）

**这不是流体/结构问题，是度量契约问题**——因此本节点没有场量云图，只有队列统计图
（`fig1`–`fig3`）。要修的两个缺陷都很具体：

1. **覆盖率是窗口相关量**：旧口径「最近 N 张 succeeded mission 里有多少张有评价记录」。N
   一变（7/30/90/300…），覆盖率就变；同一张 mission 会因被窗口挤出而从「已覆盖」跳成
   「未覆盖」。这种数不能回答「这批交付物到底评了多少」，也不能当 SLO 的分子分母。
2. **积压只能人工清**：pending 行会攒起来（Plan 35 遗留 55 行），没有「越阈值就自动排空」
   的机器动作，也没有「排空到什么程度算达标」的契约。

**本次把它改成队列不变量 + 自动排空**：mission 的 `finished_at` 落在第 k 个 ISO 周（UTC，
周一 00:00 起），它**永久**属于 cohort `YYYY-Www`；覆盖率的分子/分母都在 cohort 内取，
窗口只决定「显示哪些 cohort」，不参与任何一格的数值。

| 参数 | 值 | 来源 |
|---|---|---|
| mission 源表 | `tensor_dev_missions`（`status='succeeded' AND finished_at IS NOT NULL`） | 既有表，**只读** |
| 评价记录表 | `outcome_evaluations`（`pending/confirmed/candidate/rejected`） | 既有表（AOG SPEC §7.1） |
| 新增运维表 | `eval_inflight_events`、`eval_drain_runs` | 本节点（`triggers.DDL_SQLITE_PG`，幂等） |
| 判据夹具 | 250 张 mission / 3 个 ISO 周（W43–W45）/ 100 行 pending / 30 张未记录 | `tools/evaluation_ops/fixtures.py`（seed=7） |
| 观测时刻 `as_of` | `2026-11-07T09:00:00+00:00`（W45 进行中） | 夹具固定值 |
| 积压阈值 | `pending_rows > 50` → 自动排空 | `triggers.DEFAULT_BACKLOG_THRESHOLD` |
| 排空目标 | 24 h 内排空率 ≥ 90% | `DRAIN_TARGET_SHARE` / `DRAIN_WINDOW_HOURS` |
| in-flight 时延契约 | P95 < 60 s | 判据 J2 |

口径（钉死；改口径必须同时改 `cohorts.METRIC_FIELDS` 与 SCHEMA 版本）：

- **覆盖率** = `missions_decided / missions_total`；分子只含 `confirmed|candidate`，
  `pending` 是积压、`rejected` 是负例留痕，都不进分子；分母为 0 → `None`（不折成 0%）。
- **时延** = `min(decided_at) - finished_at`（小时）；`decided_at < finished_at` 的脏样本剔出
  分位并计数。
- **排空份额** = 本次开始时已 pending 的那批 mission 中被判定出结果的张数 / 该批张数。
  一边排空一边有新 pending 进队时，仍用**起始集合**做分母（否则份额没有意义）。

## 2. 怎么做的（方法 / 流程 / 工具链）

### 2.1 事件驱动 in-flight 评价（第 ③ 条）

钩子挂在 mission 完结事务**提交之后**（`backend/api/tensor_missions.py` 的 finish 端点尾部），
**逐张**处理，不做全仓扫描：

```
mission 终态(succeeded/failed/done) → triggers.on_mission_terminal(conn, mission_id)
   ├─ 已有 confirmed/candidate 记录 → 幂等短路（绝不降级回 pending）
   ├─ failed/stopped（无交付物）    → 只入队 pending，机器扫描不写等级
   ├─ 有 pending 行且有证据         → inflight.apply 就地判定（pending 行原地升级）
   └─ 无任何记录                     → 先建 pending 再判定
   最后写一行 eval_inflight_events（event_at / decided_at / latency_seconds / outcome）
```

钩子**完全旁路**：整段包在 `try/except` 里，失败只告警。返回体与状态机一个字都没改
（J4 用契约基线机验）。补偿通道 `triggers.catch_up()` 处理「进程崩了/表刚上线」漏掉的
终态事件，有界（`limit`）、可重放、不重复入队。

### 2.2 积压排空 SLO（第 ④ 条）

`should_drain()` 每单判一次：`pending_rows > 50` ⇒ `drain_backlog()`。排空只处理**起始 pending
集合**，按 `batch` 分批（默认 100，`limit` 上限 200），每批后重读 pending 行数；
每次排空写一行 `eval_drain_runs`（`pending_before/after`、`drained`、`drain_share`、
`elapsed_seconds`、`met`）。**证据不足的成果按纪律保持 pending**——不许为了刷指标写
`rejected`（J3 显式断言 `rejected_written = 0`）。

### 2.3 监控面板（第 ⑤ 条）

`panel.build_panel()` 只做投影，不定义新口径：cohort 覆盖率趋势（`cohorts.compute_cohorts`）
+ 多视图不变性（`views.window_independence`）+ 积压与排空序列（`eval_drain_runs`）+
in-flight 时延分位/直方图（`eval_inflight_events`）。输出是机器可读 JSON，图
（`plot_timeliness.py`）读同一份 JSON 画——**图里的数和表里的数是同一次计算**。
`panel.panel_consistency()` 抽 10 个单元用独立 SQL 重算（J6）。

### 2.4 工具链（全部新增，既有模块零改动语义）

| 模块 | 职责 |
|---|---|
| `tools/evaluation_ops/triggers.py` | 事件驱动增量评价 + 积压排空 + 两张运维表 + 时延分位 |
| `tools/evaluation_ops/panel.py` | 面板投影（趋势/排空/时延）+ 抽样一致性校验 |
| `tools/evaluation_ops/fixtures.py` | 确定性合成夹具（固定种子，可复算） |
| `tools/evaluation_ops/contract_baseline.py` | 契约基线：动手**之前**冻结 `summarize()` / `stats_queries` / 路由表 |
| `tools/evaluation_ops/acceptance_timeliness.py` | J1–J6 机器裁 → `acceptance.json` + 结果表注入本文件 |
| `tools/evaluation_ops/plot_timeliness.py` | 读 JSON 出 3 张图 |
| `backend/api/tensor_missions.py` | 只在 finish 端点尾部加了 fail-open 钩子（约 12 行） |
| `tests/test_eval_timeliness.py` | 18 条 pytest（J1–J6 各至少两条，含负控） |

### 2.5 复算命令

```bash
python3 -m tools.evaluation_ops.contract_baseline --check        # J4：契约零差异
python3 -m tools.evaluation_ops.acceptance_timeliness            # J1–J6 全量裁据
python3 -m tools.evaluation_ops.plot_timeliness                  # 三张图
python3 -m pytest tests/test_eval_timeliness.py -q               # 18 条
```

<!-- BEGIN:RESULTS -->
### 验收判据总表（`artifacts/IN35-N07/acceptance.json`，程序化生成）

| 判据 | 实测 | 阈值 | 结论 |
|---|---|---|---|
| J1 cohort 窗口无关 | 视图差异 0 处 / max|Δ| 0.0，查询时刻差异 0 处（24 个单元） | 差异=0 | PASS |
| J2 in-flight P95 延迟 | P50 0.0037 s / P95 0.0075 s / P99 0.0095 s（n=240） | P95 < 60 s | PASS |
| J3 积压排空 SLO | 注入 100 行 pending → 排空 92 行（92.00%），耗时 0.0696 s | ≥90 行 / 24 h | PASS |
| J4 向后兼容 | 基线差异 0 处（12 个契约键） | 差异=0 | PASS |
| J5 pytest 数量 | 收集 18 条 | ≥15 | PASS |
| J6 面板一致性 | 抽 10 单元，不一致 0 | 不一致=0 | PASS |

### cohort 覆盖率（分母/分子/覆盖率，窗口无关口径）

| cohort | 分母 missions_total | 分子 missions_decided | 覆盖率 | 冻结 |
|---|---|---|---|---|
| 2026-W43 | 40 | 10 | 0.2500 | True |
| 2026-W44 | 40 | 20 | 0.5000 | True |
| 2026-W45 | 170 | 20 | 0.1176 | False |

### 窗口无关性：各视图选中的 cohort（视图确实在筛，数值却不变）

| 视图（天） | 选中 cohort 数 | cohort 集合 |
|---|---|---|
| 2 | 1 | 2026-W45 |
| 7 | 2 | 2026-W44, 2026-W45 |
| 15 | 3 | 2026-W43, 2026-W44, 2026-W45 |
| 30 | 3 | 2026-W43, 2026-W44, 2026-W45 |
| 90 | 3 | 2026-W43, 2026-W44, 2026-W45 |
| 300 | 3 | 2026-W43, 2026-W44, 2026-W45 |

### 积压排空（起始 pending 集合口径）

| 项 | 值 |
|---|---|
| 起始 pending 行 | 100 |
| 排空后 pending 行 | 8 |
| 已判定（排空） | 92 |
| 证据不足仍 pending | 8 |
| 写 rejected 行 | 0 |
| 排空份额 / 目标 | 0.9200 / 0.90 |
| 墙钟耗时 | 0.0696 s（窗口 24.0 h） |
| 自动触发 | True（规则：pending_rows > 50 ⇒ 自动批量评价） |

### in-flight 时延直方图（秒）

| 区间 | 事件数 |
|---|---|
| [0.0, 0.05) | 240 |
| [0.05, 0.2) | 0 |
| [0.2, 1.0) | 0 |
| [1.0, 5.0) | 0 |
| [5.0, 60.0) | 0 |
| [60.0, +∞) | 0 |
<!-- END:RESULTS -->

## 4. 结果图

![fig1](../artifacts/IN35-N07/fig1_coverage_trend.png)

**图 1**：左＝逐 cohort 覆盖率（柱上标 `分子/分母`，蓝=已冻结周、橙=进行中周）；右＝同一
cohort 在 2/7/15/30/90/300 天视图下的覆盖率——折线**完全重合**。视图只在「筛掉哪些
cohort」上起作用（90/300 天视图含 W43，2 天视图只剩 W45），数值一格不变。

![fig2](../artifacts/IN35-N07/fig2_backlog_drain.png)

**图 2**：积压排空瀑布：起始 100 行 pending → 92 行已判定 / 8 行证据不足保持 pending →
排空后 8 行；右图排空份额 92% 越过 90% 目标线（红色虚线＝SLO，黑色虚线＝触发阈值 50）。

![fig3](../artifacts/IN35-N07/fig3_inflight_latency.png)

**图 3**：in-flight 增量评价时延（n=240）：直方图（秒，全部落在 [0.05 s, 0.2 s) 桶之前的
子毫秒区）与经验 CDF，P95 ≈ 7 ms，距 60 s 契约有三个数量级余量。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

**踩过的坑（都进了代码或测试）**

1. `oe.store_record()` 对已有有效行是**无条件 UPDATE**（`upgrade_pending_sql`）——重复的终态
   事件（例如先把单判成功、随后状态改判 failed）会把已 `confirmed` 的记录**降级成 pending**。
   修法：`on_mission_terminal` 先查有效记录，已判定就直接幂等短路；`test_repeat_event_is_
   idempotent_and_never_downgrades` 钉住这条。
2. 排空的分子分母若用「库里 pending 行数前后差」，会出现 `drained = before - after < 0`
   （排空过程中新 pending 进队）。修法：口径钉在**起始 pending 集合**上（`_pending_missions`
   快照 + `_decided_mission_ids` 事后判定），`pending_after` 另报实时值。
3. `inspect(Session)` 不可用（API 层传的是 Session）→ `triggers.has_table()` 先取
   `get_bind()` 再 inspect，否则 API 路径会误判「表不存在」并触发降级写入。
4. 面板/排空读面在运维表未建时必须退化为空列表而不是抛异常（SQLite 夹具首跑就撞上）。

**局限（不粉饰）**

- **判据用合成夹具，不是线上生产数**：线上库是活体（每单都在变），同一判据跑两次数字不同
  → 不可复算。因此覆盖率/排空/时延的数字**全部来自固定种子的 SQLite 夹具**；线上数据本次
  只做只读口径对照（未参与任何判据算术）。这与 IN38-N09 的「线上实拍」口径不同，请勿混用。
- **J2 时延是进程内墙钟**（`time.monotonic`：事件进入 `on_mission_terminal` → 判定记录落库），
  **不含** API/网关排队与 PG 网络往返。夹具是本地 SQLite，故 7 ms 是「评价器本身」的增量
  成本；切到 PG + 网络后应重测，契约（60 s）对那个量级依然宽松。
- **排空率天花板由证据决定**：夹具里 8/100 张「无任何平台事实」的成果按纪律保持 pending，
  所以 92% 是本夹具的**上界**；真实队列若证据更薄，份额会更低——那时该改的是证据采集，
  不是排空判据。
- **失败单只入队不判定**是策略选择（无交付物 → 机器扫描不该替它写等级），代价是 failed 单
  会长期占 pending；若将来要闭合，应接独立审查者而不是放宽机器扫描。
- 钩子跑在 finish 请求路径上：`pending > 50` 时会顺带排空（`limit=200`），该请求耗时会增加
  百毫秒级；对完结接口可接受，但不应把它当后台 worker 用。
- 「24 h 内排空 ≥90%」在本夹具上 0.055 s 完成——24 h 是**上限契约**，不是必须等 24 h；
  分布式部署下真正要监控的是「最早一条 pending 的年龄」而不只是份额。
- 契约基线（J4）覆盖 `summarize()` 输出、`stats_queries` 查询名与 API 路由表；**不**覆盖
  ORM/Pydantic 细节与响应头，那部分靠既有 `tests/test_outcome_evaluations.py` 兜。
- 平台处于冻结期（2026-11-02 起）：本节点是战役表指派的纯增量改造（新模块 + 一个 fail-open
  钩子），未改任何既有端点、既有统计口径与 dispatcher 行为；`missions_digest` 前后一致
  （`J3.dispatcher_untouched = true`）证明调度面未被触碰。

## 6. 误差判定（关键量 vs 基准，逐项）

本节点无实验/解析基准（度量任务），因此「误差」定义为**同一量的两条独立算路是否一致**：

| 关键量 | 基准（独立算路） | 实测差异 | 是否 <3% |
|---|---|---|---|
| cohort 覆盖率（3 个 cohort） | `panel_consistency` 的独立 SQL 计数（分母/分子/比值） | 0 处不一致（10 单元） | 是（0%） |
| 同一 cohort 跨视图数值 | 2/7/15/30/90/300 天视图互比 | 0 处差异，max\|Δ\|=0.0 | 是（0%） |
| 冻结 cohort 跨查询时刻覆盖率 | as_of、+6 h、+2 d、+14 d 四次重算 | 0 处差异（24 单元） | 是（0%） |
| in-flight 时延分位 | 事件表原始样本重算分位 | 0 处不一致 | 是（0%） |
| 排空份额 | 起始 pending 集合的事后 SQL 判定 | 92/100 = 0.9200（与 SQL 一致） | 是（0%） |
| 评价 API 契约 | 动手前冻结的 `baseline_contract.json` | 0 处差异（12 契约键） | 是（0%） |

**显式回答**：上述每一项都是「同一量的两条独立算路差 0 处」，远小于 3%；但必须说明这
**不是物理实验误差**——本节点没有实验/解析基准可比（无场量、无几何），所以不存在
「与实验值误差 x%」这种量；把 0% 说成「精度达标」是口径混淆，正确的陈述是「复算一致性
100%」。唯一有物理味道的量（时延、排空耗时）都是墙钟测量，其不确定度来自机器负载
（单次 0.002–0.017 s 的抖动），报告只把它当作「远低于 60 s 契约」的证据，不当精密测量用。

## 7. 审计链

- 代码：`agent/m6142` 分支；`tools/evaluation_ops/{triggers,panel,fixtures,contract_baseline,
  acceptance_timeliness,plot_timeliness}.py`、`tests/test_eval_timeliness.py`、
  `backend/api/tensor_missions.py`（fail-open 钩子）。
- 产物：`artifacts/IN35-N07/{acceptance.json, baseline_contract.json, panel_baseline.json,
  results.md, fig1_coverage_trend.png, fig2_backlog_drain.png, fig3_inflight_latency.png,
  figures_manifest.json}`。
- 判据数字全部可复算：`python3 -m tools.evaluation_ops.acceptance_timeliness`（固定种子 →
  同数字），失败即以非零退出码退出。
- 回滚：删除新增模块 + 撤掉 `tensor_missions.py` 那 12 行钩子即可，无 schema 迁移、无既有表改动；
  两张运维表可直接 `DROP`（不参与覆盖率/契约计算）。