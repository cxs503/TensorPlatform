"""Tests for the ITTC 7.5-03-01/02 GCI core (tools.gci.core).

Every expected value here is closed form / hand-derivable -- the point of the
suite is to prove the implementation reproduces the ITTC equations, not to
freeze the current output of the code under test.
"""

from __future__ import annotations

import math
import os
import sys
from decimal import Decimal, getcontext

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.gci.core import (  # noqa: E402
    FAIL,
    FS_THREE_GRID,
    FS_TWO_GRID,
    GATE_DEFAULT,
    PASS,
    WITHHELD,
    gci,
    ittc_validation_state,
    richardson_extrapolate,
    verdict,
)


# ---------------------------------------------------------------------------
# 1. observed order of accuracy on analytically constructed families
# ---------------------------------------------------------------------------
def test_p2_family_gives_exactly_p_two():
    """eps21 = -1e-2, eps32 = -4e-2 with r = 2 -> p = ln 4 / ln 2 = 2."""
    res = gci(1.0, 0.99, 0.9504)
    assert res.p_obs == pytest.approx(2.0, rel=1e-12)
    assert res.R_G == pytest.approx(0.25, rel=1e-12)
    assert res.convergence == "monotonic"
    assert res.p_source == "observed"


def test_p1_family_gives_exactly_p_one():
    res = gci(1.0, 0.99, 0.9702)
    assert res.p_obs == pytest.approx(1.0, rel=1e-12)
    assert res.R_G == pytest.approx(0.5, rel=1e-12)
    assert res.gci21 == pytest.approx(FS_THREE_GRID * 0.01 / (2.0 ** 1.0 - 1.0), rel=1e-12)
    assert res.gci32 == pytest.approx(FS_THREE_GRID * 0.02 / (2.0 ** 1.0 - 1.0), rel=1e-12)


def test_relative_normalisation_shifts_observed_order():
    """Documented trap: an exactly O(h^2) error family gives p_obs = 1.9734.

    ITTC normalises the differences by S1 and S2 (Eqs. 1-2), so the exact
    quadratic family (0.1*h^2 with h = 1, 1/2, 1/4) does not return p = 2.
    The closed-form value is pinned here so the limitation is never silently
    "fixed" by switching to absolute differences.
    """
    res = gci(1.00625, 1.025, 1.1)
    assert res.p_obs == pytest.approx(1.9733648734965503, rel=1e-12)
    assert res.p_obs < 2.0
    assert res.convergence == "monotonic"


# ---------------------------------------------------------------------------
# 2. convergence-ratio classification and the limiting-order fallback
# ---------------------------------------------------------------------------
def test_oscillatory_triplet_triggers_limiting_order():
    res = gci(1.0, 1.01, 1.005)
    assert res.R_G < 0.0
    assert res.convergence == "oscillatory"
    assert res.p_used == pytest.approx(0.5)
    assert res.p_source == "fallback:non_monotonic"
    assert res.p_obs is None
    assert any("limiting order" in n for n in res.notes)


def test_divergent_triplet_triggers_limiting_order():
    res = gci(1.0, 1.04, 1.0416)
    assert res.R_G > 1.0
    assert res.convergence == "divergent"
    assert res.p_used == pytest.approx(0.5)
    assert res.p_source == "fallback:p_obs_non_positive"
    assert res.p_obs < 0.0


def test_p_above_policy_max_triggers_limiting_order():
    res = gci(1.0 + 0.1 / 1024.0, 1.0 + 0.1 / 32.0, 1.1)
    assert res.p_obs is not None and res.p_obs > 4.0
    assert res.p_used == pytest.approx(0.5)
    assert res.p_source == "fallback:p_obs_above_max"


def test_convergence_class_thresholds():
    # R_G -> 0 : eps21 vanishes (medium and fine solutions coincide)
    res = gci(1.0, 1.0, 1.1)
    assert res.R_G == pytest.approx(0.0)
    assert res.convergence == "monotonic"
    assert res.gci21 == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 3. Richardson extrapolation
# ---------------------------------------------------------------------------
def test_richardson_extrapolate_closed_form():
    # p = 2, r = 2: phi_ext = f1 + (f1 - f2)/3
    assert richardson_extrapolate(1.0, 0.99, 2.0, 2.0) == pytest.approx(1.0 + 0.01 / 3.0, rel=1e-15)
    # p = 1, r = 3: phi_ext = f1 + (f1 - f2)/2
    assert richardson_extrapolate(2.0, 1.5, 3.0, 1.0) == pytest.approx(2.25, rel=1e-15)
    with pytest.raises(ValueError):
        richardson_extrapolate(1.0, 0.9, 1.0, 2.0)
    with pytest.raises(ValueError):
        richardson_extrapolate(1.0, 0.9, 2.0, 0.0)


def test_exact_p2_family_converges_to_the_exact_limit():
    """Richardson with the theoretical order recovers S_inf exactly.

    The same family returns p_obs = 1.9734 when the order is *estimated*
    (relative normalisation, see the trap test above), so the extrapolated
    limit is then biased at the 1e-4 level.  Both statements are checked here
    because the difference is a real property of the ITTC formulation.
    """
    s_inf, c = 1.0, 0.1
    f1 = s_inf + c / 16.0
    f2 = s_inf + c / 4.0
    f3 = s_inf + c
    exact = gci(f1, f2, f3, p_assumed=2.0, fs=FS_TWO_GRID)
    assert exact.phi_ext == pytest.approx(s_inf, rel=1e-14)

    estimated = gci(f1, f2, f3)
    assert estimated.p_source == "observed"
    assert estimated.p_obs == pytest.approx(1.9733648734965503, rel=1e-12)
    assert abs(estimated.phi_ext - s_inf) < 5e-4


# ---------------------------------------------------------------------------
# 4. hand calculation at 50 significant digits (acceptance criterion (c))
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "f1,f2,f3,r",
    [
        (1.0, 0.99, 0.9504, 2.0),
        (1.0, 0.99, 0.9702, 2.0),
        (0.9998380299669203, 0.9995025002950253, 0.998166119701974, 2.0),
        (1.00625, 1.025, 1.1, 2.0),
        (3.0, 2.94, 2.8212, 2.0),
    ],
)
def test_gci_matches_high_precision_hand_calculation(f1, f2, f3, r):
    """Independent Decimal (50-digit) evaluation of ITTC Eqs. (1)-(7), (15)."""
    getcontext().prec = 50
    d1, d2, d3, dr = map(Decimal, (repr(f1), repr(f2), repr(f3), repr(r)))
    eps21 = (d2 - d1) / d1
    eps32 = (d3 - d2) / d2
    p = (eps32 / eps21).ln() / dr.ln()
    fs = Decimal(repr(FS_THREE_GRID))
    gci21 = fs * abs(eps21) / (dr ** p - 1)
    gci32 = fs * abs(eps32) / (dr ** p - 1)
    u_v = gci21  # U_input = 0

    res = gci(f1, f2, f3, r, fs=FS_THREE_GRID)
    assert res.p_source == "observed"
    assert abs(res.p_used - float(p)) <= 1e-10 * max(abs(float(p)), 1.0)
    assert abs(res.gci21 - float(gci21)) <= 1e-10 * abs(float(gci21))
    assert abs(res.gci32 - float(gci32)) <= 1e-10 * abs(float(gci32))
    assert abs(res.u_v - float(u_v)) <= 1e-10 * abs(float(u_v))


def test_gci_hand_value_three_lines_of_arithmetic():
    """The p = 2, r = 2 case is literally |eps21|/2.4 with Fs = 1.25."""
    res = gci(1.0, 0.99, 0.9504)
    assert res.gci21 == pytest.approx(0.01 / 2.4, rel=1e-12)
    assert res.gci32 == pytest.approx(0.04 / 2.4, rel=1e-12)
    assert res.u_v == pytest.approx(res.gci21)


# ---------------------------------------------------------------------------
# 5. safety factors, U_V bookkeeping
# ---------------------------------------------------------------------------
def test_two_grid_safety_factor_scales_gci():
    three = gci(1.0, 0.99, 0.9504, fs=FS_THREE_GRID)
    two = gci(1.0, 0.99, 0.9504, fs=FS_TWO_GRID)
    assert two.gci21 / three.gci21 == pytest.approx(FS_TWO_GRID / FS_THREE_GRID, rel=1e-12)
    assert two.gci21 == pytest.approx(0.01, rel=1e-12)


def test_u_v_folds_in_input_uncertainty():
    res = gci(1.0, 0.99, 0.9504, u_input=0.05)
    assert res.u_v == pytest.approx(math.hypot(res.gci21, 0.05), rel=1e-15)
    assert res.u_v > 0.05


def test_assumed_order_bypasses_fallback():
    res = gci(1.0, 1.01, 1.005, p_assumed=2.0, fs=FS_TWO_GRID)
    assert res.p_source == "assumed"
    assert res.p_used == pytest.approx(2.0)
    assert res.gci21 == pytest.approx(FS_TWO_GRID * 0.01 / 3.0, rel=1e-12)


# ---------------------------------------------------------------------------
# 6. input validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [
        dict(f1=1.0, f2=0.99, f3=0.95, r21=1.0),
        dict(f1=1.0, f2=0.99, f3=0.95, r21=0.5),
        dict(f1=float("nan"), f2=0.99, f3=0.95),
        dict(f1=0.0, f2=0.99, f3=0.95),
        dict(f1=1.0, f2=0.99, f3=0.95, fs=0.0),
    ],
)
def test_invalid_inputs_raise(kwargs):
    with pytest.raises(ValueError):
        gci(**kwargs)


# ---------------------------------------------------------------------------
# 7. the upgraded two-criterion verdict
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "u_v,deviation,expected",
    [
        (0.01, 0.02, PASS),          # tight band, inside the gate
        (0.01, 0.14, PASS),          # tight band, just inside
        (0.01, 0.30, FAIL),          # tight band, outside -> solver distortion
        (0.149, 0.99, FAIL),         # band below the gate still allows FAIL
        (0.15, 0.0, WITHHELD),       # band == gate -> withheld (boundary)
        (0.30, 0.0, WITHHELD),       # wide band -> nothing can be concluded
        (0.30, 0.90, WITHHELD),      # wide band overrides a large deviation
        (0.01, None, WITHHELD),      # no reference -> withheld
        (0.01, float("nan"), WITHHELD),
    ],
)
def test_verdict_three_states(u_v, deviation, expected):
    assert verdict(u_v, deviation, GATE_DEFAULT) == expected


def test_withheld_is_never_pass_or_fail():
    for dev in (0.0, 0.05, 0.2, 5.0):
        assert verdict(GATE_DEFAULT, dev, GATE_DEFAULT) == WITHHELD
        assert verdict(0.9, dev, GATE_DEFAULT) == WITHHELD


def test_verdict_rejects_bad_inputs():
    with pytest.raises(ValueError):
        verdict(-0.1, 0.0)
    with pytest.raises(ValueError):
        verdict(0.01, 0.0, gate=0.0)


def test_ittc_validation_states():
    assert ittc_validation_state(0.01, 0.005) == "VALIDATED"
    assert ittc_validation_state(0.01, -0.02) == "SIMULATION_ERROR"
    assert ittc_validation_state(0.0, 0.0) == WITHHELD