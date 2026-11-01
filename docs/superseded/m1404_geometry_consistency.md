# m1404 — geometry_consistency 审计（归档）

**分支**：`agent/m1404`（提交 c877252）
**交付**：`backend/api/domain/geometry_consistency.py`（438 行）、数据集 v1.1 几何修正、
`artifacts/m1404/geometry_audit.{json,csv}`、`tests/test_geometry_consistency.py`（302 行）、
`tools/ship_perf/audit_benchmark_geometry.py`、`docs/reports/m1404_geometry_validation_debt.md`。

**数值主张**：Cb/L/B/T/Δ 几何一致性门，修正 displacement 列。

**裁决**：**规则并入正典，服务不单列。**
- 采纳：Δ = Cb·L·B·T·ρ 闭合（正典 R4）。正典复算：DTMB-5415 记录 466.3 kg 与几何差 16.1%
  （ρ=998.2 派生 541.6 kg）；KVLCC2 记录 307000 t 差 4.4%（派生 320437 t）。记录值进 `legacy_*`。
- 采纳：几何先验不得放行"尺度/介质张冠李戴"（正典 R6 + R2）。
- 不采纳：再开一个 API 域模块做几何校验。几何校验属于参考数据闸门，应留在
  `benchmark_integrity`（C2/C5）与正典引擎（R4/R6）里，避免第三个裁判。
