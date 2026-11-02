"""Faithful Holtrop-Mennen (1984) ship calm-water resistance reference implementation.

Provenance of every equation (public, textbook, no external data fetch required):

* Frictional line ................ ITTC-1957 model-ship correlation line (7th ITTC, 1957)
                                   Cf = 0.075 / (log10 Re - 2)^2
* Schoenherr line ................ Schoenherr (1932), 0.242/sqrt(Cf) = log10(Re Cf)
                                   implemented only as an *independent cross-check*
* Wetted surface ................. Holtrop & Mennen (1982) eq. for S, extended with the
                                   bulb term 2.38 ABT / Cb (Holtrop 1984)
* Form factor (1+k) .............. Holtrop (1984), c14 = 1 + 0.011 cstern
* Wave resistance ................ Holtrop (1984): c1 c2 c5 grad rho g exp(m1 Fn^-0.9
                                   + m4 cos(lambda Fn^-2)), with c7, iE, m1, m4, lambda,
                                   c15, c16 as published
* Bulbous bow resistance ......... Holtrop (1984): Rb = 0.11 exp(-3 PB^2) Fni^3 ABT^1.5
                                   rho g / (1 + Fni^2)
* Transom immersion resistance ... Holtrop (1984): Rtr = 0.5 rho V^2 AT c6
* Correlation allowance .......... Holtrop (1984) for L >= 50 m:
                                   Ca = 0.006 (L + 100)^-0.16 - 0.00205
* Total .......................... Rt = (1+k) Rf + Rw + Rb + Rtr + Ra

Deliberate, documented simplifications (kept explicit so the audit cannot over-claim):

1. ``rhow`` (wave-resistance hull-form factor) is taken as 1.0, i.e. results are
   restricted to Fn <= 0.4.  ``ResistanceResult.fn_in_rhow_domain`` reports whether
   the evaluated point stays inside that range.
2. The low-length correction term of Ca (validity L < 50 m) is not implemented.
3. The m4 (wave-resistance Froude-dependence) model form of Holtrop (1984),
   m4 = c15 0.4 exp(-0.034 Fn^-3.29), is used.  The earlier Holtrop & Mennen (1982)
   variant m2 = c15 Cp^2 exp(-0.1 Fn^-2) is *not* implemented here; the two forms
   differ by <=3% on Rw and <=0.6% on Rt over Fn = 0.10-0.35 (see
   tests/test_resistance_published_parity.py), so this is a documented model-form
   uncertainty, not a numerical defect.
4. rhow (wave-resistance hull-form factor) is 1.0 as stated above.

Earlier versions of this module additionally approximated the length of run by
``LR ~ L`` ("valid because LR/B > 4 for the benchmark hulls").  That validity claim
was machine-checkably false (published regression gives LR/B = 0.62 for KVLCC2 and
1.85 for KCS) and the approximation biased Rw by +70-77%, because iE -> c1 enters
the wave-resistance exponential.  LR now follows the published regression
``length_of_run()``; see docs/reports/m1826/.  The same defect made the (1+k)
regression degenerate: the published factor (L/LR)^0.121563 is a function of the
run length, whereas the old code used (L/nabla^(1/3))^0.121563, i.e. a second power
of the single variable (L^3/nabla) already present as (L^3/nabla)^0.36486.

All quantities in SI: m, m/s, m^3, N, W.

Author: TensorPlatform agent seat (m1536), verification-debt track.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

G = 9.81                # gravity [m/s^2]
RHO_SEA = 1025.0        # sea water density [kg/m^3]
NU_SEA_15C = 1.19e-6    # kinematic viscosity of sea water at 15 C [m^2/s]

# ITTC-78 specific frictional (roughness) allowance, as used by the platform API
DELTA_CF_ROUGHNESS = 0.0004


# ---------------------------------------------------------------------------
# Friction lines
# ---------------------------------------------------------------------------

def cf_ittc1957(re: float) -> float:
    """ITTC-1957 model-ship correlation line Cf = 0.075 / (log10(Re) - 2)^2."""
    if re <= 100.0:
        raise ValueError(f"Reynolds number out of range for ITTC-57: {re}")
    x = math.log10(re) - 2.0
    return 0.075 / (x * x)


def cf_schoenherr(re: float) -> float:
    """Schoenherr (1932) line, solved from 0.242/sqrt(Cf) = log10(Re*Cf).

    Independent historical line, used to cross-check ITTC-57 (the ITTC-57 line was
    fitted to Schoenherr's line, so agreement within a few percent is expected).
    """
    if re <= 0:
        raise ValueError(f"Reynolds number must be positive: {re}")
    lo, hi = 1e-4, 2e-2
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f = 0.242 / math.sqrt(mid) - math.log10(re * mid)
        # f is monotonically decreasing in Cf
        if f > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Geometry correlations
# ---------------------------------------------------------------------------

def wetted_surface(
    l: float, b: float, t: float, cb: float, cm: float, cwp: float, abt: float = 0.0
) -> float:
    """Holtrop wetted surface [m^2] (Holtrop & Mennen 1982, Holtrop 1984 bulb term)."""
    s = (
        l * (2.0 * t + b) * math.sqrt(cm)
        * (0.453 + 0.4425 * cb - 0.2862 * cm - 0.003467 * b / t + 0.3696 * cwp)
    )
    if abt > 0.0:
        s += 2.38 * abt / cb
    return s


def wetted_surface_denny_mumford(l: float, b: float, t: float, cb: float) -> float:
    """Denny-Mumford (1962) wetted-surface correlation, an independent estimator.

    Classic form: S = L (1.7 T + Cb B).  Used only as a cross-check (bounded band).
    """
    return l * (1.7 * t + cb * b)


def form_factor_1pk(
    l: float,
    b: float,
    t: float,
    cb: float,
    cm: float,
    vol: float,
    cstern: float = 0.0,
    lcb_pct: float = 0.0,
) -> float:
    """Holtrop (1984) form factor (1+k), returned as a *full multiplier*.

    (1+k) = 0.93 + 0.487118 c14 (B/L)^1.06806 (T/L)^0.46106 (L/LR)^0.121563
            (L^3/vol)^0.36486 (1 - Cp)^-0.604247

    LR is the published length of the run (:func:`length_of_run`); ``lcb_pct`` feeds
    that regression (default 0.0 = LCB at 0.5 L, the L-independent special case).
    """
    c14 = 1.0 + 0.011 * cstern
    cp = cb / cm
    l3v = l ** 3 / vol
    lr = length_of_run(l, cp, lcb_pct)
    return (
        0.93
        + 0.487118 * c14
        * (b / l) ** 1.06806
        * (t / l) ** 0.46106
        * (l / lr) ** 0.121563
        * l3v ** 0.36486
        * (1.0 - cp) ** (-0.604247)
    )


def c7_coefficient(b: float, l: float) -> float:
    """Holtrop (1984) c7 as a function of B/L."""
    bl = b / l
    if bl < 0.11:
        return 0.229577 * bl ** (1.0 / 3.0)
    if bl < 0.25:
        return bl
    return 0.5 - 0.0625 * (l / b)


def length_of_run(l: float, cp: float, lcb_pct: float) -> float:
    """Holtrop (1984) length of the run [m]: LR = L (1 - Cp + 0.06 Cp lcb / (4 Cp - 1)).

    ``lcb_pct`` is the LCB position in percent of L, positive forward of 0.5 L
    (Holtrop's sign convention).  LR enters the iE (half-angle of entrance)
    correlation, which drives c1 and therefore the whole wave-resistance term:
    a relative error of 10% on LR can move Rw by tens of percent, so LR must not be
    approximated by L.
    """
    denom = 4.0 * cp - 1.0
    if denom <= 0.0:
        raise ValueError(f"prismatic coefficient out of range for LR regression: Cp={cp}")
    lr = l * (1.0 - cp + 0.06 * cp * lcb_pct / denom)
    if lr <= 0.0:
        raise ValueError(
            f"LR regression returned a non-positive run length (LR={lr}): "
            f"L={l}, Cp={cp}, lcb%={lcb_pct}"
        )
    return lr


def iE_coefficient(
    l: float, b: float, cwp: float, cp: float, lcb_pct: float, vol: float, lr: float
) -> float:
    """Holtrop (1984) half-angle of entrance iE [deg]; lcb_pct in percent of L."""
    return 1.0 + 89.0 * math.exp(
        -((l / b) ** 0.80856)
        * ((1.0 - cwp) ** 0.30484)
        * ((1.0 - cp - 0.0225 * lcb_pct) ** 0.6367)
        * ((lr / b) ** 0.34574)
        * ((100.0 * vol / l ** 3) ** 0.16302)
    )


# ---------------------------------------------------------------------------
# Regression-line anchor: IMO EEDI reference line (MEPC.203(62))
# ---------------------------------------------------------------------------

# Ship-type constants of the EEDI reference line  EEDI_ref = a * b^(-c), b = DWT.
EEDI_REFERENCE = {
    "tanker": (1218.80, 0.488),
    "bulk_carrier": (961.79, 0.477),
    "container": (186.22, 0.448),   # container uses capacity-dependent form; rough use only
}

CF_HFO = 3.114      # g CO2 / g fuel (MEPC.308(73) default)
SFC_DIESEL = 175.0  # g fuel / kWh (typical 2-stroke, conservative)


def eedi_reference(ship_type: str, deadweight: float) -> float:
    """IMO EEDI reference line value [g CO2 / (t nm)]."""
    a, c = EEDI_REFERENCE[ship_type]
    return a * deadweight ** (-c)


def eedi_from_shaft_power(
    p_me_kw: float, deadweight: float, speed_kn: float, sf_c: float = SFC_DIESEL
) -> float:
    """Attained EEDI [g CO2 / (t nm)] for a single main engine, auxiliaries ignored."""
    return p_me_kw * sf_c * CF_HFO / (deadweight * speed_kn)


def required_shaft_power_for_eedi(
    target_eedi: float, deadweight: float, speed_kn: float, sf_c: float = SFC_DIESEL
) -> float:
    """Shaft power [kW] implied by a target EEDI value (inverse of eedi_from_shaft_power)."""
    return target_eedi * deadweight * speed_kn / (sf_c * CF_HFO)


# ---------------------------------------------------------------------------
# Full Holtrop-Mennen 1984 resistance
# ---------------------------------------------------------------------------

@dataclass
class HullGeometry:
    """Benchmark hull particulars (full scale unless stated otherwise)."""

    name: str
    l: float          # length (Lpp/Lwl used in the method) [m]
    b: float          # beam [m]
    t: float          # mean draft [m]
    cb: float         # block coefficient
    cm: float         # midship section coefficient
    cwp: float        # waterplane coefficient
    lcb_pct: float    # LCB as percent of L, positive forward of 0.5L
    vol: float        # displaced volume [m^3]
    abt: float = 0.0  # transverse area of bulb at forward perpendicular [m^2]
    hb: float = 0.0   # height of bulb centre above keel line [m]
    tf: Optional[float] = None  # forward draft [m]; defaults to t
    at: float = 0.0   # immersed transom area [m^2]
    cstern: float = 0.0
    deadweight: Optional[float] = None  # for EEDI cross-check [t]

    def __post_init__(self) -> None:
        if self.tf is None:
            self.tf = self.t


@dataclass
class ResistanceResult:
    """Component-wise resistance decomposition [N] plus derived coefficients."""

    name: str
    speed_ms: float
    fn: float
    re: float
    s_ref: float
    cf: float
    form_factor_1pk: float
    rf: float                       # frictional, bare hull
    rf_1pk: float                   # (1+k) Rf  (i.e. hull viscous resistance)
    rw: float                       # wave resistance
    rb: float                       # bulbous-bow resistance
    rtr: float                      # transom immersion resistance
    ra: float                       # correlation allowance resistance
    rt: float
    pe_kw: float
    ct: float
    cv: float                       # viscous coefficient (1+k)Cf
    cw: float                       # wave coefficient rw / (0.5 rho V^2 S)
    ca: float
    iE: float
    m1: float
    m4: float
    lam: float
    fn_in_rhow_domain: bool = True
    notes: list = field(default_factory=list)


def holtrop1984_resistance(
    geom: HullGeometry,
    speed_ms: float,
    rho: float = RHO_SEA,
    nu: float = NU_SEA_15C,
) -> ResistanceResult:
    """Compute calm-water resistance with the published Holtrop (1984) method."""
    l, b, t = geom.l, geom.b, geom.t
    cb, cm, cwp = geom.cb, geom.cm, geom.cwp
    vol = geom.vol
    cp = cb / cm
    tf = geom.tf if geom.tf is not None else t

    notes: list = []
    s = wetted_surface(l, b, t, cb, cm, cwp, geom.abt)
    fn = speed_ms / math.sqrt(G * l)
    re = speed_ms * l / nu
    cf = cf_ittc1957(re)
    rf = 0.5 * rho * speed_ms ** 2 * s * cf
    one_plus_k = form_factor_1pk(l, b, t, cb, cm, vol, geom.cstern, geom.lcb_pct)

    # --- wave resistance -------------------------------------------------
    lr = length_of_run(l, cp, geom.lcb_pct)
    ie = iE_coefficient(l, b, cwp, cp, geom.lcb_pct, vol, lr)
    if cp < 0.8:
        c16 = 8.07981 * cp - 13.8673 * cp ** 2 + 6.984388 * cp ** 3
    else:
        c16 = 1.73014 - 0.7067 * cp
    m1 = 0.0140407 * l / t - 1.75254 * vol ** (1.0 / 3.0) / l - 4.79323 * b / l - c16
    lam = 1.446 * cp - 0.03 * l / b if l / b < 12.0 else 1.446 * cp - 0.36

    l3v = l ** 3 / vol
    if l3v < 512.0:
        c15 = -1.69385
    elif l3v > 1727.0:
        c15 = 0.0
    else:
        c15 = -1.69385 + (l / vol ** (1.0 / 3.0) - 8.0) / 2.36
    m4 = c15 * 0.4 * math.exp(-0.034 * fn ** -3.29)

    c7 = c7_coefficient(b, l)
    c1 = 2223105.0 * c7 ** 3.78613 * (t / b) ** 1.07961 * (90.0 - ie) ** (-1.37565)

    if geom.abt > 0.0:
        c3 = (
            0.56 * geom.abt ** 1.5
            / (b * t * (0.31 * math.sqrt(geom.abt) + tf - geom.hb))
        )
    else:
        c3 = 0.0
    c2 = math.exp(-1.89 * math.sqrt(c3))
    c5 = 1.0 - 0.8 * geom.at / (b * t * cm)

    # rhow = 1 for Fn <= 0.4 (documented range restriction)
    fn_in_domain = fn <= 0.4
    if not fn_in_domain:
        notes.append(
            f"Fn={fn:.4f} > 0.4: the Fn-dependent wave-resistance factor rhow is not "
            "implemented (rhow=1.0 assumed), result outside the validated range"
        )
    rw = (
        c1 * c2 * c5 * vol * rho * G
        * math.exp(m1 * fn ** -0.9 + m4 * math.cos(lam * fn ** -2))
    )

    # --- bulbous bow ------------------------------------------------------
    rb = 0.0
    if geom.abt > 0.0:
        pb = 0.56 * math.sqrt(geom.abt) / (tf - 1.5 * geom.hb)
        fni = speed_ms / math.sqrt(
            G * (tf - geom.hb - 0.25 * math.sqrt(geom.abt)) + 0.15 * speed_ms ** 2
        )
        rb = 0.11 * math.exp(-3.0 * pb ** 2) * fni ** 3 * geom.abt ** 1.5 * rho * G / (
            1.0 + fni ** 2
        )

    # --- transom ----------------------------------------------------------
    rtr = 0.0
    if geom.at > 0.0:
        fnt = speed_ms / math.sqrt(2.0 * G * geom.at / (b + b * cwp))
        c6 = 0.2 * (1.0 - 0.2 * fnt) if fnt < 5.0 else 0.0
        rtr = 0.5 * rho * speed_ms ** 2 * geom.at * c6

    # --- correlation allowance -------------------------------------------
    if l >= 50.0:
        ca = 0.006 * (l + 100.0) ** -0.16 - 0.00205
    else:
        notes.append(f"L={l:.1f} m < 50 m: Ca low-length correction not implemented")
        ca = 0.006 * (l + 100.0) ** -0.16 - 0.00205
    ra = 0.5 * rho * speed_ms ** 2 * s * ca

    rt = one_plus_k * rf + rw + rb + rtr + ra
    q = 0.5 * rho * speed_ms ** 2 * s
    return ResistanceResult(
        name=geom.name,
        speed_ms=speed_ms,
        fn=fn,
        re=re,
        s_ref=s,
        cf=cf,
        form_factor_1pk=one_plus_k,
        rf=rf,
        rf_1pk=one_plus_k * rf,
        rw=rw,
        rb=rb,
        rtr=rtr,
        ra=ra,
        rt=rt,
        pe_kw=rt * speed_ms / 1000.0,
        ct=rt / q,
        cv=one_plus_k * cf,
        cw=rw / q,
        ca=ca,
        iE=ie,
        m1=m1,
        m4=m4,
        lam=lam,
        fn_in_rhow_domain=fn_in_domain,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Benchmark hulls (published benchmark-geometry particulars)
# ---------------------------------------------------------------------------

# KVLCC2 (KRISO very large crude carrier): Gothenburg 2010 / Tokyo 2015 workshop hull.
# Published particulars: Lpp=320 m, B=58 m, T=20.8 m, vol=312622 m^3, Cb=0.8098,
# Cm=0.9980, S=27194 m^2, LCB=+3.48 % L aft of midship (hence -3.48 here).
KVLCC2 = HullGeometry(
    name="KVLCC2",
    l=320.0, b=58.0, t=20.8,
    cb=0.8098, cm=0.9980, cwp=0.8548,
    lcb_pct=-3.48, vol=312622.0,
    deadweight=312622.0,   # approx. displaced volume in tonnes of sea water ~ DWT class
)

# KCS (KRISO container ship): Tokyo 2015 workshop hull.
# Lpp=230 m, B=32.2 m, T=10.8 m, vol=52030 m^3, Cb=0.6505, Cm=0.9846, S=9494 m^2.
KCS = HullGeometry(
    name="KCS",
    l=230.0, b=32.2, t=10.8,
    cb=0.6505, cm=0.9846, cwp=0.7350,
    lcb_pct=-0.7, vol=52030.0,
    deadweight=36000.0,
)

# 1984-vintage product tanker representative of Holtrop-Mennen's own calibration set
# (Aframax 110000 dwt class).  Used as an independent mid-Cb hull.
AFRAMAX = HullGeometry(
    name="AFRAMAX-110k",
    l=244.0, b=42.0, t=14.8,
    cb=0.8200, cm=0.9950, cwp=0.8700,
    lcb_pct=-2.0, vol=124000.0,
    deadweight=110000.0,
)


BENCHMARK_HULLS = {h.name: h for h in (KVLCC2, KCS, AFRAMAX)}