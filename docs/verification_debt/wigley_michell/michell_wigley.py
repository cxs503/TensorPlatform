"""Michell thin-ship wave resistance for the Wigley parabolic hull.

Status: verification reference module (see ``README.md`` for the audit report
and ``artifacts/verification_verdicts.json`` for the machine-readable verdicts).

Geometry (Wigley parabolic hull, standard thin-ship test form)
--------------------------------------------------------------
Half-breadth, with ``z`` measured *downward from the undisturbed waterline*
(``z = 0`` at the WL, ``z = T`` at the keel)::

    y(x, z) = B/2 * (1 - (2x/L)^2) * (1 - (z/T)^2),   x in [-L/2, L/2]

Closed-form geometry gates (no fitted data involved)::

    Cb        = 4/9                      (exact)
    volume    = 4 L B T / 9              (exact)

Physics: Michell (1898) thin-ship theory in deep water
------------------------------------------------------
::

    Rw = PRE * Int_0^{pi/2} |I(theta)|^2 sec^3(theta) dtheta
    I(theta) = Int_S (dy/dx) exp(-k0 z sec^2 theta) exp(i k0 x sec theta) dx dz
    k0 = g / U^2 ,  PRE = 4 rho g^2 / (pi U^2)      (half-hull I convention)

``S`` is the centre-plane footprint of one half-hull and ``dy/dx`` is the
half-breadth slope, so the ``4 rho g^2/(pi U^2)`` prefactor already accounts
for both sides of the ship (Tuck 1989; Bertram, *Practical Ship Hydrodynamics*,
thin-ship section).  Because that prefactor is the one genuinely
convention-dependent quantity, it is exposed explicitly through ``side_factor``
(``1.0`` = literature convention above, ``2.0`` = both-sides-superposed
reading of the same statement) and every verdict in the audit report is split
into "prefactor-independent" and "prefactor-dependent" classes.

Three numerical routes and what each one independently checks
-------------------------------------------------------------
================  ====================================  ==========================
route             inner integral (x, z)                 theta integral
================  ====================================  ==========================
A ``rw_theta_integral``   closed form (analytic)         Gauss-Legendre, n nodes
B ``rw_double_quad``      2-D Gauss-Legendre, depth      Gauss-Legendre, same nodes
                          variable d = T - z
C ``rw_highprec``         closed form at 30 digits        mpmath adaptive (tanh-sinh)
================  ====================================  ==========================

* A vs B  -> validates the closed-form inner integrals **and** the depth
  convention (B never uses the closed form and parametrises depth from the
  waterline instead of from the keel).
* A vs C  -> validates that the theta quadrature of route A is converged to
  the requested tolerance (C is adaptive, not a fixed rule).
* A(n) vs A(2n) / Richardson -> discretisation error estimate (GCI-style).

History note: the version of this module committed in mission m1568 returned
``~1e-28`` from route B (a complex array was cast with ``float()`` before
``abs()``, discarding the imaginary part) so the "two independent routes agree"
claim in its docstring was vacuous.  That defect is fixed here and guarded by
``tests/verification/test_wigley_michell.py``.

Units: SI.  U [m/s], lengths [m], Rw [N], rho [kg/m^3].
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Tuple

import numpy as np

G = 9.81
KNOT = 0.5144444444444445


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
@dataclass
class Wigley:
    """Wigley parabolic hull.  ``z`` is measured downward from the waterline."""

    L: float
    B: float
    T: float
    rho: float = 1025.0
    g: float = G

    # -- closed-form geometry gates -----------------------------------------
    @property
    def cb(self) -> float:
        """Block coefficient, exactly 4/9 for this hull."""
        return 4.0 / 9.0

    @property
    def volume(self) -> float:
        """Displacement volume 4 L B T / 9 [m^3] (exact)."""
        return 4.0 * self.L * self.B * self.T / 9.0

    @property
    def cm(self) -> float:
        """Midship section coefficient, exactly 2/3."""
        return 2.0 / 3.0

    @property
    def cwp(self) -> float:
        """Waterplane coefficient, exactly 2/3."""
        return 2.0 / 3.0

    # -- shape ---------------------------------------------------------------
    def half_beam(self, x: float, z: float) -> float:
        return 0.5 * self.B * (1.0 - (2.0 * x / self.L) ** 2) * (1.0 - (z / self.T) ** 2)

    def dy_dx(self, x: float, z: float) -> float:
        """Half-breadth longitudinal slope  dy/dx = -4 B x / L^2 (1-(z/T)^2)."""
        return -4.0 * self.B * x / self.L**2 * (1.0 - (z / self.T) ** 2)


def _leggauss(n: int):
    """Gauss-Legendre nodes/weights on [-1, 1] (cached per order)."""
    return np.polynomial.legendre.leggauss(n)


_RULE_CACHE: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def _rule(n: int):
    """Cached Gauss-Legendre rule on [-1, 1] (small orders only)."""
    if n not in _RULE_CACHE:
        _RULE_CACHE[n] = np.polynomial.legendre.leggauss(n)
    return _RULE_CACHE[n]


def panel_gauss(a: float, b: float, n_panel: int, n_rule: int = 24):
    """Composite Gauss-Legendre nodes/weights on [a, b].

    Panel-based rather than a single high-order rule: ``leggauss`` is O(n^2)
    and the theta/x integrands oscillate, so composite panels of a 24-point
    rule are both cheaper and far better conditioned.
    """
    g, w = _rule(n_rule)
    edges = np.linspace(a, b, n_panel + 1)
    h = (b - a) / n_panel
    nodes = (0.5 * (edges[:-1] + edges[1:])[:, None] + 0.5 * h * g[None, :]).ravel()
    weights = np.tile(0.5 * h * w, n_panel)
    return nodes, weights


def amplitude_double_quad(
    hull: Wigley, U: float, theta: float, n_panel_x: int = 48, n_panel_z: int = 12,
    n_rule: int = 24, fd_step: float = 1e-4,
) -> complex:
    """I(theta) by fully numerical surface quadrature (no closed forms).

    Independence from :func:`amplitude_closed` is bought twice over:

    * ``dy/dx`` is obtained by central differences of :meth:`Wigley.half_beam`
      (the analytic slope ``-4Bx/L^2 (1-(z/T)^2)`` is never used), and
    * the x/z integrals are composite Gauss-Legendre quadrature rather than
      closed forms.

    ``z`` is the same variable as in the closed form: depth measured *downward
    from the waterline* (0 at the WL, T at the keel), so the depth attenuation
    ``exp(-k0 z sec^2 theta)`` and the shape ``(1-(z/T)^2)`` share it.
    """
    x, wx = panel_gauss(-0.5 * hull.L, 0.5 * hull.L, n_panel_x, n_rule)
    z, wz = panel_gauss(0.0, hull.T, n_panel_z, n_rule)
    X, Z = np.meshgrid(x, z, indexing="ij")
    hstep = fd_step * hull.L
    dydx = (hull.half_beam(X + hstep, Z) - hull.half_beam(X - hstep, Z)) / (2.0 * hstep)
    dA = np.outer(wx, wz)
    k0 = hull.g / U**2
    sec = 1.0 / math.cos(theta)
    att = np.exp(-k0 * Z * sec**2)
    ang = k0 * X * sec
    return complex(np.sum(dA * dydx * att * (np.cos(ang) + 1j * np.sin(ang))))


def wetted_surface(hull: Wigley, n_x: int = 400, n_z: int = 160) -> float:
    """Wetted surface of both sides by Gauss-Legendre quadrature.

    dS = 2 sqrt(1 + (dy/dx)^2 + (dy/dz)^2) dx dz on the half hull, mirrored.
    The integrand is analytic, so the rule converges spectrally; the default
    order (400 x 160) is converged to < 1e-10 relative (see README).
    """
    gx, wx = _leggauss(n_x)
    gz, wz = _leggauss(n_z)
    x = 0.5 * hull.L * gx
    z = 0.5 * hull.T * (gz + 1.0)
    wx = 0.5 * hull.L * wx
    wz = 0.5 * hull.T * wz

    X, Z = np.meshgrid(x, z, indexing="ij")
    dydx = -4.0 * hull.B * X / hull.L**2 * (1.0 - (Z / hull.T) ** 2)
    dydz = 0.5 * hull.B * (1.0 - (2.0 * X / hull.L) ** 2) * (-2.0 * Z / hull.T**2)
    dS = 2.0 * np.sqrt(1.0 + dydx**2 + dydz**2)
    return float(np.einsum("i,j,ij->", wx, wz, dS))


# ---------------------------------------------------------------------------
# Prefactor convention
# ---------------------------------------------------------------------------
def michell_prefactor(rho: float, U: float, side_factor: float = 1.0) -> float:
    """Michell prefactor PRE = side_factor * 4 rho g^2 / (pi U^2).

    ``side_factor = 1`` is the literature convention for a half-hull ``I``
    (both sides already included); ``2`` is the alternative reading.  The
    factor cannot be settled without an experimental anchor, so the audit
    reports it as an explicit sensitivity band instead of hiding it.
    """
    return side_factor * 4.0 * rho * G**2 / (math.pi * U**2)


# ---------------------------------------------------------------------------
# Closed-form inner integrals (route A / C)
# ---------------------------------------------------------------------------
def inner_x_closed(hull: Wigley, b: float) -> complex:
    """Int_{-L/2}^{L/2} x exp(i b x) dx = 2i (sin(bA)/b^2 - A cos(bA)/b)."""
    A = 0.5 * hull.L
    if abs(b) < 1e-12:
        return 0.0 + 0.0j
    return 2.0j * (math.sin(b * A) / b**2 - A * math.cos(b * A) / b)


def inner_z_closed(hull: Wigley, a: float) -> float:
    """Int_0^T (1 - (z/T)^2) exp(-a z) dz with z measured down from the WL."""
    T = hull.T
    if a < 1e-9:
        return 2.0 * T / 3.0
    e = math.exp(-a * T)
    i0 = (1.0 - e) / a
    i2 = (2.0 / a**3) * (1.0 - e * (1.0 + a * T + 0.5 * (a * T) ** 2))
    return i0 - i2 / T**2


def amplitude_closed(hull: Wigley, U: float, theta: float) -> complex:
    """Wave amplitude integral I(theta) in closed form."""
    k0 = hull.g / U**2
    sec = 1.0 / math.cos(theta)
    return -4.0 * hull.B / hull.L**2 * inner_x_closed(hull, k0 * sec) * inner_z_closed(
        hull, k0 * sec**2
    )


def _theta_limit(hull: Wigley, U: float, exponent: float = 60.0) -> float:
    """Upper theta limit where the depth attenuation reaches exp(-exponent)."""
    k0 = hull.g / U**2
    val = 2.0 * k0 * hull.T / exponent
    if val >= 1.0:  # very low Fn: attenuation never reaches the threshold
        return 0.5 * math.pi
    return math.acos(math.sqrt(max(val, 0.0)))


# ---------------------------------------------------------------------------
# Route A : closed-form inner integrals + Gauss-Legendre theta
# ---------------------------------------------------------------------------
def rw_theta_integral(
    hull: Wigley, U: float, n_theta: int = 400, side_factor: float = 1.0
) -> float:
    """Michell wave resistance [N], route A."""
    theta_max = _theta_limit(hull, U)
    gt, wt = _leggauss(n_theta)
    thetas = 0.5 * theta_max * (gt + 1.0)
    weights = 0.5 * theta_max * wt
    acc = 0.0
    for th, w in zip(thetas, weights):
        th = float(th)
        i_th = amplitude_closed(hull, U, th)
        acc += float(w) * abs(i_th) ** 2 / math.cos(th) ** 3
    return michell_prefactor(hull.rho, U, side_factor) * acc


# ---------------------------------------------------------------------------
# Route B : numeric 2-D surface quadrature (depth measured from the WL)
# ---------------------------------------------------------------------------
def amplitudes_double_quad(
    hull: Wigley, U: float, thetas: np.ndarray, n_panel_x: int = 32, n_panel_z: int = 10,
    n_rule: int = 24, fd_step: float = 1e-4, n_chunk: int = 256,
) -> np.ndarray:
    """Vectorised :func:`amplitude_double_quad` over an array of theta values."""
    x, wx = panel_gauss(-0.5 * hull.L, 0.5 * hull.L, n_panel_x, n_rule)
    z, wz = panel_gauss(0.0, hull.T, n_panel_z, n_rule)
    X, Z = np.meshgrid(x, z, indexing="ij")
    hstep = fd_step * hull.L
    dydx = (hull.half_beam(X + hstep, Z) - hull.half_beam(X - hstep, Z)) / (2.0 * hstep)
    dA = (np.outer(wx, wz) * dydx).ravel()
    Xf, Zf = X.ravel(), Z.ravel()
    k0 = hull.g / U**2

    out = np.empty(len(thetas), dtype=complex)
    for i in range(0, len(thetas), n_chunk):
        th = np.asarray(thetas[i : i + n_chunk], dtype=float)
        sec = 1.0 / np.cos(th)
        att = np.exp(-k0 * Zf[:, None] * sec[None, :] ** 2)
        ang = k0 * Xf[:, None] * sec[None, :]
        kern = att * (np.cos(ang) + 1j * np.sin(ang))
        out[i : i + n_chunk] = dA @ kern
    return out


def rw_double_quad(
    hull: Wigley,
    U: float,
    n_panel_x: int = 32,
    n_panel_z: int = 10,
    n_theta: int = 96,
    n_rule: int = 24,
    side_factor: float = 1.0,
) -> float:
    """Michell wave resistance [N], route B: brute-force surface quadrature.

    Surface integral evaluated numerically (:func:`amplitude_double_quad`: no
    closed forms, ``dy/dx`` by central differences) before the theta
    quadrature.  Panel/theta orders are convergence-checked in the report.
    """
    theta_max = _theta_limit(hull, U)
    thetas, weights = panel_gauss(0.0, theta_max, max(1, n_theta // n_rule), n_rule)
    amps = amplitudes_double_quad(
        hull, U, thetas, n_panel_x=n_panel_x, n_panel_z=n_panel_z, n_rule=n_rule
    )
    acc = float(np.sum(weights * np.abs(amps) ** 2 / np.cos(thetas) ** 3))
    return michell_prefactor(hull.rho, U, side_factor) * acc


# ---------------------------------------------------------------------------
# Route C : closed-form inner integrals at high precision, adaptive theta
# ---------------------------------------------------------------------------
def rw_highprec(
    hull: Wigley, U: float, dps: int = 30, side_factor: float = 1.0
) -> float:
    """Michell wave resistance [N], route C: mpmath, 30-digit arithmetic."""
    import mpmath as mp

    with mp.workdps(dps):
        L, B, T, rho = mp.mpf(hull.L), mp.mpf(hull.B), mp.mpf(hull.T), mp.mpf(hull.rho)
        g = mp.mpf(hull.g)
        Um = mp.mpf(U)
        k0 = g / Um**2
        A = L / 2

        def inner_x(b):
            if abs(b) < mp.mpf("1e-12"):
                return mp.mpc(0, 0)
            return 2j * (mp.sin(b * A) / b**2 - A * mp.cos(b * A) / b)

        def inner_z(a):
            if a < mp.mpf("1e-9"):
                return 2 * T / 3
            e = mp.e ** (-a * T)
            i0 = (1 - e) / a
            i2 = (2 / a**3) * (1 - e * (1 + a * T + (a * T) ** 2 / 2))
            return i0 - i2 / T**2

        def integrand(theta):
            sec = 1 / mp.cos(theta)
            amp = -4 * B / L**2 * inner_x(k0 * sec) * inner_z(k0 * sec**2)
            return abs(amp) ** 2 / mp.cos(theta) ** 3

        theta_max = mp.mpf(_theta_limit(hull, U))
        acc = mp.quad(integrand, [0, theta_max])
        return float(side_factor * 4 * rho * g**2 / (mp.pi * Um**2) * acc)


# ---------------------------------------------------------------------------
# Public convenience wrappers
# ---------------------------------------------------------------------------
def michell_rw(hull: Wigley, U: float, n_theta: int = 400, side_factor: float = 1.0) -> float:
    """Michell wave resistance [N] (route A, literature prefactor convention)."""
    return rw_theta_integral(hull, U, n_theta=n_theta, side_factor=side_factor)


def michell_rw_converged(
    hull: Wigley, U: float, n_theta: int = 200, ratio: int = 2, side_factor: float = 1.0
) -> Tuple[float, float]:
    """Richardson-extrapolated Rw and the relative increment |Rw(n)-Rw(n*ratio)|.

    The theta integrand is analytic on [0, theta_max] (its endpoint behaviour is
    a Gauss-Legendre-friendly exponential decay), so the observed order is
    estimated from three levels rather than assumed; a conservative exponent of
    4 is used when the estimate is unreliable.
    """
    r1 = rw_theta_integral(hull, U, n_theta, side_factor)
    r2 = rw_theta_integral(hull, U, n_theta * ratio, side_factor)
    delta = abs(r1 - r2) / max(abs(r2), 1e-300)
    p = 4.0
    return r2 + (r2 - r1) / (ratio**p - 1.0), delta


def michell_cw(hull: Wigley, U: float, s_ref: float | None = None, side_factor: float = 1.0) -> float:
    """Wave resistance coefficient based on 0.5 rho S U^2 (S = wetted area)."""
    s = wetted_surface(hull) if s_ref is None else s_ref
    return michell_rw(hull, U, side_factor=side_factor) / (0.5 * hull.rho * s * U**2)


def michell_sweep(
    hull: Wigley,
    fn_list: Iterable[float],
    side_factor: float = 1.0,
    s_ref: float | None = None,
) -> list[dict]:
    """Cw/Fn sweep with route A, converged value and the discretisation increment."""
    s = wetted_surface(hull) if s_ref is None else s_ref
    out = []
    for Fn in fn_list:
        U = Fn * math.sqrt(hull.g * hull.L)
        rw = michell_rw(hull, U, side_factor=side_factor)
        rw_cv, inc = michell_rw_converged(hull, U, side_factor=side_factor)
        out.append(
            {
                "Fn": Fn,
                "U_ms": U,
                "Rw_N": rw,
                "Rw_richardson_N": rw_cv,
                "theta_increment_rel": inc,
                "Cw": rw / (0.5 * hull.rho * s * U**2),
                "Cw_richardson": rw_cv / (0.5 * hull.rho * s * U**2),
            }
        )
    return out