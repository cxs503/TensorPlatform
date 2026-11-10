"""Generic, case-file-driven uncertainty-quantification benchmark runner.

The runner is *domain free*: everything physical (the black-box model, the
input laws, the acceptance thresholds) arrives through a JSON **case file**.
A case file names a callable by ``module:attribute`` (and optionally a file to
load it from, so a domain package does not have to be importable), states the
marginal law of every uncertain input, the PCE design, the Monte Carlo budget
and the acceptance thresholds.  The runner then

1. builds the non-intrusive PCE from ``(X, Y)`` samples,
2. repeats the same estimate by direct Monte Carlo at the stated budget,
3. scores the PCE against a *much larger* Monte Carlo reference (when given)
   and against a closed-form reference (when the case names one),
4. times both routes on the same calling interface and reports the
   acceleration in model evaluations **and** in wall clock,
5. sweeps the Monte Carlo budget and the PCE design so the acceleration and
   error curves are measured, not asserted, and
6. decides every acceptance criterion mechanically and writes the ledger that
   is the only source of the numbers quoted in the report.

Usage::

    python -m tools.pce.case_runner --case config/uq_cases/<name>.json \
        --out-dir artifacts/pce_m9833
    python -m tools.pce.case_runner --all --out-dir artifacts/pce_m9833

Nothing here knows what the response means -- compare the module docstring of
:mod:`tools.pce.kernel`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from backend.services.pce_uq.marginals import Marginal, to_physical
from backend.services.pce_uq.spec import resolve_callable

from .kernel import marginal_detail, pce_band, pce_fit, result_summary
from .sampling import physical_design, quadrature_design, unit_design

DEFAULT_CASE_DIR = Path("config/uq_cases")
CASE_GLOB = "p152w5_n07_*.json"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rel(a: float, b: float) -> float:
    return float(abs(a - b) / abs(b)) if b else float("inf")


def _git_sha() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:  # pragma: no cover - not a git checkout
        return "unknown"


def _stack() -> Dict[str, Any]:
    import scipy

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "machine": platform.machine(),
        "platform": platform.platform(),
    }


def _min_time(fn: Callable[[], Any], reps: int) -> tuple:
    """Run ``fn`` ``reps`` times, return ``(best_wall_seconds, last_result)``."""
    best = math.inf
    out = None
    for _ in range(max(1, int(reps))):
        t0 = time.perf_counter()
        out = fn()
        best = min(best, time.perf_counter() - t0)
    return float(best), out


def _eval_model(func: Callable[..., Any], X: np.ndarray, names: Sequence[str],
                output: Optional[str], call_style: str = "kwargs") -> np.ndarray:
    """Evaluate a black-box scalar model row by row (the honest cost unit)."""
    X = np.atleast_2d(np.asarray(X, dtype=float))
    out = np.empty(X.shape[0], dtype=float)
    style = str(call_style).lower()
    for i in range(X.shape[0]):
        if style == "array":
            val = func(np.array([float(v) for v in X[i]]))
        elif style == "kwargs":
            val = func(**{name: float(X[i, j]) for j, name in enumerate(names)})
        else:
            raise ValueError(f"unknown call_style {call_style!r}; expected kwargs/array")
        if output is not None:
            if hasattr(val, "as_dict"):
                val = val.as_dict()[output]
            elif isinstance(val, dict):
                val = val[output]
            else:
                val = getattr(val, output)
        out[i] = float(np.asarray(val, dtype=float).reshape(-1)[0])
    return out


def _summary(y: np.ndarray, probs: Sequence[float]) -> Dict[str, Any]:
    y = np.asarray(y, dtype=float)
    q = np.quantile(y, list(probs))
    return {
        "mean": float(np.mean(y)),
        "std": float(np.std(y, ddof=1)) if y.size > 1 else 0.0,
        "quantiles": {f"p{100 * p:g}": float(v) for p, v in zip(probs, q)},
        "band": float(q[-1] - q[0]),
        "n": int(y.size),
    }


def _model_call_cost(func: Callable[..., Any], nominal: Dict[str, float],
                     output: Optional[str], call_style: str = "kwargs",
                     reps: int = 300) -> float:
    """Seconds per black-box call (micro-benchmark, best of ``reps``)."""
    import numpy as _np

    if not nominal:
        return 0.0
    names = list(nominal)
    X = _np.array([[nominal[n] for n in names]], dtype=float)
    # warm-up (imports inside the model, caches of the derived coefficients)
    _eval_model(func, X, names, output, call_style)
    best = math.inf
    for _ in range(int(reps)):
        t0 = time.perf_counter()
        _eval_model(func, X, names, output, call_style)
        best = min(best, time.perf_counter() - t0)
    return float(best)


# --------------------------------------------------------------------------- #
# case spec
# --------------------------------------------------------------------------- #
def load_case(path: Path) -> Dict[str, Any]:
    path = Path(path)
    case = json.loads(path.read_text())
    for key in ("case_id", "model", "inputs", "design", "mc"):
        if key not in case:
            raise ValueError(f"{path}: case file is missing required key {key!r}")
    if not case["inputs"]:
        raise ValueError(f"{path}: case has no uncertain inputs")
    case["_path"] = str(path)
    case["_sha256"] = _sha256(path)
    return case


def _marginals(case: Dict[str, Any]) -> tuple:
    return tuple(
        Marginal.from_spec(dict(spec)) for spec in case["inputs"]
    )


def _case_names(case: Dict[str, Any]) -> List[str]:
    return [str(spec["name"]) for spec in case["inputs"]]


def _prob_pair(case: Dict[str, Any]) -> List[float]:
    band = case.get("band", {})
    probs = band.get("probs", [0.025, 0.5, 0.975])
    return [float(p) for p in probs]


# --------------------------------------------------------------------------- #
# the two routes
# --------------------------------------------------------------------------- #
def run_pce_route(
    case: Dict[str, Any],
    func: Callable[..., Any],
    marginals: Sequence[Marginal],
    names: Sequence[str],
    reps: int = 3,
) -> Dict[str, Any]:
    """Build the PCE, its moments and its confidence band; time the pipeline."""
    design = dict(case["design"])
    mode = str(design.get("mode", "regression")).lower()
    band_cfg = dict(case.get("band", {}))
    probs = _prob_pair(case)
    n_surrogate = int(band_cfg.get("n_surrogate", 16384))
    band_seed = int(band_cfg.get("seed", 3))
    output = case["model"].get("output")
    call_style = str(case["model"].get("call_style", "kwargs"))

    def pipeline() -> Dict[str, Any]:
        if mode == "projection":
            quad = dict(design.get("quadrature", {}))
            grid = quadrature_design(
                case["inputs"],
                kind=str(quad.get("kind", "tensor")),
                n_per_dim=int(quad.get("n_per_dim", 3)),
                level=int(quad.get("level", 2)),
            )
            X = grid["X"]
            weights = grid["weights"]
            design_info = {
                "mode": "projection",
                "kind": grid["kind"],
                "level": grid["level"],
                "n_points": grid["n_points"],
                "n_negative_weights": grid["n_negative_weights"],
                "weight_sum": grid["weight_sum"],
            }
        else:
            n_samples = int(design.get("n_samples", 0))
            if n_samples <= 0:
                raise ValueError("regression design needs a positive n_samples")
            X, _ = physical_design(
                n_samples, marginals,
                sampler=str(design.get("sampler", "lhs")),
                seed=int(design.get("seed", 0)),
            )
            weights = None
            design_info = {
                "mode": "regression",
                "n_samples": n_samples,
                "sampler": str(design.get("sampler", "lhs")),
                "seed": int(design.get("seed", 0)),
            }

        t0 = time.perf_counter()
        Y = _eval_model(func, X, names, output, call_style)
        fit = pce_fit(
            X, Y, int(design.get("degree", 2)),
            marginals=case["inputs"],
            weights=None if weights is None else np.asarray(weights),
            q_norm=float(design.get("q_norm", 1.0)),
        )
        band = pce_band(fit, probs=probs, n_surrogate=n_surrogate, seed=band_seed)
        wall = time.perf_counter() - t0
        return {
            "fit": fit,
            "band": band,
            "design": design_info,
            "n_model_evaluations": int(X.shape[0]),
            "wall_seconds": float(wall),
            "samples": X,
            "responses": Y,
        }

    wall, res = _min_time(pipeline, reps)
    fit = res["fit"]
    summary = result_summary(fit)
    summary.update(
        {
            "design": res["design"],
            "n_model_evaluations": res["n_model_evaluations"],
            "wall_seconds_min": wall,
            "band": res["band"],
        }
    )
    return summary


def run_mc_route(
    case: Dict[str, Any],
    func: Callable[..., Any],
    marginals: Sequence[Marginal],
    names: Sequence[str],
    budget: Optional[int] = None,
    sampler: Optional[str] = None,
    seed: Optional[int] = None,
    reps: int = 3,
    probs: Optional[Sequence[float]] = None,
    keep_samples: int = 0,
) -> Dict[str, Any]:
    """Direct Monte Carlo / LHS baseline at the stated budget (timed)."""
    mc = dict(case["mc"])
    n = int(mc["n"] if budget is None else budget)
    sampler = str(sampler or mc.get("sampler", "lhs"))
    seed = int(mc.get("seed", 0) if seed is None else seed)
    output = case["model"].get("output")
    call_style = str(case["model"].get("call_style", "kwargs"))
    probs = list(probs or _prob_pair(case))

    def pipeline() -> Dict[str, Any]:
        U = unit_design(n, len(marginals), sampler=sampler, seed=seed)
        X = to_physical(marginals, U)
        t0 = time.perf_counter()
        Y = _eval_model(func, X, names, output, call_style)
        wall = time.perf_counter() - t0
        return {"Y": Y, "wall_seconds": float(wall), "n": int(n)}

    wall, res = _min_time(pipeline, reps)
    stats = _summary(res["Y"], probs)
    stats.update(
        {
            "n_model_evaluations": int(res["n"]),
            "wall_seconds_min": wall,
            "sampler": sampler,
            "seed": seed,
        }
    )
    if keep_samples > 0:
        # a deterministic prefix of the response sample, for plotting only
        stats["response_sample"] = [float(v) for v in res["Y"][: int(keep_samples)]]
    return stats


# --------------------------------------------------------------------------- #
# sweep (measure the acceleration / error curves instead of asserting them)
# --------------------------------------------------------------------------- #
def run_sweep(
    case: Dict[str, Any],
    func: Callable[..., Any],
    marginals: Sequence[Marginal],
    names: Sequence[str],
    reference: Dict[str, Any],
    probs: Sequence[float],
) -> Dict[str, Any]:
    """MC budget ladder and PCE design ladder, scored against the reference."""
    sweep_cfg = dict(case.get("sweep", {}))
    mc_budgets = [int(v) for v in sweep_cfg.get("mc_budgets", [64, 128, 256, 512, 1024, 2048, 4096, 10000])]
    pce_ladder = sweep_cfg.get(
        "pce_designs",
        [
            {"degree": 1, "n_samples": 32},
            {"degree": 2, "n_samples": 48},
            {"degree": 2, "n_samples": 64},
            {"degree": 2, "n_samples": 97},
            {"degree": 3, "n_samples": 160},
        ],
    )
    ref_mean = reference["mean"]
    ref_std = reference["std"]
    ref_band = reference["band"]
    ref_sobol = reference.get("sobol")

    mc_rows: List[Dict[str, Any]] = []
    for budget in mc_budgets:
        stats = run_mc_route(case, func, marginals, names, budget=budget, reps=1, probs=probs)
        mc_rows.append(
            {
                "n_model_evaluations": stats["n_model_evaluations"],
                "mean": stats["mean"],
                "std": stats["std"],
                "band": stats["band"],
                "rel_err_mean": _rel(stats["mean"], ref_mean),
                "rel_err_std": _rel(stats["std"], ref_std),
                "rel_err_band": _rel(stats["band"], ref_band),
                "wall_seconds": stats["wall_seconds_min"],
            }
        )

    pce_rows: List[Dict[str, Any]] = []
    for entry in pce_ladder:
        local = json.loads(json.dumps({k: v for k, v in case.items() if not k.startswith("_")}))
        local["design"] = {
            "mode": "regression",
            "degree": int(entry["degree"]),
            "n_samples": int(entry["n_samples"]),
            "sampler": str(entry.get("sampler", case["design"].get("sampler", "lhs"))),
            "seed": int(entry.get("seed", case["design"].get("seed", 0))),
        }
        try:
            pce = run_pce_route(local, func, marginals, names, reps=1)
        except ValueError as exc:  # under-determined design
            pce_rows.append({"degree": int(entry["degree"]),
                             "n_samples": int(entry["n_samples"]),
                             "error": str(exc)})
            continue
        row = {
            "degree": int(entry["degree"]),
            "n_samples": int(entry["n_samples"]),
            "n_terms": pce["n_terms"],
            "n_model_evaluations": pce["n_model_evaluations"],
            "mean": pce["mean"],
            "std": pce["std"],
            "band": pce["band"]["width"],
            "rel_err_mean": _rel(pce["mean"], ref_mean),
            "rel_err_std": _rel(pce["std"], ref_std),
            "rel_err_band": _rel(pce["band"]["width"], ref_band),
            "loo_relative_error": pce["loo_relative_error"],
            "wall_seconds": pce["wall_seconds_min"],
        }
        if ref_sobol:
            row["max_abs_err_sobol"] = max(
                [abs(pce["sobol_first"][k] - v) for k, v in ref_sobol["first"].items()]
                + [abs(pce["sobol_total"][k] - v) for k, v in ref_sobol["total"].items()]
            )
        pce_rows.append(row)

    return {"mc_ladder": mc_rows, "pce_ladder": pce_rows}


# --------------------------------------------------------------------------- #
# main entry
# --------------------------------------------------------------------------- #
def run_case(
    case: Dict[str, Any],
    reps: int = 3,
    with_sweep: bool = True,
) -> Dict[str, Any]:
    model_spec = dict(case["model"])
    func = resolve_callable(model_spec["path"], model_spec.get("file"))
    marginals = _marginals(case)
    names = _case_names(case)
    probs = _prob_pair(case)

    # -- reference: large Monte Carlo, optionally a closed-form anchor ------
    mc_cfg = dict(case["mc"])
    ref_n = int(mc_cfg.get("reference_n", 0) or 0)
    reference: Dict[str, Any] = {}
    if ref_n > 0:
        ref_run = run_mc_route(
            case, func, marginals, names,
            budget=ref_n,
            sampler=str(mc_cfg.get("reference_sampler", "qmc")),
            seed=int(mc_cfg.get("reference_seed", 12345)),
            reps=1,
            probs=probs,
            keep_samples=2000,
        )
        reference = {
            "source": "monte_carlo",
            "n": ref_run["n_model_evaluations"],
            "sampler": ref_run["sampler"],
            "mean": ref_run["mean"],
            "std": ref_run["std"],
            "band": ref_run["band"],
            "quantiles": ref_run["quantiles"],
            "response_sample": ref_run.get("response_sample", []),
        }

    analytic = case.get("reference")
    sobol_ref = None
    if analytic:
        ref_func = resolve_callable(analytic["path"], analytic.get("file"))
        raw = ref_func()
        first = raw.get("first_order")
        total = raw.get("total_order")
        names_ref = list(raw.get("names") or names)
        sobol_ref = {
            "source": str(analytic.get("source", "closed_form")),
            "variance": float(raw["variance"]) if "variance" in raw else None,
            "first": {str(n): float(v) for n, v in zip(names_ref, first)},
            "total": {str(n): float(v) for n, v in zip(names_ref, total)},
            "second": {str(k): float(v) for k, v in (analytic.get("second_order") or {}).items()},
            "second_source": analytic.get("second_order_source"),
        }
        reference.setdefault("source", "closed_form")
        reference["sobol"] = sobol_ref
        if sobol_ref["variance"] is not None:
            reference["variance"] = sobol_ref["variance"]

    # -- the two routes -----------------------------------------------------
    pce = run_pce_route(case, func, marginals, names, reps=reps)
    mc = run_mc_route(case, func, marginals, names, reps=reps, probs=probs)

    # -- error bookkeeping --------------------------------------------------
    ref_mean = reference["mean"]
    ref_std = reference["std"]
    ref_band = reference["band"]
    errors = {
        "vs_reference": {
            "rel_err_mean": _rel(pce["mean"], ref_mean),
            "rel_err_std": _rel(pce["std"], ref_std),
            "rel_err_band": _rel(pce["band"]["width"], ref_band),
        },
        "vs_mc_budget": {
            "rel_err_mean": _rel(pce["mean"], mc["mean"]),
            "rel_err_std": _rel(pce["std"], mc["std"]),
            "rel_err_band": _rel(pce["band"]["width"], mc["band"]),
        },
        "mc_vs_reference": {
            "rel_err_mean": _rel(mc["mean"], ref_mean),
            "rel_err_std": _rel(mc["std"], ref_std),
            "rel_err_band": _rel(mc["band"], ref_band),
        },
    }
    if sobol_ref:
        names_t = list(pce["names"])
        first_err = {
            n: abs(pce["sobol_first"][n] - sobol_ref["first"][n]) for n in names_t
        }
        total_err = {
            n: abs(pce["sobol_total"][n] - sobol_ref["total"][n]) for n in names_t
        }
        rel_first = {
            n: (first_err[n] / abs(sobol_ref["first"][n]) if sobol_ref["first"][n] else float("nan"))
            for n in names_t
        }
        rel_total = {
            n: (total_err[n] / abs(sobol_ref["total"][n]) if sobol_ref["total"][n] else float("nan"))
            for n in names_t
        }
        errors["sobol"] = {
            "abs_err_first": first_err,
            "abs_err_total": total_err,
            "rel_err_first": rel_first,
            "rel_err_total": rel_total,
            "max_abs_err_first": max(first_err.values()),
            "max_abs_err_total": max(total_err.values()),
            "max_rel_err_first": max(
                [v for v in rel_first.values() if v == v] or [0.0]
            ),
            "max_rel_err_total": max(
                [v for v in rel_total.values() if v == v] or [0.0]
            ),
        }
        if sobol_ref["variance"]:
            errors["sobol"]["rel_err_variance"] = _rel(pce["variance"], sobol_ref["variance"])

    # -- acceleration -------------------------------------------------------
    speedup_evals = float(mc["n_model_evaluations"]) / float(pce["n_model_evaluations"])
    speedup_wall = float(mc["wall_seconds_min"]) / float(pce["wall_seconds_min"])
    # pure model-call cost (micro-benchmark, no sampling / statistics inside)
    nominal = {m.name: float(m.moments()[0]) for m in marginals}
    t_model = _model_call_cost(func, nominal, case["model"].get("output"),
                               str(case["model"].get("call_style", "kwargs")))
    overhead = float(max(pce["wall_seconds_min"] - pce["n_model_evaluations"] * t_model, 0.0))
    target = float(case.get("acceleration_target", 100.0))
    # wall-clock speed-up as a function of the per-evaluation cost of the black
    # box:  n_mc * t / (n_pce * t + overhead) >= target
    denom = mc["n_model_evaluations"] / target - pce["n_model_evaluations"]
    breakeven_t = float(overhead / denom) if denom > 0 else float("inf")
    acceleration = {
        "n_model_evaluations_pce": pce["n_model_evaluations"],
        "n_model_evaluations_mc": mc["n_model_evaluations"],
        "speedup_model_evaluations": speedup_evals,
        "wall_seconds_pce": pce["wall_seconds_min"],
        "wall_seconds_mc": mc["wall_seconds_min"],
        "speedup_wall_clock": speedup_wall,
        "seconds_per_model_evaluation": t_model,
        "pce_fixed_overhead_seconds": overhead,
        "target_wall_clock_speedup": target,
        "breakeven_seconds_per_evaluation": breakeven_t,
        "projected_speedup_at_1s_per_evaluation": float(
            mc["n_model_evaluations"] * 1.0 / (pce["n_model_evaluations"] * 1.0 + overhead)
        ),
        "note": (
            "speedup_model_evaluations is the scale-invariant acceleration (the black box is the "
            "expensive object measured in the same way for both routes). speedup_wall_clock is "
            "measured end-to-end on this platform's very cheap reference model, where Python "
            "overhead dominates; for a model costing more than breakeven_seconds_per_evaluation "
            "the measured wall-clock speed-up exceeds target_wall_clock_speedup."
        ),
    }

    # -- acceptance ---------------------------------------------------------
    thr = dict(case.get("thresholds", {}))
    tol_moment = float(thr.get("moment_rel_tol", 0.03))
    tol_sobol = float(thr.get("sobol_rel_tol", 0.05))
    min_speedup = float(thr.get("min_eval_speedup", 100.0))
    min_r2 = float(thr.get("min_r2", 0.99))

    criteria: Dict[str, Any] = {}

    def add(key: str, value: float, threshold: float, mode: str, note: str = "") -> None:
        ok = value <= threshold if mode == "max" else value >= threshold
        criteria[key] = {
            "value": float(value),
            "threshold": float(threshold),
            "mode": mode,
            "pass": bool(ok),
            "note": note,
        }

    add("moment_rel_err_std_vs_reference", errors["vs_reference"]["rel_err_std"], tol_moment, "max")
    add("moment_rel_err_mean_vs_reference", errors["vs_reference"]["rel_err_mean"], tol_moment, "max")
    add("band_rel_err_vs_reference", errors["vs_reference"]["rel_err_band"], tol_moment, "max")
    add("moment_rel_err_std_vs_mc_budget", errors["vs_mc_budget"]["rel_err_std"], tol_moment, "max")
    if "min_eval_speedup" in thr:
        add("speedup_model_evaluations", speedup_evals, min_speedup, "min")
    add("pce_r2", float(pce["r2"]) if pce["r2"] == pce["r2"] else 0.0, min_r2, "min")
    if "sobol" in errors:
        add("sobol_max_abs_err_first", errors["sobol"]["max_abs_err_first"], tol_sobol, "max")
        add("sobol_max_abs_err_total", errors["sobol"]["max_abs_err_total"], tol_sobol, "max")
        add(
            "sobol_max_rel_err_total",
            errors["sobol"]["max_rel_err_total"],
            tol_sobol,
            "max",
        )

    ledger: Dict[str, Any] = {
        "case_id": case["case_id"],
        "title": case.get("title", ""),
        "case_path": case["_path"],
        "case_sha256": case["_sha256"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_sha": _git_sha(),
        "stack": _stack(),
        "model": {
            "path": model_spec["path"],
            "file": model_spec.get("file"),
            "output": model_spec.get("output"),
            "call_style": str(model_spec.get("call_style", "kwargs")),
        },
        "inputs": [marginal_detail(m) for m in marginals],
        "band_probs": probs,
        "pce": pce,
        "monte_carlo": mc,
        "reference": reference,
        "errors": errors,
        "acceleration": acceleration,
        "criteria": criteria,
        "all_criteria_pass": bool(all(c["pass"] for c in criteria.values())),
    }

    if with_sweep:
        ledger["sweep"] = run_sweep(case, func, marginals, names, reference, probs)

    return ledger


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", action="append", default=[], help="case JSON path (repeatable)")
    ap.add_argument("--all", action="store_true", help="run every case in the default case dir")
    ap.add_argument("--case-dir", default=str(DEFAULT_CASE_DIR))
    ap.add_argument("--out-dir", default="artifacts/pce_m9833")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--no-sweep", action="store_true")
    args = ap.parse_args(argv)

    paths = [Path(p) for p in args.case]
    if args.all:
        paths += sorted(Path(args.case_dir).glob(CASE_GLOB))
    if not paths:
        raise SystemExit("no case selected; pass --case or --all")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index: Dict[str, Any] = {"cases": [], "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    for path in paths:
        case = load_case(path)
        print(f"[case] {case['case_id']} <- {path}", flush=True)
        ledger = run_case(case, reps=args.reps, with_sweep=not args.no_sweep)
        out = out_dir / f"{case['case_id']}.json"
        out.write_text(json.dumps(ledger, indent=2, sort_keys=True))
        index["cases"].append(
            {
                "case_id": case["case_id"],
                "ledger": str(out),
                "all_criteria_pass": ledger["all_criteria_pass"],
                "title": case.get("title", ""),
            }
        )
        print(
            f"[case] {case['case_id']}: all_criteria_pass={ledger['all_criteria_pass']} "
            f"speedup_evals={ledger['acceleration']['speedup_model_evaluations']:.1f} "
            f"-> {out}",
            flush=True,
        )
    (out_dir / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True))
    return 0 if all(c["all_criteria_pass"] for c in index["cases"]) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())