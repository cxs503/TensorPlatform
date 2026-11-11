# 交差审计：m15118 / KPI-SWEEP-1000（TensorPlatform）

**审计单**：m15148（board-audit）｜**被审单**：m15118（status=succeeded）｜**被审 commit**：`2891a88f`（"KPI-SWEEP-1000 计量：工况级三档口径实测 B=12116/A=255/C=23431…"）｜**审计时刻**：2026-11-11
**裁判**：`tensor_benchmark_cases WHERE anchor_slug='KPI-SWEEP-1000'`（唯一机器判据）｜**结论**：**L2 独立功能成果，confirmed**；L3/L4 证据为空，不得上抬。

---

## ① 案例介绍（审什么）

m15118 的交付不是求解器能力，而是一次**计量**：回答"无人值守累计完成工况数"这个 KPI（目标 ≥1000）。它定义了三档口径：

| 档 | 含义 | 申报值 |
|---|---|---|
| A 严格求解器批量 | 批量线逐工况工件（batch API 批量 + mission 级多工况工件），按 sha256 去重 | 255 |
| **B headline（公式链批量）** | A + `ship_samples.kb_*` 无人值守程序化批量回填 | **12,116** |
| C 宽口径 | B + `geometry_validation` 单点几何自洽行（无物理量） | 23,431 |

交付物：`scripts/kpi_sweep_1000_census.py`(296 行)、`scripts/kpi_sweep_1000_ledger.py`(142 行)、`artifacts/kpi_sweep_1000/{kpi_sweep_1000_result.json, artifact_census.json, samples_census.json, stage/cluster_jobs_census.json, fig1_kpi_tiers_and_cluster.png}`、`docs/kpi_sweep_1000_census.md`、report id 14254。

## ② 怎么做的（审计方法：独立实现，不复用被审代码）

自建 `verify_m15118.py`（本目录），逐项独立复算，产物 `verify_output.json`：

1. **C1 计数**：直接 SQL 数 `ship_samples` 按 `calc_type` 与 `kb_samples`，重算 A/B/C；
2. **C2 镜像**：`kb_samples.case_id` ↔ `ship_samples.tags->>'case_id'` 双向 EXCEPT（"只计一次"的前提）；
3. **C3 质量**：对 resistance/propulsion/ice/stability/seakeeping 各**重抽 300 行（seed=20261111，与被审 400 行样本不同）**，用 numpy/纯 Python 独立复算恒等式；
4. **C4 去重**：全 worktree 找 `batch_sweep_demo.json`，逐份 sha256；
5. **C5 基准裁判**：查 `tensor_benchmark_cases`；
6. **C6 可复现**：把 m15118 的 `artifacts/`+`scripts/` 拷到临时目录**重跑 ledger**，与入库 JSON 逐键 diff。

## ③ 计算结果（数字表，全部程序化产出）

| 量 | 申报 | 审计复算 | Δ | 判读 |
|---|---|---|---|---|
| headline（B 档） | 12,116 | **12,116** | **0** | ✅ 可复现（重跑 ledger 亦 12,116，Δ=0） |
| A 档 | 255 | 255（11 工件求和） | 0 | ⚠️ 含 14 行**非工况**（m11074 `failclosed_matrix.json` 是测试影响选择矩阵）→ A′=241 |
| C 档 | 23,431 | 23,176（kb+geometry 口径） | −255 | 口径差（申报含 A），非错误 |
| kb 工况行 | 11,861 | 11,861 | 0 | ✅ |
| kb↔ship 镜像 | "一一对应" | 双向 EXCEPT 均为 0 | 0 | ✅ 只计一次成立 |
| geometry_validation | 11,315 | 11,315 | 0 | ✅ 不计入 headline 合理 |
| 去重（batch_sweep_demo） | 976 份→51 工况 | **982 份、distinct sha256=1** | — | ✅ 去重正确（且拷贝数仍在增长） |
| shard 型作业 succeeded | 0 | 0 | 0 | ✅ 瓶颈结论成立 |
| 恒等式 阻力 Cf=ITTC57 | 400/400 | 300/300 = 1.0 | 0 | ✅ |
| 恒等式 阻力 Rt=Σ | 0.68（标注 1e-3） | **1.0 @1e-3** | +0.32 | ❌ 标注错（0.68 实为 1e-6 容差） |
| 恒等式 稳性 GM | 1.0 | 1.0 | 0 | ✅ |
| 恒等式 推进 η0 | 0.8875 | 0.9067（重抽） | +0.02 | ✅ 同向（抽样差） |
| 恒等式 冰区 R_ice=Σ | 0.1675 | 0.15（重抽） | −0.02 | ✅ 同向，问题真实 |
| 冰区 vs 外部参考 <10% | 未报 | **279/300 = 93%** | — | 新增：冰区物理与参考值一致，恒等式失配属标度/公式变体 |
| 基准裁判登记行 | — | **0 行**（全表 13 行） | — | ❌ 本锚点无算例/容差 |

**图**：`fig_audit_m15118.png`（左：申报 vs 复算三档 log 柱状 + 目标线；右：恒等式合格率 申报 400 行 vs 审计 300 行重抽）。

## ④ 结果图说明

见上，两幅子图均由 `fig_audit_m15118.py` 从 `verify_output.json` + evidence 工件直读生成，零手抄。

## ⑤ 注意点（发现，按严重度）

| # | 严重度 | 发现 | 证据 | 处置 |
|---|---|---|---|---|
| F1 | **HIGH（证据面）** | **无基准裁判**：`tensor_benchmark_cases` 本锚点 0 行 → KPI 达标完全依赖自定口径，L4 不可能成立 | `verify_output.json:C5_anchor` | 我方 verdict 显式 `benchmark.status=NO_BENCHMARK_CASE`；建议派生锚点登记"复算程序+期望值+容差"作 benchmark case |
| F2 | **HIGH（口径）** | headline ≥1000 由公式链回填 11,861 支撑；严格求解器档 A=255（25.5%）。且 A 混入 14 行非工况（`failclosed_matrix` 测试矩阵） | C1/C4 + 工件原文 | 原单已在 ⑤/⑥ 与 `top_gaps` 诚实披露，**不构成虚标**；A 档计数应扣 14 → 241 |
| F3 | MEDIUM（可复现） | 重跑 ledger **非幂等**：入库件含 `current_l1_verified_lower_bound/_note` 两键，脚本不产出；输入快照 `stage/ship_samples.json`(24.6 MB) **未入 git** → 干净检出不可复跑 | C6_repro | 判 L2 仍成立（产物+入口真在，重跑得同一 headline）；返修项：快照入库或写 SQL 复原脚本 |
| F4 | MEDIUM（标签） | ledger 把 1e-6 容差的 `Rt=Σ`(0.68) 标成 `Rt=sum(1e-3 tol)`，而 1e-3 下审计为 1.0 → **低估自身质量**，且读者无法辨容差 | C3 + `samples_census.json` | 报告口径标注缺陷，交返修修正标签 |
| F5 | MEDIUM（质量主张） | 冰区"R_ice≠Σ分量"判为证据降级过严：93% 行与 `reference_value_N` 误差 <10% | C3 | 建议改判：保留行 + 落 `formula_version`（与原单 `KPI-SWEEP-ICE-IDENTITY` 提议一致） |
| F6 | MEDIUM（集成） | commit 无 `tests/`；脚本外**零调用方**；`tensor_metric_samples` 0 行（KPI 无时序采样） | grep + SQL | L3 不成立；原单已列为 gap |
| F7 | LOW | `cluster_jobs` 现值漂移（19:33 865 job/queued 205 → 现在 869/209）；已落快照，可追溯 | C1/快照 | 无动作，读值须带时间戳 |
| F8 | **正例** | 去重与镜像两条"只计一次"前提**经独立复核成立**（982 份拷贝同 sha256；双向差集 0）——这正是本单最有价值的机制贡献 | C2/C4 | 计入能力 |

## ⑥ 误差判定（是否 <3%）

| 关键量 | 基准 | 偏差 | <3%？ |
|---|---|---|---|
| headline 计数 | 独立重算 + 重跑 ledger | **0.0%** | ✅ 是 |
| A 档计数 | 11 工件 cases 求和 | 0.0%（但含 14 行非工况，口径内 −5.5%） | ✅ 数值是；口径否 |
| kb↔ship 镜像 | 双向差集 | 0 行 | ✅ 是 |
| 阻/稳/耐波/绿 恒等式 | 落盘值 | 0（300/300） | ✅ 是 |
| 推进 η0 恒等式 | 落盘值 | 申报 11.25% 失败 / 审计 9.33% 失败 | ❌ 否（域级证据部分 WITHHELD） |
| 冰区 Σ 恒等式 | 落盘分量 | 申报 83.25% 失败 / 审计 85% 失败 | ❌ 否（但外部参考 93% 一致） |
| **基准裁判（唯一裁判）** | `tensor_benchmark_cases` | **无登记 → 不可判** | ⛔ NOT_SUPPORTED |

## 判级与处置

- **L0** 否：有可执行实现，非纯文档。
- **L1** 否（更高级已满足）：commit 内无测试文件；但审计已做端到端重跑复现。
- **L2 ✅ 成立**：`impl_files`（两脚本，非占位）+ `entry`（`python3 scripts/kpi_sweep_1000_ledger.py` 审计实测 exit 0）+ `run_artifacts`（5 件真工件，含 PNG），且**计数独立复算 Δ=0**。
- **L3 ✗**：`integration_callers` 空（生态仓 grep 无脚本外引用；`tensor_metric_samples` 0 行）。
- **L4 ✗**：`benchmark` 无登记算例（唯一裁判空表），`repro_cmd` 非幂等且缺输入快照。
- **L5 ✗**：无新方法主张。

**Disposition：approve with notes / level=2 confirmed。** 数字真、口径诚实（原单自己写明 A=255、shard 0 成功、3,073 行证据降级），不判虚标；但**不得**当作"无人值守规模化能力已达 1000 工况"对外宣称——按严格求解器口径实际 255（扣非工况 241）。

**未做（unknown/not_run，不得视为 pass）**：全盘 157,831 文件 sha256 重扫；11,861 行逐行恒等式复算；cluster job 级 per-case 恢复；kb_samples 行"是否真无人值守产生"的溯源核验。
## 落表与机器裁定（tensor_maturity_verdicts）

- **落表**：`INSERT ... VALUES ('TensorPlatform','m15118 KPI-SWEEP-1000',15148,2,<evidence>, 'board-audit')` → **verdict id=231**（evidence 见 `verdict_m15118.json`，含 impl_files/entry/run_artifacts/tests/integration_callers/benchmark/repro_cmd/external_anchor/audit_findings/audit_recompute）。
- **gate 干跑复核**（用守护自己的判据函数，不写库）：`judge(2, ev, 'TensorPlatform', 15148) -> (2, [])`，即 L2 三键（impl_files 非占位 / entry / run_artifacts）全过，无缺项；`judge(4, ...) -> (2, ['L4 缺: integration_callers','L3 缺: integration_callers'])` 印证 L3/L4 不成立。
- **gate_state 现状 = pending 是正确行为**：`maturity_gate.gate_once()` 对 `a.status in (running, claimed, queued)` 的审计单直接 `continue`（审计在飞不裁），本审计单 m15148 当前 running，故 19:47 那一轮批次（226–230）按计划跳过我这条；本单终态后下一轮（30min 周期）按 `gl=2≥2` 走 **confirmed** 并 ingest 为 knowledge_base 能力条目 `cap:tensorplatform:m15118_KPI-SWEEP-1000`。
- **同批顺带观察（非本审计结论）**：19:47 一轮把 4 条 L2+/L4 claim 降到 L1 并开返修（m14670 WINKLER L4→L1、m15119 KPI-SUCCESS-90 L2→L1、m14672 GAP-LBM-SPHERE L2→L1、m14960 L1→L1），仅 m15126 KPI-CYCLE-50-30 与 m14897 H5-CALIB-REFLUX 维持 confirmed——说明"证据键缺失=降级"是常态机制，本单被留 L2 是靠独立复算 Δ=0 + 工件真在，而非报告措辞。
