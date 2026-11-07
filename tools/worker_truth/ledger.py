"""Claim-window divergence ledger — external truth vs the platform's own "busy" definition.

Two definitions of "this worker is busy", both evaluated on the same sampled timeline:

  **truth**  (this mission)      process tree alive **and** CPU burned inside the 60 s window
                                — :func:`tools.worker_truth.states.derive_state`.
  **claim**  (platform status quo) claim held **and** heartbeat fresh
                                — ``claim_active and heartbeat_age_s <= 300 s``.

The platform only ever had the second one, so every disagreement is a bookkeeping error it
cannot see from the inside.  The ledger reports, per worker and fleet-wide:

  * time-weighted agreement rate over both definitions (unit: seconds, not sample counts —
    a 10 s cadence is not a uniform clock when scans slow down);
  * a 2×2 confusion matrix  TP / FP / FN / TN  with the *cost* interpretation of each cell:
      FP = claim inflation  (claim says busy, no CPU burned → silent hang / silent death
                             hidden behind a live claim; the failure mode this node targets)
      FN = off-claim burn   (CPU burned with no claim → work the ledger does not know about)
  * mismatch episodes (contiguous runs, with duration and worker) so an operator can go look;
  * silent-death episodes with the detection delay.

Gap handling: a weight is capped at ``GAP_CAP_FACTOR × median interval`` so a suspended
laptop or a stalled sampler cannot dominate the average.  Nothing is interpolated: gaps stay
gaps, they are just not allowed to outvote the measured interior.
"""
from __future__ import annotations

import gzip
import json
import statistics
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tools.worker_truth import states

CLAIM_BUSY_HB_S = 300.0     # heartbeat freshness inside the claim-window "busy" definition
GAP_CAP_FACTOR = 3.0        # weight cap = 3 × median sample interval


def load_records(path: str) -> List[Dict[str, object]]:
    """Read a sampler JSONL (host + worker records); ``.gz`` is read transparently."""
    out: List[Dict[str, object]] = []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def split_by_worker(records: Iterable[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    groups: Dict[str, List[Dict[str, object]]] = {}
    for r in records:
        if r.get("kind") != "worker":
            continue
        groups.setdefault(str(r["worker_id"]), []).append(r)
    for wid in groups:
        groups[wid].sort(key=lambda r: float(r["ts_epoch"]))
    return groups


def claim_window_busy(rec: Dict[str, object], hb_fresh_s: float = CLAIM_BUSY_HB_S) -> bool:
    """The platform's own definition of an occupied worker (claim + fresh heartbeat)."""
    if not rec.get("claim_active"):
        return False
    age = rec.get("heartbeat_age_s")
    if age is None:
        return True  # claim without a heartbeat yet = just-claimed grace (IN15 lineage)
    return float(age) <= hb_fresh_s


def _weights(series: Sequence[Dict[str, object]]) -> List[float]:
    """Per-sample time weights in seconds, gap-capped at ``GAP_CAP_FACTOR × median``."""
    if not series:
        return []
    ts = [float(r["ts_epoch"]) for r in series]
    diffs = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    med = statistics.median(diffs) if diffs else 1.0
    cap = GAP_CAP_FACTOR * med
    w = [min(b - a, cap) for a, b in zip(ts, ts[1:])]
    w.append(min(med, cap) if diffs else 0.0)  # last sample borrows the median interval
    return w


def _durations(series: Sequence[Dict[str, object]], weights: Sequence[float],
               key: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for rec, w in zip(series, weights):
        k = str(rec.get(key))
        out[k] = out.get(k, 0.0) + w
    return dict(sorted(out.items()))


def episodes(series: Sequence[Dict[str, object]], weights: Sequence[float],
             flag_ok, min_gap_s: float = 0.0) -> List[Dict[str, object]]:
    """Contiguous runs where ``flag_ok(rec)`` is False → episodes with time span."""
    out: List[Dict[str, object]] = []
    cur: Optional[Dict[str, object]] = None
    for rec, w in zip(series, weights):
        bad = not flag_ok(rec)
        if bad:
            if cur is None:
                cur = {"start_ts": rec["ts"], "start_epoch": float(rec["ts_epoch"]),
                       "end_ts": rec["ts"], "seconds": 0.0, "n_samples": 0,
                       "worker_id": rec.get("worker_id"),
                       "truth": rec.get("state"), "claim_busy": claim_window_busy(rec)}
            cur["end_ts"] = rec["ts"]
            cur["seconds"] = round(float(cur["seconds"]) + w, 3)
            cur["n_samples"] = int(cur["n_samples"]) + 1
        else:
            if cur is not None:
                out.append(cur)
                cur = None
    if cur is not None:
        out.append(cur)
    return [e for e in out if float(e["seconds"]) >= min_gap_s]


def worker_ledger(series: Sequence[Dict[str, object]],
                  hb_fresh_s: float = CLAIM_BUSY_HB_S,
                  window_s: float = states.WINDOW_S,
                  eps: float = states.EPS_CPU_S,
                  eps_io: Optional[float] = None) -> Dict[str, object]:
    """Per-worker: state time budget, agreement, confusion matrix, episodes."""
    derived = states.derive_series(series, window_s=window_s, eps=eps, eps_io=eps_io)
    w = _weights(derived)
    total = sum(w)
    tp = fp = fn = tn = 0.0
    for rec, wt in zip(derived, w):
        t_busy = rec["state"] == "busy"
        c_busy = claim_window_busy(rec, hb_fresh_s)
        if t_busy and c_busy:
            tp += wt
        elif c_busy and not t_busy:
            fp += wt
        elif t_busy and not c_busy:
            fn += wt
        else:
            tn += wt
    agree = (tp + tn) / total if total else 0.0
    dead = [r for r in derived if r["state"] == "silent_dead"]
    dead_episode_starts: List[Dict[str, object]] = []
    prev_dead = False
    for rec in derived:
        is_dead = rec["state"] == "silent_dead"
        if is_dead and not prev_dead:
            dead_episode_starts.append({"start_ts": rec["ts"],
                                        "claim_ids": rec.get("claim_ids") or [],
                                        "heartbeat_age_s": rec.get("heartbeat_age_s")})
        prev_dead = is_dead
    return {
        "n_samples": len(derived),
        "observed_s": round(total, 3),
        "state_seconds": {k: round(v, 3) for k, v in _durations(derived, w, "state").items()},
        "claim_busy_seconds": round(sum(ww for r, ww in zip(derived, w)
                                        if claim_window_busy(r, hb_fresh_s)), 3),
        "truth_busy_seconds": round(sum(ww for r, ww in zip(derived, w)
                                        if r["state"] == "busy"), 3),
        "agreement": round(agree, 6),
        "confusion": {"tp": round(tp, 3), "fp": round(fp, 3), "fn": round(fn, 3), "tn": round(tn, 3)},
        "silent_dead_episodes": dead_episode_starts,
        # episodes where the two planes disagree at all (either direction)
        "claim_mismatch_episodes": episodes(
            derived, w, lambda r: (r["state"] == "busy") == claim_window_busy(r, hb_fresh_s)),
        "derived": derived,
    }


def build_ledger(records: Sequence[Dict[str, object]],
                 hb_fresh_s: float = CLAIM_BUSY_HB_S,
                 claims_only: bool = False,
                 window_s: float = states.WINDOW_S,
                 eps: float = states.EPS_CPU_S,
                 eps_io: Optional[float] = None) -> Dict[str, object]:
    """Fleet ledger: global time-weighted agreement + matrix + per-worker distribution."""
    groups = split_by_worker(records)
    per_worker: Dict[str, object] = {}
    agg = {"tp": 0.0, "fp": 0.0, "fn": 0.0, "tn": 0.0}
    state_s: Dict[str, float] = {s: 0.0 for s in states.STATES}
    observed = 0.0
    mismatch: List[Dict[str, object]] = []
    dead_eps: List[Dict[str, object]] = []
    for wid, series in groups.items():
        if claims_only and not any(bool(r.get("claim_active")) for r in series):
            continue
        led = worker_ledger(series, hb_fresh_s, window_s=window_s, eps=eps, eps_io=eps_io)
        for k in agg:
            agg[k] += float(led["confusion"][k])
        for k, v in led["state_seconds"].items():
            state_s[k] = state_s.get(k, 0.0) + float(v)
        observed += float(led["observed_s"])
        for ep in led["claim_mismatch_episodes"]:
            mismatch.append({**ep, "worker_id": wid})
        for ep in led["silent_dead_episodes"]:
            dead_eps.append({**ep, "worker_id": wid})
        per_worker[wid] = {k: v for k, v in led.items() if k != "derived"}
    total = sum(agg.values())
    agree = (agg["tp"] + agg["tn"]) / total if total else 0.0
    agreements = sorted(float(v["agreement"]) for v in per_worker.values())
    observed_h = observed / 3600.0
    duration_s = 0.0
    all_ts = [float(r["ts_epoch"]) for g in groups.values() for r in g]
    if all_ts:
        duration_s = max(all_ts) - min(all_ts)
    return {
        "definition": {
            "truth_busy": "pids>0 and cpu_delta_s>0 over the trailing 60 s window",
            "claim_busy": f"claim_active and heartbeat_age_s<={hb_fresh_s:.0f} s",
            "online": "busy | suspect_hang | idle (a body exists or no claim pending)",
            "dead": "silent_dead (claim held, no process)",
        },
        "n_workers": len(per_worker),
        "n_samples": sum(int(v["n_samples"]) for v in per_worker.values()),
        "wall_s": round(duration_s, 3),
        "observed_worker_seconds": round(observed, 3),
        "observed_worker_hours": round(observed_h, 4),
        "time_weighted_agreement": round(agree, 6),
        "claim_busy_seconds": round(sum(float(v["claim_busy_seconds"])
                                        for v in per_worker.values()), 3),
        "truth_busy_seconds": round(sum(float(v["truth_busy_seconds"])
                                        for v in per_worker.values()), 3),
        "confusion_seconds": {k: round(v, 3) for k, v in agg.items()},
        "state_seconds": {k: round(v, 3) for k, v in state_s.items()},
        "per_worker_agreement": {
            "min": agreements[0] if agreements else 0.0,
            "median": statistics.median(agreements) if agreements else 0.0,
            "max": agreements[-1] if agreements else 0.0,
            "below_90pct": sum(1 for a in agreements if a < 0.9),
        },
        "silent_dead_episodes": dead_eps,
        "claim_mismatch_episodes": sorted(mismatch, key=lambda e: -float(e["seconds"]))[:20],
        "per_worker": per_worker,
    }

def liveness_agreement(records: Sequence[Dict[str, object]],
                       hb_fresh_s: float = CLAIM_BUSY_HB_S) -> Dict[str, object]:
    """A second, coarser question: does the claim predict *presence* rather than *work*?

    truth = ``pids > 0`` (a body exists), claim = claim held.  Splitting "does the claim know
    a process is there" from "does the claim know the process is working" is what turns one
    useless agreement number into a diagnosis.
    """
    groups = split_by_worker(records)
    tp = fp = fn = tn = 0.0
    for series in groups.values():
        w = _weights(series)
        for rec, wt in zip(series, w):
            alive = int(rec.get("pids") or 0) > 0
            claimed = bool(rec.get("claim_active"))
            if alive and claimed:
                tp += wt
            elif claimed and not alive:
                fp += wt
            elif alive and not claimed:
                fn += wt
            else:
                tn += wt
    total = tp + fp + fn + tn
    return {"tp": round(tp, 3), "fp": round(fp, 3), "fn": round(fn, 3), "tn": round(tn, 3),
            "agreement": round((tp + tn) / total, 6) if total else 0.0,
            "seconds": round(total, 3)}


def sensitivity(records: Sequence[Dict[str, object]],
                windows: Sequence[float] = (60.0, 120.0, 300.0),
                eps_list: Sequence[float] = (states.EPS_CPU_S, 1.0, 6.0, 30.0),
                hb_fresh_s: float = CLAIM_BUSY_HB_S) -> List[Dict[str, object]]:
    """Agreement as a function of the *truth* calibration (window length × activity floor).

    ``eps`` is a floor in CPU-seconds per window: an agent worker polls while idle, so
    ``cpu_delta > 0`` labels polling as work.  ``eps=6 s / 60 s window`` means "at least 10 %
    of one core on average" — the calibration used in the report.
    """
    out: List[Dict[str, object]] = []
    for window_s in windows:
        for eps in eps_list:
            fl = build_ledger(records, hb_fresh_s=hb_fresh_s, window_s=window_s, eps=eps)
            out.append({
                "window_s": window_s,
                "eps_cpu_s": eps,
                "core_fraction": round(eps / window_s, 4),
                "agreement": fl["time_weighted_agreement"],
                "confusion_seconds": fl["confusion_seconds"],
                "state_seconds": fl["state_seconds"],
            })
    return out
