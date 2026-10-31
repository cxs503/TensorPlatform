"""Intact stability hydrostatics (numerical KN / GZ) and GM.

Method
------
The cross-curved waterline shifts are integrated numerically on a 2-D (x, y)
grid of the idealised hull (rectangular sections, ``b(x)`` from
``geometry.HullForm``).  For every heel angle the sinkage ``z0`` of the heeled
waterline ``z = z0 + y tan(phi)`` is solved by bisection so that the submerged
volume equals the design displacement; the centroid of the submerged volume
gives ``KN(phi)`` and hence

    GZ(phi) = KN(phi) - KG sin(phi)      (free-surface correction optional)

Numerical checks (in the test-suite and in the report)
* volume / waterplane area / I_T are compared with the closed-form integrals;
* ``dKN/dphi`` at the upright is compared with ``KB + BM - KG`` computed from
  two independent formulas (< 1 %);
* for ``Cb = 1`` (rectangular barge) ``GZ`` is compared with the analytic
  wall-sided formula ``GZ = sin(phi)(GM + BM tan^2(phi)/2)`` (< 3 %).

Validity: deck-edge immersion is not modelled (sections end at depth ``T``), so
the curve is valid up to ``phi ~ 25-30 deg`` for the target hulls.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List

import numpy as np

from .geometry import HullForm

G = 9.80665


DECK_FREEBOARD_RATIO = 1.25   # deck height / draft for the idealised hull


def _grid(hull: HullForm, n_x: int = 81, n_y: int = 161):
    x = np.linspace(-0.5 * hull.L, 0.5 * hull.L, n_x)
    y = np.linspace(-0.5 * hull.B, 0.5 * hull.B, n_y)
    b = hull.half_breadth(x)
    mask = np.abs(y)[None, :] <= b[:, None]
    dx = float(x[1] - x[0])
    dy = float(y[1] - y[0])
    return x, y, mask, dx, dy


def immersed_volume(hull: HullForm, heel_deg: float, grid=None) -> Dict[str, float]:
    """Submerged volume, centroid and KN at a given heel (design displacement)."""
    x, y, mask, dx, dy = grid if grid is not None else _grid(hull)
    D = DECK_FREEBOARD_RATIO * hull.T      # sections extend to the deck edge
    phi = math.radians(heel_deg)
    tn = math.tan(phi)
    target = hull.volume
    y2 = y[None, :]

    def volume_and_moments(z0: float):
        h = np.clip(z0 + y2 * tn, 0.0, D) * mask
        v = float(np.trapezoid(np.trapezoid(h, y, axis=1), x))
        return h, v

    # bisection on the waterline intercept z0 (volume is monotone in z0)
    lo = -abs(tn) * hull.B / 2.0 - 0.05 * hull.T
    hi = D + abs(tn) * hull.B / 2.0 + 0.05 * hull.T
    for _ in range(90):
        mid = 0.5 * (lo + hi)
        _, v = volume_and_moments(mid)
        if v < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-11:
            break
    z0 = 0.5 * (lo + hi)
    h, v = volume_and_moments(z0)
    zc = float(np.trapezoid(np.trapezoid(0.5 * h ** 2, y, axis=1), x)) / v
    yc = float(np.trapezoid(np.trapezoid(y2 * h, y, axis=1), x)) / v
    kn = yc * math.cos(phi) + zc * math.sin(phi)
    return {"volume_m3": v, "z0_m": z0, "zc_m": zc, "yc_m": yc, "KN_m": kn,
            "heel_deg": float(heel_deg)}


def kn_curve(hull: HullForm, heels_deg: Iterable[float], KG: float,
             grid=None) -> Dict[str, object]:
    """KN / GZ curve and derived GM."""
    grid = grid if grid is not None else _grid(hull)
    heels = [float(h) for h in heels_deg]
    kn = [immersed_volume(hull, h, grid)["KN_m"] for h in heels]
    phi = [math.radians(h) for h in heels]
    gz = [k - KG * math.sin(p) for k, p in zip(kn, phi)]

    # dKN/dphi at the upright -> metacentric height
    dphi = math.radians(1.0)
    kn_p = immersed_volume(hull, math.degrees(dphi), grid)["KN_m"]
    kn_m = immersed_volume(hull, -math.degrees(dphi), grid)["KN_m"]
    dkn_dphi = (kn_p - kn_m) / (2.0 * dphi)
    gm_numeric = dkn_dphi - KG

    return {
        "heels_deg": heels,
        "KN_m": kn,
        "GZ_m": gz,
        "GM_numeric_m": float(gm_numeric),
        "GM_hydrostatic_m": float(hull.KB + hull.BM - KG),
        "GM_rel_error": float(abs(gm_numeric - (hull.KB + hull.BM - KG)) /
                              max(abs(hull.KB + hull.BM - KG), 1e-9)),
        "GM0_m": float(kn[heels.index(0.0)]) if 0.0 in heels else float(kn[0]),
        "KB_m": float(hull.KB),
        "BM_m": float(hull.BM),
        "KG_m": float(KG),
        "max_GZ_m": float(max(gz)),
        "heel_at_max_GZ_deg": float(heels[int(np.argmax(gz))]),
        "area_0_30_mrad": float(np.trapezoid(
            [g for h, g in zip(heels, gz) if h <= 30.0],
            [math.radians(h) for h in heels if h <= 30.0])) if any(h <= 30 for h in heels) else 0.0,
    }


def wall_sided_gz(hull: HullForm, heel_deg: float, KG: float) -> float:
    """Analytic wall-sided ``GZ`` (exact for the rectangular barge, Cb = 1)."""
    phi = math.radians(heel_deg)
    gm = hull.KB + hull.BM - KG
    return math.sin(phi) * (gm + 0.5 * hull.BM * math.tan(phi) ** 2)


def stability_summary(hull: HullForm, KG: float, free_surface_m: float = 0.05) -> Dict[str, float]:
    """IMO-relevant indicators of the upright condition."""
    km = hull.KB + hull.BM
    gm = km - KG - free_surface_m
    return {
        "KM_m": float(km),
        "GM_eff_m": float(gm),
        "GM_over_B": float(gm / hull.B),
        "rolling_period_s": float(2 * math.pi * 0.8 * hull.B / math.sqrt(G * max(gm, 1e-3))),
        "GM0_criterion_IMO_m": 0.15,
        "GM_margin_m": float(gm - 0.15),
    }