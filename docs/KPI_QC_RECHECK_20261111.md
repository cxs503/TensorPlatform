# KPI-QC-RECHECK：已合并 agent PR 独立复核通过率 —— 真实现值计量报告

- 计量时刻：2026-11-11T19:27Z（脚本自动打点，见 `artifacts/kpi_qc_recheck/kpi_result.json` → `measured_at`）
- 任务：m15116（锚点 KPI-QC-RECHECK，只计量不改进；本单未改动任何平台代码/数据）
- 计量脚本：`artifacts/kpi_qc_recheck/measure.py`（只读 SQL）
- 复算 SQL：`artifacts/kpi_qc_recheck/recompute.sql`（独立第二实现，L1 数学验证用）

---

## ① 案例介绍（计量对象与口径）

被计量的对象不是物理场，而是**平台的质量控制回路本身**：近 30 天内由智能体产出、经合并口进入主干的每一份
交付（agent PR），有多少份被**独立参考复核**过并且通过。

口径（协调官 2026-11-11 规划令）：

| 项 | 定义 | 数据来源 |
|---|---|---|
| 分母 N | 近 30 天已合并 agent PR，按 `(mission_id, repo, pr)` 去重 | `mission_lifecycle_log`，`action='pr_gate'` 且 `detail->>'state'='merged'` |
| 分子 M | 该 mission 存在**独立参考复核通过**记录：`reviewer_kind='independent'` 且 `review_status='reviewed'` 且 `status='confirmed'` | `outcome_evaluations`（独立席位 S×E 复判记录） |
| 指标 | M / N × 100% | 复算 SQL 见 `recompute.sql` |
| 目标 | 100%（治理语义：每条合并交付都应有独立复核结论） | — |

**为什么分子排除 `auto_scan`**：`outcome_evaluations` 的 `confirmed` 记录里有 2917 行来自
`reviewer_kind='auto_scan'`（平台证据自动扫描），它不构成"另一双眼睛"的独立参考复核。
若把它算进分子，通过率会从 5.16% 虚高到 12.98%（见定义阶梯 D1）——这是本次计量最重要的口径陷阱。

## ② 怎么做的（方法与工具链）

1. **定口径**：先把"已合并"、"独立"、"通过"三个词各自绑定到唯一 SQL 谓词，写进 `measure.py` 常量区；
   再把口径放宽/收紧成四级阶梯（D0–D3），让读数不依赖单一武断定义。
2. **拉数**：`psycopg3` 只读连接 `postgresql:///tensorplatform`，五组聚合 SQL：
   分母（pr-gate 回写 merged）、分子（复判记录按 mission 关联）、复核证据构成、复核供给侧逐日产出、
   待复核积压。
3. **分母对账**：用 `git log origin/main` 的 `Merge #N (mXXX) via merge-master` 合并提交做第三源交叉校验，
   逐日比对，检验平台自报回写是否与真实合并事实一致。
4. **阶段切分**：用"最后一次独立复核给出结论的日期"作切点（脚本自动判定，不手填），把分母劈成两段，
   避免用一个混合平均值掩盖结构性事实。
5. **出图**：`make_figures.py` 只读 `kpi_result.json` 出 6 张图（环境无 CJK 字体，图中标注为英文，
   图注在报告中中文说明，杜绝豆腐块）。
6. **L1 复算**：`recompute.sql` 用手写单条 SQL 独立重算分子分母，与脚本结果逐位比对（见 ⑥）。

**重计算通道**：按规划令"计算重走 cluster_queue"，本单把 `recompute.sql` 递交集群作业
`cj984`（TensorPlatform 仓，stage_dir=本工作树）做复核；本地同时保留等价复算（结果见 ⑥）。
集群作业在测量时刻仍处排队态，最终态见文末"作业状态"节。

## ③ 计算结果（数字表，程序化注入零手抄）

**主读数**

| 指标 | 值 |
|---|---|
| 分母 N（已合并 agent PR，去重） | **5315** |
| 分子 M（独立复核通过） | **274** |
| **KPI 现值（M/N）** | **5.16%** |
| 目标 | 100% |
| 缺口 | **94.84 个百分点**（5041 条 PR 无独立复核通过记录） |

**口径阶梯（同一分母 5315，只是"什么算复核通过"逐步收紧）**

| 级 | 定义 | 计数 | 覆盖率 |
|---|---|---|---|
| D0 | 有任何复核记录（含 pending 占位行） | 729 | 13.72% |
| D1 | 任意 kind 的 reviewed+confirmed（**含 auto_scan**） | 690 | 12.98% |
| **D2（本 KPI）** | `reviewer_kind='independent'` 的 reviewed+confirmed | **274** | **5.16%** |
| D3 | D2 且 `cross_model_verified`（异构模型交叉） | 3 | 0.06% |

**任务粒度（一个任务常迭代出多个 PR）**：3188 个任务在窗口内被合并，其中 239 个有独立复核通过 → 7.50%；
502 个任务有任何复核记录。

**复核质量**：在"有任何复核记录"的 729 条 PR 中，独立复核通过 274 条 → **37.59%**（其余多为 auto_scan 自动确认）。

**阶段切分（本次计量的头号发现）**

| 阶段 | 已合并 PR | 独立复核通过 | 通过率 |
|---|---|---|---|
| ≤ 2026-11-07（复核通道停产前） | 817 | 274 | **33.54%** |
| > 2026-11-07（停产之后） | 4498 | **0** | **0.00%** |

最后一次独立复核给出结论的时刻：**2026-11-07T06:43:24Z**；之后 `outcome_evaluations` 只新增
`pending` 占位行（11-09 36 条、11-10 1809 条、11-11 1025 条），无一条被裁决。

**分仓覆盖（全表见 `per_repo.csv`）**

| 仓 | 已合并 PR | 有任何复核 | 独立复核通过 | 通过率 |
|---|---|---|---|---|
| TensorLBM | 1067 | 186 | 36 | 3.37% |
| TensorDEM | 912 | 147 | 46 | 5.04% |
| ship-performance-platform | 897 | 0 | 0 | 0.00% |
| TensorPlatform | 561 | 57 | 19 | 3.39% |
| TensorFEM | 446 | 91 | 49 | 10.99% |
| TensorBEM | 334 | 66 | 27 | 8.08% |
| TensorPhysics | 264 | 22 | 3 | 1.14% |
| TensorFVM | 245 | 70 | 31 | 12.65% |
| TensorSPH | 174 | 38 | 23 | 13.22% |
| TensorPapers | 141 | 0 | 0 | 0.00% |
| TensorDG | 134 | 23 | 19 | 14.18% |
| TensorCAD | 107 | 29 | 21 | 19.63% |
| TensorKnowledge / TensorBench / TensorData | 28 / 4 / 1 | 0 | 0 | 0.00% |

**复核供给侧逐日产出**（`review_supply_daily`）：11-07 之后新增复判行 100% 是 `pending`，
11-11 仅 1 条 `auto_scan` confirmed。待复核积压：**2871 条 pending 行 / 2871 个任务**，
其中仅 11 个任务已有合并 PR——即积压的主体是"任务已完但 PR 未合"，复核队列在空转。

**复核时延**（仅对有复核记录的 729 条）：中位数 **3.22 h**，P90 **37.69 h**。
即"被复核的那部分"响应很快，问题不在速度，而在**产能停在 11-07**。

**分母对账（TensorPlatform 仓，重叠窗口 11-07~11-11）**

| 源 | 计数 |
|---|---|
| git `Merge #N (mX) via merge-master` 合并提交 | 591 |
| pr-gate 回写 merged（去重） | 561 |
| 比值 | **0.949**（偏差 5.1%，在 10% 内） |

但 30 天口径下：git 侧 TensorPlatform 合并提交 **1045** 条，pr-gate 回写只有 **561** 条覆盖
→ **回写覆盖率 53.68%**（11-06 清场前的合并没有回写）。若强行按 git 30 天分母计，
TensorPlatform 单仓通过率只有 **1.82%**。

**其它复核通道与已合并任务的重叠（说明主口径之外还有没有别的证据）**：

| 通道 | 覆盖的任务数 |
|---|---|
| `tensor_maturity_verdicts`（成熟度裁决） | 34 |
| `eval_redteam_rechecks`（盲评红队复算） | 3 |
| `review_budget_ledger`（复核官台账） | 8 行（全部是 11-07 的 IN24-E2 夹具数据） |
| `tensor_repro_verdicts` / `split_verdict_events` / `repro_verdict_votes` | 0 / 0 / 0 |

→ 除主口径外，没有任何通道在量级上承担复核职责。

## ④ 结果图

1. `fig1_definition_ladder.png` — 口径阶梯：D0 13.72% / D1 12.98%（含 auto_scan 虚高）/ **D2 5.16%** / D3 0.06%，
   红线为目标 100%。**读法**：D1 与 D2 之间那 7.8 个百分点的差就是"auto_scan 冒充满复核"。
2. `fig2_per_repo.png` — 分仓已合并量（灰）vs 独立复核通过（橙），括号为通过率。
   **读法**：ship-performance-platform（897 条）与 TensorPapers（141 条）**零复核记录**，是纯缺口区。
3. `fig3_daily.png` — 逐日合并量 vs 独立复核通过量。**读法**：11-08 起橙柱消失，视觉上就是"复核通道停产"。
4. `fig4_latency.png` — 复核时延直方图（中位 3.22 h，P90 37.69 h，24h 门为建议锚点）。
   **读法**：被复核的样本响应快，瓶颈不在单次复核速度。
5. `fig5_reconcile.png` — 分母对账：git 合并提交 vs pr-gate 回写逐日柱。
   **读法**：重叠窗口合计 591 vs 561，比值 0.95，回写可信；但 11-08 后逐日形态有偏（回写滞后/补记）。
6. `fig6_review_supply.png` — 复核供给侧逐日新增复判行（按状态堆叠）。
   **读法**：11-07 之后全是灰色 pending，绿/橙（有结论）几乎为零 → 供给停摆、队列空转。

## ⑤ 注意点（坑、局限、适用范围）

1. **30 天窗口没有 30 天数据**：pr-gate 回写最早记录 2026-11-04T23:52Z（11-06 清场重建后才有），
   实际有效窗口只有 11-05~11-11 约 7 天。任何"近 30 天"结论都只覆盖最后 7 天的合并。
   本报告因此额外给出 git 30 天分母的敏感性读数（1.82%）说明方向不变。
2. **分母只能信平台自报**：本环境无 `gh` CLI、无 GitHub 令牌，无法用 GitHub API 独立确认合并事实
   （`tensor_quality_patrol` 最近一轮亦记录"13/13 仓 GitHub 拉取失败"）。git 交叉校验只覆盖
   TensorPlatform 单仓（其余 14 仓不在本工作树），比值 0.95 → 回写未发现系统性虚报，但**未证伪**。
3. **混入口径会虚高**：若按 `status='confirmed'` 全量计（D1），通过率 12.98%，是真实值 5.16% 的 2.5 倍。
   `auto_scan` 是自动扫描而非独立复核，任何后续 KPI 复算必须显式排除。
4. **PR 粒度 vs 任务粒度**：5315 条 PR 来自 3188 个任务（668 个任务迭代出多个 PR）。复核记录挂在任务上，
   因此任务粒度通过率（7.50%）高于 PR 粒度（5.16%）。两个数都对，但不可混用。
5. **pending ≠ 已被复核**：2871 条 pending 行里只有 11 个任务已有合并 PR，其余是"任务完但未合"，
   直接用 pending 数当"复核缺口"会双重计数。
6. **审计链是 append-only 快照**：平台在计量期间仍在合并（本次运行分母从 5310 涨到 5315），
   重跑会有 0.1% 量级漂移，属正常活数据，不是脚本不稳定。
7. **本单不改任何东西**：以上缺口一律只提出派生锚点与派单建议，未修代码/未补数据。

## ⑥ 误差判定（关键量 vs 基准，逐项回答"是否 <3%"）

| 关键量 | 本次值 | 独立复算（`recompute.sql` 手写单条 SQL） | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| 分母 N | 5315 | 5315 | 0.00% | **是** |
| 分子 M | 274 | 274 | 0.00% | **是** |
| KPI 现值 | 5.16% | 5.16% | 0.00% | **是** |
| 分母 vs git 交叉源（TensorPlatform 重叠窗口） | 561 | 591（git） | 5.08% | **否**（>3%，属两源定义差异，非计算误差；已在 ⑤.2 声明） |

- 两条独立实现（`measure.py` 的 SQL 模板 / `recompute.sql` 的手写单条 SQL）在分子分母上**逐位一致**，
  故计算本身无误差。
- 唯一 >3% 的偏差是**分母两源之间**的 5.08%（pr-gate 回写 vs git 合并提交）——这是测量仪器的系统偏差，
  不是本报告的算术误差；在 10% 容差内接受，并记为待解缺口（见 `proposed_anchors` 的 KPI-QC-CROSS-CHECK）。
- **结论：KPI 现值 5.16% 是可复算的真实现值；不好看，如实报。**

## ⑦ 缺口与派单建议（本单只提建议，不执行）

| 优先级 | 缺口 | 证据 | 建议派生锚点 / 派单 |
|---|---|---|---|
| P0 | **复核供给侧自 11-07T06:43 起停产**：之后 4498 条合并 PR 零复核结论 | `phase_split` | 锚点 `KPI-QC-LATENCY-24H`；派"复核官复产"单：先把 pending 队列的裁决回路接通并每日消化 |
| P0 | 待复核队列空转：2871 条 pending 只建行不裁决 | `pending_backlog` | 与上同单：给复核席位设产能下限（每日裁决 ≥ 新增 pending） |
| P1 | 独立复核覆盖 5.16%，距目标 94.84 个百分点 | 主读数 | 锚点 `KPI-QC-COVERAGE-80`（≥80%，验收=SQL 复算 + 未复核清单条数 ≤ 20% 分母） |
| P1 | `auto_scan` 冒充满复核（D1 12.98% vs D2 5.16%） | `evidence_mix` | 锚点 `KPI-QC-INDEP-PURITY`（independent 占比 ≥90%，auto_scan 禁止计入分子） |
| P1 | ship-performance-platform(897)、TensorPapers(141)、TensorKnowledge(28) 等**零复核记录** | `per_repo.csv` | 派"零覆盖仓接线"单：把这三仓任务纳入复核席位派单队列 |
| P2 | 回写只覆盖清场后 7 天（TP 仓覆盖率 53.68%） | `sensitivity_30d` | 锚点 `KPI-QC-CROSS-CHECK`（分仓合并数与 git 偏差 ≤10%） |
| P2 | 无 GitHub 侧独立确认手段 | `top_gaps` | 派"合并事实第三方核验"单：接入 GitHub API/令牌核对合并事实 |

## ⑧ 作业状态（集群复核）

- 递交：`cj984`（TensorPlatform 仓，`stage_dir=/root/tp-agent/work/m15116`，
  `psql -f artifacts/kpi_qc_recheck/recompute.sql`），递交时刻 19:27Z，队列长度 204。
- 报告发布时刻（19:31Z）作业仍为 `queued`（集群负载 ~37/128 核、队列 204 深度）。
  本地 `recompute.sql` 等价复算已完成且与脚本逐位一致（5315 / 274 / 5.16%），
  **故集群复核在本单内未取得执行结果，如实记为 not_run / 未完成项，不作"通过"计**。
- 报告发布：tensor_reports id=14247（mission_id 15116）。

## 附：产物清单

```
artifacts/kpi_qc_recheck/
  measure.py            只读计量脚本（含全部 SQL 原文与定义阶梯）
  make_figures.py       出图脚本（只读 kpi_result.json）
  recompute.sql         独立第二实现（L1 数学验证）
  kpi_result.json       主结果：current/unit/target/gap/data_sources/top_gaps/proposed_anchors
  unreviewed_prs.csv    未复核清单 5041 行（repo, mission_id, pr, merged_at, 证据列）
  per_repo.csv          分仓覆盖
  daily.csv             逐日合并/复核
  latency.csv           合并→首次复核时延（729 行）
  fig1..fig6.png        6 张图
```