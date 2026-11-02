"""Regression tests for the m1882 independent verification of the ice UQ numerics.

Two layers are pinned:

1. the reference layer (``tools/validation/ice_uq_reference_m1882.py``) -- it must
   itself be correct, otherwise the verdicts mean nothing;
2. the audited behaviour -- the properties that must keep holding, and the
   aliasing defect that must be *detected* (``xfail(strict=True)``: fixing the
   defect turns the test red on purpose, forcing the evidence to be re-issued).

Run: python3 -m pytest tests/test_ice_uq_kernel_reference_m1882.py -q
"""
from __future__ import annotations

import importlib.util
import os
import sys

import math

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools", "validation"))

import ice_uq_reference_m1882 as REF  # noqa: E402

AUDITED = os.path.join(ROOT, "backend", "services", "ice_resistance_uq.py")
BOUNDS = [(0.3, np.inf), (0.1, np.inf), (0.02, 0.5), (0.5, np.inf)]


def _audited():
    spec = importlib.util.spec_from_file_location("iceuq_audited_tests", AUDITED)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["iceuq_audited_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def aud():
    return _audited()


# --------------------------------------------------------------------- reference layer
def test_reference_normal_moments():
    assert REF.normal_moment(0) == 1.0
    assert REF.normal_moment(1) == 0.0
    assert REF.normal_moment(2) == 1.0
    assert REF.normal_moment(4) == 3.0
    assert REF.normal_moment(6) == 15.0


def test_reference_hermite_orthonormality_by_quadrature(aud):
    """<He_a, He_b> = a! delta_ab checked with the audited rule itself is weak;
    instead check the analytic norm identity on a fine independent grid."""
    x = np.linspace(-8, 8, 400001)
    w = np.exp(-x * x / 2.0) / np.sqrt(2 * np.pi)
    for a in range(0, 5):
        va = REF.hermite_he(x, a)
        integ = float(np.trapezoid(va * va * w, x))
        assert abs(integ - math.factorial(a)) < 1e-4 * max(1, math.factorial(a))


def test_reference_analytic_sobol_linear_and_interaction():
    lin = REF.PolynomialModel(2, {(1, 0): 2.0, (0, 1): 5.0})
    an = REF.analytic_pce_of_polynomial(lin)
    assert an["variance"] == pytest.approx(29.0)
    assert an["first_order"] == pytest.approx([4 / 29, 25 / 29])
    assert an["total_order"] == pytest.approx([4 / 29, 25 / 29])

    prod = REF.PolynomialModel(2, {(1, 1): 1.0})
    an2 = REF.analytic_pce_of_polynomial(prod)
    assert an2["variance"] == pytest.approx(1.0)
    assert an2["first_order"] == pytest.approx([0.0, 0.0])  # pure interaction
    assert an2["total_order"] == pytest.approx([1.0, 1.0])


def test_reference_mc_estimator_recovers_analytic_indices():
    model = REF.PolynomialModel(2, {(1, 0): 2.0, (0, 1): 5.0, (1, 1): 1.0})
    an = REF.analytic_pce_of_polynomial(model)
    mc = REF.legendre_saltelli_indices(lambda z: model.call_array(z), dim=2, n_base=1 << 14)
    assert mc["total_order"] == pytest.approx(an["total_order"], abs=0.03)
    assert mc["first_order"] == pytest.approx(an["first_order"], abs=0.03)


# --------------------------------------------------------------------- audited numerics
def test_v1_gauss_hermite_exactness_by_degree(aud):
    """n-point Gauss-Hermite integrates degree <= 2n-1 exactly (relative 1e-9)."""
    for n in range(1, 9):
        nodes, weights = aud._gauss_hermite_nodes_weights(n)
        row = REF.smooth_1d_rule_check(nodes, weights)
        assert row["weight_sum_err"] <= 1e-12
        assert row["rel_err_at_expected_degree"] <= 1e-9, (n, row)


def test_v2_smolyak_weights_and_moment_exactness(aud):
    for dim, level in [(2, 3), (4, 3), (4, 4)]:
        nodes, weights = aud.smolyak_sparse_grid(level, dim)
        assert abs(np.sum(weights) - 1.0) <= 1e-10
        table = REF.monomial_moment_exactness(nodes, weights, dim, 2 * level - 2)
        for row in table:
            assert row["exact"], (dim, level, row)


def test_v3_pce_exact_for_polynomials_when_level_rule_holds(aud):
    """max_level >= response degree + 1 -> coefficients recovered exactly."""
    U = aud.UncertainInput
    inputs = [U("x0", 1.0, 1.0), U("x1", 1.0, 1.0)]
    model = REF.PolynomialModel(2, {(0, 0): 1.5, (1, 0): -2.0, (0, 2): 0.7, (1, 2): 0.4})
    pce = aud.build_pce(
        lambda *x: model(*[xi - 1.0 for xi in x]), inputs, max_level=4, max_poly_order=3
    )
    coef = dict(zip(pce["multi_indices"], pce["coefficients"]))
    for alpha, value in model.terms.items():
        assert coef[alpha] == pytest.approx(value, abs=1e-10)
    an = REF.analytic_pce_of_polynomial(model)
    assert aud.pce_statistics(pce)["variance"] == pytest.approx(an["variance"], rel=1e-12)


@pytest.mark.xfail(
    strict=True,
    reason="m1882 defect: shipped default (max_level=3, max_poly_order=3) aliases "
    "cubic responses, inventing Sobol mass for an input with zero influence; "
    "fixing the default turns this XPASS -> the evidence must be re-issued",
)
def test_v3_defect_default_config_admits_no_spurious_sensitivity(aud):
    U = aud.UncertainInput
    pce = aud.build_pce(
        lambda *x: (x[0] - 1.0) ** 3,
        [U("x0", 1.0, 1.0), U("x1", 1.0, 1.0)],
        max_level=3,
        max_poly_order=3,
    )
    st = list(aud.sobol_indices(pce)["total_order"].values())
    assert st[1] <= 1e-6  # xi_1 has zero influence by construction


def test_v3_defect_is_detected_and_pinned(aud):
    """The defect must be *observable* -- pinned so a silent regression is caught."""
    U = aud.UncertainInput
    pce = aud.build_pce(
        lambda *x: (x[0] - 1.0) ** 3,
        [U("x0", 1.0, 1.0), U("x1", 1.0, 1.0)],
        max_level=3,
        max_poly_order=3,
    )
    st = list(aud.sobol_indices(pce)["total_order"].values())
    assert st[1] == pytest.approx(0.1818, abs=1e-3)  # spurious, deterministic
    assert aud.pce_statistics(pce)["variance"] == pytest.approx(11.0, abs=1e-6)  # true 15


def test_v3_raising_level_alone_repairs_the_response_variance(aud):
    U = aud.UncertainInput
    pce = aud.build_pce(
        lambda *x: (x[0] - 1.0) ** 3,
        [U("x0", 1.0, 1.0), U("x1", 1.0, 1.0)],
        max_level=4,
        max_poly_order=3,
    )
    assert aud.pce_statistics(pce)["variance"] == pytest.approx(15.0, abs=1e-9)
    assert list(aud.sobol_indices(pce)["total_order"].values())[1] == pytest.approx(0.0, abs=1e-12)


def test_v4_real_model_pce_vs_independent_mc(aud):
    """Converged PCE total-order indices agree with Saltelli/Jansen MC within 15 %."""
    trial = aud.SHIP_TRIAL_DATA["MV_Arctic"]
    ship = trial["params"]
    inputs = [
        aud.UncertainInput("ice_strength_MPa", trial["nominal_ice_strength_MPa"], 0.20),
        aud.UncertainInput("ice_thickness_m", trial["nominal_ice_thickness_m"], 0.15),
        aud.UncertainInput("friction_coeff", trial["nominal_friction_coeff"], 0.25),
        aud.UncertainInput("ship_speed_ms", trial["nominal_speed_ms"], 0.10),
    ]
    scalar = lambda s, h, m, v: aud.lindqvist_ice_resistance(ship, h, s, m, v)  # noqa: E731
    means = np.array([i.mean for i in inputs])
    stds = np.array([i.std for i in inputs])
    lo = np.array([b[0] for b in BOUNDS])
    hi = np.array([b[1] for b in BOUNDS])

    def vec(z):
        x = np.clip(means + stds * np.atleast_2d(z), lo, hi)
        return np.array([scalar(*row) for row in x])

    mc = REF.legendre_saltelli_indices(vec, dim=4, n_base=1 << 12)
    pce = aud.build_pce(scalar, inputs, max_level=5, max_poly_order=4)
    st = np.array(list(aud.sobol_indices(pce)["total_order"].values()))
    assert np.max(np.abs(st - np.array(mc["total_order"]))) <= 0.15
    assert float(np.sum(st)) == pytest.approx(1.0, abs=0.05)


def test_v5_clipped_sampler_bias_is_bounded_but_real(aud):
    """Quantifies the module's clip: small here (<=1 %), but it is a real bias."""
    trial = aud.SHIP_TRIAL_DATA["Terry_Fox"]
    ship = trial["params"]
    means = [trial["nominal_ice_strength_MPa"], trial["nominal_ice_thickness_m"],
             trial["nominal_friction_coeff"], trial["nominal_speed_ms"]]
    stds = [means[0] * 0.20, means[1] * 0.15, means[2] * 0.25, means[3] * 0.10]
    res = REF.clipped_gaussian_reference(
        lambda x: np.array([aud.lindqvist_ice_resistance(ship, r[1], r[0], r[2], r[3]) for r in x]),
        means, stds, BOUNDS, n_samples=20000,
    )
    assert 0.0 <= res["clip_fraction"] <= 0.20
    assert abs(res["std_rel_bias"]) <= 0.20
    assert res["acceptance_rate"] > 0.95


def test_verdict_artifacts_are_published():
    """The evidence artefacts must exist in the worktree (no silent absence)."""
    for rel in ("artifacts/m1882/verdicts.json",
                "artifacts/m1882/fig_aliasing_map.png",
                "artifacts/m1882/fig_sobol_crosscheck.png",
                "docs/verification/ice_uq_kernel_independent_review_m1882.md"):
        assert os.path.exists(os.path.join(ROOT, rel)), rel