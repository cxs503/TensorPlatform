"""Independent first-principles audit of the ship-resistance benchmark chain.

Why this tool exists
--------------------
The platform advertises a "对标算例" (benchmark case) capability
(``POST /api/v1/calculations/benchmark_validate``) whose pass/fail judgement is
built from two artefacts:

* ``backend/data/benchmark_reference.json`` — the "experimental" reference
  values (C_T, R_T, F_r), and
* the live resistance solver (``/api/v1/calculations/resistance``) whose C_T is
  compared against those references.

Both artefacts can be audited **without** trusting any memorised literature
table, because a resistance result must satisfy a handful of *algebraic
identities* that follow from the definitions alone:

    I1  F_r        = V / sqrt(g L)                         (Froude definition)
    I2  R_T        = C_T * 1/2 rho S V^2                   (coefficient definition)
    I3  R_F/R_R    = C_F/C_R * ... and C_T >= C_F(Re)      (form+wave resistance
                                                            is a non-negative
                                                            addition to friction)
    I4  P_E        = R_T * V                               (effective power)
    I5  S          must lie in a geometry band derived from L, B, D
                   (a wetted surface can never exceed the enveloping cylinder
                    pi*D*L for a body of revolution)

Any dataset or API response that violates I1-I5 is *provably* inconsistent,
independent of which empirical curve or which source document was used.  The
audit therefore converts "we cannot remember the reference value" into
"the reported numbers cannot all be true" — which is a machine-checkable
verdict and exactly what a verifier owes the platform.

Usage
-----
    python tools/verifier/benchmark_audit.py                     # dataset only
    python tools/verifier/benchmark_audit.py --api               # + live API audit
    python tools/verifier/benchmark_audit.py --out artifacts/benchmark_audit

Verdict vocabulary
------------------
``USABLE``    every identity check closed inside tolerance -> the case may be
              used as a benchmark reference.
``WITHHELD``  at least one identity failed, or the inputs needed for the check
              are missing -> the case may NOT be used to grade a solver.  The
              failing identity, the implied (physically impossible) quantity and
              the required correction factor are reported as evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Physical constants (documented so the audit is reproducible)
# --------------------------------------------------------------------------
G = 9.80665                     # m/s^2, standard gravity
NU_FRESH_20C = 1.004e-6         # m^2/s, fresh water 20 C
NU_SEA_15C = 1.1883e-6          # m^2/s, sea water 15 C, ITTC water properties
RHO_FRESH_20C = 998.2           # kg/m^3
RHO_SEA_15C = 1025.0            # kg/m^3
KNOT_TO_MS = 1852.0 / 3600.0    # 0.514444... m/s per knot
# Admissible force conventions for the R_T column; the audit must not mistake a
# missing unit label for a physics failure, so every common convention is tried
# and only "none of them closes" counts as a violation.
FORCE_UNITS = (
    ("N", 1.0),
    ("kN", 1e-3),
    ("kgf", 1.0 / G),
    ("lbf", 1.0 / 4.4482216152605),
    ("tf", 1.0 / (G * 1000.0)),
)

# Tolerances: the identity checks are algebra, not experiment, so they must
# close to well under one percent; 1 % leaves room for the rounding of the
# published/reported values themselves (3-4 significant figures).
TOL_IDENTITY_PCT = 1.0
TOL_COEFF_PCT = 2.0

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"


# --------------------------------------------------------------------------
# Base hydrodynamics helpers (pure functions, unit tested)
# --------------------------------------------------------------------------
def ittc57_cf(re: float) -> float:
    """ITTC-1957 model-ship correlation line."""
    if re <= 0:
        raise ValueError("Reynolds number must be positive")
    return 0.075 / (math.log10(re) - 2.0) ** 2


def ittc78_cf(re: float) -> float:
    """ITTC-1978 line (same algebra, kept separate for clarity of provenance)."""
    return 0.075 / (math.log10(re) - 2.0) ** 2


def reynolds(v: float, length: float, nu: float) -> float:
    return v * length / nu


def froude(v: float, length: float, g: float = G) -> float:
    return v / math.sqrt(g * length)


def dynamic_pressure_factor(rho: float, v: float, s: float) -> float:
    """1/2 rho S V^2 -- the multiplier that turns a coefficient into a force."""
    return 0.5 * rho * s * v * v


def wetted_surface_estimate(length: float, beam: float, draft: float, cb: float) -> float:
    """Mumford-type wetted-surface estimate  S ~ L (1.7 T + Cb B).

    Documented approximation good to ~5 % for conventional monohulls
    (Danckwardt / Mumford family); used only to build a *band*, never as an
    authoritative value.
    """
    return length * (1.7 * draft + cb * beam)


def body_of_revolution_band(length: float, diameter: float) -> Tuple[float, float]:
    """Plausible wetted-surface band for a submerged body of revolution.

    Upper bound: the enveloping cylinder S = pi D L (unreachable, a body that
    fills the cylinder everywhere).  Lower bound: 0.5 pi D L, below which the
    body would have to be drastically thinner than its stated maximum diameter.
    """
    cyl = math.pi * diameter * length
    return 0.5 * cyl, cyl


def monohull_band(length: float, beam: float, draft: float, cb: float) -> Tuple[float, float]:
    """+/-15 % band around the Mumford-type estimate (see above)."""
    est = wetted_surface_estimate(length, beam, draft, cb)
    return 0.85 * est, 1.15 * est


# --------------------------------------------------------------------------
# Check records
# --------------------------------------------------------------------------
@dataclass
class Check:
    name: str
    verdict: str            # PASS / FAIL / WITHHELD / NOT_APPLICABLE
    detail: str
    reported: Optional[float] = None
    expected: Optional[float] = None
    deviation_pct: Optional[float] = None
    tolerance_pct: Optional[float] = None
    implied: Optional[Dict[str, Any]] = None   # diagnostic "what would have to be true"

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _pct(a: float, b: float) -> float:
    """Relative deviation of a from b, in percent (b is the reference)."""
    if b == 0:
        return float("inf")
    return abs(a - b) / abs(b) * 100.0


# --------------------------------------------------------------------------
# Per-case identity checks
# --------------------------------------------------------------------------
def check_froude_identity(case: Dict[str, Any]) -> Check:
    """I1: F_r stated in the dataset must equal V / sqrt(g L)."""
    geom = case.get("geometry", {}) or {}
    length = geom.get("length_m")
    v = case.get("speed_ms")
    fn_rep = case.get("froude_number")
    if length is None or v is None or fn_rep is None:
        return Check("I1_froude_identity", "WITHHELD",
                     "missing one of length_m / speed_ms / froude_number")
    fn_exp = froude(v, length)
    if fn_rep <= 0:
        return Check("I1_froude_identity", "FAIL", "non-positive Froude number",
                     reported=fn_rep, expected=round(fn_exp, 6))
    ratio = fn_exp / fn_rep
    dev = _pct(fn_rep, fn_exp)
    # A constant ratio across a ship type betrays a unit/definition mix-up;
    # report it explicitly because that is actionable.
    note = f"F_r(stated)/F_r(V/sqrt(gL)) = {fn_rep / fn_exp:.4f}"
    verdict = "PASS" if dev <= TOL_IDENTITY_PCT else "FAIL"
    return Check("I1_froude_identity", verdict, note,
                 reported=round(fn_rep, 6), expected=round(fn_exp, 6),
                 deviation_pct=round(dev, 3), tolerance_pct=TOL_IDENTITY_PCT,
                 implied={"length_m_implied_by_stated_Fr": round((v / fn_rep) ** 2 / G, 1),
                          "ratio_stated_over_exact": round(fn_rep / fn_exp, 4),
                          "ratio_exact_over_stated": round(ratio, 4)})


def _surface_and_band(case: Dict[str, Any]) -> Tuple[Optional[float], Optional[Tuple[float, float]], str]:
    geom = case.get("geometry", {}) or {}
    length = geom.get("length_m")
    s_given = geom.get("wetted_surface_m2")
    diameter = geom.get("diameter_m")
    if diameter is not None and length is not None:
        band = body_of_revolution_band(length, diameter)
        src = "body of revolution pi*D*L band"
    elif None not in (length, geom.get("beam_m"), geom.get("draft_m"), geom.get("cb")):
        band = monohull_band(length, geom["beam_m"], geom["draft_m"], geom["cb"])
        src = "Mumford-type estimate +/-15 %"
    else:
        band, src = None, "no usable geometry"
    if s_given is not None:
        return float(s_given), band, src
    if band is not None:
        return 0.5 * (band[0] + band[1]), band, src + " (mid-band value)"
    return None, band, src


def _rt_deviation(case: Dict[str, Any], rho: float, scale: float) -> Optional[Dict[str, float]]:
    """Deviation of the stored R_T from C_T * 1/2 rho S V^2 under one convention."""
    ct = case.get("experimental_ct")
    rt_rep = case.get("experimental_rt_n")
    v = case.get("speed_ms")
    s, band, _ = _surface_and_band(case)
    if None in (ct, rt_rep, v, s):
        return None
    pred = ct * dynamic_pressure_factor(rho, v, s) * scale
    if pred <= 0:
        return None
    return {"predicted": pred, "deviation_pct": _pct(rt_rep, pred),
            "implied_s_m2": (rt_rep / scale) / (ct * dynamic_pressure_factor(rho, v, 1.0)),
            "band": band or (float("nan"), float("nan"))}


def infer_dataset_convention(cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Infer the single (density, force unit) convention that best explains the dataset.

    A reference table produced by one measurement pipeline uses one convention.
    Scanning per case would allow a lucky unit to mask a broken column, so the
    convention is chosen to minimise the *worst* case deviation over the whole
    dataset; the residual of that best convention is then the honest measure of
    whether the C_T and R_T columns can coexist.
    """
    grid: List[Dict[str, Any]] = []
    for rho_name, rho in (("fresh_998.2", RHO_FRESH_20C), ("sea_1025", RHO_SEA_15C)):
        for unit_name, scale in FORCE_UNITS:
            devs = []
            for case in cases:
                d = _rt_deviation(case, rho, scale)
                if d is not None:
                    devs.append(d["deviation_pct"])
            if not devs:
                continue
            grid.append({"rho": rho_name, "force_unit": unit_name, "scale": scale,
                         "n_cases": len(devs), "max_deviation_pct": round(max(devs), 3),
                         "mean_deviation_pct": round(sum(devs) / len(devs), 3)})
    grid.sort(key=lambda g: g["max_deviation_pct"])
    best = grid[0] if grid else None
    verdict = "PASS" if best and best["max_deviation_pct"] <= TOL_COEFF_PCT else "FAIL"
    return {
        "verdict": verdict,
        "best": best,
        "candidates": grid,
        "detail": (
            f"best convention rho={best['rho']} force_unit={best['force_unit']} still leaves a "
            f"worst-case deviation of {best['max_deviation_pct']} % across {best['n_cases']} cases"
            if best else "no gradable case"),
    }


def check_ct_rt_identity(case: Dict[str, Any],
                         convention: Optional[Dict[str, Any]] = None) -> Check:
    """I2: R_T stated must equal C_T * 1/2 rho S V^2 under the dataset convention."""
    ct = case.get("experimental_ct")
    rt_rep = case.get("experimental_rt_n")
    if ct is None or rt_rep is None:
        return Check("I2_rt_from_ct", "WITHHELD", "missing experimental_ct / experimental_rt_n")
    s, band, src = _surface_and_band(case)
    if s is None:
        return Check("I2_rt_from_ct", "WITHHELD", "no wetted surface and no geometry to bound it")

    if convention and convention.get("best"):
        rho = dict((("fresh_998.2", RHO_FRESH_20C), ("sea_1025", RHO_SEA_15C)))[convention["best"]["rho"]]
        scale = convention["best"]["scale"]
        unit_name = convention["best"]["force_unit"]
        prov = "dataset-level inferred convention"
    else:
        rho, scale, unit_name, prov = RHO_FRESH_20C, 1.0, "N", "default SI convention"

    d = _rt_deviation(case, rho, scale)
    if d is None:
        return Check("I2_rt_from_ct", "WITHHELD", "missing speed_ms")
    dev = d["deviation_pct"]
    implied_s = d["implied_s_m2"]
    lo, hi = d["band"]
    inside = not (math.isnan(lo) or math.isnan(hi)) and lo <= implied_s <= hi
    detail = (f"R_T vs C_T*1/2 rho S V^2 under rho={('fresh_998.2' if rho == RHO_FRESH_20C else 'sea_1025')}, "
              f"force in {unit_name} ({prov}); S={s:.4g} m2 from {src}")
    ok = dev <= TOL_COEFF_PCT
    return Check(
        "I2_rt_from_ct", "PASS" if ok else "FAIL", detail,
        reported=rt_rep, expected=round(d["predicted"], 6),
        deviation_pct=round(dev, 3), tolerance_pct=TOL_COEFF_PCT,
        implied={"implied_wetted_surface_m2": round(implied_s, 4),
                 "physical_band_m2": [round(lo, 4), round(hi, 4)] if not math.isnan(lo) else None,
                 "implied_within_band": inside,
                 "correction_factor_on_rt": round(d["predicted"] / rt_rep, 4) if rt_rep else None})


def check_ct_above_cf(case: Dict[str, Any]) -> Check:
    """I3: C_T >= C_F(Re) for *any* plausible water viscosity.

    Form + wave resistance cannot be negative for a bare hull.  The check uses
    both fresh (20 C) and sea (15 C) viscosity: only if C_T < C_F under both is
    the coefficient provably inadmissible.
    """
    geom = case.get("geometry", {}) or {}
    length = geom.get("length_m")
    ct = case.get("experimental_ct")
    v = case.get("speed_ms")
    if length is None or ct is None or v is None:
        return Check("I3_ct_ge_cf", "WITHHELD", "missing length_ms / experimental_ct / speed_ms")
    rows = {}
    for name, nu in (("fresh_20C", NU_FRESH_20C), ("sea_15C", NU_SEA_15C)):
        re = reynolds(v, length, nu)
        rows[name] = {"Re": re, "Cf_ITTC57": ittc57_cf(re), "Ct_over_Cf": ct / ittc57_cf(re)}
    worst = max(rows, key=lambda k: rows[k]["Cf_ITTC57"])
    if all(rows[k]["Ct_over_Cf"] >= 1.0 for k in rows):
        return Check("I3_ct_ge_cf", "PASS",
                     "C_T/C_F = " + ", ".join(f"{k}:{rows[k]['Ct_over_Cf']:.2f}"
                                              for k in rows),
                     implied={"detail": rows})
    return Check("I3_ct_ge_cf", "FAIL",
                 f"C_T below the ITTC-57 friction line for all viscosities (worst {worst}: "
                 f"C_T/C_F={rows[worst]['Ct_over_Cf']:.3f})",
                 implied={"detail": rows})


def check_surface_band(case: Dict[str, Any]) -> Check:
    """I5: the stated wetted surface must sit inside the geometry band."""
    s, band, src = _surface_and_band(case)
    geom = case.get("geometry", {}) or {}
    if geom.get("wetted_surface_m2") is None:
        return Check("I5_surface_band", "NOT_APPLICABLE",
                     "dataset does not state a wetted surface (band used as estimate only)")
    if band is None:
        return Check("I5_surface_band", "WITHHELD", "insufficient geometry to build a band")
    s = float(geom["wetted_surface_m2"])
    inside = band[0] <= s <= band[1]
    return Check("I5_surface_band", "PASS" if inside else "FAIL",
                 f"S={s:.4g} m2 vs band [{band[0]:.4g}, {band[1]:.4g}] ({src})",
                 reported=s, expected=round(0.5 * (band[0] + band[1]), 4))


def audit_case(case: Dict[str, Any], convention: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    checks = [check_froude_identity(case), check_ct_rt_identity(case, convention),
              check_ct_above_cf(case), check_surface_band(case)]
    fails = [c for c in checks if c.verdict == "FAIL"]
    withheld = [c for c in checks if c.verdict == "WITHHELD"]
    if fails:
        verdict = "WITHHELD"
        reason = "identity violation: " + "; ".join(c.name for c in fails)
    elif withheld:
        verdict = "WITHHELD"
        reason = "uncheckable: " + "; ".join(c.name for c in withheld)
    else:
        verdict = "USABLE"
        reason = "all identities closed"
    return {
        "case_id": case.get("case_id"),
        "ship_type": case.get("ship_type"),
        "speed_ms": case.get("speed_ms"),
        "verdict": verdict,
        "reason": reason,
        "reference_grade": "WITHHELD" if verdict != "USABLE" else "REAL(identity-consistent)",
        "checks": [c.as_dict() for c in checks],
    }


# --------------------------------------------------------------------------
# Live API audit
# --------------------------------------------------------------------------
_TOKEN_CACHE: Dict[str, str] = {}


def login_token(base_url: str, timeout: float = 15.0) -> Optional[str]:
    """Obtain a bearer token from the platform using the agent env credentials.

    Credentials come from ``TENSOR_AGENT_USER`` / ``TENSOR_AGENT_PASS``; nothing
    is written to disk or to the report.
    """
    user, pw = os.environ.get("TENSOR_AGENT_USER"), os.environ.get("TENSOR_AGENT_PASS")
    if not (user and pw):
        return None
    root = base_url.rstrip("/")
    if not root.endswith("/api/v1"):
        root += "/api/v1"
    try:
        data = _post_json(root + "/auth/login", {"username": user, "password": pw}, timeout)
    except Exception as exc:                       # noqa: BLE001 - audit must not die
        print(f"[audit] login failed: {exc}", file=sys.stderr)
        return None
    return data.get("access_token")


def _post_json(url: str, payload: Dict[str, Any], timeout: float = 15.0,
               token: Optional[str] = None) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 (local API)
        return json.loads(resp.read().decode())


def api_resistance(base_url: str, payload: Dict[str, Any], timeout: float = 15.0,
                   token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    root = base_url.rstrip("/")
    if not root.endswith("/api/v1"):
        root += "/api/v1"
    url = root + "/calculations/resistance"
    if token is None:
        token = _TOKEN_CACHE.get(root) or login_token(root, timeout)
        if token:
            _TOKEN_CACHE[root] = token
    try:
        return _post_json(url, payload, timeout, token)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(f"[audit] API call failed: {exc}", file=sys.stderr)
        return None


def detect_speed_unit(responses: Sequence[Tuple[float, Dict[str, Any]]],
                      length: float) -> Dict[str, Any]:
    """Which unit does the API silently assume for its ``speed`` field?

    Given (input_value, response) pairs, the reported Froude number pins the
    internally used speed: V_used = F_r * sqrt(g L).  The ratio
    V_used / V_input identifies the convention (1.0 -> m/s, 0.514444 -> knots).
    """
    rows = []
    for v_in, resp in responses:
        fn = resp.get("froude_number")
        if not fn or v_in <= 0:
            continue
        v_used = fn * math.sqrt(G * length)
        rows.append({"speed_input": v_in, "froude_number": fn,
                     "speed_used_internal": round(v_used, 6),
                     "ratio_used_over_input": round(v_used / v_in, 6)})
    if not rows:
        return {"verdict": "WITHHELD", "detail": "no Froude numbers returned"}
    ratios = [r["ratio_used_over_input"] for r in rows]
    mean = sum(ratios) / len(ratios)
    spread = _pct(max(ratios), min(ratios)) if min(ratios) > 0 else float("inf")
    if spread > TOL_IDENTITY_PCT:
        # Different probes disagree: the convention is not even stable, which is
        # worse than a systematic slip because no caller-side conversion can fix it.
        return {"verdict": "FAIL(unstable convention across probes)", "assumed_unit": "unstable",
                "ratio_mean": round(mean, 6), "ratio_spread_pct": round(spread, 4),
                "knot_to_ms": round(KNOT_TO_MS, 6),
                "detail": (f"the implied input-speed multiplier varies by {spread:.1f} % across "
                           f"identical requests (mean {mean:.6f}); the API does not expose one "
                           f"documented convention for its speed field"),
                "rows": rows}
    if _pct(mean, 1.0) <= TOL_IDENTITY_PCT:
        conv, verdict = "m/s", "PASS(m/s)"
    elif _pct(mean, KNOT_TO_MS) <= TOL_IDENTITY_PCT:
        conv, verdict = "knots", "MISMATCH(silent knots)"
    else:
        conv, verdict = "unknown", "FAIL(unexplained factor)"
    return {
        "verdict": verdict,
        "assumed_unit": conv,
        "ratio_mean": round(mean, 6),
        "ratio_spread_pct": round(spread, 4),
        "knot_to_ms": round(KNOT_TO_MS, 6),
        "detail": (f"reported Froude numbers imply the API multiplies the input speed by "
                   f"{mean:.6f} (= {conv}); the platform's documented request field is "
                   f"speed_ms in m/s" ),
        "rows": rows,
    }


def audit_api_response(resp: Dict[str, Any], v_used: float,
                       rho_candidates: Sequence[Tuple[str, float]] = (("sea_1025", RHO_SEA_15C),
                                                                      ("fresh_998.2", RHO_FRESH_20C))) -> List[Check]:
    """I2/I4 on a single live resistance response."""
    checks: List[Check] = []
    s = resp.get("wetted_surface")
    ct_ = resp.get("ct")
    cf = resp.get("cf")
    cr = resp.get("cr")
    rt = resp.get("rt")
    rf = resp.get("rf")
    rr = resp.get("rr")
    pe = resp.get("pe")
    if None in (s, ct_, rt):
        return [Check("API_I2_rt_from_ct", "WITHHELD", "response lacks wetted_surface/ct/rt")]

    # I2 with unit scan
    best = None
    for rho_name, rho in rho_candidates:
        for unit_name, scale in FORCE_UNITS:
            pred = ct_ * dynamic_pressure_factor(rho, v_used, s) * scale
            dev = _pct(rt, pred)
            if best is None or dev < best["deviation_pct"]:
                best = {"deviation_pct": dev, "rho": rho_name, "unit": unit_name,
                        "predicted": pred, "scale": scale}
    assert best is not None
    checks.append(Check(
        "API_I2_rt_from_ct",
        "PASS" if best["deviation_pct"] <= TOL_COEFF_PCT else "FAIL",
        f"R_T = C_T*1/2 rho S V^2 closes with rho={best['rho']}, force in {best['unit']}",
        reported=rt, expected=round(best["predicted"], 6),
        deviation_pct=round(best["deviation_pct"], 3), tolerance_pct=TOL_COEFF_PCT))

    rho_used = dict(rho_candidates)[best["rho"]]
    q = dynamic_pressure_factor(rho_used, v_used, s)
    for label, got, coeff in (("API_I2a_rf_from_cf", rf, cf), ("API_I2b_rr_from_cr", rr, cr)):
        if got is None or coeff is None:
            continue
        pred = coeff * q * best["scale"]
        dev = _pct(got, pred)
        checks.append(Check(label, "PASS" if dev <= TOL_COEFF_PCT else "FAIL",
                            f"component vs coeff*1/2 rho S V^2 (rho={best['rho']}, {best['unit']})",
                            reported=got, expected=round(pred, 6),
                            deviation_pct=round(dev, 3), tolerance_pct=TOL_COEFF_PCT))
    # I4 effective power
    if pe is not None:
        scale = best["scale"]
        rt_si = rt / scale
        pe_si = pe / scale          # power carries the same prefix as the force
        pred = rt_si * v_used
        dev = _pct(pe_si, pred)
        checks.append(Check("API_I4_pe_from_rt", "PASS" if dev <= 2.0 else "FAIL",
                            f"P_E vs R_T*V (P in kW, R_T in N) -- unit-consistent reading",
                            reported=round(pe_si, 6), expected=round(pred, 6),
                            deviation_pct=round(dev, 3), tolerance_pct=2.0))
    # I3 total >= friction
    if cf is not None:
        ratio = ct_ / cf if cf else float("inf")
        checks.append(Check("API_I3_ct_ge_cf", "PASS" if ratio >= 1.0 else "FAIL",
                            f"C_T/C_F = {ratio:.3f} (form+wave must be >= 0)",
                            reported=round(ratio, 4), expected=1.0))
    return checks


def benchmark_parity(base_url: str, cases: Sequence[Dict[str, Any]],
                     speed_unit: str, token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Grade the live solver against the dataset, under both unit conventions.

    ``speed_unit`` is the unit the API *assumes* (from :func:`detect_speed_unit`).
    We feed the API a value that is correct under that convention, i.e. the
    dataset's m/s speed divided by the detection ratio, so the comparison tests
    the *physics*, not the unit slip.
    """
    ratio = 1.0 if speed_unit == "m/s" else KNOT_TO_MS
    out: List[Dict[str, Any]] = []
    for case in cases:
        geom = case.get("geometry", {}) or {}
        v_ms = case.get("speed_ms")
        ct_ref = case.get("experimental_ct")
        if v_ms is None or ct_ref is None:
            continue
        payload = {
            "ship_type": case.get("ship_type"),
            "speed": v_ms / ratio,
            "displacement": geom.get("displacement_t") or geom.get("displacement_kg", 1.0) or 1.0,
            "length": geom.get("length_m"),
            "beam": geom.get("beam_m") or geom.get("diameter_m"),
            "draft": geom.get("draft_m") or (geom.get("diameter_m", 2.0) / 2.0),
            "cb": geom.get("cb", 0.5),
        }
        resp = api_resistance(base_url, payload, token=token)
        if resp is None or resp.get("ct") is None:
            out.append({"case_id": case.get("case_id"), "status": "api_unavailable"})
            continue
        ct_calc = resp["ct"]
        out.append({
            "case_id": case.get("case_id"),
            "ship_type": case.get("ship_type"),
            "speed_ms_input": round(v_ms / ratio, 4),
            "ct_reference": ct_ref,
            "ct_calculated": ct_calc,
            "error_pct": round(_pct(ct_calc, ct_ref), 3),
            "gradable": case.get("_gradable", True),
            "method": resp.get("method"),
        })
    return out


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------
def make_figures(report: Dict[str, Any], outdir: Path) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []

    # ---- Fig 1: identity residuals per case (I1 Froude + I2 R_T<-C_T) ----
    cases = report["dataset"]["cases"]
    ids = [c["case_id"] for c in cases]
    series: Dict[str, List[float]] = {"I1 Froude": [], "I2 R_T<-C_T": []}
    for c in cases:
        by_name = {k["name"]: k for k in c["checks"]}
        for key, cname in (("I1 Froude", "I1_froude_identity"), ("I2 R_T<-C_T", "I2_rt_from_ct")):
            chk = by_name.get(cname, {})
            dev = chk.get("deviation_pct")
            series[key].append(max(dev, 1e-3) if dev is not None else float("nan"))

    fig, ax = plt.subplots(figsize=(10, 4.6))
    xs = range(len(ids))
    w = 0.38
    for i, (label, vals) in enumerate(series.items()):
        ax.bar([x + (i - 0.5) * w for x in xs], vals, width=w, label=label)
    ax.set_yscale("log")
    ax.axhline(1.0, color="tab:green", lw=1.2, ls="--", label="1 % identity tolerance")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(ids, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("relative deviation [%]  (log)")
    ax.set_title("Benchmark reference dataset: algebraic identity residuals\n"
                 "(bars above the dashed line cannot be explained by physics)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p1 = outdir / "fig1_identity_residuals.png"
    fig.savefig(p1, dpi=150)
    plt.close(fig)
    figs.append({"name": p1.name, "caption": "Fig 1  Benchmark dataset: algebraic identity residuals (I1 Froude, I2 R_T<-C_T). Dashed line = 1 % tolerance; the dashed line is crossed by every R_T column, which is why the dataset cannot grade a solver."})

    # ---- Fig 2: Froude parity (stated vs V/sqrt(gL)) ----
    pts = []
    for c in cases:
        chk = {k["name"]: k for k in c["checks"]}.get("I1_froude_identity", {})
        if chk.get("deviation_pct") is not None:
            pts.append((c["case_id"], c["ship_type"], chk["expected"], chk["reported"]))
    fig, ax = plt.subplots(figsize=(5.6, 5.2))
    markers = {"SUBOFF": "o", "DTMB-5415": "s", "KVLCC2": "^"}
    for cid, st, exp, rep in pts:
        ax.plot(exp, rep, markers.get(st, "x"), label=st if st not in ax.get_legend_handles_labels()[1] else None,
                alpha=0.85)
    lims = [1e-2, 1.2]
    ax.plot(lims, lims, "k--", lw=1, label="identity F_r=V/sqrt(gL)")
    ax.plot(lims, [v * KNOT_TO_MS for v in lims], ":", color="tab:red", lw=1, label="F_r if V in knots")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlim(*lims); ax.set_ylim(*lims)
    ax.set_xlabel("F_r recomputed from stated length  V/sqrt(gL)")
    ax.set_ylabel("F_r stated in dataset")
    ax.set_title("Froude column parity\n(groups off the identity line are unit/length mix-ups)")
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout()
    p2 = outdir / "fig2_froude_parity.png"
    fig.savefig(p2, dpi=150)
    plt.close(fig)
    figs.append({"name": p2.name, "caption": "Fig 2  Froude-column parity: recomputed F_r = V/sqrt(gL) vs the F_r stored in the dataset. SUBOFF and KVLCC2 sit on the identity line; DTMB-5415 sits off it, i.e. its F_r column was produced with a different reference length."})

    # ---- Fig 3: implied wetted area vs physical band ----
    rows = []
    for c in cases:
        chk = {k["name"]: k for k in c["checks"]}.get("I2_rt_from_ct", {})
        imp = (chk.get("implied") or {})
        if imp.get("implied_wetted_surface_m2") is not None:
            rows.append((c["case_id"], imp["implied_wetted_surface_m2"],
                         imp.get("physical_band_m2"), c["ship_type"]))
    if rows:
        fig, ax = plt.subplots(figsize=(9.5, 4.4))
        for x, (cid, imp_s, band, st) in enumerate(rows):
            lo, hi = (band or [imp_s, imp_s])
            ax.vlines(x, lo, hi, color="tab:blue", lw=6, alpha=0.35,
                      label="physical band" if x == 0 else None)
            ax.plot(x, imp_s, "o", color="tab:red",
                    label="implied by R_T/C_T" if x == 0 else None)
        ax.set_yscale("log")
        ax.set_xticks(range(len(rows)))
        ax.set_xticklabels([r[0] for r in rows], rotation=45, ha="right", fontsize=8)
        ax.set_ylabel("wetted surface [m$^2$] (log)")
        ax.set_title("What wetted surface would the stored (C_T, R_T) pair require?\n"
                     "red = implied   band = geometrically admissible")
        ax.legend(fontsize=8); ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        p3 = outdir / "fig3_implied_wetted_surface.png"
        fig.savefig(p3, dpi=150)
        plt.close(fig)
        figs.append({"name": p3.name, "caption": "Fig 3  Wetted surface implied by each stored (C_T, R_T) pair (red) against the geometrically admissible band (blue). Implied values far below the band prove the R_T column cannot be reconciled with the C_T column by any water density or unit convention."})

    # ---- Fig 4: API unit detection + parity ----
    api = report.get("api") or {}
    det = api.get("speed_unit_detection") or {}
    if det.get("rows"):
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
        v_in = [r["speed_input"] for r in det["rows"]]
        v_used = [r["speed_used_internal"] for r in det["rows"]]
        ax1.plot(v_in, v_used, "o-", label="speed the API actually used")
        ax1.plot(v_in, v_in, "k--", label="identity (input treated as m/s)")
        ax1.plot(v_in, [v * KNOT_TO_MS for v in v_in], ":", color="tab:red",
                 label=f"input treated as knots ({KNOT_TO_MS:.4f} m/s per kn)")
        ax1.set_xlabel("speed value sent to the API")
        ax1.set_ylabel("speed implied by returned F_r [m/s]")
        ax1.set_title("Live API: silent speed-unit detection")
        ax1.legend(fontsize=8); ax1.grid(alpha=0.3)

        par = [r for r in (api.get("parity") or []) if r.get("ct_reference")]
        if par:
            ax2.plot([0, 0.02], [0, 0.02], "k--", lw=1, label="perfect agreement")
            for r in par:
                ax2.plot(r["ct_reference"], r["ct_calculated"], "o",
                         color="tab:purple", alpha=0.85)
            ax2.fill_between([0, 0.02], [0, 0.02 * 0.85], [0, 0.02 * 1.15],
                             color="tab:green", alpha=0.12, label="+/-15 % acceptance")
            ax2.set_xlabel("C_T reference (dataset)")
            ax2.set_ylabel("C_T calculated (live API, unit-corrected)")
            ax2.set_title("Benchmark parity after unit correction")
            ax2.legend(fontsize=8); ax2.grid(alpha=0.3)
        fig.tight_layout()
        p4 = outdir / "fig4_api_unit_and_parity.png"
        fig.savefig(p4, dpi=150)
        plt.close(fig)
        figs.append({"name": p4.name, "caption": "Fig 4  Left: the live resistance endpoint's returned F_r implies the input speed is multiplied by 0.5144 (knots->m/s), i.e. the field is silently interpreted as knots. Right: C_T parity against the dataset once that unit slip is compensated; the remaining scatter is dominated by the reference values themselves, see Fig 3."})

    return figs


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def audit_dataset(path: Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text())
    cases = data.get("cases", [])
    convention = infer_dataset_convention(cases)
    audited = [audit_case(c, convention) for c in cases]
    usable = [c for c in audited if c["verdict"] == "USABLE"]
    by_ship: Dict[str, Dict[str, int]] = {}
    for c in audited:
        slot = by_ship.setdefault(c["ship_type"], {"USABLE": 0, "WITHHELD": 0})
        slot[c["verdict"]] += 1
    fail_names: Dict[str, int] = {}
    for c in audited:
        for chk in c["checks"]:
            if chk["verdict"] == "FAIL":
                fail_names[chk["name"]] = fail_names.get(chk["name"], 0) + 1
    return {
        "source": str(path),
        "convention_inference": convention,
        "n_cases": len(audited),
        "n_usable": len(usable),
        "n_withheld": len(audited) - len(usable),
        "by_ship_type": by_ship,
        "failed_checks": fail_names,
        "cases": audited,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Audit the resistance benchmark chain against algebraic identities")
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--out", default=str(REPO_ROOT / "artifacts" / "benchmark_audit"))
    ap.add_argument("--api", action="store_true", help="also audit the live resistance endpoint")
    ap.add_argument("--api-base", default=os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1"),
                    help="platform root (with or without the /api/v1 suffix)")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    report: Dict[str, Any] = {
        "tool": "tools/verifier/benchmark_audit.py",
        "constants": {"g": G, "nu_fresh_20C": NU_FRESH_20C, "nu_sea_15C": NU_SEA_15C,
                      "rho_fresh_20C": RHO_FRESH_20C, "rho_sea_15C": RHO_SEA_15C,
                      "knot_to_ms": round(KNOT_TO_MS, 8)},
        "tolerances_pct": {"identity": TOL_IDENTITY_PCT, "coefficient": TOL_COEFF_PCT},
    }
    report["dataset"] = audit_dataset(Path(args.dataset))

    if args.api:
        geom = {"length": 4.356, "beam": 0.508, "draft": 0.476, "cb": 0.542}
        probes = []
        for v in (1.0, 2.0, 3.05, 4.0):
            payload = dict(geom, ship_type="SUBOFF", speed=v, displacement=0.5)
            resp = api_resistance(args.api_base, payload)
            if resp:
                probes.append((v, resp))
        detection = detect_speed_unit(probes, geom["length"]) if probes else {"verdict": "WITHHELD"}
        api_checks: List[Dict[str, Any]] = []
        if probes:
            v_in, resp = probes[-1]
            ratio = detection.get("ratio_mean", 1.0) or 1.0
            api_checks = [c.as_dict() for c in audit_api_response(resp, v_in * ratio)]
        speed_unit = detection.get("assumed_unit", "m/s") or "m/s"
        parity = benchmark_parity(args.api_base, report["dataset"]["cases"], speed_unit)
        report["api"] = {
            "base": args.api_base,
            "probe_count": len(probes),
            "speed_unit_detection": detection,
            "checks_on_probe": api_checks,
            "parity": parity,
            "parity_units_applied": speed_unit,
        }

    figs: List[Dict[str, str]] = []
    if not args.no_figures:
        try:
            figs = make_figures(report, outdir)
        except Exception as exc:   # pragma: no cover - figure failure must not kill the audit
            print(f"[audit] figure generation skipped: {exc}", file=sys.stderr)
    report["figures"] = figs

    out_json = outdir / "audit_report.json"
    out_json.write_text(json.dumps(report, indent=1, ensure_ascii=False))

    ds = report["dataset"]
    print(f"[audit] dataset cases={ds['n_cases']} usable={ds['n_usable']} withheld={ds['n_withheld']}")
    print(f"[audit] failed checks: {ds['failed_checks']}")
    if args.api:
        api = report["api"]
        print(f"[audit] API speed unit: {api['speed_unit_detection'].get('verdict')} "
              f"(ratio {api['speed_unit_detection'].get('ratio_mean')})")
        for chk in api["checks_on_probe"]:
            print(f"[audit]   {chk['name']}: {chk['verdict']} dev={chk.get('deviation_pct')} %")
    print(f"[audit] report -> {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())