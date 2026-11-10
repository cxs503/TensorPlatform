# 预注册：任务周期基线口径冻结（cycle-baseline-2026-11-10）

- 口径指纹 `SPEC_SHA256`: `8d72f88bb8e708252155ea285b0775c12fdd08f238285e9e723fc477e208ef37`
- schema: `cycle_baseline.v1`
- 冻结时点: 2026-11-10（本文件一经冻结，后续测量只许按本口径；改口径必须换 prereg id）
- 基线工件: `artifacts/cycle_baseline/baseline.json`（其中 `spec_sha256` 必须与本文件一致）

## 1. 为什么现在冻结

KPI「周期 −50%」长期无法判定，因为平台上不存在任何被冻结的周期口径：`finished_at - created_at` / `finished_at - claimed_at` / `created→report` 三种说法在不同报表里混用，谁都能挑出对自己有利的那个。本文件把口径写死成代码常量（`tools/cycle_baseline/spec.py`）并对口径字典求 sha256；报告与工件必须嵌入同一指纹，指纹不符即视为口径漂移。

## 2. 冻结的口径（逐条，机器常量）

| 项 | 冻结内容 | 备注 |
|---|---|---|
| `cohort` | cohort = 时间窗内终态 mission（status ∈ 契约 terminal 集合），按 created_at 归窗；分母固定为 cohort 全量，不因缺证据缩小（缩分母=自欺） | 分母恒为窗口内全部终态 mission |
| `窗口锚点` | asof 固定 ISO 时刻，窗口 = [asof − N 天, asof)；N 默认 8 天 | 不得用 now()：否则两次查询种群漂移，行级导出与 SQL 聚合无法对拍（实测 n 差 1、p50 差 1e-4） |
| `cycle（主口径）` | 周期 = created_at → finished_at（预注册主口径）。原战役假设『created→report』与实测相位相反（>95% 报告早于 finished_at），故本口径按实测修正并在此冻结 | 原战役假定的 created→report 与实测相位相反（>95% 报告早于 finished_at），按实测修正 |
| `t_queue` | 首个 mission_claimed 事件 − mission.created_at（排队等待；无领取证据 → 缺失） | 缺失记 None，不记 0 |
| `t_exec` | mission.finished_at − 首个 mission_claimed 事件（领取到终态的墙钟；无领取证据 → 缺失） | 缺失记 None，不记 0 |
| `t_merge` | 首次落地 commit 时间 − mission.finished_at（git 侧按 mission id 反查；无匹配 commit → 缺失）。原值可为负：先落地后收尾的 push-merge 路径 | 原值可为负；clamp 值单列，负值比例单列 |
| `t_report` | 报告行 created_at − mission.finished_at（tensor_reports.mission_id 关联；无报告行 → 缺失）。原值可为负：先发报告再 finish 的执行路径 | 原值可为负；clamp 值单列，负值比例单列 |
| `segment_share` | 份额 = 段时长 / Σ 已测段时长（仅在四段齐备的 complete-case 子集上可加；缺证据时分子分母同时收缩并在 coverage 字段显式给出） | 只在四段齐备的 complete-case 上可加 |
| `coverage` | 段覆盖率 = 该段有证据的 mission 数 / cohort mission 数 | 门禁分母与主口径分母相同 |
| `jain` | Jain 公平指数 J(x) = (Σx)² / (n Σx²)，x 为 worker 维度负载（计数或忙时秒）；J ∈ [1/n, 1]，1 = 完全均分 | 计数与忙时两种口径都给 |
| `gini` | Gini = Σ_i Σ_j |x_i − x_j| / (2 n² μ)；μ=0 时取 0 并标记 degenerate | mu=0 记 0 并标 degenerate |
| `starvation` | 饥饿事件 = 排队时长 > 同 cohort 排队 p95 且终态非 succeeded（等待超阈却未正常完成） | 阈值取同 cohort 排队 p95 |
| `priority_inversion` | 优先级反转 = 同战役内 mission A 的 planned_start 早于 B，但 A 的领取时间晚于 B（调度序与计划序倒置），阈值 gap 秒 | gap = 60 s |

## 3. 冻结的门限与判据（事后不得下调）

| 判据 | 门限 | 常量 |
|---|---|---|
| (a) Jain 恢复误差 | <= 0.02 | `JAIN_RECOVERY_TOL` |
| (b) 检测器 F1（合成夹具） | >= 0.9 | `DETECTOR_F1_MIN` |
| (c) 每行 SQL + sha256 可复算 | 全部行 | `provenance.verify_table` |
| (d) 段覆盖率门禁 | < 80% → WITHHELD | `COVERAGE_MIN` |
| (e) 三策略 vs 解析预测 | <= 5% | `POLICY_THEORY_TOL` |

## 4. 允许与禁止的后续声明

- **允许**：引用 `cycle_s` 的分位作为 KPI 基线（覆盖率 99.9%，达标）；引用条件子总体结论时必须同时给出可证据总体 n 与总体内覆盖率。
- **允许**：策略结论（权重须与产能匹配）在合成负载内部成立，可用于驱动「是否上加权调度」的决策。
- **禁止**：把 WITHHELD 段的描述性子样本数字当作总体口径；把合成夹具 F1 当作真实检出能力；用缩小分母 / clamp 负值 / 四舍五入覆盖率等方式让门禁通过。
- **降级规则**：任一段覆盖率 < 80% → 该段结论必须标 WITHHELD；条件子总体达标时给 `MEASURED_CONDITIONAL`，但不得外推。

## 5. 本次基线（冻结时点快照，程序化注入）

| 项 | 值 |
|---|---|
| cohort n | 7,895 |
| asof | `2026-11-10T18:30:00+00:00` |
| `t_queue` | coverage 74.9% · p50 3795.2 s · WITHHELD |
| `t_exec` | coverage 74.8% · p50 1328.1 s · WITHHELD |
| `t_merge` | coverage 41.3% · p50 1129.9 s · WITHHELD |
| `t_report` | coverage 65.3% · p50 0.0 s · WITHHELD |
| `cycle_s`（KPI 基线） | p50 3,456.0 s · p90 30,301.1 s · p99 153,240.0 s · coverage 99.9% |

## 6. 复现命令

```bash
python -m tools.cycle_baseline --source pg --repos-root /root/tensor-ecosystem \
    --asof 2026-11-10T18:30:00+00:00 --window-days 8 \
    --out-json artifacts/cycle_baseline/baseline.json \
    --out-md docs/IN55W3-N08_cycle_baseline.md --figure-dir artifacts/cycle_baseline
```

同一 asof + 同一数据库快照 ⇒ 同一 cohort。数据本身演进（asof 后落终态、补报告）会让 `value_sha256` 变化；这是数据变化而非口径漂移，报告同时登记 asof 与 sha256 以区分两者。
