#!/usr/bin/env python3
"""Alert-rule engine for the observability minimal surface — pure functions, no I/O.

Why this file exists
--------------------
The platform can already *record* mission state, but it cannot answer the two questions a
night watch actually asks:

  (1) which running missions have gone quiet (>2 h without a heartbeat) — i.e. the slot is
      burning budget with nobody home; and
  (2) which campaign nodes have failed so often that re-dispatching them is wasted motion
      (failed x3 -> blocked) and which nodes are *intentionally* parked waiting for a gate
      release (待放门)?

This module turns a snapshot of ``tensor_dev_missions`` / ``campaign_nodes`` /
``tensor_approval_requests`` into an explicit alert set **plus the negative controls** —
every mission that was *examined and deliberately not alerted* is reported with the rule id
that suppressed it.  A detector that cannot show its negative examples cannot be audited:
"no alert" must be distinguishable from "not looked at", and "long but alive" must be
distinguishable from "long and stalled".

Rules (thresholds live in ``THRESHOLDS``; nothing is hard-coded at the call site)
---------------
R1_STUCK_RUNNING    status == running  AND  heartbeat age >= 120 min          -> P1 blocker
R2_BLOCKED_THRICE   node.status == blocked AND consecutive_fails >= 3         -> P1 ledger
R3_PENDING_GATE     node parked on a gate release / approval request pending  -> P2 watch

Suppressions (negative controls; each records why nothing fired)
---------------
S1_JUST_CLAIMED     running, no heartbeat yet, claim age <= 15 min   (刚领取不告警)
S2_LONG_BUT_ALIVE   running age >= 120 min but heartbeat fresh       (长跑但活着不告警)
S3_SUBTHRESHOLD     blocked node with consecutive_fails < 3          (未到 x3 不进处置账)

Determinism: every function takes ``as_of`` explicitly; no wall-clock reads inside the rules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

# ── thresholds (single source of truth; digest prints them verbatim) ───────────
THRESHOLDS: Dict[str, Any] = {
    "stuck_minutes": 120,        # 停滞判据：heartbeat 断 >2h
    "claim_grace_minutes": 15,   # 刚领取宽限：领取后 15min 内尚未心跳不算停滞
    "long_run_minutes": 120,     # "长跑"参考线（与停滞同一时钟，但有心跳即安全）
    "block_fails": 3,            # failed ×3 -> blocked 处置账
    "watch_fails": 1,            # ×1..×2 -> 观察账（不进 P1）
}

RULE_LABEL = {
    "R1_STUCK_RUNNING": "运行中停滞（heartbeat 断流）",
    "R2_BLOCKED_THRICE": "失败×3 阻塞（应进处置账）",
    "R3_PENDING_GATE": "待放门（人为/机械闸口未放行）",
}
SUPPRESS_LABEL = {
    "S1_JUST_CLAIMED": "刚领取·宽限内不告警",
    "S2_LONG_BUT_ALIVE": "长跑但心跳新鲜·不告警",
    "S3_SUBTHRESHOLD": "阻塞但未达×3·仅观察",
}

# ── fleet (三军) mapping: the three tenants the platform serves ───────────────
SOLVER_REPOS = (
    "TensorLBM", "TensorFEM", "TensorFVM", "TensorDEM", "TensorBEM",
    "TensorSPH", "TensorDG", "TensorCAD",
)
FLEET_LABEL = {
    "solver": "求解器军（TensorLBM/FEM/FVM/DEM/BEM/SPH/DG/CAD）",
    "ship": "船舶军（ship-performance-platform）",
    "platform": "平台军（TensorPlatform/TensorPhysics/TensorPapers/其他）",
}
FLEET_ORDER = ("solver", "ship", "platform")


def fleet_of(repo: Optional[str]) -> str:
    """Map a repo name onto one of the three fleets."""
    r = (repo or "").strip()
    if r in SOLVER_REPOS:
        return "solver"
    if r == "ship-performance-platform":
        return "ship"
    return "platform"


# ── time helpers ──────────────────────────────────────────────────────────────
def parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (psql json output) or datetime into aware UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def minutes_between(later: datetime, earlier: Optional[datetime]) -> Optional[float]:
    if earlier is None:
        return None
    return round((later - earlier).total_seconds() / 60.0, 1)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


# ── per-mission state classification (R1 + S1/S2) ─────────────────────────────
# ── time-travel projection (replay of a past instant) ─────────────────────────
def project_at(snapshot: Dict[str, Any], as_of: datetime) -> Dict[str, Any]:
    """Rebuild "what was alive at ``as_of``" from the *current* rows — no history table needed.

    The DB only stores the *current* ``status``, so a past instant cannot be re-read directly.
    A mission is promoted to "running at as_of" only when that is provable from timestamps:
      * current status == running, or
      * current status is terminal *and* finished_at > as_of (it ended after the instant asked).
    Anything else (suspended / stopped / queued / revoked without finished_at) keeps its stored
    status and is therefore **not** alerted — conservative: a stop that left no timestamp is a
    missed detection, never a false alarm.
    ``heartbeat_at`` is a last-heartbeat watermark, therefore monotone: if it lies *after*
    ``as_of`` the worker was demonstrably alive at ``as_of`` and we clamp it (``heartbeat_clamped``
    = True).  Terminals that ended before ``as_of`` are dropped.

    Limitations (documented, not hidden): a re-queued mission (same id re-claimed) and any
    status edit that left no timestamp trace are not reconstructible; the projection is exact
    for the monotone fields it uses and is always reported as ``projection`` in the alert payload.
    """
    missions: List[Dict[str, Any]] = []
    dropped = 0
    clamped = 0
    for m in snapshot.get("missions") or []:
        created = parse_ts(m.get("created_at"))
        if created is None or created > as_of:
            dropped += 1
            continue
        finished = parse_ts(m.get("finished_at"))
        if finished is not None and finished <= as_of:
            dropped += 1
            continue
        row = dict(m)
        status = str(m.get("status") or "")
        row["status_current"] = status
        alive_at_as_of = status == "running" or (
            status in ("succeeded", "failed", "revoked") and finished is not None and finished > as_of)
        row["status"] = "running" if alive_at_as_of else status
        hb = parse_ts(m.get("heartbeat_at"))
        if hb is not None and hb > as_of:
            row["heartbeat_at"] = iso(as_of)
            row["heartbeat_clamped"] = True
            clamped += 1
        missions.append(row)
    out = dict(snapshot)
    out["missions"] = missions
    out["projection"] = {"as_of": iso(as_of), "dropped": dropped, "heartbeat_clamped": clamped,
                         "rule": "created_at <= as_of < coalesce(finished_at, now)"}
    return out


def classify_running(missions: Sequence[Dict[str, Any]], as_of: datetime,
                     thresholds: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Classify every mission that is *not* terminal.

    States
    ------
    stuck         heartbeat silent >= stuck_minutes                    -> R1 alert
    just_claimed  never heartbeated and claim age <= claim_grace        -> S1 suppression
    long_alive    running >= long_run_minutes but heartbeat fresh       -> S2 suppression
    active        running/queued, everything fresh and young            -> no rule involved
    other         any other non-terminal status (suspended/stopped/...) -> informational
    """
    th = thresholds or THRESHOLDS
    terminal = {"succeeded", "failed", "revoked", "cancelled", "canceled"}
    out: List[Dict[str, Any]] = []
    for m in missions:
        status = str(m.get("status") or "")
        if status in terminal:
            continue
        created = parse_ts(m.get("created_at"))
        heartbeat = parse_ts(m.get("heartbeat_at"))
        finished = parse_ts(m.get("finished_at"))
        if finished is not None and finished <= as_of:  # already over at as_of
            continue
        claim_age = minutes_between(as_of, created) if created else None
        hb_age = minutes_between(as_of, heartbeat) if heartbeat else claim_age
        age = claim_age
        row = {
            "id": m.get("id"),
            "repo": m.get("repo"),
            "fleet": fleet_of(m.get("repo")),
            "status": status,
            "task_key": m.get("task_key"),
            "claimed_by": m.get("claimed_by"),
            "model": m.get("model"),
            "created_at": iso(created),
            "heartbeat_at": iso(heartbeat),
            "claim_age_min": claim_age,
            "heartbeat_age_min": hb_age,
            "heartbeat_present": heartbeat is not None,
        }
        if status != "running":
            row["state"] = "other"
            out.append(row)
            continue
        if (heartbeat is None and claim_age is not None
                and claim_age <= th["claim_grace_minutes"]):
            row["state"] = "just_claimed"
            row["suppress"] = "S1_JUST_CLAIMED"
        elif hb_age is not None and hb_age >= th["stuck_minutes"]:
            row["state"] = "stuck"
            row["rule"] = "R1_STUCK_RUNNING"
        elif age is not None and age >= th["long_run_minutes"]:
            row["state"] = "long_alive"
            row["suppress"] = "S2_LONG_BUT_ALIVE"
        else:
            row["state"] = "active"
        out.append(row)
    out.sort(key=lambda r: (r["state"] != "stuck", -(r.get("heartbeat_age_min") or 0.0)))
    return out


# ── node ledgers (R2 + R3 + S3) ───────────────────────────────────────────────
GATE_HINTS = ("待放", "放门", "验收门", "核销门", "手动放", "release", "gate")


def _looks_like_gate(node: Dict[str, Any]) -> bool:
    text = " ".join(str(node.get(k) or "") for k in ("block_reason", "name", "node_type"))
    low = text.lower()
    return any(h.lower() in low for h in GATE_HINTS)


def _node_fleet(node: Dict[str, Any], repo_by_mission: Dict[Any, Any],
                plans: Dict[Any, Dict[str, Any]]) -> str:
    """Attribute a campaign node to a fleet.

    Order: repo of the node's linked mission -> repo named in the plan title -> platform.
    Documented because it is an *inference*, not a stored field (no schema surgery allowed).
    """
    repo = repo_by_mission.get(node.get("mission_id"))
    if repo:
        return fleet_of(repo)
    plan = plans.get(node.get("plan_id")) or {}
    text = " ".join(str(x or "") for x in (plan.get("title"), plan.get("name"),
                                           node.get("name"), node.get("node_type")))
    for solver in SOLVER_REPOS:
        if solver in text:
            return "solver"
    if "ship" in text.lower():
        return "ship"
    return "platform"


def classify_nodes(nodes: Sequence[Dict[str, Any]],
                   thresholds: Optional[Dict[str, Any]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Split nodes into the three ledgers: fail-blocked (P1), watch (S3), gates (R3)."""
    th = thresholds or THRESHOLDS
    ledgers: Dict[str, List[Dict[str, Any]]] = {"blocked": [], "watch": [], "gates": []}
    for n in nodes:
        status = str(n.get("status") or "")
        fails = int(n.get("consecutive_fails") or 0)
        row = {
            "id": n.get("id"),
            "plan_id": n.get("plan_id"),
            "name": (str(n.get("name") or "")).strip(),
            "status": status,
            "consecutive_fails": fails,
            "mission_id": n.get("mission_id"),
            "block_reason": (str(n.get("block_reason") or "")).strip(),
        }
        if status != "blocked":
            continue
        if _looks_like_gate(n):
            row["rule"] = "R3_PENDING_GATE"
            ledgers["gates"].append(row)
        elif fails >= th["block_fails"]:
            row["rule"] = "R2_BLOCKED_THRICE"
            ledgers["blocked"].append(row)
        else:
            row["suppress"] = "S3_SUBTHRESHOLD"
            ledgers["watch"].append(row)
    ledgers["blocked"].sort(key=lambda r: (-r["consecutive_fails"], r["id"] or 0))
    ledgers["gates"].sort(key=lambda r: r["id"] or 0)
    ledgers["watch"].sort(key=lambda r: (r["id"] or 0))
    return ledgers


# ── top-level evaluation ──────────────────────────────────────────────────────
def evaluate(snapshot: Dict[str, Any], as_of: Optional[datetime] = None,
             thresholds: Optional[Dict[str, Any]] = None,
             window_hours: int = 24) -> Dict[str, Any]:
    """Evaluate all rules over a snapshot; returns alerts + suppressions + fleets + stats.

    The snapshot is the *only* input, so a committed snapshot JSON reproduces the digest
    byte-for-byte (audit chain requirement).
    """
    th = dict(thresholds or THRESHOLDS)
    as_of = as_of or parse_ts(snapshot.get("generated_at")) or datetime.now(timezone.utc)
    missions = list(snapshot.get("missions") or [])
    nodes = list(snapshot.get("nodes") or [])
    approvals = list(snapshot.get("approvals") or [])
    plans = {p.get("id"): p for p in (snapshot.get("plans") or [])}

    states = classify_running(missions, as_of, th)
    ledgers = classify_nodes(nodes, th)

    alerts: List[Dict[str, Any]] = []
    suppressions: List[Dict[str, Any]] = []

    for row in states:
        if row["state"] == "stuck":
            alerts.append({
                "rule": "R1_STUCK_RUNNING",
                "severity": "P1",
                "subject": f"mission:{row['id']}",
                "evidence": {
                    "repo": row["repo"], "fleet": row["fleet"],
                    "heartbeat_age_min": row["heartbeat_age_min"],
                    "claim_age_min": row["claim_age_min"],
                    "last_heartbeat": row["heartbeat_at"],
                    "claimed_by": row["claimed_by"], "model": row["model"],
                    "threshold_min": th["stuck_minutes"],
                },
                "action": "先判活（worker 进程/heartbeat 通道），再 stop 释放槽位；勿直接判失败",
            })
        elif row.get("suppress"):
            suppressions.append({
                "code": row["suppress"],
                "subject": f"mission:{row['id']}",
                "reason": SUPPRESS_LABEL[row["suppress"]],
                "observed": {
                    "heartbeat_age_min": row["heartbeat_age_min"],
                    "claim_age_min": row["claim_age_min"],
                    "heartbeat_present": row["heartbeat_present"],
                },
            })

    for row in ledgers["blocked"]:
        alerts.append({
            "rule": "R2_BLOCKED_THRICE",
            "severity": "P1",
            "subject": f"node:{row['id']}",
            "evidence": {
                "plan_id": row["plan_id"], "name": row["name"],
                "consecutive_fails": row["consecutive_fails"],
                "block_reason": row["block_reason"], "mission_id": row["mission_id"],
                "threshold_fails": th["block_fails"],
            },
            "action": "进处置账：换打法/换模型/拆小目标后再放行，禁止原样重派",
        })

    for row in ledgers["gates"]:
        alerts.append({
            "rule": "R3_PENDING_GATE",
            "severity": "P2",
            "subject": f"node:{row['id']}",
            "evidence": {
                "plan_id": row["plan_id"], "name": row["name"],
                "block_reason": row["block_reason"],
            },
            "action": "确认放门条件是否已满足；满足则放行，否则保持 blocked（非故障）",
        })

    for row in ledgers["watch"]:
        suppressions.append({
            "code": "S3_SUBTHRESHOLD",
            "subject": f"node:{row['id']}",
            "reason": SUPPRESS_LABEL["S3_SUBTHRESHOLD"],
            "observed": {"consecutive_fails": row["consecutive_fails"],
                         "block_reason": row["block_reason"]},
        })

    pending_approvals = []
    for a in approvals:
        if str(a.get("status") or "") == "pending":
            pending_approvals.append({
                "id": a.get("id"), "kind": a.get("kind"),
                "mission_id": a.get("mission_id"),
                "from_stage": a.get("from_stage"), "to_stage": a.get("to_stage"),
                "reason": (str(a.get("reason") or ""))[:120],
                "created_at": a.get("created_at"),
            })

    # ── fleet rollup (三军状态) ──
    window_start = as_of - timedelta(hours=window_hours)
    fleets = {key: {"fleet": key, "label": FLEET_LABEL[key], "running": 0, "queued": 0,
                    "stuck": 0, "just_claimed": 0, "long_alive": 0,
                    "failed_window": 0, "succeeded_window": 0,
                    "blocked_nodes": 0, "gate_nodes": 0}
              for key in FLEET_ORDER}
    for row in states:
        f = fleets[row["fleet"]]
        if row["status"] == "running":
            f["running"] += 1
        elif row["status"] == "queued":
            f["queued"] += 1
        f[row["state"]] = f.get(row["state"], 0) + 1
    for m in missions:
        created = parse_ts(m.get("created_at"))
        finished = parse_ts(m.get("finished_at")) or created
        if finished is None or finished < window_start:
            continue
        f = fleets[fleet_of(m.get("repo"))]
        status = str(m.get("status") or "")
        if status == "failed":
            f["failed_window"] += 1
        elif status == "succeeded":
            f["succeeded_window"] += 1
    repo_by_mission = {m.get("id"): m.get("repo") for m in missions}
    for bucket, key in (("blocked", "blocked_nodes"), ("gates", "gate_nodes")):
        for row in ledgers[bucket]:
            f = fleets[_node_fleet(row, repo_by_mission, plans)]
            f[key] += 1

    alerts.sort(key=lambda a: (a["severity"], a["rule"], str(a["subject"])))
    counts = {
        "missions_total": len(missions),
        "non_terminal_examined": len(states),
        "running": sum(1 for r in states if r["status"] == "running"),
        "stuck": sum(1 for r in states if r["state"] == "stuck"),
        "just_claimed": sum(1 for r in states if r["state"] == "just_claimed"),
        "long_alive": sum(1 for r in states if r["state"] == "long_alive"),
        "active": sum(1 for r in states if r["state"] == "active"),
        "nodes_total": len(nodes),
        "blocked_nodes_total": sum(1 for n in nodes if str(n.get("status")) == "blocked"),
        "fail_blocked_ge3": len(ledgers["blocked"]),
        "gate_nodes": len(ledgers["gates"]),
        "watch_nodes": len(ledgers["watch"]),
        "pending_approvals": len(pending_approvals),
        "alerts_total": len(alerts),
        "alerts_p1": sum(1 for a in alerts if a["severity"] == "P1"),
        "alerts_p2": sum(1 for a in alerts if a["severity"] == "P2"),
        "suppressions_total": len(suppressions),
    }
    return {
        "schema_version": 1,
        "as_of": iso(as_of),
        "projection": snapshot.get("projection"),
        "window_hours": window_hours,
        "thresholds": th,
        "rule_labels": RULE_LABEL,
        "suppress_labels": SUPPRESS_LABEL,
        "alerts": alerts,
        "suppressions": suppressions,
        "ledgers": ledgers,
        "states": states,
        "pending_approvals": pending_approvals,
        "fleets": [fleets[k] for k in FLEET_ORDER],
        "counts": counts,
    }


def worst_offenders(evaluation: Dict[str, Any], n: int = 10) -> List[Dict[str, Any]]:
    """The n running missions with the oldest heartbeat (used by the digest + figure)."""
    running = [r for r in evaluation["states"] if r["status"] == "running"]
    running.sort(key=lambda r: -(r.get("heartbeat_age_min") or 0.0))
    return running[:n]