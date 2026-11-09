"""C5 test: maturity comparison against the frozen plan-165 baseline.

Guards the honesty contract: the *replay* comparison (same durations,
dependencies enforced) must stay negative, the workload-equalised serial model
must clear the 30% bar, and the pass decision must come from the intervention
branch (>= 50% reduction).  If someone later "improves" the numbers by swapping
the comparison basis, this test fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.gate_lifecycle import baseline
from tools.gate_lifecycle.dag import critical_path, validate_schedule

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "tools" / "gate_lifecycle" / "data" / "baseline_plan165.json"


@pytest.fixture(scope="module")
def base():
    return baseline.load_baseline(BASE_PATH)


@pytest.fixture(scope="module")
def out(base):
    return baseline.evaluate(base, capacity=16)


def test_baseline_fixture_is_provenanced(base):
    assert base["plan_id"] == 165
    assert len(base["nodes"]) == 8
    assert base["events"]["total"] == 16
    assert base["planned_times_present"] is False
    assert len(base["provenance"]["sql"]) >= 3
    for rec in base["nodes"]:
        assert rec["duration_s"] >= 0
        assert "src_id" in rec


def test_baseline_fixture_is_anonymised(base):
    """No domain vocabulary may leak into tools/tests fixtures."""
    text = json.dumps(base, ensure_ascii=False).lower()
    for forbidden in ("hull", "ship", "propuls", "seakeep", "resist", "ice", "船", "阻力"):
        assert forbidden not in text


def test_dependency_violation_of_the_baseline_is_reproduced(base):
    viol = baseline.evaluate(base)["baseline"]["dependency_violations"]
    assert len(viol) == 7  # the summary node started with its 7 parents in flight
    assert all(v["start_offset_s"] < v["dependency_end_offset_s"] for v in viol)


def test_replay_comparison_is_honestly_negative(out):
    replay = out["comparison"]["time_replay"]
    assert replay["reduction"] < 0
    assert replay["pass"] is False


def test_serial_model_clears_the_30_percent_time_bar(out):
    serial = out["comparison"]["time_serial"]
    assert serial["reduction"] >= 0.30
    assert serial["pass"] is True
    assert serial["turnaround_s"] == pytest.approx(62.0)


def test_intervention_branch_carries_the_decision(out):
    iv = out["comparison"]["interventions"]
    assert iv["baseline_per_node"] == pytest.approx(2.0)
    assert iv["automated_per_node"] == 0
    assert iv["reduction"] >= 0.50
    assert out["comparison"]["decision"] == "PASS"
    # the serial-model time branch also clears 30%; both are reported honestly
    assert "intervention" in out["comparison"]["satisfied_branch"]


def test_schedule_of_the_replayed_baseline_is_valid(base, out):
    plan = baseline.plan_from_baseline(base)
    from tools.gate_lifecycle.dag import schedule

    sched = schedule(plan, capacity=16)
    assert validate_schedule(plan, sched) == []
    assert sched.makespan == pytest.approx(out["automated"]["makespan_s"], abs=1e-6)
    cp, length = critical_path(plan)
    assert length == pytest.approx(out["automated"]["critical_length_s"], abs=1e-6)
    # the anonymised replay really carries the summary node behind its parents
    assert cp[-1] == "n08"


def test_comparison_digest_is_reproducible(base):
    a = baseline.evaluate(base, capacity=16)["digest"]
    b = baseline.evaluate(json.loads(json.dumps(base)), capacity=16)["digest"]
    assert a == b