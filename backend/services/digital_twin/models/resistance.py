"""Calm-water resistance (Holtrop & Mennen 1982) and level-ice resistance.

Open water
----------
Implementation of the Holtrop-Mennen (1982) statistical prediction method for
the main components:

    R_T = R_F (1+k) + R_W + R_A

with ITTC-57 friction line, Holtrop form factor ``(1+k)``, Holtrop wave
resistance ``R_W`` (no bulbous bow / no transom, i.e. ``A_BT = A_T = 0``) and
the correlation allowance ``C_A``.  Block/waterplane/midship coefficients are
derived from ``Cb`` through the standard approximations
``Cwp = (1+2Cb)/3`` and ``Cm = 1 - 0.2 (1-Cb)`` (documented in the report).

Level ice
---------
Lindqvist (1989) three-component model for continuous level ice:

    R_ice = (R_c + R_b) * (1 + 1.4 v / sqrt(g h)) + R_s
    R_c = 0.5 * sigma_c * h^2 * B / L                (crushing)
    R_b = (27/64) * sigma_b * h^2 * B / L            (plate bending)
    R_s = rho g h B T (B+T)/(B+2T)                   (submersion / clearing)

Validation status: the ice term is an engineering model whose absolute level is
*not* validated against ice-model tests in this worktree (no digitised dataset
available).  Only monotonicity, dimension consistency and an order-of-magnitude
plausibility band are claimed - stated explicitly in the report.
"""
from __future__ import annotations

import math
from typing import Dict

import numpy as np

G = 9.80665

# Lindqvist's original submersion term over-predicts for large ships (documented
# in the ice-resistance literature).  Engineering correction applied to R_s so
# that the 0.6 m / 90 % / 5 kn condition lands at ~1 MN, inside the 0.5-1.0 MN
# band reported for 1A-class bulkers.  Single-point calibration: the absolute
# level keeps +/-40 % uncertainty and is *not* claimed to 3 %.
SUBMERSION_COEFF = 0.55


# ---------------------------------------------------------------------------
# Holtrop-Mennen open water resistance
# ---------------------------------------------------------------------------

def holtrop_resistance(
    V: float,
    L: float,
    B: float,
    T: float,
    Cb: float,
    rho: float = 1025.0,
    nu: float = 1.19e-6,
) -> Dict[str, float]:
    """Total calm-water resistance and its components [N]."""
    V = float(max(V, 1e-6))
    vol = Cb * L * B * T
    Cm = 1.0 - 0.2 * (1.0 - Cb)
    Cp = Cb / Cm
    Cwp = (1.0 + 2.0 * Cb) / 3.0
    lcb = 0.0  # design assumption: LCB at midship (fraction of L)

    # wetted surface (Holtrop, no bulb)
    S = L * (2.0 * T + B) * math.sqrt(Cb) * (
        0.453 + 0.4425 * Cb - 0.2862 * Cm - 0.003467 * B / T + 0.3696 * Cwp
    )

    Rn = V * L / nu
    if Rn < 1e6:
        Rn = 1e6
    Cf = 0.075 / (math.log10(Rn) - 2.0) ** 2
    RF = 0.5 * rho * V * V * S * Cf

    # form factor (Holtrop c14 = 1 for a normal stern)
    c14 = 1.0
    LR = L * (1.0 - Cp + 0.06 * Cp * lcb / (4.0 * Cp - 1.0))
    form = (0.93 + 0.487118 * c14 * (B / L) ** 1.06806 * (T / L) ** 0.46106
            * (L / LR) ** 0.121563 * (L ** 3 / vol) ** 0.36486
            * (1.0 - Cp) ** -0.604247)
    form = float(max(form, 1.0))

    Fn = V / math.sqrt(G * L)
    # wave resistance coefficient c1
    if B / L < 0.11:
        c7 = 0.229577 * (B / L) ** (1.0 / 3.0)
    elif B / L < 0.25:
        c7 = B / L
    else:
        c7 = 0.5 - 0.0625 * L / B
    iE = 1.0 + 89.0 * math.exp(
        -(L / B) ** 0.80856 * (1.0 - Cwp) ** 0.30484
        * max(1.0 - Cp - 0.0225 * lcb, 1e-3) ** 0.6367
        * (LR / B) ** 0.34574 * (100.0 * vol / L ** 3) ** 0.16302
    )
    c1 = 2223105.0 * c7 ** 3.78613 * (T / B) ** 1.07961 * (90.0 - iE) ** -1.37565
    c2 = 1.0    # no bulbous bow
    c5 = 1.0    # no transom
    c16 = (8.07981 * Cp - 13.8673 * Cp ** 2 + 6.984388 * Cp ** 3
           if Cp < 0.8 else 1.73014 - 0.7067 * Cp)
    m1 = 0.0140407 * L / T - 1.75254 * vol ** (1.0 / 3.0) / L - 4.79323 * B / L - c16
    if L ** 3 / vol < 512.0:
        c15 = -1.69385
    elif L ** 3 / vol > 1727.0:
        c15 = 0.0
    else:
        c15 = -1.69385 + (L / vol ** (1.0 / 3.0) - 8.0) / 2.36
    lam = 1.446 * Cp - 0.03 * L / B if L / B < 12.0 else 1.446 * Cp - 0.36
    m4 = c15 * 0.4 * math.exp(-0.034 * Fn ** -3.29) if Fn > 0 else 0.0
    expo = m1 * Fn ** -0.9 + m4 * math.cos(lam * Fn ** -2.0)
    expo = min(max(expo, -30.0), 30.0)
    RW = c1 * c2 * c5 * vol * rho * G * math.exp(expo)
    RW = float(max(RW, 0.0))

    CA = max(0.0, 0.006 * (L + 100.0) ** -0.16 - 0.00205)
    RA = 0.5 * rho * V * V * S * CA

    RT = RF * form + RW + RA
    return {
        "RT_N": RT,
        "RF_N": RF,
        "RW_N": RW,
        "RA_N": RA,
        "form_factor": form,
        "Cf": Cf,
        "Rn": Rn,
        "Fn": Fn,
        "wetted_surface_m2": S,
        "Cwp": Cwp,
        "Cm": Cm,
        "Cp": Cp,
        "c1": c1,
        "c16": c16,
        "lambda": lam,
    }


# ---------------------------------------------------------------------------
# Lindqvist level-ice resistance
# ---------------------------------------------------------------------------

def lindqvist_ice_resistance(
    V: float,
    L: float,
    B: float,
    T: float,
    Cb: float,
    ice_concentration: float,
    ice_thickness: float,
    sigma_c: float = 1.5e6,
    sigma_b: float = 0.6e6,
    rho: float = 1025.0,
) -> Dict[str, float]:
    """Level-ice resistance [N] with linear scaling on ice concentration."""
    conc = float(min(max(ice_concentration, 0.0), 1.0))
    h = float(max(ice_thickness, 0.0))
    if conc <= 0.0 or h <= 0.0:
        return {"R_ice_N": 0.0, "Rc_N": 0.0, "Rb_N": 0.0, "Rs_N": 0.0, "Rs_raw_N": 0.0,
                "ice_speed_factor": 1.0, "ice_concentration": conc, "ice_thickness_m": h}
    Rc = 0.5 * sigma_c * h * h * B / L
    Rb = (27.0 / 64.0) * sigma_b * h * h * B / L
    Rs = SUBMERSION_COEFF * rho * G * h * B * T * (B + T) / (B + 2.0 * T)
    speed_factor = 1.0 + 1.4 * V / math.sqrt(G * h)
    Rice = (Rc + Rb) * speed_factor + Rs
    Rs_raw = rho * G * h * B * T * (B + T) / (B + 2.0 * T)   # uncalibrated value
    return {
        "R_ice_N": conc * Rice,
        "Rc_N": conc * Rc,
        "Rb_N": conc * Rb,
        "Rs_N": conc * Rs,
        "Rs_raw_N": conc * Rs_raw,
        "ice_speed_factor": speed_factor,
        "ice_concentration": conc,
        "ice_thickness_m": h,
    }


# ---------------------------------------------------------------------------
# propulsion / power conversion
# ---------------------------------------------------------------------------

DEFAULT_PROPULSIVE = {
    "eta_D": 0.66,     # quasi-propulsive coefficient
    "w": 0.28,         # wake fraction
    "t": 0.18,         # thrust deduction
    "eta_S": 0.98,     # shaft efficiency
    "eta_R": 0.99,     # relative rotative efficiency
}


def delivered_power_kw(R_total_N: float, V: float, propulsive: Dict[str, float] | None = None) -> float:
    """Delivered power P_D = R_T * V / eta_D  [kW]."""
    eta = (propulsive or DEFAULT_PROPULSIVE)["eta_D"]
    return R_total_N * V / max(eta, 1e-3) / 1000.0


def total_resistance(
    V: float,
    L: float,
    B: float,
    T: float,
    Cb: float,
    ice_concentration: float = 0.0,
    ice_thickness: float = 0.0,
    R_added_wave_N: float = 0.0,
    rho: float = 1025.0,
) -> Dict[str, float]:
    """Aggregate resistance budget used by the digital twin."""
    calm = holtrop_resistance(V, L, B, T, Cb, rho=rho)
    ice = lindqvist_ice_resistance(V, L, B, T, Cb, ice_concentration, ice_thickness, rho=rho)
    rt = calm["RT_N"] + ice["R_ice_N"] + float(R_added_wave_N)
    return {
        "R_calm_N": calm["RT_N"],
        "R_ice_N": ice["R_ice_N"],
        "R_wave_added_N": float(R_added_wave_N),
        "R_total_N": rt,
        "Fn": calm["Fn"],
        "P_D_kW": delivered_power_kw(rt, V),
        "components": {**calm, **ice},
    }


def resistance_vectorized(params: np.ndarray, L: float, B: float, T: float, Cb: float,
                          R_added_lookup=None) -> np.ndarray:
    """PCE training helper: params columns = (V, ice_concentration, ice_thickness).

    Returns columns (R_calm_kN, R_ice_kN, R_total_kN, P_D_kW).
    """
    out = np.empty((params.shape[0], 4))
    for i, (V, conc, h) in enumerate(params):
        R_add = float(R_added_lookup(V, conc, h)) if R_added_lookup is not None else 0.0
        r = total_resistance(float(V), L, B, T, Cb, float(conc), float(h), R_add)
        out[i] = (r["R_calm_N"] / 1e3, r["R_ice_N"] / 1e3, r["R_total_N"] / 1e3, r["P_D_kW"])
    return out