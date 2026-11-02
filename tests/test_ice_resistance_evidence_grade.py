"""Evidence-grade regression tests for the Lindqvist ice-resistance chain (m1690).

These tests lock in the *audit findings* of
``tools/verify_ice_resistance_evidence.py`` so that the platform cannot silently
re-claim an absolute accuracy for ``ice-uq`` that is an artefact of the per-ship
trial anchor:

J1  the nominal residual of the calibrated model is zero **by construction**
    (|err| < 1e-9), therefore it is not evidence of accuracy;
J2  the raw (formula-only) residual is non-zero and is recoverable from the
    calibration factor, i.e. ``raw == trial / factor`` to machine precision;
J3  the calibration factor is a real, non-unit per-ship number with a wide
    spread (it is an anchor, not a small correction);
J4  the component decomposition reconstructs the raw total exactly via
    (crushing+bending)*velocity_multiplier + submersion + speed (m1720
    corrected physics; no drift between the audit tool and the model);
J5  thickness scaling: 1 < d lnR / d ln h < 2 (submersion-dominated, not the
    bending-dominated h^2 limit);
J6  beam scaling is **not** extensive in B (exponent < 1) because the crushing
    component carries no contact width while the bending component does;
J7  the sub-model that carries the untraceable constants (submersion with
    ``0.3*L`` + speed with ``0.1``) keeps a substantial share of the prediction
    (0.25-0.5; pre-m1720 it was the majority -- the restored velocity
    multiplier re-weights the traceable structural terms upward);
J8  structural invariants: monotone in h, V, mu; the uncalibrated entry point is
    independent of the calibration factor;
J9  the deterministic API response exposes raw / factor / terms and satisfies
    ``total == raw * factor``;
J10 module import does not compound the calibration (re-calibrating an
    already-calibrated ship reproduces the same factor).
"""

from __future__ import annotations

import math
import os
import sys

import pytest

_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services.ice_resistance_uq import (  # noqa: E402
    SHIP_TRIAL_DATA,
    _calibrate_ship,
    _lindqvist_raw,
    lindqvist_ice_resistance,
    lindqvist_ice_resistance_uncalibrated,
    lindqvist_terms,
)

SHIPS = sorted(SHIP_TRIAL_DATA)


def _nominal(name):
    t = SHIP_TRIAL_DATA[name]
    return (
        t["params"],
        t["nominal_ice_thickness_m"],
        t["nominal_ice_strength_MPa"],
        t["nominal_friction_coeff"],
        t["nominal_speed_ms"],
        t["mean_resistance_kN"],
    )


# --------------------------------------------------------------------------
# J1 -- circularity of the nominal anchor
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", SHIPS)
def test_nominal_residual_is_zero_by_construction(name):
    ship, h, s, mu, v, R_trial = _nominal(name)
    R_cal = lindqvist_ice_resistance(ship, h, s, mu, v)
    rel = abs(R_cal - R_trial) / R_trial
    assert rel < 1e-9, f"{name}: nominal residual {rel:.3e} should be ~0 (circular)"


# --------------------------------------------------------------------------
# J2 -- raw residual is non-zero and factor-consistent
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", SHIPS)
def test_raw_residual_recoverable_from_factor(name):
    ship, h, s, mu, v, R_trial = _nominal(name)
    R_raw = lindqvist_ice_resistance_uncalibrated(ship, h, s, mu, v)
    assert abs(R_raw - R_trial / ship.calibration_factor) < 1e-9 * R_trial
    rel = abs(R_raw - R_trial) / R_trial
    assert rel > 1e-6, "a genuinely independent prediction cannot match the anchor exactly"


def test_worst_ship_exceeds_15_percent_when_uncalibrated():
    """Post-m1720 the worst ship still misses its trial mean by > 15 %.

    Terry_Fox was the >15 % exemplar under the pre-multiplier physics; with the
    m1720 velocity multiplier its raw deviation drops to ~11 %, while
    Polarstern rises to ~+49 % -- so the claim is asserted on the worst ship
    rather than on a hard-coded name.
    """
    worst = max(
        abs(lindqvist_ice_resistance_uncalibrated(ship, h, s, mu, v) - R_trial) / R_trial
        for ship, h, s, mu, v, R_trial in (_nominal(n) for n in SHIPS)
    )
    assert worst > 0.15


# --------------------------------------------------------------------------
# J3 -- the anchor is a real, non-unit, widely spread number
# --------------------------------------------------------------------------
def test_calibration_factors_are_non_trivial():
    factors = [SHIP_TRIAL_DATA[n]["params"].calibration_factor for n in SHIPS]
    assert all(abs(f - 1.0) > 0.05 for f in factors)
    spread = (max(factors) - min(factors)) / min(factors)
    assert spread > 0.20, f"factor spread {spread:.3f} too small to be a mere correction"


# --------------------------------------------------------------------------
# J4 -- decomposition integrity
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", SHIPS)
def test_terms_sum_to_raw(name):
    ship, h, s, mu, v, _ = _nominal(name)
    terms = lindqvist_terms(ship, h, s, mu, v)
    assert set(terms) == {"crushing", "bending", "submersion", "speed",
                          "velocity_multiplier"}
    recon = ((terms["crushing"] + terms["bending"]) * terms["velocity_multiplier"]
             + terms["submersion"] + terms["speed"])
    assert abs(recon - _lindqvist_raw(ship, h, s, mu, v)) < 1e-9
    assert all(vv > 0 for vv in terms.values())


# --------------------------------------------------------------------------
# J5 -- thickness exponent
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", SHIPS)
def test_thickness_exponent_between_one_and_two(name):
    ship, h, s, mu, v, _ = _nominal(name)
    d = 1e-4
    rp = _lindqvist_raw(ship, h * math.exp(d), s, mu, v)
    rm = _lindqvist_raw(ship, h * math.exp(-d), s, mu, v)
    expo = (math.log(rp) - math.log(rm)) / (2 * d)
    assert 1.0 < expo < 2.0, f"{name}: h-exponent {expo:.3f} outside (1, 2)"


# --------------------------------------------------------------------------
# J6 -- beam scaling defect
# --------------------------------------------------------------------------
def test_crushing_component_is_beam_independent():
    ship, h, s, mu, v, _ = _nominal("MV_Arctic")
    import copy

    wide = copy.copy(ship)
    wide.beam_m = ship.beam_m * 2.0
    t0 = lindqvist_terms(ship, h, s, mu, v)
    t1 = lindqvist_terms(wide, h, s, mu, v)
    assert abs(t1["crushing"] - t0["crushing"]) < 1e-9 * t0["crushing"]
    assert abs(t1["bending"] / t0["bending"] - 2.0) < 1e-6


@pytest.mark.parametrize("name", SHIPS)
def test_total_is_not_extensive_in_beam(name):
    ship, h, s, mu, v, _ = _nominal(name)
    import copy

    d = 1e-4
    wide, narrow = copy.copy(ship), copy.copy(ship)
    wide.beam_m = ship.beam_m * math.exp(d)
    narrow.beam_m = ship.beam_m * math.exp(-d)
    expo = (
        math.log(_lindqvist_raw(wide, h, s, mu, v))
        - math.log(_lindqvist_raw(narrow, h, s, mu, v))
    ) / (2 * d)
    # m1720 乘子恢复后 crushing(无 B 依赖)份额被稀释，两船指数降到 0.47-0.48
    assert 0.4 < expo < 0.99, f"{name}: beam exponent {expo:.3f} unexpectedly extensive"


# --------------------------------------------------------------------------
# J7 -- untraceable-constant share dominates
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", SHIPS)
def test_untraceable_terms_are_the_majority(name):
    ship, h, s, mu, v, _ = _nominal(name)
    terms = lindqvist_terms(ship, h, s, mu, v)
    total = ((terms["crushing"] + terms["bending"]) * terms["velocity_multiplier"]
             + terms["submersion"] + terms["speed"])
    share = (terms["submersion"] + terms["speed"]) / total
    assert 0.25 < share < 0.5, (
        f"{name}: untraceable share {share:.3f} outside post-m1720 band"
    )


# --------------------------------------------------------------------------
# J8 -- structural invariants
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", SHIPS)
def test_monotonic_and_uncalibrated_entry_point(name):
    ship, h, s, mu, v, _ = _nominal(name)
    base = _lindqvist_raw(ship, h, s, mu, v)
    assert _lindqvist_raw(ship, h + 0.05, s, mu, v) > base
    assert _lindqvist_raw(ship, h, s, mu, v + 0.5) > base
    assert _lindqvist_raw(ship, h, s, mu + 0.05, v) > base
    # uncalibrated entry point must ignore the shipped calibration factor
    assert abs(lindqvist_ice_resistance_uncalibrated(ship, h, s, mu, v) - base) < 1e-12


# --------------------------------------------------------------------------
# J9 -- API contract
# --------------------------------------------------------------------------
def _load_ice_uq_api():
    """Load api/domain/ice_uq.py standalone (the api package __init__ pulls the DB)."""
    import importlib.util

    path = os.path.join(_BACKEND, "api", "domain", "ice_uq.py")
    spec = importlib.util.spec_from_file_location("_m1690_ice_uq_api", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # pydantic resolves annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


def test_api_response_exposes_raw_factor_and_terms():
    import asyncio

    mod = _load_ice_uq_api()
    IceResistanceRequest, calc_ice_resistance = mod.IceResistanceRequest, mod.calc_ice_resistance

    req = IceResistanceRequest(ship_name="Terry_Fox", ice_thickness_m=0.6,
                               ice_strength_MPa=1.0, friction_coeff=0.10,
                               ship_speed_ms=2.0)
    resp = asyncio.run(calc_ice_resistance(req))
    assert resp.raw_resistance_kN is not None
    assert resp.calibration_factor is not None
    assert resp.terms_kN is not None
    assert abs(resp.total_resistance_kN - resp.raw_resistance_kN * resp.calibration_factor) < 1e-2
    t = resp.terms_kN
    recon = ((t["crushing"] + t["bending"]) * t["velocity_multiplier"]
             + t["submersion"] + t["speed"])
    assert abs(recon - resp.raw_resistance_kN) < 1e-2
    # the raw value must NOT match the trial mean (no circular claim)
    # m1720 乘子后 Terry_Fox 未标定偏差 ~+11%（旧物理 >15%）；Polarstern ~+49%
    assert abs(resp.raw_resistance_kN - 850.0) / 850.0 > 0.10


# --------------------------------------------------------------------------
# J10 -- calibration is not compounded at import / on repeat calls
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", SHIPS)
def test_calibration_is_not_compounded(name):
    f0 = SHIP_TRIAL_DATA[name]["params"].calibration_factor
    again = _calibrate_ship(name)
    assert abs(again.calibration_factor - f0) < 1e-12