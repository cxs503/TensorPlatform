#!/usr/bin/env python3
"""停摆时间线 fixture 生成器（IN20-R5 回归样例 · 2026-11-07 00:52→03:18）。

为什么要生成器而不是手写 JSON
----------------------------
回归样例必须**可重算**：锚点来自平台台账实测，缺口内的每分钟值按**显式重建律**
插值，重建律写在 fixture 的 provenance 里，任何读者都能重跑本脚本逐字节复核。

真实锚点（2026-11-07 实测 `saturation_pulse`，只读 SELECT）
----------------------------------------------------------
    select to_char(ts,'YYYY-MM-DD HH24:MI:SS'), online, busy, held
    from saturation_pulse order by ts;              -- 见 ANCHORS

实测断档：最后一行 00:52:35，下一行 03:20:08 → gap = 8841 s = 147.35 min。
这就是任务书说的 00:52→03:18 停摆；脉搏表本身就是那条证据。

重建律（缺口内 = 无观测区，证据等级 WITHHELD）
----------------------------------------------
* `pulse_last_ts`：取「≤ 样本时刻的最后一条实测脉搏行」——缺口内自然冻结在
  00:52:35，这正是规则 R3 要看的信号（表停 = 生产者停）。
* `workers_online`：缺口内恒取 52（00:52:06 与 03:20:08 两侧实测都是 52）。
* `busy`：00:52:35 的实测 30 在 10 min 内线性降到 8（03:22:43 / 03:24:43 实测
  低水位），其后恒 8 —— 「断粮后车没活干」的重建形状，非实测。
* `pending / queued`：缺口内 = 0（任务书给定 + 当日 campaign_nodes 可派状态实测 0）。
* `fleet_heartbeat`：本案例是断粮+脉搏停，不是舰队失联；enrolled=57 心率全新鲜
  （57 = 当日 factory_resident_workers enabled 实测行数）→ R2 应保持沉默（负控）。
* 恢复段（≥03:19）：pending=19 并保持（当日 campaign_nodes in-flight=19 实测；
  03:15–03:30 桶新建任务 19 条实测），脉搏恢复新鲜。

用法：python3 tools/stall_fixture_20261107.py [--out PATH]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "tests/fixtures/stall_20261107_0052_0318.json"

# 实测锚点：2026-11-07 saturation_pulse（ts, online, busy, held）
ANCHORS = [
    ("2026-11-07T00:35:53", 43, 34, 11),
    ("2026-11-07T00:44:03", 53, 40, 16),
    ("2026-11-07T00:46:16", 53, 40, 16),
    ("2026-11-07T00:47:02", 53, 39, 16),
    ("2026-11-07T00:47:47", 53, 37, 16),
    ("2026-11-07T00:48:00", 53, 35, 16),
    ("2026-11-07T00:49:01", 53, 34, 16),
    ("2026-11-07T00:50:03", 52, 32, 16),
    ("2026-11-07T00:52:06", 52, 30, 16),
    ("2026-11-07T00:52:35", 52, 30, 16),
    # ── 断档 147.35 min（无观测）──
    ("2026-11-07T03:20:08", 52, 1, 0),
    ("2026-11-07T03:20:43", 52, 9, 0),
    ("2026-11-07T03:22:43", 53, 8, 0),
    ("2026-11-07T03:24:43", 47, 8, 0),
    ("2026-11-07T03:26:43", 45, 19, 0),
]
GAP_START = ANCHORS[9][0]
GAP_END = ANCHORS[10][0]
FLEET_ENROLLED = 57          # 当日 enabled 席位实测
RECOVERY_PENDING = 19        # campaign_nodes in-flight=19 实测
GAP_BUSY_FLOOR = 8           # 03:22:43/03:24:43 实测低水位
GAP_BUSY_RAMP_S = 600        # 30→8 的重建斜坡时长（10 min）

START = datetime(2026, 11, 7, 0, 35, tzinfo=UTC)
END = datetime(2026, 11, 7, 3, 30, tzinfo=UTC)
RECOVERY_AT = datetime(2026, 11, 7, 3, 19, tzinfo=UTC)
GAP_START_DT = datetime.fromisoformat(GAP_START).replace(tzinfo=UTC)
GAP_END_DT = datetime.fromisoformat(GAP_END).replace(tzinfo=UTC)
ANCHOR_DT = [(datetime.fromisoformat(ts).replace(tzinfo=UTC), online, busy, held)
             for ts, online, busy, held in ANCHORS]


def _f(x: float) -> int:
    return int(round(x))


def online_busy_at(moment: datetime) -> tuple[int, int, str]:
    """返回 (online, busy, 取值性质)。"""
    if GAP_START_DT < moment < GAP_END_DT:
        ramp = min(1.0, (moment - GAP_START_DT).total_seconds() / GAP_BUSY_RAMP_S)
        busy = ANCHORS[9][2] + (GAP_BUSY_FLOOR - ANCHORS[9][2]) * ramp
        return 52, _f(busy), "reconstruction"
    prev = [a for a in ANCHOR_DT if a[0] <= moment]
    nxt = [a for a in ANCHOR_DT if a[0] > moment]
    if not prev:
        return nxt[0][1], nxt[0][2], "anchor_hold"
    if not nxt or prev[-1][0] == GAP_END_DT:
        return prev[-1][1], prev[-1][2], "anchor_hold"
    a, b = prev[-1], nxt[0]
    span = (b[0] - a[0]).total_seconds()
    frac = 0.0 if span <= 0 else (moment - a[0]).total_seconds() / span
    return _f(a[1] + (b[1] - a[1]) * frac), _f(a[2] + (b[2] - a[2]) * frac), "interpolated"


def build() -> dict:
    samples = []
    moment = START
    while moment <= END:
        online, busy, nature = online_busy_at(moment)
        pulse = [a[0] for a in ANCHOR_DT if a[0] <= moment]
        pulse_last = pulse[-1] if pulse else None
        post = moment >= RECOVERY_AT
        samples.append({
            "ts": moment.isoformat(timespec="seconds"),
            "dag_pending": RECOVERY_PENDING if post else 0,
            "plan_pending": 0,
            "queued_missions": 0,
            "workers_enrolled": FLEET_ENROLLED,
            "heartbeat_fresh": FLEET_ENROLLED if not post else max(0, FLEET_ENROLLED - 1),
            "workers_online": online,
            "workers_idle": max(0, online - busy),
            "pulse_last_ts": pulse_last.isoformat(timespec="seconds") if pulse_last else None,
            "source": ("post_recovery" if post else
                       ("gap_reconstruction" if GAP_START_DT < moment < GAP_END_DT else nature)),
            "detail": {"pulse_rows_in_window": 0 if GAP_START_DT < moment < GAP_END_DT else 1},
        })
        moment += timedelta(minutes=1)
    return {
        "schema": "stall_detector/fixture/1",
        "case": "2026-11-07 00:52→03:18 停摆（断粮 + 脉搏表停滞）",
        "provenance": {
            "generator": "tools/stall_fixture_20261107.py",
            "anchors_query": ("select to_char(ts,'YYYY-MM-DD HH24:MI:SS'), online, busy, held "
                              "from saturation_pulse where ts >= '2026-11-07 00:00' order by ts"),
            "anchors": [{"ts": ts, "online": on, "busy": b, "held": h}
                        for ts, on, b, h in ANCHORS],
            "gap": {"start": GAP_START, "end": GAP_END,
                    "seconds": (GAP_END_DT - GAP_START_DT).total_seconds(),
                    "evidence_grade": "WITHHELD（缺口内无任何观测行）"},
            "reconstruction_law": {
                "pulse_last_ts": "≤ 样本时刻的最后一条实测脉搏行（缺口内冻结于 00:52:35）",
                "workers_online": "缺口内恒 52（两侧实测均为 52）",
                "busy": (f"00:52:35 实测 30，{GAP_BUSY_RAMP_S}s 内线性降到 "
                         f"{GAP_BUSY_FLOOR}（03:22:43/03:24:43 实测低水位）后恒定"),
                "workers_idle": "online − busy（与 tools/pulse 口径一致）",
                "dag_pending/queued": "缺口内 0：任务书给定 + 当日 campaign_nodes 可派状态实测 0",
                "fleet_heartbeat": (f"enrolled={FLEET_ENROLLED}（当日 enabled 实测），"
                                    "心率全新鲜 → R2 本案例应沉默（负控）"),
                "recovery": (f"≥{RECOVERY_AT.strftime('%H:%M')}：pending={RECOVERY_PENDING}"
                             "（campaign_nodes in-flight 实测 19）"),
            },
            "evidence_grades": {
                "pulse rows / gap 边界 / online / 高度水位": "REAL",
                "gap 内 busy 与 pending 的逐分钟值": "RECONSTRUCTED（依上律，非实测）",
                "判据依赖": ("定性形状（pending=0 + idle 高 ≥15 min、脉搏停 ≥30 min）"
                             "来自任务书与 gap 边界实测；检测器不得依赖 gap 内具体数值"),
            },
        },
        "thresholds": {},
        "samples": samples,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    payload = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {args.out} ({len(payload['samples'])} samples)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())