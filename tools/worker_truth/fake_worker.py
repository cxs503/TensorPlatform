"""Replay worker body — a real process tree with a real claim file (IN23-N08 forensics).

Used *only* by ``tools/worker_truth/replay.py`` to reproduce the 2026-11-02 failure mode
(claim held, process gone) on live hardware, so the silent-death detector can be measured
with a stopwatch instead of an argument.

Semantics, chosen to mirror the platform worker:

  * it writes/refreshes a claim file ``<claims-dir>/<agent-id>.json`` with
    ``claim_active: true`` and ``heartbeat_age_s ≈ 0`` — i.e. the bookkeeping side keeps
    claiming "busy" while the body is alive;
  * the *clean* path releases the claim **before** exiting (``claim_active: false``);
  * the *silent death* path is ``SIGKILL`` from outside: no release, no heartbeat, claim
    frozen — exactly what a wedged/OOM-killed or zombie worker leaves behind;
  * ``--release-lag S`` simulates a booked-but-late platform release (negative control for
    the detector's confirmation window).

It burns a controlled duty cycle (default 20 %) so "busy" is unambiguous without loading the
host: the truth plane only needs ``cpu_delta > 0``.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

DUTY = 0.2


def _write_claim(claims_dir: str, agent_id: str, active: bool, hb_age: float) -> None:
    os.makedirs(claims_dir, exist_ok=True)
    path = os.path.join(claims_dir, f"{agent_id}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({
            "claim_active": active,
            "claim_ids": [900000 + (abs(hash(agent_id)) % 1000)],
            "heartbeat_age_s": hb_age,
            "db_state": "running" if active else "idle",
            "source": "replay",
        }, fh)
    os.replace(tmp, path)


def _burn(seconds: float) -> None:
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        t0 = time.monotonic()
        while time.monotonic() - t0 < DUTY * 0.05:
            pass
        time.sleep(0.05 * (1.0 - DUTY))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent-id", required=True)
    ap.add_argument("--claims-dir", required=True)
    ap.add_argument("--seconds", type=float, default=70.0)
    ap.add_argument("--release-lag", type=float, default=0.0)
    args = ap.parse_args(argv)

    stop = {"v": False}

    def _on_term(signum, frame):  # graceful only if someone asks nicely
        stop["v"] = True

    signal.signal(signal.SIGTERM, _on_term)

    _write_claim(args.claims_dir, args.agent_id, True, 0.0)
    t_end = time.monotonic() + args.seconds
    last_hb = 0.0
    while time.monotonic() < t_end and not stop["v"]:
        _burn(0.2)
        if time.monotonic() - last_hb >= 1.0:  # heartbeat refresh, like the real worker
            _write_claim(args.claims_dir, args.agent_id, True, 0.0)
            last_hb = time.monotonic()
    if args.release_lag > 0:
        time.sleep(args.release_lag)  # books lag behind the body (negative control)
    _write_claim(args.claims_dir, args.agent_id, False, 9999.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())