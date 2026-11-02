"""Analytic-benchmark regression tests for the PCE / quadrature / Sobol stack.

Every reference used here is a closed-form value (Gauss-Hermite moments,
orthogonality norms, Ishigami 1990, Sobol g-function) computed inside
``tools/pce_verify/analytic_benchmarks.py`` -- none is read back from the code
under test, so the tests can fail.

Covers the two engines:

* ``backend/services/ice_resistance_uq.py`` (Hermite / Smolyak projection)
* ``backend/services/digital_twin/pce.py``   (Legendre least squares + Sobol)
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from backend.services.digital_twin.pce import PolynomialChaos
from backend.services.ice_resistance_uq import (
    UncertainInput,
    _evaluate_pce_basis,
    _gauss_hermite_nodes_weights,
    build_pce,
    pce_statistics,
    required_quadrature_level,
    smolyak_sparse_grid,
    sobol_indices,
)
from tools.pce_verify.analytic_benchmarks import (
    g_function,
    g_function_analytic,
    gauss_hermite_moment,
    hermite_model_coefficients,
    hermite_model_indices,
    ishigami,
    ishigami_analytic,
    legendre_model_coefficients,
    legendre_model_indices,
    rel_error,
)

MACHINE = 1e-10


# ----------------------------------------------------------------------
# A. Gauss-Hermite quadrature
# ----------------------------------------------------------------------

@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6])
def test_gauss_hermite_is_exact_to_degree_2n_minus_1(n):
    nodes, weights = _gauss_hermite_nodes_weights(n)
    assert len(nodes) == n
    assert weights.sum() == pytest.approx(1.0, abs=MACHINE)
    assert np.all(weights > 0.0)
    assert np.allclose(nodes, -nodes[::-1], atol=1e-12)  # symmetric about 0
    for k in range(0, 2 * n):
        approx = float(np.sum(weights * nodes ** k))
        assert approx == pytest.approx(gauss_hermite_moment(k), abs=MACHINE), f"n={n} k={k}"


def test_gauss_hermite_beyond_declared_degree_is_not_exact():
    """Guard against a trivially "perfect" quadrature (e.g. a wrong reference)."""
    nodes, weights = _gauss_hermite_nodes_weights(2)
    approx = float(np.sum(weights * nodes ** 4))
    assert approx != pytest.approx(gauss_hermite_moment(4), rel=1e-6)


# ----------------------------------------------------------------------
# B. Smolyak sparse grid
# ----------------------------------------------------------------------

@pytest.mark.parametrize("dim", [2, 3])
@pytest.mark.parametrize("level", [2, 3, 4])
def test_smolyak_partition_of_unity(dim, level):
    nodes, weights = smolyak_sparse_grid(level, dim)
    assert weights.sum() == pytest.approx(1.0, abs=MACHINE)
    assert len(nodes) == len(weights)


@pytest.mark.parametrize("dim", [2, 3])
@pytest.mark.parametrize("level", [2, 3])
def test_smolyak_exact_to_total_degree_2L_minus_1(dim, level):
    nodes, weights = smolyak_sparse_grid(level, dim)
    deg = 2 * level - 1
    for k in range(0, deg + 1):
        approx = float(np.sum(weights * nodes[:, 0] ** k))
        assert approx == pytest.approx(gauss_hermite_moment(k), abs=MACHINE), f"k={k}"
    if deg >= 4:
        mixed = float(np.sum(weights * nodes[:, 0] ** 2 * nodes[:, 1] ** 2))
        assert mixed == pytest.approx(1.0, abs=MACHINE)


def test_smolyak_rules_have_negative_weights():
    """Documented property, not a defect: the combination technique on
    non-nested Gauss rules produces signed weights (min -2 at dim=3, level=2).
    A positive-weight assumption (e.g. for MC-free error bars) would be wrong."""
    _, weights = smolyak_sparse_grid(3, 3)
    assert np.any(weights < 0.0)
    assert weights.min() == pytest.approx(-1.0, abs=1e-12)


# ----------------------------------------------------------------------
# C. ice_resistance_uq projection PCE + Sobol, vs closed-form Hermite model
# ----------------------------------------------------------------------

def _hermite_model(dim):
    coeff = hermite_model_coefficients(dim)

    def f_std(*x):
        return float(sum(c * _evaluate_pce_basis(np.asarray(x, float), a)
                         for a, c in coeff.items()))

    return coeff, f_std


def _inputs(dim):
    # UncertainInput.std == nominal * std_relative, so a zero-mean input is not
    # expressible; use mean 1 / std 1 and shift the model into standard space.
    return [UncertainInput(f"x{i+1}", nominal=1.0, std_relative=1.0) for i in range(dim)]


def test_required_quadrature_level_rule():
    assert required_quadrature_level(0) == 1
    assert required_quadrature_level(3) == 4
    assert required_quadrature_level(4) == 5


@pytest.mark.parametrize("dim", [2, 3])
def test_projection_pce_is_exact_when_level_coupled(dim):
    coeff, f_std = _hermite_model(dim)
    ref = hermite_model_indices(coeff, dim)
    pce = build_pce(lambda *x: f_std(*[xi - 1.0 for xi in x]), _inputs(dim),
                    max_level=3, max_poly_order=3)  # level 3 < required 4
    assert pce["max_level"] == 4
    assert pce["level_auto_raised"] is True
    assert pce["under_integrated"] is False

    stats = pce_statistics(pce)
    assert rel_error(stats["variance"], ref["variance"]) <= MACHINE
    names = [f"x{i+1}" for i in range(dim)]
    sob = sobol_indices(pce)
    for i, name in enumerate(names):
        assert sob["first_order"][name] == pytest.approx(ref["first_order"][i], abs=1e-9)
        assert sob["total_order"][name] == pytest.approx(ref["total_order"][i], abs=1e-9)


def test_under_integrated_projection_is_flagged_and_wrong():
    """Regression test for the defect found by this verification:
    level 3 with total order 3 under-integrates (exactness 5 < 6)."""
    dim = 3
    coeff, f_std = _hermite_model(dim)
    ref = hermite_model_indices(coeff, dim)
    pce = build_pce(lambda *x: f_std(*[xi - 1.0 for xi in x]), _inputs(dim),
                    max_level=3, max_poly_order=3, allow_under_integrated=True)
    assert pce["under_integrated"] is True
    assert pce["level_auto_raised"] is False
    hidden = max(rel_error(pce["coefficients"][j], coeff[tuple(a)])
                 for a, j in (((a), j) for j, a in enumerate(pce["multi_indices"])
                              if tuple(a) in coeff))
    assert hidden > 0.5  # coefficients are not marginally off -- they are wrong
    sob = sobol_indices(pce)
    names = [f"x{i+1}" for i in range(dim)]
    assert max(rel_error(sob["first_order"][n], ref["first_order"][i])
               for i, n in enumerate(names)) > 0.5


def test_sobol_bounds_hold_for_exact_projection():
    dim = 3
    coeff, f_std = _hermite_model(dim)
    pce = build_pce(lambda *x: f_std(*[xi - 1.0 for xi in x]), _inputs(dim),
                    max_level=4, max_poly_order=3)
    sob = sobol_indices(pce)
    assert sum(sob["first_order"].values()) <= 1.0 + 1e-12
    assert sum(sob["total_order"].values()) >= 1.0 - 1e-12


# ----------------------------------------------------------------------
# D. digital_twin Legendre engine: statistics() / sobol_indices()
# ----------------------------------------------------------------------

def _legendre_model(dim):
    coeff = legendre_model_coefficients(dim)

    def func(X):
        X = np.atleast_2d(np.asarray(X, float))
        out = np.zeros(X.shape[0])
        for a, c in coeff.items():
            col = np.ones(X.shape[0])
            for j, k in enumerate(a):
                col = col * np.polynomial.legendre.Legendre.basis(k)(X[:, j])
            out += c * col
        return out

    return coeff, func


def test_legendre_engine_statistics_and_sobol_are_exact():
    dim = 3
    coeff, func = _legendre_model(dim)
    ref = legendre_model_indices(coeff, dim)
    pce = PolynomialChaos([f"x{i+1}" for i in range(dim)], [-1.0] * dim, [1.0] * dim, degree=4)
    pce.fit(func, n_samples=400, seed=7)

    stats = pce.statistics()["y0"]
    assert rel_error(stats["variance"], ref["variance"]) <= MACHINE
    assert stats["mean"] == pytest.approx(ref["mean"], abs=1e-12)
    assert stats["std"] == pytest.approx(math.sqrt(ref["variance"]), rel=1e-10)

    sob = pce.sobol_indices()["y0"]
    for i in range(dim):
        name = f"x{i+1}"
        assert sob["first_order"][name] == pytest.approx(ref["first_order"][i], abs=1e-9)
        assert sob["total_order"][name] == pytest.approx(ref["total_order"][i], abs=1e-9)
    assert sob["sum_first_order"] <= 1.0 + 1e-12
    assert sob["sum_total_order"] >= 1.0 - 1e-12


def test_legendre_engine_sobol_zero_variance_model():
    """A constant surrogate must not divide by zero."""
    pce = PolynomialChaos(["x"], [0.0], [1.0], degree=2)
    pce.fit(lambda X: np.full(X.shape[0], 2.5), n_samples=50, seed=1)
    stats = pce.statistics()["y0"]
    assert stats["variance"] == pytest.approx(0.0, abs=1e-18)
    assert stats["mean"] == pytest.approx(2.5, rel=1e-9)
    sob = pce.sobol_indices()["y0"]
    assert sob["first_order"]["x"] == 0.0
    assert sob["total_order"]["x"] == 0.0


# ----------------------------------------------------------------------
# E/F. Literature benchmarks: Ishigami (1990) and Sobol's g-function
# ----------------------------------------------------------------------

def test_ishigami_analytic_reference_matches_literature():
    ref = ishigami_analytic()
    # published values (Ishigami & Homma 1990)
    assert ref["first_order"][0] == pytest.approx(0.3139, abs=5e-4)
    assert ref["first_order"][1] == pytest.approx(0.4424, abs=5e-4)
    assert ref["first_order"][2] == pytest.approx(0.0, abs=1e-12)
    assert ref["total_order"][0] == pytest.approx(0.5576, abs=5e-4)
    assert ref["total_order"][2] == pytest.approx(0.2436, abs=5e-4)


def test_ishigami_pce_recovers_analytic_indices():
    ref = ishigami_analytic()
    pi = math.pi
    pce = PolynomialChaos(["x1", "x2", "x3"], [-pi] * 3, [pi] * 3, degree=10)
    pce.fit(lambda X: np.array([ishigami(r) for r in X]), n_samples=1200, seed=11)
    sob = pce.sobol_indices()["y0"]
    stats = pce.statistics()["y0"]
    assert rel_error(stats["variance"], ref["variance"]) <= 5e-3
    for i, name in enumerate(["x1", "x2", "x3"]):
        assert sob["first_order"][name] == pytest.approx(ref["first_order"][i], abs=5e-3)
        assert sob["total_order"][name] == pytest.approx(ref["total_order"][i], abs=5e-3)


def test_g_function_pce_recovers_analytic_indices():
    a = (0.0, 0.5, 1.0, 3.0, 9.0)
    ref = g_function_analytic(a)
    dim = len(a)
    pce = PolynomialChaos([f"x{i+1}" for i in range(dim)], [0.0] * dim, [1.0] * dim, degree=7)
    pce.fit(lambda X: np.array([g_function(r, a) for r in X]), n_samples=3000, seed=13)
    sob = pce.sobol_indices()["y0"]
    for i in range(dim):
        name = f"x{i+1}"
        assert sob["first_order"][name] == pytest.approx(ref["first_order"][i], abs=3e-2)
        assert sob["total_order"][name] == pytest.approx(ref["total_order"][i], abs=3e-2)


# ----------------------------------------------------------------------
# G. production configuration of the ice-resistance UQ chain
# ----------------------------------------------------------------------

def test_ice_chain_default_uses_coupled_level():
    """run_ice_resistance_uq must not ship an under-integrated projection."""
    from backend.services.ice_resistance_uq import run_ice_resistance_uq

    res = run_ice_resistance_uq("MV_Arctic", n_mc_validation=200, n_mc_reference=2000,
                                max_level=3, max_poly_order=3)
    # 4 uncertain inputs, order 3 -> level 4 -> 165 quadrature points
    assert res.n_model_evaluations == 165
    assert res.sobol_sum_first_order <= 1.0 + 0.05
    assert res.sobol_sum_total_order >= 1.0 - 0.05