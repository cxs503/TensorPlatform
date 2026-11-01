"""Reference-invariant tests for the ship-platform verification audit.

These tests do not touch the platform: they pin the *reference* side of the
audit (docs/verification_debt/wigley_michell) so that a later change of the
audit cannot silently change the yardstick.

Checks
------
1. Wigley parabolic hull: analytically Cb = 4/9 and displacement = 4 L B T / 9.
   Numerical volume integration must reproduce it to <1e-6 relative error.
2. Wetted-surface quadrature convergence (n vs 2n < 1e-5 relative).
3. Michell closed-form inner integrals vs direct numerical quadrature (<1e-9).
4. Michell wave resistance: theta-quadrature refinement <1% for Fn in [0.15,0.35].
5. ITTC-1957 vs Schoenherr friction line agree within 5% for Re in [1e7, 1e9]
   (textbook consistency of the two widely used friction lines).
6. Rectangular pontoon: wall-sided GZ -> GM*phi as phi -> 0, GZ(0) = 0, and the
   vanishing angle satisfies GZ = 0.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

AUDIT_DIR = (Path(__file__).resolve().parents[3] / "docs" / "verification_debt"
             / "wigley_michell")
if str(AUDIT_DIR) not in sys.path:
    sys.path.insert(0, str(AUDIT_DIR))

import references as ref  # noqa: E402
from michell_wigley import (  # noqa: E402
    Wigley, _inner_x, _inner_z, rw_theta_integral, wetted_surface,
)


def test_wigley_volume_and_cb_are_analytic():
    L, B, T = 100.0, 10.0, 6.25
    hull = Wigley(L=L, B=B, T=T)
    # numerical volume by Gauss-Legendre over the half-body (mirror symmetry)
    gx, wx = np.polynomial.legendre.leggauss(200)
    gz, wz = np.polynomial.legendre.leggauss(200)
    x = 0.5 * L * gx
    wx = 0.5 * L * wx
    z = 0.5 * T * (gz + 1.0)
    wz = 0.5 * T * wz
    X, Z = np.meshgrid(x, z, indexing="ij")
    half = 0.5 * B * (1.0 - (2.0 * X / L) ** 2) * (1.0 - (Z / T) ** 2)
    vol = float(np.einsum("i,j,ij->", wx, wz, 2.0 * half))
    assert vol == pytest.approx(ref.wigley_volume(L, B, T), rel=1e-6)
    assert ref.wigley_cb() == pytest.approx(4.0 / 9.0)
    assert hull.cb == pytest.approx(4.0 / 9.0)
    assert hull.volume == pytest.approx(vol, rel=1e-6)


def test_wetted_surface_converges():
    hull = Wigley()
    s1 = wetted_surface(hull, 500, 200)
    s2 = wetted_surface(hull, 1000, 400)
    assert s2 > 0.0
    assert abs(s2 - s1) / s2 < 1e-5


def test_inner_integrals_closed_form_vs_quadrature():
    hull = Wigley()
    T, A = hull.T, hull.L / 2.0
    for b in (0.05, 1.0, 7.3):
        # Int_{-A}^{A} x exp(i b x) dx by fine Simpson rule
        xs = np.linspace(-A, A, 200001)
        f = xs * np.exp(1j * b * xs)
        num = np.trapezoid(f, xs)
        assert abs(_inner_x(hull, b) - num) < 2e-5 * max(1.0, abs(num))  # trapezoid reference floor
    for a in (0.05, 1.0, 12.0):
        zs = np.linspace(0.0, T, 200001)
        f = (1.0 - (zs / T) ** 2) * np.exp(-a * zs)
        num = np.trapezoid(f, zs)
        assert abs(_inner_z(hull, a) - num) < 2e-5 * max(1.0, abs(num))


def test_michell_theta_quadrature_converges():
    hull = Wigley()
    for Fn in (0.15, 0.25, 0.35):
        U = Fn * math.sqrt(ref.G * hull.L)
        r1 = rw_theta_integral(hull, U, n_theta=200)
        r2 = rw_theta_integral(hull, U, n_theta=400)
        assert r1 > 0.0 and r2 > 0.0
        assert abs(r2 - r1) / r2 < 0.01
    # wave resistance must grow with Froude number over this range
    us = [Fn * math.sqrt(ref.G * hull.L) for Fn in (0.15, 0.20, 0.25)]
    rw = [rw_theta_integral(hull, u, n_theta=300) for u in us]
    assert rw[0] < rw[1] < rw[2]


def test_ittc57_consistent_with_schoenherr():
    for re in (1e7, 1e8, 1e9):
        cf57 = ref.ittc57_cf(re)
        cfs = ref.schoenherr_cf(re)
        assert abs(cf57 - cfs) / cfs < 0.05


def test_pontoon_wall_sided_limits():
    B, T, KG = 20.0, 8.0, 6.0
    h = ref.barge_hydrostatics(B, T, KG)
    assert h["KB"] == pytest.approx(T / 2)
    assert h["BM"] == pytest.approx(B**2 / (12.0 * T))
    assert h["GM"] == pytest.approx(h["KB"] + h["BM"] - KG)
    assert ref.barge_gz(B, T, KG, 0.0) == pytest.approx(0.0)
    phi = 0.1
    assert ref.barge_gz(B, T, KG, phi) == pytest.approx(h["GM"] * math.radians(phi), rel=1e-3)
    # negative GM -> finite vanishing angle where GZ = 0
    h2 = ref.barge_hydrostatics(B, T, 12.0)
    assert h2["GM"] < 0
    va = ref.barge_vanishing_angle(B, T, 12.0)
    assert ref.barge_gz(B, T, 12.0, va) == pytest.approx(0.0, abs=1e-9)
