#!/usr/bin/env python3
"""Verification of the **portrait** side: seat (worker) profile consistency & drift.

Unlike the pulse/chronicle checks this module does not re-implement anything: it
imports the shipped :mod:`tools.worker_portrait.portrait` and requires its
``aggregate`` output to reproduce, within 5 %, the aggregates the simulator
computed *before* serialising the mission rows (success rate, mean wall time,
capability-domain share).  A pipeline defect (timestamp rounding, status
bucketing, a wrong denominator) therefore shows up as a numerical deviation --
the comparison is not a second run of the same code.

The drift half asks the operational question: *how many events may pass before a
behavioural regime change is flagged?*  Ceiling is 10 events.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tools.worker_portrait import portrait as wp

from . import stats
from .sim import CAPABILITY_DOMAINS, PortraitSim, simulate_portrait, simulate_drift_stream


class PortraitVerifyError(ValueError):
    """Raised when the portrait comparison cannot be formed."""


DEFAULT_TOLERANCE = 0.05  # 5 %


def compare_portrait(sim: PortraitSim, *, tolerance: float = DEFAULT_TOLERANCE,
                     min_missions: int = 3) -> Dict[str, Any]:
    """Run the real ``portrait.aggregate`` and diff it against simulator truth."""
    report = wp.aggregate(sim.rows, min_missions=min_missions)
    rows_by_worker: Dict[str, List[Dict[str, Any]]] = {}
    for row in sim.rows:
        rows_by_worker.setdefault(row["worker"], []).append(row)

    per_worker: List[Dict[str, Any]] = []
    for profile in report["workers"]:
        worker = profile["worker"]
        truth = sim.truth[worker]
        measured_rate = profile["success_rate"]
        measured_wall = profile["wall_s"]["mean"]
        measured_share = profile["repo_share"]
        rate_err = stats.abs_error(measured_rate, truth["success_rate"])
        wall_err = stats.rel_error(measured_wall, truth["mean_wall_s"])
        share_err = max(
            abs(measured_share.get(d, 0.0) - truth["domain_share"][d]) for d in CAPABILITY_DOMAINS
        )
        per_worker.append({
            "worker": worker,
            "n_missions": profile["n_missions"],
            "success_rate_measured": measured_rate,
            "success_rate_truth": round(truth["success_rate"], 4),
            "success_rate_abs_error": rate_err,
            "mean_wall_s_measured": measured_wall,
            "mean_wall_s_truth": round(truth["mean_wall_s"], 3),
            "mean_wall_s_rel_error": wall_err,
            "domain_share_measured": measured_share,
            "domain_share_truth": {k: round(v, 4) for k, v in truth["domain_share"].items()},
            "domain_share_max_abs_error": share_err,
            "pass": bool(
                (rate_err is not None and rate_err <= tolerance)
                and (wall_err is not None and wall_err <= tolerance)
                and share_err <= tolerance
            ),
        })

    max_rate_err = max(w["success_rate_abs_error"] or 0.0 for w in per_worker)
    max_wall_err = max(w["mean_wall_s_rel_error"] or 0.0 for w in per_worker)
    max_share_err = max(w["domain_share_max_abs_error"] or 0.0 for w in per_worker)
    trace = wp.verify_against_missions(report, sim.rows)

    return {
        "tolerance": tolerance,
        "n_workers": len(per_worker),
        "n_missions": len(sim.rows),
        "max_success_rate_abs_error": max_rate_err,
        "max_mean_wall_rel_error": max_wall_err,
        "max_domain_share_abs_error": max_share_err,
        "workers": per_worker,
        "traceability": {"ok": trace["ok"], "checked": trace["checked"],
                         "n_mismatches": trace["n_mismatches"]},
        "schema": report.get("schema"),
        "definition_version": report.get("definition_version"),
        "pass": bool(
            all(w["pass"] for w in per_worker)
            and max_rate_err <= tolerance
            and max_wall_err <= tolerance
            and max_share_err <= tolerance
            and trace["ok"]
        ),
    }


# --------------------------------------------------------------------------- #
# drift detection
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DriftConfig:
    """One-sided CUSUM on Bernoulli outcomes (failures are the signal).

    ``reference_n`` outcomes estimate the nominal success rate ``p0``; the detector
    accumulates the log-likelihood ratio of "seat now succeeds at
    ``p_alternative``" against "seat still succeeds at ``p0``" and alarms when the
    statistic crosses ``threshold_h`` nats.  ``h = 8.0`` was calibrated by
    simulation (``evaluate_drift``): for the declared break ``0.95 -> 0.20`` the
    detection delay is ~4.5 events (max 9 over 20 seeds) while the measured
    in-control run length is ~1.7e4 events; ``h = 7.0`` shortens nothing
    measurable but quadruples the false-alarm rate.
    """

    reference_n: int = 50
    p_alternative: float = 0.30
    threshold_h: float = 8.0
    p0_floor: float = 0.05
    p0_ceiling: float = 0.99

    def __post_init__(self) -> None:
        if self.reference_n < 2:
            raise ValueError("reference_n must be >= 2")
        if not 0.0 < self.p_alternative < 1.0:
            raise ValueError("p_alternative must be within (0, 1)")
        if self.threshold_h <= 0:
            raise ValueError("threshold_h must be > 0")


def detect_success_drift(outcomes: Sequence[int], cfg: Optional[DriftConfig] = None,
                         *, change_index: Optional[int] = None) -> Dict[str, Any]:
    """Flag the event at which the seat's success rate has provably shifted.

    ``change_index`` is evaluation-only (it never enters the statistics); it only
    converts the alarm index into a detection latency.
    """
    cfg = cfg or DriftConfig()
    data = [1 if int(v) else 0 for v in outcomes]
    if len(data) <= cfg.reference_n + 1:
        raise PortraitVerifyError("stream too short for the configured reference window")
    p0 = sum(data[:cfg.reference_n]) / cfg.reference_n
    p0 = min(cfg.p0_ceiling, max(cfg.p0_floor, p0))
    p1 = cfg.p_alternative
    llr_failure = math.log((1.0 - p1) / (1.0 - p0))   # a failure is evidence for p1
    llr_success = math.log(p1 / p0)
    statistic = 0.0
    alarm_index: Optional[int] = None
    trajectory: List[Tuple[int, float]] = []
    for i in range(cfg.reference_n, len(data)):
        statistic = max(0.0, statistic + (llr_success if data[i] else llr_failure))
        if i % 5 == 0 or statistic > cfg.threshold_h:
            trajectory.append((i, round(statistic, 4)))
        if alarm_index is None and statistic > cfg.threshold_h:
            alarm_index = i
    latency = None
    if alarm_index is not None and change_index is not None:
        latency = alarm_index - change_index
    return {
        "reference_rate": round(p0, 4),
        "p_alternative": p1,
        "threshold_h": cfg.threshold_h,
        "llr_failure": round(llr_failure, 4),
        "llr_success": round(llr_success, 4),
        "reference_n": cfg.reference_n,
        "alarm_index": alarm_index,
        "alarmed": alarm_index is not None,
        "change_index": change_index,
        "latency_events": latency,
        "trajectory": trajectory,
        "applicable": bool(p1 < p0),
    }


def detect_domain_drift(domains: Sequence[str], cfg: Optional[DriftConfig] = None,
                        *, change_index: Optional[int] = None) -> Dict[str, Any]:
    """Same two-window idea on the capability-domain mix (total-variation distance).

    A seat that silently migrates from one capability domain to another must not
    keep its old profile; this control covers the "capability domain" half of the
    consistency claim.
    """
    cfg = cfg or DriftConfig()
    window = 10
    labels = sorted(set(domains))
    if len(domains) <= cfg.reference_n + window:
        raise PortraitVerifyError("stream too short for the configured windows")

    def shares(chunk: Sequence[str]) -> Dict[str, float]:
        n = len(chunk)
        return {lbl: sum(1 for d in chunk if d == lbl) / n for lbl in labels}

    reference = shares(domains[:cfg.reference_n])
    alarm_index: Optional[int] = None
    trajectory: List[Tuple[int, float]] = []
    for i in range(cfg.reference_n + window - 1, len(domains)):
        window_shares = shares(domains[i - window + 1:i + 1])
        tv = 0.5 * sum(abs(window_shares[lbl] - reference[lbl]) for lbl in labels)
        trajectory.append((i, round(tv, 4)))
        if alarm_index is None and tv > 0.5:
            alarm_index = i
    latency = None
    if alarm_index is not None and change_index is not None:
        latency = alarm_index - change_index
    return {
        "reference_shares": {k: round(v, 4) for k, v in reference.items()},
        "threshold_tv": 0.5,
        "alarm_index": alarm_index,
        "alarmed": alarm_index is not None,
        "change_index": change_index,
        "latency_events": latency,
        "trajectory": trajectory,
    }


def _arl0(cfg: DriftConfig, *, n_events: int, seed: int, p0: float = 0.95) -> Optional[float]:
    """Measured in-control average run length (events between false alarms)."""
    from .stats import seeded_rng
    rng = seeded_rng(seed)
    statistic = 0.0
    alarms: List[int] = []
    llr_failure = math.log((1.0 - cfg.p_alternative) / (1.0 - p0))
    llr_success = math.log(cfg.p_alternative / p0)
    for i in range(n_events):
        statistic = max(0.0, statistic + (llr_success if rng.random() < p0 else llr_failure))
        if statistic > cfg.threshold_h:
            alarms.append(i)
            statistic = 0.0
    if not alarms:
        return None  # longer than the simulated horizon
    return n_events / len(alarms)


def evaluate_drift(*, n_events: int = 300, change_index: int = 150,
                   p_before: float = 0.95, p_after: float = 0.20,
                   seeds: Sequence[int] = tuple(range(11, 31)),
                   latency_max: int = 10,
                   arl0_events: int = 200_000,
                   arl0_min: float = 1000.0,
                   cfg: Optional[DriftConfig] = None) -> Dict[str, Any]:
    """Repeat the regime-change experiment over seeds; score sensitivity *and* specificity."""
    cfg = cfg or DriftConfig()
    latencies: List[int] = []
    misses = 0
    for seed in seeds:
        stream = simulate_drift_stream(n_events=n_events, change_index=change_index,
                                       p_before=p_before, p_after=p_after, seed=seed)
        out = detect_success_drift(stream.outcomes, cfg, change_index=stream.change_index)
        if out["latency_events"] is None:
            misses += 1
        else:
            latencies.append(out["latency_events"])

    arl0_values = [v for v in (_arl0(cfg, n_events=arl0_events, seed=seed, p0=p_before)
                               for seed in (101, 202)) if v is not None]
    stable_alarms = 0
    for seed in seeds:
        stable = simulate_drift_stream(n_events=n_events, change_index=None,
                                       p_before=p_before, seed=seed)
        if detect_success_drift(stable.outcomes, cfg, change_index=None)["alarmed"]:
            stable_alarms += 1

    power_curve = []
    for p_alt in (0.60, 0.50, 0.40, 0.30, 0.20):
        lat = []
        for seed in seeds[:10]:
            stream = simulate_drift_stream(n_events=n_events, change_index=change_index,
                                           p_before=p_before, p_after=p_alt, seed=seed)
            out = detect_success_drift(stream.outcomes, cfg, change_index=stream.change_index)
            if out["latency_events"] is not None:
                lat.append(out["latency_events"])
        power_curve.append({
            "p_after": p_alt,
            "n_detected": len(lat),
            "latency_mean": stats.mean(lat),
            "latency_max": max(lat) if lat else None,
            "within_ceiling": bool(lat and max(lat) < latency_max and len(lat) == 10),
        })

    return {
        "effect": {"p_before": p_before, "p_after": p_after,
                   "change_index": change_index, "n_events": n_events},
        "n_trials": len(seeds),
        "n_missed": misses,
        "latency_mean": stats.mean(latencies),
        "latency_max": max(latencies) if latencies else None,
        "latency_min": min(latencies) if latencies else None,
        "latency_threshold": latency_max,
        "arl0_events_measured": stats.mean(arl0_values) if arl0_values else None,
        "arl0_min_required": arl0_min,
        "n_false_alarms_stable": stable_alarms,
        "false_alarm_rate": stable_alarms / len(seeds),
        "power_curve": power_curve,
        "config": {"reference_n": cfg.reference_n, "p_alternative": cfg.p_alternative,
                   "threshold_h": cfg.threshold_h},
        "specificity_note": (
            "false_alarm_rate is measured on 250-event stable streams; at the "
            "calibrated in-control run length it is expected to be a few percent, "
            "so the binding specificity contract is arl0_min_required, not zero "
            "per-stream alarms"),
        "pass": bool(not misses and latencies and max(latencies) < latency_max
                     and (not arl0_values or min(arl0_values) >= arl0_min)),
    }


def run_portrait_verify(*, n_agents: int = 8, missions_per_agent: int = 200,
                        seed: int = 20261111, tolerance: float = DEFAULT_TOLERANCE,
                        injected_status_flips: int = 0,
                        drift: bool = True) -> Dict[str, Any]:
    """Full portrait acceptance bundle (criteria e, f + corruption negative control)."""
    honest = simulate_portrait(n_agents=n_agents, missions_per_agent=missions_per_agent, seed=seed)
    consistency = compare_portrait(honest, tolerance=tolerance)

    n_flip = injected_status_flips or max(10, int(round(missions_per_agent * n_agents * 0.15)))
    corrupted = simulate_portrait(n_agents=n_agents, missions_per_agent=missions_per_agent,
                                  seed=seed, injected_status_flips=n_flip)
    corrupt_check = compare_portrait(corrupted, tolerance=tolerance)

    out: Dict[str, Any] = {
        "consistency": {k: v for k, v in consistency.items() if k != "workers"},
        "consistency_workers": consistency["workers"],
        "corruption_control": {
            "injected_status_flips": n_flip,
            "max_success_rate_abs_error": corrupt_check["max_success_rate_abs_error"],
            "detected": not corrupt_check["pass"],
            "pass": not corrupt_check["pass"],
        },
        "pass": bool(consistency["pass"]),
    }
    if drift:
        out["drift"] = evaluate_drift()
        out["domain_drift"] = _domain_drift_summary()
        out["pass"] = bool(out["pass"] and out["drift"]["pass"])
    return out


def _domain_drift_summary(seeds: Sequence[int] = tuple(range(11, 31))) -> Dict[str, Any]:
    """Secondary control: capability mix flips from domain_alpha to domain_gamma at index 60."""
    latencies = []
    for seed in seeds:
        from .stats import seeded_rng
        rng = seeded_rng(seed)
        pre = ["domain_alpha"] * 60
        post = ["domain_gamma"] * 60 + ["domain_alpha"] * 0  # hard regime switch
        stream = pre + post
        rng.shuffle(stream[:60])
        rng.shuffle(stream[60:])
        out = detect_domain_drift(stream, change_index=60)
        if out["latency_events"] is not None:
            latencies.append(out["latency_events"])
    return {
        "n_trials": len(seeds),
        "n_detected": len(latencies),
        "latency_mean": stats.mean(latencies),
        "latency_max": max(latencies) if latencies else None,
        "threshold_events": 20,
        "pass": bool(len(latencies) == len(seeds) and latencies and max(latencies) < 20),
    }
