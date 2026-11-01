"""Independent reference hydrostatics integrator (Simpson's first rule).

Conventions (right-handed, SI)
------------------------------
* x : longitudinal, measured from midship, positive forward, x in [-L/2, +L/2]
* y : transverse, half-breadth of the hull surface
* z : vertical, measured from base line, z in [0, T]

A hull is described by a callable ``half_breadth(x, z)`` that is vectorised
over NumPy arrays and returns y >= 0.  The hull is assumed symmetric about the
centre plane (y = 0) and wall-sided along the waterline is *not* assumed: the
waterplane is evaluated from ``half_breadth(x, T)`` itself.

Integrals (all composite Simpson, citation in docs/verifier/PILOT_GEOMETRY.md):
    S(x)  = integral_0^T 2 y dz                     sectional area
    VOL   = integral_{-L/2}^{L/2} S dx              displacement volume
    A_W   = integral 2 y(x,T) dx                    waterplane area
    I_T   = integral (2/3) y(x,T)^3 dx              transverse waterplane moment of area
    Cb    = VOL / (L B T)
    Cm    = S(0) / (B T)                            midship section coefficient
    Cp    = VOL / (S(0) L)  == Cb / Cm              prismatic coefficient
    Cw    = A_W / (L B)                             waterplane coefficient
    KB    = (1/VOL) * integral integral z * 2 y dz dx
    BM    = I_T / VOL                               transverse metacentric radius
    LCB   = (1/VOL) * integral x S(x) dx            (relative to midship, + forward)
    LCF   = (1/A_W) * integral x 2 y(x,T) dx        (relative to midship, + forward)

Simpson's rule needs an even number of panels; the panel counts are therefore
rounded up to the next even integer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict

import numpy as np


@dataclass
class HullForm:
    """A parametric hull surface, symmetric about the centre plane."""

    L: float
    B: float
    T: float
    half_breadth: Callable[[np.ndarray, np.ndarray], np.ndarray]
    name: str = "hull"
    meta: Dict[str, float] = field(default_factory=dict)

    def y(self, x, z):
        return self.half_breadth(np.asarray(x, dtype=float), np.asarray(z, dtype=float))


def _simpson_weights(n: int) -> np.ndarray:
    """Composite Simpson weights for ``n`` panels (n even), unit spacing."""
    if n % 2:
        raise ValueError("Simpson's rule requires an even number of panels")
    w = np.ones(n + 1)
    w[1:-1:2] = 4.0
    w[2:-1:2] = 2.0
    return w / 3.0


def _simpson(values: np.ndarray, grid: np.ndarray, axis: int) -> np.ndarray:
    """Composite Simpson integration of ``values`` along ``axis`` over ``grid``."""
    n = values.shape[axis] - 1
    if n % 2:
        raise ValueError("need an even number of panels along the integration axis")
    h = float(grid[1] - grid[0])
    w = _simpson_weights(n)
    shape = [1] * values.ndim
    shape[axis] = w.size
    return (values * w.reshape(shape)).sum(axis=axis) * h


def _even(n: float) -> int:
    n = int(max(4, round(n)))
    return n + (n % 2)


def compute_hydrostatics(
    form: HullForm,
    n_x: int = 160,
    n_z: int = 80,
    *,
    integrate: str = "simpson",
) -> Dict[str, float]:
    """Hydrostatic coefficients of ``form`` on an (n_x, n_z) sampling grid.

    Parameters
    ----------
    form : HullForm
    n_x, n_z : int
        Panel counts (rounded up to even values).  n_z acts on z in [0, T],
        n_x on x in [-L/2, L/2].
    integrate : {"simpson", "trapezoid"}
        Only used by the mutation gate; production runs use "simpson".
    """
    n_x, n_z = _even(n_x), _even(n_z)
    xs = np.linspace(-0.5 * form.L, 0.5 * form.L, n_x + 1)
    zs = np.linspace(0.0, form.T, n_z + 1)

    Y = form.y(xs[:, None], zs[None, :])
    if np.any(Y < -1e-12):
        raise ValueError("half_breadth returned negative values; hull convention violated")
    Y = np.maximum(Y, 0.0)

    if integrate == "simpson":
        sectional = _simpson(2.0 * Y, zs, axis=1)             # S(x)
        waterline = 2.0 * Y[:, -1]                            # full breadth at z = T
        vol = float(_simpson(sectional, xs, axis=0))
        area_w = float(_simpson(waterline, xs, axis=0))
        i_t = float(_simpson((2.0 / 3.0) * Y[:, -1] ** 3, xs, axis=0))
        mom_x = float(_simpson(sectional * xs, xs, axis=0))
        mom_zw = _simpson((2.0 * Y) * zs[None, :], zs, axis=1)
        mom_z = float(_simpson(mom_zw, xs, axis=0))
        mom_xw = float(_simpson(waterline * xs, xs, axis=0))
    elif integrate == "trapezoid":
        def trap(v, g, axis):
            return np.trapezoid(v, g, axis=axis) if hasattr(np, "trapezoid") else np.trapz(v, g, axis=axis)

        sectional = trap(2.0 * Y, zs, axis=1)
        waterline = 2.0 * Y[:, -1]
        vol = float(trap(sectional, xs, axis=0))
        area_w = float(trap(waterline, xs, axis=0))
        i_t = float(trap((2.0 / 3.0) * Y[:, -1] ** 3, xs, axis=0))
        mom_x = float(trap(sectional * xs, xs, axis=0))
        mom_z = float(trap(trap(2.0 * Y * zs[None, :], zs, axis=1), xs, axis=0))
        mom_xw = float(trap(waterline * xs, xs, axis=0))
    else:
        raise ValueError(f"unknown integrator {integrate!r}")

    s_mid = float(sectional[n_x // 2])                        # x = 0 is a grid point
    res = {
        "VOL": vol,
        "Cb": vol / (form.L * form.B * form.T),
        "Cm": s_mid / (form.B * form.T),
        "Cp": vol / (s_mid * form.L) if s_mid > 0 else float("nan"),
        "A_W": area_w,
        "Cw": area_w / (form.L * form.B),
        "I_T": i_t,
        "KB": mom_z / vol,
        "BM": i_t / vol,
        "LCB": mom_x / vol,
        "LCF": mom_xw / area_w if area_w > 0 else float("nan"),
        "S_mid": s_mid,
        "n_x": float(n_x),
        "n_z": float(n_z),
    }
    return res


def convergence_study(
    form: HullForm,
    reference: Dict[str, float],
    quantities=("VOL", "Cb", "Cm", "Cp", "Cw", "KB", "BM"),
    grids=(40, 80, 160, 320),
) -> Dict[str, list]:
    """Relative error of each quantity at successive grid refinements.

    Returns ``{quantity: {"n": [...], "rel_err": [...], "order": [...]}}`` where
    ``order`` is the observed convergence order from the log-log slope.
    """
    out: Dict[str, dict] = {}
    for q in quantities:
        ns, errs = [], []
        for n in grids:
            r = compute_hydrostatics(form, n_x=n, n_z=n // 2)
            ref = reference[q]
            ns.append(n)
            if abs(ref) < 1e-15:
                errs.append(abs(r[q] - ref))
            else:
                errs.append(abs(r[q] - ref) / abs(ref))
        orders = []
        for i in range(1, len(errs)):
            if errs[i] > 0 and errs[i - 1] > 0:
                orders.append(float(np.log(errs[i - 1] / errs[i]) / np.log(ns[i] / ns[i - 1])))
            else:
                orders.append(float("nan"))
        out[q] = {"n": ns, "rel_err": errs, "order": orders}
    return out