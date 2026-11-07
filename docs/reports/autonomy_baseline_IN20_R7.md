# 自主度退出仪表盘 · 基线（IN20-R7）

- 生成时刻：`2026-11-07T03:32:34Z`
- 统计窗口：`2026-11-06T03:32:34Z` → `2026-11-07T03:32:34Z`（24.0 h）
- 交互通道身份（人类侧）：`2`
- 产出器：`tools/autonomy/dashboard.py` · commit `01548565867e963922e7239b5ca8b4d6f9bc4067`

## 指标一 无人类输入续航时长

| 项 | 值 | 证据级 |
|---|---|---|
| 续航时长 | **101.29 h** = 4.22 d | REAL |
| 最近一次人类动作 | `2026-11-02T22:15:19Z` | REAL |

## 指标二 人类干预次数/日（owner 与协调者分列）

| 角色类 | 窗口内次数 | 折算/日 | 最近一次 | 证据级 |
|---|---|---|---|---|
| owner（裁决/派单） | 0 | 0.0 | `2026-11-02 22:15:19.056729+00:00` | REAL |
| coordinator（会话动作） | 0 | 0.0 | `2026-11-02 01:55:59.459896+00:00` | REAL |
| 合计 | 0 | 0.0 | — | REAL |

## 指标三 机制覆盖率（分母 = 补给/调节/放门/合并/收割/记录/自愈/文档 八项）

**覆盖率 = 5/8 = 62.5%**（证据级 REAL）

| 职能 | daemon/替身 | 窗口内活动 | 判定 |
|---|---|---|---|
| 补给（任务池补货/派发） | `campaign_dispatcher.py` | 87 | REAL ✅ |
| 调节（饱和度脉搏/放行） | `pulse_report.py` | 33 | REAL ✅ |
| 放门（验收/总攻门自动放行） | `auto_approver.py` | 260 | REAL ✅ |
| 合并（PR 门与合流通道） | `merge_governor.py` | 205 | REAL ✅ |
| 收割（失联/超时任务清收） | `close_meaningless.py` | 0 | STUB |
| 记录（编年史快照落账） | `—` | 2 | MOCK |
| 自愈（席位补齐/任务回收重生） | `fleet_supervisor.sh` | 0 | STUB |
| 文档（报告产出/补报/质检） | `report_watch.py` | 301 | REAL ✅ |

> 判定规则：`covered = daemon 产物存在 AND 窗口内活动证据 > 0`；只有 daemon 无活动 = `STUB`，只有人做 = `MOCK`。

## SQL 可复算索引

| 指标 | 源 | 语句 |
|---|---|---|
| M1 | `owner_adjudication` | `SELECT max(decided_at) AS last_ts FROM tensor_approval_requests WHERE decided_by IN (2)` |
| M2 | `owner_adjudication` | `SELECT count(*) AS n FROM tensor_approval_requests WHERE decided_by IN (2) AND decided_at >= '2026-11-06 03:32:34+00' AND decided_at < '2026-11-07 03:32:34+00'` |
| M1 | `owner_dispatch` | `SELECT max(created_at) AS last_ts FROM tensor_dev_missions WHERE created_by IN (2) AND origin = 'owner'` |
| M2 | `owner_dispatch` | `SELECT count(*) AS n FROM tensor_dev_missions WHERE created_by IN (2) AND origin = 'owner' AND created_at >= '2026-11-06 03:32:34+00' AND created_at < '2026-11-07 03:32:34+00'` |
| M1 | `coordinator_session` | `SELECT max(created_at) AS last_ts FROM tensor_agent_sessions WHERE started_by IN (2)` |
| M2 | `coordinator_session` | `SELECT count(*) AS n FROM tensor_agent_sessions WHERE started_by IN (2) AND created_at >= '2026-11-06 03:32:34+00' AND created_at < '2026-11-07 03:32:34+00'` |
| M3 | `refill` | `SELECT count(*) AS n FROM campaign_events WHERE event_type = 'mission_dispatched' AND created_at >= '2026-11-06 03:32:34+00' AND created_at < '2026-11-07 03:32:34+00'` |
| M3 | `regulate` | `SELECT count(*) AS n FROM saturation_pulse WHERE ts >= '2026-11-06 03:32:34+00' AND ts < '2026-11-07 03:32:34+00'` |
| M3 | `gate` | `SELECT count(*) AS n FROM tensor_agent_session_events WHERE kind = 'approval_decided' AND starts_with(payload->>'note', '[auto-approve]') AND created_at >= '2026-11-06 03:32:34+00' AND created_at < '2026-11-07 03:32:34+00'` |
| M3 | `merge` | `SELECT count(*) AS n FROM mission_lifecycle_log WHERE action = 'pr_gate' AND created_at >= '2026-11-06 03:32:34+00' AND created_at < '2026-11-07 03:32:34+00'` |
| M3 | `reap` | `SELECT count(*) AS n FROM mission_lifecycle_log WHERE action IN ('write_off', 'suspend') AND created_at >= '2026-11-06 03:32:34+00' AND created_at < '2026-11-07 03:32:34+00'` |
| M3 | `record` | `SELECT count(*) AS n FROM chronicle_snapshots WHERE ts >= '2026-11-06 03:32:34+00' AND ts < '2026-11-07 03:32:34+00'` |
| M3 | `self_heal` | `SELECT count(*) AS n FROM tensor_agent_session_events WHERE payload->>'subtype' IN ('mission_reclaimed', 'mission_custody_requeue') AND created_at >= '2026-11-06 03:32:34+00' AND created_at < '2026-11-07 03:32:34+00'` |
| M3 | `documents` | `SELECT count(*) AS n FROM tensor_reports WHERE created_at >= '2026-11-06 03:32:34+00' AND created_at < '2026-11-07 03:32:34+00'` |

### 编年史趋势（chronicle_snapshots）

| ts | 续航h | owner | coord | 覆盖率% |
|---|---|---|---|---|
| 2026-11-07T03:32:34.902028+00:00 | 101.29 | 0 | 0 | 62.5 |
