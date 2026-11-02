"""Verification gates for the Michell thin-ship reference of the Wigley hull.

These tests are the *judge* for ``docs/verification_debt/wigley_michell``: they
pin (a) the closed-form geometry, (b) the closed-form inner integrals against
independent high-precision quadrature, (c) the agreement of three numerical
routes, (d) the exact scaling invariants of the Michell integral, and (e) a
regression guard for the m1568 defect where route B silently returned ~1e-28
(a complex array cast with ``float()`` before ``abs()``).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

MODULE_DIR = (
    Path(__file__).resolve().parents[2] / "docs" / "verification_debt" / "wigley_michell"
)
sys.path.insert(0, str(MODULE_DIR))

from michell_wigley import (  # noqa: E402
    Wigley,
    inner_x_closed,
    inner_z_closed,
    rw_double_quad,
    rw_highprec,
    rw_theta_integral,
    wetted_surface,
)

G = 9.81
L, B, T = 100.0, 10.0, 6.25
HULL = Wigley(L=L, B=B, T=T)


def speed(fn: float, hull: Wigley = HULL) -> float:
    return fn * math.sqrt(hull.g * hull.L)


# ---------------------------------------------------------------------------
# geometry gates (Cb / L / B must be exact before anything else is trusted)
# ---------------------------------------------------------------------------
def test_block_coefficient_exact():
    assert HULL.cb == pytest.approx(4.0 / 9.0, rel=0, abs=1e-15)
    assert HULL.volume == pytest.approx(4.0 * L * B * T / 9.0, rel=1e-15)


def test_midship_and_waterplane_coefficients_exact():
    assert HULL.cm == pytest.approx(2.0 / 3.0, rel=0, abs=1e-15)
    assert HULL.cwp == pytest.approx(2.0 / 3.0, rel=0, abs=1e-15)


def test_wetted_surface_converged():
    coarse = wetted_surface(HULL, n_x=200, n_z=80)
    fine = wetted_surface(HULL, n_x=400, n_z=160)
    assert abs(coarse - fine) / fine < 1e-9
    # geometric scaling: S ~ lambda^2
    scaled = wetted_surface(Wigley(L=2 * L, B=2 * B, T=2 * T), n_x=200, n_z=80)
    assert scaled / fine == pytest.approx(4.0, rel=1e-9)


# ---------------------------------------------------------------------------
# closed-form inner integrals vs independent high-precision quadrature
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fn,theta", [(0.15, 0.0), (0.30, 0.6), (0.35, 0.9)])
def test_inner_x_matches_quadrature(fn, theta):
    import mpmath as mp

    U = speed(fn)
    b = (G / U**2) / math.cos(theta)
    with mp.workdps(30):
        num = complex(mp.quad(lambda x: x * mp.e ** (1j * b * x), [-L / 2, L / 2]))
    assert abs(inner_x_closed(HULL, b) - num) / abs(num) < 1e-10


@pytest.mark.parametrize("fn,theta", [(0.15, 0.0), (0.30, 0.6), (0.35, 0.9)])
def test_inner_z_matches_quadrature(fn, theta):
    import mpmath as mp

    U = speed(fn)
    a = (G / U**2) / math.cos(theta) ** 2
    with mp.workdps(30):
        num = float(mp.quad(lambda z: (1 - (z / T) ** 2) * mp.e ** (-a * z), [0, T]))
    assert abs(inner_z_closed(HULL, a) - num) / num < 1e-12


# ---------------------------------------------------------------------------
# route consistency
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fn", [0.15, 0.30])
def test_route_A_vs_route_C_high_precision(fn):
    U = speed(fn)
    a = rw_theta_integral(HULL, U, n_theta=400)
    c = rw_highprec(HULL, U, dps=25)
    assert abs(a - c) / c < 1e-10


@pytest.mark.parametrize("fn", [0.15, 0.30, 0.35])
def test_route_A_vs_route_B_numerical_surface_quadrature(fn):
    U = speed(fn)
    a = rw_theta_integral(HULL, U, n_theta=400)
    b = rw_double_quad(HULL, U)
    assert abs(a - b) / b < 1e-3


def test_route_B_is_not_degenerate():
    """Regression guard for the m1568 defect: route B returned ~1e-28."""
    U = speed(0.30)
    a = rw_theta_integral(HULL, U, n_theta=400)
    b = rw_double_quad(HULL, U)
    assert b > 1e-3 * a, f"route B collapsed to {b:.3e} N (complex cast bug?)"


def test_route_B_converges_under_refinement():
    U = speed(0.30)
    coarse = rw_double_quad(HULL, U, n_panel_x=16, n_panel_z=6, n_theta=48)
    fine = rw_double_quad(HULL, U, n_panel_x=48, n_panel_z=12, n_theta=192)
    assert abs(coarse - fine) / fine < 5e-2


# ---------------------------------------------------------------------------
# exact invariants of the Michell integral
# ---------------------------------------------------------------------------
def test_wave_resistance_scales_with_breadth_squared():
    U = speed(0.25)
    base = rw_theta_integral(HULL, U, n_theta=300)
    for scale in (0.5, 1.5):
        h = Wigley(L=L, B=B * scale, T=T)
        val = rw_theta_integral(h, U, n_theta=300)
        assert abs(val / scale**2 - base) / base < 1e-9


def test_wave_resistance_scales_with_density():
    U = speed(0.25)
    base = rw_theta_integral(HULL, U, n_theta=300)
    heavy = Wigley(L=L, B=B, T=T, rho=2.0 * HULL.rho)
    assert abs(rw_theta_integral(heavy, U, n_theta=300) - 2.0 * base) / base < 1e-12


def test_froude_similarity_of_wave_coefficient():
    """At fixed Fn the thin-ship Cw must scale exactly like lambda^2."""
    fn = 0.28
    s1 = wetted_surface(HULL, n_x=200, n_z=80)
    cw1 = rw_theta_integral(HULL, speed(fn), n_theta=300) / (
        0.5 * HULL.rho * speed(fn) ** 2 * s1
    )
    lam = 0.5
    h = Wigley(L=L * lam, B=B * lam, T=T * lam)
    s = wetted_surface(h, n_x=200, n_z=80)
    U = fn * math.sqrt(G * L * lam)
    cw = rw_theta_integral(h, U, n_theta=300) / (0.5 * HULL.rho * U**2 * s)
    assert abs(cw / lam**2 - cw1) / cw1 < 1e-6, (cw / lam**2, cw1)


def test_low_froude_decay_of_wave_resistance():
    """Rw must fall steeply as Fn -> 0 (exponential in 1/Fn^2)."""
    fns = (0.12, 0.15, 0.20)
    low = [rw_theta_integral(HULL, speed(fn), n_theta=300) for fn in fns]
    assert low[0] < low[1] < low[2]
    assert low[0] < 0.2 * low[2], low
    for i in range(1, len(fns)):
        d_inv = 1.0 / fns[i] ** 2 - 1.0 / fns[i - 1] ** 2
        slope = (math.log(low[i]) - math.log(low[i - 1])) / d_inv
        assert slope < 0.0, slope
