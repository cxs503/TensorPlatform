#!/usr/bin/env python3
"""coordinator_shadow v1 —— 验收证据包（IN27-H1）。

把散在各处的证据汇成**一份机器可读的验收 JSON**（判据数字 + 产物路径）：
- ``runs/in27_h1/soak_llm.json`` / ``soak_rule.json`` —— 72h 时钟加速演练（两种判读模式）；
- ``runs/in27_h1/drill.json`` —— 负控（只读门 / 尖峰 / 坏 JSON / 幻觉器官 / 幂等）；
- ``runs/in27_h1/live_beat_1.json`` —— 生产库真实一拍（只读 + 一次账本写）；
- ``runs/in27_h1/acceptance_wallclock.json`` —— 墙钟验收裁决（当前必为 PENDING）。

所有数字**从产物里读**，不允许手抄；``honest_limits`` 逐条写清本次没做到什么。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

RUNS = Path("runs/in27_h1")
OUT = Path("artifacts/coordinator_shadow/acceptance_72h.json")

CRITERIA = {
    "continuous_hours": {"value": 72, "source": "战役表 metadata / 任务书"},
    "claimed_and_executed_min": {"value": 3, "source": "任务书「诊断单被舰队领取执行≥3件」"},
    "false_positive_rate_max": {"value": 0.30, "source": "任务书「成熟化指标=误报率<30%」",
                                "comparison": "strictly_less_than"},
    "human_repairs_max": {"value": 0, "source": "任务书「连续72h 无人工修影子日志」"},
    "coverage_ratio_min": {"value": 0.95, "source": "本模块工程下限（缺拍≤5%）"},
    "interval_seconds": {"value": 1800, "source": "任务书「每30分钟」"},
}


def _load_optional(name: str) -> Mapping[str, Any]:
    path = RUNS / name
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _load(name: str) -> Mapping[str, Any]:
    path = RUNS / name
    return json.loads(path.read_text())


def build() -> dict[str, Any]:
    soak_llm = _load("soak_llm.json")
    soak_rule = _load("soak_rule.json")
    drill = _load("drill.json")
    live = _load("live_beat_1.json")
    wall = _load("acceptance_wallclock.json")

    llm_acc = soak_llm["acceptance_simulated"]
    rule_acc = soak_rule["acceptance_simulated"]
    live_beat = live["beat"]
    digest = live_beat["pulse_digest"]

    return {
        "schema": "coordinator-shadow-acceptance-bundle/1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mission_id": 5305,
        "node": "IN27-H1",
        "module": "tools/coordinator_shadow (v1)",
        "criteria": CRITERIA,
        "verdicts": {
            "wall_clock_72h": {
                "status": wall["status"],
                "reason": wall["reason"],
                "numbers": wall["numbers"],
                "window": wall["window"],
                "path": str(RUNS / "acceptance_wallclock.json"),
                "adjudicate_cmd": (
                    "TP_DSN='dbname=tensorplatform user=postgres host=/var/run/postgresql' "
                    "python3 -m tools.coordinator_shadow.cli acceptance "
                    "--since <first-beat-iso> --json-out runs/in27_h1/acceptance_wallclock.json"),
                "note": "墙钟只能由时间给：常驻运行满 72h 后本命令自动定谳 PASS/FAIL",
            },
            "soak_72h_simulated_llm_judge": {
                "status": llm_acc["status"],
                "reason": llm_acc["reason"],
                "numbers": llm_acc["numbers"],
                "checks": llm_acc["checks"],
                "iron_law": soak_llm["iron_law"],
                "path": str(RUNS / "soak_llm.json"),
            },
            "soak_72h_simulated_rule_judge": {
                "status": rule_acc["status"],
                "numbers": rule_acc["numbers"],
                "iron_law": soak_rule["iron_law"],
                "path": str(RUNS / "soak_rule.json"),
            },
            "negative_controls": {
                "all_pass": drill["checks"]["all_pass"],
                "checks": {k: v for k, v in drill["checks"].items() if isinstance(v, bool)},
                "path": str(RUNS / "drill.json"),
            },
            "live_diagnosis_missions": {
                "count": _load_optional("live_missions.json").get("count", 0),
                "missions": [
                    {k: m.get(k) for k in ("mission_id", "task_key", "status", "claimed_by",
                                           "disposition", "created_at")}
                    for m in (_load_optional("live_missions.json").get("missions") or [])
                ],
                "path": str(RUNS / "live_missions.json"),
                "note": "生产队列里真实存在的影子诊断单（任务书「被舰队领取执行≥3件」的计分口径）",
            },
            "live_readonly_beat": {
                "beat_id": live_beat["beat_id"],
                "ts": live_beat["ts"],
                "mode": live_beat["mode"],
                "judge": live_beat["provenance"],
                "findings": live_beat["findings"],
                "filings": len(live_beat["filings"]),
                "organs": {k: digest.get(k) for k in (
                    "credit_backlog", "adoption_stall_minutes", "adoption_stall_stuck",
                    "declaration_rate", "queued_open", "running", "warn_total", "error_total",
                    "blocked_nodes")},
                "withheld": digest.get("withheld"),
                "chronicle_age_minutes": digest.get("chronicle_age_minutes"),
                "chronicle_facets_present": len(digest.get("facets_present") or []),
                "path": str(RUNS / "live_beat_1.json"),
            },
        },
        "figures": {
            "soak_timeline": "runs/in27_h1/figs/fig_soak_timeline.png",
            "gate_decision": "runs/in27_h1/figs/fig_gate_decision.png",
            "fpr_modes": "runs/in27_h1/figs/fig_fpr_modes.png",
            "live_pulse": "runs/in27_h1/figs/fig_live_pulse.png",
        },
        "honest_limits": [
            "72h 墙钟验收未完成：窗口自 2026-11-07T10:30:47Z 起算，状态 PENDING_WALL_CLOCK（不是 PASS）。",
            "诊断单 1 张（mission 5322）已立并被舰队领取（running / tp-ver-7），「执行完」待墙钟期内累计。",
            "soak 为合成平台状态下的时钟加速演练，只证明时序纪律自洽，不替代墙钟。",
            "「诊断单被舰队领取执行 ≥3 件」在墙钟期内累计；演练里 3 件为模拟舰队处置（simulated=True）。",
            "误报率分母只含已处置单；unclaimed_expired 单列，未计入误报率。",
            "v0 原型进程（coordinator_shadow.py）仍在跑，与新表共存；已做 id 序列冲突重试，退役动作留给运维/H3。",
            "生产库 tenant 角色 foundation 无 coordinator_shadow 表 DDL 权限，迁移需 postgres 角色（已在 systemd 单元里写明）。",
            "探针当前未覆盖 s1–s8 全部字段的重算：chronicle 面按原样引用，不重复计算（避免两套口径打架）。",
        ],
    }


def main() -> int:
    payload = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[written] {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())