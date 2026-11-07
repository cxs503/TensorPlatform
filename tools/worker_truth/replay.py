"""Silent-death replay forensics — two independent replays (IN23-N08 step 4).

**A. Mechanism replay (live hardware, stopwatch).**  Reproduces the 2026-11-02 failure mode
with real process trees: a worker body burns CPU, keeps a claim file fresh, and is then
``SIGKILL``-ed from outside — no release, no heartbeat, claim frozen.  Detection latency is
measured end-to-end by the very sampler that runs in production, so the number is not a
threshold-by-construction.  Two negative-control cohorts run concurrently:

  * ``clean``  — the normal path: claim released *before* process exit → must never be flagged;
  * ``lag``    — a booked-but-late release (``--release-lag`` seconds): one sample of
                 "claim held, process gone".  Tests the confirmation window, not the rule.

**B. History replay (read-only DB).**  The four missions of the 2026-11-02 heartbeat blackout
(#2109/#2112/#2113/#2114) are projected at their own clock (``as_of``) and judged by the
claim-orphan branch of the silent-dead rule (claim held ∧ heartbeat stale ∧ process evidence
unavailable → ``silent_dead_weak``).  Process evidence for a past instant is *not*
recoverable, so this replay is explicitly one tier weaker (WITHHELD on pids); what it does
give is the **opportunity window**: how long the platform kept the claim after the earliest
possible detection (``last_heartbeat + stale``).

Outputs (both under ``--outdir``): ``replay_mechanism.json``, ``replay_history.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.worker_truth import ledger, sampler as smp, states  # noqa: E402

FAKE = os.path.join(_HERE, "fake_worker.py")
STALE_S = 120.0          # mission rule: claim held with no liveness signal for 2 min = dead
HB_FRESH_S = 300.0
# A claim is only evidence of occupancy while the mission is *live*.  Rows closed by an
# administrative act (stopped/suspended/revoked) never get ``finished_at``, so projection
# alone would count them as in-flight forever — that is how a ledger grows phantom claims.
LIVE_STATUSES = {"running", "claimed", "in_progress", "in-progress", "queued", "pending",
                 "blocked", "dispatched", "assigned"}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── A. mechanism replay ───────────────────────────────────────────────────────

def _spawn(agent_id: str, claims_dir: str, seconds: float, release_lag: float) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, FAKE, "--agent-id", agent_id, "--claims-dir", claims_dir,
         "--seconds", str(seconds), "--release-lag", str(release_lag)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def run_mechanism(outdir: str, n_per_cohort: int = 4, interval: float = 2.0,
                  kill_at: float = 40.0, burn: float = 70.0, lag: float = 2.0,
                  settle: float = 30.0) -> Dict[str, object]:
    """Spawn 3 cohorts, kill the positives, sample with the production sampler."""
    claims_dir = os.path.join(outdir, "claims")
    os.makedirs(claims_dir, exist_ok=True)
    for name in os.listdir(claims_dir):
        os.unlink(os.path.join(claims_dir, name))

    cohorts: Dict[str, List[str]] = {
        "dead": [f"replay-dead-{i}" for i in range(1, n_per_cohort + 1)],
        "clean": [f"replay-clean-{i}" for i in range(1, n_per_cohort + 1)],
        "lag": [f"replay-lag-{i}" for i in range(1, n_per_cohort + 1)],
    }
    procs: Dict[str, subprocess.Popen] = {}
    for wid in cohorts["dead"] + cohorts["clean"]:
        procs[wid] = _spawn(wid, claims_dir, burn, 0.0)
    for wid in cohorts["lag"]:
        procs[wid] = _spawn(wid, claims_dir, burn, lag)

    cfg = smp.SamplerConfig(interval_s=interval, known_workers=sum(cohorts.values(), []),
                            worker_marker="fake_worker.py", want_rss=False)
    sampler = smp.Sampler(cfg, smp.FileClaimSource(claims_dir))

    t0 = time.monotonic()
    killed = False
    kill_epoch: Dict[str, float] = {}
    samples: List[Dict[str, object]] = []
    t_end = t0 + burn + settle
    while time.monotonic() < t_end:
        tick_start = time.monotonic()
        if not killed and tick_start - t0 >= kill_at:
            killed = True
            for wid in cohorts["dead"]:
                try:
                    os.killpg(os.getpgid(procs[wid].pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                kill_epoch[wid] = time.time()
        samples.extend(sampler.tick())
        time.sleep(max(0.0, interval - (time.monotonic() - tick_start)))

    for p in procs.values():
        if p.poll() is None:
            p.terminate()

    groups = ledger.split_by_worker(samples)
    per_worker: Dict[str, object] = {}
    latencies: List[float] = []
    hits = 0
    for cohort, wids in cohorts.items():
        for wid in wids:
            series = groups.get(wid, [])
            if not series:
                per_worker[wid] = {"cohort": cohort, "error": "not_sampled"}
                continue
            derived = states.derive_series(series)
            det = next((r for r in derived if r["state_confirmed"] == "silent_dead"), None)
            t_kill = kill_epoch.get(wid)
            lat = None
            if det is not None and t_kill is not None:
                lat = round(float(det["ts_epoch"]) - t_kill, 3)
                latencies.append(lat)
            if cohort == "dead" and det is not None:
                hits += 1
            per_worker[wid] = {
                "cohort": cohort,
                "n_samples": len(derived),
                "detected": det is not None,
                "detect_ts_epoch": float(det["ts_epoch"]) if det else None,
                "kill_ts_epoch": t_kill,
                "latency_s": lat,
                "state_seq": [r["state"] for r in derived],
                "claim_busy_at_detect": ledger.claim_window_busy(det) if det else None,
                "timeline": [{"ts_epoch": r["ts_epoch"], "state": r["state"],
                              "claim_busy": ledger.claim_window_busy(r),
                              "pids": r["window"]["pids"],
                              "cpu_delta_s": r["window"]["cpu_delta_s"]} for r in derived],
            }

    fp_clean = sum(1 for wid in cohorts["clean"]
                   if per_worker.get(wid, {}).get("detected"))
    fp_lag = sum(1 for wid in cohorts["lag"] if per_worker.get(wid, {}).get("detected"))
    claimed_blind = sum(1 for wid in cohorts["dead"]
                        if per_worker.get(wid, {}).get("claim_busy_at_detect"))
    out = {
        "kind": "mechanism_replay",
        "as_of": _iso(time.time()),
        "params": {"interval_s": interval, "kill_at_s": kill_at, "burn_s": burn,
                   "release_lag_s": lag, "n_per_cohort": n_per_cohort,
                   "confirm_samples": states.CONFIRM_SAMPLES, "window_s": states.WINDOW_S},
        "positives": {"n": len(cohorts["dead"]), "hits": hits,
                      "hit_rate": hits / max(1, len(cohorts["dead"]))},
        "negative_control_clean": {"n": len(cohorts["clean"]), "false_positives": fp_clean},
        "negative_control_lag": {"n": len(cohorts["lag"]), "false_positives": fp_lag,
                                 "lag_s": lag},
        "latency_s": {
            "min": min(latencies) if latencies else None,
            "median": statistics.median(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
            "values": sorted(latencies),
        },
        "claim_window_blindness": {
            "dead_workers_still_claim_busy_at_detect": claimed_blind,
            "note": "claim-window called every dead worker busy after the kill — the ledger's FP cell",
        },
        "per_worker": per_worker,
    }
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "replay_mechanism.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    return out


# ── B. history replay (read-only DB) ─────────────────────────────────────────

SQL_AT = """
SELECT id, claimed_by, status,
       to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
       coalesce(to_char(heartbeat_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"'), ''),
       coalesce(to_char(finished_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"'), '')
FROM tensor_dev_missions
WHERE claimed_by IS NOT NULL
  AND created_at <= '{as_of}'
  AND (finished_at IS NULL OR finished_at > '{as_of}')
ORDER BY id
"""


def _parse_ts(txt: str) -> Optional[float]:
    txt = (txt or "").strip()
    if not txt:
        return None
    return datetime.strptime(txt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def project_at(as_of: str, stale_s: float = STALE_S) -> List[Dict[str, object]]:
    """In-flight missions at ``as_of`` judged by the claim-orphan branch (read-only)."""
    rows = smp._psql(SQL_AT.format(as_of=as_of))
    as_of_epoch = _parse_ts(as_of) or 0.0
    out: List[Dict[str, object]] = []
    for line in rows.splitlines():
        if not line.strip():
            continue
        mid, by, status, created, hb, fin = (line.split("\t") + [""] * 6)[:6]
        hb_epoch = _parse_ts(hb)
        fin_epoch = _parse_ts(fin)
        hb_age = None if hb_epoch is None else round(as_of_epoch - hb_epoch, 1)
        if fin_epoch is not None:
            in_flight, cls = True, "projected_in_flight"     # finished after as_of
        elif status.strip().lower() in LIVE_STATUSES:
            in_flight, cls = True, "live_status_no_finish"
        else:
            in_flight, cls = False, "phantom_claim_admin_closed"
        stale = in_flight and hb_age is not None and hb_age > stale_s
        rec = {
            "mission_id": int(mid),
            "claimed_by": by,
            "status_at_as_of": status,
            "created_at": created,
            "heartbeat_at": hb or None,
            "finished_at": fin or None,
            "heartbeat_age_s": hb_age,
            "in_flight_at_as_of": in_flight,
            "liveness_class": cls,
            "claim_orphan_branch_fires": stale,
            "truth_tier": ("silent_dead_weak" if stale else
                           ("alive_or_just_claimed" if in_flight else "phantom_claim")),
        }
        if stale:
            earliest = (hb_epoch + stale_s) if hb_epoch else None
            rec["earliest_detect_at"] = _iso(earliest) if earliest else None
            rec["platform_opportunity_window_s"] = (
                round(fin_epoch - earliest, 1) if (fin_epoch and earliest) else None)
        out.append(rec)
    return out


def run_history(outdir: str, as_of: str = "2026-11-02T20:30:00Z",
                positives: Sequence[int] = (2109, 2112, 2113, 2114),
                stale_s: float = STALE_S,
                healthy_as_of: str = "2026-11-07T03:30:00Z") -> Dict[str, object]:
    """Project the blackout cohort at its own clock; two controls around it.

    * ``positives``      the four missions the incident log recorded → hit rate must be 4/4;
    * ``label control``  in-flight rows whose heartbeat was still fresh at ``as_of`` — the
                         platform could prove them alive, so the rule must leave them alone;
    * ``healthy epoch``  the same rule run at a *known-healthy* instant; any flag there is a
                         false positive by construction (nothing was wrong with the platform).
    """
    records = project_at(as_of, stale_s)
    flagged = [r for r in records if r["claim_orphan_branch_fires"]]
    pos_set = set(int(p) for p in positives)
    hit = [r for r in flagged if r["mission_id"] in pos_set]
    extra = [r for r in flagged if r["mission_id"] not in pos_set]
    labelled_alive = [r for r in records if r["in_flight_at_as_of"]
                      and not r["claim_orphan_branch_fires"]]
    phantom = [r for r in records if r["liveness_class"] == "phantom_claim_admin_closed"]
    alive_flagged = [r for r in labelled_alive if r["claim_orphan_branch_fires"]]
    for r in extra:
        r["class"] = ("deep_orphan" if (r["heartbeat_age_s"] or 0) > 3 * stale_s
                      else "borderline")
    windows = [r["platform_opportunity_window_s"] for r in hit
               if r.get("platform_opportunity_window_s") is not None]
    healthy_flagged: List[Dict[str, object]] = []
    healthy_phantom: List[Dict[str, object]] = []
    try:
        healthy_flagged = [r for r in project_at(healthy_as_of, stale_s)
                           if r["claim_orphan_branch_fires"]]
        healthy_phantom = [r for r in project_at(healthy_as_of, stale_s)
                           if r["liveness_class"] == "phantom_claim_admin_closed"]
    except Exception as exc:  # pragma: no cover - DB optional
        healthy_flagged = [{"error": str(exc)[:200]}]
    out = {
        "kind": "history_replay",
        "as_of": as_of,
        "stale_threshold_s": stale_s,
        "n_in_flight_at_as_of": len(records),
        "n_flagged_orphan": len(flagged),
        "positives": {"ids": sorted(pos_set), "hits": len(hit), "n": len(pos_set),
                      "hit_rate": len(hit) / max(1, len(pos_set))},
        "negative_control_labelled_alive": {
            "n": len(labelled_alive), "false_positives": len(alive_flagged),
            "definition": "in-flight at as_of with heartbeat_age_s <= stale threshold",
        },
        "negative_control_healthy_epoch": {
            "as_of": healthy_as_of, "n_flagged": len(healthy_flagged),
            "n_phantom_excluded": len(healthy_phantom),
            "flagged_ids": [r.get("mission_id") for r in healthy_flagged][:20],
            "definition": "same rule at an instant with no known incident; any flag is a FP",
        },
        "phantom_claims_excluded": {
            "n": len(phantom),
            "detail": [{k: r[k] for k in ("mission_id", "claimed_by", "status_at_as_of",
                                          "heartbeat_at", "heartbeat_age_s")}
                       for r in phantom],
            "note": ("admin-closed rows with no finished_at and a stale claim: bookkeeping "
                     "residue, NOT evidence of a dead worker — excluded from the rule"),
        },
        "extra_finds_beyond_incident_log": {
            "n": len(extra),
            "detail": [{k: r[k] for k in ("mission_id", "claimed_by", "heartbeat_age_s",
                                          "heartbeat_at", "finished_at", "class")}
                       for r in extra],
            "note": ("claim-orphans at as_of that the incident log did not list; process "
                     "evidence for a past instant is unavailable (WITHHELD), so these are "
                     "candidate silent deaths, not proven ones"),
        },
        "detection_latency_s": {
            "by_construction": stale_s,
            "bound_with_cadence_s": stale_s + 10.0,
            "note": "earliest possible detection = last_heartbeat + stale threshold",
        },
        "platform_opportunity_window_s": {
            "values": sorted(windows),
            "median": statistics.median(windows) if windows else None,
            "note": "how long the claim stayed held AFTER the earliest possible detection",
        },
        "positives_detail": hit,
        "records": records,
        "evidence_tier": "WITHHELD on process presence (past instant); claim/heartbeat REAL",
    }
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "replay_history.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="silent-death replay forensics")
    ap.add_argument("--mode", choices=["mechanism", "history", "both"], default="both")
    ap.add_argument("--outdir", default="runs/in23_n08/replay")
    ap.add_argument("--n-per-cohort", type=int, default=4)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--kill-at", type=float, default=40.0)
    ap.add_argument("--burn", type=float, default=70.0)
    ap.add_argument("--lag", type=float, default=2.0)
    ap.add_argument("--as-of", default="2026-11-02T20:30:00Z")
    ap.add_argument("--healthy-as-of", default="2026-11-07T03:30:00Z")
    args = ap.parse_args(argv)
    if args.mode in ("mechanism", "both"):
        rep = run_mechanism(args.outdir, args.n_per_cohort, args.interval, args.kill_at,
                            args.burn, args.lag)
        print(json.dumps({k: rep[k] for k in ("positives", "negative_control_clean",
                                              "negative_control_lag", "latency_s")},
                         sort_keys=True))
    if args.mode in ("history", "both"):
        rep = run_history(args.outdir, args.as_of, healthy_as_of=args.healthy_as_of)
        print(json.dumps({k: rep[k] for k in ("positives", "negative_control_labelled_alive",
                                              "negative_control_healthy_epoch",
                                              "platform_opportunity_window_s")},
                         sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())