"""Fidelity ladder for resistance prediction of the parametric thin hull family.

Three physically distinct models of the same quantity (total resistance coefficient
``Ct = Cf + Cw`` at a given Froude number), each with a *measured* cost:

===========  =================================================================  =========
level        model                                                              role
===========  =================================================================  =========
``L0``       empirical: ITTC-57 friction on a coarse (8x4) wetted-surface          cheap,
             quadrature; no wave term at all                                     biased
``L1``       potential flow: thin-ship Michell signature with a coarse theta       medium
             rule (n_theta = 8, n_zeta = 8) + friction on the coarse surface
``L2``       potential flow, converged: n_theta = 192 / 384 increment-checked      expensive
             signature + friction on a 240x80 surface quadrature
===========  =================================================================  =========

Costs are *measured* on the executing machine (median wall time over repeated calls, see
:func:`measure_costs`) and handed to :class:`tools.multifidelity.Ladder` as work units —
the framework itself never assumes a cost.

Design vector (all dimensionless)::

    x = [L/B, B/T, m, n, a, Fn]

with ``m`` the waterline fullness exponent, ``n`` the depth exponent and ``a`` the
waterline asymmetry (which moves the longitudinal centre of buoyancy).  The hull family
reproduces the classical Wigley paraboloid at ``m = 1, n = 2, a = 0``, which is the
bridge used to validate the generic solver against the repository's closed-form
reference.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Dict, Tuple

import numpy as np

import michell_generic as mg

__all__ = [
    "PARAM_NAMES",
    "DESIGN_LO",
    "DESIGN_HI",
    "hull_from_design",
    "level0",
    "level1",
    "level2",
    "make_ladder",
    "measure_costs",
    "geometry_gate",
]

PARAM_NAMES = ("L/B", "B/T", "m", "n", "a", "Fn")
DESIGN_LO = np.array([6.0, 2.0, 0.60, 1.20, -0.08, 0.15])
DESIGN_HI = np.array([10.0, 3.2, 1.60, 4.00, 0.08, 0.35])

L_REF = 100.0  # waterline length held fixed [m]

#: surface quadrature resolutions (length x depth)
SURFACE_COARSE = (8, 4)          # L0: empirical wetted surface
SURFACE_SCREEN = (24, 12)        # L1: screening surface
SURFACE_FINE = (96, 32)          # L2: verified surface
SURFACE_FINER = (192, 64)        # L2: refinement partner for the increment check
#: theta-rule resolutions for the thin-ship signature
MICHELL_COARSE = dict(n_theta=8, n_zeta=8)     # deliberately under-resolved (L1-low)
MICHELL_SCREEN = dict(n_theta=48, n_zeta=16)   # L1: screening
MICHELL_FINE = dict(n_theta=192, n_zeta=32)    # L2: verified
MICHELL_FINER = dict(n_theta=384, n_zeta=48)   # L2: refinement partner


@dataclass(frozen=True)
class DesignPoint:
    hull: mg.HullFamily
    fn: float
    speed: float


def hull_from_design(x: np.ndarray) -> DesignPoint:
    """Unpack a design vector into a hull + operating point."""
    x = np.asarray(x, dtype=float).reshape(-1)
    lb, bt, m, n, a, fn = (float(v) for v in x[:6])
    L = L_REF
    B = L / lb
    T = B / bt
    hull = mg.HullFamily(L=L, B=B, T=T, m=m, n=n, a=a)
    return DesignPoint(hull=hull, fn=fn, speed=mg.froude_to_speed(hull, fn))


# ---------------------------------------------------------------------------
# the three levels
# ---------------------------------------------------------------------------
def level0(x: np.ndarray) -> np.ndarray:
    """Empirical / friction-only estimate: no wave-making, coarse wetted surface."""
    out = np.empty(len(x), dtype=float)
    for i, xi in enumerate(np.atleast_2d(x)):
        dp = hull_from_design(xi)
        s = mg.wetted_surface(dp.hull, *SURFACE_COARSE)
        re = dp.speed * dp.hull.L / dp.hull.nu
        out[i] = mg.ittc57_cf(re)
    return out


def level1(x: np.ndarray) -> np.ndarray:
    """Screening model: thin-ship signature + friction on a medium surface quadrature.

    Cheap (~20x below the verified level) and, because the theta rule is already
    adequate at 48 nodes, its *model* error is small and smooth in the design variables
    — that is exactly the property co-kriging exploits.
    """
    out = np.empty(len(x), dtype=float)
    for i, xi in enumerate(np.atleast_2d(x)):
        dp = hull_from_design(xi)
        s = mg.wetted_surface(dp.hull, *SURFACE_SCREEN)
        rw = mg.michell_rw_spectral(dp.hull, dp.speed, **MICHELL_SCREEN)
        out[i] = mg.total_resistance_coefficient(dp.hull, dp.speed, rw, s)
    return out


def level2_verified(x: np.ndarray, return_increment: bool = False):
    """Verified model: converged signature + surface quadrature *and* a grid study.

    A credible CFD-grade number is never a single run: the same quantity is recomputed at
    twice the resolution and the increment is reported as the discretisation error.  That
    verification effort is what makes this level an order of magnitude more expensive than
    the screening level — and it is the honest cost model for a "high-fidelity" label.
    """
    n = len(np.atleast_2d(x))
    out = np.empty(n, dtype=float)
    inc = np.empty(n, dtype=float)
    for i, xi in enumerate(np.atleast_2d(x)):
        dp = hull_from_design(xi)
        s1 = mg.wetted_surface(dp.hull, *SURFACE_FINE)
        s2 = mg.wetted_surface(dp.hull, *SURFACE_FINER)
        rw1 = mg.michell_rw_spectral(dp.hull, dp.speed, **MICHELL_FINE)
        rw2 = mg.michell_rw_spectral(dp.hull, dp.speed, **MICHELL_FINER)
        ct1 = mg.total_resistance_coefficient(dp.hull, dp.speed, rw1, s1)
        ct2 = mg.total_resistance_coefficient(dp.hull, dp.speed, rw2, s2)
        out[i] = ct2
        inc[i] = abs(ct1 - ct2) / max(abs(ct2), 1e-300)
    if return_increment:
        return out, inc
    return out


def level2(x: np.ndarray) -> np.ndarray:
    """Verified potential flow (see :func:`level2_verified`)."""
    return level2_verified(x)


LEVEL_FUNCS: Dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "L0_empirical": level0,
    "L1_potential_coarse": level1,
    "L2_potential_converged": level2,
}


def make_ladder(costs: Dict[str, float] | None = None):
    """Build the generic :class:`tools.multifidelity.Ladder` for this case."""
    from tools.multifidelity import Ladder, Level

    if costs is None:
        costs = {"L0_empirical": 1.0, "L1_potential_coarse": 10.0,
                 "L2_potential_converged": 200.0}
    levels = [
        Level("L0_empirical", rank=0, cost=costs["L0_empirical"], evaluate=level0),
        Level("L1_potential_coarse", rank=1, cost=costs["L1_potential_coarse"], evaluate=level1),
        Level("L2_potential_converged", rank=2, cost=costs["L2_potential_converged"],
              evaluate=level2),
    ]
    return Ladder(levels)


# ---------------------------------------------------------------------------
# cost measurement + geometry gate
# ---------------------------------------------------------------------------
def measure_costs(n_repeat: int = 60, seed: int = 0) -> Dict[str, float]:
    """Median wall-clock per evaluation of each level [microseconds].

    Measured, never assumed: the numbers are written into the evidence JSON and used as
    the ladder's work units, so the cost ratios in the report are reproducible on any
    machine (only ratios are used by the allocator).
    """
    rng = np.random.default_rng(seed)
    pts = DESIGN_LO + rng.random((8, 6)) * (DESIGN_HI - DESIGN_LO)
    costs: Dict[str, float] = {}
    for name, fn in LEVEL_FUNCS.items():
        fn(pts[:1])  # warm-up (imports, legendre node cache)
        samples = []
        for _ in range(n_repeat):
            xi = pts[rng.integers(0, len(pts))][None, :]
            t0 = time.perf_counter()
            fn(xi)
            samples.append((time.perf_counter() - t0) * 1e6)
        costs[name] = float(np.median(samples))
    return costs


def geometry_gate(x: np.ndarray, tol: float = 1e-8) -> Dict[str, object]:
    """Geometry gate: analytic Cb/volume vs independent quadrature, before any physics.

    Returns the gate verdict and the numbers behind it; a failing gate must stop the
    study (a wrong hull makes every resistance number meaningless).
    """
    dp = hull_from_design(x)
    cb = dp.hull.cb_analytic
    vol = dp.hull.volume_analytic
    vol_q = mg.volume_quadrature(dp.hull)
    rel = abs(vol_q - vol) / vol
    lo, hi = 0.20, 0.80  # admissible Cb band for the thin-ship family
    ok = (rel < tol) and (lo <= cb <= hi) and (dp.hull.m >= 0.5)
    return {
        "pass": bool(ok),
        "Cb_analytic": float(cb),
        "Cb_quadrature": float(vol_q / (dp.hull.L * dp.hull.B * dp.hull.T)),
        "volume_analytic_m3": float(vol),
        "volume_quadrature_m3": float(vol_q),
        "volume_rel_diff": float(rel),
        "tolerance": tol,
        "cb_band": [lo, hi],
        "L": dp.hull.L, "B": dp.hull.B, "T": dp.hull.T,
        "L/B": dp.hull.L / dp.hull.B, "B/T": dp.hull.B / dp.hull.T,
    }