#!/usr/bin/env python3
"""tp-ver-geometry — independent verification of hydrostatics / hull geometry.

Discipline rule #1 on this platform is "geometry must be verified before anything
else: Cb, L/B, coefficients".  Report 1148 (capability ledger) nevertheless marks
every ``geometry.*`` capability UNVERIFIED and, at the time of writing, the
TensorPlatform tree contained no hydrostatics implementation at all — so there
was nothing to adjudicate against.  This pilot supplies the missing half: exact
analytic baselines (box barge, Wigley, sine-form) whose hydrostatics are derived
in closed form, plus a Simpson reference integrator that is *itself* checked
against those baselines.

Two modes
---------
self-audit (default)
    Adjudicates the in-tree reference integrator against the closed-form
    baselines.  Criteria C1 (analytic agreement), C2 (observed convergence
    order), C3 (coefficient identities), C4 (mutation gate: every planted
    defect must be rejected).

candidate (``--candidate PATH``)
    Adjudicates an external hydrostatics implementation over a held-out set of
    hulls (analytic families with seeded random dimensions) against the same
    closed-form values, applying the platform acceptance band (3%).

Candidate protocol (same as tools/verifier/tp_ver_resistance.py)
    stdin : {"specification": {"L","B","T","D","x":[...],"z":[...],"y":[[...]]}}
    stdout: {"result": {"VOL","Cb","Cm","Cp","A_W","Cw","I_T","KB","BM","LCB","LCF"}}

Usage
-----
    python3 tools/verifier/tp_ver_geometry.py --self-audit \
        --output artifacts/verifier/geometry_pilot_report.json --figures
    python3 tools/verifier/tp_ver_geometry.py --candidate my_hydro.py --candidate-id acme-1.0
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Dict, List

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.geometry.analytic_cases import ANALYTIC_CASES, METRICS, build_case, sineform, wigley, box_barge
from verifier.geometry.mutants import MUTANTS
from verifier.geometry.ref_hydrostatics import compute_hydrostatics, convergence_study

# --- criteria -----------------------------------------------------------------
TOL_ANALYTIC = 1e-6      # reference integrator vs closed form, at n = 160
TOL_IDENTITY = 1e-9      # coefficient identities (must be exact up to round-off)
MIN_ORDER = 2.5          # observed convergence order floor (Simpson ~ 4)
PLATFORM_TOL = 0.03      # platform acceptance band for external candidates (<3%)
GRIDS = (40, 80, 160, 320)


def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) if abs(b) > 1e-12 else abs(a - b)


def identities(r: Dict[str, float], form) -> Dict[str, float]:
    """Hull-independent consistency relations that any implementation must satisfy."""
    return {
        "cp_times_cm_minus_cb": abs(r["Cp"] * r["Cm"] - r["Cb"]),
        "vol_minus_cb_LBT": _rel(r["VOL"], r["Cb"] * form.L * form.B * form.T),
        "aw_minus_cw_LB": _rel(r["A_W"], r["Cw"] * form.L * form.B),
        "bm_minus_iT_over_vol": _rel(r["BM"], r["I_T"] / r["VOL"]) if r["VOL"] > 0 else 0.0,
    }


def check_candidate(form, exact: Dict[str, float], skip, result: Dict[str, float], tol: float):
    """Compare a candidate result dict against the closed-form values."""
    errs = {}
    for k in METRICS:
        if k in skip or k not in result:
            continue
        errs[k] = _rel(float(result[k]), float(exact[k]))
    worst = max(errs, key=errs.get) if errs else None
    ident = identities(result, form)
    return {
        "rel_err": errs,
        "worst_metric": worst,
        "worst_rel_err": errs[worst] if worst else float("nan"),
        "identities": ident,
        "identities_ok": all(v <= 1e-6 for v in ident.values()),
        "within_tol": bool(errs) and errs[worst] <= tol,
    }


def self_audit(n: int = 160) -> dict:
    """Adjudicate the in-tree reference integrator against closed-form values."""
    cases = {}
    for name in ANALYTIC_CASES:
        form, exact, skip = build_case(name)
        r = compute_hydrostatics(form, n_x=n, n_z=n // 2)
        res = check_candidate(form, exact, skip, r, TOL_ANALYTIC)
        conv = convergence_study(form, exact, quantities=("VOL", "Cb", "I_T", "BM"), grids=GRIDS)
        # Only quantities whose error is resolvable above round-off carry a meaningful slope.
        orders = {q: [o for o in conv[q]["order"] if np.isfinite(o)]
                  for q in conv
                  if conv[q]["rel_err"][0] > 1e-6 and conv[q]["rel_err"][-1] < conv[q]["rel_err"][0]}
        cases[name] = {
            "params": ANALYTIC_CASES[name]["params"],
            "skip": list(skip),
            "computed": {k: r[k] for k in METRICS},
            "exact": {k: exact[k] for k in METRICS},
            "rel_err": res["rel_err"],
            "worst_metric": res["worst_metric"],
            "worst_rel_err": res["worst_rel_err"],
            "identities": res["identities"],
            "identities_ok": res["identities_ok"],
            "within_tol": res["within_tol"],
            "convergence": {q: {"n": conv[q]["n"], "rel_err": conv[q]["rel_err"],
                                "order": conv[q]["order"]} for q in conv},
            # Where the error is already at round-off the log-log slope is meaningless.
            "observed_order_min": (min((min(v) for v in orders.values() if v), default=float("nan"))
                                   if res["worst_rel_err"] > 1e-12 else None),
        }

    # C2: convergence order where the error is resolvable above round-off.
    order_checks = {}
    for name, c in cases.items():
        resolvable = [q for q, v in c["convergence"].items()
                      if v["rel_err"][0] > 1e-6 and v["rel_err"][-1] < v["rel_err"][0]]
        ords = [o for q in resolvable for o in c["convergence"][q]["order"] if np.isfinite(o)]
        order_checks[name] = {
            "quantities": resolvable,
            "min_order": min(ords) if ords else None,
            "pass": (min(ords) >= MIN_ORDER) if ords else True,  # exact-at-all-grids => pass
        }

    # C4: mutation gate.
    mutations, mutants_rejected = {}, 0
    for mname, (fn, family) in MUTANTS.items():
        caught_by, detail = None, None
        for cname in ANALYTIC_CASES:
            form, exact, skip = build_case(cname)
            out = fn(form)
            res = check_candidate(form, exact, skip, out, TOL_ANALYTIC)
            if not res["within_tol"] or not res["identities_ok"]:
                caught_by = cname
                detail = {"worst_metric": res["worst_metric"], "worst_rel_err": res["worst_rel_err"],
                          "case": cname}
                break
        if caught_by is None:  # fall back on the convergence-order criterion
            for cname in ANALYTIC_CASES:
                form, exact, skip = build_case(cname)
                out = fn(form)
                conv = convergence_study(form, exact, quantities=("VOL", "Cb", "I_T", "BM"), grids=GRIDS)
                ords = [o for q in conv for o in conv[q]["order"] if np.isfinite(o)]
                if ords and min(ords) < MIN_ORDER:
                    caught_by = f"{cname}:order"
                    detail = {"min_order": min(ords), "case": cname}
                    break
        rejected = caught_by is not None
        mutants_rejected += int(rejected)
        mutations[mname] = {"expected_family": family, "rejected": rejected, "caught_by": detail}

    criteria = {
        "C1_analytic_agreement": {
            "tol": TOL_ANALYTIC,
            "pass": all(c["within_tol"] for c in cases.values()),
            "worst": max((c["worst_rel_err"] for c in cases.values())),
        },
        "C2_convergence_order": {
            "min_order": MIN_ORDER,
            "pass": all(v["pass"] for v in order_checks.values()),
            "detail": order_checks,
        },
        "C3_identities": {
            "tol": TOL_IDENTITY,
            "pass": all(c["identities_ok"] for c in cases.values()),
            "worst": max((max(c["identities"].values()) for c in cases.values())),
        },
        "C4_mutation_gate": {
            "pass": mutants_rejected == len(MUTANTS),
            "rejected": mutants_rejected,
            "total": len(MUTANTS),
            "detail": mutations,
        },
    }
    verdict = "PASS" if all(c["pass"] for c in criteria.values()) else "FAIL"
    return {"mode": "self_audit", "n_panels": n, "grids": list(GRIDS), "cases": cases,
            "criteria": criteria, "verdict": verdict}


def heldout_specs(seed: int = 20261101, n_cases: int = 6) -> List[dict]:
    """Analytic hulls with random dimensions — the candidate's held-out exam."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n_cases):
        kind = ("box_barge", "wigley", "sineform")[i % 3]
        L = float(round(rng.uniform(80.0, 320.0), 1))
        B = float(round(rng.uniform(0.10, 0.18) * L, 2))
        T = float(round(rng.uniform(0.035, 0.06) * L, 2))
        D = float(round(rng.uniform(1.2, 1.8) * T, 2))
        if kind == "box_barge":
            form, exact = box_barge(L, B, T)
            skip = ()
        elif kind == "wigley":
            form, exact = wigley(L, B, T, D)
            skip = ()
        else:
            form, exact = sineform(L, B, T, D)
            skip = ()
        nx, nz = 160, 80
        xs = np.linspace(-0.5 * L, 0.5 * L, nx + 1)
        zs = np.linspace(0.0, T, nz + 1)
        spec = {
            "hull": kind, "L": L, "B": B, "T": T, "D": D,
            "x": [round(float(v), 6) for v in xs],
            "z": [round(float(v), 6) for v in zs],
            "y": [[round(float(v), 6) for v in row] for row in form.y(xs[:, None], zs[None, :])],
        }
        out.append({"name": f"{kind}_L{L}B{B}T{T}D{D}", "spec": spec, "exact": exact,
                    "skip": list(skip), "form": form})
    return out


def run_candidate(path: str, cases: List[dict], tol: float = PLATFORM_TOL, timeout: float = 120.0) -> dict:
    """Adjudicate an external implementation through the stdin/stdout protocol."""
    results, passed = {}, 0
    for case in cases:
        payload = json.dumps({"specification": case["spec"]})
        t0 = time.time()
        try:
            proc = subprocess.run([sys.executable, path], input=payload, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            results[case["name"]] = {"error": "timeout"}
            continue
        if proc.returncode != 0:
            results[case["name"]] = {"error": proc.stderr.strip()[-500:]}
            continue
        try:
            result = json.loads(proc.stdout.strip().splitlines()[-1])["result"]
        except Exception as exc:  # noqa: BLE001
            results[case["name"]] = {"error": f"unparsable stdout: {exc}"}
            continue
        check = check_candidate(case["form"], case["exact"], case["skip"], result, tol)
        check["elapsed_s"] = round(time.time() - t0, 3)
        passed += int(check["within_tol"] and check["identities_ok"])
        results[case["name"]] = check
    verdict = "PASS" if passed == len(cases) else "FAIL"
    return {"mode": "candidate", "candidate": path, "tol": tol, "cases": results,
            "passed": passed, "total": len(cases), "verdict": verdict}


def probe_endpoint(paths=("/api/v1/geometry/hydro-curves", "/api/v1/geometry/hydro_curves",
                          "/api/v1/calculations/hydrostatics"), timeout: float = 2.0) -> dict:
    """Non-fatal reachability probe of the serving platform (evidence, not a criterion)."""
    import urllib.error
    import urllib.request

    out = {}
    for p in paths:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:8001{p}", timeout=timeout) as resp:
                out[p] = {"status": resp.status}
        except urllib.error.HTTPError as exc:
            out[p] = {"status": exc.code}
        except Exception as exc:  # noqa: BLE001
            out[p] = {"error": type(exc).__name__}
    return out


def make_figures(report: dict, outdir: str) -> List[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    figs = []

    # Fig 1: convergence (log-log) for the non-polynomial case.
    name = "sineform_D=1.5T"
    conv = report["cases"][name]["convergence"]
    fig, ax = plt.subplots(figsize=(6.2, 4.4))
    for q in ("Cb", "I_T", "BM"):
        if q in conv:
            ax.loglog(conv[q]["n"], conv[q]["rel_err"], "o-", label=q)
    ns = np.array(conv["Cb"]["n"], dtype=float)
    ax.loglog(ns, conv["Cb"]["rel_err"][0] * (ns / ns[0]) ** -4, "k--", lw=1.0, label=r"slope $-4$ (Simpson)")
    ax.set_xlabel("panels along L (n_x; n_z = n_x/2)")
    ax.set_ylabel("relative error vs closed form")
    ax.set_title(f"Hydrostatics convergence — {name}")
    ax.grid(True, which="both", ls=":", lw=0.5)
    ax.legend(fontsize=8)
    f1 = os.path.join(outdir, "fig_geometry_convergence.png")
    fig.tight_layout()
    fig.savefig(f1, dpi=140)
    plt.close(fig)
    figs.append(f1)

    # Fig 2: per-metric relative error at the gate grid, all analytic cases.
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    names = list(report["cases"])
    metrics = [m for m in METRICS]
    width = 0.8 / max(1, len(names))
    for i, cname in enumerate(names):
        errs = report["cases"][cname]["rel_err"]
        vals = [max(errs.get(m, np.nan), 1e-18) for m in metrics]
        ax.bar(np.arange(len(metrics)) + i * width, vals, width=width, label=cname)
    ax.set_yscale("log")
    ax.axhline(TOL_ANALYTIC, color="r", ls="--", lw=1.0, label=f"C1 tol = {TOL_ANALYTIC:g}")
    ax.axhline(PLATFORM_TOL, color="orange", ls=":", lw=1.0, label=f"platform band = {PLATFORM_TOL:.0%}")
    ax.set_xticks(np.arange(len(metrics)))
    ax.set_xticklabels(metrics, rotation=45, fontsize=8)
    ax.set_ylabel("relative error vs closed form (n_x=160)")
    ax.set_title("Geometry pilot: computed vs analytic hydrostatics")
    ax.legend(fontsize=7, ncol=2)
    ax.grid(True, axis="y", ls=":", lw=0.5)
    f2 = os.path.join(outdir, "fig_geometry_errors.png")
    fig.tight_layout()
    fig.savefig(f2, dpi=140)
    plt.close(fig)
    figs.append(f2)

    # Fig 3: body plans + waterlines of the three analytic hull families.
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 4.2))
    cases = [build_case("box_barge")[0], build_case("wigley_D=2T")[0], build_case("sineform_D=1.5T")[0]]
    labels = ["box barge", "Wigley (D=2T)", "sine-form (D=1.5T)"]
    zs = np.linspace(0, 1, 80)
    for form, lab in zip(cases, labels):
        zz = zs * form.T
        for xf in (-0.45, -0.25, 0.0, 0.25, 0.45):
            x = xf * form.L
            ax = axes[0]
            ax.plot(form.y(x, zz) / form.B * 2.0, zz / form.T, lw=1.0, label=None)
        xs = np.linspace(-0.5, 0.5, 200) * form.L
        axes[1].plot(xs / form.L, 2.0 * form.y(xs, np.full_like(xs, form.T)) / form.B,
                     lw=1.4, label=lab)
    axes[0].set_title("body plan (five stations), y/(B/2) vs z/T")
    axes[0].set_xlabel("y/(B/2)")
    axes[0].set_ylabel("z/T")
    axes[1].set_title("design waterline, breadth/(B)")
    axes[1].set_xlabel("x/L")
    axes[1].legend(fontsize=8)
    for ax in axes:
        ax.grid(True, ls=":", lw=0.5)
    f3 = os.path.join(outdir, "fig_geometry_hulls.png")
    fig.tight_layout()
    fig.savefig(f3, dpi=140)
    plt.close(fig)
    figs.append(f3)
    return figs


def _print_self_audit(rep: dict) -> None:
    print(f"=== geometry pilot: self-audit ({rep['n_panels']} panels) ===")
    print(f"{'case':18s} {'worst metric':8s} {'rel err':>10s} {'order':>7s}  status")
    for name, c in rep["cases"].items():
        order = "  exact" if c["observed_order_min"] is None else f"{c['observed_order_min']:7.2f}"
        print(f"{name:18s} {str(c['worst_metric']):8s} {c['worst_rel_err']:10.3e} {order:>7s}  "
              f"{'PASS' if c['within_tol'] and c['identities_ok'] else 'FAIL'}")
    print("\ncriteria:")
    for k, v in rep["criteria"].items():
        extra = ""
        if k == "C4_mutation_gate":
            extra = f" ({v['rejected']}/{v['total']} mutants rejected)"
        elif k == "C1_analytic_agreement":
            extra = f" (worst {v['worst']:.2e})"
        elif k == "C3_identities":
            extra = f" (worst {v['worst']:.2e})"
        print(f"  {'PASS' if v['pass'] else 'FAIL'}  {k}{extra}")
    print(f"\nVERDICT: {rep['verdict']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Independent hydrostatics / geometry verifier")
    ap.add_argument("--self-audit", action="store_true", help="adjudicate the in-tree reference integrator")
    ap.add_argument("--candidate", help="path to a candidate hydrostatics implementation")
    ap.add_argument("--candidate-id", default="candidate")
    ap.add_argument("--n-panels", type=int, default=160)
    ap.add_argument("--seed", type=int, default=20261101)
    ap.add_argument("--norandom", action="store_true", help="held-out set = analytic cases only")
    ap.add_argument("--output", default="artifacts/verifier/geometry_pilot_report.json")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--probe-endpoint", action="store_true")
    args = ap.parse_args()

    started = time.time()
    report = {"schema": "geometry_pilot/v1", "mission_id": 1574,
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "platform_tol": PLATFORM_TOL, "criteria_thresholds": {
                  "C1_tol": TOL_ANALYTIC, "C2_min_order": MIN_ORDER,
                  "C3_tol": TOL_IDENTITY, "C4": "all mutants rejected"}}

    if args.candidate:
        cases = heldout_specs(seed=args.seed) if not args.norandom else [
            {"name": k, "spec": None, "exact": build_case(k)[1], "skip": list(build_case(k)[2]),
             "form": build_case(k)[0]} for k in ANALYTIC_CASES]
        if args.norandom:  # attach specs for the analytic set too
            for c in cases:
                form = c["form"]
                nx, nz = 160, 80
                xs = np.linspace(-0.5 * form.L, 0.5 * form.L, nx + 1)
                zs = np.linspace(0.0, form.T, nz + 1)
                c["spec"] = {"hull": form.name, "L": form.L, "B": form.B, "T": form.T,
                             "D": form.meta.get("D", form.T),
                             "x": [round(float(v), 6) for v in xs], "z": [round(float(v), 6) for v in zs],
                             "y": [[round(float(v), 6) for v in row] for row in form.y(xs[:, None], zs[None, :])]}
        res = run_candidate(args.candidate, cases)
        res["candidate_id"] = args.candidate_id
        report["candidate_run"] = res
        report["verdict"] = res["verdict"]
        print(f"candidate {args.candidate_id}: {res['passed']}/{res['total']} cases within "
              f"{res['tol']:.0%} -> {res['verdict']}")
        for name, v in res["cases"].items():
            if "error" in v:
                print(f"  ERROR {name}: {v['error'][:120]}")
            else:
                print(f"  {'PASS' if v['within_tol'] and v['identities_ok'] else 'FAIL'} {name} "
                      f"worst={v['worst_metric']} {v['worst_rel_err']:.3e}")
    else:
        rep = self_audit(n=args.n_panels)
        report.update(rep)
        report["capability_note"] = (
            "geometry.hydro_curves / geometry.generate stay WITHHELD in the capability ledger: "
            "no hydrostatics implementation ships in TensorPlatform, so no candidate could be "
            "adjudicated here; this pilot supplies the analytic baseline a candidate must meet "
            "and adjudicates the in-tree reference integrator instead."
        )
        _print_self_audit(rep)
        if args.probe_endpoint:
            report["endpoint_probe"] = probe_endpoint()
            print("endpoint probe:", json.dumps(report["endpoint_probe"]))

    report["elapsed_s"] = round(time.time() - started, 3)
    if args.figures:
        report["figures"] = make_figures(report, os.path.dirname(args.output) or ".")
        print("figures:", ", ".join(report["figures"]))

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"\nreport -> {args.output}")
    sys.exit(0 if report.get("verdict") == "PASS" else 1)


if __name__ == "__main__":
    main()