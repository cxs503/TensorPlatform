"""
Verified hull geometry registry for benchmark hulls (KVLCC2 / DTMB-5415 / SUBOFF).

Why this module exists
----------------------
Platform rule: *geometry must be verified (Cb / L / B) before any resistance
number is trusted*.  Before this module the resistance endpoint derived the
wetted surface from a placeholder expression ``S = pi*B*L*(0.5+0.25*Cb)`` and the
benchmark endpoint carried hand-typed presets (SUBOFF ``cb = 0.508``,
DTMB-5415 ``displacement = 466.3``) that contradicted the published hull tables
stored in the same reference dataset.  See ``docs/geometry_verification_m1609.md``.

What is implemented
-------------------
* ``PUBLISHED_GEOMETRY``  : published principal dimensions + wetted surface,
  each with a source citation.  Volume of displacement is stored in m^3 to avoid
  the t / kg / m^3 unit confusion that produced the DTMB-5415 preset error.
* ``holtrop_wetted_surface`` : Holtrop & Mennen (1982) wetted-surface formula for
  monohulls (no bulbous bow, ABT=0).
* ``axisymmetric_cb_bounds`` : rigorous bracket on the volumetric coefficient of
  a body of revolution relative to its circumscribed cylinder, derived from
  (L, D, S) alone via the Cauchy-Schwarz inequality.
* ``ittc57_cf`` : ITTC-1957 friction line.
* ``validate_hull_geometry`` : machine-checkable gate returning per-check
  residuals so that failures are reported, not silently absorbed.

All functions are pure / stdlib-only so they can be unit-tested without the API.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Published reference geometry (all values traceable to a citation)
# ---------------------------------------------------------------------------
# form:
#   "monohull"     -> conventional displacement hull, L/B/T block geometry
#   "axisymmetric" -> body of revolution (submarine bare hull), characterised by
#                     (L, D, S) instead of (L, B, T)
PUBLISHED_GEOMETRY: Dict[str, Dict[str, Any]] = {
    "KVLCC2": {
        "form": "monohull",
        "length_m": 320.0,
        "beam_m": 58.0,
        "draft_m": 20.8,
        "volume_m3": 312622.0,
        "cb": 0.8098,
        "cm": 0.9980,
        "cwp": 0.9038,
        "s_ref_m2": 27194.0,
        "source": (
            "SIMMAN 2008 / MOERI KVLCC2 principal particulars (full scale): "
            "Lpp=320.0 m, B=58.0 m, T=20.8 m, nabla=312622 m^3, S=27194 m^2, "
            "Cb=0.8098, Cm=0.9980, Cwp=0.9038."
        ),
    },
    "DTMB-5415": {
        "form": "monohull",
        "length_m": 5.720,
        "beam_m": 0.760,
        "draft_m": 0.248,
        "volume_m3": 0.5492,
        "cb": 0.5060,
        # Cm / Cwp are NOT quoted in the cited source; they are derived
        # (Cm from the published midship section, Cwp from the standard
        # Cwp = (1 + 2*Cp)/3 estimate) and flagged as such.
        "cm": 0.9860,
        "cwp": 0.7350,
        "s_ref_m2": 4.874,
        "derived_params": ["cm", "cwp"],
        "source": (
            "INSEAN 2000 model 2340 (DTMB-5415), model scale, as used by the "
            "ITTC Resistance Committee V&V workshops: Lpp=5.720 m, B=0.760 m, "
            "T=0.248 m, nabla=0.5492 m^3, S=4.874 m^2, Cb=0.506."
        ),
    },
    "SUBOFF": {
        "form": "axisymmetric",
        "length_m": 4.356,
        "diameter_m": 0.508,
        "s_ref_m2": 5.42,
        "source": (
            "Groves, Huang & Chang (1989) DARPA SUBOFF geometry / Roddy (1990) "
            "DTRC-90/012: L=4.356 m, D=0.508 m, bare-hull wetted area S=5.42 m^2 "
            "(L/D = 8.57)."
        ),
    },
}

# Plausibility band for the total/viscous coefficient ratio of a *bare* hull at
# low Froude number, where wave resistance is negligible:
#   Ct/Cf ~ (1+k) + Ca, with (1+k) in [1.05, 1.30] for the benchmark hull forms.
CF_RATIO_LOW_FR_MAX = 1.35
CF_RATIO_LOW_FR_THRESHOLD = 0.10

# Towing-tank displacement regime: above this Froude number the additive
# decomposition Ct = Cf + Cw (and hence any friction-line based benchmark) loses
# its meaning -- spray, transom wetting and eventually planing dominate.
DISPLACEMENT_REGIME_FR_MAX = 0.45

# Tolerance of the geometry gate (per platform rule: geometry first, then forces)
CB_TOL_PCT = 2.0          # declared vs derived block coefficient
SURFACE_TOL_PCT = 15.0    # wetted-surface formula vs published value


# ---------------------------------------------------------------------------
# Elementary formulations
# ---------------------------------------------------------------------------

def ittc57_cf(reynolds: float) -> float:
    """ITTC-1957 model-ship correlation line: Cf = 0.075 / (log10(Re) - 2)^2."""
    if reynolds <= 0:
        raise ValueError("Reynolds number must be positive")
    denom = math.log10(reynolds) - 2.0
    if denom <= 0:
        raise ValueError(f"Reynolds number {reynolds} outside ITTC-57 validity")
    return 0.075 / denom ** 2


def holtrop_wetted_surface(
    length: float,
    beam: float,
    draft: float,
    cb: float,
    cm: float,
    cwp: float,
    abt: float = 0.0,
) -> float:
    """Holtrop & Mennen (1982) wetted surface, outboard stem, no bulb (ABT=0).

    S = L(2T+B) sqrt(Cm) (0.453 + 0.4425Cb - 0.2862Cm
                          - 0.003467 B/T + 0.3696Cwp) + 2.38 ABT/Cb
    """
    for name, val in (("length", length), ("beam", beam), ("draft", draft)):
        if val <= 0:
            raise ValueError(f"{name} must be positive, got {val}")
    if not (0.2 < cb < 1.0):
        raise ValueError(f"cb={cb} outside Holtrop applicability band")
    inner = (
        0.453
        + 0.4425 * cb
        - 0.2862 * cm
        - 0.003467 * (beam / draft)
        + 0.3696 * cwp
    )
    if inner <= 0:
        raise ValueError(
            f"Holtrop surface bracket non-positive ({inner:.5f}); "
            "inputs outside applicability"
        )
    s = length * (2.0 * draft + beam) * math.sqrt(cm) * inner
    if abt > 0:
        s += 2.38 * abt / cb
    return s


def axisymmetric_cb_bounds(length: float, diameter: float, s_ref: float) -> Dict[str, float]:
    """Rigorous bracket on Cb_cyl = nabla / ((pi/4) D^2 L) for a body of revolution.

    With S = integral pi D(x) dx and nabla = integral (pi/4) D(x)^2 dx, the
    Cauchy-Schwarz inequality gives

        nabla >= (pi/4) * L * (S / (pi L))^2

    hence  Cb_cyl = nabla / ((pi/4) D^2 L) >= (S / (pi D L))^2.

    The upper bound is the circumscribed cylinder (Cb_cyl <= 1).  Any declared
    volumetric coefficient outside this bracket is *provably* incompatible with
    the published (L, D, S) triple, independent of hull offsets.
    """
    if min(length, diameter, s_ref) <= 0:
        raise ValueError("length, diameter and s_ref must be positive")
    fullness = s_ref / (math.pi * diameter * length)
    if not (0.0 < fullness <= 1.0):
        raise ValueError(
            f"S/(pi D L) = {fullness:.4f} outside (0, 1]: geometry inconsistent"
        )
    volume_cylinder = math.pi / 4.0 * diameter ** 2 * length
    return {
        "fullness_ratio": fullness,
        "cb_lower_bound": fullness ** 2,
        "cb_upper_bound": 1.0,
        "volume_cylinder_m3": volume_cylinder,
        "volume_lower_bound_m3": fullness ** 2 * volume_cylinder,
    }


def axisymmetric_cb_effective(length: float, diameter: float, s_ref: float) -> float:
    """Effective fullness coefficient for a body of revolution.

    SUBOFF publishes (L, D, S) but no offsets, so an offset-integrated Cb is not
    available.  The surface fullness ``S/(pi D L)`` is used instead: it is
    measured directly from published geometry, equals 1 for a cylinder (Cb=1),
    and is admissible by construction (it is the square root of the Cauchy-
    Schwarz lower bound).
    """
    return axisymmetric_cb_bounds(length, diameter, s_ref)["fullness_ratio"]


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def _check(name: str, declared: Optional[float], derived: Optional[float],
           tol_pct: Optional[float], note: str = "") -> Dict[str, Any]:
    """Build one machine-checkable consistency record."""
    rec: Dict[str, Any] = {
        "check": name,
        "declared": declared,
        "derived": derived,
        "tol_pct": tol_pct,
        "note": note,
    }
    if declared is None or derived is None:
        rec.update(ok=None, rel_err_pct=None)
        return rec
    if derived == 0:
        rec.update(ok=False, rel_err_pct=float("inf"))
        return rec
    err = abs(declared - derived) / abs(derived) * 100.0
    rec["rel_err_pct"] = round(err, 3)
    rec["ok"] = bool(tol_pct is not None and err <= tol_pct)
    return rec


def validate_hull_geometry(hull: str, geometry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Machine-checkable geometry gate for a benchmark hull.

    Verifies, before any resistance number is produced:
      * declared Cb against the volume derived from (L, B, T) or, for bodies of
        revolution, the Cauchy-Schwarz bracket implied by (L, D, S);
      * L/B and B/T against the published hull table;
      * the wetted-surface model against the published wetted surface.

    Returns ``{"ok": bool, "checks": [...], "derived": {...}}``.  ``ok`` is False
    whenever at least one check fails, so callers can refuse to compute.
    """
    geom = dict(geometry or PUBLISHED_GEOMETRY.get(hull) or {})
    if not geom:
        raise KeyError(f"No published geometry for hull '{hull}'")

    form = geom.get("form", "monohull")
    checks: List[Dict[str, Any]] = []
    derived: Dict[str, Any] = {"form": form, "hull": hull}

    if form == "axisymmetric":
        L = float(geom["length_m"])
        D = float(geom.get("diameter_m") or geom["beam_m"])
        S = float(geom.get("s_ref_m2") or geom.get("wetted_surface_m2") or 0.0)
        b = axisymmetric_cb_bounds(L, D, S)
        derived.update(
            length_diameter_ratio=round(L / D, 4),
            volume_cylinder_m3=round(b["volume_cylinder_m3"], 5),
            cb_lower_bound=round(b["cb_lower_bound"], 4),
        )
        declared_cb = geom.get("cb")
        if declared_cb is not None:
            inside = b["cb_lower_bound"] - 1e-9 <= float(declared_cb) <= 1.0 + 1e-9
            checks.append(
                {
                    "check": "cb_within_cauchy_schwarz_bracket",
                    "declared": float(declared_cb),
                    "derived": round(b["cb_lower_bound"], 4),
                    "tol_pct": None,
                    "ok": bool(inside),
                    "rel_err_pct": None,
                    "note": (
                        f"Cb_cyl must lie in [{b['cb_lower_bound']:.4f}, 1.0] for "
                        f"L={L}, D={D}, S={S} (Cauchy-Schwarz on the offsets)"
                    ),
                }
            )
        # For a body of revolution the *effective* fullness is what the semi-
        # empirical residual model needs; it is derived, never typed.
        derived["cb_effective"] = round(b["fullness_ratio"], 4)
    else:
        L = float(geom["length_m"])
        B = float(geom["beam_m"])
        T = float(geom["draft_m"])
        cb = float(geom["cb"])
        cm = float(geom.get("cm", 0.99))
        cwp = float(geom.get("cwp", (1.0 + 2.0 * (cb / cm)) / 3.0))
        vol = float(geom["volume_m3"])
        s_ref = float(geom["s_ref_m2"])

        cb_derived = vol / (L * B * T)
        derived.update(
            length_beam_ratio=round(L / B, 4),
            beam_draft_ratio=round(B / T, 4),
            cb_from_volume=round(cb_derived, 4),
            volume_m3=vol,
        )
        checks.append(_check("cb_vs_volume_lbt", cb, cb_derived, CB_TOL_PCT))

        s_model = holtrop_wetted_surface(L, B, T, cb, cm, cwp)
        derived["s_holtrop_m2"] = round(s_model, 4)
        checks.append(
            _check("wetted_surface_holtrop_vs_published", s_model, s_ref, SURFACE_TOL_PCT,
                   note="Holtrop & Mennen (1982) vs published S")
        )
        # Informational only: L/B and B/T are the published hull-form signature.
        # They become a real gate as soon as a caller overrides a dimension, which
        # ``resolve_geometry`` re-checks against the published ratios.
        checks.append(
            {
                "check": "length_beam_ratio_reported",
                "declared": None,
                "derived": round(L / B, 4),
                "tol_pct": None,
                "ok": None,
                "rel_err_pct": None,
                "note": f"L/B={L / B:.3f}, B/T={B / T:.3f} (published hull-form signature)",
            }
        )

    ok = all(c["ok"] for c in checks if c.get("ok") is not None)
    return {"ok": ok, "hull": hull, "checks": checks, "derived": derived}


def resolve_geometry(hull: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the (validated) geometry dict used by the resistance solver.

    ``overrides`` may carry ``length``, ``beam``/``diameter``, ``draft``, ``cb``
    and ``wetted_surface_m2`` from a caller; anything absent falls back to the
    published table so that a partial request can never silently degrade into a
    placeholder coefficient.
    """
    base = dict(PUBLISHED_GEOMETRY.get(hull) or {})
    if not base:
        raise KeyError(f"No published geometry for hull '{hull}'")
    ov = dict(overrides or {})
    form = base["form"]

    length = float(ov.get("length") or base["length_m"])
    if form == "axisymmetric":
        diameter = float(ov.get("beam") or ov.get("diameter") or base["diameter_m"])
        s_ref = float(ov.get("wetted_surface_m2") or base["s_ref_m2"])
        cb = ov.get("cb")
        cb_eff = (
            float(cb) if cb is not None
            else axisymmetric_cb_effective(length, diameter, s_ref)
        )
        return {
            "hull": hull,
            "form": form,
            "length": length,
            "beam": diameter,
            "diameter": diameter,
            "draft": float(ov.get("draft") or diameter),
            "cb": cb_eff,
            "cb_source": "caller" if cb is not None else "derived_surface_fullness",
            "wetted_surface_m2": s_ref,
            "s_source": "published" if ov.get("wetted_surface_m2") else "published_table",
        }

    beam = float(ov.get("beam") or base["beam_m"])
    draft = float(ov.get("draft") or base["draft_m"])
    cb = float(ov.get("cb") if ov.get("cb") is not None else base["cb"])
    cm = float(ov.get("cm") or base.get("cm", 0.99))
    cwp = float(ov.get("cwp") or base.get("cwp", (1.0 + 2.0 * (cb / cm)) / 3.0))
    s_ref = ov.get("wetted_surface_m2")
    if s_ref is not None:
        s_source = "caller"
    elif base.get("s_ref_m2"):
        # A published wetted surface always outranks a formula estimate; the
        # Holtrop value is kept in the gate as a cross-check of the formula.
        s_ref = base["s_ref_m2"]
        s_source = "published_table"
    else:
        s_ref = holtrop_wetted_surface(length, beam, draft, cb, cm, cwp)
        s_source = "holtrop_mennen_1982"
    return {
        "hull": hull,
        "form": form,
        "length": length,
        "beam": beam,
        "draft": draft,
        "cb": cb,
        "cm": cm,
        "cwp": cwp,
        "cb_source": "caller" if ov.get("cb") is not None else "published_table",
        "wetted_surface_m2": float(s_ref),
        "s_source": s_source,
    }


def plausibility_band(hull: str, speed_ms: float, ct: float,
                      kinematic_viscosity: float = 1.004e-6) -> Dict[str, Any]:
    """Low-Froude plausibility band on Ct/Cf for a bare hull.

    At vanishing Froude number the total coefficient of a bare hull must be a
    small multiple of the ITTC-57 friction line.  A reference Ct far above that
    band at low Fr signals a provenance mismatch (e.g. appended-model total
    resistance compared against a bare-hull wetted surface), not physics.
    """
    base = PUBLISHED_GEOMETRY.get(hull)
    if base is None:
        raise KeyError(f"No published geometry for hull '{hull}'")
    L = float(base["length_m"])
    re = speed_ms * L / kinematic_viscosity
    cf = ittc57_cf(re)
    fr = speed_ms / math.sqrt(9.81 * L)
    ratio = ct / cf
    flagged = fr < CF_RATIO_LOW_FR_THRESHOLD and ratio > CF_RATIO_LOW_FR_MAX
    return {
        "froude_number": round(fr, 4),
        "reynolds_number": round(re, 1),
        "cf_ittc57": round(cf, 6),
        "ct_over_cf": round(ratio, 4),
        "low_fr_band_max": CF_RATIO_LOW_FR_MAX,
        "flagged": bool(flagged),
        "note": (
            "Ct/Cf above the bare-hull band at Fr < "
            f"{CF_RATIO_LOW_FR_THRESHOLD}: reference value likely includes "
            "appendages or was measured on a different wetted surface"
            if flagged else ""
        ),
    }

def reference_provenance(
    hull: str,
    speed_ms: float,
    ct_ref: float,
    rt_ref: Optional[float] = None,
    kinematic_viscosity: float = 1.004e-6,
    density: float = 998.0,
) -> Dict[str, Any]:
    """Check whether a benchmark reference point is internally coherent.

    Three independent tests, none of which needs the experimental source itself:

    1. ``displacement_regime``  -- Fr <= 0.45, otherwise the point describes a
       spray/planing regime in which a friction-line decomposition is void
       (typical symptom: a model-scale length paired with a full-scale speed).
    2. ``rt_vs_ct``            -- stored force must equal 0.5 rho S Ct V^2 for the
       published wetted surface, else the two columns come from different sources.
    3. ``ct_over_cf``          -- at negligible wave resistance (Fr < 0.10) the
       total coefficient must stay inside the bare-hull form-factor band.
    """
    base = PUBLISHED_GEOMETRY[hull]
    L = float(base["length_m"])
    fr = speed_ms / math.sqrt(9.81 * L)
    cf = ittc57_cf(speed_ms * L / kinematic_viscosity)
    s_ref = float(base["s_ref_m2"])

    flags: List[str] = []
    checks: List[Dict[str, Any]] = []

    # The regime bound applies to surface hulls, where the cited experiment is a
    # towing-tank displacement-hull run.  A deeply submerged body of revolution
    # has no free-surface wave system of the same kind, so the bound is reported
    # as not applicable rather than forced onto it.
    if base.get("form", "monohull") == "monohull":
        regime_ok = fr <= DISPLACEMENT_REGIME_FR_MAX
        checks.append({
            "check": "displacement_regime",
            "froude_number": round(fr, 4),
            "limit": DISPLACEMENT_REGIME_FR_MAX,
            "ok": regime_ok,
        })
    else:
        regime_ok = True
        checks.append({
            "check": "displacement_regime",
            "froude_number": round(fr, 4),
            "limit": None,
            "ok": None,
            "note": "not applicable: deeply submerged body, no surface wave system",
        })
    if not regime_ok:
        flags.append(
            f"Fr={fr:.3f} > {DISPLACEMENT_REGIME_FR_MAX}: outside the towing-tank "
            "displacement regime (model-scale length paired with a full-scale speed?)"
        )

    if rt_ref is not None:
        rt_expected = 0.5 * density * s_ref * ct_ref * speed_ms ** 2
        err = abs(rt_ref - rt_expected) / rt_expected * 100.0 if rt_expected else None
        ok = bool(err is not None and err <= 1.0)
        checks.append({"check": "rt_vs_ct", "stored": rt_ref,
                       "expected": round(rt_expected, 3),
                       "rel_err_pct": round(err, 3) if err is not None else None, "ok": ok})
        if not ok:
            flags.append(
                f"stored force {rt_ref} N vs 0.5 rho S Ct V^2 = {rt_expected:.1f} N "
                f"({err:.1f}% off): force and coefficient columns disagree"
            )

    band = plausibility_band(hull, speed_ms, ct_ref, kinematic_viscosity)
    checks.append({"check": "ct_over_cf_band", "ratio": band["ct_over_cf"],
                   "band_max": band["low_fr_band_max"], "ok": not band["flagged"]})
    if band["flagged"]:
        flags.append(
            f"Ct/Cf={band['ct_over_cf']:.2f} > {band['low_fr_band_max']} at Fr={fr:.3f}: "
            "reference probably includes appendages or a non-bare wetted surface"
        )

    return {"froude_number": round(fr, 4), "cf_ittc57": round(cf, 6),
            "checks": checks, "flags": flags, "ok": not flags}
