"""Physics guards for the ship platform's panel-buckling (plate-stability) domain.

The suite has two halves:

* offline reference self-checks - the closed-form oracle in
  ``tools/verification/structural_buckling/ref_buckling.py`` must reproduce the
  textbook / classification-society constants (Timoshenko & Gere Ch. 9,
  DNV-RP-C201 Table 6-1) to well inside the 3% acceptance band.  These always run.
* live guards against the deployed endpoint :8001 - skipped automatically when the
  service is unreachable, so the suite stays green in an offline CI.

The live guard on the *clamped* buckling factor is marked ``xfail(strict=False)``:
it documents a deployed defect (k = (a/b + b/a)^2 instead of 6.97) instead of
failing the build.  When the platform fix lands the test flips to XPASS - which is
the signal to promote it to a hard assertion.

Run:  pytest -q tests/test_plate_stability_physics_guard.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "verification", "structural_buckling"))
import ref_buckling as ref  # noqa: E402

E, NU, SY = 206e9, 0.30, 315e6
ARTIFACT = os.path.join(ROOT, "artifacts", "verification", "structural_buckling", "audit.json")


# --------------------------------------------------------------------------- #
# offline: closed-form oracle vs textbook constants
# --------------------------------------------------------------------------- #
def test_handy_coefficient_matches_textbook():
    """sigma_cr = C E (t/b)^2 with C = 3.62 (SS) and 6.31 (clamped)."""
    assert ref.handy_coefficient(4.00) == pytest.approx(3.615, abs=0.01)
    assert ref.handy_coefficient(6.97) == pytest.approx(6.300, abs=0.02)


def test_simply_supported_factor_minimum_is_four():
    for aspect in (1.0, 2.0, 3.0, 4.0, 6.0):
        assert ref.k_uniaxial(aspect, clamped=False) == pytest.approx(4.0, rel=1e-9)


def test_simply_supported_stress_closed_form():
    """Independent closed form: sigma_cr = 3.62 E (t/b)^2 for an integer-aspect SS panel."""
    for t in (0.010, 0.012, 0.016, 0.020):
        sig = ref.sigma_cr_plate(E, NU, 0.8, t, 3.0, clamped=False)
        assert sig == pytest.approx(3.615 * E * (t / 0.8) ** 2 / 1e6, rel=0.01)


def test_clamped_factor_and_stress():
    assert ref.k_uniaxial(3.0, clamped=True) == pytest.approx(6.97, rel=1e-12)
    sig = ref.sigma_cr_plate(E, NU, 0.8, 0.012, 3.0, clamped=True)
    assert sig == pytest.approx(ref.sigma_cr_plate(E, NU, 0.8, 0.012, 3.0) * 6.97 / 4.0, rel=1e-9)


def test_shear_factor_and_stress():
    """Long simply supported panel: k_s -> 5.34, tau_cr = 4.83 E (t/b)^2."""
    assert ref.k_shear(50.0) == pytest.approx(5.34, abs=5e-3)
    assert ref.k_shear(2.0) == pytest.approx(5.34 + 1.0, rel=1e-12)
    tau = ref.tau_cr_plate(E, NU, 0.8, 0.012, 50.0)
    assert tau == pytest.approx(4.825 * E * (0.012 / 0.8) ** 2 / 1e6, rel=0.01)


def test_euler_column_equals_slenderness_form():
    """pi^2 E I /(A L^2) must equal pi^2 E / (L/r)^2 for a solid square strut."""
    a, span = 0.05, 4.0
    I, A = a ** 4 / 12.0, a ** 2
    sig = ref.sigma_euler_column(E, I, A, span)
    assert sig == pytest.approx(math.pi ** 2 * E / (span / math.sqrt(I / A)) ** 2 / 1e6, rel=1e-12)


def test_section_properties_of_tee_with_plate():
    """Centroid/inertia of a tee-with-plate against an independent layer sum."""
    sec = ref.TeeSection(hw=0.30, tw=0.010, bf=0.12, tf=0.015, s=0.8, t=0.012).props()
    layers = [(0.30 * 0.010, 0.15), (0.12 * 0.015, 0.30 + 0.0075), (0.8 * 0.012, -0.006)]
    A = sum(a for a, _ in layers)
    y_bar = sum(a * y for a, y in layers) / A
    I = sum(b * h ** 3 / 12.0 + a * (y - y_bar) ** 2
            for (a, y), (b, h) in zip(layers, [(0.010, 0.30), (0.12, 0.015), (0.8, 0.012)]))
    assert sec["A"] == pytest.approx(A, rel=1e-12)
    assert sec["y_bar"] == pytest.approx(y_bar, rel=1e-12)
    assert sec["I"] == pytest.approx(I, rel=1e-12)


def test_ultimate_strength_correlations():
    """Faulkner beta=2 -> 0.75 sigma_y; Johnson-Ostenfeld continuity at sigma_y/2."""
    assert ref.faulkner_ultimate(SY, 2.0 * math.sqrt(E / SY), 1.0, E) == pytest.approx(0.75 * SY / 1e6, rel=1e-9)
    assert ref.johnson_ostenfeld(0.4 * SY / 1e6, SY / 1e6) == pytest.approx(0.4 * SY / 1e6, rel=1e-12)
    assert ref.johnson_ostenfeld(1.0 * SY / 1e6, SY / 1e6) == pytest.approx(0.75 * SY / 1e6, rel=1e-12)


# --------------------------------------------------------------------------- #
# recorded audit artifact (zero hand-copied numbers)
# --------------------------------------------------------------------------- #
def test_audit_artifact_reproduces_deployed_clamped_factor():
    """The archived audit must show k = (a/b + b/a)^2, i.e. the deployed defect."""
    if not os.path.exists(ARTIFACT):
        pytest.skip("audit artifact not generated in this checkout")
    rows = json.load(open(ARTIFACT, encoding="utf-8"))["plate"]
    checked = 0
    for r in rows:
        if r["bc"] == "clamped" and "k_served" in r:
            alpha = r["aspect"]
            assert r["k_served"] == pytest.approx((alpha + 1.0 / alpha) ** 2, rel=2e-3)
            assert r["k_ref"] == pytest.approx(6.97, rel=1e-12)
            checked += 1
    assert checked >= 4


# --------------------------------------------------------------------------- #
# live guards (skipped when :8001 / :8002 is not serving)
# --------------------------------------------------------------------------- #
def _post(path: str, body: dict):
    for port in (8001, 8002):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/plate-stability{path}",
                                     data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001
            continue
    return None


def _plate_payload(aspect: float, bc: str) -> dict:
    return {"a": aspect * 0.8, "b": 0.8, "t": 0.012, "E": E, "nu": NU, "sigma_y": SY,
            "edge_condition": bc, "axial_load_a": 1.0e6, "axial_load_b": 0.0,
            "shear_load": 0.0, "lateral_pressure": 0.0}


def test_live_simply_supported_within_three_percent():
    got = _post("/plate", _plate_payload(3.0, "simply_supported"))
    if got is None:
        pytest.skip("plate-stability endpoint unreachable")
    want = ref.sigma_cr_plate(E, NU, 0.8, 0.012, 3.0, clamped=False)
    assert abs(got["critical_stress_a"] - want) / want < 0.03


def test_live_shear_within_three_percent():
    got = _post("/plate", _plate_payload(3.0, "simply_supported"))
    if got is None:
        pytest.skip("plate-stability endpoint unreachable")
    want = ref.tau_cr_plate(E, NU, 0.8, 0.012, 3.0)
    assert abs(got["critical_shear"] - want) / want < 0.03


@pytest.mark.xfail(reason="deployed defect: clamped k = (a/b+b/a)^2 instead of 6.97 "
                          "(DNV-RP-C201 Tab. 6-1); non-conservative for a/b >= 3",
                   strict=False)
def test_live_clamped_within_three_percent():
    got = _post("/plate", _plate_payload(3.0, "clamped"))
    if got is None:
        pytest.skip("plate-stability endpoint unreachable")
    want = ref.sigma_cr_plate(E, NU, 0.8, 0.012, 3.0, clamped=True)
    assert abs(got["critical_stress_a"] - want) / want < 0.03


@pytest.mark.xfail(reason="deployed defect: stiffener flexural buckling uses the plate "
                          "width as span (no span input exists on the endpoint)",
                   strict=False)
def test_live_stiffener_flexural_uses_frame_span():
    got = _post("/stiffener", {"stiffener_type": "T-bar", "web_height": 0.30,
                               "web_thickness": 0.010, "flange_width": 0.12,
                               "flange_thickness": 0.015, "plate_width": 0.8,
                               "plate_thickness": 0.012, "E": E, "sigma_y": SY,
                               "axial_load": 1.0e6})
    if got is None:
        pytest.skip("plate-stability endpoint unreachable")
    sec = ref.TeeSection(0.30, 0.010, 0.12, 0.015, 0.8, 0.012).props()
    want = ref.sigma_euler_column(E, sec["I"], sec["A"], 4.0)  # 4 m transverse spacing
    assert abs(got["flexural_buckling"] - want) / want < 0.03