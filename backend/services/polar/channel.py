"""Brash-ice channel resistance (icebreaker escort channel) and its reference.

When a ship follows an icebreaker it sails in a channel filled with brash ice of
concentration ``c`` and thickness ``h_b``.  The channel resistance is modelled as
the open-water resistance plus a mixture-buoyancy / friction term:

    R_brash = rho_b g h_b (B T Cb) (mu_b + sin beta) * (c / c_max)^n
              * (1 + C_VEL v / sqrt(g h_b))
    rho_b   = c rho_i + (1 - c) rho_w        (brash-mixture density)

The reference formula is the classical Riska/VTT-type channel form, in which the
brash resistance scales with ``rho_i g h_b^2 B`` and a power law of the
concentration:

    R_channel,ref = R_ow + K rho_i g h_b^2 B (c / c_max)^n f_v(v)

``K`` is the single calibration constant of the model (the campaign node asks for
"the Riska channel formula, calibrated").  It is fitted on the **anchor
concentration c = 0.40 only**; the other two concentrations (0.20, 0.70) are
held out, so criterion (b) is a genuine hold-out check and the residual error is
the physical asymmetry of the mixture density rho_b(c) which the reference
formula (constant rho_i) does not contain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .constants import G, RHO_ICE, RHO_WATER
from .resistance import C_VEL, holtrop_open_water_resistance
from .ships import ShipGeometry

C_MAX_PACKING = 0.70     # maximum brash-ice packing fraction [-]
C_PACKING_EXP = 1.50     # concentration exponent n [-]
MU_BRASH = 0.15          # brash-ice / hull friction coefficient [-]

#: Calibration anchor and hold-out concentrations for criterion (b).
ANCHOR_CONCENTRATION = 0.40
HOLDOUT_CONCENTRATIONS: Tuple[float, ...] = (0.20, 0.70)


def brash_density(c: float) -> float:
    """Density of the brash-ice/water mixture [kg/m3]."""
    c = min(max(c, 0.0), 1.0)
    return c * RHO_ICE + (1.0 - c) * RHO_WATER


def _dynamic_factor(v_ms: float, h_b_m: float) -> float:
    if h_b_m <= 0.0:
        return 1.0
    return 1.0 + C_VEL * v_ms / math.sqrt(G * h_b_m)


def channel_resistance(
    ship: ShipGeometry,
    v_ms: float,
    h_b_m: float,
    concentration: float,
    include_open_water: bool = True,
) -> Dict[str, float]:
    """Production brash-channel resistance [N]."""
    if v_ms <= 0.0:
        raise ValueError("v_ms must be positive")
    if h_b_m < 0.0:
        raise ValueError("h_b_m must be non-negative")
    if not (0.0 <= concentration <= 1.0):
        raise ValueError("concentration must be inside [0, 1]")

    ow = holtrop_open_water_resistance(ship, v_ms)["total"] if include_open_water else 0.0
    if h_b_m == 0.0 or concentration == 0.0:
        return {"total_n": ow, "open_water_n": ow, "brash_n": 0.0}

    beta = math.atan(2.0 * ship.draft_m / ship.length_m)
    packing = (concentration / C_MAX_PACKING) ** C_PACKING_EXP
    r_brash = (
        brash_density(concentration)
        * G
        * h_b_m
        * ship.beam_m
        * ship.draft_m
        * ship.cb
        * (MU_BRASH + math.sin(beta))
        * packing
        * _dynamic_factor(v_ms, h_b_m)
    )
    return {"total_n": ow + r_brash, "open_water_n": ow, "brash_n": r_brash}


def channel_resistance_reference(
    ship: ShipGeometry,
    v_ms: float,
    h_b_m: float,
    concentration: float,
    k_cal: float = 1.0,
    include_open_water: bool = True,
) -> Dict[str, float]:
    """Riska/VTT-type brash-channel reference resistance [N] (see module doc)."""
    if v_ms <= 0.0:
        raise ValueError("v_ms must be positive")
    ow = holtrop_open_water_resistance(ship, v_ms)["total"] if include_open_water else 0.0
    if h_b_m == 0.0 or concentration == 0.0:
        return {"total_n": ow, "open_water_n": ow, "brash_n": 0.0}

    packing = (concentration / C_MAX_PACKING) ** C_PACKING_EXP
    r_brash = (
        k_cal
        * RHO_ICE
        * G
        * h_b_m ** 2
        * ship.beam_m
        * packing
        * _dynamic_factor(v_ms, h_b_m)
    )
    return {"total_n": ow + r_brash, "open_water_n": ow, "brash_n": r_brash}


def calibrate_k(
    ship: ShipGeometry,
    v_ms: float,
    h_b_m: float,
    concentration: float = ANCHOR_CONCENTRATION,
) -> float:
    """Fit the single reference constant K on the anchor concentration."""
    prod = channel_resistance(ship, v_ms, h_b_m, concentration, include_open_water=False)
    if prod["brash_n"] <= 0.0:
        raise ValueError("anchor point gives zero brash resistance; check inputs")
    ref_unit = channel_resistance_reference(
        ship, v_ms, h_b_m, concentration, k_cal=1.0, include_open_water=False
    )
    if ref_unit["brash_n"] <= 0.0:
        raise ValueError("reference formula degenerate at anchor point")
    return prod["brash_n"] / ref_unit["brash_n"]


def verify_channel_concentrations(
    ship: ShipGeometry,
    v_kn: float = 6.0,
    h_b_m: float = 0.80,
) -> Dict[str, Any]:
    """Criterion (b): production channel resistance vs calibrated Riska reference.

    ``h_b_m`` is a typical brash-layer thickness of a repeatedly transited
    channel (documented: brash layer thicker than the original level ice).
    """
    from .constants import KNOT

    v_ms = v_kn * KNOT
    k_cal = calibrate_k(ship, v_ms, h_b_m, ANCHOR_CONCENTRATION)

    rows: List[Dict[str, Any]] = []
    for c in (ANCHOR_CONCENTRATION, *HOLDOUT_CONCENTRATIONS):
        prod = channel_resistance(ship, v_ms, h_b_m, c)
        ref = channel_resistance_reference(ship, v_ms, h_b_m, c, k_cal=k_cal)
        rel = (prod["total_n"] - ref["total_n"]) / ref["total_n"]
        rel_brash = (
            (prod["brash_n"] - ref["brash_n"]) / ref["brash_n"] if ref["brash_n"] > 0 else 0.0
        )
        rows.append(
            {
                "concentration": c,
                "role": "anchor" if abs(c - ANCHOR_CONCENTRATION) < 1e-12 else "holdout",
                "h_b_m": h_b_m,
                "speed_kn": v_kn,
                "model_total_n": round(prod["total_n"], 1),
                "reference_total_n": round(ref["total_n"], 1),
                "model_brash_n": round(prod["brash_n"], 1),
                "reference_brash_n": round(ref["brash_n"], 1),
                "rel_error_total_pct": round(100.0 * rel, 4),
                "rel_error_brash_pct": round(100.0 * rel_brash, 4),
            }
        )

    max_err = max(abs(r["rel_error_total_pct"]) for r in rows)
    return {
        "criterion": "b",
        "description": "brash-channel resistance vs calibrated Riska-type reference, "
        "3 concentrations (1 anchor + 2 hold-out)",
        "ship": ship.key,
        "calibration": {"anchor_concentration": ANCHOR_CONCENTRATION, "k_cal": round(k_cal, 6)},
        "rows": rows,
        "max_abs_rel_error_pct": round(max_err, 4),
        "threshold_pct": 10.0,
        "pass": max_err < 10.0,
    }