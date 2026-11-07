# GC-03 转化证据：1366 条能力增量中实质增量 = 0（grade 天花板的接线根因）

- 质询 occ_id：`GRADE_CEIL:GC-03@2026-11-07#1`（mission 5309，周期 2026-11-07#1）
- 转化类型：repair（修补单）
- 采集时刻：2026-11-07 UTC，`GET /api/v1/tensor/capability/summary` 原始响应存于
  `runs/IN27-H4/signals_2026-11-07.json` 的 `capability_coverage.value`

## 事实现象（探针原文）

```json
{"by_category": {"new_capability": 0, "capability_extension": 0, "defect_fix": 0,
                 "reusable_knowledge": 0, "falsified_hypothesis": 0,
                 "no_substantive_delta": 1366},
 "total_deltas": 1366, "substantive_deltas": 0, "no_substantive_deltas": 1366,
 "succeeded_missions": 4049, "covered_succeeded": 1359, "coverage_ratio": 0.3356}
```

即：**已入账 1366 条增量，实质增量 0 条**；4049 件 succeeded 单里只有 1359 件被入账（33.6%），
而入账的那部分**全部**落在 `no_substantive_delta`。

## 根因（代码级）

`backend/api/tensor_approvals.py:268-275`（验收审批路径）：

```python
# SPEC §7 C-loop: a succeeded mission books exactly one capability
# delta row — agent-declared if provided, else an honest
# no_substantive_delta (never a fabricated one).
declared = _declared_capability_delta(row["mission_result"])
delta = declared or {"category": "no_substantive_delta", ...}
```

兜底逻辑本身是**诚实的**（宁可记"无实质增量"也不伪造），问题在上游：
`mission.result.capability_delta` 在 `POST /tensor/missions/{id}/complete` 时**几乎无人声明**，
于是 100% 落入兜底。⇒ 这不是"产出偏低"的证据，而是**complete 协议缺少声明引导/校验**的证据；
把它当成"质量差"会误判产线。

同时 4049 − 1359 = 2690 件 succeeded 单连 delta 行都没有（审批未走 approved 路径或单被重放）。

## 影响

- 「grade 天花板为何 S2」有了结构性答案：能力账本不承载任何实质增量 ⇒ 证据分级无法从 S2 上移。
- 任何"实质产出率"指标若用 `substantive_deltas / total_deltas`，当前恒为 0/1366，会被误读为质量塌陷。

## 修补单要求（机器可判）

1. `complete` 时对 `capability_delta` 做**必填性分级**：涉及代码/算法改动的单声明缺失 → 422 或落 `awaiting_acceptance` 待补，
   不允许静默走兜底；纯文档/审计单保留兜底并记 `reason`。
2. 补 pytest：complete 不带 `capability_delta` 的代码类单 → 拒绝；带声明 → delta.category 等于声明值。
3. 给 `capability/summary` 增 `declared_ratio = (total_deltas - no_substantive_deltas) / total_deltas` 与
   `unbooked_succeeded = succeeded_missions - covered_succeeded` 两项，避免分母误导。