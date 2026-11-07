# IN27-H7 SR 继承就绪度量 —— 周报

> 生成时钟 `2026-11-07T10:37:35Z` · schema `sr-inheritance/1` · chronicle 时钟槽 `s8`（harness）· 窗口 30 d / 周窗 7 d · 域语义 `none`

## 1. 记分板

| 指标 | 当前值 | 目标 | 达标 | 证据等级 | 口径 |
|---|---|---|---|---|---|
| SR1 影子分歧率 | WITHHELD | < 20% | WITHHELD | `WITHHELD` | no matured shadow item: 8 pending, 0 unobservable, 4 shadow round(s) in window |
| SR2 协调官自主天数 | 5.13 d | ≥ 7 d | no | `REAL` | 5.13 d since the last hand-typed decision (below the 7 d target) |
| SR3 红线决策包完整正确率 | 49.0% | = 100%（抽检） | no | `REAL` | 98/200 audited packs complete and consistent (sample 200 of 4304 decided packs; seed IN27-H7) |
| SR4 owner 非红线干预/周 | 11.00 | = 0 | no | `REAL` | 11 routine hand-typed decision(s) in 7 d (0 red-line hand-typed decision(s) kept out of the metric) |

## 2. SR1 影子分歧率（首月基线）

- 窗口内影子回合：**4**（ids=[1, 2, 3, 4]），提议动作 **8** 条；可判定（matured/early）**0** 条，其中 matched=0、diverged=0，pending=8、unobservable=0。
- 判读窗口：look-ahead 24.0 h（未到期记 `pending`，绝不进分母）；早期读数窗口 1.0 h（仅在已出现处置证据时提前判定）。

| look-ahead | 分歧率 | 可判定/条目 | 已到期 |
|---|---|---|---|
| 0.5 h | 75.0% | 4/8 | no |
| 1 h | WITHHELD | 0/8 | no |
| 6 h | WITHHELD | 0/8 | no |
| 24 h | WITHHELD | 0/8 | no |

- **首月基线读数**：0.5 h look-ahead 上 75.0%——该读数的分母是 4/8 条已到期条目（matched=1、diverged=3），另有 4 条 pending 不计入；主读数仍取 24 h。

| 动作 | matched | diverged | pending | unobservable |
|---|---|---|---|---|
| `escalate` | 0 | 0 | 2 | 0 |
| `file_mission` | 0 | 0 | 4 | 0 |
| `monitor` | 0 | 0 | 2 | 0 |

| # | 回合 | 动作 | 目标器官 | 判定 | 依据 | 命中证据 |
|---|---|---|---|---|---|---|
| 1 | 1 | `escalate` | `dispatcher_warns` | **pending** | window-not-elapsed | 0 |
| 2 | 1 | `file_mission` | `s5_outcomes` | **pending** | window-not-elapsed | 0 |
| 3 | 1 | `file_mission` | `queued_stuck` | **pending** | window-not-elapsed | 0 |
| 4 | 1 | `monitor` | `s2_campaign` | **pending** | window-not-elapsed | 0 |
| 5 | 3 | `escalate` | `dispatcher_warns` | **pending** | window-not-elapsed | 0 |
| 6 | 3 | `file_mission` | `s2_campaign` | **pending** | window-not-elapsed | 0 |
| 7 | 3 | `file_mission` | `s7_voice` | **pending** | window-not-elapsed | 0 |
| 8 | 3 | `monitor` | `awaiting_pr_merge` | **pending** | window-not-elapsed | 0 |

## 3. SR2 协调官自主天数

- 续航锚点：`2026-11-02T07:23:59Z`（last-hand-typed-decision）→ **5.13 d**
- 窗口内手打决策 14 次（≈3.27/周）；无手打决策的洁净天数 28/30。

## 4. SR3 红线决策包抽检

- 总体（decided）：**4304** 包；抽检 **200** 包（md5 定序，seed `IN27-H7`，可复算）；未决包 0 个被排除。

| 检查项 | 通过 | 失败 |
|---|---|---|
| 结论头（headline）实质 | 200 | 0 |
| 验证叙述（checks）实质 | 197 | 3 |
| 交付物指针（路径/commit/PR） | 197 | 3 |
| 量化数字 | 102 | 98 |
| mission_id + 决策备注可追溯 | 198 | 2 |
| 结论与任务终态一致 | 195 | 5 |

## 5. SR4 owner 非红线干预

- 周窗内非红线手打决策 **11** 次（kinds=['dev_acceptance']）→ **11.00/周**（目标 0）。
- 红线内手打决策 0 次（kinds=[]），不计入本指标。
- 红线生命周期动作（write_off/suspend/…）在周窗内 23 条。

## 6. 仪表连续产出

- 近 14 d：有快照 1 天，覆盖率 7.1%，当前连续产出 1 天；缺口 ['2026-11-06', '2026-11-05', '2026-11-04', '2026-11-03', '2026-11-02', '2026-11-01', '2026-10-31', '2026-10-30', '2026-10-29', '2026-10-28', '2026-10-27', '2026-10-26', '2026-10-25']。

## 7. 趋势（chronicle s8 槽）

| ts | SR1 | SR2 (d) | SR3 | SR4 (/wk) |
|---|---|---|---|---|
| 2026-11-07T10:35:22Z | WITHHELD | 5.13 | 49.0% | 11.00 |
| 2026-11-07T10:34:50Z | WITHHELD | 5.13 | 49.0% | 11.00 |
| 2026-11-07T10:31:44Z | WITHHELD | 5.13 | 49.0% | 11.00 |
| 2026-11-07T10:30:55Z | WITHHELD | 5.13 | 49.0% | 11.00 |
| 2026-11-07T10:30:05Z | WITHHELD | 5.13 | 49.0% | 11.00 |
| 2026-11-07T10:29:17Z | WITHHELD | 5.13 | 49.0% | 11.00 |
| 2026-11-07T10:28:28Z | WITHHELD | 5.13 | 49.0% | 11.00 |
| 2026-11-07T10:28:05Z | WITHHELD | 5.13 | 49.0% | 11.00 |
| 2026-11-07T10:27:42Z | WITHHELD | 5.13 | 57.0% | 11.00 |

## 8. 复算入口

每个数字都对应下方一条 SQL（参数已代入，可直接粘进 `psql`）：

- `SR1` / `shadow_rounds`: `SELECT id, ts, verdict FROM coordinator_shadow WHERE ts >= '2026-10-08 10:37:35+00' AND ts < '2026-11-07 10:37:35+00' ORDER BY ts, id`
- `SR1` / `disposition_feed:mission_goal`: `SELECT created_at, coalesce(goal,'') FROM tensor_dev_missions WHERE created_at >= '2026-10-08 10:37:35+00' AND created_at < '2026-11-07 10:37:35+00'`
- `SR1` / `disposition_feed:mission_dispatch`: `SELECT created_at, coalesce(detail::text,'') FROM campaign_events WHERE event_type = 'mission_dispatched' AND created_at >= '2026-10-08 10:37:35+00' AND created_at < '2026-11-07 10:37:35+00'`
- `SR1` / `disposition_feed:escalation`: `SELECT created_at, coalesce(detail::text,'') FROM campaign_events WHERE event_type = 'escalation_created' AND created_at >= '2026-10-08 10:37:35+00' AND created_at < '2026-11-07 10:37:35+00'`
- `SR1` / `disposition_feed:alert`: `SELECT created_at, concat_ws(' ', title, message, entity_id, source) FROM alerts WHERE created_at >= '2026-10-08 10:37:35+00' AND created_at < '2026-11-07 10:37:35+00'`
- `SR1` / `disposition_feed:hand_typed_decision`: `SELECT created_at, coalesce(payload->>'note','') FROM tensor_agent_session_events WHERE kind = 'approval_decided' AND NOT starts_with(coalesce(payload->>'note',''), '[auto-approve]') AND coalesce(payload->>'note','') <> '' AND created_at >= '2026-10-08 10:37:35+00' AND created_at < '2026-11-07 10:37:35+00'`
- `SR2` / `hand_typed_decisions`: `SELECT created_at, coalesce(payload->>'kind','') AS kind, coalesce(payload->>'decision','') AS decision, coalesce(payload->>'note','') AS note FROM tensor_agent_session_events WHERE kind = 'approval_decided' AND created_at >= '2026-10-08 10:37:35+00' AND created_at < '2026-11-07 10:37:35+00' AND coalesce(payload->>'note','') <> '' AND NOT starts_with(coalesce(payload->>'note',''), '[auto-approve]')`
- `SR2` / `last_hand_typed_decision`: `SELECT max(created_at) FROM tensor_agent_session_events WHERE kind = 'approval_decided' AND coalesce(payload->>'note','') <> '' AND NOT starts_with(coalesce(payload->>'note',''), '[auto-approve]')`
- `SR3` / `decision_packs`: `SELECT a.id, a.kind, a.status, a.mission_id, coalesce(a.reason,''), a.evidence, coalesce(a.decision_note,''), a.decided_at, coalesce(m.status,'') FROM tensor_approval_requests a LEFT JOIN tensor_dev_missions m ON m.id = a.mission_id WHERE a.created_at >= '2026-10-08 10:37:35+00' AND a.created_at < '2026-11-07 10:37:35+00' ORDER BY a.id`
- `SR4` / `routine_hand_typed_decisions`: `SELECT created_at, coalesce(payload->>'kind','') AS kind, coalesce(payload->>'decision','') AS decision, coalesce(payload->>'note','') AS note FROM tensor_agent_session_events WHERE kind = 'approval_decided' AND created_at >= '2026-10-31 10:37:35+00' AND created_at < '2026-11-07 10:37:35+00' AND coalesce(payload->>'note','') <> '' AND NOT starts_with(coalesce(payload->>'note',''), '[auto-approve]')`
- `SR4` / `redline_lifecycle_actions`: `SELECT created_at, action, coalesce(detail->>'reported_by','') AS reported_by FROM mission_lifecycle_log WHERE created_at >= '2026-10-31 10:37:35+00' AND created_at < '2026-11-07 10:37:35+00' AND action = ANY(['budget_grant', 'git_config', 'permission_change', 'secret_rotate', 'suspend', 'write_off'])`
