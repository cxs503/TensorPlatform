# m1397 — reference 审计 + v1.1 迁移（归档）

**分支**：`agent/m1397`（提交 2eca0bc）
**交付**：`tools/verifier/audit_benchmark_reference.py`（527 行）、
`tools/verifier/migrate_benchmark_reference_v11.py`、数据集 v1.1、前后审计工件
（`artifacts/verifier/benchmark_audit_{prefix,postfix}.json`）、`docs/verifier/BENCHMARK_REFERENCE_AUDIT.md`。

**数值主张**：Rt 按 **ρ=1000 kg/m³** 派生（SUBOFF-01 = **92.68 N**）；查出 Rt=Ct·½ρV²S 恒等式
12/12 违例（86.8–89.8%）与 DTMB-5415 Froude 列 82% 误差。

**裁决**：**两个诊断采纳，数值口径部分采纳。**
- 采纳：恒等式违例诊断（与 m1561 独立复算一致：v1 的 12.5 N 反解湿面积仅 0.73 m²）。
- 采纳：DTMB-5415 Froude 列问题（正典进一步反解出该列隐含船长 181.3 m ≠ 5.72 m，
  从而判定 Fr 与航速两列互不自洽，整列 WITHHELD）。
- 采纳：`reference_density_kg_m3` / `reference_kinematic_viscosity_m2_s` **行内物性字段**（正典保留）。
- 部分不采纳：ρ=1000 是无出处的整数近似；正典取 ITTC 物性表淡水 20 °C = 998.2 kg/m³
  （与 1000 差 0.18%，远小于判决阈值，但必须有出处）。SUBOFF-01 因此裁决为 **92.52 N** 而非 92.68 N。
