"""Holtrop–Mennen (1982) + Holtrop (1984) ship resistance prediction.

This module implements the *published* statistical regression of Holtrop & Mennen
(1982, International Shipbuilding Progress 29(335)) together with the 1984
additions (bulbous-bow, transom-immersion and model-ship correlation allowance),
as used by the standard ship-powering practice.  It replaces the hand-fitted
"residual resistance" curve (``Cr = 0.002 (1 + 2 Cb) Fn^2.5``) that shipped in
``backend/api/calculations.py`` and the invented ``Cr_base``/``Rw_base`` terms of
``optimizer/interfaces/resistance_eval.py``, neither of which is Holtrop-Mennen.

Equations (numbering refers to the 1982 paper / 1984 addendum)
---------------------------------------------------------------
Wetted surface (no bulb, ABT = 0)::

    S = L (2T + B) sqrt(CM) *
        (0.453 + 0.4425 CB - 0.2862 CM - 0.003467 B/T + 0.3696 CWP)
        + 2.38 ABT / CB

Form factor::

    1 + k = 0.93 + 0.487118 c14 (B/L)^1.06806 (T/L)^0.46106
                  (L/LR)^0.121563 (L^3/nabla)^0.36486 (1-CP)^(-0.604247)
    c14 = 1 + 0.011 Cstern
    LR  = L (1 - CP + 0.06 CP lcb / (4 CP - 1))        (lcb as fraction of L)

Wave resistance::

    RW = c1 c2 c5 nabla rho g exp[m1 Fn^d + m2 cos(lambda Fn^-2)],  d = -0.9
    c1 = 2223105 c7^3.78613 (T/B)^1.07961 (90 - iE)^(-1.37565)
    c5 = 1 - 0.8 AT / (B T CM)
    lambda = 1.446 CP - 0.03 L/B   (L/B < 12),  1.446 CP - 0.36  (L/B >= 12)
    m1 = 0.0140407 L/T - 1.75254 nabla^(1/3)/L - 4.79323 B/L - c16
    m2 = c15 CP^2 exp(-0.1 Fn^-2)
    c15 = -1.69385 + (L/nabla^(1/3) - 8)/2.36 (L/nabla^(1/3) < 12) else -1.69385
    c16 = 8.07981 CP - 13.8673 CP^2 + 6.984388 CP^3 (CP < 0.8)
        = 1.73014 - 0.7067 CP                       (CP >= 0.8)
    c7  = 0.229577 (B/L)^(1/3)  (B/L < 0.11)
        = 0.5 - 0.0625 L/B      (0.11 <= B/L <= 0.25)
        = B/L                   (B/L > 0.25)
    iE  = 1 + 89 exp{-(L/B)^0.80856 (1-CWP)^0.30484 (1-CP-0.0225 lcb)^0.6367
                     (LR/B)^0.34574 (100 nabla/L^3)^0.16302}   [deg]

Additional resistances (Holtrop 1984)::

    RA  = 0.5 rho V^2 S CA,  CA = 0.006 (L + 100)^-0.16 - 0.00205
    RTR = 0.5 rho V^2 AT c6, c6 = 0.2 (1 - 0.2 FnT),  FnT < 5
    FnT = V / sqrt(2 g AT / (B + B CWP))
    RB  = 0.11 exp(-3 PB^-2) Fni^3 ABT^1.5 rho g / (1 + Fni^2)      (ABT > 0)
    RAPP = 0.5 rho V^2 S_app (1+k)2 Cf_app                          (S_app > 0)

    RT = RF (1 + k) + RAPP + RW + RB + RTR + RA

Validity domain (Holtrop 1982, section 8): 0.55 <= CB <= 0.85,
3.9 <= L/B <= 9.5, 2.1 <= B/T <= 4.0, Fn <= 0.45 (wave-regression range).
Outside the domain the coefficients are being extrapolated; the caller is told so
through :func:`domain_warnings` and the results must be graded WITHHELD, not REAL.

Not implemented (documented limitation, see docs/verification/
resistance_benchmark_audit.md): the 1984 secondary term of CA for TF/LWL > 0.04,
wave-resistance reduction by a bulbous bow in the *wave* term other than c2,
appendage (1+k)2 variation, and air resistance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

G = 9.81  # m/s^2
RHO_SEAWATER = 1025.0  # kg/m^3, 15 degC
NU_SEAWATER = 1.1892e-6  # m^2/s, 15 degC
RHO_FRESHWATER = 998.5
NU_FRESHWATER = 1.1390e-6


# ---------------------------------------------------------------------------
# elementary building blocks
# ---------------------------------------------------------------------------
def ittc1957_cf(re: float) -> float:
    """ITTC-1957 model-ship correlation line Cf = 0.075 / (log10 Re - 2)^2."""
    if re <= 100.0:
        raise ValueError(f"Reynolds number too low for ITTC-1957: {re}")
    d = math.log10(re) - 2.0
    if d == 0.0:
        raise ValueError("singular ITTC-1957 denominator (log10 Re = 2)")
    return 0.075 / d ** 2


def wetted_surface(length: float, beam: float, draft: float, cb: float, cm: float,
                   cwp: float, abt: float = 0.0) -> float:
    """Holtrop-Mennen 1982 wetted-surface regression (m^2)."""
    bracket = (0.453 + 0.4425 * cb - 0.2862 * cm
               - 0.003467 * beam / draft + 0.3696 * cwp)
    s = length * (2.0 * draft + beam) * math.sqrt(cm) * bracket
    if abt > 0.0:
        s += 2.38 * abt / cb
    return s


def c7_coefficient(bl_ratio: float) -> float:
    """Holtrop-Mennen 1982 c7.

    Note: the published piecewise form is *not* continuous at B/L = 0.11 (the
    linear branch returns 0.5 - 0.0625 L/B, i.e. -0.068 at B/L = 0.11).  Because
    c1 ~ c7^3.78613 a non-positive c7 is meaningless, so values <= 0 are clipped
    to 1e-3 and flagged by :func:`c7_flagged`.  Ships with B/L >= 0.11 that the
    caller uses here should be checked against the source table.
    """
    if bl_ratio < 0.11:
        return 0.229577 * bl_ratio ** (1.0 / 3.0)
    if bl_ratio <= 0.25:
        return max(0.5 - 0.0625 / bl_ratio, 1e-3)
    return bl_ratio


def c7_flagged(bl_ratio: float) -> bool:
    """True when the linear c7 branch is non-positive (out of regression sense)."""
    return 0.11 <= bl_ratio <= 0.25 and 0.5 - 0.0625 / bl_ratio <= 0.0


def c16_coefficient(cp: float) -> float:
    if cp < 0.8:
        return 8.07981 * cp - 13.8673 * cp ** 2 + 6.984388 * cp ** 3
    return 1.73014 - 0.7067 * cp


def c15_coefficient(l_over_nabla13: float) -> float:
    if l_over_nabla13 < 12.0:
        return -1.69385 + (l_over_nabla13 - 8.0) / 2.36
    return -1.69385


def half_entrance_angle(length: float, beam: float, cwp: float, cp: float,
                        lcb: float, lr: float, nabla: float) -> float:
    """Holtrop-Mennen 1982 half angle of entrance iE (degrees)."""
    lb = length / beam
    term = ((lb ** 0.80856) * ((1.0 - cwp) ** 0.30484)
            * ((1.0 - cp - 0.0225 * lcb) ** 0.6367)
            * ((lr / beam) ** 0.34574)
            * ((100.0 * nabla / length ** 3) ** 0.16302))
    return 1.0 + 89.0 * math.exp(-term)


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------
@dataclass
class HoltropResult:
    speed_ms: float
    fn: float
    re: float
    wetted_surface_m2: float
    form_factor: float
    cp: float
    cwp: float
    cm: float
    ie_deg: float
    lcb_frac: float
    nabla_m3: float
    rf_n: float
    rw_n: float
    rbulb_n: float
    rtr_n: float
    ra_n: float
    rapp_n: float
    rt_n: float
    cf: float
    ct: float
    cr: float
    ca: float
    c1: float
    c2: float
    c5: float
    warnings: List[str] = field(default_factory=list)
    derived: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


def holtrop_resistance(
    length: float,
    beam: float,
    draft: float,
    cb: float,
    speed_ms: float,
    *,
    cm: Optional[float] = None,
    cwp: Optional[float] = None,
    lcb_frac: float = 0.0,
    t_forward: Optional[float] = None,
    abt: float = 0.0,
    hb: float = 0.0,
    at: float = 0.0,
    cstern: float = 0.0,
    wet_surface: Optional[float] = None,
    s_appendage: float = 0.0,
    form_factor_appendage: float = 2.0,
    rho: float = RHO_SEAWATER,
    nu: float = NU_SEAWATER,
    apply_correlation_allowance: bool = True,
) -> HoltropResult:
    """Total calm-water resistance by Holtrop-Mennen 1982 (+1984 addenda).

    ``cb`` is the block coefficient; ``cp = cb / cm``.  When ``cm``/``cwp`` are
    omitted sensible regressions are used and flagged in ``result.derived``
    (they are *not* silently trusted: a caller that has model-test particulars
    should always pass them).
    """
    _check(length > 0 and beam > 0 and draft > 0, "L, B, T must be positive")
    _check(0.20 < cb < 0.98, f"Cb out of physical range: {cb}")
    _check(speed_ms > 0, "speed must be positive")
    _check(-0.05 <= lcb_frac <= 0.05, "lcb_frac must be a fraction of L, |lcb|<=5%")

    derived: Dict[str, str] = {}
    warnings: List[str] = []

    if cm is None:
        cm = min(0.999, 0.92 + 0.13 * cb)  # generic fine/full-form regression
        derived["cm"] = f"0.92 + 0.13*Cb (clipped 0.999) -> {cm:.4f}"
    if cwp is None:
        cp_for_cwp = cb / cm
        cwp = (1.0 + 2.0 * cp_for_cwp) / 3.0  # Schneekluth waterplane estimate
        derived["cwp"] = f"(1 + 2*Cp)/3 -> {cwp:.4f}"
    _check(0.5 < cm <= 1.0, f"Cm out of range: {cm}")
    _check(0.5 < cwp <= 1.0, f"Cwp out of range: {cwp}")

    cp = cb / cm
    _check(0.5 < cp < 1.0, f"Cp = Cb/Cm out of range: {cp}")

    nabla = cb * length * beam * draft
    lr = length * (1.0 - cp + 0.06 * cp * lcb_frac / (4.0 * cp - 1.0))
    _check(0.02 * length < lr < 1.2 * length, f"LR pathological: {lr}")

    tf = draft if t_forward is None else t_forward
    fn = speed_ms / math.sqrt(G * length)
    re = speed_ms * length / nu

    # --- wetted surface -----------------------------------------------------
    s = wet_surface if wet_surface is not None else wetted_surface(
        length, beam, draft, cb, cm, cwp, abt)
    if wet_surface is not None:
        derived["wet_surface"] = "supplied by caller"

    # --- ITTC-1957 friction + form factor ----------------------------------
    cf = ittc1957_cf(re)
    rf = 0.5 * rho * speed_ms ** 2 * s * cf
    c14 = 1.0 + 0.011 * cstern
    form1k = (0.93
            + 0.487118 * c14
            * (beam / length) ** 1.06806
            * (draft / length) ** 0.46106
            * (length / lr) ** 0.121563
            * (length ** 3 / nabla) ** 0.36486
            * (1.0 - cp) ** (-0.604247))

    # --- wave resistance ----------------------------------------------------
    ie = half_entrance_angle(length, beam, cwp, cp, lcb_frac, lr, nabla)
    _check(0.0 < ie < 90.0, f"iE out of range: {ie}")
    if c7_flagged(beam / length):
        warnings.append(
            "c7 linear branch non-positive for L/B >= 8 (published regression "
            "discontinuity); c7 clipped and RW unreliable")
    c1 = (2223105.0 * c7_coefficient(beam / length) ** 3.78613
          * (draft / beam) ** 1.07961 * (90.0 - ie) ** (-1.37565))
    if abt > 0.0:
        c3 = (0.56 * abt ** 1.5
              / (beam * draft * (0.31 * math.sqrt(abt) + tf - hb)))
        c2 = math.exp(-1.89 * math.sqrt(c3))
    else:
        c2 = 1.0
    c5 = 1.0 - 0.8 * at / (beam * draft * cm)
    lam = (1.446 * cp - 0.03 * length / beam) if length / beam < 12.0 \
        else (1.446 * cp - 0.36)
    m1 = (0.0140407 * length / draft
          - 1.75254 * nabla ** (1.0 / 3.0) / length
          - 4.79323 * beam / length
          - c16_coefficient(cp))
    m2 = c15_coefficient(length / nabla ** (1.0 / 3.0)) * cp ** 2 \
        * math.exp(-0.1 * fn ** -2)
    exponent = m1 * fn ** -0.9 + m2 * math.cos(lam * fn ** -2)
    rw = c1 * c2 * c5 * nabla * rho * G * math.exp(exponent)
    if rw < 0.0:  # cos() oscillation can drive the regression negative
        warnings.append("RW regression returned negative value; clipped to 0")
        rw = 0.0

    # --- 1984 addenda -------------------------------------------------------
    rbulb = 0.0
    if abt > 0.0:
        denom = tf - 1.5 * hb
        if denom > 0.0:
            pb = 0.56 * math.sqrt(abt) / denom
            fni = speed_ms / math.sqrt(
                G * (tf - hb - 0.25 * math.sqrt(abt)) + 0.15 * speed_ms ** 2)
            rbulb = (0.11 * math.exp(-3.0 * pb ** -2) * fni ** 3
                     * abt ** 1.5 * rho * G / (1.0 + fni ** 2))
        else:
            warnings.append("bulb parameters inconsistent (TF - 1.5 hB <= 0); RB=0")

    rtr = 0.0
    if at > 0.0:
        fnt = speed_ms / math.sqrt(2.0 * G * at / (beam + beam * cwp))
        c6 = 0.2 * (1.0 - 0.2 * fnt) if fnt < 5.0 else 0.0
        rtr = max(c6, 0.0) * 0.5 * rho * speed_ms ** 2 * at

    ca = 0.0
    ra = 0.0
    if apply_correlation_allowance:
        ca = 0.006 * (length + 100.0) ** (-0.16) - 0.00205
        if tf / length > 0.04:
            warnings.append(
                "TF/LWL > 0.04: Holtrop-1984 secondary CA correction not "
                "implemented; CA may be under-predicted")
        ra = 0.5 * rho * speed_ms ** 2 * s * ca

    rapp = 0.0
    if s_appendage > 0.0:
        cf_app = ittc1957_cf(speed_ms * (s_appendage ** 0.5) / nu)
        rapp = 0.5 * rho * speed_ms ** 2 * s_appendage * form_factor_appendage * cf_app

    rt = rf * form1k + rapp + rw + rbulb + rtr + ra
    q = 0.5 * rho * speed_ms ** 2 * s
    ct = rt / q
    cr = (rw + rbulb + rtr) / q

    warnings.extend(domain_warnings(length, beam, draft, cb, fn))

    return HoltropResult(
        speed_ms=speed_ms, fn=fn, re=re, wetted_surface_m2=s,
        form_factor=form1k, cp=cp, cwp=cwp, cm=cm, ie_deg=ie,
        lcb_frac=lcb_frac, nabla_m3=nabla,
        rf_n=rf * form1k, rw_n=rw, rbulb_n=rbulb, rtr_n=rtr,
        ra_n=ra, rapp_n=rapp, rt_n=rt, cf=cf, ct=ct, cr=cr, ca=ca,
        c1=c1, c2=c2, c5=c5, warnings=warnings, derived=derived,
    )


def domain_warnings(length: float, beam: float, draft: float, cb: float,
                    fn: float) -> List[str]:
    """Flag extrapolation outside the 1982 regression domain."""
    out: List[str] = []
    if not 0.55 <= cb <= 0.85:
        out.append(f"Cb={cb:.3f} outside Holtrop domain [0.55, 0.85]")
    if not 3.9 <= length / beam <= 9.5:
        out.append(f"L/B={length / beam:.2f} outside [3.9, 9.5]")
    if not 2.1 <= beam / draft <= 4.0:
        out.append(f"B/T={beam / draft:.2f} outside [2.1, 4.0]")
    if fn > 0.45:
        out.append(f"Fn={fn:.3f} > 0.45 (wave regression extrapolation)")
    return out


def in_domain(length: float, beam: float, draft: float, cb: float,
              fn: float) -> bool:
    return not domain_warnings(length, beam, draft, cb, fn)