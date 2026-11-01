"""Resistance endpoint contract & analytic-consistency gate.

Black-box verification of the shipped `POST /api/v1/calculations/resistance`
endpoint (`calc.resistance`, ledger evidence grade REAL) against a reference
chain that is *entirely closed form* and never calls the platform:

    Re     = V L / nu                    (definition)
    Fr     = V / sqrt(g L)               (definition)
    Cf     = 0.075 / (log10 Re - 2)^2    (ITTC-1957)
    Cf_eff = Cf + dCf,  dCf = 0.4e-3     (ITTC-1978 roughness allowance)
    Rr     = 0.5 rho V^2 S Cr            (definition of the residual coefficient)
    Pe     = Rt V                        (effective-power definition)

History: mission 1582 ran this gate on the then-shipped endpoint and pinned five
contract defects (D1 knots unit undeclared, D2 cf/ct field incoherence,
D3 no geometry validation, D4 displacement ignored, D5 V=0 -> HTTP 500).
Mission 1611 fixed all five in the runtime repo; the same checks are kept and
must now pass, with the *same* tolerances, so that the fix is not a tolerance
relaxation.

Two outcome classes are kept separate on purpose:

  * MATH (C0-C7, C12): does the endpoint compute what its own `method` string
    claims ("ITTC-57 friction + ITTC-1978 roughness + Holtrop residual")?
    Target <3 %; measured <0.05 %.
  * CONTRACT (C8-C14): is the answer usable, self-consistent, unit-explicit and
    fail-safe?  Field closure (`cf_total + cr == ct`), declared units (schema +
    OpenAPI), `m/s` honoured numerically, rejected impossible geometry,
    4xx (not 5xx) at V<=0.

CLI:
    python3 tools/verifier/audit_resistance_contract.py \
        --captures artifacts/verifier/resistance_contract/captures.json \
        --out      artifacts/verifier/resistance_contract/gate_report.json \
        --figs     artifacts/verifier/resistance_contract

Exit code 0 iff every MATH *and* every CONTRACT check passes.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List

# --- reference-chain constants ------------------------------------------------
KN_TO_MS = 1852.0 / 3600.0          # 0.5144444... exact
KN_TO_MS_LEGACY = 0.5144            # the 4-d.p. value shipped before the D1 fix
G = 9.81
RHO = 1025.0
NU = 1.19e-6
DCF_ROUGHNESS = 4.0e-4              # ITTC-1978 roughness allowance

TOL = 0.10                          # % ; closed forms + printed quantization
TOL_TARGET = 3.0                    # % ; the acceptance line that matters
CB_MIN, CB_MAX = 0.30, 1.00         # physically realisable block-coefficient window
GEOM_TOL_PCT = 5.0                  # contract: |nabla - L*B*T*Cb| / nabla


def ittc1957_cf(re: float) -> float:
    """ITTC-1957 friction line."""
    return 0.075 / (math.log10(re) - 2.0) ** 2


def _rel(a: float, b: float) -> float:
    """Signed relative deviation of a from reference b, in percent."""
    return (a - b) / b * 100.0


def _row(check: str, kind: str, case: str, quantity: str, ref: float,
         got: float, tol: float, unit: str = "") -> Dict[str, Any]:
    err = _rel(got, ref)
    return {
        "check": check, "kind": kind, "case": case, "quantity": quantity,
        "reference": ref, "platform": got, "rel_err_pct": err,
        "tol_pct": tol, "unit": unit,
        "verdict": "PASS" if abs(err) <= tol else "FAIL",
    }


def _speed_ms(req: Dict[str, Any]) -> float:
    """Declared-unit speed in m/s (D1: the unit now travels with the request)."""
    unit = req.get("speed_unit", "kn")
    return req["speed"] * KN_TO_MS if unit == "kn" else req["speed"]


def analyse(captures: Dict[str, Any]) -> Dict[str, Any]:
    """Run every check over the captured cases and return the gate report."""
    rows: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []
    all_cases = captures["cases"]
    ok_cases = [c for c in all_cases if c.get("http_status") == 200]
    recovered_kn: List[float] = []

    for c in ok_cases:
        req, res = c["request"], c["response"]
        v = _speed_ms(req)
        kind_kn = req.get("speed_unit", "kn") == "kn"
        # C0: recover the conversion constant from Fr alone (g and L are declared)
        if kind_kn:
            recovered_kn.append(res["froude_number"] * math.sqrt(G * req["length"]) / req["speed"])
        unit_note = "from knots" if kind_kn else "declared m/s"

        rows.append(_row("C1", "MATH", c["case_id"],
                         f"reynolds_number (V in m/s, {unit_note})",
                         v * req["length"] / NU, res["reynolds_number"], TOL, "-"))
        rows.append(_row("C2", "MATH", c["case_id"], "froude_number",
                         v / math.sqrt(G * req["length"]), res["froude_number"], TOL, "-"))
        rows.append(_row("C3", "MATH", c["case_id"], "cf vs ITTC-1957",
                         ittc1957_cf(res["reynolds_number"]), res["cf"], TOL, "-"))

        q = 0.5 * RHO * v * v * res["wetted_surface"]      # N per unit coefficient
        rows.append(_row("C4", "MATH", c["case_id"], "rr vs 0.5*rho*V^2*S*cr",
                         q * res["cr"] / 1000.0, res["rr"], TOL, "kN"))
        rows.append(_row("C5", "MATH", c["case_id"], "pe vs rt*V",
                         res["rt"] * v, res["pe"], TOL, "kW"))

        cf_eff = res["rf"] * 1000.0 / q
        dcf = cf_eff - res["cf"]
        rows.append(_row("C6", "MATH", c["case_id"], "implied dCf vs ITTC-1978 0.4e-3",
                         DCF_ROUGHNESS, dcf, 1.0, "-"))
        rows.append(_row("C7", "MATH", c["case_id"], "ct vs (cf+dCf+cr) reconciled",
                         res["cf"] + dcf + res["cr"], res["ct"], TOL, "-"))

        # C8 (D2): the returned coefficient field must close by itself.  Before
        # the fix `cf_total` did not exist and cf+cr was 1.8-6.6 % below ct.
        cf_field = res.get("cf_total")
        closure = (cf_field if cf_field is not None else res["cf"]) + res["cr"]
        row = _row("C8", "CONTRACT", c["case_id"],
                   "ct vs (cf_total+cr) as returned", closure, res["ct"], TOL, "-")
        row["detail"] = {"cf_total_returned": cf_field is not None,
                         "dcf_returned": res.get("dcf")}
        rows.append(row)

    kn_mean = sum(recovered_kn) / len(recovered_kn)
    bias_legacy = ((KN_TO_MS / KN_TO_MS_LEGACY) ** 2.66 - 1.0) * 100.0
    global_rows = [{
        "check": "C0", "kind": "MATH", "case": f"all {len(recovered_kn)} knot cases",
        "quantity": "knot->m/s conversion recovered from Fr",
        "reference": KN_TO_MS, "platform": kn_mean,
        "rel_err_pct": _rel(kn_mean, KN_TO_MS), "tol_pct": 0.05, "unit": "m/s per knot",
        "verdict": "PASS" if abs(_rel(kn_mean, KN_TO_MS)) <= 0.05 else "FAIL",
        "detail": {"recovered_min": min(recovered_kn), "recovered_max": max(recovered_kn),
                   "exact_1852_over_3600": KN_TO_MS,
                   "legacy_constant": KN_TO_MS_LEGACY,
                   "legacy_bias_removed_pct": bias_legacy,
                   "note": ("pre-fix the endpoint used 0.5144 (4 d.p.) -> -0.0086% on Re/Fr "
                            "and ~-0.023% on Rt through the dlnRt/dlnV~2.66 power law")},
    }]

    # C9 (D4): does `displacement` matter, or is the inconsistent pair rejected?
    base = next(c for c in ok_cases if c["case_id"] == "boxbarge_10kn")
    light = next((c for c in all_cases if c["case_id"] == "boxbarge_10kn_disp8000"), None)
    if light is not None:
        rejected = light["http_status"] >= 400
        same = (not rejected) and all(
            abs(base["response"][k] - light["response"][k]) <= 1e-12
            for k in ("rt", "ct", "wetted_surface", "pe"))
        notes.append({
            "check": "C9", "kind": "CONTRACT", "case": "boxbarge_10kn_disp8000",
            "quantity": "displacement 16400 m3 vs 8000 m3 (nabla = L*B*T*Cb = 16000 m3)",
            "reference": "accept only if it changes the answer, else 422 on the inconsistency",
            "platform": (f"HTTP {light['http_status']} (geometry gate)" if rejected
                         else "identical response" if same else "response changed"),
            "rel_err_pct": float("nan"), "tol_pct": 0.0, "unit": "-",
            "verdict": "PASS" if (rejected or not same) else "FAIL",
            "defect": None if (rejected or not same) else "D4",
        })

    # C10 (D3): impossible block coefficient must be rejected.
    bad = next((c for c in all_cases if c["case_id"] == "invalid_cb_1p2"), None)
    if bad is not None:
        notes.append({
            "check": "C10", "kind": "CONTRACT", "case": "invalid_cb_1p2",
            "quantity": "Cb = 1.2 acceptance",
            "reference": "Cb = nabla/(L B T) <= 1 by definition -> 4xx",
            "platform": f"HTTP {bad['http_status']}",
            "rel_err_pct": float("nan"), "tol_pct": 0.0, "unit": "-",
            "verdict": "PASS" if bad["http_status"] >= 400 else "FAIL",
            "defect": None if bad["http_status"] >= 400 else "D3",
        })

    # C11 (D5): V=0 and V<0 must be 4xx, never 5xx.
    for cid, tag in (("zero_speed", "C11"), ("negative_speed", "C11b")):
        c = next((x for x in all_cases if x["case_id"] == cid), None)
        if c is None:
            continue
        ok = 400 <= c["http_status"] < 500
        notes.append({
            "check": tag, "kind": "CONTRACT", "case": cid,
            "quantity": f"V {'=' if tag == 'C11' else '<'} 0 handling",
            "reference": "reject with 4xx (Re<=0 -> Cf undefined), never 5xx",
            "platform": f"HTTP {c['http_status']}", "rel_err_pct": float("nan"),
            "tol_pct": 0.0, "unit": "-", "verdict": "PASS" if ok else "FAIL",
            "defect": None if ok else "D5",
        })

    # C13 (D1): the unit contract must be *declared*, not inferred by the caller.
    oa = captures.get("meta", {}).get("openapi", {})
    props = oa.get("request_properties", {})
    speed_prop = props.get("speed", {}) or {}
    unit_prop = props.get("speed_unit", {}) or {}
    decl = {
        "speed_unit_field": bool(unit_prop),
        "speed_unit_enum": sorted(unit_prop.get("enum", []) or []),
        "speed_description_mentions_unit": any(
            k in (speed_prop.get("description", "") or "").lower()
            for k in ("kn", "knot", "节", "m/s")),
        "endpoint_description_mentions_units": any(
            k in (oa.get("endpoint_description", "") or "").lower()
            for k in ("kn", "节", "m/s", "∇")),
        "response_has_cf_total": "cf_total" in (oa.get("response_properties") or []),
        "response_has_dcf": "dcf" in (oa.get("response_properties") or []),
        "response_has_speed_ms": "speed_ms" in (oa.get("response_properties") or []),
    }
    decl_ok = (decl["speed_unit_field"] and decl["speed_unit_enum"] == ["kn", "m/s"]
               and decl["speed_description_mentions_unit"]
               and decl["endpoint_description_mentions_units"]
               and decl["response_has_cf_total"] and decl["response_has_dcf"]
               and decl["response_has_speed_ms"])
    notes.append({
        "check": "C13", "kind": "CONTRACT", "case": "openapi.json",
        "quantity": "units + self-consistent coefficient field declared in the schema",
        "reference": "speed_unit enum {kn,m/s}, descriptions name the unit, "
                     "response exposes dcf/cf_total/speed_ms",
        "platform": json.dumps(decl, ensure_ascii=False),
        "rel_err_pct": float("nan"), "tol_pct": 0.0, "unit": "-",
        "verdict": "PASS" if decl_ok else "FAIL",
        "defect": None if decl_ok else "D1",
        "detail": decl,
    })

    # C14 (D1): `m/s` must be honoured numerically, not just documented.
    ms = next((c for c in ok_cases if c["case_id"] == "kcs_like_15p5kn_ms"), None)
    kn = next((c for c in ok_cases if c["case_id"] == "kcs_like_15p5kn"), None)
    if ms is not None and kn is not None:
        err_rt = _rel(ms["response"]["rt"], kn["response"]["rt"])
        err_ct = _rel(ms["response"]["ct"], kn["response"]["ct"])
        err_v = _rel(ms["response"]["speed_ms"], _speed_ms(ms["request"]))
        ok = max(abs(err_rt), abs(err_ct), abs(err_v)) <= TOL
        notes.append({
            "check": "C14", "kind": "CONTRACT", "case": "kcs_like_15p5kn_ms",
            "quantity": "same physical speed declared as m/s vs knots",
            "reference": kn["response"]["rt"], "platform": ms["response"]["rt"],
            "rel_err_pct": err_rt, "tol_pct": TOL, "unit": "kN",
            "verdict": "PASS" if ok else "FAIL", "defect": None if ok else "D1",
            "detail": {"rt_knots_case_kN": kn["response"]["rt"],
                       "rt_ms_case_kN": ms["response"]["rt"],
                       "ct_rel_err_pct": err_ct, "speed_ms_rel_err_pct": err_v,
                       "declared_speed_ms": _speed_ms(ms["request"]),
                       "returned_speed_ms": ms["response"].get("speed_ms")},
        })

    # C12: monotonicity + measured power law (D1 consequence quantification)
    order = ["boxbarge_5kn", "boxbarge_10kn", "boxbarge_20kn"]
    seq = [next(c for c in ok_cases if c["case_id"] == k) for k in order]
    rt = [c["response"]["rt"] for c in seq]
    vs = [c["request"]["speed"] for c in seq]
    mono = all(b > a for a, b in zip(rt, rt[1:]))
    slope = math.log(rt[-1] / rt[0]) / math.log(vs[-1] / vs[0])
    rows.append({
        "check": "C12", "kind": "MATH", "case": "5/10/20 kn sweep",
        "quantity": "Rt monotone in V (power law dlnRt/dlnV)",
        "reference": 1.0, "platform": slope, "rel_err_pct": 0.0, "tol_pct": 0.0, "unit": "-",
        "verdict": "PASS" if mono else "FAIL",
        "detail": {"rt_kn_sweep": rt, "loglog_slope": slope,
                   "consequence_if_knots_read_as_ms_pct": (KN_TO_MS ** slope - 1.0) * 100.0},
    })
    rows = global_rows + rows

    math_rows = [r for r in rows if r["kind"] == "MATH"]
    contract_rows = [r for r in rows if r["kind"] == "CONTRACT"] + notes
    math_fail = [r for r in math_rows if r["verdict"] == "FAIL"]
    contract_fail = [r for r in contract_rows if r["verdict"] == "FAIL"]
    worst = max((abs(r["rel_err_pct"]) for r in math_rows if r["rel_err_pct"] == r["rel_err_pct"]),
                default=0.0)
    c8 = [r for r in rows if r["check"] == "C8"]
    c8_worst = max((abs(r["rel_err_pct"]) for r in c8), default=0.0)

    dcf_vals = [r["platform"] for r in rows if r["check"] == "C6"]
    defects = [
        {"id": "D1", "severity": "medium", "check": "C0/C1/C2/C12/C13/C14",
         "title": "undeclared knots unit contract",
         "status": "FIXED" if not any(r["check"] in ("C13", "C14") and r["verdict"] == "FAIL"
                                      for r in notes) else "OPEN",
         "was": (f"schema had only `speed`; responses closed only when read as knots "
                 f"(recovered 0.5144 m/s per knot), and Rt moves with dlnRt/dlnV="
                 f"{slope:.2f}, so a SI-m/s caller was off by {(1 - KN_TO_MS ** slope) * 100:.0f}%"),
         "fix": ("`speed_unit` enum {kn, m/s} (default kn) + Field/OpenAPI descriptions; "
                 "knot converted with the exact 1852/3600 instead of 0.5144 "
                 f"(removes a {bias_legacy:+.3f}% Rt bias); `speed_ms` echoed back")},
        {"id": "D2", "severity": "medium", "check": "C6/C7/C8",
         "title": "cf field and rf/ct are different quantities (dCf=0.400e-3 missing)",
         "status": "FIXED" if c8_worst <= TOL else "OPEN",
         "was": (f"cf+cr sat 1.8-6.6% below ct in every case; back-solving the force gave a "
                 f"constant dCf={sum(dcf_vals) / len(dcf_vals):.3e}"),
         "fix": (f"response now returns `dcf` and `cf_total = cf + dcf`; ct is formed from the "
                 f"rounded fields, so (cf_total+cr-ct)/ct = {c8_worst:.4f}% <= {TOL}%")},
        {"id": "D3", "severity": "high", "check": "C10",
         "title": "no geometry validation (Cb>1 accepted)",
         "status": "FIXED" if not any(r["check"] == "C10" and r["verdict"] == "FAIL"
                                      for r in notes) else "OPEN",
         "was": "Cb=1.2 (nabla > L*B*T) answered with HTTP 200 and a plausible-looking Rt",
         "fix": (f"model validator: {CB_MIN} < Cb <= {CB_MAX} and "
                 f"abs(nabla - L*B*T*Cb)/nabla < {GEOM_TOL_PCT:.0f}% -> 422 "
                 f"(geometry reply echoed as cb_from_dims / displacement_consistency_pct)")},
        {"id": "D4", "severity": "low", "check": "C9",
         "title": "displacement silently ignored",
         "status": "FIXED" if not any(r["check"] == "C9" and r["verdict"] == "FAIL"
                                      for r in notes) else "OPEN",
         "was": "16400 m3 and 8000 m3 returned bit-identical responses",
         "fix": ("displacement now enters the returned geometry verdict: an inconsistent "
                 "nabla is rejected with 422 naming both volumes; consistent cases echo "
                 "cb_from_dims / displacement_consistency_pct")},
        {"id": "D5", "severity": "medium", "check": "C11/C11b",
         "title": "V=0 raised an unhandled exception (HTTP 500)",
         "status": "FIXED" if not any(r["check"] in ("C11", "C11b") and r["verdict"] == "FAIL"
                                      for r in notes) else "OPEN",
         "was": "speed=0 -> 500 Internal Server Error",
         "fix": "Field(gt=0) on `speed` plus an explicit V<=0 guard -> 422"},
    ]

    # C15 (diagnostic, not a pass/fail gate): is the residual/friction split
    # physically plausible?  A fine, low-Froude hull must be friction dominated
    # (Holtrop's (1+k) form implies Cr/Cf ~ 0.1-0.3 up to Fn ~ 0.2); a ratio of
    # order unity or more means the *absolute* Rt level cannot be trusted even
    # though every closed-form identity above holds.  Reported, never gated,
    # because an external benchmark (model-test Rt curve) is out of scope here.
    diagnostics = []
    for c in ok_cases:
        req, res = c["request"], c["response"]
        ratio = res["cr"] / res["cf"]
        fn = res["froude_number"]
        fine = req["cb"] <= 0.75
        plausible = (ratio <= 1.0) or (not fine) or fn > 0.30
        diagnostics.append({
            "check": "C15", "case": c["case_id"], "cb": req["cb"],
            "froude_number": fn, "cf": res["cf"], "cr": res["cr"],
            "cr_over_cf": ratio,
            "flag": "PLAUSIBLE" if plausible else "HIGH_RESIDUAL",
            "note": ("摩擦主导，量级合理" if plausible else
                     "剩余阻力超过摩擦阻力，对细长低 Fn 船型不合物理；"
                     "说明 Cr 子模型是简化占位式，Rt 绝对值不可用于工程判定"),
        })

    return {
        "meta": {
            "endpoint": captures["meta"]["endpoint"],
            "captured_at": captures["meta"].get("captured_at"),
            "probe": captures["meta"].get("probe"),
            "cases_total": len(all_cases), "cases_used": len(ok_cases),
            "request_schema_found": captures["meta"].get("request_schema_found"),
            "reference_chain": {"kn_to_ms": KN_TO_MS, "g": G, "rho": RHO, "nu": NU,
                                "ittc1957": "cf = 0.075/(log10 Re - 2)^2",
                                "roughness_delta_cf": DCF_ROUGHNESS},
            "tolerances": {"MATH": TOL, "acceptance_line": TOL_TARGET},
        },
        "checks": rows,
        "observations": notes,
        "diagnostics": diagnostics,
        "summary": {
            "math_checks": len(math_rows), "math_failed": len(math_fail),
            "math_worst_abs_err_pct": worst,
            "contract_checks": len(contract_rows), "contract_failed": len(contract_fail),
            "c8_worst_abs_err_pct": c8_worst,
            "verdict_math": "PASS" if not math_fail else "FAIL",
            "verdict_contract": "PASS" if not contract_fail else "FAIL",
            "under_3pct": worst <= TOL_TARGET,
        },
        "defects": defects,
    }


# ----------------------------------------------------------------- figures ----
def make_figures(report: Dict[str, Any], captures: Dict[str, Any], outdir: Path) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    made: List[str] = []
    ok = [c for c in captures["cases"] if c.get("http_status") == 200]

    # fig1: Cf(Re) - ITTC-1957 curve vs platform points
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    res = [10 ** (6 + 0.02 * i) for i in range(0, 150)]
    ax.loglog(res, [ittc1957_cf(r) for r in res], "-", lw=1.6,
              label="ITTC-1957  cf = 0.075/(log10 Re - 2)^2")
    seen, first = set(), True
    for c in ok:
        key = round(c["response"]["reynolds_number"])
        if key in seen:
            continue
        seen.add(key)
        ax.loglog(c["response"]["reynolds_number"], c["response"]["cf"], "o", ms=8,
                  mfc="none", mew=1.8,
                  label=f"platform cf ({len(ok)} cases)" if first else None)
        first = False
    worst = report["summary"]["math_worst_abs_err_pct"]
    ax.set_xlabel("Re [-]")
    ax.set_ylabel("Cf [-]")
    ax.set_title(f"Fig.1  C3: endpoint cf on top of the ITTC-1957 line "
                 f"(worst MATH error {worst:.3f}%)", fontsize=10)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    p = outdir / "fig1_cf_vs_ittc1957.png"
    fig.tight_layout(); fig.savefig(p, dpi=130); plt.close(fig); made.append(str(p))

    # fig2: relative errors per check (MATH rows + the C8 closure rows)
    rows = [r for r in report["checks"]
            if r["rel_err_pct"] == r["rel_err_pct"]]
    fig, ax = plt.subplots(figsize=(9.6, 4.4))
    labels = [r["check"] for r in rows]
    errs = [abs(r["rel_err_pct"]) + 1e-5 for r in rows]
    colors = ["#c0392b" if r["kind"] == "CONTRACT" else "#2a7fbd" for r in rows]
    ax.bar(range(len(errs)), errs, color=colors)
    ax.set_yscale("log")
    for y, lab in ((0.1, "0.1% quantization tol"), (3.0, "3% target"),
                   (15.0, "15% unacceptable")):
        ax.axhline(y, ls="--", lw=0.9, color="gray")
        ax.text(len(errs) - 0.4, y * 1.12, lab, ha="right", fontsize=7, color="gray")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=90, fontsize=6)
    ax.set_ylabel("|relative error| [%]")
    ax.set_title("Fig.2  Per-check errors after the D1-D5 fix: closed forms (blue) and "
                 "the C8 coefficient closure (red) are all quantization-limited",
                 fontsize=9)
    fig.tight_layout(); p = outdir / "fig2_error_bars.png"
    fig.savefig(p, dpi=130); plt.close(fig); made.append(str(p))

    # fig3: Rt power law + coefficient-field closure
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.6, 4.2))
    sweep = sorted([c for c in ok if c["case_id"] in
                    ("boxbarge_5kn", "boxbarge_10kn", "boxbarge_20kn")],
                   key=lambda c: c["request"]["speed"])
    vs = [_speed_ms(c["request"]) for c in sweep]
    rt = [c["response"]["rt"] for c in sweep]
    detail = report["checks"][-1]["detail"]
    slope = detail["loglog_slope"]
    a1.plot(vs, rt, "o-", ms=7, label="platform Rt (box barge 100x20x8, Cb=1)")
    a1.plot(vs, [rt[0] * (v / vs[0]) ** slope for v in vs], "--", lw=1.0,
            label=f"fit Rt ~ V^{slope:.2f}")
    a1.set_xscale("log"); a1.set_yscale("log")
    a1.set_xlabel("V [m/s]  (declared unit converted with 1852/3600)")
    a1.set_ylabel("Rt [kN]")
    a1.set_title(f"Fig.3a  Rt power law, slope {slope:.2f}\n"
                 f"reading speed as SI m/s would underestimate Rt by "
                 f"{abs(detail['consequence_if_knots_read_as_ms_pct']):.0f}%", fontsize=9)
    a1.grid(True, which="both", alpha=0.3); a1.legend(fontsize=8)

    cases = [c["case_id"] for c in ok]
    gap = [(c["response"]["ct"] - (c["response"].get("cf_total", c["response"]["cf"])
                                   + c["response"]["cr"])) / c["response"]["ct"] * 100
           for c in ok]
    a2.barh(range(len(cases)), gap, color="#2e8b57")
    a2.set_yticks(range(len(cases)))
    a2.set_yticklabels([c[:22] for c in cases], fontsize=7)
    a2.axvline(-3.0, ls="--", lw=0.9, color="gray")
    a2.axvline(3.0, ls="--", lw=0.9, color="gray")
    a2.text(-3.0, -0.9, "-3%", fontsize=7, color="gray")
    a2.text(2.0, -0.9, "+3%", fontsize=7, color="gray")
    a2.set_xlim(-5, 5)
    a2.set_xlabel("(ct - (cf_total+cr)) / ct  [%]")
    a2.set_title("Fig.3b  C8/D2: returned coefficient field closes\n"
                 "(cf_total = cf + dcf, dcf = 0.400e-3)", fontsize=9)
    a2.grid(True, axis="x", alpha=0.3)
    fig.tight_layout(); p = outdir / "fig3_rt_powerlaw_and_coef_gap.png"
    fig.savefig(p, dpi=130); plt.close(fig); made.append(str(p))

    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[2]
    ap.add_argument("--captures", default=str(root / "artifacts/verifier/resistance_contract/captures.json"))
    ap.add_argument("--out", default=str(root / "artifacts/verifier/resistance_contract/gate_report.json"))
    ap.add_argument("--figs", default=str(root / "artifacts/verifier/resistance_contract"))
    args = ap.parse_args()

    captures = json.loads(Path(args.captures).read_text())
    report = analyse(captures)
    figs = make_figures(report, captures, Path(args.figs))
    report["figures"] = [Path(f).name for f in figs]
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))

    s = report["summary"]
    print(f"MATH     : {s['math_checks'] - s['math_failed']}/{s['math_checks']} PASS, "
          f"worst |err| = {s['math_worst_abs_err_pct']:.4f}%  (<3%: {s['under_3pct']})")
    print(f"CONTRACT : {s['contract_checks'] - s['contract_failed']}/{s['contract_checks']} PASS "
          f"(C8 worst = {s['c8_worst_abs_err_pct']:.4f}%)")
    for d in report["defects"]:
        print(f"  {d['id']} [{d['severity']}] {d['status']:<6} {d['title']}")
    print("figures  :", ", ".join(report["figures"]))
    return 0 if (s["verdict_math"] == "PASS" and s["verdict_contract"] == "PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
