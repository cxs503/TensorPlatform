"""漂移四路归因：单元级判定与优先级。"""
from tools.repro_settlement.attribution import (A, B, C, D, LABELS, UNATTRIBUTED,
                                                classify, is_label, signals)

CLEAN_RNG = {"recorded_seed": 1, "observed_seed": 1, "rerun_spread": 0.0}
SAME_BASE = {"recorded_sha256": "aa", "observed_sha256": "aa"}


def test_resolution_signal_wins():
    ev = {"resolution": {"recorded": {"cells": 100}, "observed": {"cells": 400}},
          "rng": CLEAN_RNG, "baseline": SAME_BASE}
    assert classify(ev)["label"] == A


def test_solver_signal_wins():
    ev = {"solver": {"recorded": {"version": "1.0"}, "observed": {"version": "1.1"}},
          "rng": CLEAN_RNG, "baseline": SAME_BASE}
    assert classify(ev)["label"] == B


def test_seed_mismatch_is_c_signal():
    ev = {"rng": {"recorded_seed": None, "observed_seed": 42, "rerun_spread": 0.0},
          "baseline": SAME_BASE}
    assert classify(ev)["label"] == C


def test_nondeterminism_under_same_seed_is_c_signal():
    ev = {"rng": {"recorded_seed": 5, "observed_seed": 5, "rerun_spread": 1e-3},
          "baseline": SAME_BASE}
    assert classify(ev)["label"] == C


def test_baseline_sha_change_is_d_signal():
    ev = {"rng": CLEAN_RNG,
          "baseline": {"recorded_sha256": "aa", "observed_sha256": "bb"}}
    assert classify(ev)["label"] == D


def test_no_evidence_is_unattributed_not_guess():
    out = classify({})
    assert out["label"] == UNATTRIBUTED and out["confidence"] == 0.0


def test_precedence_a_over_b_on_tie():
    ev = {"resolution": {"recorded": {"n": 1}, "observed": {"n": 2}},
          "solver": {"recorded": {"v": 1}, "observed": {"v": 2}}}
    out = classify(ev)
    assert out["label"] == A and out["tied"] == [B]
    assert out["confidence"] == 0.5


def test_confidence_reflects_weight_share():
    ev = {"solver": {"recorded": {"v": 1, "p": 1}, "observed": {"v": 2, "p": 2}},
          "rng": {"recorded_seed": None, "observed_seed": 1, "rerun_spread": 0.0}}
    out = classify(ev)
    assert out["label"] == B and 0.0 < out["confidence"] < 1.0


def test_signals_list_all_hits():
    ev = {"resolution": {"recorded": {"n": 1}, "observed": {"n": 2}},
          "baseline": {"recorded_sha256": "aa", "observed_sha256": "bb"}}
    assert {s["label"] for s in signals(ev)} == {A, D}


def test_every_label_has_fix_hint():
    ev = {"rng": {"recorded_seed": None, "observed_seed": 1, "rerun_spread": 0.0}}
    out = classify(ev)
    assert out["fix"] and "种子" in out["fix"]


def test_is_label_guard():
    assert all(is_label(x) for x in LABELS) and not is_label("e_other")


def test_tolerance_controls_spread_detection():
    ev = {"rng": {"recorded_seed": 3, "observed_seed": 3, "rerun_spread": 1e-12},
          "baseline": SAME_BASE}
    assert classify(ev, tol=1e-15)["label"] == C
    assert classify(ev, tol=1e-6)["label"] == UNATTRIBUTED


def test_richardson_residual_second_order():
    from tools.repro_settlement.attribution import richardson_residual
    assert richardson_residual(0.06, 2.0, 2.0) == 0.06 / 3.0


def test_richardson_residual_first_order():
    from tools.repro_settlement.attribution import richardson_residual
    assert abs(richardson_residual(-0.03, 2.0, 1.0) - 0.03) < 1e-12


def test_richardson_requires_valid_params():
    import pytest as _pytest
    from tools.repro_settlement.attribution import richardson_residual
    with _pytest.raises(ValueError):
        richardson_residual(0.1, 1.0, 2.0)
