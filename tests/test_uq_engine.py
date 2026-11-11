"""Acceptance tests for the generic UQ engine ``tools.uq`` (PCE + MC + Sobol').

The tests are ordered the same way as the acceptance criteria they protect:

* basis sanity -- orthonormality of the uniform / normal / beta families under
  their own probability measure (this is the property every closed-form moment
  and Sobol' index relies on);
* C1 / C2 -- PCE moments vs a ``10**4``-draw Monte Carlo reference;
* C3 -- Ishigami first-/total-order indices against the published closed form,
  from two independent estimators (PCE decomposition and pick-freeze MC);
* C4 -- degree-refinement convergence (mean drift < 1%);
* C5 -- negative control: a response outside the span of a low-order chaos must
  be reported as *degree insufficient*, never quoted;
* C6 -- zero domain semantics in ``tools/uq`` (lexicon injected from
  ``config/uq_engine_domain_lexicon.json``);
* regression anchors that pin published constants (Branin minima, Ishigami
  moments) so a silent parameterisation change cannot pass unnoticed.

All cases are mathematical test functions; no application vocabulary appears in
the engine package itself.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.uq import (  # noqa: E402
    Marginal,
    SurrogateError,
    adaptive_fit,
    branin,
    branin_marginals,
    branin_minimum,
    compare_moments,
    control_exact,
    control_marginals,
    convergence_study,
    fit_pce,
    guard_or_raise,
    ishigami,
    ishigami_marginals,
    ishigami_reference,
    leave_one_out,
    monte_carlo,
    multi_index,
    nonlinear_control,
    sample_matrix,
    smooth_exact,
    smooth_marginals,
    smooth_mixed,
    sobol_mc,
)
from tools.uq.purity import scan_terms  # noqa: E402


# --------------------------------------------------------------------------- #
# basis / marginal sanity
# --------------------------------------------------------------------------- #
FAMILY_CASES = [
    ("uniform", Marginal.uniform("x", -1.0, 2.0)),
    ("normal", Marginal.normal("x", 1.0, 2.0)),
    ("beta", Marginal.beta("x", 2.0, 5.0, 0.0, 1.0)),
    ("beta_symmetric", Marginal.beta("x", 0.5, 0.5, -1.0, 1.0)),
    ("beta_flat", Marginal.beta("x", 1.0, 1.0, 0.0, 3.0)),
]


@pytest.mark.parametrize("label,marginal", FAMILY_CASES, ids=[c[0] for c in FAMILY_CASES])
def test_basis_is_orthonormal_under_its_own_measure(label, marginal):
    """E[phi_n phi_m] = delta_nm for every supported family (quadrature exact)."""
    nodes, w = marginal.quadrature(24)
    B = marginal.basis(marginal.to_standard(nodes), 5)
    gram = (B * w[:, None]).T @ B
    assert np.max(np.abs(gram - np.eye(6))) < 1e-10, f"{label}: basis not orthonormal"


@pytest.mark.parametrize("label,marginal", FAMILY_CASES, ids=[c[0] for c in FAMILY_CASES])
def test_marginal_moments_and_sampling(label, marginal):
    """Quadrature and a 2e5-draw sample agree with the analytic mean/std."""
    nodes, w = marginal.quadrature(20)
    mu = float(w @ nodes)
    sd = float(np.sqrt(w @ (nodes - mu) ** 2))
    assert mu == pytest.approx(marginal.analytic_mean, rel=1e-10, abs=1e-12)
    assert sd == pytest.approx(marginal.analytic_std, rel=1e-10, abs=1e-12)

    X = sample_matrix([marginal], 200_000, seed=3, method="mc")[:, 0]
    assert X.mean() == pytest.approx(marginal.analytic_mean, abs=4 * marginal.analytic_std
                                     / math.sqrt(200_000))
    assert X.std(ddof=1) == pytest.approx(marginal.analytic_std, rel=0.02)


def test_from_spec_roundtrip_and_errors():
    m = Marginal.from_spec({"family": "beta", "name": "u", "a": 2, "b": 3, "lower": -1, "upper": 4})
    assert m.family == "beta" and m.analytic_mean == pytest.approx(-1 + 5 * 2 / 5)
    with pytest.raises(ValueError):
        Marginal.from_spec({"family": "weibull", "name": "u"})
    with pytest.raises(ValueError):
        Marginal.normal("u", 0.0, -1.0)


def test_multi_index_structure():
    for d, p in ((1, 3), (2, 4), (3, 3)):
        idx = multi_index(d, p)
        assert tuple(idx[0]) == (0,) * d
        assert all(int(a.sum()) <= p for a in idx)
        assert idx.shape[0] == math.comb(d + p, p)
        assert [int(a.sum()) for a in idx] == sorted(int(a.sum()) for a in idx)


# --------------------------------------------------------------------------- #
# C1 / C2 -- moments vs Monte Carlo
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def smooth_surrogate():
    marginals = smooth_marginals()
    X = sample_matrix(marginals, 512, seed=1)
    return fit_pce(smooth_mixed, X, marginals, 3), marginals


def test_c1_pce_mean_matches_mc_within_1pct(smooth_surrogate):
    fit, marginals = smooth_surrogate
    mc = monte_carlo(smooth_mixed, marginals, n_samples=10_000, seed=20260101)
    cmp = compare_moments(fit.moments(), mc)
    assert cmp["mean_rel_error"] <= 0.01, cmp


def test_c2_pce_variance_matches_mc_within_3pct(smooth_surrogate):
    fit, marginals = smooth_surrogate
    mc = monte_carlo(smooth_mixed, marginals, n_samples=10_000, seed=20260101)
    cmp = compare_moments(fit.moments(), mc)
    assert cmp["variance_rel_error"] <= 0.03, cmp


def test_smooth_case_is_exactly_representable(smooth_surrogate):
    """A degree-3 polynomial must be reproduced to machine precision (truncation = 0)."""
    fit, _ = smooth_surrogate
    exact = smooth_exact()
    assert fit.moments()["mean"] == pytest.approx(exact["mean"], rel=1e-11)
    assert fit.moments()["variance"] == pytest.approx(exact["variance"], rel=1e-11)
    assert leave_one_out(fit)["loo_rel"] < 1e-10


def test_pce_quantiles_track_mc_quantiles(smooth_surrogate):
    fit, marginals = smooth_surrogate
    mc = monte_carlo(smooth_mixed, marginals, n_samples=10_000, seed=20260101)
    q = fit.predict_quantiles((0.05, 0.5, 0.95), n_surrogate=100_000, seed=5)
    for key, ref in mc["quantiles"].items():
        assert q[key] == pytest.approx(ref, abs=0.08 * mc["std"])


# --------------------------------------------------------------------------- #
# C3 -- Sobol' indices vs the published closed form
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("degree,tol", [(3, 0.40), (4, 0.12), (5, 0.10), (6, 0.05)])
def test_c3_pce_sobol_error_shrinks_with_degree(degree, tol):
    """Ishigami: PCE indices approach the analytic values as the degree rises."""
    marginals = ishigami_marginals()
    ref = ishigami_reference()
    X = sample_matrix(marginals, 1024, seed=3)
    sob = fit_pce(ishigami, X, marginals, degree).sobol()
    err_first = max(abs(sob["first_order"][k] - ref["first_order"][k]) for k in ref["first_order"])
    err_total = max(abs(sob["total_order"][k] - ref["total_order"][k]) for k in ref["total_order"])
    assert max(err_first, err_total) <= tol


def test_c3_pce_sobol_matches_analytic_within_0p05():
    marginals = ishigami_marginals()
    ref = ishigami_reference()
    X = sample_matrix(marginals, 1024, seed=3)
    sob = fit_pce(ishigami, X, marginals, 6).sobol()
    for k in ("x1", "x2", "x3"):
        assert abs(max(sob["first_order"][k], 0.0) - ref["first_order"][k]) <= 0.05
        assert abs(sob["total_order"][k] - ref["total_order"][k]) <= 0.05
    # the interaction term must be captured: S_T(x3) >> S_1(x3) = 0
    assert sob["total_order"]["x3"] > 0.15
    assert max(sob["first_order"]["x3"], 0.0) < 0.05


def test_c3_pick_freeze_mc_independent_estimator():
    """Second, model-free estimator agrees within 0.05 (no surrogate involved)."""
    marginals = ishigami_marginals()
    ref = ishigami_reference()
    smc = sobol_mc(ishigami, marginals, n_base=4096, seed=11)
    assert smc["n_evaluations"] == 4096 * 5
    for k in ("x1", "x2", "x3"):
        assert abs(smc["first_order_clipped"][k] - ref["first_order"][k]) <= 0.05
        assert abs(smc["total_order"][k] - ref["total_order"][k]) <= 0.05


def test_ishigami_reference_constants_are_the_published_ones():
    ref = ishigami_reference()
    assert ref["mean"] == pytest.approx(3.5, rel=1e-12)
    assert ref["variance"] == pytest.approx(13.8445879407, rel=1e-9)
    assert ref["first_order"]["x3"] == 0.0
    assert sum(ref["first_order"].values()) < 1.0  # non-additive model


# --------------------------------------------------------------------------- #
# C4 -- degree convergence
# --------------------------------------------------------------------------- #
def test_c4_mean_drift_below_1pct_and_converged():
    marginals = smooth_marginals()
    X = sample_matrix(marginals, 2048, seed=5)
    study = convergence_study(smooth_mixed, X, marginals, (2, 3, 4))
    assert study["status"] == "converged", study["reasons"]
    assert study["mean_drift"] < 0.01
    assert all(row["mean_drift"] < 0.01 for row in study["table"][1:])
    assert study["loo_rel"] < 0.05


def test_c4_detects_unconverged_variance_when_low_degree_only():
    """A degree-1-only ladder must not be declared converged for a degree-3 response."""
    marginals = smooth_marginals()
    X = sample_matrix(marginals, 512, seed=5)
    study = convergence_study(smooth_mixed, X, marginals, (1, 2))
    first = study["table"][1]
    assert first["variance_drift"] > 0.01  # p=1 misses x3^2 -> variance still moving


def test_adaptive_selects_smallest_converged_degree():
    marginals = smooth_marginals()
    X = sample_matrix(marginals, 1024, seed=5)
    res = adaptive_fit(smooth_mixed, X, marginals, min_degree=1, max_degree=5)
    assert res["status"] == "converged"
    assert 2 <= res["selected_degree"] <= 3


# --------------------------------------------------------------------------- #
# C5 -- negative control
# --------------------------------------------------------------------------- #
def test_c5_negative_control_reports_degree_insufficient():
    marginals = control_marginals()
    X = sample_matrix(marginals, 512, seed=7)
    study = convergence_study(nonlinear_control, X, marginals, (1, 2, 3))
    assert study["status"] == "degree_insufficient"
    assert study["loo_rel"] > 0.05
    assert study["reasons"], "an insufficient verdict must state why"


def test_c5_negative_control_not_rescued_by_more_degrees():
    marginals = control_marginals()
    X = sample_matrix(marginals, 512, seed=7)
    res = adaptive_fit(nonlinear_control, X, marginals, min_degree=1, max_degree=6)
    assert res["status"] == "degree_insufficient"
    assert res["selected_degree"] is None


def test_c5_guard_raises_instead_of_returning_numbers():
    marginals = control_marginals()
    X = sample_matrix(marginals, 512, seed=7)
    fit = fit_pce(nonlinear_control, X, marginals, 1)
    with pytest.raises(SurrogateError) as excinfo:
        guard_or_raise(fit, leave_one_out(fit))
    assert excinfo.value.diagnostics["degree"] == 1


def test_c5_negative_control_is_truly_insufficient():
    """The verdict must not be vacuous: degree 1 really is wrong (variance error > 5%)."""
    exact = control_exact(order=40)
    marginals = control_marginals()
    X = sample_matrix(marginals, 512, seed=7)
    m = fit_pce(nonlinear_control, X, marginals, 1).moments()
    assert abs(m["variance"] - exact["variance"]) / exact["variance"] > 0.05


# --------------------------------------------------------------------------- #
# C6 -- zero domain semantics
# --------------------------------------------------------------------------- #
def test_c6_engine_has_zero_domain_semantics():
    lexicon = json.loads((ROOT / "config" / "uq_engine_domain_lexicon.json")
                         .read_text(encoding="utf-8"))["terms"]
    hits = scan_terms([ROOT / "tools" / "uq"], lexicon)
    assert hits == [], f"domain vocabulary leaked into the engine: {hits[:5]}"


def test_purity_scanner_detects_a_planted_term(tmp_path):
    """The scanner itself must be able to fail (no vacuous green)."""
    planted = tmp_path / "planted.py"
    planted.write_text("VALUE = 1  # injected\n", encoding="utf-8")
    terms = ["injected"]
    assert len(scan_terms([tmp_path], terms)) == 1
    planted.write_text("VALUE = 2\n", encoding="utf-8")
    assert scan_terms([tmp_path], terms) == []


# --------------------------------------------------------------------------- #
# regression anchors
# --------------------------------------------------------------------------- #
def test_branin_published_minima():
    """Pins the standard parameterisation (a wrong b makes the minima ~1e3)."""
    ref = branin_minimum()
    for point in ref["argmin"]:
        assert branin(point) == pytest.approx(ref["value"], abs=1e-6)


def test_branin_guard_selects_degree_and_mc_agrees():
    marginals = branin_marginals()
    X = sample_matrix(marginals, 1024, seed=13)
    loo = {p: leave_one_out(fit_pce(branin, X, marginals, p))["loo_rel"]
           for p in (4, 6, 8)}
    assert loo[4] > loo[6] > loo[8]  # monotone improvement in the LOO guard
    assert loo[8] < 0.05
    mc = monte_carlo(branin, marginals, 10_000, seed=4242)
    fit = fit_pce(branin, X, marginals, 8)
    cmp = compare_moments(fit.moments(), mc)
    # the PCE is accurate; the remaining gap is the MC standard error (z < 3)
    assert cmp["mean_z"] < 3.0
    assert cmp["variance_z"] < 3.0


# --------------------------------------------------------------------------- #
# numerical hygiene
# --------------------------------------------------------------------------- #
def test_fit_rejects_underdetermined_design():
    marginals = smooth_marginals()
    X = sample_matrix(marginals, 5, seed=1)
    with pytest.raises(ValueError):
        fit_pce(smooth_mixed, X, marginals, 4)


def test_fit_is_deterministic_for_a_fixed_seed():
    marginals = smooth_marginals()
    a = sample_matrix(marginals, 256, seed=42)
    b = sample_matrix(marginals, 256, seed=42)
    assert np.array_equal(a, b)
    fa = fit_pce(smooth_mixed, a, marginals, 3)
    fb = fit_pce(smooth_mixed, b, marginals, 3)
    assert np.allclose(fa.coefficients, fb.coefficients, rtol=0, atol=0)


def test_variance_closure_of_sobol_decomposition():
    """Sum of all ANOVA terms of the polynomial case equals 1 (additive + interaction)."""
    marginals = smooth_marginals()
    X = sample_matrix(marginals, 512, seed=1)
    sob = fit_pce(smooth_mixed, X, marginals, 3).sobol()
    first = sum(sob["first_order"].values())
    second = sum(sob["second_order"].values())
    assert first + second == pytest.approx(1.0, abs=1e-10)