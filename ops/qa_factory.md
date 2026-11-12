# QA 产线 daemon（FEAT-QA-FACTORY）

**作用**：把报告质检的串行段换成「机器三查分档（O(批)）+ 分层深检」。机器只给 pass 档开清账授权，
flag/suspect 档进深检队列；**一致性门未通过时 daemon 拒绝清账**（不给自己授权）。

## 纳管

| 项 | 值 |
|---|---|
| 进程 | `python3 /root/tp-agent/work/m<当前树>/tools/qa_factory/daemon.py` |
| 单实例 | `flock /var/lib/tp-qa-factory/qa_factory.lock`（重复启动即退出，退出码 0） |
| 心跳 | `/var/lib/tp-qa-factory/heartbeat.json`（`alive_utc/cycles/cleared_total/tiered_total/gate_passed`） |
| 状态 | `/var/lib/tp-qa-factory/state.json`（批次游标 + 累计计数 + 本轮 gate 判决） |
| 日志 | `/var/log/tp-qa-factory.log`（每轮一行：scanned/tiers/cleared/gate） |
| 批产物 | `artifacts/qa_factory/round1/batches/batch_<i>.json`（逐篇证据） |
| autoheal | 已登记 `ops/taskplane/tp_autoheal.py` REGISTRY（name=`qa_factory`） |

## 运维动作

```bash
python3 -m tools.qa_factory.cli status                 # schema + 分档计数巡检
python3 -m tools.qa_factory.daemon --once --batches 5   # 手动推进 5 批（不清账：--clear-batch 0）
python3 -m tools.qa_factory.cli clear --batch 2000      # 需新鲜且通过的 consistency.json，否则退出码 3
python3 -m tools.qa_factory.cli sample --scan artifacts/qa_factory/round1/latest.json --per-stratum 15
```

## 红线

1. 机器门判据（阈值/门限）只能升版本（`GATE_VERSION` + PREREG 调整记录）后重跑全量，禁止就地调参。
2. `qa_status` 只能由 `--consistency` 门通过后的 `clear`/daemon 改；`--force` 必须写进报告。
3. 机器门不判物理正确性：`pass` ≠ 结论正确；物理对错归深检/复算。
