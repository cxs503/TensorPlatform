"""Cross-implementation audit of level-ice resistance models (m1702).

Two layers:

* ``test_reference_*``   analytic anchors on the reference decomposition
  (``backend/services/ice_canon.py``) and on the polar production path — these
  must PASS: they encode derivable physics, not literature-fitted coefficients.
* ``test_debt_*``        the known-debt ledger.  Each entry is a real defect
  found by ``docs/ice_crosscheck/audit_ice_resistance.py`` and is marked
  ``xfail(strict=True)``: the test documents the defect *and* fails loudly the
  moment somebody fixes it, forcing the ledger (and this docstring) to be
  updated instead of silently rotting.

Audit artefact: ``outputs/ice_crosscheck/ice_crosscheck.json``.
Report: ``docs/ice_crosscheck/REPORT.md``.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (os.path.join(REPO, "backend"), REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from optimizer.ice_class import objectives as OPT  # noqa: E402
from services import ice_canon as CANON  # noqa: E402
from services import ice_resistance_uq as UQ  # noqa: E402
from services.polar import resistance as POLAR  # noqa: E402
from services.polar.ships import ShipGeometry  # noqa: E402

L, B, T, CB, ALPHA = 150.0, 23.0, 8.5, 0.70, 30.0
H_REF, SIGMA_REF, MU_REF, V_REF = 0.8, 1.5e6, 0.10, 2.0
H_GENERIC = [0.2, 0.3, 0.4, 0.6, 0.8, 1.0, 1.2, 1.5]


def polar_ship(mu: float = MU_REF, beam: float = B, draft: float = T) -> ShipGeometry:
    return ShipGeometry(
        key="audit", name="Audit hull", category="general_cargo", length_m=L,
        beam_m=beam, draft_m=draft, cb=CB, dwt_t=14000.0, design_speed_kn=14.0,
        brake_power_kw=8000.0, sfoc_g_kwh=180.0, eta_propulsive=0.65,
        bow_half_angle_deg=ALPHA, ice_class="1A", h_max_m=1.2, mu_hull_ice=mu)


def canon_total(h=H_REF, sigma=SIGMA_REF, mu=MU_REF, v=V_REF) -> float:
    return CANON.ice_components(length_m=L, beam_m=B, draft_m=T, cb=CB,
                                bow_angle_deg=ALPHA, h_m=h, sigma_c_pa=sigma,
                                mu=mu, v_ms=v).total_n


def canon_terms(h: float = H_REF, sigma: float = SIGMA_REF, mu: float = MU_REF,
                v: float = V_REF) -> dict:
    c = CANON.ice_components(length_m=L, beam_m=B, draft_m=T, cb=CB,
                             bow_angle_deg=ALPHA, h_m=h, sigma_c_pa=sigma,
                             mu=mu, v_ms=v)
    return {"crushing": c.crushing_n, "breaking": c.breaking_n,
            "submersion": c.submersion_n}


def polar_terms(h: float = H_REF, sigma: float = SIGMA_REF, mu: float = MU_REF,
                v: float = V_REF, beam: float = B, draft: float = T) -> dict:
    return POLAR.ice_components(polar_ship(mu=mu, beam=beam, draft=draft), v, h,
                                sigma / 1e6)


def a_total(h=H_REF, sigma=SIGMA_REF, mu=MU_REF, v=V_REF, draft=T, cal=1.0) -> float:
    ship = UQ.ShipIceParams(name="audit", length_m=L, beam_m=B, draft_m=draft, cb=CB,
                            bow_angle_deg=ALPHA, displacement_t=14000.0,
                            calibration_factor=cal)
    return 1e3 * UQ._lindqvist_raw(ship, h, sigma / 1e6, mu, v)  # N


def b_total(h=H_REF, v=V_REF) -> float:
    return 1e3 * OPT.evaluate_ice_resistance(
        L=L, B=B, T=T, Cb=CB, alpha_bow=ALPHA, alpha_stem=20.0, h_ice=h,
        V_kn=v / 0.5144)


def loglog_slope(fn, values) -> float:
    xs = np.log(np.asarray(values, dtype=float))
    ys = np.log(np.asarray([fn(v) for v in values], dtype=float))
    return float(np.polyfit(xs, ys, 1)[0])


# ---------------------------------------------------------------------------
# Reference decomposition: analytic anchors (must PASS)
# ---------------------------------------------------------------------------
def test_reference_characteristic_length_scales_as_h_to_the_three_quarters():
    """l_c = (E h^3 / (12 rho g (1-nu^2)))^(1/4)  =>  l_c ~ h^0.75."""
    h = [0.2, 0.4, 0.8, 1.2, 1.6]
    slope = loglog_slope(CANON.characteristic_length, h)
    assert slope == pytest.approx(0.75, abs=1e-9)


def test_reference_crushing_scales_with_strength_and_h_squared():
    slope = loglog_slope(lambda h: canon_terms(h)["crushing"], H_GENERIC)
    assert slope == pytest.approx(2.0, abs=0.02)
    # linear in ice strength
    r1 = canon_terms(sigma=SIGMA_REF)["crushing"]
    r2 = canon_terms(sigma=2 * SIGMA_REF)["crushing"]
    assert r2 / r1 == pytest.approx(2.0, rel=1e-12)


def test_reference_breaking_scales_as_h_pow_1p25_and_linearly_in_beam():
    slope_h = loglog_slope(lambda h: canon_terms(h)["breaking"], H_GENERIC)
    assert slope_h == pytest.approx(1.25, abs=0.02)
    r1 = canon_terms()["breaking"]
    c2 = CANON.ice_components(length_m=L, beam_m=2 * B, draft_m=T, cb=CB,
                               bow_angle_deg=ALPHA, h_m=H_REF, sigma_c_pa=SIGMA_REF,
                               mu=MU_REF, v_ms=V_REF)
    assert c2.breaking_n / r1 == pytest.approx(2.0, rel=1e-12)


def test_reference_submersion_is_frontal_area_times_thickness_and_reacts_to_draft():
    slope = loglog_slope(lambda h: canon_terms(h)["submersion"], H_GENERIC)
    assert slope == pytest.approx(1.0, abs=1e-12)
    deeper = CANON.ice_components(length_m=L, beam_m=B, draft_m=T + 1.0, cb=CB,
                                  bow_angle_deg=ALPHA, h_m=H_REF, sigma_c_pa=SIGMA_REF,
                                  mu=MU_REF, v_ms=V_REF).submersion_n
    assert deeper > canon_terms()["submersion"] * 1.05


def test_reference_is_monotone_in_every_physical_driver():
    base = canon_total()
    assert canon_total(h=H_REF * 1.2) > base
    assert canon_total(sigma=SIGMA_REF * 1.2) > base
    assert canon_total(mu=MU_REF * 1.5) > base
    assert canon_total(v=V_REF * 1.5) > base


def test_reference_rejects_degenerate_input():
    with pytest.raises(ValueError):
        CANON.characteristic_length(0.0)
    with pytest.raises(ValueError):
        CANON.ice_components(length_m=L, beam_m=B, draft_m=T, cb=CB,
                             bow_angle_deg=ALPHA, h_m=-1.0, sigma_c_pa=SIGMA_REF,
                             mu=MU_REF, v_ms=V_REF)


# ---------------------------------------------------------------------------
# Polar production path (C): term anchors (must PASS)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("term,target,tol", [("crushing", 2.0, 0.03),
                                             ("breaking", 1.25, 0.03),
                                             ("submersion", 1.0, 0.02)])
def test_production_term_exponents_match_analytic(term, target, tol):
    v = 0.0 if term == "submersion" else V_REF   # isolate the added-mass factor
    slope = loglog_slope(lambda h: polar_terms(h, v=v)[term], H_GENERIC)
    assert slope == pytest.approx(target, rel=tol)


def test_production_agrees_with_reference_within_convention_factor():
    """C vs R differ only by the documented C_crush / C_break conventions.

    Both are evaluated at V = 0: C folds the added-mass factor into the
    submersion term while R reports it separately (``speed_term_n``).  The
    breaking term is 15 % lower in C because C uses E = 5.0 GPa, nu = 0.30
    while R defaults to E = 2.5 GPa, nu = 0.33; l_c ~ E^(1/4) gives
    2^(-1/4) = 0.841.  Both moduli sit inside the documented 0.5-10 GPa band,
    so this is a convention choice, not a defect - see the report.
    """
    for term, tol in (("crushing", 0.01), ("breaking", 0.20), ("submersion", 0.01)):
        c = polar_terms(v=0.0)[term]
        r = canon_terms(v=0.0)[term]
        assert c / r == pytest.approx(1.0, rel=tol), term


def test_reference_breaking_sensitivity_to_ice_modulus():
    """l_c ~ E^(1/4): halving the ice modulus multiplies l_c by 2^(-1/4)."""
    from services.ice_canon import characteristic_length
    assert characteristic_length(H_REF, e_ice=2.5e9) / characteristic_length(
        H_REF, e_ice=5.0e9) == pytest.approx(2.0 ** -0.25, rel=1e-12)


def test_production_added_mass_response_is_thirteen_percent():
    s2 = polar_terms(v=2.0, sigma=1e-6)["submersion"]
    s3 = polar_terms(v=3.0, sigma=1e-6)["submersion"]
    analytic = (1 + 0.5 * 3.0 / math.sqrt(9.81 * H_REF)) / (1 + 0.5 * 2.0 / math.sqrt(9.81 * H_REF))
    assert s3 / s2 == pytest.approx(analytic, rel=0.02)


# ---------------------------------------------------------------------------
# Known-debt ledger (xfail strict: fix the defect -> update this file)
# ---------------------------------------------------------------------------
@pytest.mark.xfail(strict=True, reason=(
    "DEBT-ICE-1 backend/services/ice_resistance_uq.py: the submersion term is "
    "built as (rho_w-rho_i)*g*h*B*(...)*0.3*L and never sees the draft, so the "
    "whole model has dR/dT = 0. A deeper hull cannot cost more submersion "
    "resistance - physically impossible. Anchor: R_sub ~ h*B*T."))
def test_debt_a_model_is_blind_to_draft():
    d1 = a_total(draft=8.5)
    d2 = a_total(draft=10.5)
    assert d2 > d1 * 1.05


@pytest.mark.xfail(strict=True, reason=(
    "DEBT-ICE-2 backend/services/ice_resistance_uq.py: the speed term is "
    "0.05*rho*B*h*V^2, i.e. 0.25 % of the total, and the submersion component "
    "carries no added-mass factor. V:2->3 m/s moves the resistance by +0.46 % "
    "where the classical decomposition (1 + C_v V/sqrt(g h)) requires +13.2 %."))
def test_debt_a_model_is_speed_insensitive():
    s2 = a_total(v=2.0, sigma=1e-6)
    s3 = a_total(v=3.0, sigma=1e-6)
    assert s3 / s2 - 1.0 >= 0.06


@pytest.mark.xfail(strict=True, reason=(
    "DEBT-ICE-3 optimizer/ice_class/objectives.py: ice strength is a module "
    "constant SIGMA_F = 500 kPa and is not an argument of "
    "evaluate_ice_resistance, so dR/dsigma_c is exactly 0. The ice-class design "
    "optimiser cannot trade strength against resistance."))
def test_debt_optimizer_ignores_ice_strength():
    # the public signature has no strength argument at all
    import inspect
    params = inspect.signature(OPT.evaluate_ice_resistance).parameters
    assert any("sigma" in p for p in params)


@pytest.mark.xfail(strict=True, reason=(
    "DEBT-ICE-4 optimizer/ice_class/objectives.py: friction is the module "
    "constant MU_ICE = 0.1, so dR/dmu = 0; hull-ice friction is a design "
    "variable in ice-class work and must be exposed."))
def test_debt_optimizer_ignores_friction():
    import inspect
    params = inspect.signature(OPT.evaluate_ice_resistance).parameters
    assert any(("mu" in p) or ("friction" in p) for p in params)


@pytest.mark.xfail(strict=True, reason=(
    "DEBT-ICE-5 optimizer/ice_class/objectives.py: on the audit hull and "
    "conditions the model returns 8.7 % of the reference decomposition "
    "(11.5x low), and 11-16 % of the polar production path on the three in-repo "
    "trial ships. Also: crushing uses sigma_f*h^1 (not h^2) and the bending term "
    "27*sigma_f*h^2*l_c/(B_eff+1) decreases with beam. The objective is used as "
    "a minimisation target by the NSGA-II ice-class optimiser."))
def test_debt_optimizer_magnitude_envelope():
    ratio = b_total() / canon_total()
    assert 1.0 / 3.0 <= ratio <= 3.0


@pytest.mark.xfail(strict=True, reason=(
    "DEBT-ICE-6 optimizer/ice_class/objectives.py bending term carries "
    "l_c = sqrt(sigma_f h^2/(3 rho_i g)) (h^1) instead of "
    "(E h^3/(12 rho g (1-nu^2)))^(1/4) (h^0.75), so the breaking component "
    "scales as h^3 instead of h^1.25."))
def test_debt_optimizer_bending_exponent():
    def breaking(h: float) -> float:
        l_c = math.sqrt(5.0e5 * h ** 2 / (3 * 917.0 * 9.81))
        b_eff = B * math.sin(math.radians(ALPHA)) * (0.5 + 0.5 * CB)
        return 27.0 * 5.0e5 * h ** 2 * l_c / (b_eff + 1.0)
    slope = loglog_slope(breaking, H_GENERIC)
    assert slope == pytest.approx(1.25, abs=0.03)