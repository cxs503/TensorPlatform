"""Hulls whose hydrostatics are known in closed form.

Every case here is an *analytic* baseline: the reference values are derived by
hand from the surface equation (derivations reproduced in
``docs/verifier/PILOT_GEOMETRY.md`` §2) and are therefore independent of the
integrator under test.  Nothing in this module integrates numerically.

Surface convention: y = half_breadth(x, z) with x in [-L/2, L/2], z in [0, D],
hull symmetric about y = 0, draft T <= D (D = depth).

Cases
-----
box_barge      : y = B/2                              (exact rectangle: every coefficient 1)
wigley         : y = B/2 (1 - (2x/L)^2) (1 - (z/D)^2)  (polynomial, degree 2 x 2)
sineform       : y = B/2 cos(pi x / L) (1 - (z/D)^2)   (transcendental in x, exercises
                                                        Simpson's error at non-polynomial data)
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

from .ref_hydrostatics import HullForm

# Quantities that a full hydrostatics report must contain.
METRICS = ("VOL", "Cb", "Cm", "Cp", "A_W", "Cw", "I_T", "KB", "BM", "LCB", "LCF", "S_mid")


def _half_breadth_box(B: float, _D: float):
    def f(x, z):
        return np.full(np.broadcast(x, z).shape, 0.5 * B, dtype=float)

    return f


def _half_breadth_wigley(L: float, B: float, D: float):
    def f(x, z):
        return 0.5 * B * (1.0 - (2.0 * x / L) ** 2) * (1.0 - (z / D) ** 2)

    return f


def _half_breadth_sine(L: float, B: float, D: float):
    def f(x, z):
        return 0.5 * B * np.cos(np.pi * x / L) * (1.0 - (z / D) ** 2)

    return f


def box_barge(L: float, B: float, T: float) -> Tuple[HullForm, Dict[str, float]]:
    """Rectangular barge. Exact: Cb = Cm = Cp = Cw = 1."""
    form = HullForm(
        L=L, B=B, T=T, half_breadth=_half_breadth_box(B, T),
        name="box_barge", meta={"D": T},
    )
    vol = L * B * T
    exact = {
        "VOL": vol,
        "Cb": 1.0,
        "Cm": 1.0,
        "Cp": 1.0,
        "A_W": L * B,
        "Cw": 1.0,
        "I_T": L * B ** 3 / 12.0,
        "KB": T / 2.0,
        "BM": (L * B ** 3 / 12.0) / vol,
        "LCB": 0.0,
        "LCF": 0.0,
        "S_mid": B * T,
    }
    return form, exact


def wigley(L: float, B: float, T: float, D: float) -> Tuple[HullForm, Dict[str, float]]:
    """Wigley parabolic hull, drafted to T inside a depth D (D >= T).

    Exact:
        S(x)  = B (1 - (2x/L)^2) (T - T^3/(3D^2))
        VOL   = (2/3) L B (T - T^3/(3D^2))
        Cb    = (2/3) (1 - T^2/(3D^2))
        Cm    = 1 - T^2/(3D^2)              Cp = 2/3
        A_W   = (2/3) L B (1 - T^2/D^2)     Cw = (2/3)(1 - T^2/D^2)
        I_T   = 32 L k^3 / 105,  k = (B/2)(1 - T^2/D^2)
        KB    = (T/2)(1 - T^2/(2D^2)) / (1 - T^2/(3D^2))
        LCB = LCF = 0 (fore-aft symmetry)
    Degenerate when T == D: waterplane collapses to a line, A_W = I_T = 0, Cw = 0
    and LCF is undefined (excluded from the gate).
    """
    if D < T:
        raise ValueError("depth D must be >= draft T")
    form = HullForm(L=L, B=B, T=T, half_breadth=_half_breadth_wigley(L, B, D),
                    name="wigley", meta={"D": D})
    zfac = 1.0 - T ** 2 / (3.0 * D ** 2)
    s0 = B * T * zfac
    vol = (2.0 / 3.0) * L * B * T * zfac
    wl = 1.0 - T ** 2 / D ** 2
    k = 0.5 * B * wl
    a_w = (2.0 / 3.0) * L * B * wl
    i_t = 32.0 * L * k ** 3 / 105.0
    exact = {
        "VOL": vol,
        "Cb": (2.0 / 3.0) * zfac,
        "Cm": zfac,
        "Cp": 2.0 / 3.0,
        "A_W": a_w,
        "Cw": (2.0 / 3.0) * wl,
        "I_T": i_t,
        "KB": 0.5 * T * (1.0 - T ** 2 / (2.0 * D ** 2)) / zfac,
        "BM": i_t / vol,
        "LCB": 0.0,
        "LCF": 0.0 if wl > 0 else float("nan"),
        "S_mid": s0,
    }
    return form, exact


def sineform(L: float, B: float, T: float, D: float) -> Tuple[HullForm, Dict[str, float]]:
    """Cosine (sine-form) waterlines: y = B/2 cos(pi x/L) (1 - (z/D)^2).

    Exact:
        VOL = (2/pi) L B (T - T^3/(3D^2));  Cb = (2/pi)(1 - T^2/(3D^2))
        Cm  = 1 - T^2/(3D^2);               Cp = 2/pi
        A_W = (2/pi) L B (1 - T^2/D^2);     Cw = (2/pi)(1 - T^2/D^2)
        I_T = 8 L k^3 / (9 pi),  k = (B/2)(1 - T^2/D^2)
        KB  = (T/2)(1 - T^2/(2D^2)) / (1 - T^2/(3D^2))
    Used as the non-polynomial case: Simpson must show its O(h^4) order here.
    """
    if D < T:
        raise ValueError("depth D must be >= draft T")
    form = HullForm(L=L, B=B, T=T, half_breadth=_half_breadth_sine(L, B, D),
                    name="sineform", meta={"D": D})
    zfac = 1.0 - T ** 2 / (3.0 * D ** 2)
    vol = (2.0 / np.pi) * L * B * T * zfac
    wl = 1.0 - T ** 2 / D ** 2
    k = 0.5 * B * wl
    a_w = (2.0 / np.pi) * L * B * wl
    i_t = 8.0 * L * k ** 3 / (9.0 * np.pi)
    exact = {
        "VOL": vol,
        "Cb": (2.0 / np.pi) * zfac,
        "Cm": zfac,
        "Cp": 2.0 / np.pi,
        "A_W": a_w,
        "Cw": (2.0 / np.pi) * wl,
        "I_T": i_t,
        "KB": 0.5 * T * (1.0 - T ** 2 / (2.0 * D ** 2)) / zfac,
        "BM": i_t / vol,
        "LCB": 0.0,
        "LCF": 0.0,
        "S_mid": B * T * zfac,
    }
    return form, exact


#: Registry of analytic cases: name -> (builder, params, metrics excluded from the gate).
ANALYTIC_CASES = {
    "box_barge": {"builder": box_barge, "params": dict(L=100.0, B=20.0, T=8.0), "skip": ()},
    "wigley_D=T": {
        "builder": wigley, "params": dict(L=100.0, B=20.0, T=8.0, D=8.0),
        "skip": ("A_W", "Cw", "I_T", "BM", "LCF"),   # knife-edge waterplane: degenerate
    },
    "wigley_D=2T": {
        "builder": wigley, "params": dict(L=100.0, B=20.0, T=8.0, D=16.0), "skip": ()
    },
    "sineform_D=1.5T": {
        "builder": sineform, "params": dict(L=120.0, B=22.0, T=7.5, D=11.25), "skip": ()
    },
}


def build_case(name: str):
    """Return ``(HullForm, exact_dict, skip_metrics)`` for a registered case."""
    spec = ANALYTIC_CASES[name]
    form, exact = spec["builder"](**spec["params"])
    return form, exact, tuple(spec["skip"])