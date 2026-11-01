"""Regression guards for the wetted-surface models behind /calculations/resistance.

Mission m1686 (selfstart tp-impl-8).  These tests lock in the verification of
``tools/verif/verify_wetted_surface.py``:

* the Holtrop & Mennen (1982) regression — now reachable from the endpoint via
  ``hull_form="ship"`` — stays within 3 % of the published KVLCC2 wetted surface
  (27 194 m^2, SIMMAN 2008);
* the legacy body-of-revolution estimator keeps its *documented* bias (+50 % on
  a KVLCC2-class hull, -35 % on the prolate spheroid it was fitted to) and the
  endpoint now says so in ``wetted_surface_warnings`` instead of staying silent;
* the default (``hull_form="auto"``, no coefficients) stays bit-compatible with
  the pre-m1686 response, because downstream benchmark rows depend on it;
* a wrong S cancels out of ``C_T`` (every term of the endpoint is proportional
  to S) and only moves ``R_T`` — so ``C_T`` must never be used as an S check.

Run with:  python3 -m pytest tests/test_wetted_surface_holtrop_verification.py -q
(parked outside backend/tests because that tree's conftest boots the full
FastAPI app, which needs the DB stack.)
"""
from __future__ import annotations

import asyncio
import importlib.util
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

RHO = 998.0
NU = 1.004e-6

KVLCC2 = dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098, cm=0.998, cwp=0.8748)
KVLCC2_PUBLISHED_S = 27194.0


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


calc = _load(BACKEND / "api" / "calculations.py", "tp_ws_calc_test")
verif = _load(REPO / "tools" / "verif" / "verify_wetted_surface.py", "tp_ws_verif_test")


def _resistance(**kwargs):
    return asyncio.run(calc.calc_resistance(calc.ResistanceRequest(**kwargs)))


def _kvlcc2_kwargs(**over):
    kw = dict(
        ship_type="KVLCC2",
        speed_ms=15.5 * 0.514444,
        length=KVLCC2["length"],
        beam=KVLCC2["beam"],
        draft=KVLCC2["draft"],
        cb=KVLCC2["cb"],
        water_density=RHO,
        kinematic_viscosity=NU,
    )
    kw.update(over)
    return kw


# ---------------------------------------------------------------------------
# 1. Holtrop regression vs published reference
# ---------------------------------------------------------------------------

def test_holtrop_matches_published_kvlcc2_surface():
    s, cm, cwp, warns = calc._holtrop_wetted_surface(
        KVLCC2["length"], KVLCC2["beam"], KVLCC2["draft"], KVLCC2["cb"],
        KVLCC2["cm"], KVLCC2["cwp"],
    )
    err = (s - KVLCC2_PUBLISHED_S) / KVLCC2_PUBLISHED_S * 100.0
    assert abs(err) < 3.0, f"Holtrop S={s:.1f} m2, reference={KVLCC2_PUBLISHED_S}, err={err:.2f}%"
    assert cm == pytest.approx(KVLCC2["cm"])
    assert cwp == pytest.approx(KVLCC2["cwp"])
    assert warns == []  # caller supplied both coefficients: no estimate warnings


def test_holtrop_estimated_coefficients_stay_within_3pct():
    s_est = calc._holtrop_wetted_surface(
        KVLCC2["length"], KVLCC2["beam"], KVLCC2["draft"], KVLCC2["cb"]
    )[0]
    err = (s_est - KVLCC2_PUBLISHED_S) / KVLCC2_PUBLISHED_S * 100.0
    assert abs(err) < 3.0, f"estimated-coefficient S err={err:.2f}%"


def test_holtrop_insensitive_to_midship_coefficient_estimate():
    """The Cm fallback must not be a hidden 10 % lever: it moves S < 1 %."""
    base = calc._holtrop_wetted_surface(
        KVLCC2["length"], KVLCC2["beam"], KVLCC2["draft"], KVLCC2["cb"], 0.998
    )[0]
    for cm in (0.95, 0.98, 1.0):
        s = calc._holtrop_wetted_surface(
            KVLCC2["length"], KVLCC2["beam"], KVLCC2["draft"], KVLCC2["cb"], cm
        )[0]
        assert abs(s - base) / base < 0.01, f"Cm={cm} moved S by {(s - base) / base * 100:.2f}%"


# ---------------------------------------------------------------------------
# 2. Documented bias of the legacy estimator
# ---------------------------------------------------------------------------

def test_legacy_estimator_overestimates_ship_hull():
    s_legacy = calc._empirical_wetted_surface(
        KVLCC2["length"], KVLCC2["beam"], KVLCC2["cb"]
    )
    err = (s_legacy - KVLCC2_PUBLISHED_S) / KVLCC2_PUBLISHED_S * 100.0
    assert 40.0 < err < 60.0, f"legacy S err={err:.2f}% (expected ~+50 %)"
    assert s_legacy == pytest.approx(
        math.pi * KVLCC2["beam"] * KVLCC2["length"] * (0.5 + 0.25 * KVLCC2["cb"])
    )


def test_legacy_estimator_underestimates_its_own_shape_family():
    """On a prolate spheroid (the family it was fitted to) it is biased *low*."""
    length, diameter = 4.0, 0.5
    s_exact = verif.spheroid_surface(length, diameter)
    cb_eq = (4.0 / 3.0) * math.pi * (diameter / 2) ** 3 / (length * diameter * diameter)
    s_legacy = calc._empirical_wetted_surface(length, diameter, cb_eq)
    err = (s_legacy - s_exact) / s_exact * 100.0
    assert -45.0 < err < -25.0, f"spheroid legacy err={err:.2f}%"


# ---------------------------------------------------------------------------
# 3. Endpoint behaviour: model selection, warnings, backward compatibility
# ---------------------------------------------------------------------------

def test_ship_hull_routes_through_holtrop_and_reports_coefficients():
    resp = _resistance(**_kvlcc2_kwargs(
        hull_form="ship",
        midship_coefficient=KVLCC2["cm"],
        waterplane_coefficient=KVLCC2["cwp"],
    ))
    assert resp.wetted_surface_source == "holtrop_mennen_1982"
    assert resp.wetted_surface_m2 == pytest.approx(26725.55, rel=1e-4)
    assert resp.midship_coefficient == pytest.approx(0.998)
    assert resp.waterplane_coefficient == pytest.approx(0.8748)
    assert abs(resp.wetted_surface_m2 - KVLCC2_PUBLISHED_S) / KVLCC2_PUBLISHED_S < 0.03


def test_ship_hull_without_coefficients_warns_about_estimates():
    resp = _resistance(**_kvlcc2_kwargs(hull_form="ship"))
    assert any("midship_coefficient_estimated" in w for w in resp.wetted_surface_warnings)
    assert any("waterplane_coefficient_estimated" in w for w in resp.wetted_surface_warnings)


def test_auto_path_warns_instead_of_silently_using_the_axisymmetric_fit():
    resp = _resistance(**_kvlcc2_kwargs())  # auto, no coefficients -> legacy
    assert resp.wetted_surface_source == "empirical_formula"
    assert any("legacy_axisymmetric_wetted_surface" in w for w in resp.wetted_surface_warnings)
    # explicit axisymmetric: same numbers, no nagging
    resp_ax = _resistance(**_kvlcc2_kwargs(hull_form="axisymmetric"))
    assert resp_ax.wetted_surface_source == "empirical_formula"
    assert resp_ax.wetted_surface_warnings == []
    assert resp_ax.wetted_surface_m2 == pytest.approx(resp.wetted_surface_m2)


def test_default_path_is_bit_compatible_with_pre_m1686_behaviour():
    """No new request field -> identical numbers (downstream benchmark rows)."""
    resp = _resistance(**_kvlcc2_kwargs())
    legacy = calc._empirical_wetted_surface(
        KVLCC2["length"], KVLCC2["beam"], KVLCC2["cb"]
    )
    assert resp.wetted_surface_m2 == pytest.approx(round(legacy, 6))
    assert resp.midship_coefficient is None
    assert resp.waterplane_coefficient is None


def test_geometry_validated_surface_still_wins():
    resp = _resistance(**_kvlcc2_kwargs(hull_form="ship", wetted_surface_m2=27194.0))
    assert resp.wetted_surface_source == "input_validated"
    assert resp.wetted_surface_m2 == pytest.approx(27194.0)


def test_inconsistent_cb_and_cm_is_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _resistance(**_kvlcc2_kwargs(hull_form="ship", midship_coefficient=0.45))
    assert exc.value.status_code == 400
    assert "Cb/Cm" in exc.value.detail


# ---------------------------------------------------------------------------
# 4. Why C_T is not an S check (finding of the verification harness)
# ---------------------------------------------------------------------------

def test_wrong_wetted_surface_cancels_in_ct_but_moves_rt():
    ship = _resistance(**_kvlcc2_kwargs(hull_form="ship"))
    legacy = _resistance(**_kvlcc2_kwargs(hull_form="axisymmetric"))
    assert legacy.wetted_surface_m2 > ship.wetted_surface_m2 * 1.4
    # identical C_T: every term (Rf, Rform, Rr) is proportional to S
    assert legacy.total_resistance_coefficient == pytest.approx(
        ship.total_resistance_coefficient, rel=1e-9
    )
    # ... but R_T moves with S, so power/EHP moves with the S error
    ratio = legacy.total_resistance_n / ship.total_resistance_n
    s_ratio = legacy.wetted_surface_m2 / ship.wetted_surface_m2
    assert ratio == pytest.approx(s_ratio, rel=1e-6)
    assert 1.5 < ratio < 1.6, f"R_T ratio={ratio:.3f}"


# ---------------------------------------------------------------------------
# 5. Platform geometry integration vs quasi-analytic Wigley reference
# ---------------------------------------------------------------------------

def test_wigley_girth_integration_matches_quasi_analytic_reference():
    from services.ship_hydrostatics import compute_hydrostatics, wigley_offsets

    L, B, T = 10.0, 1.0, 0.625
    reference = verif.wigley_surface_reference(L, B, T, n=320)
    offs = wigley_offsets(length=L, beam=B, draft=T, n_stations=81, n_waterlines=40)
    hydro = compute_hydrostatics(offs)
    err = (hydro.wetted_surface_m2 - reference) / reference * 100.0
    assert abs(err) < 1.0, f"girth integration err={err:.3f}% vs {reference:.4f} m2"
    # the quasi-analytic reference is itself converged to <1e-4 %
    coarse = verif.wigley_surface_reference(L, B, T, n=160)
    assert abs(coarse - reference) / reference * 100.0 < 1e-4


def test_wigley_coefficients_are_reproduced_by_the_offsets():
    from services.ship_hydrostatics import compute_hydrostatics, wigley_offsets

    offs = wigley_offsets(length=10.0, beam=1.0, draft=0.625, n_stations=81, n_waterlines=40)
    hydro = compute_hydrostatics(offs)
    # exact Wigley values: Cb = (2/3)^2, Cm = Cp = Cwp = 2/3
    assert hydro.cb == pytest.approx(4.0 / 9.0, abs=5e-3)
    assert hydro.cm == pytest.approx(2.0 / 3.0, abs=5e-3)
    assert hydro.cw == pytest.approx(2.0 / 3.0, abs=5e-3)