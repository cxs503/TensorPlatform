# KPI-SWEEP-1000 计量报告：无人值守批量工况累计完成数的真实现值

**锚点**：KPI-SWEEP-1000（mission 15118）｜**性质**：只计量、不改码不改数据｜**测量时刻**：cluster_jobs 快照 `submitted_at` 2026-11-09 ~ 2026-11-11
**交付物**：`artifacts/kpi_sweep_1000/kpi_sweep_1000_result.json`（result JSON）、`artifact_census.json`、`samples_census.json`、`stage/cluster_jobs_census.json`、`fig1_kpi_tiers_and_cluster.png`；脚本 `scripts/kpi_sweep_1000_census.py`、`scripts/kpi_sweep_1000_ledger.py`

---

## ① 案例介绍（要量什么）

指南 KPI：「**无人值守 ≥1000 工况**」。现网 canonical 实现是 `ops/taskplane/guide_tracker.py::kpis()` 里的**任务名代理口径**：

```sql
SELECT count(DISTINCT substring(goal from '案例 ([a-zA-Z0-9_]+)（'))      -- 结果 = 65
FROM tensor_dev_missions WHERE goal LIKE '%案例 %（Tensor%' AND status='succeeded';
```

该口径统计的是「goal 里出现过的验证案例名去重数」（wigley/suboff/…），**不是工况数**，且自注“案例级代理口径（每案例≈1-3 工况）；工况级精确口径待样本库上线”。本单把它换成**工况级**测量，并给出可复算的三档现值。

工作量规模（本次清点对象）：cluster_jobs 865 行；ship_samples 23,176 行 + kb_samples 11,861 行（二者重叠，见口径）；worktree 内 `artifacts/`+`runs/` JSON 工件 **157,831** 个文件，内容去重后 **8,392** 个不同工件。

## ② 怎么做的（口径 + 流程 + 工具链）

**工况(unit) 定义**：`(求解链 identity, 参数向量)` 的唯一实例，被无人值守批量执行器算至终态并留下可溯源记录（DB 行带 case_id/params/result，或工件内 per-case 记录）。

**三档互斥口径（只计一次）**

| 档 | 含义 | 现值 | 说明 |
|---|---|---|---|
| A 严格求解器批量 | 批量线逐工况工件（batch API 批量 + mission 级多工况工件），按 sha256 去重 | **255** | batch_sweep_demo 51 + m642 batch_report 130 + 9 个 mission 工件 74 |
| **B 公式链批量（headline）** | A + ship_samples `kb_*`（无人值守程序化批量回填，公式链） | **12,116** | 本文推荐 KPI 口径 |
| C 宽口径 | B + `geometry_validation` 单点几何自洽行（无物理量） | 23,431 | 仅作容量上限参考，不计 KPI |

**去重规则（这是本单最要紧的部分）**
1. 同一 sha256 工件 = 同一次执行。`batch_sweep_demo.json` 在 976 个 worktree 里各存一份（内容完全相同，md5 `4e3d6d4b…`）——按文件计数会虚增 49,752 工况，故只计 51。
2. `kb_samples`(11,861) 与 `ship_samples` 的 `kb_*` 行是同一批记录（`case_id` 一一对应），只计一次。
3. `geometry_validation` 11,315 行只有 `Cb_computed vs Cb_stored` 自洽检查，不含物理量/工况参数 → 不进 headline。
4. `cluster_jobs.job 数 ≠ 工况数`（其中含 pytest/探针/单算例）。故 cluster_jobs **不作工况折算**，只作执行侧旁证与瓶颈诊断（诚实声明：本单无法对所有 job 逐条恢复 per-case 记录，未做的工作写在这里）。

**L1 数学校验（数字 vs 源）**：对每个物理域抽样 400 行，用内部恒等式从 `params` 复算并比对落盘值（相对容差 1e-3，行内数值为 6 位有效数字）。

## ③ 计算结果（程序化注入，零手抄）

复算命令原文：

```bash
# 工件清单（IO 绑定的全盘清点，只在 99 上可做）
find /root/tp-agent/work/*/{artifacts,runs} -maxdepth 4 -type f -name '*.json' -printf '%s\t%p\n' > /tmp/artifact_manifest.txt   # 157,831 行
python3 - # 逐文件 sha256 → /tmp/artifact_hashes_full.tsv（去重键）
python3 scripts/kpi_sweep_1000_census.py samples   --in artifacts/kpi_sweep_1000/stage/ship_samples.json --out artifacts/kpi_sweep_1000/samples_census.json
python3 scripts/kpi_sweep_1000_census.py artifacts --manifest /tmp/artifact_manifest.txt --hashes /tmp/artifact_hashes_full.tsv --out artifacts/kpi_sweep_1000/artifact_census.json
python3 scripts/kpi_sweep_1000_ledger.py
```

```sql
-- 工况行清点（ship_samples；kb_samples 为镜像）
SELECT calc_type, count(*), count(DISTINCT tags->>'case_id') ids FROM ship_samples GROUP BY 1 ORDER BY 2 DESC;
-- geometry_validation 11315 | kb_resistance 2692 | kb_green_shipping 2500 | kb_seakeeping 2293 | kb_propulsion 2225 | kb_stability 1303 | kb_ice_navigation 848
SELECT state, mode, count(*) jobs, sum(coalesce(shards,1)) tasks FROM cluster_jobs GROUP BY 1,2;
```

| 量 | 值 | 来源 |
|---|---|---|
| headline 现值（B 档） | **12,116 工况** | 见 result JSON `current` |
| 目标 | 1,000 | KPI |
| 缺口 | **0**（B 档达标 1,211.6%；A 档 25.5%，缺 745） | 同上 |
| 批量线不同工件 / 含工况工件 | 8,392 / 11（255 工况） | artifact_census.json |
| shard 型任务终态 | succeeded **0**、killed 749、running 137、queued 98 | cluster_jobs_census.json |
| cluster 全量 | 865 job：succeeded 239、killed 391、failed 15、queued 205、running 15 | 同上 |
| `tensor_metric_samples` | **0 行**（KPI 无时序采样） | `SELECT count(*) FROM tensor_metric_samples` |

**图**：`artifacts/kpi_sweep_1000/fig1_kpi_tiers_and_cluster.png` 左=三档现值 vs 目标线（log），右=cluster_jobs 状态分布（batch-like job vs shard task，显示 shard 线 0 成功）。

## ④ 图（见 figures 内嵌）

图 1 左：A=255 / B=12,116 / C=23,431，目标线 1000；右：shard 型任务 succeeded=0（killed 749），是 distance-to-1000 的真实瓶颈。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **工件复制污染**：worktree 是 git 检出，被提交的工件（如 `batch_sweep_demo.json`）在每个分支复制一份 → 任何“文件计数”都会虚增 1~2 个数量级；必须内容哈希去重。本单 976→1。
2. **代理口径不可信**：guide_tracker 的 `sweep_succ=65` 与工况无关（是案例名去重），若照搬会低估 186 倍。
3. **退化值**：`batch_sweep_demo.json` 51 例中 10 例 `rt=0.0` 仍记 `success` → 计数需要有效性门。
4. **域内公式变体**：`kb_seakeeping.significant_heave` 实测 = **2σ**（400/400 自洽），与我假设的 4σ（Rayleigh）不同；`kb_ice_navigation` 的 `R_ice` 不等于分量和（例：R_ice 55.416 N vs 分量和 96.272 N）→ 冰区 848 行证据可复算性不足，KPI 计数不应无声吞下。
5. **局限**：只清点了 `artifacts/`+`runs/` 下 maxdepth 4 的 `.json`（<40MB）；npz/csv/图片型结果与更深层目录未清点，故 A 档是**下界**；cluster job 的 per-case 恢复未做。
6. 适用范围：本测量只回答“累计完成工况数”，不回答数值精度；精度 KPI（阻力 ≤5%/冰区 ≤10%）另单。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准/源 | 偏差 | 是否 <3% |
|---|---|---|---|
| headline 计数 12,116 | 重算：255(A 工件) + 11,861(kb_*) | 0（整数重算一致，见 ledger 脚本） | ✅ 是（0%） |
| A 档 255 | 11 个不同工件的 cases 求和（51+130+74） | 0 | ✅ 是 |
| 去重正确性 | 976 份 `batch_sweep_demo.json` 同 md5 → 计 1 | 若不去重虚增 49,752（>100×） | ✅ 是（去重已消除） |
| 工件内容差异识别 | 157,831 文件 → 8,392 不同 sha256 | 精确（sha256） | ✅ 是 |
| 阻力工况 `Cf=ITTC57(Re)` | 落盘 Cf | 400/400 通过（≤1e-3） | ✅ 是 |
| 阻力 `Rf=0.5ρV²S(1+k)Cf` | 落盘 RF_N | 400/400 通过 | ✅ 是 |
| 阻力 `Rt=Rf+Rw+Ra` | 落盘 Rt_N | 400/400（≤1e-3；1e-6 容差下 272/400 系 6 位有效数字舍入） | ✅ 是 |
| 稳性 `GM=KB+BM−KG` / `BM=B²/12T` | 落盘 GM_m/BM_m | 400/400、400/400 | ✅ 是 |
| 推进 `T=KTρn²D⁴` | 落盘 thrust_N | 400/400 | ✅ 是 |
| 推进 `η0=J·KT/(2πKQ)` | 落盘 eta0 | **355/400 = 88.75%**（45 行不符） | ❌ 否 → 该域 2,225 行证据部分 WITHHELD |
| 耐波 `GM_T_m=GM_T` | 落盘 | 400/400 | ✅ 是 |
| 耐波 `significant_heave=4σ` | 落盘 | **0/400**（实测为 2σ） | ❌ 否 → 定义偏离惯用 4σ，需落口径字段 |
| 绿色 `attained_ratio=attained/required` | 落盘 | 400/400 | ✅ 是 |
| 冰区 `R_ice=Σ分量` | 落盘分量 | **67/400 = 16.75%** | ❌ 否 → 冰区 848 行不计入可复算口径 |
| 冰区 `rel_err_vs_reference` 复算 | 落盘 relative_error_pct | 189/400（仅 189 行有 reference 值） | ❌ 否（部分） |

**honest 结论**：计数类指标精确可复算（0% 偏差）；**可复算性**只在阻力/稳性/耐波(GM)/绿色四域达标（≥99%），推进 η0 与冰区总阻力不达标 → headline 的 12,116 中约有 2,225(推进) + 848(冰区) = 3,073 行证据需降级，若按“L1 可复算”严格口径计，现值下限应为 **12,116 − 3,073 = 9,043 工况**，仍远超 1000。好看难看都如实写：数字达标，但能力结构不达标（求解器级批量仅 255，分片并行 0 成功）。

---

### 建议派生锚点（带一句验收）

| 锚点 | 验收判据（一句） |
|---|---|
| KPI-SWEEP-LEDGER-DAILY | 普查脚本日更写 `tensor_metric_samples(metric_key='kpi.sweep.cases.by_scope')`，连续 3 天有样本且与重算差 <1% |
| KPI-SWEEP-SHARD-FIRST | 出现 1 个 `state=succeeded 且 mode=shard` 作业（≥8 shard），每 shard 落逐工况工件，可点出 ≥80 工况 |
| KPI-SWEEP-VALIDITY-GATE | 批量线工件中退化值（rt≤0/缺参）行数 = 0，门禁写进 batch API 的 `validity` 字段 |
| KPI-SWEEP-ICE-IDENTITY | `kb_ice_navigation` 848 行中 ≥95% 通过 `R_ice=Σ分量` 恒等式，或在行内落 `formula_version` 说明差异 |
| KPI-SWEEP-SOLVER-CASES-1000 | 求解器级（LBM/FEM/DEM/BEM/FVM）无人值守批量 ≥1000 工况，逐 case 工件含 `solver_version`+收敛判据 |