#!/usr/bin/env python3
"""
reference_impl — independent, literature-faithful reference implementations.

Purpose
-------
The verifier question bank (``tools/verifier/question_bank/questions.db``) holds
the *ground truth* against which every candidate implementation is judged.  If a
stored reference value is itself wrong, every downstream pass/fail verdict is
worthless.  This module provides reference formulas written **directly from the
cited primary sources**, independently of both the question generator
(``generate_questions.py``) and the pilot candidate
(``pilots/resistance_calculator.py``), so the bank can be audited against an
outside authority rather than against itself.

Formulas implemented
--------------------
1. Blasius (1908) laminar flat-plate friction
       Cf = 1.328 / sqrt(Re_L)
   Source: Schlichting, *Boundary-Layer Theory*, ch. 7 (exact similarity solution).

2. Prandtl-Schlichting turbulent flat-plate friction (turbulent portion only)
       Cf = 0.455 / (log10 Re_L)^2.58
   Source: Schlichting, *Boundary-Layer Theory*, 8th ed., Eq. (20.18).

3. ITTC-1957 model-ship correlation line
       Cf = 0.075 / (log10 Re - 2)^2
   Source: ITTC 1957 Performance Prediction Method (Proc. 8th ITTC).

4. Holtrop & Mennen (1982) form factor
       1 + k = 0.93 + 0.487118 * c14 * (B/L)^1.06806 * (T/L)^0.46106
                     * (L/L_R)^0.121563 * (L^3/nabla)^0.36486
                     * (1 - Cp)^(-0.604247)
   with
       c14 = 1 + 0.011 * C_stern
       L_R = L * (1 - Cp + 0.06 * Cp * LCB / (4*Cp - 1))   [LCB as fraction of L]
       nabla = Cb * L * B * T
       Cp = Cb / Cm
   Source: Holtrop, J. & Mennen, G.G.J., "An Approximate Power Prediction
   Method", *International Shipbuilding Progress* 29 (1982) 166-170, Eq. (12);
   reproduced in Molland, Turnock & Hudson, *Ship Resistance and Propulsion*,
   CUP 2011, Eq. (5.24).

   The 1982 paper works in terms of Cb, L, B, T, Cp and LCB.  A question that
   supplies only (Cb, L, B, T) under-determines Cp and LCB, so the implementation
   below takes explicit defaults **and** exposes a sensitivity band
   (``holtrop_mennen_form_factor_band``) so that an audit conclusion never rests
   on one arbitrary choice of the missing inputs.

Applicability limits (Holtrop & Mennen 1982; Molland et al. 2011):
    0.55 <= Cb <= 0.85,  3.90 <= L/B <= 14.90,  2.10 <= B/T <= 4.00,
    Fn <= 0.45 for the form-factor relation.

The functions are pure, dependency-free (stdlib only) and deterministic.
"""

from __future__ import annotations

import math
from typing import Dict, Tuple

__all__ = [
    "blasius_laminar_cf",
    "prandtl_schlichting_cf",
    "ittc_1957_cf",
    "holtrop_mennen_form_factor",
    "holtrop_mennen_form_factor_band",
    "HOLTROP_APPLICABILITY",
]

HOLTROP_APPLICABILITY = {
    "Cb": (0.55, 0.85),
    "L_over_B": (3.90, 14.90),
    "B_over_T": (2.10, 4.00),
}


# --------------------------------------------------------------------------- #
# Flat plate
# --------------------------------------------------------------------------- #
def blasius_laminar_cf(re_l: float) -> float:
    """Blasius (1908) laminar flat plate: Cf = 1.328 / sqrt(Re_L)."""
    if re_l <= 0:
        raise ValueError(f"Re_L must be positive, got {re_l}")
    return 1.328 / math.sqrt(re_l)


def prandtl_schlichting_cf(re_l: float) -> float:
    """Prandtl-Schlichting turbulent flat plate: Cf = 0.455 / (log10 Re_L)^2.58."""
    if re_l <= 0:
        raise ValueError(f"Re_L must be positive, got {re_l}")
    log_re = math.log10(re_l)
    if log_re == 0:
        raise ValueError("Re_L must differ from 1")
    return 0.455 / (log_re ** 2.58)


def ittc_1957_cf(re_l: float) -> float:
    """ITTC-1957 correlation line: Cf = 0.075 / (log10 Re - 2)^2."""
    if re_l <= 0:
        raise ValueError(f"Re_L must be positive, got {re_l}")
    log_re = math.log10(re_l)
    denom = log_re - 2.0
    if denom == 0:
        raise ValueError("Re_L must differ from 100")
    return 0.075 / (denom ** 2)


# --------------------------------------------------------------------------- #
# Holtrop & Mennen (1982) form factor
# --------------------------------------------------------------------------- #
def holtrop_mennen_form_factor(
    Cb: float,
    L: float,
    B: float,
    T: float,
    Cm: float = 0.98,
    LCB_pct: float = 0.0,
    Cstern: float = 0.0,
) -> Dict[str, float]:
    """Holtrop & Mennen (1982) form factor (1+k), literature-faithful.

    Parameters
    ----------
    Cb, L, B, T : block coefficient [-], length, beam, draught [m]
    Cm : midship section coefficient [-] (default 0.98, typical for the
         Series-60 / bulk-carrier / tanker hulls in the bank)
    LCB_pct : longitudinal centre of buoyancy in percent of L, positive forward
              (default 0.0)
    Cstern : stern shape coefficient (0 = normal section, Pram with gondola,
             -10 = V-shaped sections, +10 = open/normal section per 1982 paper)

    Returns
    -------
    dict with ``1+k`` and every intermediate quantity, so an audit can show its
    working instead of just a verdict.
    """
    if L <= 0 or B <= 0 or T <= 0:
        raise ValueError("L, B, T must be positive")
    if not 0.0 < Cb < 1.0:
        raise ValueError(f"Cb out of range: {Cb}")
    if not 0.0 < Cm <= 1.0:
        raise ValueError(f"Cm out of range: {Cm}")
    Cp = Cb / Cm
    if not 0.0 < Cp < 1.0:
        raise ValueError(f"implied Cp out of range: {Cp}")

    nabla = Cb * L * B * T                      # displacement volume [m^3]
    Cstern_term = 1.0 + 0.011 * Cstern          # c14
    LCB = LCB_pct / 100.0
    LR = L * (1.0 - Cp + 0.06 * Cp * LCB / (4.0 * Cp - 1.0))   # running length

    if LR <= 0 or nabla <= 0:
        raise ValueError("degenerate Holtrop geometry")

    one_plus_k = (
        0.93
        + 0.487118
        * Cstern_term
        * (B / L) ** 1.06806
        * (T / L) ** 0.46106
        * (L / LR) ** 0.121563
        * (L ** 3 / nabla) ** 0.36486
        * (1.0 - Cp) ** (-0.604247)
    )

    return {
        "1+k": one_plus_k,
        "Cp": Cp,
        "nabla": nabla,
        "LR": LR,
        "c14": Cstern_term,
        "L3_over_nabla": L ** 3 / nabla,
        "L_over_LR": L / LR,
    }


def holtrop_mennen_form_factor_band(
    Cb: float,
    L: float,
    B: float,
    T: float,
    Cm_range: Tuple[float, float] = (0.95, 0.99),
    LCB_range_pct: Tuple[float, float] = (-2.0, 2.0),
    Cstern_range: Tuple[float, float] = (-10.0, 10.0),
    n: int = 9,
) -> Dict[str, float]:
    """Sensitivity band of (1+k) over the inputs a 4-parameter question omits.

    Returns min / max / nominal.  If a bank value lies outside ``[min, max]``
    the verdict is robust: no admissible choice of the missing inputs can
    reproduce it.
    """
    values = []
    for i in range(n):
        f = i / (n - 1) if n > 1 else 0.0
        Cm = Cm_range[0] + f * (Cm_range[1] - Cm_range[0])
        for j in range(n):
            g = j / (n - 1) if n > 1 else 0.0
            lcb = LCB_range_pct[0] + g * (LCB_range_pct[1] - LCB_range_pct[0])
            for k in range(n):
                h = k / (n - 1) if n > 1 else 0.0
                cst = Cstern_range[0] + h * (Cstern_range[1] - Cstern_range[0])
                values.append(
                    holtrop_mennen_form_factor(Cb, L, B, T, Cm=Cm, LCB_pct=lcb, Cstern=cst)["1+k"]
                )
    nominal = holtrop_mennen_form_factor(Cb, L, B, T)["1+k"]
    return {"min": min(values), "max": max(values), "nominal": nominal}


def applicability_violations(Cb: float, L: float, B: float, T: float) -> list:
    """Report violations of the published Holtrop & Mennen (1982) applicability limits."""
    out = []
    checks = {
        "Cb": Cb,
        "L_over_B": L / B,
        "B_over_T": B / T,
    }
    for key, value in checks.items():
        lo, hi = HOLTROP_APPLICABILITY[key]
        if not (lo <= value <= hi):
            out.append(f"{key}={value:.3f} outside [{lo}, {hi}]")
    return out