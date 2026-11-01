"""
Ship resistance core — pure functions, single source of truth.

Extracted from :mod:`api.calculations` (mission m1388) so that the same physics
serves both the public endpoint ``POST /api/v1/calculations/resistance`` and the
in-process benchmark validation chain
(``POST /api/v1/calculations/benchmark_validate``).

Before m1388 the benchmark chain reached the resistance model over HTTP to
``localhost:8002`` with a payload/response schema that did not match the actual
endpoint (``speed`` vs ``speed_ms``, response fields ``ct``/``method`` that the
endpoint never returned).  Whenever that hop was unavailable every benchmark
case degraded to ``calc_ct = 0.0`` / ``error_pct = 999`` — i.e. the "对标算例"
capability was silently MOCK.  The physics now lives here and is importable.

Model basis (unchanged from the merged endpoint, so endpoint outputs are
bit-identical for the documented inputs):

* wetted surface — axisymmetric-body approximation ``S = pi*B*L*(0.5 + 0.25*Cb)``
* friction       — ITTC-1957 correlation line ``Cf = 0.075 / (log10(Re) - 2)^2``
* residual       — empirical fit ``Cr = 0.002*(1 + 2*Cb)*Fn^2.5`` (Fn > 0.05)

Applicability warning: the geometry and residual-resistance fits were calibrated
on SUBOFF-like axisymmetric bodies.  They are *not* a general merchant-ship
resistance method (no Holtrop wave/ bulb/ transom terms, no form factor).  See
``docs/reports/m1388_benchmark_chain_audit.md``.
"""
from __future__ import annotations

import math
from typing import Dict

G = 9.81
DEFAULT_WATER_DENSITY = 998.0
DEFAULT_KINEMATIC_VISCOSITY = 1.004e-6


# ---------------------------------------------------------------------------
# Non-dimensional quantities
# ---------------------------------------------------------------------------

def reynolds_number(speed: float, length: float, nu: float = DEFAULT_KINEMATIC_VISCOSITY) -> float:
    """Re = V * L / nu."""
    return speed * length / nu


def froude_number(speed: float, length: float, g: float = G) -> float:
    """Fn = V / sqrt(g * L)."""
    return speed / math.sqrt(g * length)


# ---------------------------------------------------------------------------
# Friction lines
# ---------------------------------------------------------------------------

def ittc57_cf(re: float) -> float:
    """ITTC-1957 correlation line Cf = 0.075 / (log10(Re) - 2)^2."""
    if re <= 0:
        raise ValueError("Reynolds number must be positive")
    denom = math.log10(re) - 2.0
    if denom <= 0:
        raise ValueError("Reynolds number too small for ITTC-1957 (log10(Re) <= 2)")
    return 0.075 / denom ** 2


def schoenherr_cf(re: float, tol: float = 1e-14, max_iter: int = 100) -> float:
    """Schoenherr (1932) average friction line, solved for Cf.

    ``0.242 / sqrt(Cf) = log10(Re * Cf)`` is implicit in Cf; solved here by
    bisection on the monotone residual, which keeps the helper dependency-free
    and deterministic.  Used only as an *independent* cross-check of the
    ITTC-1957 line (two distinct historical correlations, not a tautology).
    """
    if re <= 0:
        raise ValueError("Reynolds number must be positive")

    def resid(cf: float) -> float:
        return 0.242 / math.sqrt(cf) - math.log10(re * cf)

    lo, hi = 1e-6, 0.02
    f_lo = resid(lo)
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = resid(mid)
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Resistance model
# ---------------------------------------------------------------------------

def wetted_surface_axisym(beam: float, length: float, cb: float) -> float:
    """Axisymmetric-body wetted-surface approximation used by the endpoint."""
    return math.pi * beam * length * (0.5 + 0.25 * cb)


def residual_coefficient(fr: float, cb: float, fn_floor: float = 0.05) -> float:
    """Empirical residual-resistance coefficient (SUBOFF-calibrated fit)."""
    return 0.002 * (1.0 + 2.0 * cb) * fr ** 2.5 if fr > fn_floor else 0.0


def resistance_breakdown(
    length: float,
    beam: float,
    draft: float,
    cb: float,
    speed: float,
    rho: float = DEFAULT_WATER_DENSITY,
    nu: float = DEFAULT_KINEMATIC_VISCOSITY,
) -> Dict[str, float]:
    """Full resistance breakdown: S, Re, Fn, Cf, Cr, Rf, Rr, Rt, Ct."""
    s_wet = wetted_surface_axisym(beam, length, cb)
    re = reynolds_number(speed, length, nu)
    fn = froude_number(speed, length)
    cf = ittc57_cf(re)
    cr = residual_coefficient(fn, cb)
    q = 0.5 * rho * s_wet * speed ** 2
    rf = q * cf
    rr = q * cr
    return {
        "wetted_surface_m2": s_wet,
        "reynolds": re,
        "froude": fn,
        "cf": cf,
        "cr": cr,
        "ct": cf + cr,
        "rf_n": rf,
        "rr_n": rr,
        "rt_n": rf + rr,
    }


def ct_from_rt(rt: float, rho: float, s_wet: float, speed: float) -> float:
    """Ct = Rt / (0.5 * rho * S * V^2) — the identity used to audit datasets."""
    q = 0.5 * rho * s_wet * speed ** 2
    if q <= 0:
        raise ValueError("Dynamic pressure must be positive")
    return rt / q


# ---------------------------------------------------------------------------
# Derived geometry helpers
# ---------------------------------------------------------------------------

def displacement_from_cb(length: float, beam: float, draft: float, cb: float,
                         rho: float = DEFAULT_WATER_DENSITY) -> float:
    """Displacement in tonnes from the block coefficient (nabla = Cb * L * B * T)."""
    return cb * length * beam * draft * rho / 1000.0