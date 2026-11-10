"""Acceptance tests for the model-ship scale-effect / extrapolation kernel (m9028).

Node ``p234w2-n02-scale-law-maturity`` acceptance criteria (machine-checkable)
------------------------------------------------------------------------------
(a) ITTC-1957 reproduces printed 4-significant-digit handbook values with
    ``|err| < 0.1 %``;
(b) three-scale (lambda = 1/10, 1/25, 1/50) extrapolation of the same hull to
    the full-scale ship is within ``5 %`` of the reference resistance;
(c) the low-Re friction used at ``Re = 1e5`` agrees with Schlichting / Blasius
    laminar within ``2 %``;
(d) the three extrapolations of the same ship agree among themselves within
    ``3 %``;
(e) ``C_F -> 0`` as ``Re -> inf`` (asymptotic behaviour of the correlation
    line);
(f) this file: >= 10 tests, all green.

Two further, deliberately adversarial checks are included because a scale-law
module that only passes its own happy path is worthless:

* the literal ``Delta C_F`` expression from the mission text (``+`` sign) is
  shown to over-predict by > 50 % -- the physics-consistent ``-`` sign is what
  the module implements;
* the classical "ITTC-1957 on both sides" extrapolation is shown to *fail* the
  5 % gate for a small USV-scale model whose ``Re_M`` is inside the transition
  band, which is exactly the capability gap this node closes.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from api.domain import scale_effect as se  # noqa: E402

G = 9.80665
NU_SEA = 1.1883e-6
RHO_SEA = 1025.0
NU_FRESH = 1.137e-6
RHO_FRESH = 999.1
LAMBDAS = (0.10, 0.04, 0.02)


# ---------------------------------------------------------------------------
# helpers: a synthetic, closure-explicit model/ship pair
# ---------------------------------------------------------------------------

def _ship(length: float, fr: float) -> se.ScaleState:
    v = fr * math.sqrt(G * length)
    b = length / 10.0
    t = b / 1.6
    s = 0.72 * length * (b + 2.0 * t)  # slender monohull wetted-surface estimate
    return se.ScaleState(length=length, velocity=v, density=RHO_SEA,
                         viscosity=NU_SEA, wetted_surface=s)


def _model(ship: se.ScaleState, lam: float) -> se.ScaleState:
    return se.ScaleState(
        length=ship.length * lam,
        velocity=se.froude_velocity(ship.velocity, lam),
        density=RHO_FRESH,
        viscosity=NU_FRESH,
        wetted_surface=ship.wetted_surface * lam ** 2,
    )


def _cw(fr: float) -> float:
    """Stand-in inviscid wave-resistance coefficient (Froude-similar, Fn only)."""
    return 1.0e-3 * (fr / 0.30) ** 4


def _tank_resistance(model: se.ScaleState, k: float, cf_true) -> float:
    q = model.dynamic_pressure * model.wetted_surface
    return (1.0 + k) * q * cf_true(model.reynolds) + _cw(model.froude) * q


def _reference_ship(ship: se.ScaleState, k: float, c_a: float, cf_ship) -> float:
    q = ship.dynamic_pressure * ship.wetted_surface
    return (1.0 + k) * q * cf_ship(ship.reynolds) + _cw(ship.froude) * q + q * c_a


# ---------------------------------------------------------------------------
# (a) ITTC-1957 against the printed handbook table
# ---------------------------------------------------------------------------

def test_ittc1957_matches_handbook_values():
    printed = {1e5: 8.333e-3, 1e6: 4.688e-3, 1e7: 3.000e-3, 1e8: 2.083e-3, 1e9: 1.531e-3}
    for re, ref in printed.items():
        cf = se.ittc1957_cf(re)
        err = abs(cf - ref) / ref
        assert err < 1e-3, f"Re={re:g}: Cf={cf:.6e} vs printed {ref:.4e} (err {err:.2%})"


def test_ittc1957_guards_invalid_reynolds():
    for bad in (0.0, -1.0, 1e3):
        with pytest.raises(ValueError):
            se.ittc1957_cf(bad)


def test_schoenherr_is_independent_of_ittc1957():
    # implicit line solved by bisection: converges onto its own residual
    cf = se.schoenherr_cf(1e7)
    assert abs(0.242 / math.sqrt(cf) - math.log10(1e7 * cf)) < 1e-10
    # converges to ITTC-1957 at very high Re, and is clearly different at low Re
    assert abs(se.schoenherr_cf(1e9) - se.ittc1957_cf(1e9)) / se.ittc1957_cf(1e9) < 0.01
    assert se.schoenherr_cf(1e6) < se.ittc1957_cf(1e6)


# ---------------------------------------------------------------------------
# (c) low-Re behaviour: Schlichting / Blasius at Re = 1e5
# ---------------------------------------------------------------------------

def test_low_re_matches_schlichting_at_1e5():
    re = 1.0e5
    ours = se.low_re_friction_cf(re)
    schlichting = se.blasius_cf(re)  # sub-critical Schlichting branch = Blasius
    assert se.schlichting_plate_cf(re) == pytest.approx(schlichting, rel=1e-12)
    assert abs(ours - schlichting) / schlichting < 0.02
    # ITTC-1957, used blindly, is ~100 % higher here -- that is the gap
    assert se.ittc1957_cf(re) > 1.5 * ours


def test_low_re_line_is_continuous_with_physical_transition_rise():
    """C_F falls below and above the band and RISES across it (real physics)."""
    laminar_grid = [10 ** (4 + 0.05 * i) for i in range(21)]     # 1e4 .. 3e5
    turbulent_grid = [10 ** (6 + 0.05 * i) for i in range(61)]   # 1e6 .. 1e9
    band = [10 ** (5.477 + 0.02 * i) for i in range(21)]         # 3e5 .. 1e6
    for grid in (laminar_grid, turbulent_grid):
        vals = [se.low_re_friction_cf(r) for r in grid]
        assert all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
    band_vals = [se.low_re_friction_cf(r) for r in band]
    # one interior minimum, then a monotone rise to the turbulent level
    diffs = [band_vals[i + 1] - band_vals[i] for i in range(len(band_vals) - 1)]
    signs = [1 if d > 0 else -1 for d in diffs]
    flips = sum(1 for a, b in zip(signs[:-1], signs[1:]) if a != b)
    assert flips == 1, "transition band must have a single minimum"
    assert band_vals[-1] > 1.5 * band_vals[0], "C_F must rise across transition"
    # continuity: no jump on a fine grid (the transition band is steep but smooth,
    # ~2 % per 0.01 decade, so the bound is 5 % per step rather than 2 %)
    grid = [10 ** (4 + 0.01 * i) for i in range(401)]  # 1e4 .. 1e8
    vals = [se.low_re_friction_cf(r) for r in grid]
    for a, b in zip(vals[:-1], vals[1:]):
        assert abs(b - a) / a < 0.05
    # exact join at both ends of the intermittency ramp
    assert se.low_re_friction_cf(3.0e5) == pytest.approx(se.blasius_cf(3.0e5), rel=1e-12)
    assert se.low_re_friction_cf(1.0e6) == pytest.approx(se.prandtl_schlichting_cf(1.0e6), rel=1e-12)


def test_low_re_tends_to_prandtl_schlichting():
    assert se.low_re_friction_cf(1e6) == pytest.approx(se.prandtl_schlichting_cf(1e6), rel=1e-12)
    assert se.low_re_friction_cf(1e8) == pytest.approx(se.prandtl_schlichting_cf(1e8), rel=1e-12)
    # intermittency ramp endpoints
    assert se.transition_intermittency(2e5) == 0.0
    assert se.transition_intermittency(2e6) == 1.0
    assert 0.0 < se.transition_intermittency(5.5e5) < 1.0


def test_schlichting_plate_piecewise_branches():
    assert se.schlichting_plate_cf(1e5) == pytest.approx(se.blasius_cf(1e5), rel=1e-12)
    mid = se.schlichting_plate_cf(1e6)
    assert mid == pytest.approx(se.prandtl_schlichting_cf(1e6) - 1700.0 / 1e6, rel=1e-12)
    assert mid < se.prandtl_schlichting_cf(1e6)
    hi = se.schlichting_plate_cf(5e7)
    assert hi == pytest.approx(se.prandtl_schlichting_cf(5e7), rel=1e-12)


def test_model_cf_line_switch():
    assert se.low_re_model_cf_line(5e5) == se.low_re_friction_cf(5e5)
    assert se.low_re_model_cf_line(5e6) == se.ittc1957_cf(5e6)


# ---------------------------------------------------------------------------
# (e) asymptotic behaviour of the correlation line
# ---------------------------------------------------------------------------

def test_ittc1957_vanishes_as_re_grows():
    grid = [10 ** (5 + 0.25 * i) for i in range(101)]  # 1e5 .. 1e30
    vals = [se.ittc1957_cf(r) for r in grid]
    assert all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
    assert se.ittc1957_cf(1e12) == pytest.approx(0.075 / 100.0, rel=1e-12)
    assert se.ittc1957_cf(1e30) < 1e-4
    # slow logarithmic decay: C_F ~ 0.075/(log10 Re)^2
    ratio = se.ittc1957_cf(1e30) / se.ittc1957_cf(1e12)
    assert 0.1 < ratio < 0.2


# ---------------------------------------------------------------------------
# similarity relations
# ---------------------------------------------------------------------------

def test_reynolds_similarity_law():
    for lam in LAMBDAS:
        assert se.reynolds_ratio_similar(lam, NU_SEA, NU_SEA) == pytest.approx(lam ** 1.5, rel=1e-12)
    # with the real viscosity ratio the law still holds exactly
    ship = _ship(100.0, 0.30)
    for lam in LAMBDAS:
        model = _model(ship, lam)
        assert ship.reynolds / model.reynolds == pytest.approx(
            se.reynolds_ratio_similar(1.0 / lam, model.viscosity, ship.viscosity),
            rel=1e-12,
        )
    # a 1/50 model of a 10 m USV really does land outside the turbulent band
    usv = _ship(10.0, 0.25)
    assert _model(usv, 0.02).reynolds < 1e5


def test_correlation_allowance_formula():
    assert se.correlation_allowance_ittc78(100.0) == pytest.approx(5.203e-4, rel=1e-3)
    assert se.correlation_allowance_ittc78(200.0) < se.correlation_allowance_ittc78(100.0)
    # outside the fitted band (L >= 50 m) the value is flagged by being large
    assert se.correlation_allowance_ittc78(10.0) > 5e-4


# ---------------------------------------------------------------------------
# extrapolation algebra
# ---------------------------------------------------------------------------

def test_delta_cf_equals_ittc1978_without_form_factor_or_allowance():
    ship = _ship(100.0, 0.30)
    model = _model(ship, 0.04)
    r_tm = _tank_resistance(model, 0.0, se.ittc1957_cf)
    a = se.extrapolate_ittc78(r_tm, model, ship, cf_line_ship="ittc1957",
                              form_factor_k=0.0, c_a=0.0)
    b = se.extrapolate_delta_cf(r_tm, model, ship, cf_line_ship="ittc1957",
                                c_a=0.0, delta_cf_sign=-1)
    assert a.r_ts == pytest.approx(b.r_ts, rel=1e-12)


def test_mission_literal_sign_is_rejected_as_physics():
    ship = _ship(100.0, 0.30)
    model = _model(ship, 0.02)
    r_tm = _tank_resistance(model, 0.1, se.low_re_friction_cf)
    physics = se.extrapolate_delta_cf(r_tm, model, ship, cf_line_ship="ittc1957",
                                      cf_line_model="low_re", delta_cf_sign=-1)
    literal = se.extrapolate_delta_cf(r_tm, model, ship, cf_line_ship="ittc1957",
                                      cf_line_model="low_re", delta_cf_sign=+1)
    assert literal.r_ts > physics.r_ts
    assert (literal.r_ts - physics.r_ts) / physics.r_ts > 0.5
    with pytest.raises(ValueError):
        se.extrapolate_delta_cf(r_tm, model, ship, delta_cf_sign=0)


def test_extrapolation_decomposition_is_conservative():
    ship = _ship(100.0, 0.30)
    model = _model(ship, 0.04)
    r_tm = _tank_resistance(model, 0.1, se.low_re_friction_cf)
    ex = se.extrapolate_ittc78(r_tm, model, ship, cf_line_ship="ittc1957",
                               cf_line_model="low_re", form_factor_k=0.1, c_a=5e-4)
    assert ex.r_ts == pytest.approx(
        (1.0 + ex.form_factor_k) * ex.r_fs + ex.r_rs + ex.r_a, rel=1e-12
    )
    assert ex.r_rm == pytest.approx(r_tm - (1.0 + ex.form_factor_k) * ex.r_fm, rel=1e-12)
    assert ex.r_a == pytest.approx(
        ship.dynamic_pressure * ship.wetted_surface * 5e-4, rel=1e-12
    )
    with pytest.raises(ValueError):
        se.extrapolate_ittc78(-1.0, model, ship)


# ---------------------------------------------------------------------------
# (b) + (d) three-scale extrapolation ladder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("length,fr", [(100.0, 0.30), (10.0, 0.30), (10.0, 0.20)])
def test_three_scale_extrapolation_is_consistent(length, fr):
    ship = _ship(length, fr)
    k = 0.1
    ref = _reference_ship(ship, k, 0.0, se.low_re_friction_cf)
    results = {}
    for lam in LAMBDAS:
        model = _model(ship, lam)
        r_tm = _tank_resistance(model, k, se.low_re_friction_cf)
        ex = se.extrapolate_ittc78(r_tm, model, ship, cf_line_ship="low_re",
                                   cf_line_model="low_re", form_factor_k=k)
        results[lam] = ex.r_ts
        assert abs(ex.r_ts - ref) / ref < 0.05, (lam, ex.r_ts, ref)
    spread = se.self_consistency_spread({str(k_): v for k_, v in results.items()})
    assert spread["spread_pct"] < 3.0


def test_baseline_ittc1957_fails_for_small_model():
    """Negative control: the platform's blind ITTC-57 closure is not scale-safe."""
    ship = _ship(10.0, 0.25)
    k = 0.1
    ref = _reference_ship(ship, k, 0.0, se.low_re_friction_cf)
    naive_errors = []
    for lam in LAMBDAS:
        model = _model(ship, lam)
        assert model.reynolds < 1e6  # USV model regime
        r_tm = _tank_resistance(model, k, se.low_re_friction_cf)
        naive = se.extrapolate_ittc78(r_tm, model, ship, cf_line_ship="ittc1957",
                                      cf_line_model="ittc1957", form_factor_k=k)
        naive_errors.append(abs(naive.r_ts - ref) / ref)
    assert max(naive_errors) > 0.05

    # and the corrected closure brings every scale back inside the gate
    corrected = []
    for lam in LAMBDAS:
        model = _model(ship, lam)
        r_tm = _tank_resistance(model, k, se.low_re_friction_cf)
        ex = se.extrapolate_ittc78(r_tm, model, ship, cf_line_ship="ittc1957",
                                   cf_line_model="low_re", form_factor_k=k)
        corrected.append(abs(ex.r_ts - ref) / ref)
    assert max(corrected) < 0.05


def test_self_consistency_spread_metric():
    assert se.relative_spread([1.0, 1.0, 1.0]) == 0.0
    assert se.relative_spread([0.99, 1.01]) == pytest.approx(0.02, rel=1e-9)
    out = se.self_consistency_spread({"a": 100.0, "b": 102.0})
    assert out["spread_pct"] == pytest.approx(100.0 * 2.0 / 101.0, rel=1e-12)


# ---------------------------------------------------------------------------
# propeller scale effect
# ---------------------------------------------------------------------------

def test_propeller_scale_correction_signs_and_magnitude():
    out = se.propeller_scale_effect(
        0.25, 0.035, chord=1.5, thickness_ratio=0.05, pitch_ratio=0.95,
        n_blades=5.0, diameter=6.0, re_co_model=3.2e5, re_co_ship=1.07e8,
    )
    assert out["delta_cd"] > 0.0                 # model sections rougher
    assert out["delta_kt"] < 0.0                 # full-scale thrust drops
    assert abs(out["delta_kt"] / out["kt_model"]) < 0.02
    assert out["kt_ship"] < out["kt_model"]
    assert abs(out["eta_rel_change"]) < 0.05
    assert out["eta_rel_change"] == pytest.approx(0.0, abs=1e-12)  # "relative" torque model
    # the declared alternative bound: thrust-only correction costs efficiency
    alt = se.propeller_scale_effect(
        0.25, 0.035, chord=1.5, thickness_ratio=0.05, pitch_ratio=0.95,
        n_blades=5.0, diameter=6.0, re_co_model=3.2e5, re_co_ship=1.07e8,
        torque_model="none",
    )
    assert -0.05 < alt["eta_rel_change"] < 0.0
    with pytest.raises(ValueError):
        se.propeller_scale_effect(0.25, 0.035, chord=1.5, thickness_ratio=0.05,
                                  pitch_ratio=0.95, n_blades=5.0, diameter=6.0,
                                  re_co_model=3.2e5, re_co_ship=1.07e8,
                                  torque_model="guess")


def test_section_drag_coefficient_validity_band():
    assert not se.section_drag_in_range(1e5)      # below the fitted band
    assert se.section_drag_in_range(1e6)
    assert se.SECTION_DRAG_RE_MIN == pytest.approx((5.0 / 0.044) ** 2, rel=1e-12)
    # below the sign change the formula returns a non-physical value: this is
    # why callers must gate on section_drag_in_range
    assert se.section_drag_coefficient(0.05, 0.0025, 5e3) < 0.0
    assert se.section_drag_coefficient(0.05, 0.0025, 1e6) > 0.0


# ---------------------------------------------------------------------------
# fluid properties
# ---------------------------------------------------------------------------

def test_water_property_anchors():
    assert se.kinematic_viscosity(15.0, 0.0) == pytest.approx(1.1380e-6, rel=2e-3)
    assert se.kinematic_viscosity(15.0, 35.0) == pytest.approx(1.1883e-6, rel=2e-3)
    assert se.density_water(15.0, 0.0) == pytest.approx(999.10, rel=1e-3)
    assert se.density_water(15.0, 35.0) == pytest.approx(1026.0, rel=2e-3)
    # viscosity decreases with temperature, density peaks near 4 degC
    assert se.kinematic_viscosity(25.0, 35.0) < se.kinematic_viscosity(15.0, 35.0)
    assert se.density_water(4.0) > se.density_water(20.0)
    with pytest.raises(ValueError):
        se.kinematic_viscosity(15.0, -1.0)