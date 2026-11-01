"""
Canonical calm-water resistance model (single source of truth).

Why this module exists
----------------------
Before this module the platform had *two* divergent resistance implementations:

* ``api/calculations.py`` used a crude axisymmetric wetted-surface formula
  ``S = pi*B*L*(0.5 + 0.25*Cb)`` plus an uncited residual-resistance curve fit
  ``Cr = 0.002*(1+2*Cb)*Fn**2.5``;
* ``api/domain/ship_benchmark.py`` claimed to validate "Holtrop/ITTC-57" but
  actually forwarded HTTP to a non-existent port-8002 service and read a
  ``ct`` / ``method`` field that the real endpoint never returns, so every
  benchmark case degenerated to ``calc_ct = 0.0`` (100 % error) or a 999 %
  "API call failed" placeholder.

Both paths now delegate here, so the number the user sees and the number the
benchmark gate judges are the *same* number.

Evidence grading (per component)
--------------------------------
======================  ====================================================
component               grade / basis
======================  ====================================================
``ittc57_cf``           REAL — ITTC-1957 model-ship correlation line,
                        closed form, cross-checked against the independent
                        Schoenherr line (``schoenherr_cf``).
``schoenherr_cf``       REAL — Schoenherr (1932) line solved by bisection;
                        used only as an independent cross-check.
``wetted_surface``      REAL for displacement hulls — Holtrop & Mennen (1982)
                        wetted-surface regression (reproduced below).
``form_factor``         ESTIMATE — class defaults / Hoerner's closed-form for
                        bodies of revolution. Callers should pass a value when
                        one is known (model tests, CFD).
``ittc78_ca``           REAL — ITTC-1978 recommended correlation allowance.
``wave_resistance``     NOT_SUPPORTED — no wave/residual resistance model is
                        implemented here. The returned ``residual_coeff`` is
                        whatever the caller supplies (default 0), and
                        ``evidence["wave"] == "NOT_SUPPORTED"``. Any total-
                        resistance number from this module is therefore a
                        **viscous lower bound**; the shortfall against
                        experiment is reported, never hidden.
======================  ====================================================

References
----------
* Holtrop, J. & Mennen, G.G.J. (1982) "An approximate power prediction method",
  International Shipbuilding Progress 29(335).
* ITTC (1957) model-ship correlation line; ITTC (1978) performance prediction
  method (correlation allowance).
* Schoenherr, K.E. (1932) Trans. SNAME 40.
* Hoerner, S.F. (1965) "Fluid-Dynamic Drag", ch. 6 (form factor of bodies of
  revolution).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

G = 9.81
RHO_FRESH = 998.0
NU_FRESH = 1.004e-6


# ---------------------------------------------------------------------------
# Friction lines
# ---------------------------------------------------------------------------

def ittc57_cf(re: float) -> float:
    """ITTC-1957 model-ship correlation line C_F = 0.075/(log10 Re - 2)^2.

    Valid for 10^4 < Re < 10^10 (validity statement of the 1957 line).
    """
    if re <= 0:
        raise ValueError(f"Reynolds number must be positive, got {re}")
    denom = math.log10(re) - 2.0
    if denom <= 0:
        raise ValueError(f"Reynolds number {re} below ITTC-57 validity (log10 Re <= 2)")
    return 0.075 / denom ** 2


def schoenherr_cf(re: float) -> float:
    """Schoenherr (1932) line: 0.242/sqrt(C_F) = log10(Re * C_F).

    Solved by bisection. Independent of ITTC-57 and used only as a
    cross-check of the friction line (they agree to a few percent at high Re).
    """
    if re <= 0:
        raise ValueError(f"Reynolds number must be positive, got {re}")

    def residual(cf: float) -> float:
        return 0.242 / math.sqrt(cf) - math.log10(re * cf)

    lo, hi = 1e-5, 5e-2
    f_lo = residual(lo)  # positive (0.242/sqrt small is huge)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = residual(mid)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def ittc78_ca(re: float) -> float:
    """ITTC-1978 correlation allowance C_A = (5.68 - 0.6*log10(Re)) * 1e-3.

    Clamped at zero: the regression is only meaningful for full-scale
    Reynolds numbers (Re >~ 10^8); at model scale it must not be applied.
    """
    ca = (5.68 - 0.6 * math.log10(re)) * 1e-3
    return max(0.0, ca)


# ---------------------------------------------------------------------------
# Wetted surface
# ---------------------------------------------------------------------------

def wetted_surface_holtrop(
    length: float,
    beam: float,
    draft: float,
    cb: float,
    cm: Optional[float] = None,
    cwp: Optional[float] = None,
    transom_area: float = 0.0,
) -> float:
    """Wetted surface of a displacement hull, Holtrop & Mennen (1982).

        S = L(2T + B) sqrt(Cm) *
            (0.453 + 0.4425 Cb - 0.2862 Cm - 0.003467 B/T + 0.3696 Cwp)
            + 2.38 A_bt / Cb

    ``Cm`` / ``Cwp`` may be supplied by the caller; when omitted they come
    from :func:`default_midship_waterplane` (labelled ESTIMATE).
    """
    for name, value in (("length", length), ("beam", beam), ("draft", draft), ("cb", cb)):
        if value <= 0:
            raise ValueError(f"{name} must be positive, got {value}")
    cm_d, cwp_d = default_midship_waterplane(cb)
    cm = cm_d if cm is None else cm
    cwp = cwp_d if cwp is None else cwp
    if not (0.2 < cm <= 1.0):
        raise ValueError(f"midship coefficient Cm={cm} outside (0.2, 1.0]")
    if not (0.2 < cwp <= 1.0):
        raise ValueError(f"waterplane coefficient Cwp={cwp} outside (0.2, 1.0]")

    s = (
        length * (2.0 * draft + beam) * math.sqrt(cm)
        * (
            0.453
            + 0.4425 * cb
            - 0.2862 * cm
            - 0.003467 * (beam / draft)
            + 0.3696 * cwp
        )
        + 2.38 * transom_area / cb
    )
    if s <= 0:
        raise ValueError(
            f"Holtrop wetted surface non-positive (S={s}); check Cb={cb}, B/T={beam/draft}"
        )
    return s


def wetted_surface_box_bound(length: float, beam: float, draft: float) -> float:
    """Wetted surface of the box hull (Cb = 1 limit) enclosing the hull.

    bottom + two sides + two ends = L*B + 2*L*T + 2*B*T.
    A physical hull inscribed in the box must not exceed it, which is why this
    bound is used as a hard sanity gate for wetted-surface formulas.
    """
    return length * beam + 2.0 * length * draft + 2.0 * beam * draft


def default_midship_waterplane(cb: float) -> tuple[float, float]:
    """ESTIMATE midship (Cm) and waterplane (Cwp) coefficients from Cb alone.

    * ``Cwp = (1 + 2*Cb)/3`` — standard naval-architecture approximation.
    * ``Cm`` follows the usual block-coefficient trend for displacement hulls
      (Cm -> ~0.85 at Cb ~ 0.5, saturating at 0.999 for full forms) and is
      clipped to the physical maximum 1.0.

    Both are ESTIMATE-grade: supply measured values when available.
    """
    cm = 0.60 + 0.50 * cb
    cm = min(0.999, max(0.80, cm))
    cwp = (1.0 + 2.0 * cb) / 3.0
    return cm, cwp


# ---------------------------------------------------------------------------
# Form factor
# ---------------------------------------------------------------------------

def form_factor_body_of_revolution(diameter: float, length: float) -> float:
    """Hoerner (1965) form factor 1+k for a streamlined body of revolution.

        1 + k = 1 + 1.5 (D/L)^1.5 + 7 (D/L)^3

    REAL (closed form), but it strictly applies to bodies of revolution, not
    to displacement hulls.
    """
    if length <= 0 or diameter <= 0:
        raise ValueError("diameter and length must be positive")
    d_over_l = diameter / length
    return 1.0 + 1.5 * d_over_l ** 1.5 + 7.0 * d_over_l ** 3


#: ESTIMATE class defaults for the viscous form factor of displacement hulls.
#: Single-screw merchant hulls typically fall in 1.10-1.20 (model tests/CFD).
FORM_FACTOR_CLASS_DEFAULT = (
    (0.60, 1.08),
    (0.70, 1.12),
    (0.80, 1.16),
    (1.00, 1.20),
)


def form_factor_ship_default(cb: float) -> float:
    """ESTIMATE form factor for a displacement hull, interpolated on Cb."""
    if cb <= FORM_FACTOR_CLASS_DEFAULT[0][0]:
        return FORM_FACTOR_CLASS_DEFAULT[0][1]
    for (cb0, f0), (cb1, f1) in zip(FORM_FACTOR_CLASS_DEFAULT,
                                    FORM_FACTOR_CLASS_DEFAULT[1:]):
        if cb <= cb1:
            t = (cb - cb0) / (cb1 - cb0)
            return f0 + t * (f1 - f0)
    return FORM_FACTOR_CLASS_DEFAULT[-1][1]


# ---------------------------------------------------------------------------
# Full model
# ---------------------------------------------------------------------------

@dataclass
class ResistancePrediction:
    speed_ms: float
    length: float
    wetted_surface_m2: float
    reynolds_number: float
    froude_number: float
    cf_ittc57: float
    cf_schoenherr: float
    form_factor: float
    ca: float
    ct_viscous: float            # (1+k) Cf + CA  -> viscous lower bound
    residual_coeff: float        # caller-supplied (wave/viscous-pressure + roughness)
    ct_total: float
    rt_viscous_n: float
    rt_total_n: float
    hull_class: str
    evidence: Dict[str, str] = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "speed_ms": round(self.speed_ms, 6),
            "length": round(self.length, 4),
            "wetted_surface_m2": round(self.wetted_surface_m2, 4),
            "reynolds_number": round(self.reynolds_number, 2),
            "froude_number": round(self.froude_number, 6),
            "cf_ittc57": round(self.cf_ittc57, 8),
            "cf_schoenherr": round(self.cf_schoenherr, 8),
            "form_factor": round(self.form_factor, 4),
            "correlation_allowance": round(self.ca, 8),
            "ct_viscous": round(self.ct_viscous, 8),
            "residual_coeff": round(self.residual_coeff, 8),
            "ct_total": round(self.ct_total, 8),
            "rt_viscous_n": round(self.rt_viscous_n, 3),
            "rt_total_n": round(self.rt_total_n, 3),
            "hull_class": self.hull_class,
            "method": "ittc57+form_factor+ca",
            "evidence": dict(self.evidence),
            "notes": list(self.notes),
        }


def predict_resistance(
    length: float,
    speed_ms: float,
    beam: Optional[float] = None,
    draft: Optional[float] = None,
    cb: Optional[float] = None,
    *,
    wetted_surface_m2: Optional[float] = None,
    form_factor: Optional[float] = None,
    cm: Optional[float] = None,
    cwp: Optional[float] = None,
    transom_area: float = 0.0,
    residual_coeff: Optional[float] = None,
    rho: float = RHO_FRESH,
    nu: float = NU_FRESH,
    hull_class: Optional[str] = None,
    apply_ca: bool = True,
) -> ResistancePrediction:
    """Predict calm-water resistance with an explicit, honest evidence trail.

    Parameters
    ----------
    length : reference length (LWL for displacement hulls) in metres.
    speed_ms : speed in m/s.
    beam, draft, cb : hull block descriptors (required unless
        ``wetted_surface_m2`` is supplied).
    wetted_surface_m2 : measured/computed wetted surface; overrides the
        Holtrop regression.
    form_factor : 1+k. When omitted: Hoerner's closed form for axisymmetric
        hulls, class default for displacement hulls.
    residual_coeff : C_W + dCf (wave, pressure, roughness) added to the
        viscous coefficient. Default 0 => the result is a viscous lower bound
        and ``evidence["wave"] == "NOT_SUPPORTED"``.
    hull_class : "ship" | "axisymmetric"; inferred when omitted.

    Returns
    -------
    ResistancePrediction (see :meth:`ResistancePrediction.as_dict`).
    """
    if length <= 0:
        raise ValueError("length must be positive")
    if speed_ms <= 0:
        raise ValueError("speed_ms must be positive")
    if rho <= 0 or nu <= 0:
        raise ValueError("rho and nu must be positive")

    notes: list = []
    if hull_class is None:
        if beam is not None and draft is not None and abs(beam - draft) / length < 1e-3:
            hull_class = "axisymmetric"
        else:
            hull_class = "ship"

    # --- wetted surface -----------------------------------------------------
    if wetted_surface_m2 is not None:
        if wetted_surface_m2 <= 0:
            raise ValueError("wetted_surface_m2 must be positive")
        s_wet = float(wetted_surface_m2)
        evidence_surface = "REAL(measured/computed input)"
    elif hull_class == "ship":
        if beam is None or draft is None or cb is None:
            raise ValueError("ship hull_class requires beam, draft and cb (or wetted_surface_m2)")
        _check_block_coefficient(cb)
        s_wet = wetted_surface_holtrop(length, beam, draft, cb, cm=cm, cwp=cwp,
                                      transom_area=transom_area)
        evidence_surface = "REAL(Holtrop&Mennen 1982 regression)"
    else:
        if beam is None:
            raise ValueError("axisymmetric hull_class requires beam (=diameter) or wetted_surface_m2")
        # ESTIMATE: pi*D*L*kappa, kappa from streamlined bodies of revolution.
        kappa = 0.78
        s_wet = math.pi * beam * length * kappa
        evidence_surface = "ESTIMATE(pi*D*L*0.78)"
        notes.append(
            "axisymmetric wetted surface defaulted to pi*D*L*0.78 (ESTIMATE); "
            "pass wetted_surface_m2 for a REAL value"
        )

    # --- friction, form factor, allowance -----------------------------------
    re = speed_ms * length / nu
    fn = speed_ms / math.sqrt(G * length)
    cf = ittc57_cf(re)
    cf_sch = schoenherr_cf(re)

    if form_factor is not None:
        one_plus_k = float(form_factor)
        evidence_ff = "REAL(user input)"
    elif hull_class == "axisymmetric":
        if beam is None:
            raise ValueError("axisymmetric hull_class requires beam (=diameter) for Hoerner form factor")
        one_plus_k = form_factor_body_of_revolution(beam, length)
        evidence_ff = "REAL(Hoerner 1965 closed form)"
    else:
        if cb is None:
            raise ValueError("ship hull_class requires cb for the default form factor")
        one_plus_k = form_factor_ship_default(cb)
        evidence_ff = "ESTIMATE(class default on Cb)"
        notes.append(
            "form factor defaulted from Cb class table (ESTIMATE); "
            "case C_T error bracket should be read over 1+k in [1.05, 1.25]"
        )
    if one_plus_k < 1.0:
        raise ValueError(f"form factor 1+k={one_plus_k} < 1 is not physical")

    ca = ittc78_ca(re) if apply_ca else 0.0
    c_res = 0.0 if residual_coeff is None else float(residual_coeff)

    ct_visc = one_plus_k * cf + ca
    ct_total = ct_visc + c_res

    q = 0.5 * rho * speed_ms ** 2
    rt_visc = q * s_wet * ct_visc
    rt_total = q * s_wet * ct_total

    evidence = {
        "friction": "REAL(ITTC-1957, cross-checked vs Schoenherr)",
        "wetted_surface": evidence_surface,
        "form_factor": evidence_ff,
        "correlation_allowance": "REAL(ITTC-1978)" if apply_ca else "disabled",
        "wave": "NOT_SUPPORTED" if residual_coeff is None else "USER-SUPPLIED(empirical)",
    }
    if residual_coeff is None:
        notes.append(
            "no wave/residual resistance model: ct_total is a viscous lower bound "
            f"(C_W = 0). Measured shortfall must be reported, not hidden."
        )

    return ResistancePrediction(
        speed_ms=speed_ms,
        length=length,
        wetted_surface_m2=s_wet,
        reynolds_number=re,
        froude_number=fn,
        cf_ittc57=cf,
        cf_schoenherr=cf_sch,
        form_factor=one_plus_k,
        ca=ca,
        ct_viscous=ct_visc,
        residual_coeff=c_res,
        ct_total=ct_total,
        rt_viscous_n=rt_visc,
        rt_total_n=rt_total,
        hull_class=hull_class,
        evidence=evidence,
        notes=notes,
    )


def legacy_predict(length: float, beam: float, draft: float, cb: float, speed_ms: float,
                   rho: float = RHO_FRESH, nu: float = NU_FRESH) -> Dict[str, float]:
    """The pre-fix ``/calculations/resistance`` model, kept for regression tests.

    Documented defects (see docs/validation/RESISTANCE_VALIDATION.md):

    * ``S = pi*B*L*(0.5 + 0.25*Cb)`` is an axisymmetric-body formula; for a
      full hull it can exceed the wetted surface of the enclosing box hull
      (:func:`wetted_surface_box_bound`) — physically impossible.
    * The residual term ``2e-3 (1 + 2 Cb) Fn^2.5`` has no published source.
    * No form factor and no correlation allowance.
    """
    s = math.pi * beam * length * (0.5 + 0.25 * cb)
    re = speed_ms * length / nu
    cf = ittc57_cf(re)
    fn = speed_ms / math.sqrt(G * length)
    cr = 0.002 * (1.0 + 2.0 * cb) * fn ** 2.5 if fn > 0.05 else 0.0
    q = 0.5 * rho * speed_ms ** 2
    return {
        "wetted_surface_m2": s,
        "reynolds_number": re,
        "froude_number": fn,
        "cf": cf,
        "residual_coeff": cr,
        "ct_total": cf + cr,
        "rt_total_n": q * s * (cf + cr),
    }


def _check_block_coefficient(cb: float) -> None:
    if not (0.3 < cb < 1.0):
        raise ValueError(f"Cb={cb} out of physical range (0.3, 1.0)")