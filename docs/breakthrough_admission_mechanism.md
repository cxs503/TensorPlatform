# breakthrough_problems 准入门（IN59-A1）机制说明

## 背景

S4/S5 突破/创造类任务（难度≥3 或 research 类）此前只有「派发梯度」与「探索预算」
两个记账面（见 `mission_gradient` / `guidance_loop`），缺少「攻关问题本体」的库内
实体：问题定义 / 基线 / 验收标准 / 七环阶段 / 战役关联都没有可查询、可机检的落库。
本节点补齐 `breakthrough_problems` 表 + 准入门 API。

## 准入门三要素（缺任一 → 422 并列缺件清单）

| 要素 | 机检判据 |
|---|---|
| problem_definition 问题定义 | 问题树 ≥3 子问题（JSON 数组，非空字符串） |
| baseline 基线 | 现状最好方案（≥8 字符）+ 差距（≥8 字符） |
| acceptance_criteria 验收标准 | ≥1 条可机检判据（每条含 metric/target/tolerance） |

**门必须可失败**：三要素不全的攻关任务必须被拒（红队负例）。

## 七环状态机

```
problem_definition → investigation → hypothesis → experiment
→ challenge → acceptance → distillation
```

只允许**相邻环**推进（前进/回退一步），不可跳环（红队负例：跳环必须被拒）。

## 豁免声明位（exemption_declared）

- 普通工程单（难度<3 且 engineering）可显式声明豁免 → `exempted`。
- **科研任务（research）不得借工程单豁免绕门**（`exemption_guard` 强制）——
  声明豁免的 research 任务必须被拒（`exemption_abuse`）。

## 接入既有 mission 创建流

`POST /tensor/missions`（`tensor_missions.py`）新增可选字段：
`difficulty` / `task_class` / `problem_tree` / `baseline_summary` /
`baseline_gap` / `acceptance_criteria` / `exemption_declared`。

当 `difficulty` 或 `task_class` 被声明时，创建前强制走准入门：
- 三要素不全 → 422 并列缺件清单（门可失败）；
- 科研任务声明豁免 → 422（绕门被拒）。

## 落库

- 迁移：`backend/migrations/106_breakthrough_problems.sql`（可移植 SQL 子集，
  Postgres 与 SQLite 均接受）。
- 服务层：`backend/services/breakthrough_admission.py`（纯函数，零领域语义）。
- API 层：`backend/api/breakthrough_problems.py`（准入门 + 落库 + 查询 + 七环推进 +
  显式准入/豁免）。
- 测试：`backend/tests/test_breakthrough_admission.py`（36 用例：三要素校验 /
  准入门决策 / 豁免守卫 / 七环状态机 / 缺件清单 / 迁移即建库 / 端到端红队可失败）。
