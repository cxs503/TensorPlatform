"""Independent hydrostatics reference for hull-form verification (m1576).

Why this file exists
--------------------
`GET /api/v1/geometry/hydro-curves/{ship}` (runtime repo
`ship-performance-platform/backend/api/hydrostatics.py`) is audited by a
*second, independent* integration of the same physics: fixed hull form,
watertight trim, integration only over the part of the hull below the
waterline.  Nothing here imports the audited code path, so agreement (or
disagreement) is evidence, not self-consistency.

Conventions (ITTC / naval architecture)
---------------------------------------
x in [0, L] from AP (aft perpendicular) to FP, z in [0, T] above the
base line, half-breadth b(x, z) >= 0 measured from the centre plane.

    nabla (displacement volume) = int int 2 b dz dx
    Cb    = nabla / (L B T)                       (block coefficient)
    Awp   = int 2 b(x, T) dx                      (waterplane area)
    Cwp   = Awp / (L B)
    VCB   = int int 2 b z dz dx / nabla           (centre of buoyancy, KB)
    LCB   = int int 2 b x dz dx / nabla           (from AP)
    IT    = int (2/3) b(x,T)^3 dx                 (transverse 2nd moment)
    BM    = IT / nabla
    Sw    = int int 2 sqrt(1 + (db/dx)^2) dz dx   (wetted side surface)
            + int 2 b(x, 0) dx                    (wetted bottom)

Two hulls with closed-form answers are provided as calibration cases, so
the integrator itself is verified before it is used as a reference:

  Box(L, B, T)             nabla = LBT, Cb = 1, KB = T/2, BM = B^2/(12T),
                           Sw = LB + 2LT
  ParabolicWallSided(L,B,T) b(x) = (B/2)(1-u^2), u = 2x/L - 1
                           nabla = (2/3)LBT, Cb = Cwp = 2/3, KB = T/2,
                           BM = (2/35) B^2/T, IT = (4/105) B^3 L
"""
from __future__ import annotations

from typing import Callable, Dict

import numpy as np

__all__ = ["Box", "ParabolicWallSided", "hydrostatics", "KCS_PARTICULARS"]


class Box:
    """Wall-sided rectangular pontoon: the unique hull with nabla = LBT."""

    name = "box"

    def half_breadth(self, x, z):
        return np.full(np.broadcast(x, z).shape, 1.0) * (self.B / 2.0)

    def __init__(self, L: float = 100.0, B: float = 20.0, T: float = 8.0):
        self.L, self.B, self.T = float(L), float(B), float(T)


class ParabolicWallSided:
    """Wall-sided hull with a parabolic waterplane (b independent of z).

    Chosen because every hydrostatic quantity has a closed form, including
    the *curved* wetted side surface, so the arc-length part of the
    integrator is calibrated too (a box alone would hide sign/derivative
    bugs in the sqrt(1 + (db/dx)^2) term).
    """

    name = "parabolic_wall_sided"

    def __init__(self, L: float = 100.0, B: float = 20.0, T: float = 8.0):
        self.L, self.B, self.T = float(L), float(B), float(T)

    def half_breadth(self, x, z):
        u = 2.0 * np.asarray(x, dtype=float) / self.L - 1.0
        prof = 0.5 * self.B * np.clip(1.0 - u ** 2, 0.0, None)
        return np.broadcast_to(prof, np.broadcast(x, z).shape)


def hydrostatics(shape, draft: float, nx: int = 801, nz: int = 201) -> Dict[str, float]:
    """Hydrostatics of `shape` at `draft` (trapezoidal, fine uniform grid).

    The hull form is generated ONCE at design draft T and only the part
    below `draft` is integrated: this is the property the audited endpoint
    violates (it rescales the whole hull at every draft).
    """
    L, B, T = shape.L, shape.B, shape.T
    d = float(min(max(draft, 0.0), T))
    if d <= 0.0:
        return {k: 0.0 for k in ("nabla", "displacement_t", "Cb", "Awp", "Cwp",
                                 "VCB", "LCB", "IT", "BM", "wet_surface", "draft")}
    x = np.linspace(0.0, L, nx)
    z = np.linspace(0.0, d, nz)
    X, Z = np.meshgrid(x, z, indexing="ij")
    b = np.asarray(shape.half_breadth(X, Z), dtype=float)
    area = np.trapezoid(2.0 * b, z, axis=1)              # section area A(x)
    nabla = np.trapezoid(area, x)
    mom_z = np.trapezoid(np.trapezoid(2.0 * b * Z, z, axis=1), x)
    mom_x = np.trapezoid(area * x, x)

    b_wl = np.asarray(shape.half_breadth(x, np.full_like(x, d)), dtype=float)
    awp = np.trapezoid(2.0 * b_wl, x)
    it = np.trapezoid((2.0 / 3.0) * b_wl ** 3, x)

    dbdx = np.gradient(b, x, axis=0, edge_order=2)
    side = np.trapezoid(np.trapezoid(2.0 * np.sqrt(1.0 + dbdx ** 2), z, axis=1), x)
    b_keel = np.asarray(shape.half_breadth(x, np.zeros_like(x)), dtype=float)
    bottom = np.trapezoid(2.0 * b_keel, x)

    return {
        "nabla": float(nabla),
        "displacement_t": float(nabla * 1.025),
        "Cb": float(nabla / (L * B * d)),
        "Awp": float(awp),
        "Cwp": float(awp / (L * B)),
        "VCB": float(mom_z / nabla),
        "LCB": float(mom_x / nabla),
        "IT": float(it),
        "BM": float(it / nabla),
        "wet_surface": float(side + bottom),
        "draft": d,
    }


def closed_form_box(L: float, B: float, T: float, draft: float) -> Dict[str, float]:
    d = min(draft, T)
    nabla = L * B * d
    return {"nabla": nabla, "Cb": 1.0, "Awp": L * B, "Cwp": 1.0, "VCB": d / 2.0,
            "LCB": L / 2.0, "IT": B ** 3 * L / 12.0, "BM": B ** 2 / (12.0 * d),
            "wet_surface": L * B + 2.0 * L * d}


def closed_form_parabolic(L: float, B: float, T: float, draft: float) -> Dict[str, float]:
    d = min(draft, T)
    nabla = 2.0 / 3.0 * L * B * d
    return {"nabla": nabla, "Cb": 2.0 / 3.0, "Awp": 2.0 / 3.0 * L * B,
            "Cwp": 2.0 / 3.0, "VCB": d / 2.0, "LCB": L / 2.0,
            "IT": 4.0 / 105.0 * B ** 3 * L, "BM": 2.0 / 35.0 * B ** 2 / d,
            # wetted surface has a closed form too but involves the elliptic
            # arc integral; it is calibrated against scipy.integrate.quad in
            # tests/test_hydrostatics_reference.py instead of hardcoded here.
            "wet_surface": float("nan")}


# KCS (KRISO Container Ship) particulars, public benchmark hull
# (SIMMAN/Gothenburg 2010 geometry description; volume and wetted surface
#  are the standard published values used for resistance benchmarking).
KCS_PARTICULARS = {
    "Lpp": 230.0,
    "B": 32.2,
    "T": 10.8,
    "Cb": 0.6505,
    "nabla_m3": 52030.3,
    "displacement_t": 52030.3 * 1.025,
    "wet_surface_m2": 9424.0,
    "LCB_aft_of_midship_m": -3.484,
    "source": "public KCS benchmark particulars (SNAME/Gothenburg 2010)",
}