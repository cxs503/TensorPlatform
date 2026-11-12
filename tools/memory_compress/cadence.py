"""cadence —— 压缩节奏：定期（interval_days）+ 超阈值（体积/腐化率）双触发。

原则：压缩不是"感觉该压了"，而是**可判定**的：任一触发条件成立即到期，并给出理由。
状态文件（``runs/memory_compress/cadence_state.json``）记录上次执行时间与计数。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..context_package.loader import ROOT

DEFAULT_POLICY = {
    "interval_days": 7,
    "min_interval_hours": 12,
    "thresholds": {"seat_memory_tokens": 1200, "kb_stale_ratio": 0.35,
                   "global_view_tokens": 2400},
    "state_path": "runs/memory_compress/cadence_state.json",
}


def load_policy(path: Path | str | None = None) -> dict:
    p = Path(path) if path else ROOT / "config" / "memory_compress.v1.json"
    try:
        with open(p, encoding="utf-8") as fh:
            pol = json.load(fh)
    except Exception:
        pol = dict(DEFAULT_POLICY)
    merged = {**DEFAULT_POLICY, **pol}
    merged["thresholds"] = {**DEFAULT_POLICY["thresholds"], **(pol.get("thresholds") or {})}
    return merged


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def due_triggers(metrics: Mapping[str, Any], state: Mapping[str, Any] | None = None,
                 policy: Mapping[str, Any] | None = None) -> dict:
    """返回 ``{"due": bool, "triggers": [...], "blocked_by": str|None}``。"""
    pol = policy or load_policy()
    state = state or {}
    now = _now()
    trig: list[dict] = []
    last = state.get("last_run_at")
    if last:
        try:
            dt = now - datetime.fromisoformat(str(last))
            if dt.days >= int(pol["interval_days"]):
                trig.append({"trigger": "PERIODIC", "detail": f"距上次 {dt.days}d ≥ {pol['interval_days']}d"})
            elif dt.total_seconds() < int(pol.get("min_interval_hours", 12)) * 3600:
                return {"due": False, "triggers": [], "blocked_by": "MIN_INTERVAL",
                        "next_check_hint": f"{pol.get('min_interval_hours')}h"}
        except Exception:
            trig.append({"trigger": "PERIODIC", "detail": "state 时间不可解析→按到期处理"})
    else:
        trig.append({"trigger": "BOOTSTRAP", "detail": "无上次执行记录"})

    th = pol["thresholds"]
    seat_tok = metrics.get("seat_memory_tokens")
    if seat_tok is not None and float(seat_tok) > float(th["seat_memory_tokens"]):
        trig.append({"trigger": "SEAT_MEMORY_SIZE", "detail": f"{seat_tok} > {th['seat_memory_tokens']} tok"})
    stale_ratio = metrics.get("kb_stale_ratio")
    if stale_ratio is not None and float(stale_ratio) > float(th["kb_stale_ratio"]):
        trig.append({"trigger": "KB_STALE_RATIO", "detail": f"{stale_ratio:.2f} > {th['kb_stale_ratio']}"})
    gv = metrics.get("global_view_tokens")
    if gv is not None and float(gv) > float(th["global_view_tokens"]):
        trig.append({"trigger": "GLOBAL_VIEW_SIZE", "detail": f"{gv} > {th['global_view_tokens']} tok"})
    return {"due": bool(trig), "triggers": trig, "blocked_by": None}


def update_state(ran: bool = True, layers: list[str] | None = None,
                 path: Path | str | None = None, extra: Mapping[str, Any] | None = None) -> dict:
    pol = load_policy()
    p = Path(path) if path else ROOT / pol["state_path"]
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        state = {"runs": 0}
    if ran:
        state["last_run_at"] = _now().isoformat(timespec="seconds")
        state["runs"] = int(state.get("runs") or 0) + 1
        state["layers"] = layers or ["global", "seat", "kb"]
    if extra:
        state.update(dict(extra))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state
