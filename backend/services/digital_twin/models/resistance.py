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

try:  # pragma: no cover - package root variants
    from services.holtrop_mennen import (
        c16_coefficient as _c16_coefficient,
        holtrop_resistance as _mennen_holtrop,
    )
except ImportError:  # pragma: no cover
    from ...holtrop_mennen import (  # type: ignore[no-redef]
        c16_coefficient as _c16_coefficient,
        holtrop_resistance as _mennen_holtrop,
    )
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
    """Total calm-water resistance and its components [N].

    Single source of truth: this wrapper delegates to the paper-faithful
    Holtrop-Mennen (1982) + Holtrop (1984) implementation in
    :mod:`services.holtrop_mennen` (equations audited against the publication;
    see ``docs/verification/resistance_benchmark_audit.md`` and
    ``docs/verification/holtrop_crosscheck_m1648.md``).

    Before mission m1648 the local copy of the regression carried three defects
    that cancelled in ``RT_N`` only by accident while leaving every component
    wrong (KCS at Fn=0.21: ``RW`` +4056 %, S60 Cb=0.6: ``RW`` +22001 %):

    * the published ``c7`` piecewise branches for ``0.11 <= B/L <= 0.25`` and
      ``B/L > 0.25`` were swapped, so ``c1 ~ c7^3.78613`` was off by up to 24x;
    * ``m2 = c15 Cp^2 exp(-0.1 Fn^-2)`` was replaced by the unrelated
      ``m4 = c15 * 0.4 exp(-0.034 Fn^-3.29)`` term;
    * the wetted surface used ``sqrt(Cb)`` instead of the published
      ``sqrt(Cm)``.

    Retained simplifications (documented, not paper-exact because the caller
    supplies no hull form beyond ``L, B, T, Cb``): ``Cm = 1 - 0.2 (1 - Cb)``,
    ``Cwp = (1 + 2 Cb) / 3``, ``lcb = 0`` (midship), no bulb (``ABT = 0``),
    no transom (``AT = 0``), no appendages, ``cstern = 0``. These parameters
    are passed explicitly to the reference implementation so the two entry
    points agree to machine precision (verified by
    ``tests/test_digital_twin_holtrop_consistency.py``).

    Interface note: ``RF_N`` is the bare ITTC-1957 friction of the wetted
    surface and the form factor is returned separately in ``form_factor``
    (``services.holtrop_mennen`` folds ``(1 + k)`` into its ``rf_n``).
    Valid for ``Fn >= 1e-3``: the wave-resistance regression contains
    ``Fn^-2`` and is not extrapolated to zero speed.
    """
    length, beam, draft, cb = float(L), float(B), float(T), float(Cb)
    speed = float(max(V, 1e-6))
    cm = 1.0 - 0.2 * (1.0 - cb)
    cwp = (1.0 + 2.0 * cb) / 3.0
    ref = _mennen_holtrop(
        length=length, beam=beam, draft=draft, cb=cb, speed_ms=speed,
        cm=cm, cwp=cwp, lcb_frac=0.0, rho=float(rho), nu=float(nu),
    )
    cp = ref.cp
    lam = (1.446 * cp - 0.03 * length / beam) if length / beam < 12.0 \
        else (1.446 * cp - 0.36)
    return {
        "RT_N": ref.rt_n,
        "RF_N": ref.rf_n / ref.form_factor,
        "RW_N": ref.rw_n,
        "RA_N": ref.ra_n,
        "form_factor": ref.form_factor,
        "Cf": ref.cf,
        "Rn": ref.re,
        "Fn": ref.fn,
        "wetted_surface_m2": ref.wetted_surface_m2,
        "Cwp": cwp,
        "Cm": cm,
        "Cp": cp,
        "c1": ref.c1,
        "c16": _c16_coefficient(cp),
        "lambda": lam,
        "warnings": list(ref.warnings),
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