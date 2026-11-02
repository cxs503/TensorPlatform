"""
Resistance Evaluation Pipeline (m547 interface, physics delegated in m1610)

m1610 change (supersedes the m547/m549 hand-fitted kernel)
----------------------------------------------------------
The calm-water resistance of the optimizer objective is now computed by the
**canonical** Holtrop-Mennen (1984) kernel that also serves
``POST /calculations/resistance``: :mod:`backend.api.holtrop1984` (landed by
agent/m1553), reached through :mod:`optimizer.interfaces.canonical_bridge`.
No coefficient chain is re-implemented on the optimizer side (report 1144:
multiple branches of the same chain drift apart; one implementation only).

Geometry (``cm``/``cwp``/``lcb_pct``/``vol``/``abt``/``hb``/``at``) is supplied by
:mod:`optimizer.interfaces.kcs_bow_param`; the eight bow parameters act through the
pathways that actually exist in the method (waterplane fullness, bulb area, bulb
height, LCB), never through ad-hoc resistance multipliers.

Evidence grading
----------------
* :func:`holtrop_resistance` -> ``evidence_level="REAL"`` when the canonical kernel
  answered (REAL = published method, full coefficient chain, dimensions checked).
* The pre-m1610 hand-fitted kernel is kept **only** as
  :func:`_legacy_holtrop_resistance` (fallback when the canonical kernel cannot be
  loaded).  It stays stamped ``evidence_level="MOCK"`` because its residual/wave
  terms are collapsed by the exponential forms (Ct == ITTC-57 Cf to machine
  precision) and its correlation allowance ``Ca = 0.006 (L+100)^-0.16`` is added to
  the force sum in m^2 instead of being made dimensionless.
* :func:`evaluate_resistance` propagates whichever grade was actually used as
  ``objective_evidence_level`` / ``objective_physics_source`` so that no downstream
  consumer can mistake a fallback number for a REAL one.

Interface contract (m547): geometry dict (from m546) + ship speed -> resistance
components and total Ct, with a Holtrop calibration-domain check.

Reference: Holtrop J, Mennen GGA. "An approximate power prediction method",
           International Shipbuilding Progress, 1982; Holtrop J., "A statistical
           re-analysis of resistance and propulsion data", ISP 31 (363), 1984.
"""
import numpy as np
from typing import Dict, Any, Tuple

from .canonical_bridge import canonical_kernel, estimate_cm, estimate_cwp

# Physical constants (identical to the canonical kernel; kept for the legacy path)
RHO_WATER = 1025.0  # Seawater density [kg/m³]
NU_WATER = 1.19e-6  # Kinematic viscosity [m²/s] (15°C seawater)
G = 9.81  # Gravity [m/s²]

# Holtrop calibration domain (validity limits)
# Adjusted for KCS model scale (L=7.278m, B=1.019m, T=0.342m)
HOLTROP_DOMAIN = {
    "Cb": (0.55, 0.75),      # Block coefficient
    "L_B": (3.0, 8.0),       # Length/Beam ratio (expanded for KCS)
    "B_T": (2.0, 4.0),       # Beam/Draft ratio
    "Fn": (0.10, 0.35),      # Froude number
}

LEGACY_SOURCE = (
    "optimizer/interfaces/resistance_eval.py::_legacy_holtrop_resistance "
    "(hand-fitted fallback: Ct == ITTC-57 Cf, Ca mixed units)"
)


def check_holtrop_domain(geom: Dict[str, Any], Fn: float) -> Tuple[bool, str]:
    """
    Check if geometry and speed are within Holtrop calibration domain.

    Args:
        geom: Geometry dictionary
        Fn: Froude number

    Returns:
        (in_domain, reason) tuple
    """
    L = geom["L"]
    B = geom["B"]
    T = geom["T"]
    Cb = geom["Cb"]

    L_B = L / B
    B_T = B / T

    checks = [
        (HOLTROP_DOMAIN["Cb"][0] <= Cb <= HOLTROP_DOMAIN["Cb"][1],
         f"Cb={Cb:.3f} out of [{HOLTROP_DOMAIN['Cb']}]"),
        (HOLTROP_DOMAIN["L_B"][0] <= L_B <= HOLTROP_DOMAIN["L_B"][1],
         f"L/B={L_B:.2f} out of [{HOLTROP_DOMAIN['L_B']}]"),
        (HOLTROP_DOMAIN["B_T"][0] <= B_T <= HOLTROP_DOMAIN["B_T"][1],
         f"B/T={B_T:.2f} out of [{HOLTROP_DOMAIN['B_T']}]"),
        (HOLTROP_DOMAIN["Fn"][0] <= Fn <= HOLTROP_DOMAIN["Fn"][1],
         f"Fn={Fn:.3f} out of [{HOLTROP_DOMAIN['Fn']}]"),
    ]

    for valid, reason in checks:
        if not valid:
            return False, reason

    return True, "Within Holtrop domain"


# ---------------------------------------------------------------------------
# Canonical path (REAL): delegate to backend/api/holtrop1984.py
# ---------------------------------------------------------------------------
def _hull_geometry(geom: Dict[str, Any], kernel: Dict[str, Any]) -> Any:
    """Build the canonical ``HullGeometry`` from the m546 geometry dict."""
    mod = kernel["module"]
    parts = geom.get("holtrop_particulars") or {}
    L, B, T, Cb = float(geom["L"]), float(geom["B"]), float(geom["T"]), float(geom["Cb"])

    cm = float(parts.get("Cm", geom.get("Cm", estimate_cm(Cb))))
    cwp = float(parts.get("Cwp", geom.get("Cwp", estimate_cwp(Cb))))
    vol = float(parts.get("vol", geom.get("vol", Cb * L * B * T)))
    lcb_pct = float(parts.get("lcb_pct", geom.get("lcb_pct", 0.0)))
    abt = float(parts.get("abt", geom.get("abt", 0.0)))
    hb = float(parts.get("hb", geom.get("hb", 0.0)))
    at = float(parts.get("at", geom.get("at", 0.0)))

    return mod.HullGeometry(
        name="KCS-bow-param", l=L, b=B, t=T, cb=Cb, cm=cm, cwp=cwp,
        lcb_pct=lcb_pct, vol=vol, abt=abt, hb=hb, at=at,
    )


def canonical_resistance(geom: Dict[str, Any], V_ms: float) -> Dict[str, Any]:
    """Resistance from the canonical Holtrop-1984 kernel (REAL evidence).

    Raises:
        RuntimeError: if the canonical kernel is unavailable or rejects the input.
    """
    kernel = canonical_kernel()
    if not kernel["available"]:
        raise RuntimeError("canonical Holtrop-1984 kernel unavailable")
    mod = kernel["module"]
    hg = _hull_geometry(geom, kernel)
    r = mod.holtrop1984_resistance(hg, float(V_ms), rho=RHO_WATER, nu=NU_WATER)

    residuary = r.rw + r.rb + r.rtr
    return {
        "V_ms": float(V_ms),
        "Fn": float(r.fn),
        "Re": float(r.re),
        "S_wet": float(r.s_ref),
        "Rf_N": float(r.rf),            # bare ITTC-1957 friction (Holtrop Rf)
        "Rv_N": float(r.rf_1pk),        # (1+k) Rf, viscous resistance
        "Rr_N": float(residuary),       # residuary = Rw + Rb + Rtr
        "Rw_N": float(r.rw),
        "Rb_N": float(r.rb),
        "Rtr_N": float(r.rtr),
        "Ca_N": float(r.ra),            # correlation allowance as a FORCE [N]
        "Rt_N": float(r.rt),
        "Ct": float(r.ct),
        "Cf": float(r.cf),
        "Cv": float(r.cv),              # (1+k) Cf
        "Cr": float(r.cw),              # wave coefficient Rw / (0.5 rho V^2 S)
        "Ca": float(r.ca),              # dimensionless correlation allowance
        "form_factor_1pk": float(r.form_factor_1pk),
        "Cm": float(hg.cm),
        "Cwp": float(hg.cwp),
        "Cp": float(hg.cb / hg.cm),
        "lcb_pct": float(hg.lcb_pct),
        "abt": float(hg.abt),
        "hb": float(hg.hb),
        "notes": list(r.notes),
        "evidence_level": "REAL",
        "physics_source": (
            "backend/api/holtrop1984.py::holtrop1984_resistance "
            f"(Holtrop-Mennen 1984, {kernel['provenance'].get('source')}, "
            f"sha256={str(kernel['provenance'].get('sha256'))[:16]})"
        ),
    }


# ---------------------------------------------------------------------------
# Legacy fallback path (MOCK): pre-m1610 hand-fitted kernel, kept for fallback
# ---------------------------------------------------------------------------
def _legacy_holtrop_resistance(geom: Dict[str, Any], V_ms: float) -> Dict[str, Any]:
    """Pre-m1610 hand-fitted kernel.  **MOCK**: kept only as a labelled fallback.

    Known defects (m1601 audit, `docs/verification/optimizer-objective-mock-audit.md`):
    the residuary term ``0.2 exp(-1.5/Fn^1.2) * 1e-3`` and the wave term
    ``0.002 exp(-2/Fn^2)`` are exponentially collapsed, so ``Ct`` equals the bare
    ITTC-57 ``Cf`` to machine precision (Cr/Cf ~ 1e-5 vs 0.335 canonical), and the
    correlation allowance is added as ``0.006 (L+100)^-0.16 * S_wet`` — an area in
    m^2 added to a force in N, i.e. the allowance coefficient is ~3 orders of
    magnitude too small.  Bow sensitivity came from artificial multipliers.
    """
    L = geom["L"]
    B = geom["B"]
    T = geom["T"]
    Cb = geom["Cb"]
    params = geom.get("params", {}) or {}

    # Wetted area approximation (hand-fitted, fixed Cm=0.57 / Cwp=0.65)
    S_wet = L * (2 * T + B) * (0.453 + 0.4425 * Cb - 0.2862 * 0.57 -
                               0.003467 * B / T + 0.3696 * 0.65)

    Fn = V_ms / np.sqrt(G * L)
    Re = V_ms * L / NU_WATER

    # 1. Frictional resistance (ITTC 1957)
    Cf = 0.075 / (np.log10(Re) - 2) ** 2
    Rf = 0.5 * RHO_WATER * V_ms ** 2 * S_wet * Cf

    # 2. Residuary resistance (collapsed by the exponential form)
    Cr_base = (
        0.2 * np.exp(-1.5 / Fn ** 1.2) *
        (1.0 + 0.3 * (Cb - 0.65)) *
        (1.0 - 0.05 * (L / B - 5.0))
    )
    bow_entrance_delta = params.get("bow_entrance_angle_delta", 0.0)
    bow_factor = 1.0 - 0.08 * bow_entrance_delta
    bulb_scale = params.get("bulb_volume_scale", 1.0)
    bulb_factor = 1.0 - 0.15 * (bulb_scale - 1.0)
    stem_rake = params.get("stem_rake_delta", 0.0)
    stem_factor = 1.0 - 0.03 * abs(stem_rake)
    bow_form_factor = bow_factor * bulb_factor * stem_factor

    Cr_coeff = Cr_base * bow_form_factor
    Rr = 0.5 * RHO_WATER * V_ms ** 2 * S_wet * Cr_coeff * 1e-3

    # 3. Wave resistance (collapsed by the exponential form)
    Rw_base = 0.5 * RHO_WATER * V_ms ** 2 * S_wet * 0.002 * np.exp(-2.0 / Fn ** 2)
    waterline_flare = params.get("waterline_flare_fp", 0.0)
    Rw = Rw_base * (1.0 - 0.10 * waterline_flare)

    # 4. Correlation allowance -- DIMENSIONALLY WRONG (m^2 added to N)
    Ca = 0.006 * (L + 100) ** (-0.16) * S_wet

    Rt = Rf + Rr + Rw + Ca

    Ct = Rt / (0.5 * RHO_WATER * V_ms ** 2 * S_wet)
    Cr_total = (Rr + Rw) / (0.5 * RHO_WATER * V_ms ** 2 * S_wet)

    return {
        "V_ms": float(V_ms),
        "Fn": float(Fn),
        "Re": float(Re),
        "S_wet": float(S_wet),
        "Rf_N": float(Rf),
        "Rv_N": float(Rf),
        "Rr_N": float(Rr),
        "Rw_N": float(Rw),
        "Rb_N": 0.0,
        "Rtr_N": 0.0,
        "Ca_N": float(Ca),
        "Rt_N": float(Rt),
        "Ct": float(Ct),
        "Cf": float(Cf),
        "Cv": float(Cf),
        "Cr": float(Cr_total),
        "Ca": float(Ca / (0.5 * RHO_WATER * V_ms ** 2 * S_wet)),
        "form_factor_1pk": 1.0,
        "notes": ["legacy MOCK fallback: Ct == ITTC-57 Cf; Ca added in m^2"],
        "evidence_level": "MOCK",
        "physics_source": LEGACY_SOURCE,
    }


def holtrop_resistance(geom: Dict[str, Any], V_ms: float,
                       allow_fallback: bool = True) -> Dict[str, Any]:
    """Calm-water resistance of the optimizer objective.

    Delegates to the canonical Holtrop-1984 kernel (REAL).  If that kernel is
    unavailable (or rejects the geometry), falls back to
    :func:`_legacy_holtrop_resistance` **only** when ``allow_fallback`` and stamps
    the result ``evidence_level="MOCK"`` with the reason in ``fallback_reason``.

    Args:
        geom: geometry dict from :func:`optimizer.interfaces.kcs_bow_param.generate_geometry`
        V_ms: ship speed [m/s]
        allow_fallback: keep the legacy chain as a labelled fallback
    """
    try:
        return canonical_resistance(geom, V_ms)
    except Exception as exc:  # noqa: BLE001 - any canonical failure -> labelled fallback
        if not allow_fallback:
            raise
        out = _legacy_holtrop_resistance(geom, V_ms)
        out["fallback_reason"] = f"{type(exc).__name__}: {exc}"
        return out


def evaluate_resistance(geom: Dict[str, Any], Fn_design: float = None,
                        seed: int = None) -> Dict[str, Any]:
    """
    Evaluate total resistance at design Fn (m547 interface).

    The physics comes from the canonical Holtrop-1984 kernel (see
    :func:`holtrop_resistance`); the legacy MOCK chain is used only as a labelled
    fallback.  ``objective_evidence_level`` / ``objective_physics_source`` describe
    which one actually produced the number.

    Args:
        geom: Geometry dictionary from m546
        Fn_design: Design Froude number (default: KCS design Fn=0.26)
        seed: Random seed for reproducibility (not used: the evaluation is deterministic)

    Returns:
        Evaluation dictionary with:
        - Ct: total resistance coefficient
        - components: resistance breakdown
        - in_holtrop_domain: bool
        - valid: bool (geometry valid + in domain)
        - objective_evidence_level / objective_physics_source: provenance of the physics
    """
    if seed is not None:
        np.random.seed(seed)

    if Fn_design is None:
        Fn_design = 0.26  # KCS design Fn

    # Check Holtrop domain
    in_domain, domain_reason = check_holtrop_domain(geom, Fn_design)

    def _stamp(payload: Dict[str, Any], level: str, source: str) -> Dict[str, Any]:
        payload["evidence_level"] = level
        payload["objective_evidence_level"] = level
        payload["objective_physics_source"] = source
        payload["physics_source"] = source
        return payload

    if not geom["valid"]:
        return _stamp({
            "Ct": np.inf,
            "components": {},
            "in_holtrop_domain": False,
            "valid": False,
            "reason": "Geometry invalid",
        }, "STUB", "none (invalid geometry, objective not evaluated)")

    if not in_domain:
        return _stamp({
            "Ct": np.inf,
            "components": {},
            "in_holtrop_domain": False,
            "valid": False,
            "reason": f"Outside Holtrop domain: {domain_reason}",
        }, "STUB", "none (outside Holtrop domain, objective not evaluated)")

    # Calculate speed from Fn
    V_ms = Fn_design * np.sqrt(G * geom["L"])

    # Calculate resistance (canonical, else labelled legacy fallback)
    result = holtrop_resistance(geom, V_ms)

    # 分量按需读取：物理内核可以只回 Ct/Rt（精简实现），缺项记为 None 而不是崩
    g = result.get
    payload = {
        "Ct": result["Ct"],
        "components": {
            "Cf": g("Cf"),
            "Cv": g("Cv", g("Cf")),
            "Cr": g("Cr"),
            "Ct": g("Ct"),
            "Rt_N": g("Rt_N"),
            "Rf_N": g("Rf_N"),
            "Rv_N": g("Rv_N", g("Rf_N")),
            "Rr_N": g("Rr_N"),
            "Rw_N": g("Rw_N"),
            "Rb_N": g("Rb_N"),
            "Rtr_N": g("Rtr_N"),
            "Ra_N": g("Ca_N"),
            "Ca": g("Ca"),
            "form_factor_1pk": g("form_factor_1pk"),
            "S_wet_m2": g("S_wet"),
        },
        "Fn": g("Fn"),
        "V_ms": g("V_ms"),
        "Re": g("Re"),
        "S_wet_m2": g("S_wet"),
        "in_holtrop_domain": True,
        "valid": True,
        "reason": "OK",
        "notes": result.get("notes", []),
    }
    if "fallback_reason" in result:
        payload["fallback_reason"] = result["fallback_reason"]
    return _stamp(payload, result.get("evidence_level", "WITHHELD"),
                  result.get("physics_source", "unknown"))


def get_baseline_resistance() -> Dict[str, Any]:
    """
    Get baseline KCS resistance at design Fn.

    Returns:
        Baseline evaluation dictionary
    """
    from .kcs_bow_param import generate_geometry, KCS_BASELINE

    # Baseline = zero deformation
    baseline_params = np.zeros(8)
    geom = generate_geometry(baseline_params)

    return evaluate_resistance(geom, KCS_BASELINE["Fn_design"])