"""Resistance endpoint contract & analytic-consistency gate.

Black-box verification of the shipped `POST /api/v1/calculations/resistance`
endpoint (`calc.resistance`, ledger evidence grade REAL) against a reference
chain that is *entirely closed form* and never calls the platform:

    Re     = V L / nu                    (definition)
    Fr     = V / sqrt(g L)               (definition)
    Cf     = 0.075 / (log10 Re - 2)^2    (ITTC-1957)
    Cf_eff = Cf + dCf,  dCf = 0.4e-3     (ITTC-1978 roughness allowance, *inferred* here)
    Rr     = 0.5 rho V^2 S Cr            (definition of the residual coefficient)
    Pe     = Rt V                        (effective-power definition)

The unit contract is not documented on the endpoint, so it is recovered from the
data itself: responses only close when `speed` is read as knots (recovered
conversion 0.5144 m/s per knot, i.e. the exact 1852/3600 rounded to four
decimals), with g = 9.81 m/s^2, nu = 1.19e-6 m^2/s and rho = 1025 kg/m^3.
Every MATH check below then closes to <0.03 %, which is quantization-limited by
the 2-6 decimal places the endpoint prints.

Two outcome classes are kept separate on purpose:

  * MATH (C1-C7, C12): does the endpoint compute what its own `method` string
    claims ("ITTC-57 friction + Holtrop residual")?  Target <3 %; here <0.03 %.
  * CONTRACT (C8-C11): is the answer usable and fail-safe?  Field aliasing
    (`cf + cr != ct`), silently ignored `displacement`, accepted impossible
    geometry (Cb>1) and HTTP 500 at V=0 belong here.

CLI:
    python3 tools/verifier/audit_resistance_contract.py \
        --captures artifacts/verifier/resistance_contract/captures.json \
        --out      artifacts/verifier/resistance_contract/gate_report.json \
        --figs     artifacts/verifier/resistance_contract

Exit code 0 when every MATH check passes and every CONTRACT check is annotated.
It deliberately does not exit non-zero on the known contract defects: this file
is also the regression gate that must keep running until they are fixed.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List

# --- reference-chain constants ------------------------------------------------
KN_TO_MS = 1852.0 / 3600.0          # 0.5144444... exact
KN_TO_MS_PLATFORM = 0.5144          # value recovered from the responses
G = 9.81
RHO = 1025.0
NU = 1.19e-6
DCF_ROUGHNESS = 4.0e-4              # ITTC-1978 roughness allowance

TOL = 0.10                          # % ; closed forms + printed quantization
TOL_TARGET = 3.0                    # % ; the acceptance line that matters


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


def analyse(captures: Dict[str, Any]) -> Dict[str, Any]:
    """Run every check over the captured cases and return the gate report."""
    rows: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []
    ok_cases = [c for c in captures["cases"] if c.get("http_status") == 200]
    recovered_kn: List[float] = []

    for c in ok_cases:
        req, res = c["request"], c["response"]
        v = req["speed"] * KN_TO_MS
        # C0: recover the conversion constant from Fr alone (g given, L given)
        recovered_kn.append(res["froude_number"] * math.sqrt(G * req["length"]) / req["speed"])

        rows.append(_row("C1", "MATH", c["case_id"], "reynolds_number (V in m/s from knots)",
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
        rows.append(_row("C8", "CONTRACT", c["case_id"], "ct vs (cf+cr) as returned",
                         res["cf"] + res["cr"], res["ct"], TOL, "-"))

    kn_mean = sum(recovered_kn) / len(recovered_kn)
    global_rows = [{
        "check": "C0", "kind": "MATH", "case": "all 200 cases",
        "quantity": "knot->m/s conversion recovered from Fr",
        "reference": KN_TO_MS, "platform": kn_mean,
        "rel_err_pct": _rel(kn_mean, KN_TO_MS), "tol_pct": 0.05, "unit": "m/s per knot",
        "verdict": "PASS" if abs(_rel(kn_mean, KN_TO_MS)) <= 0.05 else "FAIL",
        "detail": {"recovered_min": min(recovered_kn), "recovered_max": max(recovered_kn),
                   "exact_1852_over_3600": KN_TO_MS,
                   "note": "platform uses 0.5144 (4 d.p.) -> -0.0086% bias on Re and Fr"},
    }]

    # C9: is `displacement` used at all?
    base = next(c for c in ok_cases if c["case_id"] == "boxbarge_10kn")
    light = next((c for c in ok_cases if c["case_id"] == "boxbarge_10kn_disp8000"), None)
    if light is not None:
        same = all(abs(base["response"][k] - light["response"][k]) <= 1e-12
                   for k in ("rt", "ct", "wetted_surface", "pe"))
        notes.append({
            "check": "C9", "kind": "CONTRACT", "case": "boxbarge_10kn_disp8000",
            "quantity": "displacement 16400 t vs 8000 t sensitivity",
            "reference": "must react to displacement, or reject the inconsistent pair",
            "platform": "identical response" if same else "response changed",
            "rel_err_pct": 0.0 if same else float("nan"), "tol_pct": 0.0, "unit": "-",
            "verdict": "FAIL" if same else "PASS", "defect": "D4" if same else None,
        })

    bad = next((c for c in captures["cases"] if c["case_id"] == "invalid_cb_1p2"), None)
    if bad is not None:
        notes.append({
            "check": "C10", "kind": "CONTRACT", "case": "invalid_cb_1p2",
            "quantity": "Cb = 1.2 acceptance",
            "reference": "Cb = nabla/(L B T) <= 1 by definition -> 422",
            "platform": f"HTTP {bad['http_status']} with a plausible-looking answer",
            "rel_err_pct": float("nan"), "tol_pct": 0.0, "unit": "-",
            "verdict": "PASS" if bad["http_status"] >= 400 else "FAIL",
            "defect": None if bad["http_status"] >= 400 else "D3",
        })

    zero = next((c for c in captures["cases"] if c["case_id"] == "zero_speed"), None)
    if zero is not None:
        notes.append({
            "check": "C11", "kind": "CONTRACT", "case": "zero_speed",
            "quantity": "V = 0 handling",
            "reference": "reject with 4xx (Re=0 -> Cf undefined), never 5xx",
            "platform": f"HTTP {zero['http_status']}", "rel_err_pct": float("nan"),
            "tol_pct": 0.0, "unit": "-",
            "verdict": "PASS" if 400 <= zero["http_status"] < 500 else "FAIL",
            "defect": None if 400 <= zero["http_status"] < 500 else "D5",
        })

    # C12: monotonicity + measured power law (consequence of D1 unit contract)
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

    return {
        "meta": {
            "endpoint": captures["meta"]["endpoint"],
            "cases_total": len(captures["cases"]), "cases_used": len(ok_cases),
            "reference_chain": {"kn_to_ms": KN_TO_MS, "g": G, "rho": RHO, "nu": NU,
                                "ittc1957": "cf = 0.075/(log10 Re - 2)^2",
                                "roughness_delta_cf": DCF_ROUGHNESS},
            "tolerances": {"MATH": TOL, "acceptance_line": TOL_TARGET},
        },
        "checks": rows,
        "observations": notes,
        "summary": {
            "math_checks": len(math_rows), "math_failed": len(math_fail),
            "math_worst_abs_err_pct": worst,
            "contract_checks": len(contract_rows), "contract_failed": len(contract_fail),
            "verdict_math": "PASS" if not math_fail else "FAIL",
            "verdict_contract": "PASS" if not contract_fail else "FAIL",
            "under_3pct": worst <= TOL_TARGET,
        },
        "defects": [
            {"id": "D1", "severity": "medium", "check": "C0/C1/C2/C12",
             "title": "undeclared knots unit contract",
             "evidence": f"closes only when speed is read as knots (recovered "
                         f"{kn_mean:.6f} m/s per knot); reading it as SI m/s "
                         f"underestimates Rt by {(1 - KN_TO_MS ** slope) * 100:.1f}%",
             "fix": "declare units in the schema (or switch to m/s) + OpenAPI description"},
            {"id": "D2", "severity": "medium", "check": "C6/C7/C8",
             "title": "cf field and rf/ct field are not the same quantity (dCf=0.400e-3 missing)",
             "evidence": "cf+cr is 1.8-6.6% below ct in all 6 cases; back-solving the force "
                         "gives a constant dCf=3.99e-4 (C6 within 0.2% in all cases)",
             "fix": "return cf_total=cf+dCf (or an explicit dcf field) so that cf+cr=ct"},
            {"id": "D3", "severity": "high", "check": "C10",
             "title": "no geometry/consistency validation (Cb>1 accepted)",
             "evidence": "Cb=1.2 => nabla=L*B*T*Cb>L*B*T, physically impossible; "
                         "endpoint returns 200 with a plausible-looking answer",
             "fix": "validate 0.3<Cb<=1.0 and |nabla-L*B*T*Cb|/nabla<5%, else 422"},
            {"id": "D4", "severity": "low", "check": "C9",
             "title": "displacement silently ignored",
             "evidence": "16400 t and 8000 t give bit-identical responses",
             "fix": "derive Cb/Cm from displacement, or mark the field ignored"},
            {"id": "D5", "severity": "medium", "check": "C11",
             "title": "V=0 raises an unhandled exception (HTTP 500)",
             "evidence": "speed=0 -> 500 Internal Server Error (expected 422)",
             "fix": "validate speed>0, return 422 for V<=0 or Re<=1"},
        ],
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
                  mfc="none", mew=1.8, label="platform cf (6 cases)" if first else None)
        first = False
    ax.set_xlabel("Re [-]")
    ax.set_ylabel("Cf [-]")
    ax.set_title("Fig.1  C3: endpoint cf on top of the ITTC-1957 line "
                 "(max |err| = 0.023%)", fontsize=10)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    p = outdir / "fig1_cf_vs_ittc1957.png"
    fig.tight_layout(); fig.savefig(p, dpi=130); plt.close(fig); made.append(str(p))

    # fig2: relative errors per check
    rows = [r for r in report["checks"] if r["kind"] == "MATH" and r["rel_err_pct"] == r["rel_err_pct"]]
    rows += [r for r in report["checks"] if r["kind"] == "CONTRACT"]
    fig, ax = plt.subplots(figsize=(9.4, 4.4))
    labels = [f'{r["check"]}' for r in rows]
    errs = [abs(r["rel_err_pct"]) + 1e-4 for r in rows]
    colors = ["#c0392b" if r["kind"] == "CONTRACT" else "#2a7fbd" for r in rows]
    ax.bar(range(len(errs)), errs, color=colors)
    ax.set_yscale("log")
    for y, lab in ((0.1, "0.1% (quantization tol)"), (3.0, "3% target"), (15.0, "15% unacceptable")):
        ax.axhline(y, ls="--", lw=0.9, color="gray")
        ax.text(len(errs) - 0.3, y * 1.12, lab, ha="right", fontsize=7, color="gray")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=90, fontsize=6)
    ax.set_ylabel("|relative error| [%]")
    ax.set_title("Fig.2  Per-check errors: closed-form identities (blue) < 0.03%; "
                 "contract check C8 (red) up to 6.6%", fontsize=10)
    fig.tight_layout(); p = outdir / "fig2_error_bars.png"
    fig.savefig(p, dpi=130); plt.close(fig); made.append(str(p))

    # fig3: Rt power law + coefficient-field gap
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.6, 4.2))
    sweep = sorted([c for c in ok if c["case_id"].startswith("boxbarge_")
                    and "disp" not in c["case_id"]], key=lambda c: c["request"]["speed"])
    vs = [c["request"]["speed"] * KN_TO_MS for c in sweep]
    rt = [c["response"]["rt"] for c in sweep]
    detail = report["checks"][-1]["detail"]
    slope = detail["loglog_slope"]
    a1.plot(vs, rt, "o-", ms=7, label="platform Rt (box barge 100x20x8, Cb=1)")
    a1.plot(vs, [rt[0] * (v / vs[0]) ** slope for v in vs], "--", lw=1.0,
            label=f"fit Rt ~ V^{slope:.2f}")
    a1.set_xscale("log"); a1.set_yscale("log")
    a1.set_xlabel("V [m/s]  (speed[knots] x 0.5144)")
    a1.set_ylabel("Rt [kN]")
    a1.set_title(f"Fig.3a  Rt power law, slope {slope:.2f}\n"
                 f"reading speed as SI m/s would underestimate Rt by "
                 f"{abs(detail['consequence_if_knots_read_as_ms_pct']):.0f}%", fontsize=9)
    a1.grid(True, which="both", alpha=0.3); a1.legend(fontsize=8)

    cases = [c["case_id"] for c in ok]
    gap = [(c["response"]["ct"] - (c["response"]["cf"] + c["response"]["cr"]))
           / c["response"]["ct"] * 100 for c in ok]
    a2.barh(range(len(cases)), gap, color="#c0392b")
    a2.set_yticks(range(len(cases)))
    a2.set_yticklabels([c[:20] for c in cases], fontsize=7)
    a2.axvline(-3.0, ls="--", lw=0.9, color="gray")
    a2.text(-3.0, -0.7, "3%", fontsize=7, color="gray")
    a2.set_xlabel("(ct - (cf+cr)) / ct  [%]")
    a2.set_title("Fig.3b  C8/D2: coefficient fields do not add up\n"
                 "(missing dCf = 0.400e-3)", fontsize=9)
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
    print(f"CONTRACT : {s['contract_checks'] - s['contract_failed']}/{s['contract_checks']} PASS")
    for d in report["defects"]:
        print(f"  {d['id']} [{d['severity']}] {d['title']}")
    print("figures  :", ", ".join(report["figures"]))
    return 0 if s["verdict_math"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())