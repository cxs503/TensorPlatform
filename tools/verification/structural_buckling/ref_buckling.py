"""Independent closed-form references for ship-structure panel buckling.

Nothing in this module imports platform code: every expression is reproduced from
the primary source so that served results are judged against an *external*
baseline instead of against themselves.

Sources
-------
* Uniaxial compression of a rectangular plate, simply supported edges:
  Timoshenko & Gere, "Theory of Elastic Stability", 2nd ed., Ch. 9,
  sigma_cr = k * pi^2 * D / (b^2 * t), D = E t^3 / (12 (1 - nu^2)),
  k_min = 4.0 for a/b >= 1 (integer half-wave number m = int(a/b)).
* Clamped edges: DNV-RP-C201 (2021) Table 6-1 / IACS CSR notation,
  k = 4.00 (simply supported), k = 6.97 (clamped), long-panel regime a/b >= 1.
  Same constant is used by Bleich / Bulson tabulations ("6.97" asymptote).
* Shear buckling, simply supported long panel:
  tau_cr = k_s * pi^2 * D / (b^2 * t) with k_s = 5.34 + 4 (b/a)^2
  (Timoshenko & Gere, Ch. 9, Eq. for infinite strip in shear).
* Column (stiffener as Euler strut over span L): sigma_E = pi^2 E I / (A L^2).
* Plate-induced collapse of a slender panel: Faulkner (1975)
  sigma_u / sigma_y = 2/beta - 1/beta^2, beta = (b/t) sqrt(sigma_y/E), beta >= 1;
  Johnson-Ostenfeld inelastic correction
  sigma_u = sigma_y (1 - sigma_y / (4 sigma_cr)) for sigma_cr > sigma_y / 2.

All stresses are returned in MPa, lengths and inputs in SI (m, Pa).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Standard closed-form coefficients (dimensionless buckling factors).
K_SS_LONG = 4.00          # simply supported, uniaxial, a/b >= 1  (Timoshenko)
K_CLAMPED_LONG = 6.97     # clamped edges, uniaxial, a/b >= 1      (DNV-RP-C201 Tab. 6-1)
K_SHEAR_SS = 5.34         # shear, simply supported long panel     (Timoshenko)


def flexural_rigidity(E: float, t: float, nu: float) -> float:
    """Plate flexural rigidity D [N m] for E in Pa, t in m."""
    return E * t ** 3 / (12.0 * (1.0 - nu ** 2))


def k_uniaxial(aspect: float, clamped: bool = False) -> float:
    """Uniaxial-compression buckling factor for a panel of aspect ratio a/b >= 1.

    Simply supported: exact integer half-wave minimisation of (m/aspect + aspect/m)^2.
    Clamped: long-panel asymptote 6.97 (DNV-RP-C201 Tab. 6-1).
    """
    if aspect < 1.0:
        raise ValueError("reference restricted to a/b >= 1 (long-panel regime)")
    if clamped:
        return K_CLAMPED_LONG
    m = max(1, int(round(aspect)))
    return min((m / aspect + aspect / m) ** 2 for m in (m - 1, m, m + 1) if m >= 1)


def sigma_cr_plate(E: float, nu: float, b: float, t: float,
                   aspect: float, clamped: bool = False) -> float:
    """Elastic uniaxial buckling stress of a panel, MPa."""
    D = flexural_rigidity(E, t, nu)
    k = k_uniaxial(aspect, clamped)
    return k * math.pi ** 2 * D / (b ** 2 * t) / 1e6


def k_shear(aspect: float) -> float:
    """Shear buckling factor, simply supported long panel, aspect = a/b >= 1."""
    return K_SHEAR_SS + 4.0 * (1.0 / aspect) ** 2


def tau_cr_plate(E: float, nu: float, b: float, t: float, aspect: float) -> float:
    """Elastic shear buckling stress of a panel, MPa."""
    D = flexural_rigidity(E, t, nu)
    return k_shear(aspect) * math.pi ** 2 * D / (b ** 2 * t) / 1e6


def handy_coefficient(k: float, nu: float = 0.3) -> float:
    """Dimensionless C in sigma_cr = C * E * (t/b)^2 (textbook shorthand)."""
    return k * math.pi ** 2 / (12.0 * (1.0 - nu ** 2))


def sigma_euler_column(E: float, I: float, A: float, span: float) -> float:
    """Euler strut buckling stress sigma_E = pi^2 E I / (A L^2), MPa (SI inputs)."""
    return math.pi ** 2 * E * I / (A * span ** 2) / 1e6


@dataclass(frozen=True)
class TeeSection:
    """Tee stiffener with effective plate (width s, thickness t) - geometric props."""
    hw: float
    tw: float
    bf: float
    tf: float
    s: float
    t: float

    def props(self) -> dict:
        """Area, centroid (from plate mid-surface), inertia, radius of gyration."""
        A_w, A_f, A_p = self.hw * self.tw, self.bf * self.tf, self.s * self.t
        y_w, y_f, y_p = self.hw / 2.0, self.hw + self.tf / 2.0, -self.t / 2.0
        A = A_w + A_f + A_p
        y_bar = (A_w * y_w + A_f * y_f + A_p * y_p) / A
        I = (self.tw * self.hw ** 3 / 12.0 + A_w * (y_w - y_bar) ** 2
             + self.bf * self.tf ** 3 / 12.0 + A_f * (y_f - y_bar) ** 2
             + self.s * self.t ** 3 / 12.0 + A_p * (y_p - y_bar) ** 2)
        return {"A": A, "y_bar": y_bar, "I": I,
                "r": math.sqrt(I / A), "Z": I / max(self.hw + self.tf - y_bar, y_bar + self.t)}


def faulkner_ultimate(sigma_y: float, b: float, t: float, E: float) -> float:
    """Plate-induced ultimate strength, MPa (Faulkner 1975 + plate slenderness beta)."""
    beta = (b / t) * math.sqrt(sigma_y / E)
    if beta <= 1.0:
        return sigma_y / 1e6
    return sigma_y * (2.0 / beta - 1.0 / beta ** 2) / 1e6


def johnson_ostenfeld(sigma_cr: float, sigma_y: float) -> float:
    """Inelastic (Johnson-Ostenfeld) correction of an elastic buckling stress, MPa."""
    if sigma_cr <= sigma_y / 2.0:
        return sigma_cr
    return sigma_y * (1.0 - sigma_y / (4.0 * sigma_cr))