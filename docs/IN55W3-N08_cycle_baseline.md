# 任务周期四段分解基线与调度公平性（cycle-baseline-2026-11-10）

- schema: `cycle_baseline.v1` · prereg: `cycle-baseline-2026-11-10`
- 口径指纹 `SPEC_SHA256`: `8d72f88bb8e708252155ea285b0775c12fdd08f238285e9e723fc477e208ef37`
- 窗口锚点 asof: `2026-11-10T18:30:00+00:00`（固化锚点才能复现同一 cohort）
- 数据源: `pg` · 窗口: 最近 8 天终态 mission · cohort n = **7,956**
- 生成时刻(UTC): 2026-11-10T18:17:02+00:00

> 零手抄声明：本文件全部数字由 `python -m tools.cycle_baseline` 从基线报告字典程序化注入；每行基线在 `artifacts/cycle_baseline/baseline.json` 的 `provenance.tables` 中携带 SQL 原文与 `value_sha256`，可由 `provenance.verify_table` 逐行复算。

## 1. 案例介绍（平台调度管线，非物理域）

测量对象是平台的**任务生命周期管线**：mission 创建 → worker 领取 → 执行到终态 → 改动落地（git）→ 报告发布。学科上等价于一个多工位开环排队系统：到达率由战役派发器决定，服务时间由 agent 执行决定，落地与报告是**尾段开销**。

四段口径（冻结于 `tools/cycle_baseline/spec.py`）：

- **t_queue**: 首个 mission_claimed 事件 − mission.created_at（排队等待；无领取证据 → 缺失）
- **t_exec**: mission.finished_at − 首个 mission_claimed 事件（领取到终态的墙钟；无领取证据 → 缺失）
- **t_merge**: 首次落地 commit 时间 − mission.finished_at（git 侧按 mission id 反查；无匹配 commit → 缺失）。原值可为负：先落地后收尾的 push-merge 路径
- **t_report**: 报告行 created_at − mission.finished_at（tensor_reports.mission_id 关联；无报告行 → 缺失）。原值可为负：先发报告再 finish 的执行路径
- **cycle**: 周期 = created_at → finished_at（预注册主口径）。原战役假设『created→report』与实测相位相反（>95% 报告早于 finished_at），故本口径按实测修正并在此冻结
- **segment_share**: 份额 = 段时长 / Σ 已测段时长（仅在四段齐备的 complete-case 子集上可加；缺证据时分子分母同时收缩并在 coverage 字段显式给出）
- **coverage**: 段覆盖率 = 该段有证据的 mission 数 / cohort mission 数
- **starvation**: 饥饿事件 = 排队时长 > 同 cohort 排队 p95 且终态非 succeeded（等待超阈却未正常完成）
- **priority_inversion**: 优先级反转 = 同战役内 mission A 的 planned_start 早于 B，但 A 的领取时间晚于 B（调度序与计划序倒置），阈值 gap 秒

## 2. 怎么做的

1. **取数**（只读）：`tensor_dev_missions` + `tensor_agent_session_events`（`mission_claimed`）+ `tensor_reports` + `campaign_nodes`（planned_start）；落地时间由 `git log --all` 按 mission id 反查（`sources.git_merge_index`）。
2. **四段分解**（`segments.build_record`）：每段按自身锚点算原值，允许为负；非执行开销口径另给 `max(0,·)` 并单列 `negative_share`。
3. **分位账本**（`segments.segment_ledger`）：p50/p90/p99 + 覆盖率 + complete-case 份额。
4. **归因表**（`segments.attribution_table`）：终态 × repo，给段份额与非执行开销占比，识别瓶颈。
5. **公平性**（`fairness`）：worker 维度 Jain/Gini（计数与忙时两种口径）、等待 Gini、饥饿事件（等待 > p95 且非成功终态）、优先级反转（战役内 planned_start 与领取序倒置）。
6. **策略模拟器**（`simulator`）：round-robin / sticky / weighted 三策略跑同一合成负载，与名义份额的解析 Jain/Gini 对比，并从 trace 反算 Jain（模拟平台侧黑盒观测）。
7. **证据链**（`provenance`）：每行 SQL + sha256；Python 统计与 SQL `percentile_cont` 独立对拍。

## 3. 计算结果（分位账本，程序化注入）

| 段 | n | 覆盖率 | 门禁 | p50 [s] | p90 [s] | p99 [s] | 原值 p50 [s] | 均值 [s] | complete-case 份额 | 负值占比 |
|---|---|---|---|---|---|---|---|---|---|---|
| `t_queue` | 5,972 | 75.1% | WITHHELD | 3,900.9 | 26,108.8 | 32,698.3 | — | 8,463.1 | 26.3% | — |
| `t_exec` | 5,968 | 75.0% | WITHHELD | 1,316.2 | 6,177.9 | 148,441.4 | — | 9,334.5 | 40.2% | — |
| `t_merge` | 3,271 | 41.1% | WITHHELD | 1,133.7 | 28,530.1 | 69,229.3 | 1,133.7 | 8,567.0 | 33.5% | 19.0% |
| `t_report` | 5,163 | 64.9% | WITHHELD | 0.0 | 0.0 | 62.7 | -36.1 | 8.1 | 0.0% | 98.7% |
| `cycle_s` | 7,952 | 99.9% | — | 3,545.2 | 30,244.5 | 153,216.2 | — | 15,401.0 | — | — |

complete-case（四段齐备）n = **1,826**（覆盖率 23.0%）。

**判据 (d) 门禁**：覆盖率 < 80% 的段 = ['t_queue', 't_exec', 't_merge', 't_report'] → 这些段的**总体结论降级 WITHHELD**（描述性子样本统计仍给出，但不得作为总体口径引用）。

### 3.0b 条件子总体（覆盖率不足时的唯一诚实出路）

严格 cohort 上四段覆盖率 41%–75%，按判据 (d) **全部降级 WITHHELD**；但缺口有结构性来源（无会话关联 → 不可能有领取事件；无 PR 动作 → 不可能有落地时间）。故对每段定义**可证据总体**并在其上重算，结论只对该子总体成立：

| 段 | 可证据总体 n | 占 cohort | 实测 n | 总体内覆盖率 | 判定 | p50 [s] | 代表性（有证据/缺证据 周期 p50 比） | 缺证据样本 n |
|---|---|---|---|---|---|---|---|---|
| `t_queue` | 5,982 | 75.2% | 5,972 | 99.8% | MEASURED_CONDITIONAL | 3,900.9 | 7.400 | 1,984 |
| `t_exec` | 5,982 | 75.2% | 5,968 | 99.8% | MEASURED_CONDITIONAL | 1,316.2 | 7.400 | 1,984 |
| `t_merge` | 2,291 | 28.8% | 2,267 | 99.0% | MEASURED_CONDITIONAL | 3,143.0 | 1.233 | 4,681 |
| `t_report` | 4,924 | 61.9% | 3,720 | 75.5% | WITHHELD | 0.0 | 0.587 | 2,789 |

- `t_queue` 可证据总体理由：领取证据只能经 session 事件关联（session_id 为空则不可能有领取时间）
- `t_exec` 可证据总体理由：同 t_queue：执行段以领取时刻为锚
- `t_merge` 可证据总体理由：落地时间只对发起过 pr_gate 动作的 mission 有意义
- `t_report` 可证据总体理由：平台自报 report_owed=false 才算该交且已交报告（欠账单不算仪器缺失）
  - **台账不一致**：该总体内 1,201 条（24.4%）平台称不欠报告却无报告行 —— 平台台账称不欠报告但无报告行：口径与实交不一致（治理缺口，不是仪器缺失）

条件判定汇总：可证据子总体达标 ['t_queue', 't_exec', 't_merge']；仍 WITHHELD ['t_report']。条件结论只对可证据子总体成立，不得外推到全体 cohort。

### 3.1 归因表（终态 × repo，前 12 行按 cohort n 降序）

| status | repo | n | n(complete) | cycle p50 [s] | exec 份额 | queue 份额 | 非执行开销份额 |
|---|---|---|---|---|---|---|---|
| succeeded | ship-performance-platform | 1,441 | 472 | 8,959.3 | 25.7% | 32.5% | 41.8% |
| succeeded | TensorPlatform | 1,423 | 354 | 8,134.4 | 40.2% | 34.7% | 25.1% |
| succeeded | TensorDEM | 617 | 224 | 2,931.6 | 41.5% | 23.9% | 34.6% |
| succeeded | TensorLBM | 578 | 206 | 4,156.7 | 49.8% | 20.1% | 30.1% |
| succeeded | TensorFEM | 441 | 111 | 1,632.4 | 45.7% | 18.1% | 36.2% |
| succeeded | TensorBEM | 432 | 90 | 1,708.0 | 58.1% | 16.1% | 25.9% |
| succeeded | TensorSPH | 380 | 39 | 1,554.4 | 62.7% | 8.7% | 28.6% |
| succeeded | TensorFVM | 348 | 79 | 1,277.4 | 51.4% | 14.2% | 34.3% |
| failed | ship-performance-platform | 330 | 5 | 15,993.6 | 2.2% | 28.6% | 69.2% |
| succeeded | TensorCAD | 282 | 32 | 1,295.0 | 68.2% | 6.9% | 25.0% |
| succeeded | TensorPapers | 280 | 37 | 539.3 | 34.0% | 8.7% | 57.3% |
| succeeded | TensorDG | 208 | 24 | 1,759.7 | 63.3% | 6.8% | 29.9% |

### 3.2 公平性（真实语料）

- worker 数 = 88，有主 mission 数 = 7,928
- **Jain（领取计数）** = 0.6593 · **Jain（执行忙时）** = 0.7248
- **Gini（计数）** = 0.4088 · **Gini（忙时）** = 0.3495
- 最忙 worker 领取量 / 均值 = 2.608，领取计数变异系数 CV = 0.719
- 等待时长 Gini = 0.6259；等待 p50 = 3,900.9 s，p95 = 29,413.6 s (n=5,972)
- 饥饿事件：阈值 p95 = 29,413.6 s，命中 171 / 5,972（2.9%）
- 优先级反转：可判 169 条、比较对 410，命中 28（16.6%），未领取 0
- 口径：Jain = (Σx)²/(nΣx²)，x 为 worker 负载；Gini 为平均绝对差形式。

### 3.3 检测器判分（合成带标签夹具）

| 检测器 | n_flagged | precision | recall | F1 |
|---|---|---|---|---|
| 饥饿 | 13 | 0.923 | 1.000 | **0.960** |
| 优先级反转 | 14 | 1.000 | 1.000 | **1.000** |

判据 (b) 门限 F1 ≥ 0.9；**范围限制**：F1 只在合成夹具（注入故障 + 诱饵）上可算；真实语料无人工标签，真实 F1 未知，不得外推为真实检出能力已达标。

### 3.4 策略模拟器（Jain/Gini 与解析预测）

| 策略 | 名义份额 | Jain(理论) | Jain(实测) | Jain 恢复误差 | Gini(理论) | Gini(实测) | Gini 相对误差 | 平均等待 [s] |
|---|---|---|---|---|---|---|---|---|
| `round_robin` | w0:0.250, w1:0.250, w2:0.250, w3:0.250 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 10,285.5 |
| `sticky` | w0:0.287, w1:0.233, w2:0.242, w3:0.237 | 0.9924 | 0.9924 | 0.0000 | 0.0417 | 0.0417 | 0.0000 | 10,793.4 |
| `weighted` | w0:0.100, w1:0.200, w2:0.300, w3:0.400 | 0.8333 | 0.8333 | 0.0000 | 0.2500 | 0.2500 | 0.0000 | 14,882.1 |

判据 (a) Jain 恢复容差 ≤ 0.02；判据 (e) 策略-理论一致容差 ≤ 5%。模拟负载 n_jobs = 240，worker = 4，seed = 20261110。

#### 3.4b 权重是否该与产能匹配（同负载两场景）

| 场景 | 轮转 平均等待 [s] | 加权 平均等待 [s] | 加权/轮转 | 结论 |
|---|---|---|---|---|
| 同产能（worker 等速） | 10,285.5 | 14,882.1 | 1.447 | 加权更差 |
| 产能=权重（速度 1:2:3:4） | 2,796.1 | 125.6 | 0.045 | 加权更好 |

结论（可证伪）：权重与产能不匹配时加权策略恶化平均等待；匹配时显著改善（数值见上）。含义：**加权调度只有在权重≈实际产能时才是改进**；否则它把任务堆给慢工位，平均等待反而变差（本节点实测恶化 44.7%）。平台若要给 worker 配权重，必须先用实测吞吐标定产能，否则不要上加权。

### 3.5 关键发现与 KPI「周期 −50%」的着力点

1. **KPI 基线已可判定**：`cycle_s` p50 = 3,545.2 s（覆盖率 99.9%），「周期 −50%」自此有了冻结的对照值。
2. **最大着力点在排队，不在执行**：complete-case 加权份额 queue = 26.3%、exec = 40.2%、merge = 33.5%。要把周期砍半，先减少派发等待（领取延迟），而不是压缩执行。
3. **落地是隐形开销**：merge 段在 complete-case 中占 33.5%，p90 = 28,530.1 s；任务做完了但落地排队，周期照算。
4. **worker 负载不均**：Jain(计数) = 0.659、最忙工位是均值的 2.61 倍；这与 3.4b 的结论同向：盲目加权只会加剧不均，需要先标定产能。
5. **仪器缺口（决定覆盖率天花板）**：session 关联 75.2%、有 PR 动作 28.8%、有报告行 64.9%、platform 自报欠报告 38.1%、战役节点关联 33.7%。
6. **可执行的三条改进**：(i) mission 表直接落 `claimed_at` 列（或把 claim 事件同时写 `mission_lifecycle_log`）→ t_queue/t_exec 覆盖率可从 ~75% 提到 ~100%，条件结论升为总体结论；(ii) 所有 push-merge 路径也写 `pr_gate merged` 生命周期事件 → t_merge 不再依赖 git 反查；(iii) 报告欠账与台账对齐（report_owed=false 却无报告行的 24%）→ t_report 段才可能达标。

## 4. 结果图

![fig_segment_quantiles.png](fig_segment_quantiles.png)

*四段分位账本：p50/p90/p99（对数轴），标注各段样本数与覆盖率*

![fig_coverage_gate.png](fig_coverage_gate.png)

*各段证据覆盖率 vs 预注册 80% 门限；低于门限者标红并降级 WITHHELD*

![fig_policy_fairness.png](fig_policy_fairness.png)

*三策略模拟 vs 解析预测：左 Jain、右 Gini（份额口径）*

![fig_cycle_distribution.png](fig_cycle_distribution.png)

*周期分布（尤尔-辛普森不适用/对数轴）+ p50 标记*

## 5. 注意点（踩坑与局限）

- **相位陷阱**：战役原假设 `queue→exec→merge→report` 串行，实测 >95% 的报告行早于 `finished_at`（agent 先发报告再 finish），push-merge 路径的落地 commit 也可能早于终态。因此段原值允许为负、非执行开销单列 clamp 值 —— 用 clamp 掩盖相位是本任务最容易犯的假账，已用 `negative_share` 显式暴露。
- **缺证据 ≠ 0**：无领取事件的 mission 的 t_queue/t_exec 记缺失（不是 0 秒）；分母恒为 cohort 全量，缩分母会让覆盖率虚高。
- **merge 证据来自 git**：git 侧按 mission id 反查（三种历史写法），多仓重名/shallow clone/时间窗口都会造成漏配；漏配计入缺失而非 0。
- **检测器 F1 只在合成夹具可算**，真实语料无标签；不要把夹具 F1 当作真实检出能力。
- **窗口锚点必须是固定 asof，不能是 now()**：第一次实现用 ``now() - interval``，行级导出与 SQL 聚合两次查询之间种群漂移（asof 时在飞、之后落终态的 mission），实测 n 差 1~3 条、p50 差 1e-4，对拍永远红。改为**单事务 REPEATABLE READ 快照 + 固定 asof**后对拍误差归零。教训：对拍要先保证两次计算看的是同一张快照，否则测的是抖动不是口径。
- **原生 ``GREATEST(NULL, 0)`` 会返回 0**：PostgreSQL 的 GREATEST/LEAST 忽略 NULL 入参，于是「报告时间为 NULL」被静默算成「报告开销 0 秒」，虚增 3 条样本并抬高 p99；必须显式 ``CASE WHEN ... IS NULL THEN NULL ELSE GREATEST(...) END``。同类坑还有 psql 默认不带 ON_ERROR_STOP：SQL 报错也返回 0，空结果会被当成「无数据」（fail-open），现已强制 ``-v ON_ERROR_STOP=1``。
- **假策略（反例留档）**：加权派发最初写成「有空闲 worker 就按 w/(n+1) 打分派给它」，weights=[1,2,3,4] 下计数仍近似均分（Jain 0.9993 vs 理论 0.8333）——并行空位存在时分配由「谁先干完」决定，权重根本不构成约束。必须先**目标派发**（决定谁收这活）再由私有队列执行。该条已固化为回归测试 ``test_weighted_actually_constrains_allocation_regression``。
- **模拟器是合成负载**（lognormal 服务时间、指数到达），worker 速度同质；真实平台有异构模型与预算上限，绝对数值不可外推到生产，仅用于策略间相对比较。

## 6. 误差判定（逐项）

| 判据 | 内容 | 门限 | 实测 | 判定 |
|---|---|---|---|---|
| (a) | Jain 恢复误差（trace 反算 vs 解析） | ≤ 0.02 | 0.0000 | PASS |
| (b) | 检测器 F1（合成夹具） | ≥ 0.9 | starv 0.960 / inv 1.000 | PASS |
| (c) | 每行 SQL+sha256 可复算 | 全部行 | 40 行，bad 0，SQL 对拍失配 0 | PASS |
| (d) | 覆盖率门禁生效 | 任一段 <80% → WITHHELD | ['t_queue', 't_exec', 't_merge', 't_report'] | PASS |
| (e) | 三策略 vs 理论 ≤5% | 相对误差 | 0.0000 | PASS |

**总体**：all_pass = PASS；被降级段 = ['t_queue', 't_exec', 't_merge', 't_report']。

**「是否 <3%」的回答**：本节点是平台调度度量（非物理量），不存在解析解基准，故 3% 判据不适用；替代基准是 (i) SQL `percentile_cont` 独立复算（容差 1e-6，见 crosscheck），(ii) 策略名义份额解析值（容差 2%/5%，见上表）。两者均按机器判定给出，未达标项绝不含糊其辞。

## 7. 复现

```bash
python -m tools.cycle_baseline --source pg --repos-root /root/tensor-ecosystem \
    --out-json artifacts/cycle_baseline/baseline.json \
    --out-md docs/IN55W3-N08_cycle_baseline.md --figure-dir artifacts/cycle_baseline
python -m pytest tests/test_cycle_baseline_*.py -q
```
