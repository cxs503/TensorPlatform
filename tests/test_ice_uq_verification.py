"""Verification gate for the ice-resistance PCE/UQ engine (mission m1972).

The engine (``backend/services/ice_resistance_uq.py``) had **no** regression
test before this file: ``grep -rl ice_resistance_uq tests backend/tests``
returned nothing, so nothing could distinguish a correct spectral projection
from an aliased one.  Every assertion below is anchored on a closed form, not
on the engine's own output:

* exact Gaussian / uniform moments for the quadrature rules,
* analytic PCE statistics for manufactured polynomials,
* an independent tensor-Gauss projection (``tools/verif/uq/anchors.py``) for
  the real Lindqvist ice-resistance model.

The three ``xfail(strict=True)`` cases pin *open defects*: they assert the
behaviour a correct engine must have, so the day the defect is fixed the test
suite turns XPASS -> FAIL and the marker has to be removed deliberately.  That
is the machine-checkable form of the debt ledger.

Run: ``python3 -m pytest tests/test_ice_uq_verification.py -q``
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

WORKTREE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKTREE / "backend"))
sys.path.insert(0, str(WORKTREE))

from services import ice_resistance_uq as U  # noqa: E402

from tools.verif.uq import anchors as A  # noqa: E402


SHIP = "MV_Arctic"


# ---------------------------------------------------------------------------
# reference layer self-checks (the yardstick must itself be exact)
# ---------------------------------------------------------------------------

def test_reference_gauss_hermite_exact_to_machine_precision():
    for n in range(1, 7):
        nodes, weights = A.gh_nodes_weights(n)
        assert abs(weights.sum() - 1.0) < 1e-13
        for k in range(2 * n):
            got = float(np.sum(weights * nodes ** k))
            assert abs(got - A.normal_moment(k)) < 1e-9 * max(1.0, A.normal_moment(k))


def test_reference_gauss_legendre_exact_to_machine_precision():
    for n in range(1, 7):
        nodes, weights = A.gl_nodes_weights(n)
        assert abs(0.5 * weights.sum() - 1.0) < 1e-13
        for k in range(2 * n):
            got = 0.5 * float(np.sum(weights * nodes ** k))
            assert abs(got - A.uniform_moment(k)) < 1e-9 * max(1.0, A.uniform_moment(k))


def test_reference_pce_reproduces_analytic_statistics():
    ins = [A.ReferenceInput("x1", "normal", 1.0, 1.0), A.ReferenceInput("x2", "normal", 1.0, 1.0)]
    cases = [
        (A.manufactured_linear(ins), 0.0, 2.0, 0.5),
        (A.manufactured_quadratic(ins), 2.0, 4.0, 0.5),
        (A.manufactured_interaction(ins), 0.0, 3.0, 1.0 / 3.0),
    ]
    for model, mean, var, s1 in cases:
        ref = A.ReferencePCE(ins, max_order=3).fit(model)
        st, sb = ref.statistics(), ref.sobol()
        assert abs(st["mean"] - mean) < 1e-9
        assert abs(st["variance"] - var) < 1e-9
        assert abs(sb["first_order"]["x1"] - s1) < 1e-9


def test_reference_pce_uniform_statistics():
    """X ~ U(-1,1): E[X²] = 1/3, Var[X²] = 1/5 - 1/9."""
    ins = [A.ReferenceInput("u1", "uniform", 0.0, 1.0 / math.sqrt(3.0))]
    ref = A.ReferencePCE(ins, max_order=3).fit(lambda a: a ** 2)
    st = ref.statistics()
    assert abs(st["mean"] - 1.0 / 3.0) < 1e-9
    assert abs(st["variance"] - (0.2 - 1.0 / 9.0)) < 1e-9


def test_required_level_rule():
    """A total-order-p projection integrates degree 2p -> needs level >= p+1."""
    for p in range(0, 6):
        assert A.required_sparse_level(p) == p + 1
        assert A.sparse_exact_degree(p + 1) == 2 * p + 1


# ---------------------------------------------------------------------------
# anchor group 1: the engine's quadrature rules
# ---------------------------------------------------------------------------

def test_engine_gauss_hermite_is_exact():
    for n in range(1, 7):
        nodes, weights = U._gauss_hermite_nodes_weights(n)
        assert abs(float(weights.sum()) - 1.0) < 1e-12
        for k in range(2 * n):
            got = float(np.sum(weights * nodes ** k))
            assert abs(got - A.normal_moment(k)) < 1e-9 * max(1.0, A.normal_moment(k))


@pytest.mark.parametrize("dim,level", [(2, 2), (2, 3), (3, 3), (4, 3), (4, 4)])
def test_engine_smolyak_weight_sum_and_exactness(dim, level):
    nodes, weights = U.smolyak_sparse_grid(level, dim)
    assert abs(float(weights.sum()) - 1.0) < 1e-12
    # total degree <= 2q+1 is integrated exactly (q = level-1)
    deg = A.sparse_exact_degree(level)
    for alpha in _multi_indices_exact_degree(deg, dim):
        got = float(np.sum(weights * np.prod(nodes ** np.array(alpha), axis=1)))
        ref = 1.0
        for a in alpha:
            ref *= A.normal_moment(a)
        assert abs(got - ref) < 1e-10 * max(1.0, abs(ref))


def _multi_indices_exact_degree(total: int, dim: int):
    out = []

    def rec(prefix, remaining, slots):
        if slots == 1:
            out.append(tuple(prefix + [remaining]))
            return
        for v in range(remaining + 1):
            rec(prefix + [v], remaining - v, slots - 1)

    rec([], total, dim)
    return out


# ---------------------------------------------------------------------------
# anchor group 2: PCE statistics against analytic values (Gaussian inputs)
# ---------------------------------------------------------------------------

def test_engine_pce_manufactured_models_match_analytic():
    ins = [U.UncertainInput("x1", 1.0, 1.0), U.UncertainInput("x2", 1.0, 1.0)]
    cases = {
        "linear": (lambda a, b: (a - 1) + (b - 1), 0.0, 2.0),
        "quadratic": (lambda a, b: (a - 1) ** 2 + (b - 1) ** 2, 2.0, 4.0),
        "interaction": (lambda a, b: (a - 1) + (b - 1) + (a - 1) * (b - 1), 0.0, 3.0),
    }
    for name, (model, mean, var) in cases.items():
        pce = U.build_pce(model, ins, max_level=3, max_poly_order=3)
        st = U.pce_statistics(pce)
        assert abs(st["mean"] - mean) < 1e-9, name
        assert abs(st["variance"] - var) < 1e-9, name


def test_engine_pce_sobol_matches_analytic():
    """f = z1 + z2 + z1*z2: S1 = S2 = 1/3, S12 = 1/3, ST1 = ST2 = 2/3."""
    ins = [U.UncertainInput("x1", 1.0, 1.0), U.UncertainInput("x2", 1.0, 1.0)]
    pce = U.build_pce(lambda a, b: (a - 1) + (b - 1) + (a - 1) * (b - 1),
                      ins, max_level=3, max_poly_order=3)
    sb = U.sobol_indices(pce)
    assert abs(sb["first_order"]["x1"] - 1.0 / 3.0) < 1e-9
    assert abs(sb["first_order"]["x2"] - 1.0 / 3.0) < 1e-9
    assert abs(sb["total_order"]["x1"] - 2.0 / 3.0) < 1e-9
    assert abs(sum(sb["first_order"].values()) + 1.0 / 3.0 - 1.0) < 1e-9


def test_engine_pce_surrogate_reproduces_polynomial():
    ins = [U.UncertainInput("x1", 1.0, 1.0), U.UncertainInput("x2", 1.0, 1.0)]
    pce = U.build_pce(lambda a, b: (a - 1) + (b - 1) + (a - 1) * (b - 1),
                      ins, max_level=3, max_poly_order=3)
    rng = np.random.default_rng(7)
    for z in rng.standard_normal((100, 2)):
        exact = z[0] + z[1] + z[0] * z[1]
        assert abs(U.evaluate_pce(pce, z) - exact) < 1e-9


# ---------------------------------------------------------------------------
# anchor group 3: the real Lindqvist ice model vs an independent projection
# ---------------------------------------------------------------------------

def _engine_inputs():
    trial = U.SHIP_TRIAL_DATA[SHIP]
    return trial, [
        U.UncertainInput("ice_strength_MPa", trial["nominal_ice_strength_MPa"], 0.20),
        U.UncertainInput("ice_thickness_m", trial["nominal_ice_thickness_m"], 0.15),
        U.UncertainInput("friction_coeff", trial["nominal_friction_coeff"], 0.25),
        U.UncertainInput("ship_speed_ms", trial["nominal_speed_ms"], 0.10),
    ]


def test_ice_resistance_pce_matches_independent_projection():
    """Correctly configured PCE must agree with the reference within 3 %.

    This is the acceptance criterion of the mission: mean and standard
    deviation of level-ice resistance for MV Arctic, engine (max_level = 4,
    order 3) vs an independent 4^4-node tensor Gauss projection.
    """
    trial, ins = _engine_inputs()
    ship = trial["params"]

    def model(s, h, f, v):
        return U.lindqvist_ice_resistance(ship, h, s, f, v)

    ref_ins = [A.ReferenceInput(i.name, "normal", i.mean, i.std) for i in ins]
    ref = A.ReferencePCE(ref_ins, max_order=3).fit(model)
    ref_stats = ref.statistics()

    pce = U.build_pce(model, ins, max_level=4, max_poly_order=3)
    st = U.pce_statistics(pce)

    assert abs(st["mean"] - ref_stats["mean"]) / ref_stats["mean"] < 0.03
    assert abs(st["std"] - ref_stats["std"]) / ref_stats["std"] < 0.03
    # and the surrogate is a genuine function of the inputs, not a constant
    assert st["std"] > 0.05 * st["mean"]


def test_ice_resistance_sobol_attribution_matches_reference():
    trial, ins = _engine_inputs()
    ship = trial["params"]

    def model(s, h, f, v):
        return U.lindqvist_ice_resistance(ship, h, s, f, v)

    ref_ins = [A.ReferenceInput(i.name, "normal", i.mean, i.std) for i in ins]
    ref = A.ReferencePCE(ref_ins, max_order=3).fit(model).sobol()
    eng = U.sobol_indices(U.build_pce(model, ins, max_level=4, max_poly_order=3))
    for name in ref["first_order"]:
        assert abs(eng["first_order"][name] - ref["first_order"][name]) < 0.02, name
    # total-order indices bracket first-order ones and sum to >= 1
    assert sum(eng["total_order"].values()) >= 1.0 - 1e-6


# ---------------------------------------------------------------------------
# open defects, pinned as strict xfails (assert the CORRECT behaviour)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "DEFECT D1 (open): UncertainInput(distribution='uniform') is accepted without "
    "error but integrated against a standard normal (Hermite nodes/basis); "
    "mean of X^2 for X~U(-1,1) comes out 2.0 instead of 1/3."))
def test_defect_uniform_distribution_must_be_honoured():
    ins = [U.UncertainInput("u1", 0.0, 1.0, distribution="uniform", lower=-1.0, upper=1.0)]
    pce = U.build_pce(lambda a: a ** 2, ins, max_level=3, max_poly_order=2)
    st = U.pce_statistics(pce)
    assert abs(st["mean"] - A.uniform_moment(2, -1.0, 1.0)) < 1e-6


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT D2 (open): build_pce does not enforce max_level >= max_poly_order+1, so a "
    "total-order-5 projection on a level-3 sparse grid is aliased and reports a "
    "standard deviation ~88% above the converged value, with no warning."))
def test_defect_aliased_projection_must_not_be_silent():
    trial, ins = _engine_inputs()
    ship = trial["params"]

    def model(s, h, f, v):
        return U.lindqvist_ice_resistance(ship, h, s, f, v)

    converged = U.pce_statistics(U.build_pce(model, ins, max_level=6, max_poly_order=5))
    aliased = U.pce_statistics(U.build_pce(model, ins, max_level=3, max_poly_order=5))
    # a guarded engine would either refuse this configuration or agree with the
    # converged value; silently returning a number is the defect
    assert abs(aliased["std"] - converged["std"]) / converged["std"] < 0.03


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT D3 (open): UncertainInput.std = nominal * std_relative, so a nominal of 0 "
    "collapses to a deterministic model and the PCE returns all-zero coefficients "
    "without any error; there is no way to declare an absolute standard deviation."))
def test_defect_zero_nominal_must_not_silently_deterministic():
    ins = [U.UncertainInput("x1", 0.0, 0.5)]
    assert ins[0].std > 0.0  # a declared 50 % spread must mean *some* spread