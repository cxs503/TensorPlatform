"""Closed-form structural baselines used to audit the platform structure endpoints.

Every baseline here is *independent of the platform implementation*: it is either a
classical textbook solution or a classification-society rule formula.  All inputs are
SI-consistent with shipbuilding practice (dimensions in m, stresses in MPa, moments in
kN*m) so that a unit slip of 1e3 or 1e6 is detectable by inspection.

References
----------
[1] Timoshenko & Gere, *Theory of Elastic Stability*, 2nd ed., McGraw-Hill 1961,
    Art. 9.2 (Eq. 9-3): simply supported rectangular plate, uniaxial compression,
    sigma_cr = k * pi^2 * D / (b^2 * t),  D = E t^3 / (12 (1 - nu^2)),
    k = min_{m>=1} (m*b/a + a/(m*b))^2,  k_min = 4 at a/b integer.
[2] IACS Unified Requirement S11 (Rev.7 2010), 5.2.1: wave bending moment amidships
    M_wh = +0.19 C L^2 B C_B   [kN*m]   (hogging),
    M_ws = -0.11 C L^2 B (C_B + 0.7) [kN*m]   (sagging),
    C = 10.75 - ((300 - L)/100)^1.5  for 90 <= L <= 300.
[3] IACS Unified Requirement S7.1.1: minimum amidships section modulus
    W_min = C L^2 B (C_B + 0.7) * 1e-6  [m^3].
[4] DNV-RP-C203 (2016), Table 2-1, S-N curves in air, two-slope form
    log10 N = log10 a1 - m1 log10(S)          (N <= 1e7)
    log10 N = log10 a2 - m2 log10(S)          (N > 1e7)
    C-curve: (log a1, m1) = (12.592, 3.0), (log a2, m2) = (16.320, 5.0)
    D-curve: (log a1, m1) = (12.164, 3.0), (log a2, m2) = (15.606, 5.0)
    The two branches must meet at N = 1e7 (continuity is asserted in the unit tests).
[5] Palmgren-Miner linear damage accumulation: D = sum(n_i / N_i); life = 1/D_year.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------- plate buckling

def plate_rigidity(E: float, t: float, nu: float = 0.3) -> float:
    """Flexural rigidity D = E t^3 / (12 (1 - nu^2))  [MPa*m^3] for E in MPa, t in m."""
    return E * t ** 3 / (12.0 * (1.0 - nu ** 2))


def plate_buckling_coefficient(aspect_ratio: float, max_m: int = 60) -> tuple[float, int]:
    """Return (k_min, m_argmin) for a simply supported plate under uniaxial compression.

    aspect_ratio = a / b  with a the loaded (long) edge and b the width [1].
    """
    if aspect_ratio <= 0:
        raise ValueError("aspect_ratio must be positive")
    best_k, best_m = float("inf"), 1
    for m in range(1, max_m + 1):
        k = (m / aspect_ratio + aspect_ratio / m) ** 2
        if k < best_k:
            best_k, best_m = k, m
    return best_k, best_m


def plate_buckling_stress(a: float, b: float, t: float,
                          E: float = 210000.0, nu: float = 0.3) -> dict:
    """Simply supported plate, uniaxial compression: critical edge stress in MPa [1].

    Returns dict with k, m, D, sigma_cr [MPa] and the total edge force
    F = sigma_cr * b * t / 1e3  [kN] -- the physically meaningful companion of the
    stress, used to audit the endpoint's `critical_load` field.
    """
    k, m = plate_buckling_coefficient(a / b)
    D = plate_rigidity(E, t, nu)
    sigma_cr = k * math.pi ** 2 * D / (b ** 2 * t)
    return {
        "k": k,
        "m": m,
        "D": D,
        "sigma_cr": sigma_cr,          # MPa
        "edge_force_kN": sigma_cr * b * t / 1.0e3,
    }


# ------------------------------------------------------- hull girder (rule based)

def iacs_wave_bending_moment(L: float, B: float, Cb: float) -> dict:
    """IACS UR S11 5.2.1 wave bending moments amidships [kN*m] [2].

    C is evaluated with the L-dependent expression (valid 90 <= L <= 300); outside
    that range the value is clamped and the flag `extrapolated` is set so callers
    can decide whether to trust the baseline.
    """
    if 90.0 <= L <= 300.0:
        C = 10.75 - ((300.0 - L) / 100.0) ** 1.5
        extrapolated = False
    else:
        C = 10.75 if L > 300.0 else 10.75 - ((300.0 - 90.0) / 100.0) ** 1.5
        extrapolated = True
    M_wh = 0.19 * C * L ** 2 * B * Cb
    M_ws = -0.11 * C * L ** 2 * B * (Cb + 0.7)
    return {"C": C, "M_wh": M_wh, "M_ws": M_ws, "extrapolated": extrapolated}


def iacs_min_section_modulus(L: float, B: float, Cb: float) -> float:
    """IACS UR S7.1.1 minimum amidships section modulus W_min [m^3] [3]."""
    C = iacs_wave_bending_moment(L, B, Cb)["C"]
    return C * L ** 2 * B * (Cb + 0.7) * 1.0e-6


def hull_girder_stress_rule(L: float, B: float, Cb: float) -> dict:
    """Rule-based hull girder wave bending stress at the strength deck / keel [MPa].

    sigma = M / W_min  (M in kN*m -> MPa with /1e3 and W in m^3).
    """
    mb = iacs_wave_bending_moment(L, B, Cb)
    W = iacs_min_section_modulus(L, B, Cb)
    return {
        "W_min_m3": W,
        "M_wh_kNm": mb["M_wh"],
        "M_ws_kNm": mb["M_ws"],
        "sigma_hog_MPa": mb["M_wh"] / W / 1.0e3,
        "sigma_sag_MPa": abs(mb["M_ws"]) / W / 1.0e3,
        "extrapolated": mb["extrapolated"],
    }


def hull_girder_stress_box(L: float, B: float, D: float, Cb: float, t: float) -> dict:
    """First-principles estimate of the same quantity via an idealised box girder.

    Single-wall box of breadth B, depth D and uniform plating thickness t:
        I = 2 * B t (D/2)^2 + 2 * (t D^3 / 12)      [m^4]
        W = I / (D/2)                               [m^3]
        sigma = M_ws / W
    This route shares no algebra with the IACS rule formulas, so it can serve as the
    positive-control implementation of the stress field.
    """
    I = 2.0 * B * t * (D / 2.0) ** 2 + 2.0 * (t * D ** 3 / 12.0)
    W = I / (D / 2.0)
    mb = iacs_wave_bending_moment(L, B, Cb)
    sigma_sag = abs(mb["M_ws"]) / W / 1.0e3
    sigma_hog = mb["M_wh"] / W / 1.0e3
    return {"W_m3": W, "I_m4": I,
            "sigma_sag_MPa": sigma_sag, "sigma_hog_MPa": sigma_hog}


def allowable_normal_stress(yield_stress: float, factor: float = 0.6) -> float:
    """Permissible hull girder normal stress 0.6 * sigma_y [MPa].

    The platform's /structure/analyze returns exactly 0.6*sigma_y (141.0 MPa for
    sigma_y = 235), so this is the convention under audit, not an outside claim.
    """
    return factor * yield_stress


# ------------------------------------------------------------------- fatigue S-N

@dataclass(frozen=True)
class SNCurve:
    """Two-slope S-N curve taken from DNV-RP-C203 Table 2-1 (in air) [4]."""

    name: str
    log_a1: float
    m1: float
    log_a2: float
    m2: float
    knee_cycles: float = 1.0e7

    def cycles(self, stress_range: float) -> float:
        """Allowable number of cycles at a constant stress range [MPa]."""
        if stress_range <= 0:
            raise ValueError("stress_range must be positive")
        S = float(stress_range)
        if self.log_a1 - self.m1 * math.log10(S) <= math.log10(self.knee_cycles):
            return 10.0 ** (self.log_a1) * S ** (-self.m1)
        return 10.0 ** (self.log_a2) * S ** (-self.m2)

    def stress_at_knee(self) -> float:
        """Stress range at the N = 1e7 knee, evaluated from both branches."""
        return 10.0 ** ((self.log_a1 - math.log10(self.knee_cycles)) / self.m1)

    def stress_at_knee_second_branch(self) -> float:
        return 10.0 ** ((self.log_a2 - math.log10(self.knee_cycles)) / self.m2)


DNV_C = SNCurve("DNV-RP-C203 C (air)", 12.592, 3.0, 16.320, 5.0)
DNV_D = SNCurve("DNV-RP-C203 D (air)", 12.164, 3.0, 15.606, 5.0)


def miner_life_years(stress_ranges, cycles_per_year, curve: SNCurve = DNV_C) -> float:
    """Palmgren-Miner life [years] for a one-block-per-range spectrum [5].

    stress_ranges and cycles_per_year are equal-length sequences (n_i per year).
    """
    damage = 0.0
    for S, n in zip(stress_ranges, cycles_per_year):
        damage += n / curve.cycles(S)
    if damage <= 0:
        raise ValueError("zero damage spectrum")
    return 1.0 / damage


def implied_annual_cycles(stress_range: float, life_years: float,
                          curve: SNCurve = DNV_C) -> float:
    """Inverse of miner_life_years for a single-block spectrum: n = N(S) / life."""
    return curve.cycles(stress_range) / life_years


def plausible_wave_cycle_band(mean_period_s: tuple[float, float] = (5.0, 12.0)) -> tuple[float, float]:
    """Annual cycle band implied by wave encounter periods of 5-12 s.

    1 year = 3.15576e7 s; periods outside 4-12 s are not representative of the
    North Atlantic wave climate used by the platform's own sea-state assumptions.
    """
    lo, hi = mean_period_s
    return 3.15576e7 / hi, 3.15576e7 / lo