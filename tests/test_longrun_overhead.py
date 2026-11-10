"""Negative control: the forensics layer must be nearly free on the happy path.

Acceptance criterion (node p148w2-n03): a normal job pays <= 2 % wall clock for the
surveillance.  Two independent measurements are used because the first one is
noise-limited on a shared 128-core host:

1. **instrumented cost** -- :attr:`FlightRecorder.overhead_ns` accumulates the real
   in-thread time spent journaling/sampling.  High resolution, immune to external
   interference; a lower bound on the true cost.
2. **ABBA block experiment** -- alternating (uninstrumented, instrumented) and
   (instrumented, uninstrumented) blocks on the same job state, so that position
   bias (cache/TLB warm-up, CPU-frequency drift) cancels.  This bias is real: an
   AB-only design measured a phantom +5 % that flipped sign when the order was
   swapped (see docs/M9082_LONGRUN_SURVIVAL.md), so the ABBA median is the gate.
"""

from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.longrun.forensics import FlightRecorder  # noqa: E402
from tools.longrun.job import SyntheticIterativeJob  # noqa: E402

GRID = 1024
BLOCK_STEPS = 6
ROUNDS = 12
BUDGET_PCT = 2.0


def _pin() -> None:
    try:
        os.sched_setaffinity(0, {min(os.cpu_count() or 1, 8) - 1})
    except (AttributeError, OSError):  # pragma: no cover - platform dependent
        pass


def _block(job, step, n, recorder=None):
    c0 = time.process_time()
    for _ in range(n):
        job.step(step)
        if recorder is not None:
            recorder.note_step(step, 0.0)
            recorder.sample_memory()
        step += 1
    return time.process_time() - c0, step


def test_forensics_overhead_within_budget():
    _pin()
    job = SyntheticIterativeJob(grid=GRID)
    rec = FlightRecorder(tempfile.mkdtemp(), job_id="overhead")
    step = 0
    per_round = []
    for r in range(ROUNDS):
        order = (False, True) if r % 2 == 0 else (True, False)
        times = {}
        for instrumented in order:
            dt, step = _block(job, step, BLOCK_STEPS, rec if instrumented else None)
            times[instrumented] = dt
        per_round.append((times[True] - times[False]) / times[False] * 100.0)
    layer_block_total = sum(
        1 for _ in per_round
    ) * BLOCK_STEPS  # bookkeeping for the instrumented denominator
    rec.close()

    median = statistics.median(per_round)
    assert median <= BUDGET_PCT, (
        f"median block overhead {median:.2f}% > {BUDGET_PCT}% "
        f"(per-round {[round(v, 2) for v in per_round]})"
    )

    # instrumented in-thread cost, independent of scheduler noise
    layer_seconds = sum(times.values()) / 2.0 * len(per_round)
    instrumented_pct = rec.overhead_ns / 1e9 / max(layer_seconds, 1e-9) * 100.0
    assert instrumented_pct <= BUDGET_PCT, (
        f"instrumented cost {instrumented_pct:.3f}% of {layer_seconds:.3f}s"
    )
    assert layer_block_total > 0


def test_recorder_disabled_costs_nothing():
    rec = FlightRecorder(tempfile.mkdtemp(), job_id="off", enabled=False)
    job = SyntheticIterativeJob(grid=32)
    t0 = time.perf_counter()
    for i in range(50):
        job.step(i)
        rec.note_step(i, 0.0)
        rec.sample_memory()
    elapsed = time.perf_counter() - t0
    rec.close()
    assert rec.overhead_ns == 0
    assert elapsed < 5.0
    assert not (Path(rec.outdir) / "flight.log").exists()


def test_journal_is_throttled_but_last_step_kept():
    rec = FlightRecorder(tempfile.mkdtemp(), job_id="throttle", journal_interval=60.0)
    for i in range(100):
        rec.note_step(i, 0.0)
    rec.close()
    lines = [ln for ln in (Path(rec.outdir) / "journal.jsonl").read_text().splitlines() if ln]
    assert len(lines) <= 2, "journal must not be written once per step"
    assert rec.last_step == 99
    meta = (Path(rec.outdir) / "meta.json").read_text()
    assert '"last_step": 99' in meta