"""Resume bit-consistency, honest-FAIL retention and memory-constrained degradation.

These are the three properties that decide whether a long campaign can be trusted
after an incident:

* a resumed run must reproduce the uninterrupted run **bit for bit** (A2/A9) and a
  normal run must not be perturbed by the survival layer at all (A7);
* a failed run must keep its artifacts, enforced in code, not by convention (A6);
* under a memory cap the run must degrade itself and stay within 5 % of the
  full-precision reference (A10).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools.longrun import supervise as supervise_mod
from tools.longrun.job import SyntheticIterativeJob
from tools.progress_meter.strategies import Strategy, StrategyPlan
from tools.survival import degrade
from tools.survival.honest import HonestFail
from tools.survival.workload import TaskConfig, run_task

STEPS = 20
JOB = {"job": {"grid": 24, "precision": "float64", "dt": 0.25, "seed": 7},
       "steps": STEPS, "checkpoint_every": 5,
       "forensics": {"stack_interval_s": 0.5}}


def _bare_digest() -> str:
    job = SyntheticIterativeJob(grid=24, precision="float64", dt=0.25, seed=7)
    for i in range(1, STEPS + 1):
        job.step(i)
    return job.result_digest()


@pytest.fixture(scope="module")
def interrupted_run(tmp_path_factory) -> dict:
    """One interrupted-at-midpoint workdir, shared by the resume tests."""
    root = tmp_path_factory.mktemp("resume")
    full = supervise_mod.supervise(dict(JOB), workdir=root / "full", job_id="full")
    crash = supervise_mod.supervise({**JOB, "crash": {"at_step": 11, "mode": "segv"}},
                                    workdir=root / "crash", job_id="crash")
    resumed = supervise_mod.supervise({**JOB, "job_id": "resumed"},
                                      workdir=root / "crash", job_id="resumed")
    return {"root": root, "full": full, "crash": crash, "resumed": resumed}


def test_crash_then_resume_is_bit_identical(interrupted_run: dict) -> None:
    """A2: crash after the 50 % checkpoint, resume, compare sha256 of the final state."""
    full = interrupted_run["full"]
    resumed = interrupted_run["resumed"]
    assert interrupted_run["crash"]["failure_mode"] == "native_segfault"
    # crash fired at step 11 -> the newest durable checkpoint is step 10 = 50 % of the plan
    assert resumed["child"]["resumed_from_step"] == 10
    assert resumed["child"]["result_digest"] == full["child"]["result_digest"]
    assert len(full["child"]["result_digest"]) == 64


def test_resume_matches_bare_job_negative_control(interrupted_run: dict) -> None:
    """A7: the survival layer is transparent -- digest equals the bare stepped job."""
    assert interrupted_run["full"]["child"]["result_digest"] == _bare_digest()


def test_three_resumes_from_one_checkpoint_agree(interrupted_run: dict) -> None:
    """A9: idempotency -- same checkpoint, three resumes, one digest and one key."""
    digests, keys = set(), set()
    for rep in range(3):
        rep_report = supervise_mod.supervise({**JOB, "job_id": f"idem{rep}"},
                                             workdir=interrupted_run["root"] / "crash",
                                             job_id=f"idem{rep}")
        digests.add(rep_report["child"]["result_digest"])
        keys.add(rep_report["child"]["idempotency_key"])
    assert len(digests) == 1
    assert len(keys) == 1
    assert digests.pop() == interrupted_run["full"]["child"]["result_digest"]


# --------------------------------------------------------------------------- #
# honest FAIL
# --------------------------------------------------------------------------- #
def test_honest_fail_refuses_to_claim_without_artifacts(tmp_path: Path) -> None:
    """A6 (mechanism): the retention floor is enforced, not documented."""
    art = tmp_path / "artifacts"
    art.mkdir()
    hf = HonestFail(artifact_dir=art, planned_total=4)
    for name in ("a.json", "b.json"):
        (art / name).write_text("{}")
        hf.register(name)
    assert hf.ratios()["retention_vs_produced"] == pytest.approx(1.0)
    (art / "a.json").unlink()
    (art / "b.json").unlink()
    with pytest.raises(RuntimeError):
        hf.write(reason="nothing left")
    assert not (tmp_path / "FAIL.json").exists()


def test_honest_fail_record_keeps_partial_work(tmp_path: Path) -> None:
    """A6 (end to end): strategy dies at 40 % budget -> FAIL record + artifacts on disk."""
    run = run_task(
        tmp_path / "run",
        TaskConfig(budget_total=200, grid=8, planned_artifacts=40),
        plan=StrategyPlan([Strategy("early", 0.1, active_until_budget=40), Strategy("dead", 0.0)]),
        stall_frac=0.10,
    )
    assert run["status"] == "FAIL"
    fail = run["fail"]
    assert fail["status"] == "FAIL" and fail["reason"]
    assert fail["ratios"]["retention_vs_produced"] >= 0.5
    on_disk = [rec for rec in fail["partial_artifacts"]
               if (Path(run["artifact_dir"]) / rec["name"]).exists()]
    assert len(on_disk) == fail["ratios"]["retained"] >= 1
    assert (tmp_path / "run" / "FAIL.json").exists()
    assert (tmp_path / "run" / "FAIL.md").exists()
    early = [a for a in run["artifacts"] if "early" in a]
    assert len(early) >= 3, "partial work before the strategy died must survive"


def test_honest_fail_requires_reason(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    hf = HonestFail(artifact_dir=art, planned_total=1)
    with pytest.raises(ValueError):
        hf.write()


# --------------------------------------------------------------------------- #
# memory-constrained degradation
# --------------------------------------------------------------------------- #
def test_decide_pins_single_thread_when_state_cannot_fit() -> None:
    """A10 (policy): the decision is a pure function of measured numbers."""
    tight = degrade.decide(mem_cap_bytes=64 << 20, rss_bytes=32 << 20, n=2048)
    assert tight["degraded"] is True and tight["threads"] == 1
    assert tight["chunk_rows"] and tight["chunk_rows"] < 2048
    roomy = degrade.decide(mem_cap_bytes=8 << 30, rss_bytes=32 << 20, n=64)
    assert roomy["degraded"] is False and roomy["chunk_rows"] is None


def test_capped_alloc_refuses_oversize_allocations() -> None:
    alloc = degrade.capped_alloc(cap_bytes=8 << 20, rss_bytes=4 << 20)
    with pytest.raises(MemoryError):
        alloc.empty((4, 1024, 1024))  # 32 MB under a 4 MB remaining budget
    arr = alloc.empty((16, 16))
    assert arr.shape == (16, 16)


def test_chunked_single_thread_result_within_5pct() -> None:
    """A10 (numerics): row-streamed single-thread equals the full-grid reference."""
    full = degrade.run_kernel(n=192, iters=6, chunk=None, threads=1)
    chunked = degrade.run_kernel(n=192, iters=6, chunk=24, threads=1)
    err = degrade.rel_error(chunked, full)
    assert err <= 0.05, err
    # and the stencil is per-point exact, so only the reduction order differs
    assert err < 1e-12


def test_degraded_worker_runs_under_cap_and_reports_decision(tmp_path: Path) -> None:
    """A10 (end to end): the child process decides, streams, and returns a field."""
    result = degrade.run_degraded(n=768, iters=4, mem_cap_bytes=48 << 20,
                                  outdir=tmp_path, threads=1)
    decision = result["decision"]
    assert decision["threads"] == 1
    assert decision["degraded"] is True
    assert result["full_grid_probe"]["full_grid_admissible"] is False
    field = np.load(result["field_path"])
    assert field.shape == (768, 768)
    assert result["thread_env"]["OPENBLAS_NUM_THREADS"] == "1"
    json.dumps(result)  # report must be serialisable for the evidence trail