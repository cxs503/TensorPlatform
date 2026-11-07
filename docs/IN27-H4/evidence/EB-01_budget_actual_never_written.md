# EB-01 转化证据：`campaign_plans.budget_actual` 从不回写，且"实耗"实为计划上限

- 质询 occ_id：`EXP_BUDGET:EB-01@2026-11-07#2`（mission 5310，周期 2026-11-07#1）
- 转化类型：repair（修补单）
- 采集时刻：2026-11-07T09:5x UTC，信号快照 `runs/IN27-H4/signals_2026-11-07.json`

## 事实现象（探针原文，非手抄）

`GET /api/v1/tensor/campaigns/plans` 返回 34 个主计划，其中 **26 个 `budget_actual` 全部科目为 0**（76%）。
样例（plan 42「继承者军」）：

```json
{"budget_planned": {"max_tokens": 0, "max_tool_calls": 0, "max_wall_seconds": 0},
 "budget_actual":  {"max_tokens": 0, "max_tool_calls": 0, "max_wall_seconds": 0}}
```

复现命令（只读）：

```bash
TOKEN=...; curl -s "$API/tensor/campaigns/plans" -H "Authorization: Bearer $TOKEN" \
  | jq '[.items[] | select((.budget_actual // {}) | length > 0)
        | select([.budget_actual[]] | add == 0)] | length'
```

## 根因（代码级，两处）

1. **预算实耗只写入快照表，从不回写 plan 行**：`backend/api/campaign_plans.py:418-429`
   在 *snapshot* 端点内聚合出 `budget_actual`，随后在 `:489-506` 只 `INSERT INTO campaign_snapshots`。
   全仓不存在 `UPDATE campaign_plans SET budget_actual`（`grep -rn "SET budget_actual" backend` 仅命中 418 行的局部变量赋值）。
   ⇒ 读 plans 端点（`:165`）永远拿到建表默认值 0。

2. **"实耗"语义错误**：聚合源是 `tensor_dev_missions.budget` 的 `max_tool_calls/max_wall_seconds/max_tokens`，
   即**计划上限**，而非实际消耗。即使回写成功，`budget_actual` 也会等于"所有已关联单的预算上限之和"，
   把「预算」和「花销」混成一个数——探索预算审计从此失去意义。

## 影响

- 「探索预算是否真被使用」这一 owner 级问题，当前平台**结构上不可回答**：账面恒零。
- IN27-G24 继承验收门若依赖预算实耗，将拿到恒零分母。

## 修补单要求（机器可判）

1. 在 snapshot 落库后追加 `UPDATE campaign_plans SET budget_actual = :ba WHERE id = :pid`，
   并补一条 pytest：连续两次 snapshot 后 `GET /plans/{id}` 的 `budget_actual` 非零。
2. 引入真实消耗源（session/execution 的 tokens/tool_calls 实际用量）作为 `budget_actual`，
   把现聚合重命名为 `budget_caps_committed`，两字段并存，禁止同名字段承载两种语义。
3. 负例：无关联单的 plan，`budget_actual` 必须为 `{}` 或 null（表示"无数据"），不得为 0（表示"花了 0"）。