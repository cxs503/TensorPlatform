# 交差审计 m15118 / KPI-SWEEP-1000：数字可复现（Δ=0），但基准裁判空表 → 判 L2

**审计单** m15148（board-audit）｜**被审单** m15118（succeeded）｜**被审 commit** `2891a88f`｜**裁判** `tensor_benchmark_cases(anchor_slug='KPI-SWEEP-1000')`
**一句话**：被审单的计数、去重、镜像、恒等式四类声明**经独立实现复算全部对上**（headline 12,116，重跑 ledger Δ=0），但**唯一裁判是空表（0 行）**、且无集成调用、无落地测试 → 判 **L2 独立功能成果（confirmed）**，L3/L4 证据为空，不得上抬。

## ① 案例介绍（审什么、物理背景与几何）

m15118 交付的不是求解器，而是**计量**：回答「无人值守累计完成工况数 ≥1000」这个 KPI。它把"一个工况"定义为 `(求解链 identity, 参数向量)` 的唯一实例、被无人值守批量执行器算至终态并留可溯源记录，并给出三档口径：

| 档 | 定义 | 申报值 |
|---|---|---|
| A 严格求解器批量 | 批量线逐工况工件（batch API + mission 级多工况工件），sha256 去重 | 255 |
| **B headline（公式链批量）** | A + `ship_samples.kb_*` 无人值守程序化回填 | **12,116** |
| C 宽口径 | B + `geometry_validation` 几何自洽行（无物理量） | 23,431 |

被计的数据本体为六域工况行：阻力 2,692（Holtrop-1984 链）、绿色航行 2,500（CII 参考线）、耐波 2,293（SDOF+谱矩）、推进 2,225（BEM 敞水点）、稳性 1,303（exact GZ）、冰区 848（MT Uikku）；外加 11,315 行几何 Cb 自洽校验。

## ② 怎么做的（审计方法、关键设置、工具链）

审计脚本**独立实现**（`docs/board_audit/m15148/verify_m15118.py`，不复用被审代码），六问：

1. **C1 计数**：SQL 直接数 `ship_samples`(按 calc_type) 与 `kb_samples`，重算 A/B/C；
2. **C2 镜像**：`kb_samples.case_id` ↔ `ship_samples.tags->>'case_id'` 双向 `EXCEPT`（验证"只计一次"前提）；
3. **C3 质量**：五域**重新抽样 300 行/域，seed=20261111**（与被审的 400 行样本不同），独立复算 Cf/Rt/η0/T/GM/σ_heave/R_ice 恒等式，容差 1e-3；
4. **C4 去重**：全 worktree 抓 `batch_sweep_demo.json`，逐份 sha256；
5. **C5 基准裁判**：查 `tensor_benchmark_cases`；
6. **C6 可复现**：把被审 `artifacts/`+`scripts/` 拷到临时目录**重跑 ledger**，与入库 JSON 逐键 diff。

工具链：psql（`runuser -u postgres`）、Python 3 + hashlib/json、matplotlib(Agg)。数据源 `tensorplatform` 库（`ship_samples` 23,176 行 / `kb_samples` 11,861 行 / `cluster_jobs`）+ 被审工件树。

## ③ 计算结果（数字表，程序化注入）

| 量 | 申报 | 审计复算 | Δ |
|---|---|---|---|
| headline B | 12,116 | **12,116** | **0**（重跑 ledger 亦 12,116） |
| A 档 | 255 | 255（11 工件求和） | 0（其中 14 行非工况 → A′=241） |
| C 档 | 23,431 | 23,176（kb+geo 口径） | −255（口径差） |
| kb↔ship 镜像 | 一一对应 | 双向差集 0 / 0 | 0 |
| `batch_sweep_demo.json` | 976 份→51 工况 | 982 份、distinct sha256 = **1** | ✅ 去重正确 |
| shard 作业 succeeded | 0 | 0 | 0（瓶颈结论成立） |
| 阻力 Cf=ITTC57 | 400/400 | 300/300 = 1.000 | 0 |
| 阻力 Rt=Σ | 0.68（标 1e-3） | **1.000 @1e-3** | +0.32（标注缺陷） |
| 稳性 GM=KB+BM−KG | 1.0 | 1.000 | 0 |
| 推进 η0=J·KT/(2πKQ) | 0.8875 | 0.9067 | +0.02（抽样） |
| 冰区 R_ice=Σ分量 | 0.1675 | 0.150 | −0.02（问题真实） |
| 冰区 vs 外部参考 <10% | 未报 | **0.930** | 新增结论 |
| 基准裁判登记行 | — | **0**（全表 13 行） | ⛔ 不可判 |

## ④ 结果图

图 1（`fig_audit_m15118.png`）：左＝三档口径申报 vs 独立复算（log 轴 + KPI 目标线 1000，B 完全重合、A 标出 14 行非工况、C 差在口径定义）；右＝L1 恒等式合格率申报 400 行 vs 审计重抽 300 行（阻力/稳性 1.0 重合，推进与冰区同向偏低——证明"问题真实但非抽样噪声"）。
图 2（原单 `fig1_kpi_tiers_and_cluster.png`）：被审单自绘的三档现值 + cluster 状态分布，供并排核对。

## ⑤ 注意点（发现，按严重度）

- **F1 HIGH 证据面——基准裁判空表**：`tensor_benchmark_cases` 本锚点 0 行，KPI"达标"完全依赖自定口径 → L4 结构性不成立。建议派生 benchmark case：把"复算程序 + 期望 headline + ≤1% 容差"登记为算例。
- **F2 HIGH 口径**：headline 12,116 由公式链回填 11,861 支撑；严格求解器档 A=255（25.5%），且 A 混入 14 行 `failclosed_matrix`（测试影响选择矩阵，非工况）→ A′=241。原单已诚实披露 A=255 与"能力密度偏低"，**不构成虚标**。
- **F3 MEDIUM 可复现**：重跑 ledger **非幂等**（入库件含 `current_l1_verified_lower_bound/_note` 两键，脚本不产出）；输入快照 `stage/ship_samples.json`(24.6 MB) **未入 git** → 干净检出不可复跑（SQL 可复原，但未见复原脚本入库）。
- **F4 MEDIUM 标签**：ledger 把 **1e-6 容差**的 `Rt=Rf+Rw+Ra`(0.68) 标成 `Rt=sum(1e-3 tol)`，而 1e-3 下实为 1.0 → 低估自身质量、读者无法辨容差。
- **F5 MEDIUM 质量主张**：冰区"R_ice≠Σ分量"一刀切降级 848 行过严——93% 行与 `reference_value_N` 误差 <10%，属分量标度/公式变体，宜保留并落 `formula_version`（与原单 `KPI-SWEEP-ICE-IDENTITY` 提议一致）。
- **F6 MEDIUM 集成**：commit 无 `tests/`；脚本外零调用方（grep）；`tensor_metric_samples` 0 行（KPI 无时序采样）→ L3 不成立。
- **F7 LOW 漂移**：`cluster_jobs` 快照 865 job/queued 205（19:33）→ 审计时 869/209；已落快照可追溯，读值须带时间戳。
- **F8 正例**：去重与镜像两条"只计一次"前提经独立复核成立（982 份拷贝同 sha256；双向差集 0）——这是本单最有价值的机制贡献，可复用于任何"文件即成果"的计数。
- **局限**：同库同树复核，代码独立但数据源不独立；恒等式为抽样非全量；未复跑 157,831 文件全盘 sha256；未核验每行是否真由无人值守执行器产生。

## ⑥ 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 基准 | 偏差 | <3%？ |
|---|---|---|---|
| headline 计数 12,116 | 独立重算 + 重跑 ledger | **0.0%** | ✅ 是 |
| A 档 255 | 11 工件 cases 求和 | 0.0%（口径内 −5.5% 非工况行） | ✅ 数值是 / 口径否 |
| kb↔ship 镜像 | 双向差集 | 0 行 | ✅ 是 |
| 阻力/稳性/耐波/绿色恒等式 | 落盘值 | 0（300/300） | ✅ 是 |
| 推进 η0 恒等式 | 落盘值 | 申报 11.25% 失败、审计 9.33% 失败 | ❌ 否（该域部分 WITHHELD） |
| 冰区 Σ 恒等式 | 落盘分量 | 申报 83.25% 失败、审计 85% 失败（但外部参考 93% 一致） | ❌ 否 |
| 基准裁判 | `tensor_benchmark_cases` | 无登记 | ⛔ NOT_SUPPORTED |

**判级**：L2 独立功能成果（impl_files 两脚本非占位 + entry `python3 scripts/kpi_sweep_1000_ledger.py` 审计实测 exit 0 + run_artifacts 5 件真工件；计数独立复算 Δ=0）。**L3 ✗**（无集成调用方）、**L4 ✗**（无基准算例、repro 非幂等）、**L5 ✗**。**Disposition：approve with notes / L2 confirmed**；禁止对外宣称"无人值守求解器级 1000 工况"——严格口径 255（扣非工况 241）。