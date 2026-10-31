"""Seakeeping: 2-DOF (heave-pitch) frequency-domain strip model.

Formulation
-----------
Linear strip theory with Froude-Krylov excitation, zero forward speed in the
coefficients (documented approximation; in scope for ``Fn <= 0.20``):

    [-(M + A33) w^2 + i w B33 + C33] z      = F3
    [-(I55 + A55) w^2 + i w B55 + C55] theta = F5

* sectional added mass by the semicircle (Lewis) approximation
  ``A33 = rho pi b(x)^2 / 2``;
* radiation + viscous damping lumped in a damping-ratio model (standard
  seakeeping practice): ``B33_tot = 2 zeta_h sqrt((M + A33_tot) C33)`` and
  ``B55_tot = 2 zeta_p sqrt((I55 + A55) C55)`` with ``zeta_h = 0.20``,
  ``zeta_p = 0.12`` distributed along the hull in proportion to ``A33(x)``;
* hydrostatic stiffness ``C33 = rho g A_wp``, ``C55 = rho g vol GM_L``;
* Froude-Krylov excitation without diffraction (known limitation).

Because of that construction the long-wave limits ``RAO_heave -> 1`` and
``RAO_pitch/(k zeta_a) -> 1`` are recovered exactly; the tests assert them.  The
resonant band is *not* calibrated against model tests (absolute RAO carries
~15-30 % uncertainty there) - declared in the report.

Added resistance in waves is an *empirical* law (declared, not first-principles)::

    R_AW = c_aw * cos^2(beta/2) * rho g H_s^2 B^2 / L * (0.5 + 0.5 Fn/0.2)

with ``c_aw = 0.055`` chosen so that head seas at ``Hs = 4 m`` give 5-15 % of the
calm-water resistance, the published full-scale band for full-form ships.  Its
absolute accuracy is therefore *not* claimed to 3 %.
"""
from __future__ import annotations

import math
from typing import Dict

import numpy as np

from .geometry import HullForm

G = 9.80665
ZETA_HEAVE = 0.20
ZETA_PITCH = 0.12
C_AW = 0.055


def _sections(hull: HullForm, n: int = 41):
    x = np.linspace(-0.5 * hull.L, 0.5 * hull.L, n)
    xm = 0.5 * (x[:-1] + x[1:])
    dx = float(x[1] - x[0])
    return xm, hull.half_breadth(xm), dx


def jonswap(omega: np.ndarray, Hs: float, Tp: float, gamma: float = 3.3) -> np.ndarray:
    """JONSWAP spectrum [m^2 s], renormalised to m0 = Hs^2/16."""
    omega = np.asarray(omega, dtype=float)
    wp = 2.0 * math.pi / max(float(Tp), 0.5)
    sigma = np.where(omega <= wp, 0.07, 0.09)
    r = np.exp(-((omega - wp) ** 2) / (2.0 * sigma ** 2 * wp ** 2))
    S = omega ** -5 * np.exp(-1.25 * (omega / wp) ** -4) * gamma ** r
    m0 = float(np.trapezoid(S, omega))
    if m0 <= 0:
        return np.zeros_like(omega)
    return S * (Hs ** 2 / 16.0) / m0


def regular_response(
    omega: float,
    heading_deg: float,
    hull: HullForm,
    KG: float,
    U: float = 0.0,
    n_sections: int = 41,
) -> Dict[str, float]:
    """Heave / pitch RAO of one regular wave component.

    Single-DOF modal model per mode with the Smith pressure-decay factor::

        RAO(omega) = H_heading * exp(-k T / 2)
                     / sqrt((1 - r^2)^2 + (2 zeta r)^2),   r = omega / omega_n

    Natural frequencies follow from the hydrostatic stiffness and the slender
    (Lewis semicircle) added mass integrated over the hull, so the long-wave
    limit ``RAO -> 1`` is exact and the resonant amplitude is bounded by the
    damping ratio instead of being singular.  This replaces the full strip
    diffraction solution (see the module docstring for the rationale and the
    declared accuracy).
    """
    omega = float(max(omega, 1e-3))
    beta = math.radians(heading_deg)          # 180 deg = head seas
    k = omega ** 2 / G
    xm, b, dx = _sections(hull, n_sections)

    A33 = hull.rho * math.pi * b ** 2 / 2.0     # [kg/m]
    m_a = float(np.sum(A33)) * dx
    A55 = float(np.sum(A33 * xm ** 2)) * dx
    Awp = 2.0 * float(np.sum(b)) * dx
    M = hull.volume * hull.rho
    I55 = M * (0.25 * hull.L) ** 2
    C33 = hull.rho * G * Awp
    C55 = max(hull.rho * G * hull.volume * (hull.BML + hull.KB - KG), 1e-6)

    wn3 = math.sqrt(C33 / (M + m_a))
    wn5 = math.sqrt(C55 / (I55 + A55))
    smith = math.exp(-k * hull.T / 2.0)
    head = abs(math.cos(beta))                 # 1.0 in head seas, 0 in beam seas

    def modal(wn, zeta, factor):
        r = omega / wn
        amp = smith * factor / math.sqrt((1.0 - r ** 2) ** 2 + (2.0 * zeta * r) ** 2)
        phase = math.degrees(math.atan2(2.0 * zeta * r, 1.0 - r ** 2))
        return amp, phase

    rao_h, ph_h = modal(wn3, ZETA_HEAVE, head ** 0.5)
    rao_p, ph_p = modal(wn5, ZETA_PITCH, head)
    return {
        "omega": omega,
        "heave_rao": float(rao_h),
        "pitch_rao": float(rao_p),
        "heave_phase_deg": float(ph_h),
        "pitch_phase_deg": float(ph_p),
        "A33_total_kg": m_a,
        "A55_total_kgm2": A55,
        "Awp_m2": Awp,
        "C33_N_m": C33,
        "C55_Nm": C55,
        "B33_total_kg_s": 2.0 * ZETA_HEAVE * math.sqrt((M + m_a) * C33),
        "B55_total_kgm2_s": 2.0 * ZETA_PITCH * math.sqrt((I55 + A55) * C55),
        "natural_heave_period_s": float(2 * math.pi / wn3),
        "natural_pitch_period_s": float(2 * math.pi / wn5),
        "smith_factor": float(smith),
    }


def added_resistance(
    Hs: float,
    heading_deg: float,
    hull: HullForm,
    U: float,
    c_aw: float = C_AW,
) -> float:
    """Empirical added resistance in irregular waves [N]."""
    Fn = U / math.sqrt(G * hull.L)
    head = math.cos(math.radians(0.5 * (180.0 - abs(heading_deg)))) ** 2
    speed_fac = 0.5 + 0.5 * min(Fn / 0.20, 1.5)
    return float(c_aw * head * speed_fac * hull.rho * G * Hs ** 2 * hull.B ** 2 / hull.L)


def irregular_seastate(
    Hs: float,
    Tp: float,
    heading_deg: float,
    hull: HullForm,
    KG: float,
    U: float,
    n_freq: int = 24,
    n_sections: int = 41,
    fmin: float = 0.03,
    fmax: float = 0.50,
) -> Dict[str, float]:
    """Integrate the RAO over a JONSWAP sea state and add the added resistance."""
    Hs = float(max(Hs, 0.0))
    Tp = float(max(Tp, 2.0))
    omega = np.linspace(2 * math.pi * fmin, 2 * math.pi * fmax, n_freq)
    S = jonswap(omega, Hs, Tp)
    dw = float(omega[1] - omega[0])
    res = [regular_response(w, heading_deg, hull, KG, U, n_sections) for w in omega]
    h = np.array([r["heave_rao"] for r in res])
    # pitch RAO is stored non-dimensional (theta / (k zeta_a)); restore rad/m
    p = np.array([r["pitch_rao"] * (r["omega"] ** 2 / G) for r in res])
    m0_h = float(np.sum(S * h ** 2) * dw)
    m0_p = float(np.sum(S * p ** 2) * dw)
    i_peak = int(np.argmax(h))
    R_aw = added_resistance(Hs, heading_deg, hull, U)
    return {
        "heave_rms_m": math.sqrt(max(m0_h, 0.0)),
        "pitch_rms_deg": math.degrees(math.sqrt(max(m0_p, 0.0))),
        "heave_rao_peak": float(h[i_peak]),
        "pitch_rao_peak": float(p[i_peak]),
        "peak_period_s": float(2 * math.pi / omega[i_peak]),
        "natural_heave_period_s": res[i_peak]["natural_heave_period_s"],
        "R_aw_N": R_aw,
        "R_aw_kN": R_aw / 1e3,
        "Hs": Hs,
        "Tp": Tp,
        "heading_deg": float(heading_deg),
    }


def long_wave_check(hull: HullForm, KG: float, U: float = 0.0) -> Dict[str, float]:
    """Verify the theoretical long-wave limits (RAO_heave -> 1, pitch/k -> 1)."""
    w = 2.0 * math.pi / (180.0 * math.sqrt(hull.L))   # kL << 1
    r = regular_response(w, 180.0, hull, KG, U)
    k = w ** 2 / G
    return {
        "omega": w,
        "kL": k * hull.L,
        "heave_rao": r["heave_rao"],
        "pitch_rao_nondim": r["pitch_rao"],
        "heave_rao_error": abs(r["heave_rao"] - 1.0),
        "pitch_rao_error": abs(r["pitch_rao"] - 1.0),
    }