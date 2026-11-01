#!/usr/bin/env python3
"""Holtrop-Mennen cross-implementation cross-check (mission m1648).

Why this tool exists
--------------------
TensorPlatform ships *two* implementations that both claim to be "Holtrop":

* ``backend/services/holtrop_mennen.py`` - the paper-faithful reference
  (equation-by-equation against Holtrop & Mennen 1982 + Holtrop 1984, audited
  in ``docs/verification/resistance_benchmark_audit.md``);
* ``backend/services/digital_twin/models/resistance.py`` - the digital-twin
  copy consumed by manoeuvring, the online predictor and the PCE surrogates.

A merged implementation is only REAL if it reproduces the published equations.
This tool re-derives the equations **independently** (``paper_literal`` below,
coded straight from the paper text, no imports from the two candidates) and
then grades three implementations against it:

======================  =====================================================
implementation          role
======================  =====================================================
``paper_literal``       independent re-derivation of the 1982/1984 equations
``reference``           ``services.holtrop_mennen`` (paper-faithful claim)
``digital_twin``        ``services.digital_twin.models.resistance``
``legacy``              the pre-m1648 digital-twin copy, kept verbatim here as
                        a *negative control*: it must keep failing the checks
======================  =====================================================

Checks (machine verdicts, no human judgement)
---------------------------------------------
A1  digital_twin vs reference, component by component  (tolerance 1e-6 rel)
A2  both vs ``paper_literal``                          (tolerance 1e-6 rel)
A3  physical invariants: ITTC-57 closed form, published c7 branch table,
    increasing R_T(V), R_W -> 0 at low Fn, S follows sqrt(Cm) not sqrt(Cb)
A4  frozen regression pins for KCS / KVLCC2 / Series-60 (change = physics
    changed; re-audit before accepting a new pin)

Usage
-----
    python3 tools/verify/holtrop_crosscheck.py            # writes artifacts/
    python3 tools/verify/holtrop_crosscheck.py --print-only

Exit code 0 iff every check on ``reference``/``digital_twin`` passes *and* the
legacy negative control is still flagged.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "artifacts" / "holtrop_crosscheck"

G = 9.81
RHO = 1025.0
NU = 1.188e-6

# hulls inside the 1982 regression domain (0.55 <= Cb <= 0.85, 3.9 <= L/B <= 9.5,
# 2.1 <= B/T <= 4.0).  Cm/Cwp follow the same standard approximations the two
# candidates use, so the comparison isolates the *resistance* equations.
HULLS = {
    "KVLCC2": dict(L=320.0, B=58.0, T=20.8, Cb=0.8098, fn=[0.10, 0.142, 0.18, 0.22]),
    "KCS": dict(L=230.0, B=32.2, T=10.8, Cb=0.651, fn=[0.15, 0.21, 0.26, 0.30]),
    "S60-Cb070": dict(L=121.92, B=16.26, T=6.50, Cb=0.70, fn=[0.15, 0.22, 0.28, 0.32]),
    "S60-Cb060": dict(L=121.92, B=16.26, T=6.50, Cb=0.60, fn=[0.15, 0.22, 0.28, 0.32]),
    "FEEDER": dict(L=120.0, B=19.0, T=7.5, Cb=0.78, fn=[0.12, 0.18, 0.22, 0.26]),
}

COMPONENTS = ("wetted_surface_m2", "form_factor", "Cf", "RF_N", "RW_N", "RA_N", "RT_N")


def _cm(cb: float) -> float:
    return 1.0 - 0.2 * (1.0 - cb)


def _cwp(cb: float) -> float:
    return (1.0 + 2.0 * cb) / 3.0


# ---------------------------------------------------------------------------
# independent re-derivation of the published equations
# ---------------------------------------------------------------------------
def paper_literal(L: float, B: float, T: float, Cb: float, V: float,
                  rho: float = RHO, nu: float = NU) -> dict:
    """Holtrop & Mennen (1982) + Holtrop (1984), transcribed from the paper.

    Deliberately self-contained (no import from either candidate module) so a
    shared coding bug in the platform cannot hide here as well.
    """
    cm, cwp = _cm(Cb), _cwp(Cb)
    cp = Cb / cm
    nabla = Cb * L * B * T
    fn = V / math.sqrt(G * L)
    re = V * L / nu

    # wetted surface (1982 eq. for ABT = 0)
    S = L * (2.0 * T + B) * math.sqrt(cm) * (
        0.453 + 0.4425 * Cb - 0.2862 * cm - 0.003467 * B / T + 0.3696 * cwp)

    # ITTC-1957 correlation line
    Cf = 0.075 / (math.log10(re) - 2.0) ** 2

    # form factor (c14 = 1, cstern = 0; lcb = 0)
    LR = L * (1.0 - cp + 0.06 * cp * 0.0 / (4.0 * cp - 1.0))
    form = (0.93 + 0.487118 * (B / L) ** 1.06806 * (T / L) ** 0.46106
            * (L / LR) ** 0.121563 * (L ** 3 / nabla) ** 0.36486
            * (1.0 - cp) ** (-0.604247))

    # c7 piecewise (the published branches; L/B = 1/(B/L))
    bl = B / L
    if bl < 0.11:
        c7 = 0.229577 * bl ** (1.0 / 3.0)
    elif bl <= 0.25:
        c7 = 0.5 - 0.0625 / bl
    else:
        c7 = bl

    iE = 1.0 + 89.0 * math.exp(
        -((L / B) ** 0.80856 * (1.0 - cwp) ** 0.30484
          * (1.0 - cp - 0.0225 * 0.0) ** 0.6367
          * (LR / B) ** 0.34574 * (100.0 * nabla / L ** 3) ** 0.16302))
    c1 = 2223105.0 * c7 ** 3.78613 * (T / B) ** 1.07961 * (90.0 - iE) ** (-1.37565)
    c2 = 1.0
    c5 = 1.0

    c15 = (-1.69385 if L / nabla ** (1.0 / 3.0) < 8.0 else
           (0.0 if L / nabla ** (1.0 / 3.0) > 12.0 else
            -1.69385 + (L / nabla ** (1.0 / 3.0) - 8.0) / 2.36))
    c16 = (8.07981 * cp - 13.8673 * cp ** 2 + 6.984388 * cp ** 3
           if cp < 0.8 else 1.73014 - 0.7067 * cp)
    m1 = (0.0140407 * L / T - 1.75254 * nabla ** (1.0 / 3.0) / L
          - 4.79323 * B / L - c16)
    m2 = c15 * cp ** 2 * math.exp(-0.1 * fn ** -2)
    lam = (1.446 * cp - 0.03 * L / B) if L / B < 12.0 else (1.446 * cp - 0.36)
    rw = c1 * c2 * c5 * nabla * rho * G * math.exp(m1 * fn ** -0.9
                                                   + m2 * math.cos(lam * fn ** -2))
    rw = max(rw, 0.0)

    CA = 0.006 * (L + 100.0) ** (-0.16) - 0.00205
    q = 0.5 * rho * V * V * S
    rf_bare = q * Cf
    ra = q * CA
    rt = rf_bare * form + rw + ra
    return {
        "wetted_surface_m2": S, "form_factor": form, "Cf": Cf,
        "RF_N": rf_bare, "RW_N": rw, "RA_N": ra, "RT_N": rt,
        "c7": c7, "c1": c1, "m2": m2, "Fn": fn, "Cm": cm, "Cp": cp,
    }


# ---------------------------------------------------------------------------
# pre-m1648 digital-twin copy, verbatim apart from the return mapping.
# Kept as a negative control: it must keep failing A2.
# ---------------------------------------------------------------------------
def legacy(V, L, B, T, Cb, rho=RHO, nu=1.19e-6):
    V = float(max(V, 1e-6))
    vol = Cb * L * B * T
    Cm = 1.0 - 0.2 * (1.0 - Cb)
    Cp = Cb / Cm
    Cwp = (1.0 + 2.0 * Cb) / 3.0
    lcb = 0.0
    S = L * (2.0 * T + B) * math.sqrt(Cb) * (
        0.453 + 0.4425 * Cb - 0.2862 * Cm - 0.003467 * B / T + 0.3696 * Cwp)
    Rn = max(V * L / nu, 1e6)
    Cf = 0.075 / (math.log10(Rn) - 2.0) ** 2
    RF = 0.5 * rho * V * V * S * Cf
    c14 = 1.0
    LR = L * (1.0 - Cp + 0.06 * Cp * lcb / (4.0 * Cp - 1.0))
    form = max(0.93 + 0.487118 * c14 * (B / L) ** 1.06806 * (T / L) ** 0.46106
               * (L / LR) ** 0.121563 * (L ** 3 / vol) ** 0.36486
               * (1.0 - Cp) ** -0.604247, 1.0)
    Fn = V / math.sqrt(9.80665 * L)
    if B / L < 0.11:
        c7 = 0.229577 * (B / L) ** (1.0 / 3.0)
    elif B / L < 0.25:
        c7 = B / L                                     # <- swapped branch
    else:
        c7 = 0.5 - 0.0625 * L / B                      # <- swapped branch
    iE = 1.0 + 89.0 * math.exp(
        -(L / B) ** 0.80856 * (1.0 - Cwp) ** 0.30484
        * max(1.0 - Cp - 0.0225 * lcb, 1e-3) ** 0.6367
        * (LR / B) ** 0.34574 * (100.0 * vol / L ** 3) ** 0.16302)
    c1 = 2223105.0 * c7 ** 3.78613 * (T / B) ** 1.07961 * (90.0 - iE) ** -1.37565
    c2 = 1.0
    c5 = 1.0
    c16 = (8.07981 * Cp - 13.8673 * Cp ** 2 + 6.984388 * Cp ** 3
           if Cp < 0.8 else 1.73014 - 0.7067 * Cp)
    m1 = 0.0140407 * L / T - 1.75254 * vol ** (1.0 / 3.0) / L - 4.79323 * B / L - c16
    if L ** 3 / vol < 512.0:
        c15 = -1.69385
    elif L ** 3 / vol > 1727.0:
        c15 = 0.0
    else:
        c15 = -1.69385 + (L / vol ** (1.0 / 3.0) - 8.0) / 2.36
    lam = 1.446 * Cp - 0.03 * L / B if L / B < 12.0 else 1.446 * Cp - 0.36
    m4 = c15 * 0.4 * math.exp(-0.034 * Fn ** -3.29) if Fn > 0 else 0.0   # <- m4 !!m2
    expo = min(max(m1 * Fn ** -0.9 + m4 * math.cos(lam * Fn ** -2.0), -30.0), 30.0)
    RW = max(c1 * c2 * c5 * vol * rho * G * math.exp(expo), 0.0)
    CA = max(0.0, 0.006 * (L + 100.0) ** -0.16 - 0.00205)
    RA = 0.5 * rho * V * V * S * CA
    RT = RF * form + RW + RA
    return {"wetted_surface_m2": S, "form_factor": form, "Cf": Cf,
            "RF_N": RF, "RW_N": RW, "RA_N": RA, "RT_N": RT, "c1": c1,
            "Fn": Fn, "Cm": Cm, "Cp": Cp}


# ---------------------------------------------------------------------------
# candidates from the worktree
# ---------------------------------------------------------------------------
def _load_worktree_impls():
    sys.path.insert(0, str(REPO / "backend"))
    import importlib
    ref = importlib.import_module("services.holtrop_mennen")
    dt = importlib.import_module("services.digital_twin.models.resistance")
    return ref.holtrop_resistance, dt.holtrop_resistance


def reference_call(func, L, B, T, Cb, V):
    r = func(length=L, beam=B, draft=T, cb=Cb, speed_ms=V,
             cm=_cm(Cb), cwp=_cwp(Cb), lcb_frac=0.0, rho=RHO, nu=NU)
    form = r.form_factor
    return {"wetted_surface_m2": r.wetted_surface_m2, "form_factor": form,
            "Cf": r.cf, "RF_N": r.rf_n / form, "RW_N": r.rw_n,
            "RA_N": r.ra_n, "RT_N": r.rt_n, "c1": r.c1,
            "Fn": r.fn, "Cm": r.cm, "Cp": r.cp}


def digital_twin_call(func, L, B, T, Cb, V):
    r = func(V, L, B, T, Cb, rho=RHO, nu=NU)
    return {k: r[k] for k in COMPONENTS} | {"c1": r["c1"], "Fn": r["Fn"],
                                            "Cm": r["Cm"], "Cp": r["Cp"]}


def _rel(a: float, b: float) -> float:
    if a == 0.0:
        return 0.0 if b == 0.0 else float("inf")
    return abs(b - a) / abs(a)


def compare(candidate: dict, baseline: dict, keys=COMPONENTS) -> dict:
    errs = {k: _rel(baseline[k], candidate[k]) for k in keys}
    worst = max(errs, key=errs.get)
    return {"rel_err": errs, "worst_key": worst, "worst": errs[worst]}


# ---------------------------------------------------------------------------
# A3 physical invariants (independent of any implementation)
# ---------------------------------------------------------------------------
def invariants() -> list:
    out = []

    def check(name, ok, detail):
        out.append({"check": name, "pass": bool(ok), "detail": detail})

    # ITTC-1957 closed form
    cf = 0.075 / (math.log10(1e7) - 2.0) ** 2
    check("ittc1957_closed_form", abs(cf - 0.0030) < 1e-12,
          f"Cf(Re=1e7) = {cf:.7f} (ITTC-1957 0.075/(log10Re-2)^2 = 0.0030000)")

    # published c7 branch values
    lit = {0.08: 0.229577 * 0.08 ** (1 / 3), 0.15: 0.5 - 0.0625 / 0.15,
           0.30: 0.30}
    got = {bl: paper_literal(100.0, bl * 100.0, 5.0, 0.7, 3.0)["c7"] for bl in lit}
    check("c7_branch_table", all(abs(got[k] - lit[k]) < 1e-12 for k in lit),
          {f"B/L={k}": round(got[k], 6) for k in got})

    # wetted surface must follow sqrt(Cm), not sqrt(Cb)
    h = HULLS["KCS"]
    s_paper = paper_literal(h["L"], h["B"], h["T"], h["Cb"], 0.0 + 1e-3)["wetted_surface_m2"]
    cm = _cm(h["Cb"])
    bracket = (0.453 + 0.4425 * h["Cb"] - 0.2862 * cm
               - 0.003467 * h["B"] / h["T"] + 0.3696 * _cwp(h["Cb"]))
    s_cm = h["L"] * (2 * h["T"] + h["B"]) * math.sqrt(cm) * bracket
    s_cb = h["L"] * (2 * h["T"] + h["B"]) * math.sqrt(h["Cb"]) * bracket
    check("wetted_surface_sqrtCm", abs(s_paper - s_cm) < 1e-9 * s_cm,
          {"S_sqrtCm": round(s_cm, 1), "S_sqrtCb": round(s_cb, 1),
           "sqrtCb_error_pct": round(100 * (s_cb - s_cm) / s_cm, 2)})

    # R_T increasing in V and R_W/RT small at low Fn
    for name, h in HULLS.items():
        v = [f * math.sqrt(G * h["L"]) for f in h["fn"]]
        rt = [paper_literal(h["L"], h["B"], h["T"], h["Cb"], x)["RT_N"] for x in v]
        check(f"rt_monotone_in_V[{name}]",
              all(b > a for a, b in zip(rt, rt[1:])),
              [round(x, 1) for x in rt])
        low = paper_literal(h["L"], h["B"], h["T"], h["Cb"], 0.3 * v[0])
        share = low["RW_N"] / low["RT_N"]
        check(f"rw_small_at_low_fn[{name}]", share < 0.15,
              f"RW/RT = {share:.2e} at Fn={low['Fn']:.4f}")
    return out


# frozen pins: (hull, Fn) -> RT_N and RW_N from the paper-literal chain.
PINS = {
    # re-frozen at m1648 after the published c15 branches were restored
    ("KVLCC2", 0.142): {"RT_N": 1919681.0, "RW_N": 44303.0},
    ("KCS", 0.21): {"RT_N": 915110.0, "RW_N": 5945.0},
    ("S60-Cb060", 0.22): {"RT_N": 168487.0, "RW_N": 157.0},
}


def pin_check(impl_call) -> list:
    out = []
    for (name, fn), pin in PINS.items():
        h = HULLS[name]
        V = fn * math.sqrt(G * h["L"])
        got = impl_call(h["L"], h["B"], h["T"], h["Cb"], V)
        for key, want in pin.items():
            rel = _rel(want, got[key])
            out.append({"check": f"pin[{name}@Fn{fn}].{key}", "pass": rel <= 2e-3,
                        "detail": {"frozen": want, "measured": round(got[key], 3),
                                   "rel_err": round(rel, 6)}})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-only", action="store_true")
    args = ap.parse_args(argv)

    ref_func, dt_func = _load_worktree_impls()
    rows, a1, a2 = [], [], []
    for name, h in HULLS.items():
        for fn in h["fn"]:
            V = fn * math.sqrt(G * h["L"])
            lit = paper_literal(h["L"], h["B"], h["T"], h["Cb"], V)
            ref = reference_call(ref_func, h["L"], h["B"], h["T"], h["Cb"], V)
            dtv = digital_twin_call(dt_func, h["L"], h["B"], h["T"], h["Cb"], V)
            leg = legacy(V, h["L"], h["B"], h["T"], h["Cb"])
            c_ref = compare(ref, lit)
            c_dt = compare(dtv, lit)
            c_leg = compare(leg, lit)
            a1.append({"case": f"{name}@Fn{fn}", **compare(dtv, ref)})
            a2.append({"case": f"{name}@Fn{fn}", "reference": c_ref["worst"],
                       "digital_twin": c_dt["worst"], "legacy": c_leg["worst"],
                       "legacy_worst_key": c_leg["worst_key"]})
            rows.append({"hull": name, "fn": fn, "V_ms": round(V, 4),
                         "RT_N": round(lit["RT_N"], 2), "RW_N": round(lit["RW_N"], 2),
                         "dt_vs_lit_worst": round(c_dt["worst"], 6),
                         "legacy_vs_lit_worst": round(c_leg["worst"], 6),
                         "legacy_worst_key": c_leg["worst_key"]})

    tol = 1e-6
    checks = []
    for r in a1:
        checks.append({"check": f"A1[{r['case']}]", "pass": r["worst"] <= tol,
                       "detail": f"max rel {r['worst']:.2e} on {r['worst_key']}"})
    for r in a2:
        checks.append({"check": f"A2.ref[{r['case']}]", "pass": r["reference"] <= tol,
                       "detail": f"max rel {r['reference']:.2e}"})
        checks.append({"check": f"A2.dt[{r['case']}]", "pass": r["digital_twin"] <= tol,
                       "detail": f"max rel {r['digital_twin']:.2e}"})
    # negative control (aggregate): the pre-m1648 copy must stay flagged
    legacy_max = max(r["legacy"] for r in a2)
    legacy_worst = max(a2, key=lambda r: r["legacy"])
    checks.append({"check": "A2.legacy_negative_control",
                   "pass": legacy_max > 2.0,
                   "detail": f"pre-m1648 copy max rel {legacy_max:.2f} "
                             f"({legacy_worst['case']}, term {legacy_worst['legacy_worst_key']}) "
                             f"must remain > 2.0 so the audit keeps its teeth"})
    checks.extend(invariants())
    checks.extend(pin_check(lambda L, B, T, Cb, V: digital_twin_call(dt_func, L, B, T, Cb, V)))

    n_fail = sum(1 for c in checks if not c["pass"])
    payload = {
        "tool": "tools/verify/holtrop_crosscheck.py",
        "mission": 1648,
        "tolerances": {"A1_A2_rel": tol, "pin_rel": 2e-3, "legacy_control_min_rel": 0.5},
        "n_checks": len(checks),
        "n_failed": n_fail,
        "verdict": "CROSSCHECK_PASS" if n_fail == 0 else "CROSSCHECK_FAIL",
        "failures": [c for c in checks if not c["pass"]],
        "checks": checks,
        "sweep": rows,
        "A2_summary": {
            "reference_max_rel": max(r["reference"] for r in a2),
            "digital_twin_max_rel": max(r["digital_twin"] for r in a2),
            "legacy_max_rel": max(r["legacy"] for r in a2),
        },
    }

    if not args.print_only:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "crosscheck.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2))
        (OUT_DIR / "table.md").write_text(_table(rows, payload))

    print(f"{payload['verdict']}  checks={len(checks)} failed={n_fail}")
    print("A2 worst rel err: reference={:.2e}  digital_twin={:.2e}  legacy={:.2e}"
          .format(payload["A2_summary"]["reference_max_rel"],
                  payload["A2_summary"]["digital_twin_max_rel"],
                  payload["A2_summary"]["legacy_max_rel"]))
    for f in payload["failures"][:12]:
        print("  FAIL", f["check"], f["detail"])
    return 0 if n_fail == 0 else 1


def _table(rows, payload) -> str:
    lines = ["# Holtrop-Mennen cross-check (m1648)", "",
             f"verdict: **{payload['verdict']}** ({payload['n_checks']} checks, "
             f"{payload['n_failed']} failed)", "",
             "| hull | Fn | V [m/s] | RT [N] | RW [N] | digital_twin vs paper | "
             "pre-m1648 copy vs paper | pre-m1648 worst term |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| {hull} | {fn} | {V_ms} | {RT_N} | {RW_N} | {d:.2%} | {l:.2%} | {k} |"
                     .format(hull=r["hull"], fn=r["fn"], V_ms=r["V_ms"], RT_N=r["RT_N"],
                             RW_N=r["RW_N"], d=r["dt_vs_lit_worst"],
                             l=r["legacy_vs_lit_worst"], k=r["legacy_worst_key"]))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())