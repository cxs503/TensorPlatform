# m1402 — benchmark_dataset_audit 服务（降级为 facade）

**分支**：`agent/m1402`（提交 1b7c409）
**交付**：`backend/tools/benchmark_dataset_audit.py`（398 行，整合波里落到
`backend/services/benchmark_dataset_audit.py`）、`migrate_benchmark_dataset_v2.py`、
`outputs/benchmark_audit/` 前后审计工件与四张图、`docs/ship-benchmark-reference-audit.md`。
其 v2.0 数据集已随整合波进入基线。

**裁决**：**架构降级为 facade；数值被取代。**
- 降级理由：I1–I3 三检查是正典闸门 C1–C5 的真子集；密度被写死为单一常量
  （`ship_resistance.DEFAULT_WATER_DENSITY`），**无法表达"模型试验=淡水 / 实船=海水"**，
  正是 SUBOFF 用海水密度报出 95.0 N 的根因。基线下该模块无测试随行（`test_benchmark_dataset_audit.py`
  未合并），而 API 层 `ship_benchmark` 仍依赖其三值口径。
- 处理：保留导出名 `CONSISTENT/PARTIAL/INCONSISTENT`、`assess_case/assess_dataset/load_and_assess/
  case_status_map`，**物理全部委托 `services.benchmark_integrity`**，并优先读行内物性字段。
  这样 API 契约不变、只有一个物理实现。
- 数值：v2.0 的 SUBOFF 95.0 N（ρ=1025）被正典 92.52 N（ρ=998.2）取代，v2 值进
  `legacy_values.superseded_experimental_rt_n`。
- 效果（可复现）：对记录值（v2）判定 0/12 行 CONSISTENT → 无法声明任何精度；
  对正典 v3.0 判定 4/12 CONSISTENT（SUBOFF 四行）+ 8/12 PARTIAL（DTMB-5415、KVLCC2 缺湿面积）。
