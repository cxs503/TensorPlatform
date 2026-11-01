#!/usr/bin/env python3
"""
Independent literature reference formulas (verifier side).

Purpose
-------
The verifier pilot (mission 537) produced reference values with the *same*
formula string that the candidate implementation uses, so every deviation came
out bit-identical (0.0) and the verdict was structurally guaranteed to be
"pass". That is circular verification: it proves the code is self-consistent,
not that it is right.

This module provides an *independent* reference path:

  1. Closed-form formulas re-derived from the published sources (constants
     quoted from the papers / ITTC recommended procedures), written here
     separately from the candidate code so that a typo in the candidate cannot
     propagate into the reference.
  2. ``PUBLISHED_ANCHORS`` — values *tabulated* in the literature (not computed
     here) so the reference itself can be audited. Any reference that cannot be
     anchored to a published number is a suspicion, not a proof.
  3. A full implementation of the Holtrop & Mennen (1982) form factor, which the
     pilot replaced by an ad-hoc "simplified, scaled for pilot" expression.

All functions are pure and cheap (no state, no I/O).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provenance:
    """Where a reference value / formula comes from."""

    source: str
    reference: str
    formula: str
    applicability: str = ""
    notes: str = ""


# --------------------------------------------------------------------------- #
# 1. Friction lines
# --------------------------------------------------------------------------- #
# ITTC-1957 model-ship correlation line (7th ITTC, 1957; reissued in ITTC
# Recommended Procedure 7.5-02-02-01 "Resistance Test"):
#     Cf = 0.075 / (log10 Re - 2)^2
ITTC57_PROVENANCE = Provenance(
    source="ITTC-1957 model-ship correlation line",
    reference="Proceedings of the 7th ITTC (1957); ITTC Rec. Proc. 7.5-02-02-01",
    formula="Cf = 0.075 / (log10(Re) - 2)^2",
    applicability="Model-ship correlation line; Re = V*L/nu, no roughness allowance",
    notes="Includes the form-effect proxy; not a smooth-plate friction line.",
)

# Blasius (1908) laminar flat plate, mean skin friction coefficient:
#     Cf = 1.328 / sqrt(Re_L)     (valid for Re_L < 5e5, i.e. laminar up to transition)
BLASIUS_PROVENANCE = Provenance(
    source="Blasius (1908) laminar flat plate",
    reference="Blasius, H. (1908) Grenzschichten in Fluessigkeiten mit kleiner Reibung",
    formula="Cf = 1.328 / sqrt(Re_L)",
    applicability="Laminar, smooth flat plate, zero pressure gradient, Re_L < 5e5",
)

# Prandtl-Schlichting turbulent flat plate, mean skin friction coefficient
# (as used for the turbulent flat plate in Schlichting, Boundary-Layer Theory):
#     Cf = 0.455 / (log10 Re_L)^2.58
PRANDTL_SCHLICHTING_PROVENANCE = Provenance(
    source="Prandtl-Schlichting turbulent flat plate",
    reference="Schlichting, H. Boundary-Layer Theory (7th ed.), ch. 21",
    formula="Cf = 0.455 / (log10(Re_L))^2.58",
    applicability="Turbulent, smooth flat plate, 1e6 < Re_L < 1e9",
    notes="Some texts subtract a Re-dependent term (e.g. -1700/Re); the plain "
          "form is the one the pilot candidate claims.",
)

# Schoenherr / ATTC-1947 line, implicit:  0.242 / sqrt(Cf) = log10(Re * Cf)
SCHOENHERR_PROVENANCE = Provenance(
    source="Schoenherr (ATTC 1947) turbulent flat plate",
    reference="Schoenherr, K.E. (1932); ATTC 1947 report",
    formula="0.242 / sqrt(Cf) = log10(Re * Cf)  (solved numerically)",
    applicability="Turbulent, smooth, 1e5 < Re < 1e10",
    notes="Used here only as an *independent* cross-formula check of the "
          "Prandtl-Schlichting level (not as an acceptance criterion).",
)


def ittc1957_cf(Re: float) -> float:
    """ITTC-1957 model-ship correlation line: Cf = 0.075/(log10 Re - 2)^2."""
    if Re <= 100.0:
        raise ValueError(f"Re too low for ITTC-1957 line: {Re}")
    denom = (math.log10(Re) - 2.0) ** 2
    if denom == 0.0:
        raise ValueError(f"Singular denominator at Re={Re}")
    return 0.075 / denom


def blasius_cf(Re_L: float) -> float:
    """Blasius laminar flat plate: Cf = 1.328/sqrt(Re_L)."""
    if Re_L <= 0.0:
        raise ValueError(f"Re_L must be positive: {Re_L}")
    return 1.328 / math.sqrt(Re_L)


def prandtl_schlichting_cf(Re_L: float) -> float:
    """Prandtl-Schlichting turbulent flat plate: Cf = 0.455/(log10 Re_L)^2.58."""
    if Re_L <= 1.0:
        raise ValueError(f"Re_L must be > 1: {Re_L}")
    return 0.455 / (math.log10(Re_L) ** 2.58)


def schoenherr_cf(Re_L: float, tol: float = 1e-14, itmax: int = 200) -> float:
    """Solve Schoenherr's implicit flat-plate line 0.242/sqrt(Cf) = log10(Re*Cf).

    Solved by bisection on the monotone residual; used only for cross-checking
    the Prandtl-Schlichting level within ~1-2 %.
    """
    if Re_L <= 0.0:
        raise ValueError(f"Re_L must be positive: {Re_L}")

    def residual(cf: float) -> float:
        return 0.242 / math.sqrt(cf) - math.log10(Re_L * cf)

    lo, hi = 1e-6, 0.1
    f_lo, f_hi = residual(lo), residual(hi)
    if f_lo * f_hi > 0.0:
        raise ValueError(f"No bracket for Schoenherr Cf at Re={Re_L}")
    for _ in range(itmax):
        mid = 0.5 * (lo + hi)
        f_mid = residual(mid)
        if f_mid * f_lo <= 0.0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------- #
# 2. Holtrop & Mennen (1982) form factor  -- full published expression
# --------------------------------------------------------------------------- #
HOLTROP1982_PROVENANCE = Provenance(
    source="Holtrop & Mennen (1982) form factor",
    reference="Holtrop, J. & Mennen, G.G.J. (1982) 'An approximate power "
              "prediction method', International Shipbuilding Progress 29(335)",
    formula=(
        "1+k = 0.93 + 0.487118*c14*(B/L)^1.06806*(T/L)^0.46106"
        "*(L/LR)^0.121563*(L^3/nabla)^0.36486*(1-Cp)^(-0.604247), "
        "LR = L*(1 - Cp + 0.06*Cp*LCB/(4*Cp - 1)), c14 = 1 + 0.011*Cstern"
    ),
    applicability="Displacement hulls, 0.55 < Cp < 0.85, L/B in 3.9-14.9; "
                 "LCB in % of L, positive forward; nabla = moulded volume",
)


def holtrop1982_lr(L: float, Cp: float, LCB_pct: float) -> float:
    """Holtrop & Mennen (1982) length of run LR = L(1 - Cp + 0.06 Cp LCB/(4Cp-1))."""
    denom = 4.0 * Cp - 1.0
    if abs(denom) < 1e-9:
        raise ValueError(f"Degenerate Cp={Cp} (4Cp-1 = 0)")
    return L * (1.0 - Cp + 0.06 * Cp * LCB_pct / denom)


def holtrop1982_form_factor(
    L: float,
    B: float,
    T: float,
    Cb: float,
    Cm: float = 0.98,
    LCB_pct: float = -1.5,
    Cstern: float = 0.0,
) -> dict:
    """Full Holtrop & Mennen (1982) form factor 1+k (and k).

    Parameters
    ----------
    L, B, T : moulded length / breadth / draught [m]
    Cb      : block coefficient (moulded)
    Cm      : midship section coefficient (default 0.98, typical)
    LCB_pct : longitudinal centre of buoyancy in % of L, positive forward
    Cstern  : stern shape coefficient (0 = normal sections, -10..+10)

    Returns
    -------
    dict with Cp, nabla, LR, c14 and the dimensionless '1+k' / 'k'.
    """
    if not (0.30 < Cb < 0.95):
        raise ValueError(f"Cb out of Holtrop applicability range: {Cb}")
    if min(L, B, T) <= 0.0:
        raise ValueError("Principal dimensions must be positive")
    if not (0.5 < Cm <= 1.0):
        raise ValueError(f"Cm out of range: {Cm}")

    Cp = Cb / Cm
    if not (0.55 < Cp < 0.85):
        raise ValueError(f"Cp={Cp:.4f} outside Holtrop applicability 0.55-0.85")

    nabla = Cb * L * B * T
    LR = holtrop1982_lr(L, Cp, LCB_pct)
    c14 = 1.0 + 0.011 * Cstern

    one_plus_k = (
        0.93
        + 0.487118
        * c14
        * (B / L) ** 1.06806
        * (T / L) ** 0.46106
        * (L / LR) ** 0.121563
        * (L ** 3 / nabla) ** 0.36486
        * (1.0 - Cp) ** (-0.604247)
    )
    return {
        "Cp": Cp,
        "nabla": nabla,
        "LR": LR,
        "c14": c14,
        "1+k": one_plus_k,
        "k": one_plus_k - 1.0,
    }


def holtrop1982_form_factor_sensitivity(
    L: float,
    B: float,
    T: float,
    Cb: float,
    Cm_range: tuple[float, ...] = (0.97, 0.98, 0.995),
    lcb_range: tuple[float, ...] = (-3.0, -2.0, -1.5, -1.0, 0.0),
) -> dict:
    """Sensitivity envelope of 1+k over the unknown Cm / LCB of a bare hull.

    The question bank stores only (Cb, L, B, T); Cm and LCB are not recorded.
    Rather than assume one pair, sweep a physically plausible set and report the
    envelope, so an acceptance/failure statement stays valid under uncertainty.
    """
    values = []
    for cm in Cm_range:
        for lcb in lcb_range:
            try:
                r = holtrop1982_form_factor(L, B, T, Cb, Cm=cm, LCB_pct=lcb)
            except ValueError:
                continue
            values.append(
                {"Cm": cm, "LCB_pct": lcb, "1+k": r["1+k"], "k": r["k"]}
            )
    if not values:
        raise ValueError("Empty sensitivity envelope -- check inputs")
    ones = [v["1+k"] for v in values]
    ks = [v["k"] for v in values]
    return {
        "samples": values,
        "one_plus_k_min": min(ones),
        "one_plus_k_max": max(ones),
        "k_min": min(ks),
        "k_max": max(ks),
    }


# --------------------------------------------------------------------------- #
# 3. Published anchor values (tabulated in the literature, NOT computed here)
# --------------------------------------------------------------------------- #
# The anchors let us audit the reference itself: if our ITTC-1957 implementation
# stopped matching the published table the reference would be broken, not the
# candidate. ITTC-57 values are quoted to the 4 significant figures found in the
# standard resistance-test procedure tables / textbook worked examples.
PUBLISHED_ANCHORS: dict[str, list[dict]] = {
    "ittc_1957": [
        {"Re": 1e6, "Cf": 4.688e-3, "decimals": 4},
        {"Re": 1e7, "Cf": 3.000e-3, "decimals": 4},
        {"Re": 1e8, "Cf": 2.083e-3, "decimals": 4},
        {"Re": 1e9, "Cf": 1.531e-3, "decimals": 4},
    ],
    "blasius": [
        {"Re_L": 1e5, "Cf": 4.200e-3, "decimals": 4},
        {"Re_L": 1e6, "Cf": 1.328e-3, "decimals": 4},
    ],
    # Turbulent flat plate, smooth: textbook values of the Prandtl-Schlichting
    # line (Schlichting, Boundary-Layer Theory, table of Cf vs Re_L).
    "prandtl_schlichting": [
        {"Re_L": 1e6, "Cf": 4.471e-3, "decimals": 4},
        {"Re_L": 1e7, "Cf": 3.004e-3, "decimals": 4},
        {"Re_L": 1e8, "Cf": 2.128e-3, "decimals": 4},
        {"Re_L": 1e9, "Cf": 1.571e-3, "decimals": 4},
    ],
}


def relative_error(actual: float, expected: float) -> float:
    """|actual - expected| / |expected| (fraction, not percent)."""
    if expected == 0.0:
        raise ValueError("expected == 0 -- relative error undefined")
    return abs(actual - expected) / abs(expected)


if __name__ == "__main__":  # pragma: no cover - manual smoke run
    print("ITTC-57 Cf(1e7) =", ittc1957_cf(1e7))
    print("Blasius Cf(1e5) =", blasius_cf(1e5))
    print("PS      Cf(1e7) =", prandtl_schlichting_cf(1e7))
    print("Schoenherr Cf(1e7) =", schoenherr_cf(1e7))
    print("Holtrop 1+k (Cb=.7) =", holtrop1982_form_factor(150.0, 22.0, 8.5, 0.7))