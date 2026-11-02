"""Frozen snapshot of the platform stability engine formulas (audited 2026-11-01).

The engine that answers ``POST /api/v1/calculations/stability`` on the ship
performance platform (:8001) is deployed from the ``ship-performance-platform``
repository, ``backend/api/calculations.py``.  That repository is outside this
worktree's write whitelist, so the audited formulation is transcribed here
**verbatim** — this snapshot is an audit record, not production code, and must
not be "improved": if the deployed engine changes, re-audit and update the
snapshot together with ``docs/verification/stability_engine_verification_m1597.md``.

Transcribed formulas (deployed source, 2026-11-01)::

    calculate_kb(T, Cb):     KB = T * (1 - Cb/3)
    calculate_bm(B, T, Cb):  Cwp = 0.75 + 0.25*Cb
                             BM  = (Cwp**2 * B**3 / 12) / (B*T*Cb)
    calculate_gz_curve(...): phi < 15 deg : GZ = GM * sin(phi)
                             else         : GZ = (GM + 0.5*BM*sin(phi)**2) * sin(phi)
    (endpoint) angles 0..60 deg step 5, GZ clipped at 0, vanishing angle is a
    scan over that grid with default 60 (the grid cap), areas by trapezoid.
"""
from __future__ import annotations

import math
from typing import Dict, List

__all__ = ["engine_kb", "engine_bm", "engine_gz", "engine_curve"]

AUDITED_SOURCE = "ship-performance-platform backend/api/calculations.py (deploy 2026-11-01)"


def engine_kb(draft: float, cb: float) -> float:
    return draft * (1.0 - cb / 3.0)


def engine_bm(beam: float, draft: float, cb: float) -> float:
    cwp = 0.75 + 0.25 * cb
    inertia = cwp ** 2 * beam ** 3 / 12.0
    return inertia / (beam * draft * cb)


def engine_gz(phi_deg: float, gm: float, bm: float) -> float:
    phi = math.radians(phi_deg)
    if phi_deg < 15.0:
        return gm * math.sin(phi)
    return (gm + 0.5 * bm * math.sin(phi) ** 2) * math.sin(phi)


def engine_curve(kg: float, kb: float, bm: float, step: float = 5.0,
                 max_angle: float = 60.0) -> Dict:
    """Reproduce the endpoint's GZ curve, areas, GZmax and vanishing angle."""
    gm = kb + bm - kg
    n = int(round(max_angle / step))
    angles: List[float] = [i * step for i in range(n + 1)]
    gz_raw = [engine_gz(a, gm, bm) for a in angles]
    gz = [max(v, 0.0) for v in gz_raw]

    def trap(lo: float, hi: float) -> float:
        total = 0.0
        for i in range(len(angles) - 1):
            if angles[i] >= lo and angles[i + 1] <= hi:
                total += 0.5 * (gz[i] + gz[i + 1]) * math.radians(step)
        return total

    gz_max = max(gz)
    vanishing = max_angle
    for i, v in enumerate(gz):
        if v <= 0.0 and i > 0:
            vanishing = angles[i - 1] + step
            break
    return dict(gm=gm, angles=angles, gz=gz, gz_raw=gz_raw, gz_max=gz_max,
                gz_max_angle=angles[gz.index(gz_max)], vanishing_angle=vanishing,
                area_0_30=trap(0.0, 30.0), area_30_40=trap(30.0, 40.0),
                area_0_40=trap(0.0, 40.0))