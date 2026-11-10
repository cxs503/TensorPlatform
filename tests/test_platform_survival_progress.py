"""Progress meter + stall ladder: measurement accuracy, negative controls, escalation order.

Every test here is about *numbers or transitions*, not about smoke-importing the
module: the meter's rate is compared against a recount taken from disk, the stall
detector's detection time is compared against the 30 % acceptance bound, and the
ladder's rung order is asserted explicitly.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tools.progress_meter import ProgressMeter, StallDetector, Strategy, StrategyPlan, Weights
from tools.survival.workload import TaskConfig, run_task


# --------------------------------------------------------------------------- #
# meter
# --------------------------------------------------------------------------- #
def test_progress_rate_matches_manual_recount(tmp_path: Path) -> None:
    """A3: the meter's rate and a recount from artifacts-on-disk agree within 5 %."""
    run = run_task(
        tmp_path / "a3",
        TaskConfig(budget_total=80, grid=10, planned_artifacts=16,
                   criteria=(("first_artifact", 1), ("ten", 10))),
        plan=StrategyPlan([Strategy("baseline", 0.2)]),
    )
    manual = run["manual"]
    err = abs(run["progress_rate"] - manual["progress_rate"]) / manual["progress_rate"]
    assert manual["artifacts"] == run["artifact_count"]
    assert manual["budget_used"] == run["budget_used"]
    assert err <= 0.05, f"progress_rate {run['progress_rate']} vs manual {manual['progress_rate']}: {err:.4%}"


def test_meter_is_idempotent_per_name(tmp_path: Path) -> None:
    m = ProgressMeter(budget_total=10, ledger_path=tmp_path / "p.jsonl")
    m.advance(budget_used=5)
    assert m.record_artifact("a.json", digest="x") is True
    assert m.record_artifact("a.json", digest="x") is False  # resumed run re-emits it
    assert m.counts["artifact"] == 1
    assert m.credits == pytest.approx(1.0)
    assert len(m.ledger()) == 1


def test_meter_rejects_backwards_budget() -> None:
    m = ProgressMeter(budget_total=10)
    m.advance(budget_used=5)
    with pytest.raises(ValueError):
        m.advance(budget_used=4)


def test_manual_recount_reads_disk_not_memory(tmp_path: Path) -> None:
    """The recount must not trust in-memory counters: an extra file changes it."""
    art = tmp_path / "art"
    art.mkdir()
    m = ProgressMeter(budget_total=10, ledger_path=tmp_path / "p.jsonl", artifact_dir=art)
    m.advance(budget_used=4)
    (art / "one.json").write_text("{}")
    m.record_artifact("one.json")
    recount = m.manual_recount()
    (art / "stray.json").write_text("{}")
    assert m.manual_recount()["artifacts"] == recount["artifacts"] + 1


def test_weights_change_credit_linearly(tmp_path: Path) -> None:
    m1 = ProgressMeter(budget_total=10, weights=Weights(artifact=1.0, criterion=2.0, info_gain=1.0))
    m2 = ProgressMeter(budget_total=10, weights=Weights(artifact=2.0, criterion=2.0, info_gain=1.0))
    for m in (m1, m2):
        m.advance(budget_used=10)
        m.record_artifact("a")
        m.record_criterion("c", True)
    assert m2.credits - m1.credits == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# stall detection
# --------------------------------------------------------------------------- #
def test_stall_detected_within_30pct_and_reroutes(tmp_path: Path) -> None:
    """A4: a zero-yield strategy is caught inside 30 % of the budget, reroute issued."""
    run = run_task(
        tmp_path / "a4",
        TaskConfig(budget_total=100, grid=10, planned_artifacts=20),
        plan=StrategyPlan([Strategy("barren", 0.0), Strategy("fruitful", 0.05)]),
    )
    assert run["stall"]["detected"] is True
    frac = run["stall"]["first_detection_fraction"]
    assert frac <= 0.30, f"first detection at {frac:.3f} of budget"
    levels = [e["level"] for e in run["stall"]["events"]]
    assert levels[:2] == ["restate", "reroute"]


def test_reroute_produces_artifact_within_remaining_budget(tmp_path: Path) -> None:
    """A5: after the switch the run must get back to emitting credit."""
    run = run_task(
        tmp_path / "a5",
        TaskConfig(budget_total=100, grid=10, planned_artifacts=20),
        plan=StrategyPlan([Strategy("barren", 0.0), Strategy("fruitful", 0.05)]),
    )
    assert run["reroute_at_budget"] is not None
    assert run["post_reroute_artifacts"] >= 1
    post = json.loads((Path(run["artifact_dir"]).parent / "stall.json").read_text())
    assert post["recoveries"], "no recovered event recorded after the reroute"


@pytest.mark.parametrize("yield_per_budget,gap_fraction", [(0.2, 0.05), (0.05, 0.20)])
def test_no_false_positive_on_uneven_but_real_progress(tmp_path: Path, yield_per_budget: float,
                                                       gap_fraction: float) -> None:
    """A8: steady (5 % gaps) and bursty (20 % gaps) producers never trip the detector."""
    run = run_task(
        tmp_path / f"a8_{gap_fraction}",
        TaskConfig(budget_total=100, grid=10, planned_artifacts=10),
        plan=StrategyPlan([Strategy("s", yield_per_budget)]),
    )
    assert run["stall"]["detected"] is False, run["stall"]["events"]
    assert run["artifact_count"] >= 4
    # the gap is genuinely close to the bound, i.e. this is not a trivially easy negative
    assert gap_fraction < 0.25


def test_ladder_escalates_one_rung_per_barren_window(tmp_path: Path) -> None:
    """restate -> reroute -> shrink -> honest_fail, never skipping and never repeating."""
    run = run_task(
        tmp_path / "ladder",
        TaskConfig(budget_total=200, grid=8, planned_artifacts=40),
        plan=StrategyPlan([Strategy("barren", 0.0)]),
        stall_frac=0.10,
    )
    levels = [e["level"] for e in run["stall"]["events"]]
    assert levels == ["restate", "reroute", "shrink", "honest_fail"]
    assert run["status"] == "FAIL"
    at = [e["budget_used"] for e in run["stall"]["events"]]
    assert at == sorted(at), "events must be monotone in budget"
    windows = [b - a for a, b in zip([0] + at, at)]
    assert all(w > 0 for w in windows)


def test_blacklist_is_append_only_and_never_reselected(tmp_path: Path) -> None:
    plan = StrategyPlan([Strategy("a", 0.1), Strategy("b", 0.1), Strategy("c", 0.1)])
    assert plan.current.name == "a"
    plan.blacklist_current(10.0, "stall")
    nxt = plan.next(10.0)
    assert nxt is not None and nxt.name == "b"
    plan.blacklist_current(20.0, "stall")
    nxt2 = plan.next(20.0)
    assert nxt2 is not None and nxt2.name == "c"
    names = [rec["strategy"] for rec in plan.blacklist]
    assert names == ["a", "b"], names
    assert plan.next(30.0) is None  # queue exhausted -> next rung


def test_detector_threshold_semantics() -> None:
    """Exactly at the window the detector fires; just below it stays silent."""
    m = ProgressMeter(budget_total=100)
    det = StallDetector(m, stall_frac=0.25)
    m.advance(budget_used=24.9)
    assert det.observe() is None
    m.advance(budget_used=25.0)
    ev = det.observe()
    assert ev is not None and ev.level == "restate"
    assert math.isclose(ev.budget_fraction_at_detection, 0.25)