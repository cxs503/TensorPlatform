"""Structural fatigue: spectral (narrow-band) Miner damage + time-domain check.

Physics chain
-------------
1. Vertical wave bending moment at midship from the IACS UR S11 longitudinal
   strength formulae (hogging / sagging amplitudes):

       M_hog = 0.19 * C * L^2 * B * Cb      [kN m]
       M_sag = 0.11 * C * L^2 * B * Cb      [kN m]
       C = 10.75 - ((300 - L)/100)^1.5      (90 < L <= 300 m)

2. Nominal stress amplitude ``sigma_a = M / Z`` with the thin-walled
   rectangular section modulus ``Z = B T^2 / 6`` of the idealised hull.

3. Palmgren-Miner damage with the DNV-RP-C203 S-N curve F in air
   (``log10 a = 11.699``, ``m = 3``, S in MPa).  Narrow-band Gaussian process:

       D = (nu0 * T_sec / a) * (2 sqrt(2) sigma_rms)^m * Gamma(1 + m/2)

   corrected for spectral width with the Wirsching-Light factor, and validated
   in the test-suite against rain-flow counting of a synthesised bimodal stress
   record (the correction reduces the narrow-band over-prediction to a few %).

4. Ice-induced cycling is added through an encounter-rate model (number of
   load cycles per hour proportional to concentration, thickness and speed with
   the documented coefficient ``ICE_CYCLE_RATE``) so that the digital twin can
   extrapolate the damage increment in ice.

The fatigue "D" surrogate therefore returns damage per operational day and the
remaining life in years, both of which enter the online forecast.
"""
from __future__ import annotations

import math
from typing import Dict, Sequence

import numpy as np
from scipy.special import gamma as gamma_fn, gammainc, gammaincc

from .geometry import HullForm

G = 9.80665
SN_LOG_A = 11.699                  # DNV-RP-C203 curve F (air): N = a * S^-m1
SN_A = 10.0 ** SN_LOG_A
SN_M = 3.0                         # slope for N <= 1e7
SN_M2 = 5.0                        # slope for N > 1e7 (knee)
N_KNEE = 1.0e7
S_KNEE = (SN_A / N_KNEE) ** (1.0 / SN_M)      # 36.8 MPa for curve F
SN_A2 = SN_A * S_KNEE ** (SN_M2 - SN_M)       # continuity at the knee
GAMMA_1_PLUS_M_HALF = math.gamma(1.0 + SN_M / 2.0)   # Gamma(2.5) = 1.3293
ICE_CYCLE_RATE = 0.020             # Hz per (unit concentration * unit h * unit speed)


def wave_bending_moment(L: float, B: float, Cb: float) -> Dict[str, float]:
    """IACS UR S11 vertical wave bending moment amplitudes [kN m]."""
    if 90.0 < L <= 300.0:
        C = 10.75 - ((300.0 - L) / 100.0) ** 1.5
    elif 300.0 < L <= 350.0:
        C = 10.75
    else:
        C = 10.75 - ((300.0 - L) / 100.0) ** 1.5 if L <= 300 else 10.75
    base = C * L ** 2 * B * Cb
    return {"M_hog_kNm": 0.19 * base, "M_sag_kNm": 0.11 * base, "C_wave": C, "base": base}


T_DECK = 0.030      # [m] equivalent deck/bottom plating thickness (midship)
T_SIDE = 0.020      # [m] equivalent side shell thickness
HS_IACS = 11.0      # [m] sea state associated with the UR S11 design moment


def section_modulus(B: float, T: float, t_deck: float = T_DECK, t_side: float = T_SIDE) -> float:
    """Thin-walled box-girder section modulus [m^3] about the transverse axis.

    I  = 2*(B t_deck)(T/2)^2 + 2*(t_side T^3/12),  Z = I / (T/2)
    """
    I = 0.5 * B * t_deck * T ** 2 + t_side * T ** 3 / 6.0
    return I / (0.5 * T)


def stress_amplitude_mpa(hull: HullForm, sea_state_factor: float = 1.0) -> float:
    m = wave_bending_moment(hull.L, hull.B, hull.Cb)
    z = section_modulus(hull.B, hull.T)
    return float(m["M_hog_kNm"] * 1e3 * sea_state_factor / z / 1e6)


def sn_cycles(s_mpa: float) -> float:
    """Cycles to failure of the DNV-RP-C203 curve F (bilinear with knee)."""
    s = max(float(s_mpa), 1e-6)
    if s > S_KNEE:
        return SN_A * s ** -SN_M
    return SN_A2 * s ** -SN_M2


def rayleigh_bilinear_moment(sigma_rms_mpa: float) -> Dict[str, float]:
    """Spectral moments E[S^m] of the Rayleigh *range* distribution, split at the knee.

    Returns the two terms of ``E[1/N(S)]``:
      ``m3`` -> int_0^{S_knee} S^3 f(S) dS / a1
      ``m5`` -> int_{S_knee}^{inf} S^5 f(S) dS / a2
    """
    sig = max(float(sigma_rms_mpa), 1e-9)
    scale = 2.0 * math.sqrt(2.0) * sig
    xk = S_KNEE ** 2 / (8.0 * sig ** 2)
    a1, a2 = 1.0 + SN_M / 2.0, 1.0 + SN_M2 / 2.0
    # S > S_knee -> slope m1 (short life);  S < S_knee -> slope m2 (long life)
    i3 = scale ** SN_M * gamma_fn(a1) * float(gammaincc(a1, xk))
    i5 = scale ** SN_M2 * gamma_fn(a2) * float(gammainc(a2, xk))
    return {"inv_life_m3": i3 / SN_A, "inv_life_m5": i5 / SN_A2,
            "x_knee": float(xk), "S_knee_MPa": float(S_KNEE),
            "S_scale_MPa": float(scale)}


def spectral_width(Hs: float, Tp: float, U_knots: float, L: float) -> float:
    """Approximate spectral bandwidth of the encountered stress process."""
    # long-crested JONSWAP with a second (swell) component gives eps ~ 0.6-0.75
    return float(min(max(0.62 + 0.004 * U_knots + 0.002 * (Tp - 8.0), 0.45), 0.90))


def wirsching_light_factor(m: float, eps: float) -> float:
    """Wirsching-Light wide-band correction factor lambda."""
    eps = float(min(max(eps, 0.0), 1.0))
    a = 0.926 - 0.033 * m
    b = 1.587 * m - 2.323
    return float(a + (1.0 - a) * (1.0 - eps) ** b)


def spectral_fatigue(
    hull: HullForm,
    Hs: float,
    Tp: float,
    U_knots: float = 12.0,
    ice_concentration: float = 0.0,
    ice_thickness: float = 0.6,
    exposure_s: float = 86400.0,
    sigma_rms_mpa: float | None = None,
) -> Dict[str, float]:
    """Damage accumulated during ``exposure_s`` seconds of the given condition."""
    Hs = max(float(Hs), 0.0)
    Tp = float(max(Tp, 2.0))
    sig_design = wave_bending_moment(hull.L, hull.B, hull.Cb)["M_hog_kNm"] * 1e3 / section_modulus(hull.B, hull.T) / 1e6
    sig_a = sigma_rms_mpa if sigma_rms_mpa is not None else sig_design * (Hs / HS_IACS) ** 2
    sigma_proc = max(sig_a / math.sqrt(2.0), 1e-6)      # Rayleigh amplitudes
    nu0 = 1.4 / Tp
    eps = spectral_width(Hs, Tp, U_knots, hull.L)
    lam = wirsching_light_factor(SN_M, eps)
    mom = rayleigh_bilinear_moment(sigma_proc)
    d_nb = nu0 * exposure_s * (mom["inv_life_m3"] + mom["inv_life_m5"])
    d_wave = d_nb * lam

    # ice-induced cycling (encounter-rate model)
    V = max(U_knots, 0.0) * 0.5144
    f_ice = ICE_CYCLE_RATE * float(min(max(ice_concentration, 0.0), 1.0)) * float(ice_thickness) * V
    n_ice = f_ice * exposure_s
    s_ice = 0.35 * sig_a
    d_ice = n_ice / sn_cycles(2.0 * s_ice) if s_ice > 1e-9 else 0.0

    damage = d_wave + d_ice
    per_day = damage * 86400.0 / max(exposure_s, 1.0)
    return {
        "sigma_amp_MPa": float(sig_a),
        "sigma_rms_MPa": float(sigma_proc),
        "sigma_design_MPa": float(sig_design),
        "nu0_Hz": float(nu0),
        "bandwidth_eps": float(eps),
        "wideband_factor": float(lam),
        "damage_narrowband": float(d_nb),
        "damage_wave": float(d_wave),
        "damage_ice": float(d_ice),
        "damage_total": float(damage),
        "damage_per_day": float(per_day),
        "life_years": float(1.0 / per_day / 365.25) if per_day > 0 else math.inf,
        "ice_cycles": float(n_ice),
        "S_knee_MPa": float(S_KNEE),
    }


# ---------------------------------------------------------------------------
# time-domain reference (validation): rain-flow counting
# ---------------------------------------------------------------------------

def rainflow_damage(stress_mpa: np.ndarray) -> Dict[str, float]:
    """ASTM E1049 three-point rain-flow damage of a stress *range* record."""
    s = np.asarray(stress_mpa, dtype=float)
    # extract local extrema
    ext = [s[0]]
    for i in range(1, len(s) - 1):
        if (s[i] - s[i - 1]) * (s[i + 1] - s[i]) < 0:
            ext.append(s[i])
    ext.append(s[-1])
    stack: list[float] = []
    ranges: list[float] = []
    for x in ext:
        stack.append(x)
        while len(stack) >= 3:
            r1 = abs(stack[-2] - stack[-3])
            r2 = abs(stack[-1] - stack[-2])
            if r2 < r1:
                break
            ranges.append(r1)
            stack.pop(-3)                       # remove the two interior points
            stack.pop(-2)
    # residual half-cycles
    for i in range(len(stack) - 1):
        ranges.append(abs(stack[i + 1] - stack[i]))
    ranges = np.asarray(ranges, dtype=float)
    if ranges.size == 0:
        return {"damage": 0.0, "n_cycles": 0.0, "max_range_MPa": 0.0}
    n = np.array([sn_cycles(r) for r in ranges])
    return {"damage": float(np.sum(1.0 / n)), "n_cycles": float(ranges.size),
            "max_range_MPa": float(ranges.max()),
            "rms_MPa": float(np.std(stress_mpa))}


def synthesise_stress(
    n: int = 150_000,
    dt: float = 0.2,
    Hs: float = 5.0,
    Tp: float = 8.0,
    Hs_swell: float = 2.0,
    Tp_swell: float = 12.0,
    sigma_rms_MPa: float = 10.0,
    seed: int = 7,
) -> np.ndarray:
    """Synthesise a bimodal (wind sea + swell) Gaussian stress record [MPa].

    Sum of harmonics sampled from two JONSWAP spectra whose variances are scaled
    so that the *process* rms matches the target stress level.  Used as the
    time-domain reference for the spectral (narrow-band) damage formula.
    """
    rng = np.random.default_rng(seed)
    omega = np.linspace(0.15, 2.0, 120)
    S = _jonswap(omega, Hs, Tp) + _jonswap(omega, Hs_swell, Tp_swell)
    dw = float(omega[1] - omega[0])
    amp = np.sqrt(2.0 * S * dw)
    phase = rng.uniform(0.0, 2.0 * math.pi, size=omega.size)
    t = np.arange(n) * dt
    sig = np.zeros_like(t)
    for a, w, ph in zip(amp, omega, phase):
        sig += a * np.cos(w * t + ph)
    sig *= sigma_rms_MPa / max(float(np.std(sig)), 1e-9)
    return sig


def _jonswap(omega: np.ndarray, Hs: float, Tp: float, gamma: float = 3.3) -> np.ndarray:
    wp = 2.0 * math.pi / max(Tp, 0.5)
    sigma = np.where(omega <= wp, 0.07, 0.09)
    r = np.exp(-((omega - wp) ** 2) / (2.0 * sigma ** 2 * wp ** 2))
    S = omega ** -5 * np.exp(-1.25 * (omega / wp) ** -4) * gamma ** r
    m0 = float(np.trapezoid(S, omega))
    return S * (Hs ** 2 / 16.0) / m0 if m0 > 0 else np.zeros_like(omega)


def record_spectral_stats(stress_mpa: np.ndarray, dt: float) -> Dict[str, float]:
    """Spectral moments of a stress record: m0, m2, m4, nu0, bandwidth eps."""
    x = np.asarray(stress_mpa, dtype=float)
    x = x - x.mean()
    f = np.fft.rfftfreq(len(x), dt)
    spec_f = 2.0 * np.abs(np.fft.rfft(x)) ** 2 * dt / len(x)   # one-sided PSD [MPa^2 s]
    w = 2.0 * math.pi * f
    m0 = float(np.trapezoid(spec_f, f))
    m2 = float(np.trapezoid(spec_f * w ** 2, f))
    m4 = float(np.trapezoid(spec_f * w ** 4, f))
    nu0 = math.sqrt(m2 / m0) / (2.0 * math.pi) if m0 > 0 else 0.0
    eps = math.sqrt(max(1.0 - m2 ** 2 / (m0 * m4), 0.0)) if m0 > 0 and m4 > 0 else 0.0
    return {"m0": m0, "m2": m2, "m4": m4, "sigma_rms_MPa": math.sqrt(max(m0, 0.0)),
            "nu0_Hz": nu0, "bandwidth_eps": eps, "Tz_s": 1.0 / nu0 if nu0 > 0 else float("inf")}


def damage_from_stress_rms(sigma_rms_mpa: float, Tz_s: float = 5.7) -> float:
    """Damage accumulated per day at a measured stress level (sensor model)."""
    mom = rayleigh_bilinear_moment(sigma_rms_mpa)
    nu0 = 1.0 / max(float(Tz_s), 0.5)
    return float(nu0 * 86400.0 * (mom["inv_life_m3"] + mom["inv_life_m5"]))


def stress_from_damage(dmg_per_day: float, Tz_s: float = 5.7) -> float:
    """Invert :func:`damage_from_stress_rms` (bisection) - used for synthetic sensors."""
    target = max(float(dmg_per_day), 1e-12)
    lo, hi = 1e-3, 400.0
    for _ in range(120):
        mid = 0.5 * (lo + hi)
        if damage_from_stress_rms(mid, Tz_s) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def spectral_vs_rainflow(
    n: int = 150_000,
    dt: float = 0.2,
    Hs: float = 5.0,
    Tp: float = 8.0,
    sigma_rms_MPa: float = 10.0,
    seed: int = 7,
) -> Dict[str, float]:
    """Validate the spectral damage formula against rain-flow counting.

    The spectral formula is evaluated with the record's own ``nu0`` and
    bandwidth so that only the statistical (narrow-band) closure is tested.
    """
    rec = synthesise_stress(n=n, dt=dt, Hs=Hs, Tp=Tp, sigma_rms_MPa=sigma_rms_MPa, seed=seed)
    st = record_spectral_stats(rec, dt)
    mom = rayleigh_bilinear_moment(st["sigma_rms_MPa"])
    d_nb = st["nu0_Hz"] * n * dt * (mom["inv_life_m3"] + mom["inv_life_m5"])
    lam = wirsching_light_factor(SN_M, st["bandwidth_eps"])
    rf = rainflow_damage(rec)
    return {
        "damage_rainflow": rf["damage"],
        "damage_spectral_narrowband": d_nb,
        "damage_spectral_wl": d_nb * lam,
        "ratio_narrowband": d_nb / rf["damage"] if rf["damage"] > 0 else float("nan"),
        "ratio_wl": d_nb * lam / rf["damage"] if rf["damage"] > 0 else float("nan"),
        "rel_error_wl": abs(d_nb * lam - rf["damage"]) / rf["damage"] if rf["damage"] > 0 else float("nan"),
        "n_cycles_rainflow": rf["n_cycles"],
        "sigma_rms_MPa": st["sigma_rms_MPa"],
        "nu0_Hz": st["nu0_Hz"],
        "bandwidth_eps": st["bandwidth_eps"],
        "wideband_factor": lam,
        "S_knee_MPa": float(S_KNEE),
    }
