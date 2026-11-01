# m1395 — ship_resistance_core（归档）

**分支**：`agent/m1395`（提交 125a8aa）
**交付**：`backend/services/ship_resistance_core.py`（132 行）、`backend/api/domain/ship_benchmark.py`
改造、`backend/data/benchmark_reference.json` 富化（`rt_provenance` / `hull_class` /
`froude_number_computed` / `excluded`）、`tests/test_resistance_benchmark_gate.py`（269 行）、
`tools/audit_benchmark_reference.py`（425 行）、`tools/verify_resistance_accuracy.py`（238 行）。

**数值主张**：SUBOFF 四行按 ρ=998.2 kg/m³（淡水）派生 Rt，SUBOFF-01 = **92.52 N**。

**裁决**：**数值采纳，架构不采纳。**
- 采纳：淡水介质假设 + `Rt = Ct·½ρV²S` 复算口径。m1561 独立复算得 92.52 N（相对差 <1e-4），
  与 m1395 完全一致；这是两套独立推导相互印证的强证据。
- 不采纳：`ship_resistance_core.py` 与既有 `backend/services/ship_resistance.py` 物理重复，
  两套阻力核会让 `calc.resistance` 与基准判定用不同实现（正是本次要消灭的契约漂移）。
- 采纳其字段思路：`rt_provenance` → 正典的 `rt_basis` / `rt_status`。

**现状**：数值进正典 v3.0；服务代码留在 `agent/m1395` 分支，不再进主线。
