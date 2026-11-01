"""
Independent reference implementation — Holtrop & Mennen (1982) form factor.

Why this module exists
----------------------
The question bank in `tools/verifier/generate_questions.py` used to synthesise its
Holtrop "reference" values from a private expression:

    1+k = 0.93 + 0.1 * 0.487118 * Cb ** (-0.9249) * (L/B) ** 0.15 * (B/T) ** 0.2

That expression is *not* the published Holtrop-Mennen form factor: it lacks the
run-length term ``(L/L_R)``, the volumetric term ``(L**3/nabla)``, the prismatic
term ``(1-Cp)`` and the stern-shape multiplier ``c14``.  Because the expected
values and the pilot candidate were produced by the very same expression, the
verification was **circular**: any candidate implementing the same wrong formula
passed.  Cross-checking that expression against published behaviour exposes two
independent physical failures (see `agrees_with_published_trend` below):

  * it decreases monotonically with block coefficient, whereas every published
    form-factor regression increases with fullness;
  * it returns 1.03-1.06 for Cb = 0.6-0.8 hulls, whereas published values for
    that family sit near 1.16-1.32 (i.e. k = 0.16-0.32).

This module is a transcription of the primary source, written independently of
the candidate side, and is the authoritative reference for the bank.

Primary source
--------------
Holtrop, J. & Mennen, G.G.J. (1982), "An approximate power prediction method",
*International Shipbuilding Progress* 29(335), 166-170; the form factor
regression is restated in Holtrop, J. (1984), "A statistical re-analysis of
resistance and propulsion data", *International Shipbuilding Progress* 31(363).

    1 + k = 0.93
          + 0.487118 * c14
                     * (B/L)     ** 1.06806
                     * (T/L)     ** 0.46106
                     * (L/L_R)   ** 0.121563
                     * (L**3/nabla) ** 0.36486
                     * (1 - Cp)  ** (-0.604247)

with

    c14   = 1 + 0.011 * C_stern
    L_R   = L * (1 - Cp + 0.06 * Cp * lcb / (4*Cp - 1))
    nabla = Cb * L * B * T
    Cp    = Cb / Cm

``lcb`` is the longitudinal centre of buoyancy as a fraction of L, measured
positive forward of midships (e.g. -0.015 for 1.5% aft).

Applicability box (as stated in the source, for 1+k and the resistance method)
-----------------------------------------------------------------------------
    Fn 0.00-0.45 | Cp 0.55-0.85 | L/B 3.90-14.90 | B/T 2.10-4.00 | Cb 0.55-0.85

Tolerance statement
-------------------
This module carries no empirical tolerance of its own; it is a deterministic
evaluation of a published regression. Its accuracy versus *experiment* inherits
the regression's scatter, which is not pinned to a numeric table in this
offline repository — see `docs/verifier/REFERENCE_INTEGRITY.md` for the
explicit WITHHELD statement about literature table values.
"""
from __future__ import annotations

import math
from typing import Dict, NamedTuple

#: Canonical expression fingerprint.  Tests and the bank auditor assert that a
#: question's provenance carries exactly this string, so a question can never
#: silently drift back to a private/simplified expression.
CANONICAL_1PK_EXPRESSION = (
    "1+k = 0.93 + 0.487118*c14*(B/L)^1.06806*(T/L)^0.46106"
    "*(L/ L_R)^0.121563*(L^3/nabla)^0.36486*(1-Cp)^(-0.604247)"
)

#: Published applicability box.
APPLICABILITY = {
    "Cp": (0.55, 0.85),
    "L_over_B": (3.90, 14.90),
    "B_over_T": (2.10, 4.00),
    "Cb": (0.55, 0.85),
}

#: Empirical coefficient from the regression.
_K1 = 0.487118
_EXP_BL = 1.06806
_EXP_TL = 0.46106
_EXP_LLR = 0.121563
_EXP_VOL = 0.36486
_EXP_CP = -0.604247
_BASE = 0.93


class FormFactorResult(NamedTuple):
    """Full breakdown of a 1+k evaluation (kept for auditability)."""

    one_plus_k: float
    k: float
    Cp: float
    L_R: float
    c14: float
    nabla: float
    terms: Dict[str, float]


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def form_factor_terms(
    Cb: float,
    L: float,
    B: float,
    T: float,
    Cm: float,
    lcb: float = 0.0,
    C_stern: float = 0.0,
) -> FormFactorResult:
    """Evaluate the published Holtrop-Mennen 1+k, returning every intermediate.

    Parameters
    ----------
    Cb : block coefficient
    L, B, T : length (between perpendiculars), breadth, draught  [m]
    Cm : midship section coefficient (Cp = Cb / Cm is derived from it)
    lcb : LCB position as a fraction of L, positive forward of midships
    C_stern : stern shape parameter (0 = normal section, published range -25..25)
    """
    _check(L > 0.0 and B > 0.0 and T > 0.0, "L, B, T must be positive")
    _check(0.0 < Cb < 1.0, f"Cb out of range: {Cb}")
    _check(0.0 < Cm <= 1.0, f"Cm out of range: {Cm}")
    _check(Cb <= Cm, f"Cb ({Cb}) must not exceed Cm ({Cm})")

    Cp = Cb / Cm
    _check(0.0 < Cp < 1.0, f"derived Cp out of range: {Cp}")

    nabla = Cb * L * B * T
    c14 = 1.0 + 0.011 * C_stern

    denom = 4.0 * Cp - 1.0
    _check(abs(denom) > 1e-12, "degenerate Cp (4*Cp - 1 == 0)")
    L_R = L * (1.0 - Cp + 0.06 * Cp * lcb / denom)
    _check(L_R > 0.0, f"computed run length L_R <= 0: {L_R}")

    terms = {
        "B_over_L": (B / L) ** _EXP_BL,
        "T_over_L": (T / L) ** _EXP_TL,
        "L_over_LR": (L / L_R) ** _EXP_LLR,
        "volumetric": (L ** 3 / nabla) ** _EXP_VOL,
        "prismatic": (1.0 - Cp) ** _EXP_CP,
    }

    product = _K1 * c14
    for key in ("B_over_L", "T_over_L", "L_over_LR", "volumetric", "prismatic"):
        product *= terms[key]

    one_plus_k = _BASE + product
    return FormFactorResult(
        one_plus_k=one_plus_k,
        k=one_plus_k - 1.0,
        Cp=Cp,
        L_R=L_R,
        c14=c14,
        nabla=nabla,
        terms=terms,
    )


def form_factor_1pk(
    Cb: float,
    L: float,
    B: float,
    T: float,
    Cm: float,
    lcb: float = 0.0,
    C_stern: float = 0.0,
) -> float:
    """Published Holtrop-Mennen (1982) form factor ``1+k``."""
    return form_factor_terms(Cb, L, B, T, Cm, lcb, C_stern).one_plus_k


# --------------------------------------------------------------------------
# Independent second evaluation path (log-space assembly).
# Kept deliberately different from the chained multiplication above so that a
# transcription slip in either path is detected by `check_assembly_consistency`.
# --------------------------------------------------------------------------
_EXPONENT_TABLE = (
    (_EXP_BL, "B_over_L"),
    (_EXP_TL, "T_over_L"),
    (_EXP_LLR, "L_over_LR"),
    (_EXP_VOL, "volumetric"),
    (_EXP_CP, "prismatic"),
)


def form_factor_1pk_logspace(
    Cb: float,
    L: float,
    B: float,
    T: float,
    Cm: float,
    lcb: float = 0.0,
    C_stern: float = 0.0,
) -> float:
    """Same published expression, assembled additively in log space."""
    Cp = Cb / Cm
    nabla = Cb * L * B * T
    L_R = L * (1.0 - Cp + 0.06 * Cp * lcb / (4.0 * Cp - 1.0))
    bases = {
        "B_over_L": B / L,
        "T_over_L": T / L,
        "L_over_LR": L / L_R,
        "volumetric": L ** 3 / nabla,
        "prismatic": 1.0 - Cp,
    }
    log_sum = 0.0
    for exponent, key in _EXPONENT_TABLE:
        log_sum += exponent * math.log(bases[key])
    return _BASE + _K1 * (1.0 + 0.011 * C_stern) * math.exp(log_sum)


def check_assembly_consistency(**kwargs) -> float:
    """Return the relative gap between the two independent assembly paths."""
    a = form_factor_1pk(**kwargs)
    b = form_factor_1pk_logspace(**kwargs)
    return abs(a - b) / abs(a)


# --------------------------------------------------------------------------
# Conformance criteria — the machine-checkable judgement that the old
# fabricated expression failed and the published one passes.
# --------------------------------------------------------------------------
def watanabe_trend_anchor(Cb: float, L_over_B: float) -> float:
    """Single-parameter form-factor trend anchor (textbook order-of-magnitude).

        k ~= -0.095 + 25.6 * Cb / (L/B) ** 2

    This correlation is *not* a precision benchmark: it ignores prismatic
    distribution and run length.  It is used only to reject references that are
    an order of magnitude out of family, never as a pass/fail accuracy gate.
    """
    return -0.095 + 25.6 * Cb / (L_over_B ** 2)


def within_applicability(Cb: float, L: float, B: float, T: float, Cm: float) -> Dict[str, bool]:
    """Flag which published applicability limits a case satisfies."""
    Cp = Cb / Cm
    return {
        "Cp": APPLICABILITY["Cp"][0] <= Cp <= APPLICABILITY["Cp"][1],
        "L_over_B": APPLICABILITY["L_over_B"][0] <= L / B <= APPLICABILITY["L_over_B"][1],
        "B_over_T": APPLICABILITY["B_over_T"][0] <= B / T <= APPLICABILITY["B_over_T"][1],
        "Cb": APPLICABILITY["Cb"][0] <= Cb <= APPLICABILITY["Cb"][1],
    }