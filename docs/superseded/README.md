# 归档说明：m1561 六分支服务架构裁决

本目录收录 2026-11 期间六张"基准参考数据"分支持续产出、但**未被选为正典**的实现说明。
裁决输入是六张分支各自独立重推导的同一份基准数据集（12 个算例）与四套候选服务：
`benchmark_dataset_audit`、`ship_resistance_core`、`benchmark_integrity`（两个版本）、
`resistance_core + hull_geometry`、`geometry_consistency`。

## 裁决结论

| 角色 | 归属 | 状态 |
|---|---|---|
| 数值权威 | `backend/data/benchmark_reference.json` **v3.0** | 正典（唯一） |
| 复算引擎 | `backend/services/benchmark_canon.py` | 正典（本次新增，纯解析） |
| 证据闸门 | `backend/services/benchmark_integrity.py` | 正典（C1–C5 严重度梯 + 独立测试） |
| 三值兼容层 | `backend/services/benchmark_dataset_audit.py` | **降级为 facade**，物理全部委托闸门 |
| `ship_resistance_core`（m1395） | — | 归档：与 `services/ship_resistance.py` 重复 |
| `resistance_core + hull_geometry`（m1400） | — | 归档：同上，且数值未修正 Rt |
| `benchmark_integrity@m1400` 变体 | — | 归档：被基线模块 + m1561 扩展取代 |
| `geometry_consistency`（m1404） | — | 归档：闭合规则已吸入正典引擎 R4/R6 |

## 裁决依据（可机器复核）

1. **实现不重复**：正典引擎只用一个物理实现；`benchmark_dataset_audit` 不再自带物理。
2. **测试覆盖**：正典闸门带独立测试；三个被判降级的模块在基线里无测试或测试未随代码合并。
3. **可复算**：正典每个量都由恒等式复算（`python3 backend/services/benchmark_canon.py --check`），
   并从冻结输入 `backend/data/benchmark_reference_sources_v2.json` 确定性重建。
4. **不凑数**：无法复核的行标 `WITHHELD` 并退出 15% 判决（12 行中 8 行）。

逐条说明见本目录下各分支文件；完整裁决报告见
`docs/reports/m1561_benchmark_canon_adjudication.md`。
