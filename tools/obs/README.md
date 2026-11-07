# tools/obs — 观测告警最小面（additive, read-only）

稳生产用的最小观测面：把平台自己的台账读成**一页战况**，并把「跑着的」与「还活着的」区分开。
不动冻结 API 面、不做 schema 手术、不写任何数据库（只有 `SELECT`）。

| 文件 | 作用 |
|---|---|
| `alert_rules.py` | 纯规则引擎（无 I/O、无墙钟）：R1 停滞 / R2 失败×3 / R3 待放门 + 三条抑制负例 + 时间投影 |
| `obs_digest.py` | CLI：取快照（只读 psql）→ 评估 → digest md / alerts JSON / 判定平面图；带 `--dry-run` |
| `gen_report_md.py` | 从 alerts JSON 生成报告 markdown（数字程序化注入，零手抄） |
| `tests/` | pytest：18 例，含每条规则的正例与负例 |

## 判据（阈值集中在 `alert_rules.THRESHOLDS`）

| 规则 | 判据 | 级别 | 处置 |
|---|---|---|---|
| R1_STUCK_RUNNING | `running` 且心跳年龄 ≥ 120 min | P1 | 先判活再 stop 释放槽位 |
| R2_BLOCKED_THRICE | `blocked` 且 `consecutive_fails` ≥ 3 | P1 | 进处置账，禁止原样重派 |
| R3_PENDING_GATE | 阻塞原因指向放门 / 审批 `pending` | P2 | 确认放门条件，非故障 |
| S1_JUST_CLAIMED | 领取 ≤ 15 min 且从未心跳 | — | 不告警（负例 1） |
| S2_LONG_BUT_ALIVE | 跑得久但心跳新鲜 | — | 不告警（负例 2：长跑≠停滞） |
| S3_SUBTHRESHOLD | blocked 但失败 < 3 次 | — | 只进观察账 |

## 用法

```bash
# 1) 现场：读库生成 digest（只读），并把输入快照落盘以便复现
python3 tools/obs/obs_digest.py --from-db --since-days 8 \
  --write-snapshot artifacts/in15_o8/snapshot_db.json \
  --md-out artifacts/in15_o8/digest_live.md \
  --json-out artifacts/in15_o8/alerts_live.json \
  --figure artifacts/in15_o8/staleness_live.png

# 2) 回放历史某一瞬（真实停滞案例取证；自动启用时间投影）
python3 tools/obs/obs_digest.py --snapshot artifacts/in15_o8/snapshot_db.json \
  --as-of 2026-11-02T20:30:00Z \
  --md-out artifacts/in15_o8/digest_replay_20261102T2030Z.md \
  --json-out artifacts/in15_o8/alerts_replay_20261102T2030Z.json

# 3) 只算不写
python3 tools/obs/obs_digest.py --from-db --dry-run --quiet

# 4) cron：P1 存在时退出码 2（可接守望线）
python3 tools/obs/obs_digest.py --from-db --md-out /tmp/digest.md --json-out /tmp/alerts.json --fail-on-alert
```

## 纪律

- **只增不改**：新目录、新文件；不 import 平台冻结 API，不改表结构，不写数据库。
- **可复现**：同快照 + 同 `--as-of` ⇒ 逐字节相同的 alerts JSON（快照 SHA-256 写在两份产物里）。
- **负例留证**：每次评估都把「看过但故意不告警」的对象与观测值写进 `suppressions`。
- **保守优先**：回放时对无时间戳的 `stopped/suspended/revoked` 不判 `running`（宁可漏报不误报），
  局限写在报告 ⑤ 与 `projection` 字段里。