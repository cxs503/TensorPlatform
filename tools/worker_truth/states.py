"""Four-state derivation from external (proc/cgroup) samples — pure functions, no I/O.

Alphabet (the truth plane the platform currently lacks):

  ``busy``         a live process tree **and** CPU actually burned inside the window
  ``suspect_hang`` claim held, tree alive, but CPU flat across the window (burn-without-work)
  ``silent_dead``  claim held, **no process at all** (the worker died without telling anyone)
  ``idle``         no claim; nothing (or nothing burning) to attribute

Task-title mapping: ``online = busy | suspect_hang | idle`` (a body exists / no claim is
pending), ``dead = silent_dead``.  ``busy|suspect_hang`` is the "occupied" subset.

Decision table (first match wins — order matters, it encodes the evidence hierarchy):

  1. claim_active and pids == 0                     → silent_dead   (strongest: process gone)
  2. pids > 0 and cpu_delta > 0                     → busy
  3. claim_active and pids > 0 and cpu_delta == 0   → suspect_hang
  4. otherwise                                      → idle

``silent_dead`` is the only state that says "the platform's bookkeeping is lying", so it is
**confirmed** before being reported: ``CONFIRM_SAMPLES`` consecutive raw silent_dead samples
are required (a single-sample ``pids==0`` can be a legitimate re-exec/fork gap, and the
negative control must not fire on the normal exit path).
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

STATES = ("busy", "suspect_hang", "silent_dead", "idle")

WINDOW_S = 60.0          # mission criterion: 60 s CPU window
MIN_SAMPLES = 2          # need a real delta, not a single point
MIN_SPAN_FRACTION = 0.5  # window considered warm once samples span >= 30 s
CONFIRM_SAMPLES = 2      # silent_dead needs k consecutive raw hits
EPS_CPU_S = 1e-6         # below this the tree is "not burning CPU"


def window_stats(samples: Sequence[Dict[str, object]], window_s: float = WINDOW_S,
                 eps: float = EPS_CPU_S) -> Dict[str, object]:
    """CPU/mem/pids summary over the trailing *window_s* of one worker's samples.

    *samples* must be that worker's records in ascending ``ts_epoch`` order, the last one
    being the sample under evaluation.  Returns ``{n, span_s, warm, pids, cpu_delta_s,
    cpu_rate, mem_bytes, rss_bytes}``.
    """
    if not samples:
        return {"n": 0, "span_s": 0.0, "warm": False, "pids": 0, "cpu_delta_s": None,
                "cpu_rate": None, "mem_bytes": None, "rss_bytes": None}
    newest = samples[-1]
    t_end = float(newest["ts_epoch"])
    t_start = t_end - window_s
    inside = [s for s in samples if float(s["ts_epoch"]) >= t_start]
    first, last = inside[0], inside[-1]
    span = float(last["ts_epoch"]) - float(first["ts_epoch"])
    cpu_a, cpu_b = first.get("cpu_s"), last.get("cpu_s")
    delta: Optional[float] = None
    rate: Optional[float] = None
    if cpu_a is not None and cpu_b is not None and span > 0:
        delta = max(0.0, float(cpu_b) - float(cpu_a))
        rate = delta / span
    return {
        "n": len(inside),
        "span_s": span,
        "warm": len(inside) >= MIN_SAMPLES and span >= MIN_SPAN_FRACTION * window_s,
        "pids": int(last.get("pids") or 0),
        "cpu_delta_s": delta,
        "cpu_rate": rate,
        "mem_bytes": last.get("mem_bytes"),
        "rss_bytes": last.get("rss_bytes"),
    }


def derive_state(claim_active: bool, pids: int, cpu_delta_s: Optional[float],
                 warm: bool = True, eps: float = EPS_CPU_S,
                 io_delta: Optional[float] = None,
                 eps_io: Optional[float] = None) -> Dict[str, str]:
    """Map one window of external evidence → ``{"state": <one of STATES>, "reason": <str>}``.

    A cold (short-span) window can still report ``silent_dead`` — process absence needs no
    integration time — but never ``busy``/``suspect_hang``: without a delta we would be
    guessing, so the state degrades to ``idle`` with ``reason="cold_window"``.
    """
    pids = int(pids or 0)
    if claim_active and pids == 0:
        return {"state": "silent_dead", "reason": "claim_held_no_process"}
    if pids > 0 and cpu_delta_s is not None and cpu_delta_s > eps:
        return {"state": "busy", "reason": "cpu_burn_with_tree"}
    if not warm:
        return {"state": "idle", "reason": "cold_window"}
    if (pids > 0 and eps_io is not None and io_delta is not None and io_delta > eps_io):
        # alive and *touching* things (syscalls/bytes) but not burning CPU: blocked on I/O,
        # which for an agent worker is the normal "waiting on the model" state, not a hang.
        return {"state": "busy", "reason": "io_activity_no_cpu"}
    if claim_active and pids > 0:
        return {"state": "suspect_hang", "reason": "claim_held_tree_silent"}
    return {"state": "idle", "reason": "no_claim_no_burn"}


def derive_series(records: Sequence[Dict[str, object]], window_s: float = WINDOW_S,
                  confirm: int = CONFIRM_SAMPLES,
                  eps: float = EPS_CPU_S, eps_io: Optional[float] = None
                  ) -> List[Dict[str, object]]:
    """Derive states for one worker's ordered sample list.

    Adds per record: ``state``, ``state_confirmed`` (silent_dead only after *confirm* hits),
    ``reason``, ``window`` (see :func:`window_stats`), ``first_dead_ts`` (ts of the first raw
    silent_dead hit of the current episode, ``None`` if not in one).
    """
    out: List[Dict[str, object]] = []
    run = 0
    episode_start: Optional[float] = None
    for i, rec in enumerate(records):
        win = window_stats(records[: i + 1], window_s=window_s)
        raw = derive_state(bool(rec.get("claim_active")), win["pids"], win["cpu_delta_s"],
                           warm=bool(win["warm"]), eps=eps,
                           io_delta=rec.get("io_delta_syscalls"), eps_io=eps_io)
        state = raw["state"]
        if state == "silent_dead":
            run += 1
            if episode_start is None:
                episode_start = float(rec["ts_epoch"])
        else:
            run = 0
            episode_start = None
        confirmed = "silent_dead" if (state == "silent_dead" and run >= confirm) else (
            state if state != "silent_dead" else "transition")
        item = dict(rec)
        item["state"] = state
        item["state_confirmed"] = confirmed
        item["reason"] = raw["reason"]
        item["window"] = win
        item["dead_run"] = run
        item["dead_episode_start_ts"] = episode_start
        out.append(item)
    return out


def summarize_states(series: Iterable[Dict[str, object]]) -> Dict[str, float]:
    """Sample-count distribution over states (raw), for a quick sanity table."""
    total = 0
    counts: Dict[str, int] = {s: 0 for s in STATES}
    for r in series:
        s = str(r["state"])
        counts[s] = counts.get(s, 0) + 1
        total += 1
    return {s: (counts[s] / total if total else 0.0) for s in STATES} | {"n": total}