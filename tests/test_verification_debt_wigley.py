"""Verification-debt gate for the Wigley / Michell resistance audit.

Layer 1 (offline, always run): the *reference* implementations must be right,
otherwise the audited numbers are meaningless.  These pin

* the closed-form Wigley geometry (Cb = 4/9, volume = 4LBT/9, Awp = 2LB/3) and
  the convergence of the wetted-surface quadrature;
* the ITTC-1957 line against its closed form;
* the Michell integral through four independent checks: vectorised == scalar
  reference, route A == route B (numerical dy/dx), theta refinement, and the
  classical Wigley first hump (Fn ~ 0.30, Cw ~ 4e-3);
* the wall-sided pontoon hydrostatics/GZ references used by the stability
  section of the audit.

Layer 2 (live API, skipped when :8001 is unreachable): contract facts that the
ledger relies on - identities, Froude invariance, and the wetted-surface bias
which is the documented verification debt.

Run:  pytest tests/test_verification_debt_wigley.py -q
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

REF_DIR = (Path(__file__).resolve().parents[1]
           / "docs" / "verification_debt" / "wigley_michell")
sys.path.insert(0, str(REF_DIR))

import references as ref  # noqa: E402
from michell_wigley import (  # noqa: E402
    Wigley,
    michell_cw,
    michell_rw,
    rw_double_quad,
    rw_theta_integral_scalar,
    wetted_surface,
)

API_BASE = "http://127.0.0.1:8001/api/v1"


# ---------------------------------------------------------------------------
# Layer 1: reference implementations
# ---------------------------------------------------------------------------
def test_wigley_geometry_is_closed_form():
    h = Wigley(L=100.0, B=10.0, T=6.25)
    assert h.cb == pytest.approx(4.0 / 9.0, rel=1e-15)
    assert h.volume == pytest.approx(4.0 * 100.0 * 10.0 * 6.25 / 9.0, rel=1e-15)
    assert h.waterplane_area == pytest.approx(2.0 * 100.0 * 10.0 / 3.0, rel=1e-15)
    # displacement of the section at midship must equal B*T*2/3 (parabolic)
    n = 2000
    zeta = np.linspace(0.0, h.T, n)
    section = 2.0 * np.array([h.half_breadth(0.0, z) for z in zeta])
    area = float(np.trapezoid(section, zeta))
    assert area == pytest.approx(2.0 / 3.0 * h.B * h.T, rel=1e-6)


def test_wetted_surface_quadrature_converged():
    h = Wigley()
    s1 = wetted_surface(h, 240, 120)
    s2 = wetted_surface(h, 480, 240)
    assert abs(s2 - s1) / s2 < 1e-9
    assert s1 == pytest.approx(1487.9063, rel=1e-5)


def test_ittc57_line():
    # closed form of the ITTC-1957 line, spot values
    for re_ in (1e6, 1e7, 1e8, 1e9):
        cf = ref.ittc57_cf(re_)
        assert cf == pytest.approx(0.075 / (math.log10(re_) - 2.0) ** 2, rel=1e-15)
    assert ref.ittc57_cf(1e9) == pytest.approx(1.531e-3, rel=5e-3)


def test_michell_vectorised_matches_scalar_reference():
    h = Wigley()
    for fn in (0.20, 0.30, 0.40):
        u = fn * math.sqrt(ref.G * h.L)
        a = michell_rw(h, u, n_theta=200)
        b = rw_theta_integral_scalar(h, u, n_theta=120)
        assert a == pytest.approx(b, rel=2e-3)


def test_michell_two_independent_routes_agree():
    h = Wigley()
    for fn in (0.20, 0.25, 0.30, 0.35, 0.40):
        u = fn * math.sqrt(ref.G * h.L)
        a = michell_rw(h, u, n_theta=400)
        b = rw_double_quad(h, u, n_x=120, n_z=80, n_theta=100)
        assert b > 0.0, "route B must produce a positive resistance"
        assert abs(a / b - 1.0) < 2e-2


def test_michell_theta_refinement():
    h = Wigley()
    for fn in (0.25, 0.30):
        u = fn * math.sqrt(ref.G * h.L)
        r1 = michell_rw(h, u, n_theta=400)
        r2 = michell_rw(h, u, n_theta=800)
        assert abs(r1 - r2) / r2 < 1e-6


def test_michell_classical_wigley_hump():
    """Thin-ship theory for the Wigley hull: first hump near Fn = 0.30, Cw ~ 4e-3.

    This is the literature anchor that pins the Michell prefactor: if the
    constant in Rw = 8 rho g^2/(pi U^2) ... were wrong by a factor, the hump
    magnitude would move out of the band immediately.
    """
    h = Wigley()
    s = wetted_surface(h)
    fns = np.arange(0.25, 0.36, 0.01)
    cw = [michell_cw(h, fn * math.sqrt(ref.G * h.L), s_ref=s) for fn in fns]
    i = int(np.argmax(cw))
    assert 0.28 <= fns[i] <= 0.32
    assert 3.5e-3 <= cw[i] <= 5.2e-3


def test_michell_low_froude_decay_is_steep():
    """Below Fn = 0.15 thin-ship resistance must decay steeply (no plateau)."""
    h = Wigley()
    rw = [michell_rw(h, fn * math.sqrt(ref.G * h.L)) for fn in (0.12, 0.14, 0.16, 0.20)]
    assert rw[0] < rw[1] < rw[2] < rw[3]
    assert rw[0] / rw[3] < 0.10


def test_pontoon_reference_is_exact():
    h = ref.barge_hydrostatics(B=20.0, T=8.0, KG=6.0)
    assert h["KB"] == pytest.approx(4.0)
    assert h["BM"] == pytest.approx(400.0 / (12.0 * 8.0))
    assert h["KM"] == pytest.approx(4.0 + 400.0 / 96.0)
    assert h["GM"] == pytest.approx(4.0 + 400.0 / 96.0 - 6.0)
    # wall-sided GZ at small angles -> GM * sin(phi)
    for phi in (2.0, 5.0):
        gz = ref.barge_gz(20.0, 8.0, 6.0, phi)
        assert gz == pytest.approx(h["GM"] * math.sin(math.radians(phi)), rel=2e-2)


# ---------------------------------------------------------------------------
# Layer 2: live platform
# ---------------------------------------------------------------------------
def _api():
    requests = pytest.importorskip("requests")
    try:
        r = requests.get(f"{API_BASE.replace('/api/v1', '')}/health", timeout=2)
        if r.status_code >= 500:
            pytest.skip("platform not healthy")
    except Exception:
        pytest.skip("ship platform :8001 not reachable")
    return requests


def _predict(requests, hull: Wigley, speeds_kn):
    body = {"Lwl": hull.L, "Lpp": hull.L, "B": hull.B, "T": hull.T,
            "displacement": hull.displacement_t, "Cb": hull.cb,
            "Cm": 2.0 / 3.0, "Cwp": 2.0 / 3.0, "speeds": list(speeds_kn)}
    r = requests.post(f"{API_BASE}/resistance/predict", json=body, timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def test_live_identities_and_froude_invariance():
    requests = _api()
    hull = Wigley()
    rows = _predict(requests, hull, [6.0, 12.0, 18.0])
    for row in rows:
        u = row["speed"] * ref.KNOT
        assert row["froude_number"] == pytest.approx(
            ref.fn_of_speed(u, hull.L), rel=5e-3)
        assert row["reynolds_number"] == pytest.approx(
            ref.re_of_speed(u, hull.L, 1.19e-6), rel=1e-3)
        assert row["effective_power"] == pytest.approx(
            row["total_resistance"] * u, rel=1e-3)
    # Rw must scale as L^3 at equal Fn (scale invariance)
    fn, scale = 0.20, 2.0
    big = Wigley(L=hull.L * scale, B=hull.B * scale, T=hull.T * scale)
    u = fn * math.sqrt(ref.G * big.L)
    rw_big = _predict(requests, big, [u / ref.KNOT])[0]["wave_resistance"]
    u0 = fn * math.sqrt(ref.G * hull.L)
    rw_small = _predict(requests, hull, [u0 / ref.KNOT])[0]["wave_resistance"]
    assert rw_big / rw_small == pytest.approx(scale ** 3, rel=1e-2)


def test_live_wetted_surface_bias_is_known_and_bounded():
    """Documented verification debt: implied S is ~11-16% below the exact Wigley surface.

    The platform estimates S from its regression input set, so Rf (and the
    low-speed Rt) is biased low by the same amount.  The assertion range keeps
    the debt visible and machine-checked: if it is ever fixed, this test starts
    to fail on the lower bound and the ledger must be updated.
    """
    requests = _api()
    hull = Wigley()
    rows = _predict(requests, hull, [8.0, 12.0, 16.0])
    s_imp = []
    for row in rows:
        u = row["speed"] * ref.KNOT
        cf = ref.ittc57_cf(ref.re_of_speed(u, hull.L, 1.19e-6))
        s_imp.append(row["friction_resistance"] * 1e3 / (0.5 * 1025.0 * cf * u ** 2))
    # friction law itself is self-consistent across speed
    assert (max(s_imp) - min(s_imp)) / float(np.mean(s_imp)) < 0.01
    rel = float(np.mean(s_imp)) / wetted_surface(hull) - 1.0
    assert -0.16 <= rel <= -0.11, f"implied-S bias changed: {100*rel:+.2f}%"