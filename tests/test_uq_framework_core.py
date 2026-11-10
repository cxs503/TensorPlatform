"""C1 + C2 acceptance: input-propagation leg of the UQ framework.

Every number asserted here is compared against an **independent** reference:

* marginal moments against their closed forms (``(a+b+c)/3``, ``(b-a)^2/12``,
  ``sigma^2``), and the inverse-CDF map against stratified sample moments;
* the orthonormal chaos basis against its defining identity
  ``E[psi_i psi_j] = delta_ij`` (measured with an independent 200k sample);
* the chaos *moments* against the analytic moments of polynomial responses
  (which a finite-order expansion reproduces exactly -- so the test scores the
  implementation, not the truncation);
* the Sobol' shares both in closed form from the chaos coefficients and by the
  complement-design Monte Carlo estimator (two independent estimators);
* Monte Carlo moments against the analytic values, scored with the ASME V&V 20
  convention (deviation vs ``max(threshold, 3 sigma)``, because a 1000-sample
  variance estimate carries ``sqrt(2/n) ~ 4.5 %`` relative standard error).
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.services.uq_framework import (  # noqa: E402
    DEFAULT_CRITERIA,
    InputDistribution,
    UQConfig,
    UQResult,
    basis_for,
    compare_models,
    evaluate_criteria,
    fit_pce,
    run_monte_carlo,
    run_uq,
    sobol_complement,
    total_degree_indices,
)
from backend.services.uq_framework.synthetic import (  # noqa: E402
    linear_response,
    model_ensemble,
    quadratic_response,
)

DISTS_LINEAR = [InputDistribution.normal("x0", 0.0, 1.0),
                InputDistribution.uniform("x1", -1.0, 1.0)]
DISTS_TRI = [InputDistribution.triangular("x0", 0.0, 1.0, 4.0),
             InputDistribution.normal("x1", 2.0, 0.5)]
DISTS_QUAD = [InputDistribution.normal("x0", 0.0, 1.0),
              InputDistribution.uniform("x1", -1.0, 1.0),
              InputDistribution.triangular("x2", 0.0, 1.0, 4.0)]


# --------------------------------------------------------------------------- #
# marginals / basis
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dist,mean,var", [
    (InputDistribution.normal("x", 1.5, 2.0), 1.5, 4.0),
    (InputDistribution.uniform("x", -1.0, 3.0), 1.0, 16.0 / 12.0),
    (InputDistribution.triangular("x", 0.0, 1.0, 4.0), 5.0 / 3.0, 13.0 / 18.0),
])
def test_marginal_moments_match_closed_form(dist, mean, var):
    assert dist.mean == pytest.approx(mean, rel=1e-12)
    assert dist.variance == pytest.approx(var, rel=1e-12)
    assert dist.std == pytest.approx(math.sqrt(var), rel=1e-12)


def test_inverse_cdf_roundtrip_and_moments():
    """The unit-box map reproduces the analytic moments of every family."""
    rng = np.random.default_rng(7)
    u = rng.random((200000, 1))
    for dist in (InputDistribution.normal("x", 1.5, 2.0),
                 InputDistribution.uniform("x", -1.0, 3.0),
                 InputDistribution.triangular("x", 0.0, 1.0, 4.0)):
        x = dist.from_unit(u)
        assert x.mean() == pytest.approx(dist.mean, rel=2e-2)
        assert x.var() == pytest.approx(dist.variance, rel=4e-2)
        assert x.min() >= dist.from_unit(np.array([0.0]))[0] - 1e-9
        assert x.max() <= dist.from_unit(np.array([1.0]))[0] + 1e-9


def test_triangular_inverse_cdf_branches():
    """Both pieces of the triangular inverse CDF hit the exact quantiles."""
    a, c, b = 0.0, 1.0, 4.0
    d = InputDistribution.triangular("x", a, c, b)
    split = (c - a) / (b - a)
    assert float(d.from_unit(np.array([split]))[0]) == pytest.approx(c, rel=1e-12)
    assert float(d.from_unit(np.array([0.5 * split]))[0]) == pytest.approx(
        a + math.sqrt(0.5 * split * (b - a) * (c - a)), rel=1e-12)
    assert float(d.from_unit(np.array([0.5 * (1 + split)]))[0]) == pytest.approx(
        b - math.sqrt((1 - 0.5 * (1 + split)) * (b - a) * (b - c)), rel=1e-12)


def _exact_gram(dist, degree: int) -> np.ndarray:
    """``E[psi_i psi_j]`` computed by an exact quadrature for each family.

    Gauss-Hermite for the normal (a 24-node rule is exact for degree <= 47),
    Gauss-Legendre for the uniform, and a two-piece Gauss-Legendre rule for the
    triangular (whose density is piecewise linear, so a 40-node rule per piece
    is exact for degree <= 79).  Deterministic: no sampling error can hide a
    wrong recurrence.
    """
    if dist.family == "normal":
        t, w = np.polynomial.hermite.hermgauss(24)
        X = dist.params["mean"] + dist.params["std"] * math.sqrt(2.0) * t
        w = w / math.sqrt(math.pi)
    elif dist.family == "uniform":
        t, w = np.polynomial.legendre.leggauss(24)
        a, b = dist.params["lower"], dist.params["upper"]
        X = 0.5 * (a + b) + 0.5 * (b - a) * t
        w = w / 2.0
    else:
        a, c, b = dist.params["lower"], dist.params["mode"], dist.params["upper"]
        nodes, weights = np.polynomial.legendre.leggauss(40)
        X_parts, w_parts = [], []
        for lo, hi in ((a, c), (c, b)):
            if hi <= lo:
                continue
            half, mid = 0.5 * (hi - lo), 0.5 * (hi + lo)
            xx = mid + half * nodes
            dens = np.where(xx <= c,
                            2.0 * (xx - a) / ((b - a) * (c - a)) if c > a else 0.0,
                            2.0 * (b - xx) / ((b - a) * (b - c)) if b > c else 0.0)
            X_parts.append(xx)
            w_parts.append(half * weights * dens)
        X = np.concatenate(X_parts)
        w = np.concatenate(w_parts)
    V = basis_for(dist, degree).values(X)
    return (V * w[:, None]).T @ V


@pytest.mark.parametrize("dist", [
    InputDistribution.normal("x", 1.0, 2.0),
    InputDistribution.uniform("x", -1.0, 3.0),
    InputDistribution.triangular("x", 0.0, 1.0, 4.0),
    InputDistribution.triangular("x", -1.0, -1.0, 2.0),   # mode on the boundary
])
def test_basis_is_orthonormal_under_its_own_measure(dist):
    """``E[psi_i psi_j] = delta_ij`` -- the identity the closed-form moments rest on."""
    gram = _exact_gram(dist, 4)
    assert np.allclose(np.diag(gram), 1.0, atol=1e-9)
    off = gram - np.diag(np.diag(gram))
    assert np.abs(off).max() < 1e-9


def test_total_degree_index_set_is_complete_and_bounded():
    for d, order in ((1, 3), (2, 2), (3, 3), (4, 2)):
        idx = total_degree_indices(d, order)
        assert idx.shape[0] == math.comb(d + order, order)
        assert idx.sum(axis=1).max() == order
        assert np.array_equal(idx[0], np.zeros(d, dtype=int))
        assert np.all(idx >= 0)


# --------------------------------------------------------------------------- #
# C1 -- Monte Carlo against analytic moments
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dists,coef,intercept", [
    (DISTS_LINEAR, [2.0, 3.0], 1.0),
    (DISTS_TRI, [1.0, 2.0], 0.0),
    (DISTS_QUAD, [1.0, -0.5, 2.0], 0.5),
])  # DISTS_QUAD exercises the interaction term (quadratic_response)
def test_mc_moments_match_analytic(dists, coef, intercept):
    """C1: MC mean/variance vs the closed form, scored as ASME V&V 20."""
    if len(dists) == 3:
        func, meta = quadratic_response(intercept=0.0)
        ref = meta["reference"](dists)
    else:
        func, meta = linear_response(coef, intercept)
        ref = meta["reference"](dists)
    cfg = UQConfig("c1_case", dists, propagation_method="monte_carlo", n_mc=1000,
                   reference={"mean": ref["mean"], "std": ref["std"]})
    res = run_uq(func, cfg)
    mc = res.propagation["monte_carlo"]
    dev_mean = abs(mc["mean"] - ref["mean"]) / abs(ref["mean"])
    if res.criteria["C1_mc_mean"]["estimator_se"] < 0.01:
        assert dev_mean < DEFAULT_CRITERIA["mc_moment_rel_err_max"]      # literal 2 %
    assert dev_mean < 3 * res.criteria["C1_mc_mean"]["estimator_se"]
    # ASME V&V 20 band: the variance estimate carries sqrt(2/n) relative se
    se = math.sqrt(2.0 / (1000 - 1))
    assert abs(mc["variance"] - ref["variance"]) / ref["variance"] < max(
        DEFAULT_CRITERIA["mc_moment_rel_err_max"], 3 * se)
    assert res.criteria["C1_mc_mean"]["status"] == "pass"
    assert res.criteria["C1_mc_variance"]["status"] == "pass"
    assert res.criteria["C1_mc_variance"]["basis"] == "asme_vv20_validation_uncertainty"


def test_mc_mean_deviation_shrinks_with_sample_size():
    """Unbiasedness check: the mean error decays like n^{-1/2} (2 seeds averaged)."""
    func, meta = linear_response([2.0, 3.0], 1.0)
    ref = meta["reference"](DISTS_LINEAR)
    errs = []
    for n in (1000, 10000, 100000):
        vals = [run_monte_carlo(func, DISTS_LINEAR, n=n, seed=s)["mean"] for s in (0, 1, 2)]
        errs.append(abs(np.mean(vals) - ref["mean"]) / abs(ref["mean"]))
    assert errs[1] < errs[0] or errs[1] < 0.01
    assert errs[2] < 2e-3


# --------------------------------------------------------------------------- #
# C2 -- chaos surrogate accuracy and cost
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dists,build,order", [
    (DISTS_LINEAR, lambda: linear_response([2.0, 3.0], 1.0), 2),
    (DISTS_TRI, lambda: linear_response([1.0, 2.0], 0.0), 2),
    (DISTS_QUAD, lambda: quadratic_response(), 2),
])
def test_pce_matches_analytic_and_mc_at_a_fraction_of_the_cost(dists, build, order):
    """C2: PCE statistics within 5 % of MC, at >10x fewer model evaluations."""
    func, meta = build()
    ref = meta["reference"](dists)
    cfg = UQConfig("c2_case", dists, propagation_method="both", n_mc=1000,
                   pce_order=order, pce_train_factor=2.5, pce_holdout=48,
                   reference={"mean": ref["mean"], "std": ref["std"]}, sobol=False)
    res = run_uq(func, cfg)
    pce = res.propagation["pce"]
    agree = res.propagation["agreement"]

    # closed-form chaos moments reproduce the analytic ones
    assert pce["mean"] == pytest.approx(ref["mean"], rel=1e-9)
    assert pce["variance"] == pytest.approx(ref["variance"], rel=1e-9)
    # surrogate adequacy
    assert pce["r2_holdout"] >= DEFAULT_CRITERIA["pce_r2_min"]
    # PCE vs MC agreement and cost
    assert agree["mean_rel_diff"] < DEFAULT_CRITERIA["pce_vs_mc_rel_max"]
    assert agree["std_rel_diff"] < DEFAULT_CRITERIA["pce_vs_mc_rel_max"]
    assert agree["eval_speedup"] > DEFAULT_CRITERIA["pce_speedup_min"]
    for key in ("C2_pce_mean", "C2_pce_std", "C2_pce_speedup", "C2_pce_r2"):
        assert res.criteria[key]["status"] == "pass", (key, res.criteria[key])


@pytest.mark.parametrize("dists,build", [
    (DISTS_LINEAR, lambda: linear_response([2.0, 3.0], 1.0)),
    (DISTS_TRI, lambda: linear_response([1.0, 2.0], 0.0)),
    (DISTS_QUAD, lambda: quadratic_response()),
])
def test_chaos_sobol_shares_match_closed_form(dists, build):
    """Sobol' shares from the chaos coefficients == analytic shares (exact)."""
    func, meta = build()
    ref = meta["reference"](dists)
    res = fit_pce(func, dists, order=2, n_train=30, n_holdout=32, seed=3)
    for name, share in ref["sobol_first"].items():
        assert res.sobol_first[name] == pytest.approx(share, abs=2e-9), ("first", name)
    for name, share in ref["sobol_total"].items():
        assert res.sobol_total[name] == pytest.approx(share, abs=2e-9), ("total", name)
    assert sum(res.sobol_total.values()) >= 1.0 - 1e-9


def test_mc_sobol_matches_closed_form_within_estimator_noise():
    """The complement-design estimator is independent of the chaos one."""
    func, meta = linear_response([2.0, 3.0], 1.0)
    ref = meta["reference"](DISTS_LINEAR)

    def f_unit(U):
        return func(**{d.name: d.from_unit(U[:, j]) for j, d in enumerate(DISTS_LINEAR)})

    sob = sobol_complement(f_unit, 2, n_base=2048, max_order=2, seed=0)
    for name, share in ref["sobol_first"].items():
        assert sob["first_order"][name] == pytest.approx(share, abs=0.05)
    for name, share in ref["sobol_total"].items():
        assert sob["total_order"][name] == pytest.approx(share, abs=0.05)
    assert sob["closure"] == pytest.approx(1.0, abs=0.1)  # sum of all orders == 1


# --------------------------------------------------------------------------- #
# config / result contracts
# --------------------------------------------------------------------------- #
def test_config_rejects_bad_inputs():
    with pytest.raises(ValueError):
        UQConfig("c", [InputDistribution("x", "weibull", {"a": 1})])
    with pytest.raises(ValueError):
        UQConfig("c", [InputDistribution.normal("x", 0, 1),
                       InputDistribution.uniform("x", 0, 1)])
    with pytest.raises(ValueError):
        UQConfig("c", [InputDistribution.normal("x", 0, 1)], propagation_method="magic")
    with pytest.raises(ValueError):
        UQConfig("c", [InputDistribution.normal("x", 0, 1)], criteria={"nope": 1.0})
    with pytest.raises(ValueError):
        UQConfig.from_dict({"case_id": "c", "input_distributions": [], "typo_key": 1})


def test_config_roundtrip_and_json_serialisable():
    cfg = UQConfig("rt", DISTS_LINEAR, n_mc=200, pce_order=2,
                   criteria={"mc_moment_rel_err_max": 0.03},
                   numerical={"grid_solutions": [1.0, 2.0, 3.0], "p_expected": 2.0},
                   model_form={"weights": "bma", "validation": {"truth": np.zeros(3),
                                                                "models": {"a": np.zeros(3)}}})
    doc = cfg.to_dict()
    json.dumps(doc)  # must be serialisable (numpy arrays collapse)
    back = UQConfig.from_dict(doc)
    assert back.case_id == cfg.case_id
    assert back.criteria["mc_moment_rel_err_max"] == 0.03
    assert back.criteria["pce_vs_mc_rel_max"] == DEFAULT_CRITERIA["pce_vs_mc_rel_max"]


def test_result_roundtrip_and_unevaluated_criteria_are_not_passes():
    func, meta = linear_response([2.0, 3.0], 1.0)
    ref = meta["reference"](DISTS_LINEAR)
    res = run_uq(func, UQConfig("rt2", DISTS_LINEAR, n_mc=200,
                                reference={"mean": ref["mean"], "std": ref["std"]}))
    doc = json.loads(json.dumps(res.to_dict()))
    back = UQResult.from_dict(doc)
    assert back.mean == pytest.approx(res.mean, rel=1e-12)
    assert back.headline_method in ("pce", "monte_carlo")
    # no numerical / model-form evidence supplied => those criteria are not passes
    assert res.criteria["C3_gci_order"]["status"] == "not_evaluated"
    assert res.criteria["C4_model_form"]["status"] == "not_evaluated"


def test_evaluate_criteria_marks_missing_reference_not_evaluated():
    propagation = {"monte_carlo": {"mean": 1.0, "std": 1.0, "variance": 1.0, "n_samples": 1000}}
    out = evaluate_criteria({"criteria": DEFAULT_CRITERIA, "reference": None}, propagation)
    assert out["C1_mc_mean"]["status"] == "not_evaluated"


def test_headline_falls_back_to_mc_when_surrogate_fails_its_gate():
    """A response the order-1 chaos cannot represent must not become the headline."""
    dists = [InputDistribution.uniform("x0", 0.0, 1.0)]
    func = lambda x0: np.exp(6.0 * x0)  # noqa: E731 -- order 1 cannot fit this
    cfg = UQConfig("gate", dists, propagation_method="both", n_mc=4000, pce_order=1,
                   pce_train_factor=3.0, sobol=False)
    res = run_uq(func, cfg)
    assert res.propagation["pce"]["r2_holdout"] < DEFAULT_CRITERIA["pce_r2_min"]
    assert res.headline_method == "monte_carlo"
    assert res.criteria["C2_pce_r2"]["status"] == "fail"
    assert res.all_criteria_pass is False