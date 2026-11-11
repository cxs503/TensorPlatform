# KPI-QC-RECHECK：已合并 agent PR 独立参考复核通过率 —— 计量能力交付与实测值

- 计量时刻（脚本打点）：`2026-11-11T20:35:42.827929Z`
- 能力入口：`python3 -m tools.kpi_qc_recheck.measure --days 30 --outdir artifacts/kpi_qc_recheck`
- 独立复算：`python3 -m tools.kpi_qc_recheck.verify --raw-dir ... --result ... --out ...`
- 平台侧真调用：`backend/services/platform_kpi.py::_compute_qc_recheck` → `GET /api/v1/tensor/kpi/platform_dashboard` 的 `qc_recheck_rate` 维度
- 计量纪律：只读（连接置 read_only，全部 SELECT）；本能力不改任何被计量数据

## ① 案例介绍（计量对象与口径）

被计量对象不是物理场，而是**平台质量控制回路本身**：近窗口期内由智能体产出、经合并口进入主干的每一份交付（agent PR），有多少份被**独立参考复核**过并且通过。

| 项 | 定义 | 数据源 |
|---|---|---|
| 分母 N | 窗口内已合并 agent PR，按 `(mission_id, repo, pr)` 去重 | `mission_lifecycle_log`，`action='pr_gate'` 且 `detail->>'state'='merged'` |
| 分子 M | 该 mission 存在 `reviewer_kind='independent'` 且 `review_status='reviewed'` 且 `status='confirmed'` 的记录 | `outcome_evaluations` |
| 指标 | M / N × 100%（目标 100.0） | 本能力两条独立实现 |

分子排除 `auto_scan`（平台证据自动扫描不是「另一双眼睛」）：把它算进分子会把读数虚高，虚高量正是定义阶梯 D1 − D2。

## ② 怎么做的（方法/工具链）

1. `tools/kpi_qc_recheck/queries.py`：只读 SQL（分母/复核记录/供给侧/积压）+ 两个 Reader（psycopg 直连 = 离线计量；SQLAlchemy Session = 平台 API 内联调用，口径单一真源）。
2. `tools/kpi_qc_recheck/measure.py`：纯函数聚合（`dedupe_denominator` → `index_evaluations` → `aggregate`），产出主读数 + 口径阶梯 + 分仓 + 逐日 + 时延 + 积压 + 未复核清单，并落盘原始行 CSV 与逐件 sha256 的 provenance。
3. `tools/kpi_qc_recheck/verify.py`：**第二实现**（CSV set 交集法 / 手写单条 SQL），与自报值逐项比对（计数要求精确相等）。
4. `scripts/kpi_qc_recheck_integration_probe.py`：建真实 SQLAlchemy Session 调 `services.platform_kpi.compute_all_kpis(db)`，证明面板读数=脚本读数。
5. `scripts/kpi_qc_recheck_figures.py`：只读 `kpi_result.json` 出 4 张图。
6. 测试：`tests/test_kpi_qc_recheck.py`（17 例，脱库，断言不变式而非「跑过」）。

## ③ 计算结果（程序化注入，零手抄）

| 指标 | 值 |
|---|---|
| 分母 N（已合并 agent PR，去重） | **5362** |
| 分子 M（独立复核通过） | **274** |
| **KPI 现值（M/N）** | **5.11%** |
| 目标 / 缺口 | 100.0% / **94.89 个百分点** |
| 覆盖 mission 数 | 3224 个任务被合并，239 个有独立复核通过 |

**口径阶梯（同一分母）**

| 级 | 定义 | PR 数 | 覆盖率 |
|---|---|---|---|
| D0 | D0 有任何复核记录（含 pending 占位行） | 748 | 13.95% |
| D1 | D1 任意 kind 的 reviewed+confirmed（含 auto_scan） | 690 | 12.87% |
| D2 | D2 independent 的 reviewed+confirmed（**主读数**） | 274 | 5.11% |
| D3 | D3 D2 且 cross_model_verified | 3 | 0.06% |

**阶段切分（切点=最后一次独立复核给出结论的日期，脚本自动判定）**

| 阶段 | 已合并 PR | 独立复核通过 | 通过率 |
|---|---|---|---|
| ≤ 2026-11-07 | 817 | 274 | 33.54% |
| > 2026-11-07 | 4545 | 0 | 0.0% |

**分仓覆盖（前 10）**

| 仓 | 已合并 PR | 有任何复核 | 独立复核通过 | 通过率 |
|---|---|---|---|---|
| TensorLBM | 1068 | 190 | 36 | 3.37% |
| TensorDEM | 914 | 147 | 46 | 5.03% |
| ship-performance-platform | 904 | 3 | 0 | 0.0% |
| TensorPlatform | 580 | 60 | 19 | 3.28% |
| TensorFEM | 448 | 91 | 49 | 10.94% |
| TensorBEM | 335 | 68 | 27 | 8.06% |
| TensorPhysics | 266 | 24 | 3 | 1.13% |
| TensorFVM | 250 | 70 | 31 | 12.4% |
| TensorSPH | 175 | 38 | 23 | 13.14% |
| TensorPapers | 146 | 4 | 0 | 0.0% |

**复核供给侧/积压**：待裁决 pending 行 **2882** 行（2882 个任务，其中 18 个任务已 succeeded）。

**复核时延**（合并→首个独立复核结论，n=272）：中位 **15.99 h**，P90 **45.09 h**。

## ④ 结果图

1. `fig1_definition_ladder.png` — 口径阶梯 D0/D1/D2/D3 + 100% 目标线。**读法**：D1 与 D2 之间的落差就是 auto_scan 冒充满复核的虚高量。
2. `fig2_per_repo.png` — 分仓已合并量（灰）vs 独立复核通过（橙）。**读法**：灰柱高、橙柱贴地的仓=零覆盖缺口区。
3. `fig3_daily.png` — 逐日合并 vs 独立复核通过。**读法**：后半段橙柱消失=复核通道停产。
4. `fig4_review_supply.png` — 复核供给侧逐日状态堆叠。**读法**：堆叠里灰（pending）为主=队列只建行不裁决。

## ⑤ 注意点（坑、局限、适用范围）

1. **窗口是滚动的**：`created_at > now() - 30 days`，两次读数之间新合并 PR 会进分母，故面板与脚本读数允许分母只增、比率差 ≤0.01pp 的漂移（见 `integration_call.json` 的 `drift_tolerance`，本次实测 drift=0）。
2. **回写窗口短于 30 天**：pr-gate 回写最早记录在 11-05，实际有效窗口约 7 天，任何「近 30 天」结论只覆盖这段（详见 per_repo/daily）。
3. **分母只能信平台自报**：本环境无 GitHub 令牌，无法用 GitHub API 独立确认合并事实；本轮以 `git log` 合并提交作第三源敏感性对照（`sensitivity_git`）。
4. **修复的既有缺陷（本单为打通 L3 真调用所必需）**：`_safe_table` 失败未回滚导致 PostgreSQL 事务 aborted 连坐其它 KPI；`metered_usage` 表在但缺 `repro_digest` 列导致 整块 KPI 面板 500。两处均改为容错回退，不改变原 4 维语义。
5. **适用范围**：只回答「合并交付中有多少被独立复核通过」，不评价交付质量本身。

## ⑥ 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）

| 关键量 | 本次值 | 基准（独立第二实现） | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| denominator_merged_prs（CSV set 交集法） | 5362 | 5362 | 0.00% | **是**（0.00%） |
| numerator_ind_reviewed_prs（CSV set 交集法） | 274 | 274 | 0.00% | **是**（0.00%） |
| kpi_current_pct（CSV set 交集法） | 5.11 | 5.11 | 0.00% | **是**（0.00%） |
| ladder_D1_prs（CSV set 交集法） | 690 | 690 | 0.00% | **是**（0.00%） |
| denominator_merged_prs（手写单条 SQL） | 5362 | 5362 | 0.00% | **是**（0.00%） |
| numerator_ind_reviewed_prs（手写单条 SQL） | 274 | 274 | 0.00% | **是**（0.00%） |
| kpi_current_pct（手写单条 SQL） | 5.11 | 5.11 | 0.00% | **是**（0.00%） |
| ladder_D1_prs（手写单条 SQL） | 690 | 690 | 0.00% | **是**（0.00%） |

- 平台侧面板 vs 脚本：分子 274 vs 274（相等=True），比率差 0.0 pp。
- 第三源（psql 跑 `recompute.sql`）原文：

```
-- 命令: psql -U postgres -h /var/run/postgresql -d tensorplatform -f artifacts/kpi_qc_recheck/recompute.sql
-- 执行时刻: 2026-11-11T20:35:45Z
 n_merged_prs | m_ind_reviewed | rate_pct 
--------------+----------------+----------
         5362 |            274 |     5.11
(1 row)
```

- **结论：KPI 现值 5.11% 是可复算的真实现值；不好看，如实报。**

## ⑦ 缺口与建议锚点（只提建议，不执行治理动作）

| 优先级 | 缺口 | 证据 | 建议锚点 |
|---|---|---|---|
| P1 | 独立复核覆盖率距 100% 目标的缺口（见 current/gap） | `kpi_result.json` | — |
| P2 | 复核供给侧是否停产（见 review_supply 逐日状态堆叠；有结论行占比） | `kpi_result.json` | — |
| P3 | 待裁决积压（见 pending_backlog.pending_rows，只建行不裁决=队列空转） | `kpi_result.json` | — |
| P3 | 零复核记录仓（见 per_repo 中 ind_reviewed=0 且 merged_prs>0） | `kpi_result.json` | — |
| P3 | auto_scan 冒充满复核的虚高量（D1 - D2 个百分点差） | `kpi_result.json` | — |
| — | — | — | `KPI-QC-LATENCY-24H`：合并→独立复核结论时延 P90 ≤ 24h（由 latency.p90_h 逐日复算） |
| — | — | — | `KPI-QC-COVERAGE-80`：独立复核覆盖率 ≥ 80%，验收=recompute.sql 复算一致且未复核清单条数 ≤ 20% 分母 |
| — | — | — | `KPI-QC-INDEP-PURITY`：分子中 independent 占比 ≥ 90%，auto_scan 不得计入分子（D2 与 D1 差值 ≤ 1pp） |

## 附：达标证据映射（L1/L2/L3/L4）

| 级别 | 要求 | 本单工件 |
|---|---|---|
| L1 | 测试 | `tests/test_kpi_qc_recheck.py`（17 例全绿，含不变式/篡改 fail-closed/集成） |
| L2 | 非占位实现 + 入口 + 真实运行工件 | `tools/kpi_qc_recheck/{spec,queries,measure,verify}.py`；入口 `python3 -m tools.kpi_qc_recheck.measure`；工件 `artifacts/kpi_qc_recheck/`（kpi_result.json、unreviewed_prs.csv、per_repo.csv、daily.csv、raw_*.csv、provenance.json） |
| L3 | 平台侧真调用 | `backend/services/platform_kpi.py::_compute_qc_recheck` → `compute_all_kpis`（`GET /tensor/kpi/platform_dashboard`）；实调用工件 `integration_call.json`（numerator=274, denominator=5362） |
| L4 | benchmark(ref vs got) + repro_cmd | `benchmark_raw.json`（CSV 复算）、`benchmark_db.json`（手写 SQL）、`recompute_psql.txt`（psql 第三源）、`cluster_job.json`（集群作业 **cj1010 交付时刻仍 queued，记为 not_run 不计通过**）；repro_cmd 见 §② 与 `kpi_result.json.repro_cmd` |

### provenance 摘要（逐件 sha256）

| 文件 | sha256（前 16 位） | 字节 |
|---|---|---|
| daily.csv | `bde5f14e50f2ccac` | 161 |
| kpi_result.json | `e984cbcdcdf99ae1` | 41082 |
| per_repo.csv | `7d34fc5f47789ed8` | 524 |
| raw_denominator.csv | `1369b05d1951c283` | 278240 |
| raw_evaluations.csv | `0a29c63fdea0e54d` | 35238 |
| recompute.sql | `2b4f9d1b2d09d5e4` | 958 |
| unreviewed_prs.csv | `38cb0a9513bbcbbf` | 295101 |

- result_sha256（去挥发键）：`6435f2c2a6088ad9edbde29115cddc63`
- spec_sha256：`ec5c2d5d3cee53c3f03109392982bef0`
