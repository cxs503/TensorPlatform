"""
Benchmark reference-data integrity gate (对标算例数据层一致性门).

Background
----------
``backend/data/benchmark_reference.json`` is the only experimental reference base
behind the platform's ``/calculations/benchmark_validate`` endpoint, i.e. behind
every "Holtrop vs experiment: error <X>%" statement and every honest-FAIL verdict
recorded for SUBOFF / DTMB-5415 / KVLCC2.  Before such a verdict can be trusted,
the reference table itself must satisfy the definitions it is quoted under:

    C_T = R_T / (0.5 * rho * S * V^2)          (total-resistance coefficient)
    Fn  = V / sqrt(g * L)                      (Froude number)
    Delta = rho * L * B * T * Cb               (block-coefficient geometry identity)

If a row violates one of those identities, the *reference* is broken - not
necessarily the numerical model - and no error percentage computed against it may
be reported as a validation result.  This module turns that rule into machine
checks (see :func:`audit_case`, :func:`audit_dataset`).

Design notes
------------
* Pure functions, no I/O, no global state: callable from the API layer, from the
  audit script and from pytest.
* Tolerances are explicit arguments so tests can tighten them.
* Evidence grading follows the platform convention REAL / WITHHELD.  A case is
  REAL only when every identity check passes *and* a source citation exists.
* Nothing is "repaired" silently: the audit only reports.  Fixing a reference
  requires re-reading the source paper, which is out of scope for a gate.

EDIT_WHITELIST: docs/, backend/, scripts/, tests/, runs/
"""
from __future__ import annotations

import math
import os
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

G = 9.81
RHO_FRESH = 998.0   # kg/m^3, towing tank fresh water
RHO_SEA = 1025.0    # kg/m^3, sea water

# Default acceptance tolerances (relative).
TOL_FROUDE = 0.05        # declared vs recomputed Froude number
TOL_SPEED = 0.02         # speed_ms vs speed_kn conversion
TOL_IDENTITY = 0.20      # declared R_T vs R_T implied by (C_T, S, V)
TOL_WITHIN_HULL = 0.10   # spread of 0.5*rho*S across rows of one hull
TOL_GEOMETRY = 0.10      # Cb * L * B * T vs declared displacement

REAL = "REAL"
WITHHELD = "WITHHELD"


# ---------------------------------------------------------------------------
# Primitive physical relations
# ---------------------------------------------------------------------------

def froude_number(speed_ms: float, length_m: float, g: float = G) -> float:
    """Fn = V / sqrt(g L)."""
    return speed_ms / math.sqrt(g * length_m)


def implied_wetted_area(
    rt_n: float, ct: float, speed_ms: float, rho: float
) -> Optional[float]:
    """Wetted area implied by the pair (C_T, R_T) through C_T = R_T/(0.5 rho S V^2)."""
    denom = 0.5 * rho * ct * speed_ms ** 2
    if denom <= 0:
        return None
    return rt_n / denom


def resistance_from_ct(ct: float, area_m2: float, speed_ms: float, rho: float) -> float:
    """R_T = C_T * 0.5 rho S V^2."""
    return ct * 0.5 * rho * area_m2 * speed_ms ** 2


def displacement_from_geometry(
    length_m: float, beam_m: float, draft_m: float, cb: float, rho: float
) -> float:
    """Delta = rho * L * B * T * Cb (kg)."""
    return rho * length_m * beam_m * draft_m * cb


def _rel_error(value: float, reference: float) -> Optional[float]:
    if reference == 0:
        return None if value != 0 else 0.0
    return abs(value - reference) / abs(reference)


def _nearby(keys: List[str], *names: str) -> Optional[str]:
    for k in keys:
        if k in names:
            return k
    return None


# ---------------------------------------------------------------------------
# Per-case audit
# ---------------------------------------------------------------------------

def case_density(case: Dict[str, Any]) -> float:
    """Water density for a case: explicit override > displacement unit hint > fresh water."""
    geom = case.get("geometry") or {}
    for source in (case, geom):
        for key in ("water_density", "rho", "rho_water"):
            if key in source:
                return float(source[key])
    if "displacement_t" in geom:
        return RHO_SEA
    return RHO_FRESH


def audit_case(
    case: Dict[str, Any],
    tol_froude: float = TOL_FROUDE,
    tol_speed: float = TOL_SPEED,
    tol_identity: float = TOL_IDENTITY,
    tol_geometry: float = TOL_GEOMETRY,
) -> Dict[str, Any]:
    """
    Audit one benchmark case.

    Returns a dict with the recomputed invariants and a list of blocking issues.
    ``evidence`` is REAL only if ``blocking_issues`` is empty and a citation exists.
    """
    geom = case.get("geometry") or {}
    rho = case_density(case)
    issues: List[str] = []

    speed_ms = case.get("speed_ms")
    speed_kn = case.get("speed_kn")
    length = geom.get("length_m") or geom.get("length")
    wetted = (
        geom.get("wetted_surface_m2")
        or geom.get("wetted_surface")
        or case.get("wetted_surface_m2")
    )
    ct = case.get("experimental_ct")
    rt = case.get("experimental_rt_n")

    out: Dict[str, Any] = {
        "case_id": case.get("case_id"),
        "ship_type": case.get("ship_type"),
        "speed_ms": speed_ms,
        "rho": rho,
        "declared_froude": case.get("froude_number"),
        "declared_ct": ct,
        "declared_rt_n": rt,
    }

    # --- 1. speed unit consistency -------------------------------------
    if speed_ms and speed_kn:
        conv = speed_kn * 0.514444
        rel = _rel_error(speed_ms, conv)
        out["speed_kn_consistency_rel"] = rel
        if rel is not None and rel > tol_speed:
            issues.append(
                f"speed mismatch: speed_ms={speed_ms} vs speed_kn={speed_kn} "
                f"({conv:.3f} m/s), rel={rel:.1%}"
            )

    # --- 2. Froude identity -------------------------------------------
    fn_calc = None
    if speed_ms and length:
        fn_calc = froude_number(speed_ms, length)
        out["froude_recomputed"] = round(fn_calc, 6)
        declared = case.get("froude_number")
        if declared:
            rel = _rel_error(declared, fn_calc)
            out["froude_rel_error"] = rel
            if rel is not None and rel > tol_froude:
                issues.append(
                    f"Froude inconsistent: declared={declared} vs V/sqrt(gL)="
                    f"{fn_calc:.4f} (rel={rel:.1%}, L={length} m)"
                )

    # --- 3. C_T / R_T / S identity ------------------------------------
    if ct and rt and speed_ms:
        s_implied = implied_wetted_area(rt, ct, speed_ms, rho)
        out["wetted_area_implied_m2"] = None if s_implied is None else round(s_implied, 4)
        if wetted:
            rel = _rel_error(s_implied, float(wetted))
            out["wetted_area_declared_m2"] = float(wetted)
            out["wetted_area_rel_error"] = rel
            if rel is not None and rel > tol_identity:
                issues.append(
                    f"C_T/R_T/S identity violated: declared S={wetted} m^2 but "
                    f"(C_T,R_T) implies S={s_implied:.2f} m^2 (factor "
                    f"{max(s_implied, float(wetted)) / max(min(s_implied, float(wetted)), 1e-12):.2f}x)"
                )
        # rho*S/2 carries through every row of the same hull
        out["half_rho_s"] = round(rt / (ct * speed_ms ** 2), 3)

    # --- 4. Geometry (block coefficient) identity ---------------------
    L = geom.get("length_m") or geom.get("length")
    B = geom.get("beam_m") or geom.get("beam") or geom.get("diameter_m")
    T = geom.get("draft_m") or geom.get("draft") or geom.get("diameter_m")
    cb = geom.get("cb")
    disp_t = geom.get("displacement_t")
    disp_kg = geom.get("displacement_kg")
    disp = disp_t * 1000.0 if disp_t else disp_kg
    if L and B and T and cb and disp:
        d_est = displacement_from_geometry(L, B, T, cb, rho)
        rel = _rel_error(disp, d_est)
        out["displacement_declared_kg"] = disp
        out["displacement_from_geometry_kg"] = round(d_est, 1)
        out["displacement_rel_error"] = rel
        if rel is not None and rel > tol_geometry:
            issues.append(
                f"geometry inconsistent: rho*L*B*T*Cb={d_est:.0f} kg vs declared "
                f"{disp:.0f} kg (rel={rel:.1%})"
            )

    # --- 5. Wetted-area verifiability & plausibility ----------------
    # The C_T/R_T identity can only be closed when the case declares S.  A
    # geometry-only envelope (block hull, no waterplane): A_env = L*B + 2*L*T + 2*B*T.
    # For any realistic hull the wetted area is a sizeable fraction of it, so a
    # ratio far outside [0.4, 2.0] means the (C_T, R_T) pair cannot belong to the
    # declared geometry.  Reported as information; blocking rule is "S declared".
    if L and B and T:
        out["envelope_area_m2"] = round(L * B + 2 * L * T + 2 * B * T, 3)
    s_ref = s_implied if ct and rt and speed_ms else None
    if out.get("envelope_area_m2") and s_ref:
        ratio = s_ref / out["envelope_area_m2"]
        out["implied_over_envelope"] = round(ratio, 4)
        out["plausible"] = 0.4 <= ratio <= 2.0
        if not out["plausible"]:
            out["notes"] = [
                f"implied wetted area {s_ref:.2f} m^2 is {ratio:.2f}x the block "
                f"envelope {out['envelope_area_m2']:.1f} m^2 -> (C_T, R_T) pair "
                "cannot belong to the declared geometry"
            ]
    if (ct or rt) and not wetted:
        issues.append(
            "wetted surface not declared: C_T/R_T identity cannot be closed "
            "(reference value unverifiable)"
        )

    # --- 6. Provenance ------------------------------------------------
    citation = (case.get("source_citation") or "").strip()
    out["source_citation"] = citation
    out["has_citation"] = bool(citation)
    if not citation:
        issues.append("no source citation")

    out["blocking_issues"] = issues
    out["checks_passed"] = len(issues) == 0
    out["evidence"] = REAL if not issues else WITHHELD
    return out


# ---------------------------------------------------------------------------
# Dataset audit
# ---------------------------------------------------------------------------

def _within_hull_spread(
    audits: List[Dict[str, Any]], tol: float = TOL_WITHIN_HULL
) -> Dict[str, Dict[str, Any]]:
    """
    Within one hull, 0.5*rho*S must be the same for every row (same hull, same fluid).
    A drifting value means the C_T and R_T columns were not produced consistently.
    """
    groups: Dict[str, List[Tuple[str, float, float]]] = {}
    for a in audits:
        if a.get("half_rho_s"):
            groups.setdefault(a["ship_type"], []).append(
                (a["case_id"], a["half_rho_s"], a.get("rho") or RHO_FRESH)
            )

    report: Dict[str, Dict[str, Any]] = {}
    for hull, rows in groups.items():
        if len(rows) < 2:
            continue
        values = [v for _, v, _ in rows]
        med = median(values)
        rho_med = median([r for _, _, r in rows])
        dev = {cid: (v - med) / med for cid, v, _ in rows}
        worst = max(dev, key=lambda c: abs(dev[c]))
        report[hull] = {
            "median_half_rho_s": round(med, 3),
            "implied_wetted_area_m2": round(med / (0.5 * rho_med), 1),
            "max_rel_deviation": round(abs(dev[worst]), 4),
            "worst_case": worst,
            "consistent": abs(dev[worst]) <= tol,
            "per_case_deviation": {k: round(v, 4) for k, v in dev.items()},
        }
    return report


def audit_dataset(
    data: Dict[str, Any],
    tol_froude: float = TOL_FROUDE,
    tol_speed: float = TOL_SPEED,
    tol_identity: float = TOL_IDENTITY,
    tol_geometry: float = TOL_GEOMETRY,
    tol_within_hull: float = TOL_WITHIN_HULL,
) -> Dict[str, Any]:
    """Audit a whole benchmark reference dataset (``benchmark_reference.json`` shape)."""
    cases = data.get("cases") or []
    audits = [
        audit_case(
            c,
            tol_froude=tol_froude,
            tol_speed=tol_speed,
            tol_identity=tol_identity,
            tol_geometry=tol_geometry,
        )
        for c in cases
    ]
    hulls = _within_hull_spread(audits, tol=tol_within_hull)

    for a in audits:
        drift = hulls.get(a["ship_type"])
        a["within_hull_consistent"] = None if drift is None else drift["consistent"]
        if drift is not None and not drift["consistent"]:
            a["blocking_issues"].append(
                f"within-hull 0.5*rho*S drifts {drift['max_rel_deviation']:.1%} "
                f"(worst {drift['worst_case']})"
            )
            a["checks_passed"] = False
            a["evidence"] = WITHHELD

    by_evidence = {REAL: 0, WITHHELD: 0}
    for a in audits:
        by_evidence[a["evidence"]] += 1

    return {
        "dataset": data.get("description", ""),
        "version": data.get("version"),
        "tolerances": {
            "froude": tol_froude,
            "speed": tol_speed,
            "identity": tol_identity,
            "geometry": tol_geometry,
            "within_hull": tol_within_hull,
        },
        "cases": audits,
        "hulls": hulls,
        "summary": {
            "total": len(audits),
            "real": by_evidence[REAL],
            "withheld": by_evidence[WITHHELD],
            "real_case_ids": [a["case_id"] for a in audits if a["evidence"] == REAL],
            "withheld_case_ids": [a["case_id"] for a in audits if a["evidence"] == WITHHELD],
            "verdict": (
                "REFERENCE_BASE_UNUSABLE"
                if by_evidence[REAL] == 0 and audits
                else "OK"
            ),
        },
    }


# ---------------------------------------------------------------------------
# Calculation-API side: base-URL discovery and C_T recovery
# ---------------------------------------------------------------------------

def ship_platform_bases() -> List[str]:
    """Candidate base URLs for the ship performance calculation API.

    ``SHIP_PLATFORM_URL`` wins when set; otherwise the historical ports and finally
    the port this platform itself serves on (8010) are probed.  Port 8002 used to
    be the only candidate and is normally dead, which degraded every benchmark row
    to "Resistance API call failed" (error_pct = 999).
    """
    env = os.getenv("SHIP_PLATFORM_URL")
    bases = [env] if env else []
    bases += ["http://127.0.0.1:8001", "http://127.0.0.1:8002", "http://127.0.0.1:8010"]
    seen: set = set()
    ordered: List[str] = []
    for b in bases:
        b = (b or "").rstrip("/")
        if b and b not in seen:
            seen.add(b)
            ordered.append(b)
    return ordered


def total_resistance_coefficient(
    response: Dict[str, Any], case: Dict[str, Any], rho: float, speed_ms: float
) -> Optional[float]:
    """C_T = R_T / (0.5 rho S V^2), preferring the reference hull's wetted area.

    The calculation endpoint reports total resistance in newtons; without this
    conversion ``calc_ct`` was always 0.0 and every case "failed" with 100% error.
    """
    if response.get("ct"):
        return float(response["ct"])
    rt = response.get("total_resistance_n", response.get("rt"))
    if not rt:
        return None
    geom = case.get("geometry") or {}
    area = geom.get("wetted_surface_m2") or response.get("wetted_surface_m2")
    if not area:
        return None
    return float(rt) / (0.5 * rho * float(area) * speed_ms ** 2)


def case_evidence(case: Dict[str, Any], **kwargs: Any) -> str:
    """Convenience: REAL / WITHHELD for a single case."""
    return audit_case(case, **kwargs)["evidence"]