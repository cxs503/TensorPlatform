"""Resource degradation ladder regression suite (mission m9082).

The ladder must be *ordered*, *irreversible*, *throttled* and must always leave a
loadable final checkpoint behind when it decides to exit gracefully.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.longrun import checkpoint as ckpt  # noqa: E402
from tools.longrun import ladder as ladder_mod  # noqa: E402
from tools.longrun import supervise  # noqa: E402
from tools.longrun.job import SyntheticIterativeJob  # noqa: E402


def test_rungs_apply_in_declared_order():
    ladder = ladder_mod.ResourceLadder(
        probe=ladder_mod.ScriptedProbe([0.10, 0.81, 0.89, 0.95, 0.99]),
        min_interval_s=0.0,
    )
    applied = []
    for _ in range(5):
        event = ladder.poll(force=True)
        if event:
            applied.extend(event["rungs_applied"])
    assert applied == ["reduce_precision", "reduce_grid", "reduce_timestep", "graceful_exit"]
    assert ladder.exhausted is True


def test_no_degradation_below_thresholds():
    ladder = ladder_mod.ResourceLadder(
        probe=ladder_mod.ScriptedProbe([0.10, 0.50, 0.79]), min_interval_s=0.0
    )
    for _ in range(3):
        assert ladder.poll(force=True) is None
    assert ladder.applied() == []


def test_multiple_thresholds_crossed_at_once_still_apply_in_order():
    ladder = ladder_mod.ResourceLadder(
        probe=ladder_mod.ScriptedProbe([0.99]), min_interval_s=0.0
    )
    event = ladder.poll(force=True)
    assert event["rungs_applied"] == [
        "reduce_precision",
        "reduce_grid",
        "reduce_timestep",
        "graceful_exit",
    ]


def test_probe_is_throttled():
    probe = ladder_mod.ScriptedProbe([0.1] * 100)
    ladder = ladder_mod.ResourceLadder(probe=probe, min_interval_s=60.0)
    for _ in range(50):
        assert ladder.poll() is None
    assert probe.i == 1, "only the first poll inside the throttle window may sample"


def test_unknown_utilization_is_treated_as_no_pressure():
    ladder = ladder_mod.ResourceLadder(probe=lambda: {"utilization": None}, min_interval_s=0.0)
    assert ladder.poll(force=True) is None


def test_job_degrades_state_measurably():
    job = SyntheticIterativeJob(grid=32)
    nbytes0 = job.u.nbytes + job.c.nbytes
    r1 = job.apply_degradation("reduce_precision")
    assert r1["after"]["precision"] == "float32"
    assert (job.u.nbytes + job.c.nbytes) * 2 == nbytes0
    job.apply_degradation("reduce_grid")
    assert job.grid == 16
    nbytes1 = job.u.nbytes + job.c.nbytes
    job.apply_degradation("reduce_timestep")
    assert job.dt == pytest.approx(0.5)
    assert nbytes1 < nbytes0
    assert [d["rung"] for d in job.degradations] == [
        "reduce_precision",
        "reduce_grid",
        "reduce_timestep",
    ]


def test_graceful_exit_leaves_loadable_checkpoint(tmp_path):
    cfg = {
        "job": {"grid": 32},
        "steps": 200,
        "checkpoint_every": 10,
        "ladder": {
            "enabled": True,
            "min_interval_s": 0.0,
            "scripted_utilization": [0.5, 0.82, 0.86, 0.9, 0.95, 0.99],
        },
        "forensics": {"stack_interval_s": 0.5},
    }
    rep = supervise.supervise(cfg, workdir=tmp_path / "ladder", job_id="ladder")
    assert rep["failure_mode"] == "graceful_degraded_exit"
    assert rep["exit_code"] == ladder_mod.GRACEFUL_EXIT_CODE
    assert rep["child"]["ladder"]["applied"] == [
        "reduce_precision",
        "reduce_grid",
        "reduce_timestep",
        "graceful_exit",
    ]
    kinds = [d["rung"] for d in rep["child"]["degradations"]]
    assert kinds[-1] == "graceful_exit"
    store = ckpt.CheckpointStore(tmp_path / "ladder" / "work" / "checkpoints")
    ck = store.load()
    assert ck.scalars["grid"] < 32 and ck.scalars["precision"] == "float32"
    assert rep["bundle_completeness"]["completeness"] == 1.0
    audit = (tmp_path / "ladder" / "work" / "resume_audit.jsonl").read_text().splitlines()
    assert any(json.loads(line)["event"] == "graceful_exit" for line in audit)


def test_ladder_records_pressure_provenance(tmp_path):
    ladder = ladder_mod.ResourceLadder(
        probe=ladder_mod.ScriptedProbe([0.1, 0.85]), min_interval_s=0.0
    )
    ladder.poll(force=True)
    event = ladder.poll(force=True)
    assert event["source"] == "scripted"
    report = ladder.report()
    assert report["rungs"][0]["applied"] is True
    assert report["history"][0]["utilization"] == pytest.approx(0.85)