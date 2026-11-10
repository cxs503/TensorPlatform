#!/usr/bin/env python3
"""campaign_health_scenario —— 生成 p250-n04 健康监控验收场景（合成、零领域语义）。

输出 ``tests/fixtures/campaign_health_p250_n04_scenario.json``：
13 个节点 = 3 超时 + 3 空转 + 2 资源泄漏 + 5 正常（负控）+ 6 个下游 DAG 节点（不监控）。

设计要点（每个节点只暴露**一个**判据，避免归因混淆）
------------------------------------------------------
* TO-1/2/3：工作区每心跳都变、progress 递进、资源远低于阈值 → 只会命中超时锚。
  TO-1 planned 60（走 warn 90 / kill 120）、TO-2 planned 30（45/60，含 68% 中载）、
  TO-3 planned 10（15/20，短周期边界）。
* ID-1：从第一拍就静止，第 10 拍（t=4.5min）判空转；ID-2：前 3 拍有动静后静止，
  t=11min 判空转（run 从静止起点重新计数）；ID-3：工作区全程静止但 progress 还在涨，
  进度停涨后第 10 拍（t=15min）才判 → 证明 progress 活口有效（不是 4.5min 就误杀）。
* RL-1：mem 从 50 爬到 68，t=10 起 >=80 并持续 → t=15 预警（M=5）；
  RL-2：disk t=2 起 >=80 → t=7 预警。两节点工作区每 2–3 拍变化 → 不撞空转锚。
* H-1 正常；H-2 算力型（工作区全程不变、progress 每拍 +1、资源 78% 以下）；
  H-3 CPU 92% 只持续 2min(<M=5)；H-4 mem 79.5% 长期贴阈值但不越线；
  H-5 elapsed/planned = 1.35 (<1.5)。五个都必须 0 告警。
* DAG：DS-A1 只依赖 TO-1；DS-A2 依赖 TO-1 + H-1（H-1 健康 → 释放后仍 blocked）；
  DS-A3 依赖 TO-2 + TO-3（两条边都释放才 runnable）；DS-A4 依赖 ID-1；
  DS-A5 依赖 RL-1（RL-1 只预警不终止 → 边不动）；DS-A6 依赖 DS-A1（隔代不释放）。

用法
----
    python3 tools/campaign_health_scenario.py            # 写 fixture
    python3 tools/campaign_health_scenario.py --dry-run  # 只打印期望摘要
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tests/fixtures/campaign_health_p250_n04_scenario.json"
T0 = datetime(2026, 11, 9, 0, 0, 0, tzinfo=timezone.utc)
STALE = T0 - timedelta(hours=3)  # 「工作区最后一次改动」的静止基准


def ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sample(
    minute: float,
    *,
    mtime_minute: float | None = None,
    new_files: int = 0,
    commit: str = "c0",
    progress: int | None = None,
    mem: float = 50.0,
    cpu: float = 40.0,
    disk: float = 50.0,
    static_mtime: float = -180.0,
) -> dict:
    """一个心跳采样。``mtime_minute`` 给定时为活动工作区，否则用静止基准时间。"""
    mtime = ts(mtime_minute) if mtime_minute is not None else ts(static_mtime)
    return {
        "ts": ts(minute),
        "workspace": {"max_mtime": mtime, "new_files": new_files, "commit": commit},
        "progress": progress,
        "mem_pct": mem,
        "cpu_pct": cpu,
        "disk_pct": disk,
    }


def active_series(
    n: int,
    step: float,
    *,
    change_every: int = 1,
    progress_every: int = 1,
    mem=lambda i: 45.0,
    cpu=lambda i: 40.0,
    disk=lambda i: 50.0,
    start: float = 0.0,
) -> list[dict]:
    """有心跳活动的序列：每 ``change_every`` 拍工作区变化一次，progress 每 ``progress_every`` 拍 +1。"""
    out: list[dict] = []
    for i in range(n):
        changed = (i % change_every) == 0
        out.append(
            sample(
                start + i * step,
                mtime_minute=(start + i * step) if changed else None,
                new_files=(i // change_every) + (1 if changed else 0),
                commit=f"c{i // change_every}" if changed else f"c{max(0, (i // change_every))}",
                progress=(i // progress_every) if progress_every else None,
                mem=mem(i),
                cpu=cpu(i),
                disk=disk(i),
            )
        )
    return out


def build() -> dict:
    nodes: list[dict] = []

    # ── A1 超时组 ─────────────────────────────────────────────
    nodes.append({
        "node_id": "TO-1", "name": "synthetic-timeout-plain", "group": "timeout",
        "planned_minutes": 60, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": active_series(27, 5.0),  # t = 0..130 min
        "expect": {"state": "timeout_kill",
                   "kinds": ["node_timeout", "node_timeout_kill"],
                   "detect_offset_min": {"node_timeout": 90.0, "node_timeout_kill": 120.0},
                   "forbidden_kinds": ["node_idle", "resource_pressure"]},
    })
    nodes.append({
        "node_id": "TO-2", "name": "synthetic-timeout-moderate-load", "group": "timeout",
        "planned_minutes": 30, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": active_series(14, 5.0, mem=lambda i: 68.0, cpu=lambda i: 70.0),  # 0..65
        "expect": {"state": "timeout_kill", "kinds": ["node_timeout", "node_timeout_kill"],
                   "detect_offset_min": {"node_timeout": 45.0, "node_timeout_kill": 60.0},
                   "forbidden_kinds": ["node_idle", "resource_pressure"]},
    })
    nodes.append({
        "node_id": "TO-3", "name": "synthetic-timeout-short-budget", "group": "timeout",
        "planned_minutes": 10, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": active_series(23, 1.0),  # 0..22
        "expect": {"state": "timeout_kill", "kinds": ["node_timeout", "node_timeout_kill"],
                   "detect_offset_min": {"node_timeout": 15.0, "node_timeout_kill": 20.0},
                   "forbidden_kinds": ["node_idle", "resource_pressure"]},
    })

    # ── A2 空转组 ─────────────────────────────────────────────
    id1 = [
        sample(i * 0.5, new_files=0, commit="c0", progress=0, mem=55.0)
        for i in range(25)  # t = 0..12 min, 每 30 s 一拍
    ]
    nodes.append({
        "node_id": "ID-1", "name": "synthetic-idle-static", "group": "idle",
        "planned_minutes": 240, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": id1,
        "expect": {"state": "idle_kill", "kinds": ["node_idle"],
                   "detect_offset_min": {"node_idle": 4.5}, "idle_run": 10,
                   "forbidden_kinds": ["node_timeout", "resource_pressure"]},
    })
    id2: list[dict] = []
    for i in range(21):  # t = 0..20 min
        if i < 3:
            id2.append(sample(float(i), mtime_minute=float(i), new_files=i + 1,
                              commit=f"c{i}", progress=i, mem=52.0))
        else:
            id2.append(sample(float(i), new_files=3, commit="c2", progress=2, mem=52.0))
    nodes.append({
        "node_id": "ID-2", "name": "synthetic-idle-after-start", "group": "idle",
        "planned_minutes": 120, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": id2,
        "expect": {"state": "idle_kill", "kinds": ["node_idle"],
                   "detect_offset_min": {"node_idle": 12.0}, "idle_run": 10,
                   "forbidden_kinds": ["node_timeout", "resource_pressure"]},
    })
    id3 = [
        sample(float(i), new_files=0, commit="c0", progress=(i if i <= 6 else 6), mem=58.0)
        for i in range(26)  # t = 0..25 min：进度在 t<=6 递进，之后停涨
    ]
    nodes.append({
        "node_id": "ID-3", "name": "synthetic-idle-progress-then-flat", "group": "idle",
        "planned_minutes": 180, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": id3,
        "expect": {"state": "idle_kill", "kinds": ["node_idle"],
                   "detect_offset_min": {"node_idle": 15.0}, "idle_run": 10,
                   "forbidden_kinds": ["node_timeout", "resource_pressure"]},
    })

    # ── A3 资源泄漏组 ─────────────────────────────────────────
    rl1 = []
    for i in range(25):  # t = 0..24 min
        mem = 50.0 + 2.0 * i if i < 10 else 81.0 + (i % 3)
        rl1.append(sample(float(i), mtime_minute=float(i) if i % 3 == 0 else None,
                          new_files=(i // 3) + 1 if i % 3 == 0 else (i // 3),
                          commit=f"c{i // 3}" if i % 3 == 0 else f"c{max(0, i // 3 - 1)}",
                          progress=i, mem=mem, cpu=60.0, disk=55.0))
    nodes.append({
        "node_id": "RL-1", "name": "synthetic-leak-mem-ramp", "group": "leak",
        "planned_minutes": 200, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": rl1,
        "expect": {"state": "resource_warn", "kinds": ["resource_pressure"],
                   "metric": "mem_pct", "detect_offset_min": {"resource_pressure": 15.0},
                   "forbidden_kinds": ["node_timeout", "node_idle"]},
    })
    rl2 = []
    for i in range(21):  # t = 0..20 min
        disk = 70.0 if i < 2 else 84.0 + (i % 3)
        rl2.append(sample(float(i), mtime_minute=float(i) if i % 2 == 0 else None,
                          new_files=(i // 2) + 1 if i % 2 == 0 else (i // 2),
                          commit=f"d{i // 2}" if i % 2 == 0 else f"d{max(0, i // 2 - 1)}",
                          progress=i, mem=50.0, cpu=45.0, disk=disk))
    nodes.append({
        "node_id": "RL-2", "name": "synthetic-leak-disk-plateau", "group": "leak",
        "planned_minutes": 150, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": rl2,
        "expect": {"state": "resource_warn", "kinds": ["resource_pressure"],
                   "metric": "disk_pct", "detect_offset_min": {"resource_pressure": 7.0},
                   "forbidden_kinds": ["node_timeout", "node_idle"]},
    })

    # ── A4 负控组（5 个正常节点，必须 0 告警） ────────────────
    nodes.append({
        "node_id": "H-1", "name": "synthetic-healthy-normal", "group": "healthy",
        "planned_minutes": 60, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": active_series(51, 1.0),  # 0..50 min, 0.83x
        "expect": {"state": "healthy", "kinds": [], "max_alerts": 0},
    })
    nodes.append({
        "node_id": "H-2", "name": "synthetic-healthy-compute-bound", "group": "healthy",
        "planned_minutes": 120, "started_at_utc": ts(0), "status": "running", "monitored": True,
        # 工作区全程不变（不落盘），但 progress 每拍 +1；资源压在阈值下
        "samples": [
            sample(float(i), new_files=0, commit="c0", progress=i,
                   mem=72.0, cpu=78.0, disk=60.0)
            for i in range(21)
        ],
        "expect": {"state": "healthy", "kinds": [], "max_alerts": 0,
                   "note": "compute-bound: static workspace but progress advances"},
    })
    nodes.append({
        "node_id": "H-3", "name": "synthetic-healthy-cpu-spike", "group": "healthy",
        "planned_minutes": 90, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": active_series(31, 1.0, cpu=lambda i: 92.0 if 10 <= i <= 12 else 30.0),
        "expect": {"state": "healthy", "kinds": [], "max_alerts": 0,
                   "note": "2-min CPU spike < sustain window (5 min)"},
    })
    nodes.append({
        "node_id": "H-4", "name": "synthetic-healthy-near-threshold", "group": "healthy",
        "planned_minutes": 60, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": active_series(41, 1.0, mem=lambda i: 79.5, cpu=lambda i: 60.0,
                                 disk=lambda i: 70.0),
        "expect": {"state": "healthy", "kinds": [], "max_alerts": 0,
                   "note": "79.5% memory stays below 80% threshold"},
    })
    nodes.append({
        "node_id": "H-5", "name": "synthetic-healthy-tight-budget", "group": "healthy",
        "planned_minutes": 20, "started_at_utc": ts(0), "status": "running", "monitored": True,
        "samples": active_series(28, 1.0),  # 0..27 min → 1.35x
        "expect": {"state": "healthy", "kinds": [], "max_alerts": 0,
                   "note": "1.35x budget stays below 1.5x warn factor"},
    })

    # ── DAG 下游（不监控，仅用于 A5 释放判据） ────────────────
    for nid, name, deps in (
        ("DS-A1", "synthetic-downstream-single-dep", ["TO-1"]),
        ("DS-A2", "synthetic-downstream-mixed-dep", ["TO-1", "H-1"]),
        ("DS-A3", "synthetic-downstream-two-killed-deps", ["TO-2", "TO-3"]),
        ("DS-A4", "synthetic-downstream-idle-dep", ["ID-1"]),
        ("DS-A5", "synthetic-downstream-warned-only", ["RL-1"]),
        ("DS-A6", "synthetic-downstream-transitive", ["DS-A1"]),
    ):
        nodes.append({
            "node_id": nid, "name": name, "group": "downstream", "monitored": False,
            "status": "blocked", "dependencies": deps, "samples": [],
        })

    return {
        "scenario_id": "p250-n04-campaign-health-monitor",
        "generator": "tools/campaign_health_scenario.py",
        "time_origin_utc": ts(0),
        "thresholds": {
            "timeout_warn_factor": 1.5,
            "timeout_kill_factor": 2.0,
            "idle_heartbeats": 10,
            "resource_pct": 80.0,
            "resource_sustain_min": 5.0,
        },
        "nodes": nodes,
        "expectations": {
            "A1_timeout": {
                "nodes": ["TO-1", "TO-2", "TO-3"],
                "required_state": "timeout_kill",
                "required_kinds": ["node_timeout", "node_timeout_kill"],
            },
            "A2_idle": {
                "nodes": ["ID-1", "ID-2", "ID-3"],
                "required_state": "idle_kill",
                "required_kinds": ["node_idle"],
                "idle_heartbeats": 10,
            },
            "A3_leak": {
                "nodes": ["RL-1", "RL-2"],
                "required_state": "resource_warn",
                "required_kinds": ["resource_pressure"],
                "metrics": {"RL-1": "mem_pct", "RL-2": "disk_pct"},
            },
            "A4_negative": {
                "nodes": ["H-1", "H-2", "H-3", "H-4", "H-5"],
                "required_state": "healthy",
                "max_alerts": 0,
            },
            "A5_salvage": {
                "released_edges": [
                    ["TO-1", "DS-A1"], ["TO-1", "DS-A2"], ["TO-2", "DS-A3"],
                    ["TO-3", "DS-A3"], ["ID-1", "DS-A4"],
                ],
                "runnable": ["DS-A1", "DS-A3", "DS-A4"],
                "still_blocked": ["DS-A2"],
                "untouched_edges": [
                    ["H-1", "DS-A2"], ["RL-1", "DS-A5"], ["DS-A1", "DS-A6"],
                ],
            },
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="generate p250-n04 health monitor scenario")
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    scenario = build()
    print(json.dumps(scenario["expectations"], indent=2, ensure_ascii=False))
    print(f"nodes={len(scenario['nodes'])} samples={sum(len(n['samples']) for n in scenario['nodes'])}")
    if args.dry_run:
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(scenario, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
