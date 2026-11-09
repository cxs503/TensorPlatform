"""Verification suite for the forward-UQ / error-propagation framework.

What this suite proves (no assertion here is satisfied by "the code ran")
-----------------------------------------------------------------------
1. **Input model** -- truncated-normal moments against SciPy's ``truncnorm``,
   monotone inverse-CDF mapping, explicit cut-mass diagnostics, validation.
2. **Designs** -- shapes/determinism, QMC stratification, and marginal
   correctness of the inverse-CDF mapping (KS test against the target law).
3. **Quantile intervals** -- a *coverage experiment*: the nominal 95 %
   distribution-free quantile interval must bracket the true quantile in ~95 %
   of independent replications (this is the property the platform's
   "CI width must match independent replication" criterion relies on).
4. **Replicate-width consistency** -- two independent estimators of the same
   interval width (single-run bootstrap vs. scatter over independent runs)
   must agree within ``[0.8, 1.2]`` on a model with a closed-form variance.
5. **Surrogate acceleration** -- on a smooth quadratic response the surrogate
   must reproduce mean/std/P95 of brute force within a few percent *and* the
   model-evaluation speed-up must be >= 10x.
6. **Sobol' closure decomposition** -- scored against three closed forms:
   additive ``g``-function (``S_i = S_Ti``, closure = 1), multiplicative
   ``g``-function (analytic ``S_u = prod_{i in u} v_i / V`` for *all* orders,
   so the second-order terms and the higher-order deficit are both exact), and
   Ishigami & Homma (1990).
7. **Propagation chain** -- Jacobian exactness on linear maps, scaled-Jacobian
   condition number against an independently computed value, delta-method vs
   Monte-Carlo sigma on a linear chain (ratio -> 1), and an ill-conditioned
   chain that the diagnostic must flag (so the criterion is not vacuous).
8. **Figures** -- the four renderers write non-trivial PNGs.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.services.global_sensitivity import (  # noqa: E402
    ishigami,
    ishigami_reference,
    sobol_g,
    sobol_g_reference,
)
from backend.services.uq_propagation import (  # noqa: E402
    ChainStage,
    InputSpec,
    accelerated_uncertainty,
    bootstrap_quantile_ci,
    evaluate_model,
    figure_confidence_intervals,
    figure_convergence,
    figure_propagation_path,
    figure_sensitivity,
    fit_poly_surrogate,
    jacobian,
    output_uncertainty,
    propagation_chain,
    qmc_variance_reduction,
    quantile_ci_order_statistic,
    replicate_ci_width,
    sample_inputs,
    sobol_hdmr,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _uniform_box(d: int) -> list:
    return [InputSpec(name=f"x{i + 1}", kind="uniform", low=0.0, high=1.0) for i in range(d)]


# ---------------------------------------------------------------------------
# 1. Input model
# ---------------------------------------------------------------------------


def test_input_spec_truncated_normal_moments_match_scipy():
    from scipy.stats import truncnorm

    spec = InputSpec(name="a", kind="normal", mean=2.0, std=0.5, truncate_sigma=3.0)
    lo, hi = spec.physical_bounds
    assert lo == pytest.approx(0.5) and hi == pytest.approx(3.5)
    ref = truncnorm((lo - 2.0) / 0.5, (hi - 2.0) / 0.5, loc=2.0, scale=0.5)
    m, s, _, _ = spec.moments()
    assert m == pytest.approx(ref.mean(), abs=1e-6)
    assert s == pytest.approx(ref.std(), abs=1e-6)   # 64-pt Legendre quadrature
    diag = spec.diagnostics()
    assert diag["mass_cut"] == pytest.approx(2.0 * 0.001349898, abs=1e-8)
    assert diag["truncated_mass"] + diag["mass_cut"] == pytest.approx(1.0, abs=1e-12)


def test_input_spec_lognormal_marginals_and_mapping():
    spec = InputSpec(name="k", kind="lognormal", mean=3.0, std=0.9, truncate_sigma=4.0)
    u = np.linspace(0.0, 1.0, 1001)
    x = spec.to_uniform(u)
    assert np.all(np.diff(x) > 0.0)                       # monotone inverse CDF
    lo, hi = spec.physical_bounds
    assert x[0] == pytest.approx(lo, rel=1e-9) and x[-1] == pytest.approx(hi, rel=1e-9)
    # big-sample moments must approach the analytic lognormal moments
    rng = np.random.default_rng(0)
    xs = spec.to_uniform(rng.random(200000))
    mu, sig = spec._log_moments()
    assert np.mean(xs) == pytest.approx(math.exp(mu + sig**2 / 2), rel=0.02)
    assert np.std(xs) == pytest.approx(
        math.sqrt((math.exp(sig**2) - 1) * math.exp(2 * mu + sig**2)), rel=0.03
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(name="", kind="normal", mean=1.0, std=1.0),
        dict(name="a", kind="weibull", mean=1.0, std=1.0),
        dict(name="a", kind="normal", mean=1.0, std=-1.0),
        dict(name="a", kind="uniform", low=2.0, high=1.0),
        dict(name="a", kind="lognormal", mean=-1.0, std=1.0),
    ],
)
def test_input_spec_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        InputSpec(**kwargs)


# ---------------------------------------------------------------------------
# 2. Designs and model evaluation
# ---------------------------------------------------------------------------


def test_sample_inputs_determinism_and_shapes():
    specs = _uniform_box(3)
    a = sample_inputs(specs, n=257, method="mc", seed=4)
    b = sample_inputs(specs, n=257, method="mc", seed=4)
    q = sample_inputs(specs, n=257, method="qmc", seed=4)
    assert a.shape == (257, 3) and q.shape == (257, 3)
    np.testing.assert_array_equal(a, b)
    assert not np.allclose(a, q)
    assert np.all((a >= 0.0) & (a <= 1.0))


def test_qmc_design_is_stratified_where_mc_is_not():
    specs = [InputSpec(name="x1", kind="uniform", low=0.0, high=1.0)]
    n = 1024
    q = sample_inputs(specs, n=n, method="qmc", seed=7)[:, 0]
    m = sample_inputs(specs, n=n, method="mc", seed=7)[:, 0]
    strata = np.floor(q * 16).astype(int)
    assert len(np.unique(strata)) == 16                  # every 1/16 stratum hit
    counts = np.bincount(strata, minlength=16)
    assert counts.max() - counts.min() <= 2              # near-uniform occupancy
    mc_counts = np.bincount(np.floor(m * 16).astype(int), minlength=16)
    assert mc_counts.max() - mc_counts.min() >= 4        # plain MC is lumpier


def test_sampled_marginal_matches_target_law():
    from scipy.stats import kstest, truncnorm

    spec = InputSpec(name="a", kind="normal", mean=1.0, std=0.3, truncate_sigma=3.0)
    x = sample_inputs([spec], n=40000, method="qmc", seed=11)[:, 0]
    lo, hi = spec.physical_bounds
    ref = truncnorm((lo - 1.0) / 0.3, (hi - 1.0) / 0.3, loc=1.0, scale=0.3)
    p = kstest(x, ref.cdf).pvalue
    assert p > 1e-3


def test_evaluate_model_vectorised_and_scalar_signatures_agree():
    X = np.linspace(0.0, 1.0, 12).reshape(6, 2)

    def vec(A):
        return A[:, 0] + 2.0 * A[:, 1]

    def scal(x):
        return float(x[0] + 2.0 * x[1])

    np.testing.assert_allclose(evaluate_model(vec, X), evaluate_model(scal, X))


def test_evaluate_model_rejects_non_finite():
    def bad(X):
        return np.full(X.shape[0], np.nan)

    with pytest.raises(ValueError, match="non-finite"):
        evaluate_model(bad, np.zeros((4, 2)))


# ---------------------------------------------------------------------------
# 3. Quantile intervals: coverage experiment
# ---------------------------------------------------------------------------


def test_order_statistic_interval_has_nominal_coverage():
    """The 95 % interval must contain the true quantile in ~95 % of replicates."""
    from scipy.stats import lognorm

    q = 0.95
    n = 400
    n_rep = 300
    rng = np.random.default_rng(2024)
    # lognormal(0, 0.5): true 95 % quantile
    true_q = float(lognorm.ppf(q, s=0.5))
    hits = 0
    for _ in range(n_rep):
        x = lognorm.rvs(s=0.5, size=n, random_state=rng)
        ci = quantile_ci_order_statistic(x, q=q, confidence=0.95)
        assert ci["method"] == "order-statistic"
        assert ci["ci_low"] <= ci["estimate"] <= ci["ci_high"]
        hits += int(ci["ci_low"] <= true_q <= ci["ci_high"])
    coverage = hits / n_rep
    # binomial standard error at 300 reps is ~1.3 %, allow 3 sigma
    assert 0.90 <= coverage <= 0.99, coverage


def test_bootstrap_and_order_statistic_widths_agree():
    rng = np.random.default_rng(5)
    x = rng.lognormal(mean=0.0, sigma=0.6, size=8000)
    boot = bootstrap_quantile_ci(x, q=0.95, n_boot=1500, seed=3)
    order = quantile_ci_order_statistic(x, q=0.95)
    w_boot = boot["ci_high"] - boot["ci_low"]
    w_order = order["ci_high"] - order["ci_low"]
    assert 0.6 <= w_boot / w_order <= 1.6


# ---------------------------------------------------------------------------
# 4. Forward propagation and replicate-width consistency
# ---------------------------------------------------------------------------


def _linear_model_factory(coef, stds):
    def f(X):
        return X @ np.asarray(coef, dtype=float)

    return f


def test_output_uncertainty_matches_closed_form_variance():
    specs = [
        InputSpec(name="a", kind="normal", mean=0.0, std=1.0),
        InputSpec(name="b", kind="normal", mean=0.0, std=2.0),
    ]
    coef = [3.0, -0.5]
    f = _linear_model_factory(coef, [1.0, 2.0])
    res = output_uncertainty(f, specs, n=60000, quantiles=(0.95,), n_boot=500, seed=1)
    col = res["columns"][col_name(res)]
    true_std = math.sqrt((3.0 * 1.0) ** 2 + (0.5 * 2.0) ** 2)
    assert col["mean"] == pytest.approx(0.0, abs=0.02)
    assert col["std"] == pytest.approx(true_std, rel=0.02)
    assert col["quantiles"]["0.95"]["estimate"] == pytest.approx(1.6449 * true_std, rel=0.02)


def col_name(res):
    return res["output_names"][0]


def test_ci_width_matches_independent_replicates_within_band():
    """Criterion-style check: single-run interval width vs replicate scatter."""
    specs = [
        InputSpec(name="a", kind="normal", mean=0.0, std=1.0),
        InputSpec(name="b", kind="lognormal", mean=1.0, std=0.3),
    ]
    f = lambda X: 2.0 * X[:, 0] + 5.0 * X[:, 1]  # noqa: E731
    single = output_uncertainty(f, specs, n=20000, quantiles=(0.95,), seed=2)
    reps = replicate_ci_width(f, specs, n_rep=12, n_per_rep=20000, quantiles=(0.95,), seed=2)
    w_single = single["columns"][single["output_names"][0]]["ci_mean_width"]
    w_rep = reps["mean_ci_width"][0]
    ratio = w_single / w_rep
    assert 0.8 <= ratio <= 1.2, ratio


# ---------------------------------------------------------------------------
# 5. Surrogate acceleration
# ---------------------------------------------------------------------------


def test_poly_surrogate_reproduces_quadratic_model_exactly():
    specs = [
        InputSpec(name="a", kind="normal", mean=0.0, std=1.0),
        InputSpec(name="b", kind="normal", mean=0.0, std=1.0),
    ]

    def f(X):
        return 1.0 + 2.0 * X[:, 0] - 0.5 * X[:, 1] + 0.8 * X[:, 0] * X[:, 1] + 0.3 * X[:, 1] ** 2

    sur = fit_poly_surrogate(f, specs, n_design=64, order=2, seed=3)
    X = sample_inputs(specs, n=500, method="mc", seed=9)
    np.testing.assert_allclose(sur.predict(X), f(X), rtol=1e-8, atol=1e-8)
    assert sur.as_dict()["max_total_degree"] == 2


def test_accelerated_uncertainty_speedup_and_accuracy():
    specs = [
        InputSpec(name="a", kind="normal", mean=1.0, std=0.2),
        InputSpec(name="b", kind="lognormal", mean=2.0, std=0.4),
        InputSpec(name="c", kind="uniform", low=0.5, high=1.5),
    ]

    def f(X):
        return X[:, 0] ** 2 * np.sqrt(np.abs(X[:, 1])) + np.sin(X[:, 2])

    res = accelerated_uncertainty(
        f, specs, n_design=256, n_valid=128, n_mc=40000, order=2, n_rep=8, n_per_rep=1024, seed=4
    )
    assert res["speedup_evals"] >= 10.0
    assert res["accelerated"]["validation_rel_l2"] < 5e-3
    name = res["brute_force"]["columns"].keys().__iter__().__next__()
    b = res["brute_force"]["columns"][name]
    a = res["accelerated"]["columns"][name]
    assert a["mean"] == pytest.approx(b["mean"], rel=0.02)
    assert a["std"] == pytest.approx(b["std"], rel=0.05)
    assert a["quantiles"]["0.95"]["estimate"] == pytest.approx(
        b["quantiles"]["0.95"]["estimate"], rel=0.05
    )
    assert 0.8 <= res["quantile_width_ratio"][name] <= 1.2


def test_qmc_variance_reduction_is_a_ratio_of_replicate_scatter():
    specs = [InputSpec(name=f"x{i}", kind="uniform", low=0.0, high=1.0) for i in range(4)]

    def f(X):
        return np.exp(np.sum(0.4 * X, axis=1))

    res = qmc_variance_reduction(f, specs, n_rep=8, n_per_rep=256, seed=6)
    assert res["speedup_min"] > 0.0
    assert res["speedup_max"] / res["speedup_min"] < 100.0  # a ratio, not noise-proof


# ---------------------------------------------------------------------------
# 6. Sobol' closure decomposition against closed forms
# ---------------------------------------------------------------------------


def test_sobol_hdmr_additive_g_function_closure_is_one():
    a = [0.0, 0.5, 3.0, 9.0]
    specs = _uniform_box(len(a))

    def additive(X):
        return np.sum(np.abs(4.0 * X - 2.0) / (1.0 + np.asarray(a)), axis=1)

    res = sobol_hdmr(additive, specs, n_base=16384, max_order=2, seed=11, n_boot=0)
    v = np.array([1.0 / (3.0 * (1.0 + ai) ** 2) for ai in a])
    V_add = float(np.sum(v))                        # additive: V = sum v_i
    expected = v / V_add
    got = np.array([res["first_order"][f"x{i + 1}"] for i in range(len(a))])
    np.testing.assert_allclose(got, expected, atol=0.02)
    # additive model: no interaction of any order -> closure must be 1
    assert res["closure"] == pytest.approx(1.0, abs=0.02)
    assert res["higher_order_share"] == pytest.approx(0.0, abs=0.02)
    # total-order estimator from the complementary design agrees with S_i
    for i in range(len(a)):
        # additive model: total order == first order (no interaction anywhere)
        assert res["total_order"][f"x{i + 1}"] == pytest.approx(got[i], abs=0.03)


def test_sobol_hdmr_multiplicative_g_function_higher_orders():
    """Multiplicative g: V_u = prod_{i in u} v_i, so every order is analytic."""
    a = [0.0, 0.5, 3.0]
    specs = _uniform_box(3)

    def multiplicative(X):
        g = (np.abs(4.0 * X - 2.0) + np.asarray(a)) / (1.0 + np.asarray(a))
        return np.prod(g, axis=1)

    v = np.array([1.0 / (3.0 * (1.0 + ai) ** 2) for ai in a])
    V = float(np.prod(1.0 + v) - 1.0)
    res = sobol_hdmr(multiplicative, specs, n_base=32768, max_order=3, seed=12)
    for i in range(3):
        assert res["first_order"][f"x{i + 1}"] == pytest.approx(v[i] / V, abs=0.015)
    for i, j in ((0, 1), (0, 2), (1, 2)):
        assert res["second_order"][f"x{i + 1},x{j + 1}"] == pytest.approx(
            v[i] * v[j] / V, abs=0.015
        )
    # max_order=3 covers the whole decomposition -> closure = 1
    assert res["closure"] == pytest.approx(1.0, abs=0.015)
    # truncated at order 2 the deficit is exactly the third-order share
    res2 = sobol_hdmr(multiplicative, specs, n_base=32768, max_order=2, seed=12)
    assert res2["higher_order_share"] == pytest.approx(
        float(np.prod(v) / V), abs=0.015
    )
    assert 0.95 <= res2["closure"] <= 1.05


def test_sobol_hdmr_ishigami_against_reference():
    ref = ishigami_reference()
    specs = [InputSpec(name=f"x{i + 1}", kind="uniform", low=lo, high=hi)
             for i, (lo, hi) in enumerate(ref["bounds"])]
    res = sobol_hdmr(ishigami, specs, n_base=8192, max_order=2, seed=13)
    S1 = np.array([res["first_order"][nm] for nm in ref["names"]])
    ST = np.array([res["total_order"][nm] for nm in ref["names"]])
    np.testing.assert_allclose(S1, ref["first_order"], atol=0.03)
    np.testing.assert_allclose(ST, ref["total_order"], atol=0.04)
    # the S1 = 0 / ST = 0.24 input must be identified as influential
    assert S1[2] == pytest.approx(0.0, abs=0.02)
    assert 0.19 <= ST[2] <= 0.29
    # interaction 1-3 carries the whole x3 influence
    assert 0.19 <= res["second_order"]["x1,x3"] <= 0.29
    # closure over orders <= 2 is exact for Ishigami (no order-3 term)
    assert res["closure"] == pytest.approx(1.0, abs=0.03)


def test_sobol_hdmr_bootstrap_reports_uncertainty_of_closure():
    specs = _uniform_box(3)
    res = sobol_hdmr(lambda X: X[:, 0] + 2.0 * X[:, 1] * X[:, 2], specs,
                     n_base=1024, max_order=2, seed=2, n_boot=60)
    boot = res["bootstrap"]
    assert boot["n_boot"] == 60
    assert boot["closure_ci"][0] <= res["closure"] <= boot["closure_ci"][1] + 1e-12
    assert boot["closure_std"] > 0.0


# ---------------------------------------------------------------------------
# 7. Error-propagation chain
# ---------------------------------------------------------------------------


def test_jacobian_exact_on_linear_map():
    A = np.array([[1.0, 2.0, -1.0], [0.5, 0.0, 3.0]])
    J = jacobian(lambda x: A @ x, [0.3, -1.2, 2.5])
    np.testing.assert_allclose(J, A, rtol=1e-7, atol=1e-7)


def test_jacobian_central_difference_on_polynomial():
    def f(x):
        return np.array([x[0] ** 3, 2.0 * x[0] * x[1]])

    J = jacobian(f, [1.5, -0.7])
    np.testing.assert_allclose(J[0, 0], 3.0 * 1.5**2, rtol=1e-7)
    np.testing.assert_allclose(J[0, 1], 0.0, atol=1e-7)
    np.testing.assert_allclose(J[1, :], [2.0 * -0.7, 2.0 * 1.5], rtol=1e-7)


def test_propagation_chain_condition_number_matches_analytic():
    A1 = np.array([[1.0, 0.3], [0.2, 1.4]])
    A2 = np.array([[2.0, -1.0], [0.3, 0.5]])
    specs = [
        InputSpec(name="u", kind="normal", mean=0.0, std=1.0),
        InputSpec(name="v", kind="normal", mean=0.0, std=0.5),
    ]
    stages = [
        ChainStage("stage1", lambda z: A1 @ z, output_names=["z1a", "z1b"]),
        ChainStage("stage2", lambda z: A2 @ z, output_names=["out_a", "out_b"]),
    ]
    res = propagation_chain(stages, [0.0, 0.0], specs, n_mc=20000, seed=3)
    J = A2 @ A1
    sig_in = np.array([1.0, 0.5])
    sig_out = np.sqrt(np.sum((J * sig_in[None, :]) ** 2, axis=1))   # exact for a linear chain
    scaled = J * sig_in[None, :] / sig_out[:, None]
    sv = np.linalg.svd(scaled, compute_uv=False)
    expected_cond = float(sv[0] / sv[-1])
    assert res["condition_number"] == pytest.approx(expected_cond, rel=0.02)
    assert res["condition_number"] < 1e6
    # linear chain: delta-method and MC sigmas must agree at every stage
    for st in res["stages"]:
        for ratio in st["linearisation_ratio"]:
            assert ratio == pytest.approx(1.0, rel=0.05)
    # propagation path graph is complete: 2 inputs -> 2 -> 2
    assert len(res["graph"]["nodes"]) == 6
    assert res["graph"]["n_layers"] == 3
    shares = np.array([e["variance_share"] for e in res["graph"]["edges"] if e["stage"] == "stage2"])
    assert shares.sum() == pytest.approx(2.0, abs=1e-9)   # one normalisation per output


def test_propagation_chain_flags_ill_conditioning():
    eps = 1e-3
    A1 = np.array([[1.0, 1.0], [1.0, 1.0 + eps]])
    A2 = np.array([[1.0, 0.0]])
    specs = [InputSpec(name="u", kind="normal", mean=0.0, std=1.0),
             InputSpec(name="v", kind="normal", mean=0.0, std=1.0)]
    stages = [
        ChainStage("stage1", lambda z: A1 @ z),
        ChainStage("stage2", lambda z: A2 @ z),
    ]
    res = propagation_chain(stages, [0.0, 0.0], specs, n_mc=4000, seed=1)
    # the two inputs are indistinguishable in the output: the input-space
    # diagnostic must flag it (the classical min(m,d) number cannot, m = 1)
    assert res["input_space_condition_number"] > 1e4


def test_propagation_chain_identifies_dominant_input_for_nonlinear_stage():
    specs = [
        InputSpec(name="strong", kind="normal", mean=0.0, std=1.0),
        InputSpec(name="weak", kind="normal", mean=0.0, std=1.0),
    ]
    stages = [ChainStage("mix", lambda z: np.array([z[0] + 0.05 * z[1] ** 2]), output_names=["y"])]
    res = propagation_chain(stages, [0.0, 0.0], specs, n_mc=8000, seed=2)
    assert res["dominant_input"] == "strong"


# ---------------------------------------------------------------------------
# 8. Figures
# ---------------------------------------------------------------------------


def test_figures_are_written(tmp_path):
    p1 = figure_confidence_intervals(
        [{"label": "q1", "value": 1.0, "lo": 0.9, "hi": 1.1},
         {"label": "q2", "value": 2.0, "lo": 1.7, "hi": 2.4}],
        str(tmp_path / "ci.png"), title="ci", xlabel="value",
    )
    p2 = figure_sensitivity(
        [{"name": "a", "S1": 0.6, "ST": 0.7}, {"name": "b", "S1": 0.3, "ST": 0.35}],
        str(tmp_path / "sens.png"), title="sens",
    )
    graph = propagation_chain(
        [ChainStage("s1", lambda z: np.array([z[0] + z[1]]), output_names=["m"]),
         ChainStage("s2", lambda z: np.array([2.0 * z[0]]), output_names=["o"])],
        [1.0, 1.0],
        [InputSpec(name="u", kind="normal", mean=1.0, std=0.1),
         InputSpec(name="v", kind="normal", mean=1.0, std=0.1)],
        n_mc=2000, seed=0,
    )["graph"]
    p3 = figure_propagation_path(graph, str(tmp_path / "path.png"), title="path")
    p4 = figure_convergence(
        {"mc": [(100, 0.1), (1000, 0.03), (10000, 0.01)],
         "qmc": [(100, 0.05), (1000, 0.006), (10000, 0.0006)]},
        str(tmp_path / "conv.png"), title="conv",
    )
    for p in (p1, p2, p3, p4):
        assert os.path.getsize(p) > 2000