# GC-02 回填：分级数据不可得（mission.result 无 grade 字段）

- 质询 occ_id：`GRADE_CEIL:GC-02@2026-11-07#3`（mission 5311）
- 结论：**证实为"分级链断线"（分支 d），并已归并到 GC-03 修补单 5314**，本单不再单独立单。

## 证据

| 检查 | 命令/端点 | 结果 |
|---|---|---|
| mission.result 是否有 grade | `GET /tensor/missions?limit=200` → 遍历 `result` | 200 条里 `evidence_grade`/`grade` 计数 = **0**（快照 `missions_grade_counts.value = {}`，status=partial） |
| 平台是否有能力 grade 端点 | `grep -n "@router.get" backend/api/tensor_capability.py` | 仅 `/deltas`、`/summary`；**无** grade 清单端点 |
| 分级字段是否存在于别处 | `grep -rn "evidence_grade" backend --include=*.py` | 仅出现于船舶域响应（`api/domain/ship_green.py`、`services/green_navigation.py`），**不进 mission.result** |

## 判定

分支（d）分级链断线：船舶域答案里有 `evidence_grade`，但落库路径（mission.result）不承载该字段，
故任何"平台 grade 天花板"的统计都只能得到空集——**上一版"grade 天花板 = S2"的说法在本仓无可复现证据**，
真正的 S2 天花板证据来自能力账本语义（见 GC-03：实质增量 0/1366）。

## 处置

1. 不重复立单：修补动作并入 mission 5314（complete 声明 + summary 新增分母）。
2. 建议（交 5314 执行者参考）：`capability/summary` 增 `grade_distribution`，从 `tensor_capability_deltas.category`
   与 declaration 派生，避免再用 mission.result 做 grade 统计。