"""Parametric hull geometry used by every physics module.

The hull is an idealised, analytically integrable form: rectangular sections at
full draft with a parabolic waterline half-breadth

    b(x) = (B/2) * (1 - (2x/L)^2)^p ,   p = p(Cb)

where the exponent ``p`` is solved from the block-coefficient identity

    Cb = sqrt(pi) * Gamma(p+1) / (2 * Gamma(p + 3/2)) = int_{-1/2}^{1/2} g(u) du

so that displacement ``Cb*L*B*T`` is reproduced *exactly* (geometry-first rule).
For ``Cb = 1`` the form degenerates to a rectangular barge, which is used as the
analytic verification case for the hydrostatics module (wall-sided formula).

Documented limitation: because all sections are full-depth rectangles, the
waterplane coefficient equals ``Cb`` (real ships have ``Cwp ~ (1+2Cb)/3``), so
``BM`` is ~10-20 % low for full-form hulls.  This is stated in the report and
does not affect internal consistency: every derived quantity (displacement,
``KB``, ``BM``, ``KN``) is cross-checked against an independent analytic route.
"""
from __future__ import annotations

import math
from typing import Tuple

import numpy as np

from ..types import GeometryValidationError

SQRT_PI = math.sqrt(math.pi)


def _shape_integral(p: float, power: int) -> float:
    """int_{-1/2}^{1/2} (1 - 4u^2)^(p*power) du, closed form via Beta function."""
    q = p * power
    return SQRT_PI * math.gamma(q + 1.0) / (2.0 * math.gamma(q + 1.5))


def exponent_for_cb(Cb: float) -> float:
    """Solve the block-coefficient identity for the waterline exponent p."""
    if not (0.05 < Cb <= 1.0):
        raise GeometryValidationError(f"Cb={Cb} out of range for parametric hull")
    if Cb >= 0.9999:
        return 0.0
    lo, hi = 0.0, 40.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _shape_integral(mid, 1) > Cb:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return 0.5 * (lo + hi)


class HullForm:
    """Idealised hull with exact ``Cb`` closure."""

    def __init__(self, L: float, B: float, T: float, Cb: float, rho: float = 1025.0):
        self.L, self.B, self.T, self.Cb, self.rho = float(L), float(B), float(T), float(Cb), float(rho)
        if not (0.05 < self.Cb <= 1.0):
            raise GeometryValidationError(f"Cb={self.Cb} outside (0.05, 1.0]")
        if min(self.L, self.B, self.T) <= 0:
            raise GeometryValidationError("L, B, T must be positive")
        if not (2.0 <= self.L / self.B <= 14.0):
            raise GeometryValidationError(f"L/B={self.L / self.B:.2f} outside [2, 14]")
        if not (0.02 <= self.T / self.B <= 0.60):
            raise GeometryValidationError(f"T/B={self.T / self.B:.3f} outside [0.02, 0.6]")
        self.p = exponent_for_cb(self.Cb)

    # -- shape ---------------------------------------------------------------
    def half_breadth(self, x: np.ndarray) -> np.ndarray:
        u = 2.0 * np.asarray(x, dtype=float) / self.L
        u = np.clip(u, -1.0, 1.0)
        return 0.5 * self.B * np.power(np.clip(1.0 - u * u, 0.0, None), self.p)

    def section_area(self, x: np.ndarray) -> np.ndarray:
        """Section area = 2*b(x)*T (rectangular sections, full draft)."""
        return 2.0 * self.half_breadth(x) * self.T

    # -- exact integrals -----------------------------------------------------
    @property
    def volume(self) -> float:
        return self.Cb * self.L * self.B * self.T

    @property
    def waterplane_area(self) -> float:
        return self.B * self.L * self.Cb

    @property
    def waterplane_inertia_transverse(self) -> float:
        """I_T = (2/3) * int b(x)^3 dx (analytic via Beta function)."""
        return (self.B ** 3 / 12.0) * self.L * _shape_integral(self.p, 3)

    @property
    def waterplane_inertia_longitudinal(self) -> float:
        """I_L = 2 * int b(x) x^2 dx with x = (L/2) v."""
        half = 0.5 * self.L
        # int_{-1}^{1} (1-u^2)^p u^2 du = sqrt(pi) Gamma(p+1) / (2 Gamma(p+5/2))
        v2_g = SQRT_PI * math.gamma(self.p + 1.0) / (2.0 * math.gamma(self.p + 2.5))
        return self.B * half ** 3 * v2_g

    @property
    def KB(self) -> float:
        """Vertical centre of buoyancy (rectangular sections) = T/2."""
        return 0.5 * self.T

    @property
    def BM(self) -> float:
        return self.waterplane_inertia_transverse / self.volume

    @property
    def BML(self) -> float:
        return self.waterplane_inertia_longitudinal / self.volume

    # -- consistency check ---------------------------------------------------
    def verify(self, n: int = 4001) -> dict:
        """Numerically re-integrate the displacement (geometry audit)."""
        x = np.linspace(-0.5 * self.L, 0.5 * self.L, n)
        vol = float(np.trapezoid(self.section_area(x), x))
        awp = float(np.trapezoid(2.0 * self.half_breadth(x), x))
        it = float(np.trapezoid((2.0 / 3.0) * self.half_breadth(x) ** 3, x))
        il = float(np.trapezoid(2.0 * self.half_breadth(x) * x ** 2, x))
        tgt = self.volume
        return {
            "volume_numeric_m3": vol,
            "volume_analytic_m3": tgt,
            "volume_rel_error": abs(vol - tgt) / tgt,
            "awp_numeric_m2": awp,
            "awp_analytic_m2": self.waterplane_area,
            "awp_rel_error": abs(awp - self.waterplane_area) / self.waterplane_area,
            "IT_rel_error": abs(it - self.waterplane_inertia_transverse) / self.waterplane_inertia_transverse,
            "IL_numeric_m4": il,
            "IL_rel_error": abs(il - self.waterplane_inertia_longitudinal) / self.waterplane_inertia_longitudinal,
            "Cb_input": self.Cb,
            "Cb_numeric": vol / (self.L * self.B * self.T),
            "exponent_p": self.p,
        }

    def to_dict(self) -> dict:
        return {"L": self.L, "B": self.B, "T": self.T, "Cb": self.Cb,
                "rho": self.rho, "volume_m3": round(self.volume, 2),
                "mass_t": round(self.volume * self.rho / 1000.0, 1),
                "L_over_B": round(self.L / self.B, 3),
                "B_over_T": round(self.B / self.T, 3),
                "KB_m": round(self.KB, 3), "BM_m": round(self.BM, 3),
                "BML_m": round(self.BML, 3)}

    def sac_samples(self, n: int = 21) -> Tuple[np.ndarray, np.ndarray]:
        x = np.linspace(-0.5 * self.L, 0.5 * self.L, n)
        return x, self.section_area(x)