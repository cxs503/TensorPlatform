# 战役波次吞吐报表（campaign throughput · 自回归燃料③）

- 生成时间（UTC）：`2026-11-07T00:45:06+00:00`　·　schema：`campaign_throughput.v1`
- 数据源：`postgres:tensorplatform`　·　口径定义见 §7 与 JSON 的 `definitions`
- plan 范围：11, 12, 13；库内可见 plan：2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18
- 终态词表来源：`/root/tp-agent/work/m4961/backend/contracts/mission_status_vocab.v1.json`（version 2）→ terminal = `succeeded, failed, stopped, revoked, orphan`

## 1. 主表（按 plan 聚合）

| plan | mission | 终态 | 在飞 | 成功率 | 墙钟 P50 (s) | 墙钟 P95 (s) | 领取 P50 (s) | 领取 P95 (s) | 吞吐 (/h) |
|---|---|---|---|---|---|---|---|---|---|
| p11 | 11 | 9 | 2 | 100.0% | 1125.4 | 4650.6 | 4.5 | 5.6 | 6.49 |
| p12 | 5 | 3 | 2 | 100.0% | 1656.7 | 3235.4 | 2.2 | 3.0 | 4.21 |
| p13 | 8 | 3 | 5 | 100.0% | 1074.9 | 1968.4 | 6.2 | 7.2 | 10.34 |

## 2. 失败回流率与领取证据覆盖

| plan | 重试单(attempt>1) | 重试占比 | failed | 已回流 failed | 回流率 | custody_requeue 事件 | 领取证据覆盖 | 墙钟回落单数 |
|---|---|---|---|---|---|---|---|---|
| p11 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p12 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p13 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |

> 分母为 0 时回流率写 `—`（NULL），**不写 0%**：没有失败样本并不等于「回流率 = 0」，样本量见 failed 列。

## 3. 状态分布

- **p11**（n=11，终态 9）：`awaiting_pr_merge`:1, `running`:1, `succeeded`:9
- **p12**（n=5，终态 3）：`awaiting_pr_merge`:1, `running`:1, `succeeded`:3
- **p13**（n=8，终态 3）：`running`:5, `succeeded`:3

## 4. 增长曲线数据源（自回归规模证据）

| plan | 首个终态 (UTC) | 末个终态 (UTC) | 跨度 (s) | 终态数 | 吞吐 (/h) | 日桶 |
|---|---|---|---|---|---|---|
| p11 | 2026-11-06T23:18:36.990389+00:00 | 2026-11-07T00:41:46.996870+00:00 | 4990.0 | 9 | 6.49 | 2026-11-06:8, 2026-11-07:1 |
| p12 | 2026-11-06T23:28:54.472785+00:00 | 2026-11-07T00:11:39.963205+00:00 | 2565.5 | 3 | 4.21 | 2026-11-06:2, 2026-11-07:1 |
| p13 | 2026-11-07T00:25:25.252678+00:00 | 2026-11-07T00:42:49.346065+00:00 | 1044.1 | 3 | 10.34 | 2026-11-07:3 |

累计增长序列（逐终态单）写在 JSON 的 `plan_reports[].growth.points`，绘图脚本/看板可直接消费，无需二次解析任务书文本。

## 5. Python ↔ SQL 对拍

- 比对项 45 对：实比 42，双空 3，失败 0
- 结论：**一致（PASS）**（容差 rel ≤ 1e-09，时间戳比到 1e-6 s）

| plan | 指标 | Python | SQL | rel_err | 判定 |
|---|---|---|---|---|---|
| 11 | `n_missions` | 11 | 11 | 0.000000000000 | pass |
| 11 | `n_terminal` | 9 | 9 | 0.000000000000 | pass |
| 11 | `n_succeeded` | 9 | 9 | 0.000000000000 | pass |
| 11 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 11 | `success_rate` | 1.000000 | 1.000000 | 0.000000000000 | pass |
| 11 | `retry_share` | 0.000000 | 0.000000 | 0.000000000000 | pass |
| 11 | `claim_latency_s.p50` | 4.473248 | 4.473248 | 0.000000000000 | pass |
| 11 | `claim_latency_s.p95` | 5.643023 | 5.643023 | 0.000000000000 | pass |
| 11 | `wall_clock_s.p50` | 1125.422713 | 1125.422713 | 0.000000000000 | pass |
| 11 | `wall_clock_s.p95` | 4650.628176 | 4650.628176 | 0.000000000000 | pass |
| 11 | `failed_return_rate` | — | — | — | both_null |
| 11 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 11 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 11 | `throughput.first_terminal_utc` | 2026-11-06T23:18:36.990389+00:00 | 2026-11-06T23:18:36.990389+00:00 | 0.000000000000 | pass |
| 11 | `throughput.last_terminal_utc` | 2026-11-07T00:41:46.996870+00:00 | 2026-11-07T00:41:46.99687+00:00 | 0.000000000000 | pass |
| 12 | `n_missions` | 5 | 5 | 0.000000000000 | pass |
| 12 | `n_terminal` | 3 | 3 | 0.000000000000 | pass |
| 12 | `n_succeeded` | 3 | 3 | 0.000000000000 | pass |
| 12 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 12 | `success_rate` | 1.000000 | 1.000000 | 0.000000000000 | pass |
| 12 | `retry_share` | 0.000000 | 0.000000 | 0.000000000000 | pass |
| 12 | `claim_latency_s.p50` | 2.233692 | 2.233692 | 0.000000000000 | pass |
| 12 | `claim_latency_s.p95` | 3.032473 | 3.032473 | 0.000000000000 | pass |
| 12 | `wall_clock_s.p50` | 1656.653792 | 1656.653792 | 0.000000000000 | pass |
| 12 | `wall_clock_s.p95` | 3235.357107 | 3235.357107 | 0.000000000000 | pass |
| 12 | `failed_return_rate` | — | — | — | both_null |
| 12 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 12 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 12 | `throughput.first_terminal_utc` | 2026-11-06T23:28:54.472785+00:00 | 2026-11-06T23:28:54.472785+00:00 | 0.000000000000 | pass |
| 12 | `throughput.last_terminal_utc` | 2026-11-07T00:11:39.963205+00:00 | 2026-11-07T00:11:39.963205+00:00 | 0.000000000000 | pass |
| 13 | `n_missions` | 8 | 8 | 0.000000000000 | pass |
| 13 | `n_terminal` | 3 | 3 | 0.000000000000 | pass |
| 13 | `n_succeeded` | 3 | 3 | 0.000000000000 | pass |
| 13 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 13 | `success_rate` | 1.000000 | 1.000000 | 0.000000000000 | pass |
| 13 | `retry_share` | 0.000000 | 0.000000 | 0.000000000000 | pass |
| 13 | `claim_latency_s.p50` | 6.177504 | 6.177504 | 0.000000000000 | pass |
| 13 | `claim_latency_s.p95` | 7.209169 | 7.209169 | 0.000000000000 | pass |
| 13 | `wall_clock_s.p50` | 1074.850870 | 1074.850870 | 0.000000000000 | pass |
| 13 | `wall_clock_s.p95` | 1968.428801 | 1968.428801 | 0.000000000000 | pass |
| 13 | `failed_return_rate` | — | — | — | both_null |
| 13 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 13 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 13 | `throughput.first_terminal_utc` | 2026-11-07T00:25:25.252678+00:00 | 2026-11-07T00:25:25.252678+00:00 | 0.000000000000 | pass |
| 13 | `throughput.last_terminal_utc` | 2026-11-07T00:42:49.346065+00:00 | 2026-11-07T00:42:49.346065+00:00 | 0.000000000000 | pass |

<details><summary>对拍所用独立 SQL（原文，可直接 psql 复算）</summary>

```sql
WITH base AS (
  SELECT m.id, m.status, m.task_key,
         substring(m.task_key from '^p([0-9]+)-')::int AS plan_id,
         substring(m.task_key from '^p[0-9]+-(.+)-a[0-9]+$') AS node,
         substring(m.task_key from '-a([0-9]+)$')::int AS attempt,
         m.created_at, m.finished_at,
         (SELECT min(e.created_at) FROM tensor_agent_session_events e
           WHERE e.session_id = m.session_id AND e.kind = 'note'
             AND e.payload->>'subtype' = 'mission_claimed') AS claimed_at
    FROM tensor_dev_missions m
   WHERE m.task_key ~ '^p[0-9]+-.+-a[0-9]+$'
), scoped AS (
  SELECT * FROM base WHERE plan_id IN (11,12,13)
), per_plan AS (
  SELECT plan_id,
         count(*) AS n_missions,
         count(*) FILTER (WHERE status IN ('succeeded','failed','stopped','revoked','orphan'))
           AS n_terminal,
         count(*) FILTER (WHERE status = 'succeeded') AS n_succeeded,
         count(*) FILTER (WHERE status = 'failed') AS n_failed,
         count(*) FILTER (WHERE attempt > 1) AS n_attempt_gt1,
         count(*) FILTER (WHERE claimed_at IS NOT NULL) AS n_claim_evidence,
         percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM (claimed_at - created_at))) AS claim_p50,
         percentile_cont(0.95) WITHIN GROUP (
           ORDER BY extract(epoch FROM (claimed_at - created_at))) AS claim_p95,
         percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM (finished_at - claimed_at)))
           FILTER (WHERE status IN ('succeeded','failed','stopped','revoked','orphan')
                       AND claimed_at IS NOT NULL) AS wall_p50,
         percentile_cont(0.95) WITHIN GROUP (
           ORDER BY extract(epoch FROM (finished_at - claimed_at)))
           FILTER (WHERE status IN ('succeeded','failed','stopped','revoked','orphan')
                       AND claimed_at IS NOT NULL) AS wall_p95,
         min(finished_at) FILTER (
           WHERE status IN ('succeeded','failed','stopped','revoked','orphan')) AS first_terminal,
         max(finished_at) FILTER (
           WHERE status IN ('succeeded','failed','stopped','revoked','orphan')) AS last_terminal
    FROM scoped GROUP BY plan_id
), returned AS (
  SELECT b.plan_id, count(*) AS n_failed_returned
    FROM base b
   WHERE b.status = 'failed' AND b.plan_id IN (11,12,13)
     AND EXISTS (SELECT 1 FROM base b2
                  WHERE b2.plan_id = b.plan_id AND b2.node = b.node
                    AND b2.attempt > b.attempt)
   GROUP BY b.plan_id
), requeue AS (
  SELECT substring(m.task_key from '^p([0-9]+)-')::int AS plan_id, count(*) AS n
    FROM mission_lifecycle_log l JOIN tensor_dev_missions m ON m.id = l.mission_id
   WHERE l.action = 'custody_requeue' AND m.task_key ~ '^p[0-9]+-.+-a[0-9]+$'
   GROUP BY 1
)
SELECT json_build_object(
  'plan_id', p.plan_id, 'n_missions', p.n_missions, 'n_terminal', p.n_terminal,
  'n_succeeded', p.n_succeeded, 'n_failed', p.n_failed,
  'n_attempt_gt1', p.n_attempt_gt1, 'n_claim_evidence', p.n_claim_evidence,
  'claim_p50', p.claim_p50, 'claim_p95', p.claim_p95,
  'wall_p50', p.wall_p50, 'wall_p95', p.wall_p95,
  'first_terminal', p.first_terminal, 'last_terminal', p.last_terminal,
  'n_failed_returned', coalesce(r.n_failed_returned, 0),
  'custody_requeue_events', coalesce(q.n, 0))::text
FROM per_plan p
LEFT JOIN returned r USING (plan_id)
LEFT JOIN requeue q USING (plan_id)
ORDER BY p.plan_id
```

</details>

## 6. 诊断与局限

- 扫描 mission 4617 条，其中可归入 plan 的 135 条；无 plan 归属 4482 条（task_key 为空或非 `p<id>-<node>-a<n>` 形态）——本报表**不替它们猜归属**。
- 领取证据覆盖 100.0%（证据源：`tensor_agent_session_events` 的 `note/mission_claimed`，135 个 session）。缺失时墙钟回落到 `created_at → finished_at` 并于 `wall_fallback_n` 计数。
- 百分位为线性插值（与 `percentile_cont` 同构）；n<20 时 P95 属**指示性**，不要当 SLA 承诺。
- 领取时延以 session 的首个 `mission_claimed` 事件归因：有领取证据的 mission 135 条 / session 135 个 → 严格 1:1，归因无歧义。
- 回流率只在 `p<plan>-<node>-a<n>` 结构上可定义；自启单（`selfstart-*`、空 task_key）没有稳定 node 身份，回流率对其为 NULL。

## 7. 口径定义（代码即口径）

- **plan_linkage**：task_key = ^p<plan_id>-<node_id>-a<attempt>$（战役派发器写入口径），plan_id 即 campaign_plans.id；无该前缀的 mission 不计入任何 plan。
- **terminal**：status ∈ 契约 mission_status_vocab.v1.json 的 terminal 集合
- **success_rate**：#succeeded / #terminal（分母只含终态，在飞单不进分母）
- **claim_latency_s**：首个 claimed 证据 − created_at（排队等待 = worker 领取时延）
- **wall_clock_s**：finished_at − 首个 claimed 证据（执行墙钟）；无领取证据时回落 finished_at − created_at 并以 wall_fallback_n 显式计数
- **cycle_time_s**：finished_at − created_at（含排队的总在制时间）
- **retry_share**：attempt ≥ 2 的 mission 数 / 该 plan 全部 mission 数
- **failed_return_rate**：同 plan+node 存在更晚 attempt 的 failed 单数 / 该 plan failed 单数（分母为 0 时为 null）
- **custody_requeue**：mission_lifecycle_log.action='custody_requeue' 事件数（平台级回流通道）
- **throughput_per_hour**：#terminal / (末个终态 finished_at − 首个终态 finished_at)（小时）
- **percentile**：线性插值（h=(n−1)q），与 PostgreSQL percentile_cont 同构，故对拍容差 1e-9

---
复算方式：`python3 tools/campaign_throughput.py --plans 11,12,13 --crosscheck`（只读；`--dry-run` 只打印不落盘）。
