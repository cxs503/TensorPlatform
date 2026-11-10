"""Known-truth regression tests for the EVOI dispatch evaluation.

Run: pytest -q tests/test_dispatch_evoi.py
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tools.dispatch_evoi.backtest import (
    check_reproducibility,
    evaluate_gates,
    run_evaluation,
    slate_softmax,
    split_windows,
)
from tools.dispatch_evoi.cost import CostModel
from tools.dispatch_evoi.features import AggregateState, build_design, numeric_row
from tools.dispatch_evoi.fixtures import TRUE_COEF, make_dataset, priority, true_logit
from tools.dispatch_evoi.leakage import (
    audit_leakage,
    compare_metrics,
    inject_post_hoc,
    null_control,
)
from tools.dispatch_evoi.models import GBDT, LogisticModel, auc, brier, precision_at_fraction
from tools.dispatch_evoi.offpolicy import single_action_ips
from tools.dispatch_evoi.schema import LeakageError, assert_dispatch_time, assert_no_post_hoc_columns

SMALL = dict(n_rounds=400, slate_size=8, n_windows=5, n_estimators=8)


# ────────────────────────── helpers / primitives ──────────────────────────


def test_auc_matches_rank_definition():
    y = np.array([0, 0, 1, 1])
    s = np.array([0.1, 0.4, 0.35, 0.8])
    # pairs: (0.1,0.35) ok, (0.1,0.8) ok, (0.4,0.35) miss, (0.4,0.8) ok -> 3/4
    assert auc(y, s) == pytest.approx(0.75)


def test_auc_constant_score_is_half():
    y = np.array([0, 1, 0, 1, 1, 0])
    assert auc(y, np.full(6, 0.3)) == pytest.approx(0.5)


def test_brier_and_precision_at_fraction():
    y = np.array([1, 0, 0, 0, 1])
    p = np.array([0.9, 0.6, 0.1, 0.2, 0.8])
    assert brier(y, p) == pytest.approx(np.mean((p - y) ** 2))
    # top 20% of 5 rows = 1 row -> the highest score (y=1)
    assert precision_at_fraction(y, p, 0.2) == pytest.approx(1.0)


def test_logistic_recovers_a_known_signal():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(4000, 3))
    beta = np.array([1.5, -0.8, 0.0])
    y = (rng.random(4000) < 1.0 / (1.0 + np.exp(-(X @ beta)))).astype(float)
    m = LogisticModel().fit(X, y, names=["a", "b", "c"])
    got = np.array([r["coef"] for r in m.feature_report()[1:]])
    # coefficients are on the standardised scale; X is ~N(0,1) so they match beta
    assert np.allclose(got, beta, atol=0.15)


def test_gbdt_beats_noise_and_learns_a_threshold():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(3000, 2))
    y = (X[:, 0] > 0.4).astype(float)
    m = GBDT(n_estimators=30, max_depth=2).fit(X, y)
    assert auc(y, m.predict(X)) > 0.95


def test_slate_softmax_rows_sum_to_one_and_floor_is_respected():
    score = np.arange(16, dtype=float)
    pi = slate_softmax(score, slate=8, temperature=1.0, floor_eps=0.1)
    assert np.allclose(pi.sum(axis=1), 1.0)
    assert pi.min() >= 0.1 / 8 - 1e-12


# ────────────────────────── leakage guard ──────────────────────────


def test_post_hoc_field_is_rejected():
    with pytest.raises(LeakageError):
        assert_dispatch_time({"passed": 1, "field_ts": {"passed": 0.0}}, 10.0, used=["passed"])
    with pytest.raises(LeakageError):
        assert_no_post_hoc_columns(["novelty", "wall_time"])


def test_timestamp_leak_is_rejected():
    record = {
        "novelty": 0.5,
        "field_ts": {"novelty": 20.0},   # became available AFTER dispatch
    }
    with pytest.raises(LeakageError):
        assert_dispatch_time(record, dispatch_ts=10.0, used=["novelty"])
    # the same record is fine once the stamp is at/before dispatch
    record["field_ts"]["novelty"] = 9.0
    assert_dispatch_time(record, dispatch_ts=10.0, used=["novelty"])


def test_missing_stamp_is_a_leak():
    with pytest.raises(LeakageError):
        assert_dispatch_time({"novelty": 0.5}, dispatch_ts=10.0, used=["novelty"])


def test_design_matrix_has_no_post_hoc_columns():
    ds = make_dataset(seed=3, n_rounds=60, slate_size=4)
    data = build_design(ds)
    assert_no_post_hoc_columns(data["names"])
    assert "passed" not in data["names"] and "wall_time" not in data["names"]


def test_post_hoc_injection_does_not_move_metrics():
    """Behavioural leakage probe: injecting true/noisy post-hoc columns is a no-op."""
    ds = make_dataset(seed=5, n_rounds=200, slate_size=4)
    base = run_evaluation(dataset=ds, seed=5, n_estimators=5)
    tr = run_evaluation(dataset=inject_post_hoc(ds, mode="true"), seed=5, n_estimators=5)
    nz = run_evaluation(
        dataset=inject_post_hoc(ds, mode="noise", seed=9), seed=5, n_estimators=5
    )
    assert compare_metrics(base, tr)[0] == 0.0
    assert compare_metrics(base, nz)[0] == 0.0


# ────────────────────────── history aggregates ──────────────────────────


def test_aggregate_state_only_uses_the_past():
    st = AggregateState()
    # no history -> family fail rate equals the Beta prior mean
    assert st.family_fail_rate("fam_a") == pytest.approx(0.5)
    for _ in range(6):
        st.update(family="fam_a", repo="r00", scale="small", passed=False)
    global_fail = 1.0 - st.global_pass_rate
    assert st.family_fail_rate("fam_a") > global_fail + 0.1   # burnt family is visible
    assert st.family_fail_rate("fam_b") == pytest.approx(global_fail)  # shrinks to the prior
    assert st.repo_scale_success("r01", "small") < 0.5          # never ran -> prior
    assert st.repo_history("r00") == pytest.approx(6.0)   # raw count; log1p at featurisation


# ────────────────────────── known-truth regression ──────────────────────────


def test_dgp_truth_is_monotone_in_novelty_and_band():
    ds = make_dataset(seed=11, n_rounds=4, slate_size=4)
    order = dict(ds.rounds[0].candidates[0])
    st = AggregateState()
    lo = true_logit(numeric_row({**order, "novelty": 0.05}, st))
    hi = true_logit(numeric_row({**order, "novelty": 0.95}, st))
    assert hi > lo
    # criteria sweet band: 6 criteria better than 10 for the same order
    a = true_logit(numeric_row({**order, "n_criteria": 6}, st))
    b = true_logit(numeric_row({**order, "n_criteria": 10}, st))
    assert a > b
    assert TRUE_COEF["family_fail_rate_dev"] < 0 < TRUE_COEF["cell_success_dev"]


def test_model_recovers_the_dgp_ranking():
    """The learned model must beat the static baseline on out-of-time data."""
    rep = run_evaluation(seed=7, **SMALL)
    a = rep["gates"]["a_probability"]
    assert a["auc_model_pooled"] > a["auc_baseline_pooled"] + 0.10
    assert a["rel_brier_improvement_mean"] > 0.10
    assert a["windows_model_better"] >= 4
    truth = rep["truth_benchmark"]
    assert truth["auc_true_prob_pooled"] > a["auc_model_pooled"]  # ceiling is above us
    assert truth["brier_gap_rel"] < 0.15                          # but not far above


def test_cost_model_recovers_the_log_cost_surface():
    ds = make_dataset(seed=13, n_rounds=300, slate_size=4)
    data = build_design(ds)
    m = CostModel().fit(data["X"][data["logged"]], data["cost"][data["logged"]], data["names"])
    assert m.r2 > 0.2
    # larger deliverables cost more (monotone in log_target_words)
    pred = m.predict(data["X"], data["names"])
    words = data["X"][:, data["names"].index("log_target_words")]
    lo, hi = pred[words < np.quantile(words, 0.25)], pred[words > np.quantile(words, 0.75)]
    assert hi.mean() > lo.mean()


def test_static_priority_is_weak_by_design():
    ds = make_dataset(seed=17, n_rounds=200, slate_size=8)
    scores = np.concatenate([np.asarray(r.priority) for r in ds.rounds])
    y = np.concatenate([np.asarray(r.outcomes, dtype=float) for r in ds.rounds])
    assert 0.45 < auc(y, scores) < 0.62   # present but far from the ~0.75 signal


# ────────────────────────── walk-forward / off-policy ──────────────────────────


def test_walk_forward_windows_are_ordered_and_disjoint():
    wins = split_windows(1000, 5)
    assert len(wins) == 5
    for (s, e), (s2, _) in zip(wins, wins[1:]):
        assert e == s2                 # contiguous
    assert wins[0][0] == pytest.approx(1000 / 6, abs=1.0)   # first block is ~n/6 rounds
    assert wins[-1][1] == 1000
    assert all(e > s for s, e in wins)


def test_ips_snips_ess_on_a_hand_computable_case():
    # 4 rounds; target agrees with the log on rounds 0 and 2
    prop = np.array([0.5, 0.25, 0.5, 0.1])
    reward = np.array([1.0, 0.0, 1.0, 0.0])
    li = np.array([0, 1, 2, 3])
    ti = np.array([0, 0, 2, 0])
    res = single_action_ips(prop, reward, logged_index=li, target_index=ti)
    w = np.array([2.0, 0.0, 2.0, 0.0])
    assert res.ips == pytest.approx(np.mean(w * reward))
    assert res.snips == pytest.approx((w * reward).sum() / w.sum())
    assert res.ess == pytest.approx(w.sum() ** 2 / (w**2).sum())
    assert res.matched == 2
    # positivity is required
    with pytest.raises(ValueError):
        single_action_ips(np.array([0.5, 0.0]), np.array([1.0, 1.0]),
                          logged_index=np.array([0, 1]), target_index=np.array([0, 0]))


def test_offpolicy_matches_exact_value_and_naive_is_biased():
    rep = run_evaluation(seed=7, **SMALL)
    op = rep["offpolicy_pooled"]
    sam = op["sampler"]
    assert sam["matched_frac"] == pytest.approx(1.0)     # stochastic target reaches every round
    assert abs(sam["ips"] - sam["value_true"]) < 0.08    # IPS tracks the exact simulator value
    assert sam["ips_minus_naive"] > 0.02                 # naive logged mean is biased low
    assert sam["ess_frac"] > 0.25


def test_label_shuffle_negative_control_collapses_to_chance():
    """Replicated null: shuffling training labels must leave AUC at chance."""
    ctl = null_control(seed=7, n_rounds=500, slate_size=8, n_permutations=25)
    assert ctl["in_band"], ctl
    assert abs(ctl["auc"] - 0.5) < 0.05
    # a single permutation is a noisy null draw -- the spread is reported, not hidden
    assert ctl["auc_std"] > 0.0


def test_same_seed_is_bit_identical():
    a = run_evaluation(seed=3, **SMALL)
    b = run_evaluation(seed=3, **SMALL)
    for k, v in a["pooled"].items():
        assert b["pooled"][k] == pytest.approx(v, rel=0, abs=0)


# ────────────────────────── end-to-end gates ──────────────────────────


def test_end_to_end_gate_verdict_on_the_fixed_fixture():
    """Full pipeline at a reduced but representative size: all six gates must pass."""
    main = run_evaluation(seed=7, n_rounds=1200, slate_size=8, n_windows=5, n_estimators=15)
    leakage = audit_leakage(seed=7, n_rounds=500, slate_size=8, n_estimators=8)
    repro = check_reproducibility(
        seed=7, other_seed=11, n_rounds=600, slate_size=8, n_estimators=8
    )
    verdict = evaluate_gates(
        main,
        leakage=leakage,
        reproducibility=repro,
        pytest_check={"pass": True, "command": "self"},
    )
    assert verdict["checks"]["a_probability"], verdict["gates"]["a_probability"]
    assert verdict["checks"]["b_ranking"], verdict["gates"]["b_ranking"]
    assert verdict["checks"]["c_leakage"], verdict["gates"]["c_leakage"]
    assert verdict["checks"]["d_offpolicy"], verdict["gates"]["d_offpolicy"]
    assert verdict["checks"]["e_reproducibility"], verdict["gates"]["e_reproducibility"]
    assert verdict["all_pass"]
