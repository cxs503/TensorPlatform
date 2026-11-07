# 战役波次吞吐报表（campaign throughput · 自回归燃料③）

- 生成时间（UTC）：`2026-11-07T00:45:08+00:00`　·　schema：`campaign_throughput.v1`
- 数据源：`postgres:tensorplatform`　·　口径定义见 §7 与 JSON 的 `definitions`
- plan 范围：2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18；库内可见 plan：2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18
- 终态词表来源：`/root/tp-agent/work/m4961/backend/contracts/mission_status_vocab.v1.json`（version 2）→ terminal = `succeeded, failed, stopped, revoked, orphan`

## 1. 主表（按 plan 聚合）

| plan | mission | 终态 | 在飞 | 成功率 | 墙钟 P50 (s) | 墙钟 P95 (s) | 领取 P50 (s) | 领取 P95 (s) | 吞吐 (/h) |
|---|---|---|---|---|---|---|---|---|---|
| p2 | 11 | 11 | 0 | 100.0% | 809.6 | 1382.1 | 10.1 | 11.3 | 21.84 |
| p3 | 13 | 13 | 0 | 84.6% | 1879.9 | 3091.4 | 7.7 | 10.1 | 12.90 |
| p4 | 9 | 9 | 0 | 88.9% | 2077.3 | 5996.6 | 2.3 | 3.3 | 3.81 |
| p5 | 8 | 8 | 0 | 100.0% | 1873.6 | 2621.7 | 2.8 | 3.8 | 15.04 |
| p6 | 11 | 11 | 0 | 81.8% | 1618.4 | 3331.0 | 2.9 | 4.7 | 12.98 |
| p7 | 10 | 10 | 0 | 100.0% | 1739.9 | 4669.9 | 4.0 | 5.7 | 7.46 |
| p8 | 9 | 9 | 0 | 88.9% | 2460.1 | 6164.5 | 4.8 | 6.2 | 3.28 |
| p9 | 11 | 11 | 0 | 90.9% | 1753.9 | 5945.2 | 21.1 | 75.3 | 4.64 |
| p11 | 11 | 9 | 2 | 100.0% | 1125.4 | 4650.6 | 4.5 | 5.6 | 6.49 |
| p12 | 5 | 3 | 2 | 100.0% | 1656.7 | 3235.4 | 2.2 | 3.0 | 4.21 |
| p13 | 8 | 3 | 5 | 100.0% | 1074.9 | 1968.4 | 6.2 | 7.2 | 10.34 |
| p14 | 3 | 0 | 3 | — | — | — | 0.2 | 0.2 | — |
| p15 | 11 | 0 | 11 | — | — | — | 2.1 | 6.6 | — |
| p16 | 8 | 0 | 8 | — | — | — | 8.3 | 9.0 | — |
| p17 | 3 | 0 | 3 | — | — | — | 0.5 | 1.2 | — |
| p18 | 4 | 0 | 4 | — | — | — | 2.0 | 3.3 | — |

## 2. 失败回流率与领取证据覆盖

| plan | 重试单(attempt>1) | 重试占比 | failed | 已回流 failed | 回流率 | custody_requeue 事件 | 领取证据覆盖 | 墙钟回落单数 |
|---|---|---|---|---|---|---|---|---|
| p2 | 1 | 9.1% | 0 | 0 | — | 0 | 100.0% | 0 |
| p3 | 2 | 15.4% | 2 | 2 | 100.0% | 0 | 100.0% | 0 |
| p4 | 1 | 11.1% | 1 | 1 | 100.0% | 0 | 100.0% | 0 |
| p5 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p6 | 1 | 9.1% | 2 | 1 | 50.0% | 0 | 100.0% | 0 |
| p7 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p8 | 1 | 11.1% | 1 | 1 | 100.0% | 0 | 100.0% | 0 |
| p9 | 1 | 9.1% | 1 | 1 | 100.0% | 0 | 100.0% | 0 |
| p11 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p12 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p13 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p14 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p15 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p16 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p17 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |
| p18 | 0 | 0.0% | 0 | 0 | — | 0 | 100.0% | 0 |

> 分母为 0 时回流率写 `—`（NULL），**不写 0%**：没有失败样本并不等于「回流率 = 0」，样本量见 failed 列。

## 3. 状态分布

- **p2**（n=11，终态 11）：`succeeded`:11
- **p3**（n=13，终态 13）：`failed`:2, `succeeded`:11
- **p4**（n=9，终态 9）：`failed`:1, `succeeded`:8
- **p5**（n=8，终态 8）：`succeeded`:8
- **p6**（n=11，终态 11）：`failed`:2, `succeeded`:9
- **p7**（n=10，终态 10）：`succeeded`:10
- **p8**（n=9，终态 9）：`failed`:1, `succeeded`:8
- **p9**（n=11，终态 11）：`failed`:1, `succeeded`:10
- **p11**（n=11，终态 9）：`awaiting_pr_merge`:1, `running`:1, `succeeded`:9
- **p12**（n=5，终态 3）：`awaiting_pr_merge`:1, `running`:1, `succeeded`:3
- **p13**（n=8，终态 3）：`running`:5, `succeeded`:3
- **p14**（n=3，终态 0）：`running`:3
- **p15**（n=11，终态 0）：`running`:11
- **p16**（n=8，终态 0）：`running`:8
- **p17**（n=3，终态 0）：`running`:3
- **p18**（n=4，终态 0）：`running`:4

## 4. 增长曲线数据源（自回归规模证据）

| plan | 首个终态 (UTC) | 末个终态 (UTC) | 跨度 (s) | 终态数 | 吞吐 (/h) | 日桶 |
|---|---|---|---|---|---|---|
| p2 | 2026-10-31T08:26:32.597541+00:00 | 2026-10-31T08:56:45.437856+00:00 | 1812.8 | 11 | 21.84 | 2026-10-31:11 |
| p3 | 2026-10-31T12:53:05.345061+00:00 | 2026-10-31T13:53:33.970626+00:00 | 3628.6 | 13 | 12.90 | 2026-10-31:13 |
| p4 | 2026-10-31T18:10:01.559367+00:00 | 2026-10-31T20:31:36.167733+00:00 | 8494.6 | 9 | 3.81 | 2026-10-31:9 |
| p5 | 2026-10-31T21:03:06.099486+00:00 | 2026-10-31T21:35:01.583579+00:00 | 1915.5 | 8 | 15.04 | 2026-10-31:8 |
| p6 | 2026-10-31T22:02:23.939824+00:00 | 2026-10-31T22:53:15.180502+00:00 | 3051.2 | 11 | 12.98 | 2026-10-31:11 |
| p7 | 2026-10-31T23:52:29.241823+00:00 | 2026-11-01T01:12:55.114777+00:00 | 4825.9 | 10 | 7.46 | 2026-10-31:2, 2026-11-01:8 |
| p8 | 2026-11-01T01:50:27.743677+00:00 | 2026-11-01T04:35:19.527648+00:00 | 9891.8 | 9 | 3.28 | 2026-11-01:9 |
| p9 | 2026-11-01T05:08:16.210068+00:00 | 2026-11-01T07:30:22.574814+00:00 | 8526.4 | 11 | 4.64 | 2026-11-01:11 |
| p11 | 2026-11-06T23:18:36.990389+00:00 | 2026-11-07T00:41:46.996870+00:00 | 4990.0 | 9 | 6.49 | 2026-11-06:8, 2026-11-07:1 |
| p12 | 2026-11-06T23:28:54.472785+00:00 | 2026-11-07T00:11:39.963205+00:00 | 2565.5 | 3 | 4.21 | 2026-11-06:2, 2026-11-07:1 |
| p13 | 2026-11-07T00:25:25.252678+00:00 | 2026-11-07T00:42:49.346065+00:00 | 1044.1 | 3 | 10.34 | 2026-11-07:3 |
| p14 | — | — | — | 0 | — | — |
| p15 | — | — | — | 0 | — | — |
| p16 | — | — | — | 0 | — | — |
| p17 | — | — | — | 0 | — | — |
| p18 | — | — | — | 0 | — | — |

累计增长序列（逐终态单）写在 JSON 的 `plan_reports[].growth.points`，绘图脚本/看板可直接消费，无需二次解析任务书文本。

## 5. Python ↔ SQL 对拍

- 比对项 240 对：实比 204，双空 36，失败 0
- 结论：**一致（PASS）**（容差 rel ≤ 1e-09，时间戳比到 1e-6 s）

| plan | 指标 | Python | SQL | rel_err | 判定 |
|---|---|---|---|---|---|
| 2 | `n_missions` | 11 | 11 | 0.000000000000 | pass |
| 2 | `n_terminal` | 11 | 11 | 0.000000000000 | pass |
| 2 | `n_succeeded` | 11 | 11 | 0.000000000000 | pass |
| 2 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 2 | `success_rate` | 1.000000 | 1.000000 | 0.000000000000 | pass |
| 2 | `retry_share` | 0.090909 | 0.090909 | 0.000000000000 | pass |
| 2 | `claim_latency_s.p50` | 10.061393 | 10.061393 | 0.000000000000 | pass |
| 2 | `claim_latency_s.p95` | 11.300815 | 11.300814 | 0.000000000000 | pass |
| 2 | `wall_clock_s.p50` | 809.551466 | 809.551466 | 0.000000000000 | pass |
| 2 | `wall_clock_s.p95` | 1382.116118 | 1382.116117 | 0.000000000000 | pass |
| 2 | `failed_return_rate` | — | — | — | both_null |
| 2 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 2 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 2 | `throughput.first_terminal_utc` | 2026-10-31T08:26:32.597541+00:00 | 2026-10-31T08:26:32.597541+00:00 | 0.000000000000 | pass |
| 2 | `throughput.last_terminal_utc` | 2026-10-31T08:56:45.437856+00:00 | 2026-10-31T08:56:45.437856+00:00 | 0.000000000000 | pass |
| 3 | `n_missions` | 13 | 13 | 0.000000000000 | pass |
| 3 | `n_terminal` | 13 | 13 | 0.000000000000 | pass |
| 3 | `n_succeeded` | 11 | 11 | 0.000000000000 | pass |
| 3 | `n_failed` | 2 | 2 | 0.000000000000 | pass |
| 3 | `success_rate` | 0.846154 | 0.846154 | 0.000000000000 | pass |
| 3 | `retry_share` | 0.153846 | 0.153846 | 0.000000000000 | pass |
| 3 | `claim_latency_s.p50` | 7.716479 | 7.716479 | 0.000000000000 | pass |
| 3 | `claim_latency_s.p95` | 10.139706 | 10.139706 | 0.000000000000 | pass |
| 3 | `wall_clock_s.p50` | 1879.872474 | 1879.872474 | 0.000000000000 | pass |
| 3 | `wall_clock_s.p95` | 3091.386201 | 3091.386201 | 0.000000000000 | pass |
| 3 | `failed_return_rate` | 1.000000 | 1.000000 | 0.000000000000 | pass |
| 3 | `retry.n_failed_returned` | 2 | 2 | 0.000000000000 | pass |
| 3 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 3 | `throughput.first_terminal_utc` | 2026-10-31T12:53:05.345061+00:00 | 2026-10-31T12:53:05.345061+00:00 | 0.000000000000 | pass |
| 3 | `throughput.last_terminal_utc` | 2026-10-31T13:53:33.970626+00:00 | 2026-10-31T13:53:33.970626+00:00 | 0.000000000000 | pass |
| 4 | `n_missions` | 9 | 9 | 0.000000000000 | pass |
| 4 | `n_terminal` | 9 | 9 | 0.000000000000 | pass |
| 4 | `n_succeeded` | 8 | 8 | 0.000000000000 | pass |
| 4 | `n_failed` | 1 | 1 | 0.000000000000 | pass |
| 4 | `success_rate` | 0.888889 | 0.888889 | 0.000000000000 | pass |
| 4 | `retry_share` | 0.111111 | 0.111111 | 0.000000000000 | pass |
| 4 | `claim_latency_s.p50` | 2.328237 | 2.328237 | 0.000000000000 | pass |
| 4 | `claim_latency_s.p95` | 3.289654 | 3.289654 | 0.000000000000 | pass |
| 4 | `wall_clock_s.p50` | 2077.312047 | 2077.312047 | 0.000000000000 | pass |
| 4 | `wall_clock_s.p95` | 5996.646948 | 5996.646948 | 0.000000000000 | pass |
| 4 | `failed_return_rate` | 1.000000 | 1.000000 | 0.000000000000 | pass |
| 4 | `retry.n_failed_returned` | 1 | 1 | 0.000000000000 | pass |
| 4 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 4 | `throughput.first_terminal_utc` | 2026-10-31T18:10:01.559367+00:00 | 2026-10-31T18:10:01.559367+00:00 | 0.000000000000 | pass |
| 4 | `throughput.last_terminal_utc` | 2026-10-31T20:31:36.167733+00:00 | 2026-10-31T20:31:36.167733+00:00 | 0.000000000000 | pass |
| 5 | `n_missions` | 8 | 8 | 0.000000000000 | pass |
| 5 | `n_terminal` | 8 | 8 | 0.000000000000 | pass |
| 5 | `n_succeeded` | 8 | 8 | 0.000000000000 | pass |
| 5 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 5 | `success_rate` | 1.000000 | 1.000000 | 0.000000000000 | pass |
| 5 | `retry_share` | 0.000000 | 0.000000 | 0.000000000000 | pass |
| 5 | `claim_latency_s.p50` | 2.755440 | 2.755440 | 0.000000000000 | pass |
| 5 | `claim_latency_s.p95` | 3.773711 | 3.773711 | 0.000000000000 | pass |
| 5 | `wall_clock_s.p50` | 1873.570557 | 1873.570557 | 0.000000000000 | pass |
| 5 | `wall_clock_s.p95` | 2621.697634 | 2621.697634 | 0.000000000000 | pass |
| 5 | `failed_return_rate` | — | — | — | both_null |
| 5 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 5 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 5 | `throughput.first_terminal_utc` | 2026-10-31T21:03:06.099486+00:00 | 2026-10-31T21:03:06.099486+00:00 | 0.000000000000 | pass |
| 5 | `throughput.last_terminal_utc` | 2026-10-31T21:35:01.583579+00:00 | 2026-10-31T21:35:01.583579+00:00 | 0.000000000000 | pass |
| 6 | `n_missions` | 11 | 11 | 0.000000000000 | pass |
| 6 | `n_terminal` | 11 | 11 | 0.000000000000 | pass |
| 6 | `n_succeeded` | 9 | 9 | 0.000000000000 | pass |
| 6 | `n_failed` | 2 | 2 | 0.000000000000 | pass |
| 6 | `success_rate` | 0.818182 | 0.818182 | 0.000000000000 | pass |
| 6 | `retry_share` | 0.090909 | 0.090909 | 0.000000000000 | pass |
| 6 | `claim_latency_s.p50` | 2.892156 | 2.892156 | 0.000000000000 | pass |
| 6 | `claim_latency_s.p95` | 4.654979 | 4.654978 | 0.000000000000 | pass |
| 6 | `wall_clock_s.p50` | 1618.363159 | 1618.363159 | 0.000000000000 | pass |
| 6 | `wall_clock_s.p95` | 3331.004285 | 3331.004285 | 0.000000000000 | pass |
| 6 | `failed_return_rate` | 0.500000 | 0.500000 | 0.000000000000 | pass |
| 6 | `retry.n_failed_returned` | 1 | 1 | 0.000000000000 | pass |
| 6 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 6 | `throughput.first_terminal_utc` | 2026-10-31T22:02:23.939824+00:00 | 2026-10-31T22:02:23.939824+00:00 | 0.000000000000 | pass |
| 6 | `throughput.last_terminal_utc` | 2026-10-31T22:53:15.180502+00:00 | 2026-10-31T22:53:15.180502+00:00 | 0.000000000000 | pass |
| 7 | `n_missions` | 10 | 10 | 0.000000000000 | pass |
| 7 | `n_terminal` | 10 | 10 | 0.000000000000 | pass |
| 7 | `n_succeeded` | 10 | 10 | 0.000000000000 | pass |
| 7 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 7 | `success_rate` | 1.000000 | 1.000000 | 0.000000000000 | pass |
| 7 | `retry_share` | 0.000000 | 0.000000 | 0.000000000000 | pass |
| 7 | `claim_latency_s.p50` | 4.007782 | 4.007782 | 0.000000000000 | pass |
| 7 | `claim_latency_s.p95` | 5.735221 | 5.735221 | 0.000000000000 | pass |
| 7 | `wall_clock_s.p50` | 1739.864342 | 1739.864342 | 0.000000000000 | pass |
| 7 | `wall_clock_s.p95` | 4669.943585 | 4669.943585 | 0.000000000000 | pass |
| 7 | `failed_return_rate` | — | — | — | both_null |
| 7 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 7 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 7 | `throughput.first_terminal_utc` | 2026-10-31T23:52:29.241823+00:00 | 2026-10-31T23:52:29.241823+00:00 | 0.000000000000 | pass |
| 7 | `throughput.last_terminal_utc` | 2026-11-01T01:12:55.114777+00:00 | 2026-11-01T01:12:55.114777+00:00 | 0.000000000000 | pass |
| 8 | `n_missions` | 9 | 9 | 0.000000000000 | pass |
| 8 | `n_terminal` | 9 | 9 | 0.000000000000 | pass |
| 8 | `n_succeeded` | 8 | 8 | 0.000000000000 | pass |
| 8 | `n_failed` | 1 | 1 | 0.000000000000 | pass |
| 8 | `success_rate` | 0.888889 | 0.888889 | 0.000000000000 | pass |
| 8 | `retry_share` | 0.111111 | 0.111111 | 0.000000000000 | pass |
| 8 | `claim_latency_s.p50` | 4.821151 | 4.821151 | 0.000000000000 | pass |
| 8 | `claim_latency_s.p95` | 6.245049 | 6.245049 | 0.000000000000 | pass |
| 8 | `wall_clock_s.p50` | 2460.144864 | 2460.144864 | 0.000000000000 | pass |
| 8 | `wall_clock_s.p95` | 6164.529954 | 6164.529954 | 0.000000000000 | pass |
| 8 | `failed_return_rate` | 1.000000 | 1.000000 | 0.000000000000 | pass |
| 8 | `retry.n_failed_returned` | 1 | 1 | 0.000000000000 | pass |
| 8 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 8 | `throughput.first_terminal_utc` | 2026-11-01T01:50:27.743677+00:00 | 2026-11-01T01:50:27.743677+00:00 | 0.000000000000 | pass |
| 8 | `throughput.last_terminal_utc` | 2026-11-01T04:35:19.527648+00:00 | 2026-11-01T04:35:19.527648+00:00 | 0.000000000000 | pass |
| 9 | `n_missions` | 11 | 11 | 0.000000000000 | pass |
| 9 | `n_terminal` | 11 | 11 | 0.000000000000 | pass |
| 9 | `n_succeeded` | 10 | 10 | 0.000000000000 | pass |
| 9 | `n_failed` | 1 | 1 | 0.000000000000 | pass |
| 9 | `success_rate` | 0.909091 | 0.909091 | 0.000000000000 | pass |
| 9 | `retry_share` | 0.090909 | 0.090909 | 0.000000000000 | pass |
| 9 | `claim_latency_s.p50` | 21.058169 | 21.058169 | 0.000000000000 | pass |
| 9 | `claim_latency_s.p95` | 75.263854 | 75.263854 | 0.000000000000 | pass |
| 9 | `wall_clock_s.p50` | 1753.941098 | 1753.941098 | 0.000000000000 | pass |
| 9 | `wall_clock_s.p95` | 5945.206480 | 5945.206480 | 0.000000000000 | pass |
| 9 | `failed_return_rate` | 1.000000 | 1.000000 | 0.000000000000 | pass |
| 9 | `retry.n_failed_returned` | 1 | 1 | 0.000000000000 | pass |
| 9 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 9 | `throughput.first_terminal_utc` | 2026-11-01T05:08:16.210068+00:00 | 2026-11-01T05:08:16.210068+00:00 | 0.000000000000 | pass |
| 9 | `throughput.last_terminal_utc` | 2026-11-01T07:30:22.574814+00:00 | 2026-11-01T07:30:22.574814+00:00 | 0.000000000000 | pass |
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
| 14 | `n_missions` | 3 | 3 | 0.000000000000 | pass |
| 14 | `n_terminal` | 0 | 0 | 0.000000000000 | pass |
| 14 | `n_succeeded` | 0 | 0 | 0.000000000000 | pass |
| 14 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 14 | `success_rate` | — | — | — | both_null |
| 14 | `retry_share` | 0.000000 | 0.000000 | 0.000000000000 | pass |
| 14 | `claim_latency_s.p50` | 0.193712 | 0.193712 | 0.000000000000 | pass |
| 14 | `claim_latency_s.p95` | 0.232682 | 0.232682 | 0.000000000000 | pass |
| 14 | `wall_clock_s.p50` | — | — | — | both_null |
| 14 | `wall_clock_s.p95` | — | — | — | both_null |
| 14 | `failed_return_rate` | — | — | — | both_null |
| 14 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 14 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 14 | `throughput.first_terminal_utc` | — | — | — | both_null |
| 14 | `throughput.last_terminal_utc` | — | — | — | both_null |
| 15 | `n_missions` | 11 | 11 | 0.000000000000 | pass |
| 15 | `n_terminal` | 0 | 0 | 0.000000000000 | pass |
| 15 | `n_succeeded` | 0 | 0 | 0.000000000000 | pass |
| 15 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 15 | `success_rate` | — | — | — | both_null |
| 15 | `retry_share` | 0.000000 | 0.000000 | 0.000000000000 | pass |
| 15 | `claim_latency_s.p50` | 2.077767 | 2.077767 | 0.000000000000 | pass |
| 15 | `claim_latency_s.p95` | 6.644547 | 6.644547 | 0.000000000000 | pass |
| 15 | `wall_clock_s.p50` | — | — | — | both_null |
| 15 | `wall_clock_s.p95` | — | — | — | both_null |
| 15 | `failed_return_rate` | — | — | — | both_null |
| 15 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 15 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 15 | `throughput.first_terminal_utc` | — | — | — | both_null |
| 15 | `throughput.last_terminal_utc` | — | — | — | both_null |
| 16 | `n_missions` | 8 | 8 | 0.000000000000 | pass |
| 16 | `n_terminal` | 0 | 0 | 0.000000000000 | pass |
| 16 | `n_succeeded` | 0 | 0 | 0.000000000000 | pass |
| 16 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 16 | `success_rate` | — | — | — | both_null |
| 16 | `retry_share` | 0.000000 | 0.000000 | 0.000000000000 | pass |
| 16 | `claim_latency_s.p50` | 8.255653 | 8.255653 | 0.000000000000 | pass |
| 16 | `claim_latency_s.p95` | 9.023350 | 9.023350 | 0.000000000000 | pass |
| 16 | `wall_clock_s.p50` | — | — | — | both_null |
| 16 | `wall_clock_s.p95` | — | — | — | both_null |
| 16 | `failed_return_rate` | — | — | — | both_null |
| 16 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 16 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 16 | `throughput.first_terminal_utc` | — | — | — | both_null |
| 16 | `throughput.last_terminal_utc` | — | — | — | both_null |
| 17 | `n_missions` | 3 | 3 | 0.000000000000 | pass |
| 17 | `n_terminal` | 0 | 0 | 0.000000000000 | pass |
| 17 | `n_succeeded` | 0 | 0 | 0.000000000000 | pass |
| 17 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 17 | `success_rate` | — | — | — | both_null |
| 17 | `retry_share` | 0.000000 | 0.000000 | 0.000000000000 | pass |
| 17 | `claim_latency_s.p50` | 0.515728 | 0.515728 | 0.000000000000 | pass |
| 17 | `claim_latency_s.p95` | 1.232550 | 1.232550 | 0.000000000000 | pass |
| 17 | `wall_clock_s.p50` | — | — | — | both_null |
| 17 | `wall_clock_s.p95` | — | — | — | both_null |
| 17 | `failed_return_rate` | — | — | — | both_null |
| 17 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 17 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 17 | `throughput.first_terminal_utc` | — | — | — | both_null |
| 17 | `throughput.last_terminal_utc` | — | — | — | both_null |
| 18 | `n_missions` | 4 | 4 | 0.000000000000 | pass |
| 18 | `n_terminal` | 0 | 0 | 0.000000000000 | pass |
| 18 | `n_succeeded` | 0 | 0 | 0.000000000000 | pass |
| 18 | `n_failed` | 0 | 0 | 0.000000000000 | pass |
| 18 | `success_rate` | — | — | — | both_null |
| 18 | `retry_share` | 0.000000 | 0.000000 | 0.000000000000 | pass |
| 18 | `claim_latency_s.p50` | 2.036996 | 2.036996 | 0.000000000000 | pass |
| 18 | `claim_latency_s.p95` | 3.307111 | 3.307111 | 0.000000000000 | pass |
| 18 | `wall_clock_s.p50` | — | — | — | both_null |
| 18 | `wall_clock_s.p95` | — | — | — | both_null |
| 18 | `failed_return_rate` | — | — | — | both_null |
| 18 | `retry.n_failed_returned` | 0 | 0 | 0.000000000000 | pass |
| 18 | `retry.custody_requeue_events` | 0 | 0 | 0.000000000000 | pass |
| 18 | `throughput.first_terminal_utc` | — | — | — | both_null |
| 18 | `throughput.last_terminal_utc` | — | — | — | both_null |

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
  SELECT * FROM base WHERE plan_id IN (2,3,4,5,6,7,8,9,11,12,13,14,15,16,17,18)
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
   WHERE b.status = 'failed' AND b.plan_id IN (2,3,4,5,6,7,8,9,11,12,13,14,15,16,17,18)
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
复算方式：`python3 tools/campaign_throughput.py --plans 2,3,4,5,6,7,8,9,11,12,13,14,15,16,17,18 --crosscheck`（只读；`--dry-run` 只打印不落盘）。
