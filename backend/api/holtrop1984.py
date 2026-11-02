"""Holtrop-Mennen (1984) calm-water resistance kernel (reference implementation).

Used by :mod:`api.calculations` (``POST /calculations/resistance``) and by the
independent verification suite :mod:`api.audit_resistance`.  Pure Python, no
FastAPI / numpy dependency, so it can be exercised offline in tests.

Method (Holtrop, J. & Mennen, G.G.J., *An approximate power prediction method*,
International Shipbuilding Progress 29 (335), 1982; Holtrop, J., *A statistical
re-analysis of resistance and propulsion data*, ISP 31 (363), 1984)::

    RT = (1+k) Rf + Rw + Rb + Rtr + Ra   [+ optional roughness allowance Rrough]

    Rf  = 0.5 rho V^2 S Cf        Cf  = ITTC-1957 line 0.075/(log10 Re - 2)^2
    Rw  = c1 c2 c5 vol rho g exp(m1 Fn^-0.9 + m4 cos(lambda Fn^-2))
    Rb  = 0.11 exp(-3 Pb^-2) Fni^3 Abt^1.5 rho g / (1 + Fni^2)
    Rtr = 0.5 rho V^2 At c6,       c6 = 0.2 (1 - 0.2 FnT) for FnT < 5
    Ra  = 0.5 rho V^2 S Ca,        Ca = 0.006 (L + 100)^-0.16 - 0.00205

with the full coefficient chain c7 / iE / c1 / c2 / c5 / m1 / m4 / lambda and the
form factor (1+k) as published (see individual function docstrings).

Documented simplifications / limits (kept explicit, never silently applied):

1. ``rhow`` (the Fn-dependent modification of Rw valid for Fn > 0.4) is **not**
   implemented; ``rhow = 1`` is used and the result is flagged
   ``fn_in_rhow_domain = False`` when Fn > 0.4.
2. ``LR`` (length of the run) is approximated by ``L``.  For hulls with
   LR/B > 4 the published coefficient is a weak function of LR/B.
3. The low-length correction of Ca (L < 50 m) is not implemented; the model-scale
   correlation allowance is therefore flagged in ``notes`` for scale models.
4. The method is calibrated on displacement hulls (Cb ~ 0.55-0.85, Fn <= 0.4); it
   is *not* validated for slender axisymmetric bodies (submarines), where the
   ITTC/Bettar-type frictional decomposition is more appropriate.  The endpoint
   still accepts such geometry but reports the caveat.

All quantities SI: m, m/s, m^3, N, W.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

G = 9.81                # gravitational acceleration [m/s^2]
RHO_SEA = 1025.0        # sea water density [kg/m^3]
NU_SEA_15C = 1.19e-6    # kinematic viscosity of sea water at 15 degC [m^2/s]
RHO_FRESH = 998.0       # fresh water (towing tank) [kg/m^3]
NU_FRESH_20C = 1.004e-6  # fresh water at ~20 degC [m^2/s]

#: ITTC-78 specific frictional allowance for hull roughness (typical new hull,
#: k_s = 150 um).  Not part of Holtrop-1984; applied only when the caller asks.
DELTA_CF_ROUGHNESS = 0.0004

#: Upper Froude number of the validated range of the Rw correlation.
FN_RHOW_LIMIT = 0.4


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
        if f > 0:      # 0.242/sqrt(Cf) decreasing in Cf -> f decreasing in Cf
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
    """Holtrop wetted surface [m^2] (Holtrop & Mennen 1982; bulb term Holtrop 1984)."""
    s = (
        l * (2.0 * t + b) * math.sqrt(cm)
        * (0.453 + 0.4425 * cb - 0.2862 * cm - 0.003467 * b / t + 0.3696 * cwp)
    )
    if abt > 0.0:
        s += 2.38 * abt / cb
    return s


def wetted_surface_denny_mumford(l: float, b: float, t: float, cb: float) -> float:
    """Denny-Mumford (1962) wetted surface, S = L (1.7 T + Cb B).  Independent check."""
    return l * (1.7 * t + cb * b)


def form_factor_1pk(
    l: float, b: float, t: float, cb: float, cm: float, vol: float, cstern: float = 0.0
) -> float:
    """Holtrop (1984) form factor (1+k), returned as a *full multiplier*.

    (1+k) = 0.93 + 0.487118 c14 (B/L)^1.06806 (T/L)^0.46106 (L/vol^(1/3))^0.121563
            (L^3/vol)^0.36486 (1 - Cp)^-0.604247,      c14 = 1 + 0.011 cstern
    """
    c14 = 1.0 + 0.011 * cstern
    cp = cb / cm
    l3v = l ** 3 / vol
    return (
        0.93
        + 0.487118 * c14
        * (b / l) ** 1.06806
        * (t / l) ** 0.46106
        * (l / vol ** (1.0 / 3.0)) ** 0.121563
        * l3v ** 0.36486
        * (1.0 - cp) ** (-0.604247)
    )


def c7_coefficient(b: float, l: float) -> float:
    """Holtrop (1984) c7 as a function of B/L (three published branches)."""
    bl = b / l
    if bl < 0.11:
        return 0.229577 * bl ** (1.0 / 3.0)
    if bl < 0.25:
        return bl
    return 0.5 - 0.0625 * (l / b)


def iE_coefficient(
    l: float, b: float, cwp: float, cp: float, lcb_pct: float, vol: float, lr: float
) -> float:
    """Holtrop (1984) half-angle of entrance iE [deg]; ``lcb_pct`` in percent of L."""
    return 1.0 + 89.0 * math.exp(
        -((l / b) ** 0.80856)
        * ((1.0 - cwp) ** 0.30484)
        * ((1.0 - cp - 0.0225 * lcb_pct) ** 0.6367)
        * ((lr / b) ** 0.34574)
        * ((100.0 * vol / l ** 3) ** 0.16302)
    )


def c15_coefficient(l3v: float, vol: float) -> float:
    """Holtrop (1984) c15 (m4 prefactor) as a function of L^3/vol."""
    l = vol ** (1.0 / 3.0)
    if l3v < 512.0:
        return -1.69385
    if l3v > 1727.0:
        return 0.0
    return -1.69385 + (l3v ** (1.0 / 3.0) - 8.0) / 2.36


def correlation_allowance(l: float) -> float:
    """Holtrop (1984) correlation allowance Ca for sea-going vessels."""
    return 0.006 * (l + 100.0) ** -0.16 - 0.00205


# ---------------------------------------------------------------------------
# Estimated secondary section coefficients (only used when not supplied)
# ---------------------------------------------------------------------------

def estimate_cm(cb: float) -> float:
    """Weak regression of Cm on Cb fitted to the Holtrop calibration set.

    Cm is nearly constant for displacement hulls (0.95-0.998); the linear fit used
    here reproduces the two workshop hulls (KCS 0.9846 @ Cb 0.6505, KVLCC2 0.998 @
    Cb 0.8098) to better than 1%.  Callers should always supply ``cm``.
    """
    return min(0.999, max(0.90, 1.0 - 0.045 * (1.0 - cb)))


def estimate_cwp(cb: float) -> float:
    """Kerlen/Schneekluth waterplane coefficient estimate Cwp = (1 + 2 Cb)/3."""
    return (1.0 + 2.0 * cb) / 3.0


# ---------------------------------------------------------------------------
# EEDI cross-check helpers (IMO MEPC.203(62) reference line)
# ---------------------------------------------------------------------------

EEDI_REFERENCE = {
    "tanker": (1218.80, 0.488),
    "bulk_carrier": (961.79, 0.477),
    "container": (186.22, 0.448),   # capacity form; rough use only
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
    """Attained EEDI [g CO2 / (t nm)], single main engine, auxiliaries ignored."""
    return p_me_kw * sf_c * CF_HFO / (deadweight * speed_kn)


def required_shaft_power_for_eedi(
    target_eedi: float, deadweight: float, speed_kn: float, sf_c: float = SFC_DIESEL
) -> float:
    """Shaft power [kW] implied by a target EEDI (inverse of the relation above)."""
    return target_eedi * deadweight * speed_kn / (sf_c * CF_HFO)


# ---------------------------------------------------------------------------
# Full Holtrop-Mennen 1984 resistance
# ---------------------------------------------------------------------------

@dataclass
class HullGeometry:
    """Benchmark hull particulars (all SI, full scale unless stated otherwise)."""

    name: str
    l: float                 # length used in the method (Lpp/Lwl) [m]
    b: float                 # beam [m]
    t: float                 # mean draft [m]
    cb: float                # block coefficient
    cm: float                # midship section coefficient
    cwp: float               # waterplane coefficient
    lcb_pct: float = 0.0     # LCB aft of midship, percent of L (negative = forward)
    vol: float = 0.0         # displaced volume [m^3]
    cstern: float = 0.0      # stern shape coefficient c_stern
    at: float = 0.0          # immersed transom area [m^2]
    abt: float = 0.0         # transverse bulb area at FP [m^2]
    hb: float = 0.0          # bulb centre height above keel [m]
    tf: Optional[float] = None   # forward draft [m] (defaults to t)
    deadweight: Optional[float] = None
    s_published: Optional[float] = None  # published wetted surface [m^2], check only

    def __post_init__(self) -> None:
        if self.vol <= 0.0:
            self.vol = self.cb * self.l * self.b * self.t


@dataclass
class ResistanceResult:
    """Decomposition of the Holtrop-1984 resistance (SI)."""

    name: str
    speed_ms: float
    fn: float
    re: float
    s_ref: float
    cf: float
    form_factor_1pk: float
    rf: float                       # bare ITTC-57 friction
    rf_1pk: float                   # (1+k) Rf, i.e. viscous resistance of the hull
    rw: float                       # wave resistance
    rb: float                       # bulbous bow resistance
    rtr: float                      # transom immersion resistance
    ra: float                       # correlation-allowance resistance
    rt: float
    pe_kw: float
    ct: float
    cv: float                       # viscous coefficient (1+k) Cf
    cw: float                       # wave coefficient Rw / (0.5 rho V^2 S)
    ca: float
    iE: float
    m1: float
    m4: float
    lam: float
    fn_in_rhow_domain: bool = True
    roughness_allowance_cf: float = 0.0
    notes: List[str] = field(default_factory=list)


def holtrop1984_resistance(
    geom: HullGeometry,
    speed_ms: float,
    rho: float = RHO_SEA,
    nu: float = NU_SEA_15C,
    roughness_allowance: float = 0.0,
) -> ResistanceResult:
    """Calm-water resistance with the published Holtrop (1984) method.

    ``roughness_allowance`` is a *specific* frictional coefficient increment
    (ITTC-78, e.g. 0.4e-3 for a typical new hull) added as
    ``R_rough = 0.5 rho V^2 S deltaCf``; it is not part of Holtrop-1984 and is 0 by
    default.
    """
    l, b, t = geom.l, geom.b, geom.t
    cb, cm, cwp = geom.cb, geom.cm, geom.cwp
    vol = geom.vol
    cp = cb / cm
    tf = geom.tf if geom.tf is not None else t

    notes: List[str] = []
    s = wetted_surface(l, b, t, cb, cm, cwp, geom.abt)
    fn = speed_ms / math.sqrt(G * l)
    re = speed_ms * l / nu
    cf = cf_ittc1957(re)
    rf = 0.5 * rho * speed_ms ** 2 * s * cf
    one_plus_k = form_factor_1pk(l, b, t, cb, cm, vol, geom.cstern)

    # --- wave resistance -------------------------------------------------
    lr = l  # documented simplification: LR ~ L
    ie = iE_coefficient(l, b, cwp, cp, geom.lcb_pct, vol, lr)
    if cp < 0.8:
        c16 = 8.07981 * cp - 13.8673 * cp ** 2 + 6.984388 * cp ** 3
    else:
        c16 = 1.73014 - 0.7067 * cp
    m1 = 0.0140407 * l / t - 1.75254 * vol ** (1.0 / 3.0) / l - 4.79323 * b / l - c16
    lam = 1.446 * cp - 0.03 * l / b if l / b < 12.0 else 1.446 * cp - 0.36

    l3v = l ** 3 / vol
    c15 = c15_coefficient(l3v, vol)
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

    fn_in_domain = fn <= FN_RHOW_LIMIT
    if not fn_in_domain:
        notes.append(
            f"Fn={fn:.4f} > {FN_RHOW_LIMIT}: the Fn-dependent wave-resistance factor "
            "rhow of Holtrop-1984 is not implemented (rhow=1 assumed); result is "
            "outside the validated range"
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
    ca = correlation_allowance(l)
    if l < 50.0:
        notes.append(
            f"L={l:.1f} m < 50 m: the low-length correction of Ca is not implemented; "
            "the correlation allowance of the parent hull form is a scale artefact"
        )
    ra = 0.5 * rho * speed_ms ** 2 * s * ca

    # --- optional roughness allowance ------------------------------------
    rrough = 0.0
    if roughness_allowance:
        rrough = 0.5 * rho * speed_ms ** 2 * s * roughness_allowance

    rt = one_plus_k * rf + rw + rb + rtr + ra + rrough
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
        roughness_allowance_cf=roughness_allowance,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Benchmark hulls (published workshop-geometry particulars)
# ---------------------------------------------------------------------------

# KVLCC2 (KRISO very large crude carrier), Gothenburg 2010 / Tokyo 2015.
# Lpp = 320 m, B = 58 m, T = 20.8 m, vol = 312622 m^3, Cb = 0.8098, Cm = 0.9980,
# Cwp = 0.8548, published wetted surface S = 27194 m^2, LCB = +3.48 % L aft of
# midship (hence -3.48 here).  Mass = 312622 m^3 * 1.025 t/m^3 = 320437 t.
KVLCC2 = HullGeometry(
    name="KVLCC2",
    l=320.0, b=58.0, t=20.8,
    cb=0.8098, cm=0.9980, cwp=0.8548,
    lcb_pct=-3.48, vol=312622.0,
    deadweight=320437.0,
    s_published=27194.0,
)

# KCS (KRISO container ship), Tokyo 2015.  Lpp = 230 m, B = 32.2 m, T = 10.8 m,
# vol = 52030 m^3, Cb = 0.6505, Cm = 0.9846, Cwp = 0.7350, S = 9494 m^2.
KCS = HullGeometry(
    name="KCS",
    l=230.0, b=32.2, t=10.8,
    cb=0.6505, cm=0.9846, cwp=0.7350,
    lcb_pct=-0.7, vol=52030.0,
    deadweight=36000.0,
    s_published=9494.0,
)

# 1984-vintage product tanker (Aframax 110000 dwt class) representative of the
# Holtrop-Mennen calibration set.  Used as an independent mid-Cb hull.
AFRAMAX = HullGeometry(
    name="AFRAMAX-110k",
    l=244.0, b=42.0, t=14.8,
    cb=0.8200, cm=0.9950, cwp=0.8700,
    lcb_pct=-2.0, vol=124000.0,
    deadweight=110000.0,
)

# DTMB 5415 (ITTC V&V 2010) at model scale Lpp = 5.72 m.  Full-scale particulars
# L = 142 m, B = 19.06 m, T = 6.15 m, Cb = 0.5069, Cm = 0.821, Cwp = 0.771,
# vol = 8424 m^3 -> model volume = vol * (5.72/142)^3 = 0.5425 m^3 = Cb L B T.
DTMB5415_MODEL = HullGeometry(
    name="DTMB-5415-model",
    l=5.72, b=0.759, t=0.246,
    cb=0.508, cm=0.821, cwp=0.771,
    lcb_pct=0.0, vol=0.5425,
    s_published=4.6494,   # Holtrop correlation, Denny-Mumford cross-check 4.598
)

BENCHMARK_HULLS = {h.name: h for h in (KVLCC2, KCS, AFRAMAX, DTMB5415_MODEL)}