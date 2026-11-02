#!/usr/bin/env python3
"""Independent accuracy audit of the digital-twin PCE surrogates (mission m1647).

Why this exists
---------------
``services/digital_twin/surrogate_registry.py`` ships five polynomial-chaos
surrogates (resistance / seakeeping / manoeuvring / stability / fatigue) that the
real-time predictor (``services/digital_twin/online_predictor.py``) calls with
1-2 ms latency budgets.  Their only accuracy evidence was the PCE's *own*
hold-out diagnostics printed at fit time (``fit_report``), which

* are drawn from the same uniform design as the training set,
* are reported in the space the polynomial is fit in (log10 for
  ``damage_per_day``), and
* are never compared to the platform's 15 % acceptance bar.

This tool judges the shipped surrogates against their *reference physics model*
on a fresh, independently seeded uniform hold-out, in **linear output space**
(the space a user reads), and emits a machine-readable ledger plus the markdown
report and figures.  Nothing is hand-copied: every number in the report and in
``docs/verification/digital_twin_surrogate_accuracy.md`` comes from the ledger
computed here.

Judges (per output, evaluated in linear space on the hold-out):

``J1``  p95 relative error <= 15 %   (platform acceptance bar)
``J2``  median relative error <= 5 %
``J3``  R^2 >= 0.95 (linear space)
``J4``  domain guard: out-of-range input is rejected and ``safe_predict``
        degrades to the reference model instead of extrapolating silently
``J5``  surrogate latency <= 2 ms (the online predictor real-time budget;
        the speedup over the reference model is reported alongside)

Relative errors are only defined away from zero: points with
``|ref| < 1e-3 * max|ref|`` are excluded from J1/J2 and reported separately; the
zero-crossing outputs (``GZ_m`` near 0 deg heel) are additionally judged on the
absolute error normalised by the output range (``J6``, <= 5 % of range).

Usage
-----
    python3 tools/verify_digital_twin_surrogates.py            # full audit
    python3 tools/verify_digital_twin_surrogates.py --quick     # smaller hold-out
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services.digital_twin.surrogate_registry import (  # noqa: E402
    DEFAULT_VESSEL,
    HullForm,
    get_registry,
)

HOLDOUT = {"resistance": 192, "seakeeping": 64, "manoeuvring": 24,
           "stability": 48, "fatigue": 192}
HOLDOUT_QUICK = {k: max(12, v // 4) for k, v in HOLDOUT.items()}

SEED = 20261101
THRESHOLD_PCT = 15.0
MEDIAN_PCT = 5.0
R2_MIN = 0.95
RANGE_TOL = 0.05          # J6: max abs error <= 5 % of the output range
FLOOR_FRAC = 1.0e-3       # relative-error floor, fraction of max |ref|
SPEEDUP_MIN = 10.0
LATENCY_MAX_MS = 2.0      # J5: real-time budget of the online predictor

ARTIFACTS = os.path.join(_ROOT, "artifacts", "m1647")
OUT_JSON = os.path.join(ARTIFACTS, "digital_twin_surrogate_audit.json")
OUT_MD = os.path.join(_ROOT, "docs", "verification",
                      "digital_twin_surrogate_accuracy.md")
FIG_DIR = os.path.join(_ROOT, "docs", "verification", "figs")


def _holdout(surrogate, n: int, seed: int):
    fields = list(surrogate.inputs.fields)
    names = [f.name for f in fields]
    lo = np.array([f.lower for f in fields], dtype=float)
    hi = np.array([f.upper for f in fields], dtype=float)
    rng = np.random.default_rng(seed)
    return names, lo + (hi - lo) * rng.random((n, len(lo)))


def audit_surrogate(registry, name: str, n_holdout: int, seed: int) -> Dict[str, Any]:
    s = registry.get(name)
    names, X = _holdout(s, n_holdout, seed)
    outs = list(s.pce.output_names)
    ref = np.empty((len(X), len(outs)))
    got = np.empty_like(ref)
    t_ref = t_sur = 0.0
    for i, row in enumerate(X):
        point = dict(zip(names, (float(v) for v in row)))
        t0 = time.perf_counter()
        r = s._predict_reference(point)
        t_ref += time.perf_counter() - t0
        t0 = time.perf_counter()
        p = s.predict(point)
        t_sur += time.perf_counter() - t0
        ref[i] = [r[k] for k in outs]
        got[i] = [p[k] for k in outs]

    per_output: List[Dict[str, Any]] = []
    for j, out in enumerate(outs):
        r, p = ref[:, j], got[:, j]
        span = float(np.max(np.abs(r)) - np.min(r))
        span = span if span > 0 else max(abs(float(np.max(np.abs(r)))), 1e-30)
        keep = np.abs(r) >= FLOOR_FRAC * float(np.max(np.abs(r)))
        rel = np.abs(p[keep] - r[keep]) / np.abs(r[keep])
        ss_tot = float(np.sum((r - r.mean()) ** 2))
        r2 = 1.0 - float(np.sum((p - r) ** 2)) / ss_tot if ss_tot > 0 else 1.0
        err = np.abs(p - r)
        worst = int(np.argmax(np.where(keep, rel_full := np.abs(p - r) /
                                      np.maximum(np.abs(r), 1e-300), -1.0)))
        per_output.append({
            "output": out,
            "n_compared": int(keep.sum()),
            "n_excluded_near_zero": int((~keep).sum()),
            "r2_linear": round(r2, 6),
            "median_rel_pct": round(float(np.median(rel)) * 100.0, 4),
            "p95_rel_pct": round(float(np.percentile(rel, 95)) * 100.0, 4),
            "max_rel_pct": round(float(np.max(rel)) * 100.0, 4),
            "max_abs_error": float(np.max(err)),
            "max_abs_error_pct_of_range": round(100.0 * float(np.max(err)) / span, 4),
            "ref_range": [float(np.min(r)), float(np.max(r))],
            "worst_point": {k: round(float(v), 6) for k, v in zip(names, X[worst])},
            "worst_ref": float(r[worst]),
            "worst_pred": float(p[worst]),
            "J1_p95_le_15pct": bool(np.percentile(rel, 95) * 100.0 <= THRESHOLD_PCT),
            "J2_median_le_5pct": bool(np.median(rel) * 100.0 <= MEDIAN_PCT),
            "J3_r2_ge_0.95": bool(r2 >= R2_MIN),
            "J6_max_abs_le_5pct_range": bool(100.0 * float(np.max(err)) / span <= RANGE_TOL * 100.0),
        })

    # J4: domain guard + reference fallback, exercised on the live object.
    # The contract is fail-closed: an out-of-domain point must be *rejected*
    # (never silently extrapolated), and an execution failure must degrade to
    # the reference model.
    out_of_range = dict(zip(names, [f.lower - abs(f.lower) - 1.0
                                    for f in s.inputs.fields]))
    try:
        s.safe_predict(out_of_range)
        rejected, guard_error = False, None
    except Exception as exc:                              # noqa: BLE001
        rejected, guard_error = type(exc).__name__ == "InputValidationError", type(exc).__name__
    original = s.pce.predict
    s.pce.predict = lambda values: (_ for _ in ()).throw(RuntimeError("audit-injected"))
    try:
        degraded = s.safe_predict(dict(zip(names, (float(v) for v in X[0]))))
    finally:
        s.pce.predict = original
    ref0 = s._predict_reference(dict(zip(names, (float(v) for v in X[0]))))
    fallback_err = max(abs(degraded["outputs"][k] - ref0[k]) /
                       max(abs(ref0[k]), 1e-30) for k in outs)
    per_output.append({})
    per_output.pop()
    record = {
        "surrogate": name,
        "reference_model": s.reference_model,
        "degree": int(s.pce.degree),
        "basis_terms": int(s.fit_report["n_basis"]),
        "training_samples": int(s.fit_report["training_samples"]),
        "holdout_points": int(len(X)),
        "holdout_seed": seed,
        "latency_ms": {
            "surrogate": round(1000.0 * t_sur / len(X), 4),
            "reference": round(1000.0 * t_ref / len(X), 3),
            "speedup": round(t_ref / t_sur, 1) if t_sur > 0 else None,
        },
        "J4_domain_guard": {
            "out_of_range_rejected": bool(rejected),
            "rejection_error": guard_error,
            "degraded_to_reference": bool(degraded.get("degraded")),
            "fallback_max_rel_error": float(fallback_err),
            "pass": bool(rejected and degraded.get("degraded")
                         and fallback_err < 1e-12),
        },
        "J5_latency_le_2ms": bool(1000.0 * t_sur / len(X) <= LATENCY_MAX_MS),
        "outputs": per_output,
        "output_vectors": {"ref": ref.tolist(), "surrogate": got.tolist(),
                           "inputs": X.tolist(), "input_names": names},
    }
    record["pass"] = bool(
        record["J4_domain_guard"]["pass"] and record["J5_latency_le_2ms"]
        and all(o["J1_p95_le_15pct"] and o["J2_median_le_5pct"]
                and o["J3_r2_ge_0.95"] and o["J6_max_abs_le_5pct_range"]
                for o in per_output))
    return record


def _figures(records: List[Dict[str, Any]]) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(FIG_DIR, exist_ok=True)
    paths = []

    # Figure 1: parity plot, one panel per surrogate, all outputs.
    n = len(records)
    fig, axes = plt.subplots(1, n, figsize=(3.4 * n, 3.6))
    axes = np.atleast_1d(axes)
    for ax, rec in zip(axes, records):
        ref = np.array(rec["output_vectors"]["ref"])
        got = np.array(rec["output_vectors"]["surrogate"])
        for j, out in enumerate([o["output"] for o in rec["outputs"]]):
            r, p = ref[:, j], got[:, j]
            scale = max(np.max(np.abs(r)), 1e-30)
            ax.scatter(r / scale, p / scale, s=9, alpha=0.65, label=out)
        lim = [0, 1.05]
        ax.plot(lim, lim, "k--", lw=0.8)
        ax.set_xlim(lim); ax.set_ylim(lim)
        ax.set_title(f"{rec['surrogate']}\ndeg={rec['degree']}, n={rec['training_samples']}",
                     fontsize=9)
        ax.set_xlabel("reference (normalised)", fontsize=8)
        ax.set_ylabel("surrogate (normalised)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=6)
        ax.grid(alpha=0.3)
    fig.suptitle("Digital-twin PCE surrogates vs reference physics — "
                 f"independent hold-out ({records[0]['holdout_points']}-"
                 f"{max(r['holdout_points'] for r in records)} pts, seed {SEED})",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    p1 = os.path.join(FIG_DIR, "digital_twin_surrogate_parity.png")
    fig.savefig(p1, dpi=150)
    plt.close(fig)
    paths.append(p1)

    # Figure 2: p95 relative error per output vs the 15 % bar.
    labels, p95, med = [], [], []
    for rec in records:
        for o in rec["outputs"]:
            labels.append(f"{rec['surrogate']}.{o['output']}")
            p95.append(o["p95_rel_pct"])
            med.append(o["median_rel_pct"])
    fig, ax = plt.subplots(figsize=(10, 4.2))
    x = np.arange(len(labels))
    ax.bar(x - 0.2, med, width=0.4, label="median rel. err", color="#4c72b0")
    ax.bar(x + 0.2, p95, width=0.4, label="p95 rel. err", color="#dd8452")
    ax.axhline(THRESHOLD_PCT, color="crimson", ls="--", lw=1.2,
               label=f"acceptance bar {THRESHOLD_PCT:.0f}%")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=70, ha="right", fontsize=7)
    ax.set_ylabel("relative error [%] (log)")
    ax.set_title("Hold-out relative error per surrogate output", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    p2 = os.path.join(FIG_DIR, "digital_twin_surrogate_error_bars.png")
    fig.savefig(p2, dpi=150)
    plt.close(fig)
    paths.append(p2)
    return paths


def _markdown(payload: Dict[str, Any], figs: List[str]) -> str:
    recs = payload["surrogates"]
    L: List[str] = []
    L.append("# Digital-twin surrogate accuracy audit (mission m1647)\n")
    L.append("**Scope** — the five PCE surrogates of "
             "`backend/services/digital_twin/surrogate_registry.py` that serve "
             "`services/digital_twin/online_predictor.py` (resistance, seakeeping, "
             "manoeuvring, stability, fatigue), judged against their reference "
             "physics models on an **independent uniform hold-out** in linear "
             "output space.\n")
    L.append(f"**Hull** — L={payload['vessel']['L']} m, B={payload['vessel']['B']} m, "
             f"T={payload['vessel']['T']} m, Cb={payload['vessel']['Cb']}, "
             f"KG={payload['vessel']['KG']} m (geometry closure "
             f"`{payload['vessel']['geometry_check']}`).\n")
    L.append(f"**Design** — hold-out points per surrogate: "
             f"`{payload['holdout']}`, seed {payload['seed']}, thresholds "
             f"J1 p95<={THRESHOLD_PCT:.0f}%, J2 median<={MEDIAN_PCT:.0f}%, "
             f"J3 R²>={R2_MIN}, J6 max |error| <={RANGE_TOL*100:.0f}% of range, "
             f"J5 surrogate latency<={LATENCY_MAX_MS:.1f} ms.\n")
    verdict = "**PASS**" if payload["all_pass"] else "**FAIL (honest)**"
    L.append(f"**Verdict** — {verdict}: "
             f"{sum(1 for r in recs if r['pass'])}/{len(recs)} surrogates pass "
             f"every judge; failing judge instances: "
             f"{payload['failing_judges'] or 'none'}.\n")
    L.append("## Reproduction\n")
    L.append("```bash\npython3 tools/verify_digital_twin_surrogates.py\n"
             "python3 -m pytest tests/test_digital_twin_surrogate_accuracy.py -q\n```\n")
    L.append(f"Ledger: `{os.path.relpath(OUT_JSON, _ROOT)}`.\n")

    L.append("## Per-surrogate results\n")
    L.append("| surrogate | degree | basis | n_train | p95 worst output | "
             "median worst output | speedup | J4 guard | verdict |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in recs:
        w95 = max(r["outputs"], key=lambda o: o["p95_rel_pct"])
        wmed = max(r["outputs"], key=lambda o: o["median_rel_pct"])
        L.append(f"| `{r['surrogate']}` | {r['degree']} | {r['basis_terms']} | "
                 f"{r['training_samples']} | {w95['p95_rel_pct']:.2f}% "
                 f"(`{w95['output']}`) | {wmed['median_rel_pct']:.2f}% "
                 f"(`{wmed['output']}`) | {r['latency_ms']['speedup']}x | "
                 f"{'ok' if r['J4_domain_guard']['pass'] else 'FAIL'} | "
                 f"{'PASS' if r['pass'] else 'FAIL'} |")
    L.append("")
    for r in recs:
        L.append(f"### `{r['surrogate']}` — reference `{r['reference_model']}`\n")
        L.append(f"Latency: surrogate {r['latency_ms']['surrogate']:.3f} ms vs "
                 f"reference {r['latency_ms']['reference']:.2f} ms "
                 f"({r['latency_ms']['speedup']}x). "
                 f"Hold-out: {r['holdout_points']} points.\n")
        L.append("| output | n | R² (lin) | median | p95 | max | max abs | "
                 "max abs / range | J1 | J2 | J3 | J6 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for o in r["outputs"]:
            L.append(f"| `{o['output']}` | {o['n_compared']} | {o['r2_linear']:.5f} | "
                     f"{o['median_rel_pct']:.2f}% | {o['p95_rel_pct']:.2f}% | "
                     f"{o['max_rel_pct']:.1f}% | {o['max_abs_error']:.4g} | "
                     f"{o['max_abs_error_pct_of_range']:.2f}% | "
                     f"{'P' if o['J1_p95_le_15pct'] else 'F'} | "
                     f"{'P' if o['J2_median_le_5pct'] else 'F'} | "
                     f"{'P' if o['J3_r2_ge_0.95'] else 'F'} | "
                     f"{'P' if o['J6_max_abs_le_5pct_range'] else 'F'} |")
        worst = max(r["outputs"], key=lambda o: o["p95_rel_pct"])
        L.append("")
        L.append(f"Worst p95 point (`{worst['output']}`): inputs "
                 f"`{worst['worst_point']}`, reference {worst['worst_ref']:.5g}, "
                 f"surrogate {worst['worst_pred']:.5g}.\n")
    if figs:
        L.append("## Figures\n")
        L.append(f"![parity](figs/{os.path.basename(figs[0])})\n")
        L.append(f"![error bars](figs/{os.path.basename(figs[1])})\n")
    L.append(payload["notes"])
    return "\n".join(L) + "\n"


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true", help="smaller hold-out")
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--json", default=OUT_JSON)
    ap.add_argument("--md", default=OUT_MD)
    args = ap.parse_args(argv)

    sizes = HOLDOUT_QUICK if args.quick else HOLDOUT
    hull = HullForm(DEFAULT_VESSEL["L"], DEFAULT_VESSEL["B"],
                    DEFAULT_VESSEL["T"], DEFAULT_VESSEL["Cb"])
    t0 = time.perf_counter()
    registry = get_registry(DEFAULT_VESSEL["L"], DEFAULT_VESSEL["B"],
                            DEFAULT_VESSEL["T"], DEFAULT_VESSEL["Cb"],
                            DEFAULT_VESSEL["KG"], cache=False)
    fit_s = time.perf_counter() - t0
    records = [audit_surrogate(registry, name, sizes[name], SEED)
               for name in registry.names()]
    failing = [f"{r['surrogate']}.{o['output']}.{j}"
               for r in records for o in r["outputs"]
               for j, key in (("J1", "J1_p95_le_15pct"), ("J2", "J2_median_le_5pct"),
                              ("J3", "J3_r2_ge_0.95"), ("J6", "J6_max_abs_le_5pct_range"))
               if not o[key]]
    failing += [f"{r['surrogate']}.{'J4' if not r['J4_domain_guard']['pass'] else 'J5'}"
                for r in records
                if not (r["J4_domain_guard"]["pass"] and r["J5_latency_le_2ms"])]

    notes = (
        "## Notes / limitations\n\n"
        "* The reference model *is* the ground truth here: this audit measures "
        "surrogate-vs-model fidelity, **not** physical accuracy against model "
        "tests (that remains WITHHELD — no tank data is bundled).\n"
        "* The reference models are the platform's own `holtrop_mennen_1982 + "
        "lindqvist_1989`, `2dof_modal_strip + raw_empirical_hs_only_v1`, `mmg_3dof_crossflow_lift`, "
        "`numerical_kn_gz_hydrostatics` and `spectral_miner_dnv_c203_curveF` "
        "implementations, so a shared modelling error would be invisible.\n"
        "* Relative error is undefined where the reference crosses zero "
        f"(`GZ_m` near 0 deg heel); those points are excluded from J1/J2 and the "
        "range-normalised absolute error J6 is used instead.\n"
        "* The hold-out is uniform over the declared input box; it therefore "
        "weights rare combinations (e.g. 8 m seas with 3 s peak period) equally "
        "with realistic ones.  Error at the box corners is the price of a "
        "domain-wide guarantee; a sea-state-weighted study is left open.\n")

    payload = {
        "mission": "m1647",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vessel": {**DEFAULT_VESSEL,
                   "geometry_check": registry.vessel.get("geometry_check")},
        "seed": SEED,
        "holdout": sizes,
        "thresholds": {"p95_pct": THRESHOLD_PCT, "median_pct": MEDIAN_PCT,
                       "r2_min": R2_MIN, "range_frac": RANGE_TOL,
                       "latency_max_ms": LATENCY_MAX_MS},
        "build_time_s": round(fit_s, 2),
        "surrogates": records,
        "all_pass": all(r["pass"] for r in records),
        "failing_judges": failing,
        "notes": notes,
    }
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    figs = [] if args.no_figures else _figures(records)
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    os.makedirs(os.path.dirname(args.md), exist_ok=True)
    with open(args.md, "w", encoding="utf-8") as fh:
        fh.write(_markdown(payload, figs))
    print(json.dumps({"all_pass": payload["all_pass"],
                      "failing_judges": failing,
                      "build_time_s": payload["build_time_s"],
                      "ledger": args.json, "markdown": args.md,
                      "figures": figs}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())