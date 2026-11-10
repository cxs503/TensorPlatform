"""Machine-closed-loop UQ runner: PCE accuracy, uncertainty band, MC acceleration.

The runner is deliberately generic -- it knows nothing about ships.  A *case
file* (``config/uq_cases/*.json``) wires a black-box model, its marginals, the
PCE configuration, the Monte Carlo budget and the acceptance thresholds; the
runner then

1. builds the non-intrusive PCE (spectral projection or LHS regression),
2. estimates the same statistics by direct Monte Carlo at the stated budget,
3. scores the PCE against a closed-form reference (when one exists) and against
   the Monte Carlo baseline (mean / standard deviation / uncertainty band),
4. reports the acceleration both in model evaluations and in measured wall
   clock, on the same scalar calling interface, and
5. decides each acceptance criterion mechanically and prints a ledger that is
   the only source of the numbers quoted in the report.

Usage::

    python -m tools.uq_bench.run_case --all --out-dir artifacts/uq_pce_m9011
    python -m tools.uq_bench.run_case --case config/uq_cases/ishigami.json
    python -m tools.uq_bench.run_case --all --out-dir ... --sweep
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from backend.services.pce_uq import (
    CaseSpec,
    Marginal,
    compare_moments,
    fit,
    monte_carlo,
)
from tools.uq_bench.references import closed_form

CASE_DIR = Path("config/uq_cases")
DEFAULT_CASES = ("ishigami_sobol", "resistance_six_parameter")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:  # pragma: no cover - non git checkout
        return "unknown"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _median_time(fn, reps: int = 3):
    """Run ``fn`` ``reps`` times, return (last_result, median, all samples)."""
    times: List[float] = []
    result = None
    for _ in range(max(int(reps), 1)):
        t0 = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - t0)
    return result, float(statistics.median(times)), [float(t) for t in times]


def _rel(a: float, b: float) -> float:
    return float(abs(a - b) / abs(b)) if b not in (0.0, None) else float("inf")


# --------------------------------------------------------------------------- #
# case execution
# --------------------------------------------------------------------------- #
def run_case(case: CaseSpec, case_path: Optional[Path] = None, reps: int = 3,
             with_sweep: bool = False) -> Dict[str, Any]:
    func = case.model.load()
    marginals: List[Marginal] = case.marginals
    pce_cfg = dict(case.pce)
    mc_cfg = dict(case.mc)

    method = str(pce_cfg.pop("method", "projection"))
    order = int(pce_cfg.pop("order", 3))
    fit_kwargs = dict(pce_cfg)
    fit_kwargs.setdefault("output", case.model.output)
    fit_kwargs.setdefault("call_style", case.model.call_style)
    fit_kwargs.setdefault("fixed", case.model.fixed)

    pce, t_pce, t_pce_samples = _median_time(
        lambda: fit(func, marginals, order=order, method=method, **fit_kwargs), reps=reps)

    mc_n = int(mc_cfg.get("n", 10000))
    mc_sampler = str(mc_cfg.get("sampler", "lhs"))
    mc_seed = int(mc_cfg.get("seed", 12345))
    mc, t_mc, t_mc_samples = _median_time(
        lambda: monte_carlo(func, marginals, n=mc_n, sampler=mc_sampler, seed=mc_seed,
                            output=case.model.output, call_style=case.model.call_style,
                            fixed=case.model.fixed), reps=reps)

    comparison = compare_moments(pce, mc, surrogates=int(mc_cfg.get("surrogate_draws", 20000)))
    # measured wall clock takes the median pipeline timings (single-run fit times
    # are too noisy at the millisecond scale)
    counts, edges = np.histogram(np.asarray(mc.samples, dtype=float), bins=60)
    comparison["mc_sample_histogram"] = {
        "edges": [float(v) for v in edges],
        "counts": [int(v) for v in counts],
        "n": int(len(mc.samples)),
    }
    comparison["timing"] = {
        "pce_pipeline_seconds_median": t_pce,
        "mc_pipeline_seconds_median": t_mc,
        "reps": int(reps),
        "pce_pipeline_seconds_samples": t_pce_samples,
        "mc_pipeline_seconds_samples": t_mc_samples,
        "speedup_wall_clock_median": float(t_mc / max(t_pce, 1e-12)),
        "speedup_wall_clock_min": float(min(t_mc_samples) / max(max(t_pce_samples), 1e-12)),
        "speedup_wall_clock_max": float(max(t_mc_samples) / max(min(t_pce_samples), 1e-12)),
    }

    # nominal point: the model evaluated at each input's centre, used to anchor
    # the report ("what the deterministic run would have said")
    nominal = {}
    for marginal in marginals:
        if marginal.family == "uniform":
            nominal[marginal.name] = 0.5 * (marginal.lower + marginal.upper)
        else:
            nominal[marginal.name] = marginal.mean
    nominal_kwargs = dict(nominal)
    nominal_kwargs.update(case.model.fixed)
    if case.model.call_style == "array":
        nominal_value = func(np.array([nominal[m.name] for m in marginals]))
    else:
        nominal_value = func(**nominal_kwargs)
    if case.model.output is not None:
        if hasattr(nominal_value, "as_dict"):
            nominal_value = nominal_value.as_dict()[case.model.output]
        elif isinstance(nominal_value, dict):
            nominal_value = nominal_value[case.model.output]
        else:
            nominal_value = getattr(nominal_value, case.model.output)
    nominal_value = float(np.asarray(nominal_value, dtype=float).reshape(-1)[0])

    result: Dict[str, Any] = {
        "case_id": case.case_id,
        "nominal_inputs": {k: float(v) for k, v in nominal.items()},
        "nominal_response": nominal_value,
        "title": case.title,
        "spec": case.as_dict(),
        "spec_sha256": _sha256(case_path) if case_path else None,
        "pce": pce.as_dict(),
        "sobol": pce.sobol(),
        "comparison": comparison,
    }

    # -- closed-form reference (analytic Sobol' / moments) -------------------
    if case.reference and str(case.reference.get("kind", "")).lower() == "closed_form":
        ref = closed_form(str(case.reference["name"]), **dict(case.reference.get("kwargs", {})))
        sobol = pce.sobol()
        errs = {}
        for key in ("first_order", "total_order"):
            for name, val in ref[key].items():
                if name in sobol[key]:
                    errs[f"{key}.{name}"] = float(abs(sobol[key][name] - val))
        result["closed_form_reference"] = ref
        result["sobol_errors"] = errs
        result["sobol_max_abs_error"] = float(max(errs.values())) if errs else float("nan")
        result["mean_error_vs_closed_form"] = _rel(pce.mean, ref["mean"])
        result["variance_error_vs_closed_form"] = _rel(pce.variance, ref["variance"])

    # -- optional high-accuracy MC reference --------------------------------
    moment_reference = mc
    if mc_cfg.get("reference_n"):
        ref_run = monte_carlo(func, marginals, n=int(mc_cfg["reference_n"]),
                              sampler=str(mc_cfg.get("reference_sampler", "qmc")),
                              seed=int(mc_cfg.get("reference_seed", 999)),
                              output=case.model.output, call_style=case.model.call_style,
                              fixed=case.model.fixed)
        moment_reference = ref_run
        result["comparison"]["error_vs_high_accuracy_reference"] = {
            "mean_rel": _rel(comparison["pce"]["mean_sampled"], ref_run.mean),
            "std_rel": _rel(comparison["pce"]["std_sampled"], ref_run.std),
            "mc_baseline_mean_rel": _rel(mc.mean, ref_run.mean),
            "mc_baseline_std_rel": _rel(mc.std, ref_run.std),
            "reference": ref_run.as_dict(),
        }

    # -- uncertainty band ---------------------------------------------------
    probs = tuple(float(p) for p in case.mc.get("band_probs", (0.05, 0.5, 0.95)))
    band_pce = pce.quantiles(probs, n=int(mc_cfg.get("surrogate_draws", 20000)))
    band_mc = {f"p{100 * p:g}": float(np.quantile(mc.samples, p)) for p in probs}
    result["band"] = {
        "pce": band_pce,
        "mc": band_mc,
        "rel_error": {k: _rel(band_pce[k], band_mc[k]) for k in band_pce},
        "pce_mean_plus_minus_2sigma": [pce.mean - 2.0 * pce.std, pce.mean + 2.0 * pce.std],
        "mc_mean_plus_minus_2sigma": [mc.mean - 2.0 * mc.std, mc.mean + 2.0 * mc.std],
    }

    # -- acceptance criteria -------------------------------------------------
    criteria = dict(case.criteria)
    checks: Dict[str, Dict[str, Any]] = {}

    def add(name: str, value: float, threshold: float, ok: bool, note: str = "") -> None:
        checks[name] = {"value": float(value), "threshold": float(threshold),
                        "passed": bool(ok), "note": note}

    if "sobol_max_abs_error" in criteria:
        add("sobol_max_abs_error", result.get("sobol_max_abs_error", float("nan")),
            criteria["sobol_max_abs_error"],
            result.get("sobol_max_abs_error", float("inf")) <= criteria["sobol_max_abs_error"],
            "max |S_PCE - S_closed_form| over first/total order of all inputs")
    if "mean_rel_error" in criteria:
        v = comparison["error_vs_mc_baseline"]["mean_rel"]
        add("mean_rel_error", v, criteria["mean_rel_error"], v <= criteria["mean_rel_error"],
            "|mean_PCE - mean_MC| / |mean_MC|")
    if "std_rel_error" in criteria:
        v = comparison["error_vs_mc_baseline"]["std_rel"]
        add("std_rel_error", v, criteria["std_rel_error"], v <= criteria["std_rel_error"],
            "|std_PCE - std_MC| / std_MC")
    if "speedup_model_evaluations_min" in criteria:
        v = comparison["speedup_model_evaluations"]
        add("speedup_model_evaluations", v, criteria["speedup_model_evaluations_min"],
            v >= criteria["speedup_model_evaluations_min"],
            "n_MC / n_PCE black-box evaluations")
    if "speedup_wall_clock_min" in criteria:
        v = comparison["timing"]["speedup_wall_clock_median"]
        add("speedup_wall_clock", v, criteria["speedup_wall_clock_min"],
            v >= criteria["speedup_wall_clock_min"],
            "median wall clock of MC pipeline / PCE pipeline on the same callable")
    if "sum_first_order_max_abs" in criteria:
        v = abs(pce.sobol()["sum_first_order"] - 1.0) if pce.sobol()["sum_first_order"] else 0.0
        add("sum_first_order", v, criteria["sum_first_order_max_abs"],
            v <= criteria["sum_first_order_max_abs"], "|sum S_i - 1| (additivity residual)")

    result["criteria"] = criteria
    result["checks"] = checks
    result["passed"] = bool(checks) and all(c["passed"] for c in checks.values())

    if with_sweep:
        result["sweep"] = sweep_case(case, func, marginals, moment_reference=moment_reference)
    return result


# --------------------------------------------------------------------------- #
# cost / accuracy sweep (what does each configuration buy?)
# --------------------------------------------------------------------------- #
def sweep_case(case: CaseSpec, func=None, marginals: Optional[Sequence[Marginal]] = None,
               configs: Optional[Sequence[Dict[str, Any]]] = None,
               moment_reference=None) -> List[Dict[str, Any]]:
    """Cost/accuracy sweep: what each PCE configuration buys and costs."""
    func = func or case.model.load()
    marginals = list(marginals or case.marginals)
    if configs is None:
        configs = default_sweep(case)
    rows: List[Dict[str, Any]] = []
    ref = None
    if case.reference and str(case.reference.get("kind", "")).lower() == "closed_form":
        ref = closed_form(str(case.reference["name"]), **dict(case.reference.get("kwargs", {})))
    for cfg in configs:
        cfg = dict(cfg)
        method = str(cfg.pop("method", "projection"))
        order = int(cfg.pop("order", 3))
        try:
            pce = fit(func, marginals, order=order, method=method,
                      output=case.model.output, call_style=case.model.call_style,
                      fixed=case.model.fixed, **cfg)
        except Exception as exc:  # pragma: no cover - reported, not hidden
            rows.append({"config": {**cfg, "method": method, "order": order},
                         "error": f"{type(exc).__name__}: {exc}"})
            continue
        row: Dict[str, Any] = {
            "config": {**cfg, "method": method, "order": order},
            "order": order,
            "method": method,
            "n_terms": int(pce.indices.shape[0]),
            "n_model_evaluations": int(pce.n_model_evaluations),
            "fit_seconds": float(pce.fit_seconds),
            "mean": pce.mean,
            "std": pce.std,
            "speedup_model_evaluations": float(10000 / max(pce.n_model_evaluations, 1)),
        }
        if moment_reference is not None:
            draws = pce.sample_output(n=20000, seed=7)
            row["mean_rel_error"] = _rel(float(np.mean(draws)), moment_reference.mean)
            row["std_rel_error"] = _rel(float(np.std(draws, ddof=1)), moment_reference.std)
        if ref is not None:
            sobol = pce.sobol()
            errs = [
                abs(sobol[k][n] - v) for k in ("first_order", "total_order")
                for n, v in ref[k].items() if n in sobol[k]
            ]
            row["sobol_max_abs_error"] = float(max(errs)) if errs else float("nan")
        rows.append(row)
    return rows


def default_sweep(case: CaseSpec) -> List[Dict[str, Any]]:
    method = str(case.pce.get("method", "projection"))
    if method == "projection":
        if len(case.inputs) <= 3:
            return [{"method": "projection", "order": o} for o in (2, 4, 6, 8, 10)]
        return ([{"method": "projection", "order": o, "level": l}
                 for o, l in ((1, 1), (2, 2), (2, 3), (3, 3), (3, 4), (4, 4))]
                + [{"method": "regression", "order": 2, "n_samples": 56},
                   {"method": "regression", "order": 2, "n_samples": 112}])
    return [{"method": "regression", "order": o, "n_samples": n, "seed": 0}
            for o, n in ((1, None), (2, None), (3, None), (2, 128), (3, 192))]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", action="append", default=None,
                    help="case JSON path (repeatable)")
    ap.add_argument("--all", action="store_true", help="run every default case")
    ap.add_argument("--case-dir", default=str(CASE_DIR))
    ap.add_argument("--out-dir", default="artifacts/uq_pce_m9011")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--sweep", action="store_true", help="also run the cost/accuracy sweep")
    args = ap.parse_args(argv)

    paths: List[Path] = []
    if args.all:
        paths += [Path(args.case_dir) / f"{name}.json" for name in DEFAULT_CASES]
    for c in args.case or []:
        paths.append(Path(c))
    if not paths:
        ap.error("nothing to run: pass --all or --case")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ledger: Dict[str, Any] = {
        "tool": "tools.uq_bench.run_case",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": _git_commit(),
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "platform": platform.platform(),
        },
        "cases": {},
    }
    for path in paths:
        if not path.exists():
            raise SystemExit(f"case file not found: {path}")
        case = CaseSpec.load(path)
        print(f"[run] {case.case_id} <- {path}", flush=True)
        res = run_case(case, case_path=path, reps=args.reps, with_sweep=args.sweep)
        ledger["cases"][case.case_id] = res
        (out_dir / f"{case.case_id}.json").write_text(json.dumps(res, indent=2, sort_keys=True))
        for name, chk in res["checks"].items():
            flag = "PASS" if chk["passed"] else "FAIL"
            print(f"   {flag} {name}: {chk['value']:.6g} (threshold {chk['threshold']:.6g})")
        print(f"   overall: {'PASS' if res['passed'] else 'FAIL'}", flush=True)

    ledger["passed"] = all(c.get("passed", False) for c in ledger["cases"].values())
    (out_dir / "ledger.json").write_text(json.dumps(ledger, indent=2, sort_keys=True))
    print(f"[ledger] {out_dir / 'ledger.json'}  overall={'PASS' if ledger['passed'] else 'FAIL'}")
    return 0 if ledger["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())