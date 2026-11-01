"""Smith progressive-collapse method for ultimate hull-girder bending strength.

The method (Smith 1977; ISSC/CSR practice) integrates the load-shortening
behaviour of every longitudinal element at a given curvature, finds the
instantaneous neutral axis from axial force equilibrium, and increases the
curvature until the bending moment peaks.  That peak is the ultimate bending
moment ``M_u``.

Simplifications (documented, and priced in ``benchmarks.py``):

* plate elements are idealised as horizontal strips of an axis-aligned rectangle
  model of the midship section (no longitudinals, no hard corners);
* element load-shortening is elastic - perfectly plastic with a plate buckling
  plateau: ``sigma_u = min(sigma_y, sigma_cr)`` ("euler" mode, conservative) or
  ``sigma_u = min(sigma_y, sqrt(sigma_cr sigma_y))`` ("von_karman" mode, i.e. the
  classic effective-width level of a buckled plate);
* no strain hardening, no residual stresses, no shear lag.

Two analytic limits are reproduced exactly by construction and are used as the
implementation benchmarks:

* stocky plating (``sigma_cr >= sigma_y``) -> the element law degenerates to
  elastic - perfectly plastic, so ``M_u -> M_p = sigma_y Z_p`` (rigid-plastic
  solution, hand-derived in :func:`plastic_modulus`);
* a two-flange girder (deck + bottom plate only) -> ``M_u`` equals the
  hand-computable two-flange collapse moment with the buckled flange at
  ``sigma_u`` (see ``benchmarks.bench_two_flange``).

Element arrays are evaluated with numpy so that one collapse run costs
milliseconds; ``M_u`` is monotone in nothing, so the sweep is a plain geometric
scan of the curvature.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import numpy as np

from .section import Element, Part

__all__ = [
    "plastic_modulus",
    "equal_area_axis",
    "compression_ultimate",
    "collapse_analysis",
    "two_flange_closed_form",
    "SMITH_DEFAULTS",
]

SMITH_DEFAULTS = dict(
    sigma_y=235e6,          # mild steel [Pa]
    E=206e9,
    nu=0.3,
    kappa_min=1e-6,         # [1/m]
    kappa_max=5e-2,         # [1/m]  ~ 0.5 m fibre strain, far beyond first yield
    n_steps=300,
    mode="euler",           # "euler" | "von_karman"
    n_strips=40,
)


def equal_area_axis(parts: Sequence[Part], tol: float = 1e-12, itmax: int = 200) -> float:
    """Height ``z_eq`` that splits the section area in half (rigid-plastic axis)."""
    z_lo = min(p.z0 for p in parts)
    z_hi = max(p.z1 for p in parts)

    def area_below(z: float) -> float:
        tot = 0.0
        for p in parts:
            top = max(p.z0, min(z, p.z1))
            if top > p.z0:
                tot += (p.y1 - p.y0) * (top - p.z0)
        return tot

    total = sum(p.area for p in parts)
    for _ in range(itmax):
        mid = 0.5 * (z_lo + z_hi)
        if area_below(mid) < total / 2.0:
            z_lo = mid
        else:
            z_hi = mid
        if z_hi - z_lo < tol:
            break
    return 0.5 * (z_lo + z_hi)


def _rect_abs_first_moment(p: Part, z_eq: float) -> float:
    """``int |z - z_eq| dA`` over a rectangle, in closed form."""
    w = p.y1 - p.y0
    z0, z1 = p.z0, p.z1
    if z_eq <= z0:
        return w * (z1 - z0) * ((z0 + z1) / 2.0 - z_eq)
    if z_eq >= z1:
        return w * (z1 - z0) * (z_eq - (z0 + z1) / 2.0)
    return w * ((z_eq - z0) ** 2 + (z1 - z_eq) ** 2) / 2.0


def plastic_modulus(parts: Sequence[Part]) -> Tuple[float, float]:
    """Exact rigid-plastic section modulus ``Z_p`` and its axis ``z_eq`` [m^3, m].

    ``Z_p = sum |z - z_eq| dA`` with ``z_eq`` the equal-area axis; for a
    homogeneous elastic - perfectly plastic material the collapse moment is
    ``M_p = sigma_y Z_p`` (no buckling, no shear).
    """
    z_eq = equal_area_axis(parts)
    return sum(_rect_abs_first_moment(p, z_eq) for p in parts), z_eq


def compression_ultimate(sigma_cr: float, sigma_y: float, mode: str = "euler") -> float:
    """Load-shortening plateau stress of a compression element [Pa]."""
    if mode == "euler":
        return min(sigma_y, sigma_cr)
    if mode == "von_karman":
        return min(sigma_y, math.sqrt(max(sigma_cr, 0.0) * sigma_y))
    raise ValueError(f"unknown mode {mode!r}")


def two_flange_closed_form(area_comp: float, area_ten: float, depth: float,
                           sigma_y: float, sigma_u_comp: float) -> Dict[str, float]:
    """Hand-derived collapse moment of a two-flange girder whose compression flange
    buckles at ``sigma_u_deck < sigma_y``.

    For two flanges separated by ``depth`` the flange forces form a *couple*, so the
    moment is ``F * depth`` whatever the neutral axis position; the axial
    equilibrium ``C = T = F`` only fixes the split between the two flange stresses.
    The governing flange is therefore the weaker one::

        F = min(sigma_u_comp * A_comp, sigma_y * A_ten),      M = F * depth

    If the tension flange governs, the compression flange stays elastic at
    ``sigma_c = sigma_y A_ten / A_comp < sigma_u`` and vice versa, so both cases use
    the same formula.  With ``direction = +1`` (hogging) the *bottom* plate is the
    compression flange, so the caller passes its area as ``area_comp``.  The neutral axis sits at the *tension* flange side when
    the compression flange saturates first (curvature -> infinity), which is why the
    lever arm tends to the full ``depth`` rather than to ``depth - z_na`` of an
    elastic section: a naive "both flanges at their limit" equilibrium silently
    assumes ``sigma_u A_d >= sigma_y A_b`` and was off by 98 % on the first case
    tried (falsified by ``benchmarks.bench_two_flange``).
    """
    if min(area_comp, area_ten, depth) <= 0:
        raise ValueError("areas and depth must be positive")
    f_comp = sigma_u_comp * area_comp
    f_ten = sigma_y * area_ten
    force = min(f_comp, f_ten)
    regime = "compression flange buckles" if f_comp < f_ten else "tension flange yields"
    return {
        "M": force * depth,
        "F": force,
        "levers": depth,
        "sigma_comp": force / area_comp,
        "sigma_ten": force / area_ten,
        "regime": regime,
    }


# ------------------------------------------------------------------ solver core
def _arrays(els: Sequence[Element], sigma_y: float, mode: str):
    z = np.fromiter((e.z for e in els), dtype=float)
    a = np.fromiter((e.area for e in els), dtype=float)
    scr = np.fromiter((e.sigma_cr for e in els), dtype=float)
    if mode == "euler":
        su = np.minimum(sigma_y, scr)
    elif mode == "von_karman":
        su = np.minimum(sigma_y, np.sqrt(np.maximum(scr, 0.0) * sigma_y))
    else:
        raise ValueError(f"unknown mode {mode!r}")
    return z, a, su


def _stress_vec(eps: np.ndarray, su: np.ndarray, sigma_y: float, E: float) -> np.ndarray:
    """Vectorised element stress [Pa]: tension LEPP, compression capped at ``su``."""
    mag = np.minimum(E * np.abs(eps), np.where(eps >= 0.0, sigma_y, su))
    return np.where(eps >= 0.0, mag, -mag)


def _net_force(z, a, su, kappa: float, z_na: float, sigma_y: float, E: float) -> float:
    return float(np.sum(_stress_vec(kappa * (z - z_na), su, sigma_y, E) * a))


def _solve_neutral_axis(z, a, su, kappa: float, sigma_y: float, E: float,
                        itmax: int = 60) -> Tuple[float, bool]:
    """Bisection on the instantaneous neutral axis (axial force equilibrium).

    ``F(z_na)`` is monotone in ``z_na`` but the *sense* depends on the sign of the
    curvature (hogging raises strain when the axis moves down, sagging raises it
    when the axis moves up), so the bracket sense is decided from the two end
    values instead of being hard-coded.  A hard-coded sense silently returned the
    mid-height fallback for sagging and produced a non-equilibrium "ultimate"
    moment (falsified by ``benchmarks.bench_two_flange``).

    Returns ``(z_na, converged)``; ``converged = False`` means no root exists in
    ``[z_min, z_max]`` for this curvature (force capacities cannot balance) and the
    caller must not trust the moment.
    """
    z_lo, z_hi = float(z.min()), float(z.max())
    f_lo = _net_force(z, a, su, kappa, z_lo, sigma_y, E)
    f_hi = _net_force(z, a, su, kappa, z_hi, sigma_y, E)
    if f_lo == 0.0:
        return z_lo, True
    if f_hi == 0.0:
        return z_hi, True
    if f_lo * f_hi > 0.0:
        return 0.5 * (z_lo + z_hi), False
    for _ in range(itmax):
        mid = 0.5 * (z_lo + z_hi)
        f_mid = _net_force(z, a, su, kappa, mid, sigma_y, E)
        if f_mid == 0.0:
            return mid, True
        if f_lo * f_mid < 0.0:
            z_hi, f_hi = mid, f_mid
        else:
            z_lo, f_lo = mid, f_mid
    return 0.5 * (z_lo + z_hi), True


def collapse_analysis(section, sigma_y: float = 235e6, E: float = 206e9, nu: float = 0.3,
                      kappa_min: float = 1e-6, kappa_max: float = 5e-2, n_steps: int = 300,
                      mode: str = "euler", n_strips: int = 40,
                      direction: int = 1) -> Dict[str, object]:
    """Run Smith's method; return the moment-curvature record and ``M_u``.

    ``direction = +1`` loads toward **hogging** (deck in tension, keel in
    compression), ``direction = -1`` toward **sagging** (deck in compression).
    The two capacities differ, because only the compression side can buckle:
    a solver that scans one direction only reports half of the answer, which is
    exactly the mistake the two-flange benchmark caught (see ``benchmarks.py``).
    """
    if direction not in (1, -1):
        raise ValueError("direction must be +1 (hogging) or -1 (sagging)")
    els = section.elements(n_strips=n_strips, E=E, nu=nu)
    z, a, su = _arrays(els, sigma_y, mode)

    kappas = direction * np.geomspace(kappa_min, kappa_max, n_steps)
    moments = np.empty(n_steps)
    z_nas = np.empty(n_steps)
    na_failures = 0
    for i, kappa in enumerate(kappas):
        z_na, ok = _solve_neutral_axis(z, a, su, float(kappa), sigma_y, E)
        na_failures += 0 if ok else 1
        moments[i] = float(np.sum(_stress_vec(kappa * (z - z_na), su, sigma_y, E) * a * (z - z_na)))
        z_nas[i] = z_na

    # the loading direction fixes the sign of M, so the capacity is the extremum of
    # direction*M - taking max(M) directly would pick the *least loaded* state when
    # direction = -1 (sagging) and report a near-elastic moment as "ultimate"
    score = direction * moments
    i_u = int(np.argmax(score))
    kappa_u, z_na_u = float(kappas[i_u]), float(z_nas[i_u])
    sig_u = _stress_vec(kappa_u * (z - z_na_u), su, sigma_y, E)
    profile = [(els[i].label, float(z[i]), float(sig_u[i])) for i in range(len(els))]

    zp, z_eq = plastic_modulus(section.parts)
    z_bot, z_deck = section.section_moduli()
    return {
        "kappa": kappas.tolist(),
        "M": moments.tolist(),
        "M_u": float(moments[i_u]),
        "kappa_u": kappa_u,
        "z_na_u": z_na_u,
        "sigma_profile": profile,
        "Z_p": zp,
        "z_eq": z_eq,
        "M_p": sigma_y * zp,
        "Z_bottom": z_bot,
        "Z_deck": z_deck,
        "M_y": sigma_y * min(z_bot, z_deck),
        "mode": mode,
        "direction": direction,
        "n_strips": n_strips,
        "sigma_y": sigma_y,
        "elements": els,
        "na_failures": na_failures,
    }