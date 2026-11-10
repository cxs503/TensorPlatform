"""Closed-form references for the parametric body form.

Independent of :mod:`tools.benchmark_gen.numeric`: nothing is imported from the
production integrator, so a sign/power error in one cannot cancel in the other.
Every reference below is derived in closed form from the parametric definition

    y(x, z) = B/2 * f(u) * g(z),   u = 2|x| / L,   z in [-D, z_wl]

    f(u) = 1                                        u <= m
         = (1 - v^n)^s                              v = (u-m)/(1-m)
    g(z) = 1 - (|z| / D)^p                          (g = 0 at the keel z = -D)

with ``B`` beam, ``L`` length, ``D`` form depth, ``n = plan_exponent``,
``s = plan_shape``, ``p = section_exponent``, ``m = midbody_fraction`` and the
immersion ``T`` (case waterline ``z_wl = T - D``).

The only non-elementary ingredients are Euler Beta functions, obtained from
``lgamma`` (see :func:`_beta`); all integrals over ``z`` reduce to polynomials in
``|z|^p`` evaluated at two limits, so they are exact in floating point up to
round-off.  Series expansions used:

* ``\int f dx   = L [ m + (1-m) (1/n) B(1/n, s+1) ]``
* ``\int f^3 dx = L [ m + (1-m) (1/n) B(1/n, 3s+1) ]``
* ``\int x^2 f dx = L^3 m^3/12 + (L^3 (1-m)/4) (m^2 J0 + 2m(1-m) J1 + (1-m)^2 J2)``
  with ``Jk = (1/n) B((k+1)/n, s+1)``

The wetted surface has no elementary closed form: it is evaluated by a
high-order (8-node Gauss) quadrature whose discretisation error is orders of
magnitude below the production 2x2 rule, and is labelled as such in the datasets
(``evidence_level``).
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

from .numeric import BodyForm, GAUSS8

__all__ = ["closed_form_hydrostatics", "surface_reference", "beta"]


def beta(a: float, b: float) -> float:
    """Euler Beta function ``B(a, b) = Gamma(a)Gamma(b)/Gamma(a+b)``."""
    return math.exp(math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))


def _plan_ints(form: BodyForm) -> Tuple[float, float, float]:
    """``(\\int f dx, \\int f^3 dx, \\int x^2 f dx)`` in closed form."""
    L, m = form.length, form.midbody_fraction
    n, s = form.plan_exponent, form.plan_shape
    q = 1.0 - m
    c0 = L * (m + q * beta(1.0 / n, s + 1.0) / n)
    c1 = L * (m + q * beta(1.0 / n, 3.0 * s + 1.0) / n)
    j = [beta((k + 1.0) / n, s + 1.0) / n for k in range(3)]
    c2 = (L ** 3 * m ** 3 / 12.0
          + (L ** 3 * q / 4.0) * (m * m * j[0] + 2.0 * m * q * j[1] + q * q * j[2]))
    return c0, c1, c2


def _section_ints(form: BodyForm, draft: float) -> Tuple[float, float]:
    """``(\\int g dz, \\int z g dz)`` over the submerged height, closed form."""
    D, p = form.form_depth, form.section_exponent
    T = draft
    # \int_{-D}^{z_wl} g dz, z_wl = T - D
    a1 = T - (D ** (p + 1.0) - (D - T) ** (p + 1.0)) / ((p + 1.0) * D ** p)
    # \int_{-D}^{z_wl} z g dz
    b1 = (-D * D * p / (2.0 * (p + 2.0))
          + (D - T) ** 2 / 2.0
          - (D - T) ** (p + 2.0) / ((p + 2.0) * D ** p))
    return a1, b1


def closed_form_hydrostatics(form: BodyForm, draft: float) -> Dict[str, float]:
    """Exact hydrostatics of ``form`` immersed to ``draft``."""
    B, L, D, p = form.beam, form.length, form.form_depth, form.section_exponent
    z_wl = form.waterline_z(draft)
    c0, c1, c2 = _plan_ints(form)
    a1, b1 = _section_ints(form, draft)
    g_wl = 1.0 - ((D - draft) / D) ** p

    volume = B * c0 * a1
    am = B * a1                      # f(0) = 1 for every admissible form
    awp = B * g_wl * c0
    it = (2.0 / 3.0) * (B / 2.0) ** 3 * g_wl ** 3 * c1
    il = B * g_wl * c2
    z_c = b1 / a1
    kb = z_c + D
    bmt = it / volume
    bml = il / volume
    return {
        "draft": draft,
        "waterline_z": z_wl,
        "displacement_volume": volume,
        "section_area_midship": am,
        "waterplane_area": awp,
        "cb": volume / (L * B * draft),
        "cm": am / (B * draft),
        "cp": volume / (am * L),
        "cwp": awp / (L * B),
        "cvp": volume / (awp * draft),
        "kb": kb,
        "lcb": 0.0,                  # plan factor is even in x -> exact
        "lcf": 0.0,
        "transverse_inertia": it,
        "longitudinal_inertia": il,
        "bmt": bmt,
        "bml": bml,
        "kmt": kb + bmt,
    }


def surface_reference(form: BodyForm, draft: float, n_x: int = 40,
                      n_z: int = 24) -> float:
    """Wetted surface by a high-order (8-node Gauss) quadrature.

    Independent re-implementation of the integrand derivatives (logarithmic
    form), used as the reference against which the production 2x2 rule is
    measured.  Declared as *quadrature* (no closed form exists for this surface).
    """
    z_wl = form.waterline_z(draft)
    half = 0.5 * form.length
    B, L, D = form.beam, form.length, form.form_depth
    n, s, p, m = form.plan_exponent, form.plan_shape, form.section_exponent, form.midbody_fraction

    def integrand(x: float, z: float) -> float:
        u = 2.0 * abs(x) / L
        if u >= 1.0:
            return math.sqrt(1.0)      # half-breadth -> 0 there; area element -> 1
        if u <= m:
            f, df = 1.0, 0.0
        else:
            v = (u - m) / (1.0 - m)
            base = 1.0 - v ** n
            f = base ** s
            df = -s * n * v ** (n - 1.0) * (base ** (s - 1.0)) / (1.0 - m) if base > 0 else 0.0
        if z >= 0.0 or abs(z) >= D:
            g, dg = (1.0 if z >= 0.0 else 0.0), 0.0
        else:
            r = abs(z) / D
            g = 1.0 - r ** p
            dg = p * r ** (p - 1.0) / D
        y_x = 0.5 * B * df * (2.0 / L) * g
        y_z = 0.5 * B * f * dg
        return math.sqrt(1.0 + y_x * y_x + y_z * y_z)

    dx = half / n_x
    dz = (z_wl + D) / n_z
    total = 0.0
    for i in range(n_x):
        x0 = i * dx
        for j in range(n_z):
            z0 = -D + j * dz
            cell = 0.0
            for xi, wi in GAUSS8:
                x = x0 + 0.5 * dx * (1.0 + xi)
                for zj, wj in GAUSS8:
                    z = z0 + 0.5 * dz * (1.0 + zj)
                    cell += wi * wj * integrand(x, z)
            total += 0.25 * dx * dz * cell
    return 2.0 * total