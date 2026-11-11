#!/usr/bin/env python3
"""Verification of the **pulse** side of the platform metrics tools.

Two distinct claims are checked, both against synthetic ground truth:

1. *Latency metrology* -- given a fleet whose heartbeat delay distribution is
   known analytically (lognormal, parameters fixed by the simulator), does the
   reported ``mean`` / ``var`` / ``P99`` of the collected heartbeat ages recover
   the injected values inside the campaign ceilings (mean < 1 s, P99 < 5 s)?
   The reference is the closed-form lognormal quantile, not a second copy of the
   estimator, so the check cannot be satisfied by a broken-but-consistent
   implementation.
2. *Freshness core* -- does the real production predicate
   :func:`tools.pulse.saturation_pulse.worker_is_fresh` and the real
   :func:`tools.pulse.saturation_pulse.compute` snapshot agree, agent by agent,
   with the simulator's knowledge of who is alive/busy?  This part exercises the
   shipped module (imported, not re-implemented).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tools.pulse import saturation_pulse as sp

from . import stats
from .sim import AgentStream, FleetSim, simulate_fleet, FleetConfig


class PulseVerifyError(ValueError):
    """Malformed verification request (bad window, empty fleet, ...)."""


# --------------------------------------------------------------------------- #
# 1. latency / loss metrology
# --------------------------------------------------------------------------- #
def summarize_latency(fleet: FleetSim) -> Dict[str, Any]:
    """Measured latency distribution + analytic reference + estimator error."""
    latencies = fleet.delivered_latencies
    if not latencies:
        raise PulseVerifyError("no heartbeat was delivered: latency is undefined")
    summary = stats.summarize(latencies, quantiles=(0.5, 0.9, 0.99))
    reference = {
        "median": stats.lognormal_quantile(fleet.config.median_latency_s, fleet.config.sigma, 0.5),
        "p90": stats.lognormal_quantile(fleet.config.median_latency_s, fleet.config.sigma, 0.9),
        "p99": stats.lognormal_quantile(fleet.config.median_latency_s, fleet.config.sigma, 0.99),
    }
    # gain of the estimator against the analytic truth (chain-of-custody: the
    # injected parameters travel with the numbers).
    estimator_error = {
        "p50_rel": stats.rel_error(summary["p50"], reference["median"]),
        "p90_rel": stats.rel_error(summary["p90"], reference["p90"]),
        "p99_rel": stats.rel_error(summary["p99"], reference["p99"]),
    }
    return {
        "n_agents": fleet.config.n_agents,
        "n_heartbeats_sent": fleet.config.n_agents * fleet.config.n_heartbeats,
        "n_latency_samples": len(latencies),
        "injected": {
            "median_latency_s": fleet.config.median_latency_s,
            "sigma": fleet.config.sigma,
            "stall_rate": fleet.config.stall_rate,
            "loss_rate": fleet.config.loss_rate,
        },
        "measured": summary,
        "analytic_reference": reference,
        "estimator_error": estimator_error,
        "n_stalled": len(fleet.all_stalled),
    }


def evaluate_latency_ceiling(summary: Dict[str, Any],
                             mean_max_s: float = 1.0,
                             p99_max_s: float = 5.0) -> Dict[str, Any]:
    """Apply the campaign ceilings to a :func:`summarize_latency` payload."""
    measured = summary["measured"]
    mean_ok = measured["mean"] is not None and measured["mean"] < mean_max_s
    p99_ok = measured["p99"] is not None and measured["p99"] < p99_max_s
    return {
        "mean_s": measured["mean"],
        "mean_limit_s": mean_max_s,
        "mean_pass": bool(mean_ok),
        "p99_s": measured["p99"],
        "p99_limit_s": p99_max_s,
        "p99_pass": bool(p99_ok),
        "pass": bool(mean_ok and p99_ok),
    }


def detect_heartbeat_loss(fleet: FleetSim) -> Dict[str, Any]:
    """Gap detector over per-agent sequence numbers, scored against injected loss.

    The detector only sees delivered ``seq`` values, exactly like a collector that
    reads heartbeats off a queue.  A drop can be *observed* only once a later
    heartbeat of the same agent arrives; a drop on the trailing edge of a stream
    is therefore undetectable without a reconciliation query.  Both numbers are
    reported -- the strict one (all injected drops in the denominator, i.e. the
    pessimistic reading) is the acceptance number.
    """
    detected: List[Tuple[str, int]] = []
    undetectable_trailing: List[Tuple[str, int]] = []
    false_positives: List[Tuple[str, int]] = []
    gaps: Dict[str, List[Tuple[int, int]]] = {}
    for agent in fleet.agents:
        seqs = sorted(agent.seqs)
        drops = set(agent.dropped_seqs)
        if not seqs:
            continue
        agent_gaps: List[Tuple[int, int]] = []
        for prev, nxt in zip(seqs, seqs[1:]):
            for missing in range(prev + 1, nxt):
                if missing in drops:
                    detected.append((agent.agent_id, missing))
                    agent_gaps.append((prev, nxt))
                else:
                    # a hole that the simulator did *not* create: detector bug
                    false_positives.append((agent.agent_id, missing))
        max_seq = seqs[-1]
        for missing in sorted(d for d in drops if d > max_seq):
            undetectable_trailing.append((agent.agent_id, missing))
        gaps[agent.agent_id] = agent_gaps

    injected = fleet.all_dropped
    n_injected = len(injected)
    return {
        "n_injected": n_injected,
        "n_detected": len(detected),
        "n_undetectable_trailing": len(undetectable_trailing),
        "n_false_positive_gaps": len(false_positives),
        "detection_rate_strict": (len(detected) / n_injected) if n_injected else 1.0,
        "detection_rate_detectable": (
            len(detected) / max(1, n_injected - len(undetectable_trailing))
        ),
        "false_positive_rate": (len(false_positives) / n_injected) if n_injected else 0.0,
        "gap_count": sum(len(v) for v in gaps.values()),
        "undetectable_examples": undetectable_trailing[:5],
    }


# --------------------------------------------------------------------------- #
# 2. freshness / snapshot core (real production code under test)
# --------------------------------------------------------------------------- #
def build_raw_state(fleet: FleetSim, *, now: datetime, mission_heartbeat_age_s: float = 1.0,
                    stale_mission_fraction: float = 0.25) -> Tuple[sp.RawState, Dict[str, Any]]:
    """Translate the simulated fleet into the ``RawState`` the collector would read.

    Returns the state plus the simulator-side truth for ``online`` / ``busy`` /
    ``stale_claims``.  Only agents whose last heartbeat arrived are visible: an
    agent whose every heartbeat was lost is indistinguishable from a dead one and
    must be counted as offline, not as a silent ``busy``.
    """
    workers: List[Dict[str, Any]] = []
    missions: List[Dict[str, Any]] = []
    fresh_truth = set()
    busy_truth = set()
    stale_claims_truth = 0
    running_agents = [a for a in fleet.agents if a.state == "running" and a.last_received]
    n_stale = int(round(len(running_agents) * stale_mission_fraction))
    stale_set = set(a.agent_id for a in running_agents[:n_stale])

    for agent in fleet.agents:
        last = agent.last_received
        workers.append({
            "worker_id": agent.agent_id,
            "enabled": agent.enabled,
            "state": agent.state,
            "current_mission_id": agent.mission_id if last is not None else None,
            "heartbeat_at": last,
        })
        if not agent.enabled or last is None:
            continue
        age = (now - last).total_seconds()
        if age <= sp.WINDOW_SECONDS_DEFAULT:
            fresh_truth.add(agent.agent_id)
        if agent.state == "running" and age <= sp.WINDOW_SECONDS_DEFAULT:
            busy_truth.add(agent.agent_id)

    for agent in running_agents:
        if agent.agent_id in stale_set:
            heartbeat = now.timestamp() - sp.ACTIVE_MISSION_HEARTBEAT_SECONDS - 60
            stale_claims_truth += 1
        else:
            heartbeat = now.timestamp() - mission_heartbeat_age_s
            busy_truth.add(agent.agent_id)
        missions.append({
            "id": agent.mission_id,
            "status": "running",
            "claimed_by": agent.agent_id,
            "heartbeat_at": datetime.fromtimestamp(heartbeat, tz=now.tzinfo),
        })

    raw = sp.RawState(workers=tuple(workers), missions=tuple(missions), nodes=())
    truth = {
        "fresh_workers": len(fresh_truth),
        "online": len(fresh_truth),
        "busy": len(busy_truth),
        "stale_claims": stale_claims_truth,
        "fresh_ids": sorted(fresh_truth),
        "busy_ids": sorted(busy_truth),
    }
    return raw, truth


def crosscheck_pulse_core(fleet: FleetSim, *, now: datetime,
                          window_s: int = sp.WINDOW_SECONDS_DEFAULT) -> Dict[str, Any]:
    """Compare the real snapshot to simulator truth, and the freshness predicate per agent."""
    raw, truth = build_raw_state(fleet, now=now)
    snapshot = sp.compute(raw, now=now, window_s=window_s)

    predicate_mismatches: List[Dict[str, Any]] = []
    skewed_seen = 0
    for agent in fleet.agents:
        if agent.last_received is None:
            continue
        fresh, skewed = sp.worker_is_fresh(agent.last_received, now, window_s)
        skewed_seen += int(skewed)
        age = (now - agent.last_received).total_seconds()
        expected = agent.enabled and age <= window_s
        if fresh != expected:
            predicate_mismatches.append({
                "agent": agent.agent_id, "age_s": round(age, 4),
                "predicate_fresh": fresh, "truth_fresh": expected,
            })

    # truth re-derived under the requested window (build_raw_state uses the default)
    fresh_ids = set()
    for agent in fleet.agents:
        if agent.enabled and agent.last_received is not None:
            if (now - agent.last_received).total_seconds() <= window_s:
                fresh_ids.add(agent.agent_id)
    truth_online = len(fresh_ids)
    truth_busy = len(fresh_ids & set(truth["busy_ids"]))

    return {
        "window_s": window_s,
        "measured": {
            "online": snapshot.online,
            "busy": snapshot.busy,
            "idle": snapshot.idle,
            "fresh_workers": snapshot.fresh_workers,
            "stale_claims": snapshot.stale_claims,
            "skewed_heartbeats": snapshot.skewed_heartbeats,
        },
        "truth": {
            "online": truth_online,
            "busy": truth_busy,
            "idle": truth_online - truth_busy,
            "fresh_workers": len(fresh_ids),
            "stale_claims": truth["stale_claims"],
        },
        "predicate_mismatches": predicate_mismatches,
        "predicate_checks": sum(1 for a in fleet.agents if a.last_received is not None),
        "skewed_seen": skewed_seen,
        "counts_match": (
            snapshot.online == truth_online
            and snapshot.busy == truth_busy
            and snapshot.idle == truth_online - truth_busy
            and snapshot.fresh_workers == len(fresh_ids)
            and snapshot.stale_claims == truth["stale_claims"]
        ),
        "pass": bool(not predicate_mismatches and (
            snapshot.online == truth_online and snapshot.busy == truth_busy
            and snapshot.idle == truth_online - truth_busy
            and snapshot.fresh_workers == len(fresh_ids)
        )),
    }


def run_pulse_verify(cfg: Optional[FleetConfig] = None, *,
                     mean_max_s: float = 1.0, p99_max_s: float = 5.0,
                     loss_detect_min: float = 0.95) -> Dict[str, Any]:
    """Full pulse acceptance bundle (criteria a, b + core crosscheck)."""
    cfg = cfg or FleetConfig()
    fleet = simulate_fleet(cfg)
    latency = summarize_latency(fleet)
    ceiling = evaluate_latency_ceiling(latency, mean_max_s=mean_max_s, p99_max_s=p99_max_s)
    loss = detect_heartbeat_loss(simulate_fleet(
        FleetConfig(**{**cfg.__dict__, "loss_rate": 0.05, "stall_rate": 0.0})))
    clean = detect_heartbeat_loss(fleet)
    now = max(t for t in fleet.last_heartbeats().values())
    core = crosscheck_pulse_core(fleet, now=now)
    return {
        "latency": latency,
        "ceiling": ceiling,
        "loss": {**loss, "threshold_detection": loss_detect_min,
                 "pass": loss["detection_rate_strict"] > loss_detect_min
                 and loss["n_false_positive_gaps"] == 0},
        "loss_false_positive_control": {
            "n_injected": clean["n_injected"],
            "n_false_positive_gaps": clean["n_false_positive_gaps"],
            "pass": clean["n_false_positive_gaps"] == 0,
        },
        "core": core,
        "pass": bool(ceiling["pass"] and core["pass"]),
        "sensitivity_control": _sensitivity_control(cfg, mean_max_s, p99_max_s),
    }


def _sensitivity_control(cfg: FleetConfig, mean_max_s: float, p99_max_s: float) -> Dict[str, Any]:
    """Negative control: stall 8 % of heartbeats and the P99 ceiling *must* trip."""
    stalled_cfg = FleetConfig(**{**cfg.__dict__, "stall_rate": 0.08, "stall_latency_range_s": (6.0, 14.0)})
    stalled = simulate_fleet(stalled_cfg)
    summary = summarize_latency(stalled)
    verdict = evaluate_latency_ceiling(summary, mean_max_s=mean_max_s, p99_max_s=p99_max_s)
    return {
        "injected_stall_rate": 0.08,
        "measured_p99_s": verdict["p99_s"],
        "ceiling_pass": verdict["pass"],
        "detected_violation": not verdict["pass"],
        "pass": not verdict["pass"],
    }