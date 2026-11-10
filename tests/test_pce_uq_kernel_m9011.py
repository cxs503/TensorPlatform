"""Acceptance tests for the non-intrusive PCE / Sobol uncertainty framework.

Everything asserted here is scored against a reference that is independent of the
code under test:

* quadrature rules are checked against the closed-form moments they claim to be
  exact for (``(k-1)!!`` for the Gaussian measure, ``1/(k+1)`` for the uniform
  measure) rather than against their own output,
* the Ishigami & Homma (1990) indices come from the published analytic variance
  decomposition (``tools/uq_bench/references.py``), and are additionally
  cross-checked with the pick-freeze Saltelli estimator that lives in a
  different module (``backend/services/global_sensitivity.py``),
* the six-parameter propagation case is scored against direct Monte Carlo on the
  same black box, which is the quantity the surrogate is supposed to replace.

The domain-free rule is also tested: the kernel and the runner must contain none
of the platform's domain vocabulary (checked with the platform's own leak
scanner and its data-side word list).
"""

from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import convenience
    sys.path.insert(0, str(ROOT))

from backend.services.pce_uq import (  # noqa: E402
    CaseSpec,
    Marginal,
    fit_projection,
    fit_regression,
    gauss_rule_1d,
    monte_carlo,
    multi_index_set,
    smolyak_grid,
    tensor_grid,
)
from backend.services.pce_uq.basis import design_matrix  # noqa: E402
from tools.uq_bench.references import (  # noqa: E402
    ishigami,
    ishigami_closed_form,
    sobol_g,
    sobol_g_closed_form,
)

CASE_DIR = ROOT / "config" / "uq_cases"


# --------------------------------------------------------------------------- #
# 1. quadrature: claim vs closed form
# --------------------------------------------------------------------------- #
def test_gauss_hermite_rule_moments_are_exact():
    """``E[X^k] = (k-1)!!`` for even ``k`` under ``N(0, 1)``; 0 for odd ``k``."""
    for n in (3, 5, 9):
        x, w = gauss_rule_1d("hermite", n)
        assert abs(float(np.sum(w)) - 1.0) < 1e-13
        for k in range(0, 2 * n):
            got = float(np.sum(w * x ** k))
            if k % 2:
                want = 0.0
            else:
                want = float(math.factorial(k)) / (2 ** (k // 2) * math.factorial(k // 2))
            # roundoff-aware tolerance: the moment itself grows like |x|^k
            scale = float(np.sum(np.abs(w) * np.abs(x) ** k)) + 1.0
            assert abs(got - want) < 1e-10 * scale, (n, k, got, want)


def test_gauss_legendre_rule_moments_are_exact():
    """``E[xi^k] = 1/(k+1)`` for even ``k`` on ``U(-1, 1)``; 0 for odd ``k``."""
    for n in (3, 5, 9):
        xi, w = gauss_rule_1d("legendre", n)
        assert abs(float(np.sum(w)) - 1.0) < 1e-13
        for k in range(0, 2 * n):
            got = float(np.sum(w * xi ** k))
            want = 0.0 if k % 2 else 1.0 / (k + 1)
            assert abs(got - want) < 1e-12


def test_bases_are_orthonormal_under_their_measure():
    """``E[phi_i phi_j] = delta_ij`` -- the property every spectral moment relies on."""
    idx = np.arange(12).reshape(-1, 1)          # exponents 0..11 in one dimension
    xi, w = gauss_rule_1d("legendre", 12)
    A = design_matrix(idx, np.column_stack([xi]), ["legendre"])
    gram = (A * w[:, None]).T @ A
    assert np.allclose(gram, np.eye(A.shape[1]), atol=1e-11)

    z, w = gauss_rule_1d("hermite", 12)
    A = design_matrix(idx, np.column_stack([z]), ["hermite"])
    gram = (A * w[:, None]).T @ A
    assert np.allclose(gram, np.eye(A.shape[1]), atol=1e-10)


def test_smolyak_grid_is_exact_for_its_declared_total_degree():
    """Level ``L`` must integrate every polynomial of total degree ``<= 2L-1``."""
    fams = ("legendre",) * 6
    for level in (2, 3):
        grid = smolyak_grid(fams, level)
        assert abs(float(np.sum(grid.weights)) - 1.0) < 1e-12
        max_deg = 2 * level - 1
        for alpha in itertools.product(range(max_deg + 1), repeat=6):
            if sum(alpha) > max_deg:
                continue
            want = 1.0
            for a in alpha:
                want *= 0.0 if a % 2 else 1.0 / (a + 1)
            got = float(np.sum(grid.weights * np.prod(grid.points ** np.array(alpha), axis=1)))
            assert abs(got - want) < 1e-12, (level, alpha, got, want)


# --------------------------------------------------------------------------- #
# 2. the surrogate reproduces what it should reproduce
# --------------------------------------------------------------------------- #
def test_projection_recovers_a_polynomial_exactly():
    """A degree-2 response on ``U(-1,1)^2`` has closed-form mean and variance."""
    marginals = (Marginal.uniform("a", -1.0, 1.0), Marginal.uniform("b", -1.0, 1.0))

    def f(a, b):
        return 1.0 + 2.0 * a - 3.0 * b ** 2 + a * b

    pce = fit_projection(f, marginals, order=2)
    # mean = 1 + 0 - 3*(1/3) + 0 ; variance = Var(2a) + Var(-3b^2) + Var(ab)
    var_b2 = 1.0 / 5.0 - (1.0 / 3.0) ** 2
    want_var = 4.0 / 3.0 + 9.0 * var_b2 + (1.0 / 3.0) * (1.0 / 3.0)
    assert abs(pce.mean - 0.0) < 1e-12
    assert abs(pce.variance - want_var) < 1e-12

    # and it interpolates the response itself to machine precision
    rng = np.random.default_rng(0)
    X = rng.uniform(-1.0, 1.0, size=(50, 2))
    assert np.allclose(pce.predict_physical(X), f(X[:, 0], X[:, 1]), atol=1e-11)


def test_regression_recovers_a_polynomial_exactly():
    marginals = (Marginal.uniform("a", -1.0, 1.0), Marginal.uniform("b", -1.0, 1.0))

    def f(a, b):
        return 0.5 - a + 2.0 * a * b

    pce = fit_regression(f, marginals, order=2, n_samples=40, seed=3)
    assert abs(pce.mean - 0.5) < 1e-10
    assert pce.design["loo_relative_residual"] < 1e-10


def test_first_order_indices_sum_to_one_for_an_additive_model():
    marginals = (Marginal.uniform("a", 0.0, 1.0), Marginal.uniform("b", 0.0, 1.0),
                 Marginal.uniform("c", 0.0, 1.0))

    def f(a, b, c):
        return a + 2.0 * b + 0.5 * c

    pce = fit_projection(f, marginals, order=1)
    sobol = pce.sobol()
    assert abs(sobol["sum_first_order"] - 1.0) < 1e-12
    # variance shares of a + 2b + 0.5c are 1 : 4 : 0.25
    tot = 1.0 + 4.0 + 0.25
    assert abs(sobol["first_order"]["a"] - 1.0 / tot) < 1e-12
    assert abs(sobol["first_order"]["b"] - 4.0 / tot) < 1e-12
    assert abs(sobol["first_order"]["c"] - 0.25 / tot) < 1e-12


# --------------------------------------------------------------------------- #
# 3. analytic Sobol' references
# --------------------------------------------------------------------------- #
def test_ishigami_closed_form_is_consistent_with_a_direct_variance_decomposition():
    ref = ishigami_closed_form(a=7.0, b=0.1)
    rng = np.random.default_rng(11)
    U = (rng.random((4_000_000, 3)) - 0.5) * 2.0 * math.pi
    y = np.array([ishigami(row) for row in U[:200_000]])
    # Monte Carlo sanity of the closed-form mean/variance (loose, sampling error)
    assert abs(y.mean() - ref["mean"]) < 0.05
    assert abs(y.var() - ref["variance"]) / ref["variance"] < 0.05


def test_ishigami_sobol_indices_error_below_five_percent():
    case = CaseSpec.load(CASE_DIR / "ishigami_sobol.json")
    func = case.model.load()
    marginals = case.marginals
    pce = fit_projection(func, marginals, order=int(case.pce["order"]),
                         call_style=case.model.call_style)
    ref = ishigami_closed_form()
    sobol = pce.sobol()
    errs = [
        abs(sobol[bucket][name] - value)
        for bucket in ("first_order", "total_order")
        for name, value in ref[bucket].items()
    ]
    assert max(errs) < 0.05, errs
    # the discriminating feature: x3 has no first-order effect but does interact
    assert sobol["first_order"]["x3"] < 0.01
    assert sobol["total_order"]["x3"] > 0.2
    # ordering of the dominant inputs must also be right
    assert sobol["first_order"]["x2"] > sobol["first_order"]["x1"] > sobol["first_order"]["x3"]


def test_ishigami_spectral_sobol_agrees_with_the_pick_freeze_estimator():
    """Independent second opinion from the Saltelli/Jansen estimator module."""
    from backend.services.global_sensitivity import sobol_indices as saltelli

    case = CaseSpec.load(CASE_DIR / "ishigami_sobol.json")
    func = case.model.load()
    pce = fit_projection(func, case.marginals, order=int(case.pce["order"]),
                         call_style=case.model.call_style)
    spectral = pce.sobol()
    res = saltelli(lambda X: np.array([ishigami(row) for row in np.atleast_2d(X)]),
                   bounds=[(-math.pi, math.pi)] * 3, n_base=4096, seed=5,
                   names=["x1", "x2", "x3"])
    est_first = dict(zip(res.names, np.asarray(res.first_order, dtype=float)))
    for name, value in spectral["first_order"].items():
        assert abs(est_first[name] - value) < 0.05, (name, est_first[name], value)


def test_sobol_g_spectral_indices_match_the_closed_form():
    """The ``|4x-2|`` kink makes this a convergence test, not a tautology."""
    a = (0.0, 0.5, 9.0)
    marginals = tuple(Marginal.uniform(f"x{i + 1}", 0.0, 1.0) for i in range(len(a)))
    model = lambda **kw: sobol_g([kw[f"x{i + 1}"] for i in range(len(a))], a)
    ref = sobol_g_closed_form(a)

    def max_err(order, n):
        pce = fit_regression(model, marginals, order=order, n_samples=n, seed=1)
        sobol = pce.sobol()
        return max(abs(sobol[b][nme] - v) for b in ("first_order", "total_order")
                   for nme, v in ref[b].items()), sobol

    coarse, _ = max_err(3, 200)
    fine, sobol = max_err(4, 300)
    assert fine < 0.05, fine
    assert coarse > fine                      # refinement must actually buy accuracy
    # the dominant input is the one with a = 0 and the inert one stays inert
    assert sobol["first_order"]["x1"] > sobol["first_order"]["x2"] > sobol["first_order"]["x3"]


# --------------------------------------------------------------------------- #
# 4. the engineering case: six-parameter propagation + acceleration
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def resistance_case():
    return CaseSpec.load(CASE_DIR / "resistance_six_parameter.json")


def test_resistance_case_moments_match_monte_carlo_and_surrogate_is_fast(resistance_case):
    case = resistance_case
    func = case.model.load()
    marginals = case.marginals
    pce = fit_projection(func, marginals, order=int(case.pce["order"]),
                         level=int(case.pce["level"]), output=case.model.output,
                         fixed=case.model.fixed)
    mc = monte_carlo(func, marginals, n=10000, sampler="lhs", seed=20261110,
                     output=case.model.output, fixed=case.model.fixed)
    y = pce.sample_output(n=40000, seed=7)
    assert abs(np.mean(y) - mc.mean) / abs(mc.mean) < 0.03
    assert abs(np.std(y, ddof=1) - mc.std) / mc.std < 0.03
    # acceleration in black-box evaluations (the criterion that does not depend
    # on machine speed): the quadratic sparse-projection surrogate uses 13 runs
    assert 10000 / pce.n_model_evaluations > 100
    # physically meaningful ranking: speed dominates, then block coefficient
    sobol = pce.sobol()
    order = sorted(sobol["first_order"], key=sobol["first_order"].get, reverse=True)
    assert order[0] == "speed_ms"
    assert order[1] == "cb"


def test_resistance_case_band_is_consistent_between_surrogate_and_monte_carlo(resistance_case):
    case = resistance_case
    func = case.model.load()
    marginals = case.marginals
    pce = fit_projection(func, marginals, order=int(case.pce["order"]),
                         level=int(case.pce["level"]), output=case.model.output)
    mc = monte_carlo(func, marginals, n=10000, sampler="lhs", seed=20261110,
                     output=case.model.output)
    band_pce = pce.quantiles((0.05, 0.95))
    band_mc = {"p5": float(np.quantile(mc.samples, 0.05)),
               "p95": float(np.quantile(mc.samples, 0.95))}
    for key in band_mc:
        assert abs(band_pce[key] - band_mc[key]) / abs(band_mc[key]) < 0.03


# --------------------------------------------------------------------------- #
# 5. marginals, determinism and the domain-free rule
# --------------------------------------------------------------------------- #
def test_marginal_transforms_are_consistent():
    for marginal in (
        Marginal.uniform("u", -2.0, 5.0),
        Marginal.normal("n", 10.0, 1.5),
        Marginal.lognormal("l", 3.0, 0.2),
    ):
        u = np.linspace(0.001, 0.999, 2001)
        x = marginal.from_unit(u)
        z = marginal.to_standard(x)
        # same quantile ladder in the standard variable (uniform -> [-1,1],
        # normal/lognormal -> N(0,1) with the truncation inheriting the CDF)
        assert np.all(np.diff(z) > 0)
        if marginal.family == "uniform":
            assert abs(z[0] + 1.0) < 5e-3 and abs(z[-1] - 1.0) < 5e-3
        mean, std = marginal.moments()
        assert abs(float(np.mean(x)) - mean) / max(abs(mean), 1e-12) < 0.02
        assert abs(float(np.std(x, ddof=1)) - std) / std < 0.02


def test_pce_fit_is_deterministic():
    marginals = (Marginal.uniform("a", -1.0, 1.0), Marginal.uniform("b", -1.0, 1.0))

    def f(a, b):
        return np.sin(a) + 0.3 * b ** 2

    p1 = fit_projection(f, marginals, order=4)
    p2 = fit_projection(f, marginals, order=4)
    assert np.array_equal(p1.coef, p2.coef)
    r1 = fit_regression(f, marginals, order=3, n_samples=40, seed=9)
    r2 = fit_regression(f, marginals, order=3, n_samples=40, seed=9)
    assert np.array_equal(r1.coef, r2.coef)


def test_multi_index_truncation_counts_and_shapes():
    assert multi_index_set(2, 2).shape[0] == 6      # 1 + 2 + 3
    assert multi_index_set(3, 8).shape[0] == 165    # C(11, 3)
    assert multi_index_set(6, 3).shape[0] == 84     # C(9, 6)
    hyperbolic = multi_index_set(6, 3, q_norm=0.5)
    assert hyperbolic.shape[0] <= multi_index_set(6, 3).shape[0]


def test_kernel_and_runner_carry_no_domain_vocabulary():
    """Platform rule: the mechanism must stay domain free (words live in config)."""
    from tools.audit.domain_leak_scan import scan

    summary = scan(paths=["backend/services/pce_uq", "tools/uq_bench"])
    assert summary["hit_count"] == 0, summary


def test_case_files_are_machine_readable_and_thresholded():
    for name in ("ishigami_sobol", "resistance_six_parameter"):
        doc = json.loads((CASE_DIR / f"{name}.json").read_text())
        assert doc["criteria"], name
        assert all(isinstance(v, (int, float)) for v in doc["criteria"].values())
        case = CaseSpec.load(CASE_DIR / f"{name}.json")
        assert len(case.marginals) == len(doc["inputs"])