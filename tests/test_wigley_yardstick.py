"""Offline gate for the Wigley/Michell yardstick (m1813).

These tests never touch the network.  They pin the *reference* side, which is
what the platform audit is measured against:

* the two Michell routes must agree (route B was broken in the m1568 package:
  complex amplitude cast to float -> Rw ~ 1e-28, plus a spurious mirror factor);
* theta quadrature must be converged;
* two exact structural laws of the integral must hold (Rw ~ B^2 and Froude
  scaling Rw(lambda) = lambda^3 Rw) -- these are independent of the convention
  constant, so they are the strongest available offline checks;
* exact hydrostatics (Cb = 4/9, nabla = 4LBT/9, pontoon KB/BM/KM/GM);
* the verdict engine and the snapshot replay (no hand-copied number in the
  ledger);
* the absolute Michell level stays flagged WITHHELD.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ship_verification import wigley_reaudit as ra  # noqa: E402
from src.ship_verification import wigley_yardstick as y  # noqa: E402


# ---------------------------------------------------------------------------
# 1. reference side: two independent routes
# ---------------------------------------------------------------------------
def test_two_michell_routes_agree():
    """Route B must reproduce route A (the m1568 defect returned ~1e-28 N)."""
    L, B, T = y.STANDARD_L, y.STANDARD_B, y.STANDARD_T
    for fn in (0.20, 0.28):
        U = fn * math.sqrt(y.G * L)
        hull = y.Wigley(L=L, B=B, T=T, rho=y.RHO)
        a = y.rw_route_a(U, hull)
        b = y.rw_route_b(U, hull)
        assert a > 1.0 and b > 1.0
        assert abs(b - a) / a < 0.01, f"Fn={fn}: route A={a:.4e} route B={b:.4e}"


def test_theta_quadrature_converged():
    L = y.STANDARD_L
    for fn in (0.15, 0.25):
        U = fn * math.sqrt(y.G * L)
        hull = y.Wigley(L=L, B=y.STANDARD_B, T=y.STANDARD_T, rho=y.RHO)
        r1 = y.rw_route_a(U, hull, n_theta=240)
        r2 = y.rw_route_a(U, hull, n_theta=480)
        assert abs(r1 - r2) / r2 < 5e-3


# ---------------------------------------------------------------------------
# 2. reference side: exact structural laws (convention constant cancels)
# ---------------------------------------------------------------------------
def test_wave_resistance_beam_squared_law():
    """Rw prop B^2 at fixed L, T, U (the amplitude is linear in the beam)."""
    U = 0.25 * math.sqrt(y.G * y.STANDARD_L)
    r1 = y.rw_route_a(U, y.Wigley(L=100.0, B=8.0, T=6.25, rho=y.RHO))
    r2 = y.rw_route_a(U, y.Wigley(L=100.0, B=10.0, T=6.25, rho=y.RHO))
    assert r2 / r1 == pytest.approx((10.0 / 8.0) ** 2, rel=2e-3)


def test_froude_scaling_law():
    """Rw(l L, l B, l T) = l^3 Rw at fixed Fn (rho g^2/U^2 * B^2 T^2 -> l^3)."""
    lam, fn = 2.0, 0.25
    L, B, T = y.STANDARD_L, y.STANDARD_B, y.STANDARD_T
    U1 = fn * math.sqrt(y.G * L)
    U2 = fn * math.sqrt(y.G * lam * L)
    r1 = y.rw_route_a(U1, y.Wigley(L=L, B=B, T=T, rho=y.RHO))
    r2 = y.rw_route_a(U2, y.Wigley(L=lam * L, B=lam * B, T=lam * T, rho=y.RHO))
    assert r2 / r1 == pytest.approx(lam**3, rel=5e-3)


# ---------------------------------------------------------------------------
# 3. reference side: exact hydrostatics
# ---------------------------------------------------------------------------
def test_wigley_exact_hydrostatics_and_surface_convergence():
    assert y.exact_wigley_cb() == pytest.approx(4.0 / 9.0)
    assert y.exact_wigley_volume(100.0, 10.0, 6.25) == pytest.approx(4 * 100.0 * 10.0 * 6.25 / 9.0)
    s_coarse = y.exact_wigley_wetted_surface(n_x=400, n_z=160)
    s_fine = y.exact_wigley_wetted_surface(n_x=800, n_z=320)
    assert abs(s_fine - s_coarse) / s_fine < 1e-4
    assert s_fine == pytest.approx(1487.906, rel=1e-5)  # spectral value, m1568 anchor


def test_pontoon_exact_hydrostatics():
    B, T = 20.0, 8.0
    h = y.pontoon_hydrostatics(B, T, 6.0)
    assert h["KB"] == pytest.approx(T / 2)
    assert h["BM"] == pytest.approx(B * B / (12.0 * T))
    assert h["KM"] == pytest.approx(h["KB"] + h["BM"])
    assert h["GM"] == pytest.approx(h["KM"] - 6.0)
    # wall-sided GZ -> GM * phi as phi -> 0, and vanishes at phi = 0
    assert y.pontoon_wall_sided_gz(B, T, 6.0, 0.0) == pytest.approx(0.0)
    phi = 0.05
    assert y.pontoon_wall_sided_gz(B, T, 6.0, phi) == pytest.approx(
        h["GM"] * math.radians(phi), rel=1e-3)
    assert y.pontoon_wall_sided_valid_deg(B, T) == pytest.approx(
        math.degrees(math.atan(2 * T / B)))


def test_ittc57_implied_area_roundtrip():
    """S_implied(Rf) must recover S exactly when Rf is built from ITTC-57."""
    re, U, s = 5.0e8, 7.0, 1487.906
    rf = 0.5 * y.RHO * y.ittc57_cf(re) * U * U * s
    assert y.implied_wetted_area(rf, U, re) == pytest.approx(s, rel=1e-12)


# ---------------------------------------------------------------------------
# 4. verdict engine + snapshot replay
# ---------------------------------------------------------------------------
def test_verdict_engine_semantics():
    v = y.verdict("x", 1.02, 1.0, 0.05)
    assert v["status"] == "PASS" and v["rel_err"] == pytest.approx(0.02)
    assert y.verdict("x", 1.20, 1.0, 0.05)["status"] == "FAIL"
    assert y.verdict("x", None, 1.0, 0.05)["status"] == "ERROR"
    assert y.verdict("x", float("nan"), 1.0, 0.05)["status"] == "ERROR"
    assert y.verdict("x", 0.0, 0.0, 0.0)["status"] == "PASS"
    assert y.verdict("x", 0.5, 0.0, 0.1)["status"] == "FAIL"
    assert ra.identity_check("id", 0.004, 0.005)["status"] == "PASS"
    assert ra.identity_check("id", 0.006, 0.005)["status"] == "FAIL"


def test_normalization_is_flagged_withheld():
    span = y.normalization_span()
    assert span["evidence"] == "WITHHELD"
    assert span["rw_scale_range"] == [0.5, 1.0]
    assert span["prefactor_in_code"] == pytest.approx(8.0 / math.pi)
    assert span["prefactor_classical_one_side"] == pytest.approx(4.0 / math.pi)


SNAPSHOT = (ROOT / "docs" / "verification_debt" / "wigley_michell" / "artifacts"
            / "reaudit_2026-11-02.json")


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="no frozen audit snapshot")
def test_snapshot_replay_reproduces_ledger():
    """Every verdict must be a pure function of the frozen snapshot."""
    snap = json.loads(SNAPSHOT.read_text())
    v1 = ra.verdicts_from_snapshot(snap)
    v2 = ra.verdicts_from_snapshot(json.loads(json.dumps(snap)))
    ids1 = [(v["id"], v["status"]) for k in ("GEO", "RES", "STB") for v in v1[k]]
    ids2 = [(v["id"], v["status"]) for k in ("GEO", "RES", "STB") for v in v2[k]]
    assert ids1 == ids2 and ids1, "replay is not deterministic"
    for k in ("GEO", "RES", "STB"):
        for v in v1[k]:
            assert v["status"] in {"PASS", "FAIL", "WITHHELD", "ERROR"}
            if v["status"] in {"PASS", "FAIL"}:
                assert v["tol"] is not None and v["rel_err"] is not None
            if v["status"] != "WITHHELD":
                assert v.get("basis"), f"{v['id']} lacks an explicit reference basis"
    # the absolute Michell level must never be silently promoted to PASS
    res7 = next(v for v in v1["RES"] if v["id"].startswith("RES-7"))
    assert res7["status"] == "WITHHELD"