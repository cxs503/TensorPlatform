"""Hull-girder load side: IACS UR S11 wave bending moments and girder stress.

Only *closed-form rule formulae* are used here, because they are the only load
reference that can be re-derived on a machine without a strip of tank-test data:

IACS Unified Requirement S11 (also the CSR basis), wave bending moment amidships
for ``90 <= L <= 500`` m::

    M_wh = +0.19 C L^2 B Cb      [kN m]   (hogging, deck in tension)
    M_ws = -0.11 C L^2 B Cb      [kN m]   (sagging,  deck in compression)

    C(L) = 10.75 - ((300 - L)/100)^1.5        for  90 <= L <= 300
    C(L) = 10.75                              for 300 <  L <= 350
    C(L) = 10.75 - ((L - 350)/150)^1.5        for 350 <  L <= 500

Properties that make this a checkable benchmark: ``C`` is continuous and
non-decreasing in ``L``, it is flat (10.75) on ``300 < L <= 350``, so the two
branches must meet at both joins; the hogging/sagging ratio is the constant
0.19/0.11 = 1.7273.

The girder stress check uses the rule allowable of 175/k N/mm^2 (S11, hull girder
normal stress for ``L >= 90`` m).  Still-water bending moment is **not** modelled
here, so the check performed in ``benchmarks.py`` is a *necessary* condition on
the wave contribution only, never a sufficient one - stated explicitly rather
than glossed over.
"""

from __future__ import annotations

__all__ = [
    "s11_C",
    "s11_wave_bending_moments",
    "bending_stress",
    "rule_allowable_stress",
    "HOGGING_RATIO",
]

HOGGING_RATIO = 0.19 / 0.11  # 1.7273 (S11 coefficients, exact by construction)


def s11_C(L: float) -> float:
    """S11 wave coefficient ``C`` as a function of rule length ``L`` [m]."""
    if not 90.0 <= L <= 500.0:
        raise ValueError("S11 C(L) defined for 90 <= L <= 500 m")
    if L <= 300.0:
        return 10.75 - ((300.0 - L) / 100.0) ** 1.5
    if L <= 350.0:
        return 10.75
    return 10.75 - ((L - 350.0) / 150.0) ** 1.5


def s11_wave_bending_moments(L: float, B: float, Cb: float) -> dict:
    """S11 wave bending moments amidships [N m]; hogging positive."""
    C = s11_C(L)
    m_h = 0.19 * C * L ** 2 * B * Cb * 1e3
    m_s = -0.11 * C * L ** 2 * B * Cb * 1e3
    return {
        "L": L, "B": B, "Cb": Cb, "C": C,
        "M_wh": m_h, "M_ws": m_s,
        "M_wh_kNm": m_h / 1e3, "M_ws_kNm": m_s / 1e3,
        "hogging_over_sagging": abs(m_h / m_s),
    }


def bending_stress(M: float, Z: float) -> float:
    """Hull-girder normal stress ``sigma = M / Z`` [Pa] (M in N m, Z in m^3)."""
    if Z <= 0:
        raise ValueError("section modulus must be positive")
    return M / Z


def rule_allowable_stress(L: float, k: float = 1.0) -> float:
    """S11 allowable hull-girder normal stress [Pa] = 175/k N/mm^2."""
    if L < 90.0:
        raise ValueError("S11 girder stress criterion applies to L >= 90 m")
    if k <= 0:
        raise ValueError("material factor k must be positive")
    return 175e6 / k