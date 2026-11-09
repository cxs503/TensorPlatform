"""Reproducible benchmark driver: task specs, serial/parallel execution, anchors.

Everything the validation faces need is recorded per run so that the acceptance
numbers in the report can be re-derived from the JSON alone (audit chain):

* full per-generation history of GD / IGD / spacing / coverage / hypervolume,
* the convergence generation (first ``G`` from which GD stays <= threshold),
* the power-law (``GD = A * g^-k``) anchor fitted on an **early window** and
  extrapolated to the acceptance threshold — an out-of-sample prediction of the
  convergence generation,
* wall-clock timings of the serial versus the process-parallel suite (identical
  task list, identical seeds, bit-identical results).

Parallelism: ``fork``-based ``multiprocessing`` pool over independent
(problem, seed) tasks.  Tasks share nothing, so the speed-up is a clean
measurement of embarrassingly-parallel throughput (this is how a design-space
sweep would be executed on a cluster).
"""
from __future__ import annotations

import multiprocessing as mp
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from .nsga2 import NSGA2Config, converges_at, nsga2
from .problems import get_problem

__all__ = [
    "RunSpec",
    "run_spec",
    "run_suite",
    "powerlaw_anchor",
    "summarise",
    "HISTORY_KEYS",
]

HISTORY_KEYS = ("gd", "igd", "spacing", "coverage", "hypervolume", "front_share")


@dataclass
class RunSpec:
    problem: str
    seed: int
    n_var: int = 30
    pop_size: int = 100
    n_generations: int = 600
    analytic_points: int = 1000
    gd_threshold: float = 0.01
    spacing_threshold: float = 0.05

    def key(self) -> str:
        return f"{self.problem}-seed{self.seed}"


def _history_to_columns(history: Sequence[Dict[str, float]]) -> Dict[str, List[float]]:
    cols: Dict[str, List[float]] = {"generation": [int(h.get("generation", i))
                                                   for i, h in enumerate(history)]}
    for key in HISTORY_KEYS:
        cols[key] = [float(h.get(key, np.nan)) for h in history]
    return cols


def powerlaw_anchor(
    generation: Sequence[float],
    gd: Sequence[float],
    threshold: float = 0.01,
    window: tuple = (0.12, 0.55),
    min_floor: float = 1e-6,
) -> Dict[str, float]:
    """Fit ``GD(g) = A * g^(-k)`` on an early generation window and extrapolate.

    The fit window ends well before the crossing generation, so the predicted
    crossing ``g* = (A/threshold)**(1/k)`` is an out-of-sample prediction.  It
    is the second, implementation-independent anchor of the convergence face.
    """
    g = np.asarray(generation, dtype=float)
    y = np.asarray(gd, dtype=float)
    g_max = float(np.nanmax(g)) if g.size else 0.0
    lo = max(1.0, window[0] * g_max)
    hi = max(lo + 1.0, window[1] * g_max)
    mask = (g >= lo) & (g <= hi) & np.isfinite(y) & (y > min_floor)
    if mask.sum() < 8:
        return {"A": float("nan"), "k": float("nan"), "r2": float("nan"),
                "predicted_generation": float("nan"),
                "window": [lo, hi], "n_points": int(mask.sum())}
    x = np.log(g[mask])
    z = np.log(y[mask])
    k_fit, log_a = np.polyfit(x, z, 1)
    pred = log_a + k_fit * x
    ss_res = float(np.sum((z - pred) ** 2))
    ss_tot = float(np.sum((z - z.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    k = float(-k_fit)
    A = float(np.exp(log_a))
    if not np.isfinite(k) or k <= 0:
        g_star = float("nan")
    else:
        g_star = float((A / threshold) ** (1.0 / k))
    return {"A": A, "k": k, "r2": float(r2), "predicted_generation": g_star,
            "window": [lo, hi], "n_points": int(mask.sum())}


def run_spec(spec: RunSpec, with_history: bool = True) -> Dict[str, object]:
    """Execute one (problem, seed) NSGA-II run and return a JSON-ready record."""
    problem = get_problem(spec.problem, n_var=spec.n_var)
    analytic = problem.analytic_front(spec.analytic_points)
    cfg = NSGA2Config(
        pop_size=spec.pop_size,
        n_generations=spec.n_generations,
        seed=spec.seed,
        analytic_front=analytic,
    )
    t0 = time.perf_counter()
    result = nsga2(problem, cfg)
    wall = time.perf_counter() - t0

    history = result.history
    cols = _history_to_columns(history)
    cross_gd = converges_at(history, "gd", spec.gd_threshold)
    cross_spacing = converges_at(history, "spacing", spec.spacing_threshold)
    anchor = powerlaw_anchor(cols["generation"], cols["gd"], spec.gd_threshold,
                             window=(0.12, 0.45))
    final = result.final_metrics()

    record: Dict[str, object] = {
        "spec": asdict(spec),
        "key": spec.key(),
        "n_evaluations": result.n_evaluations,
        "wall_time_s": wall,
        "final_metrics": {k: float(v) for k, v in final.items()},
        "convergence": {
            "gd_threshold": spec.gd_threshold,
            "gd_generation": cross_gd,
            "spacing_threshold": spec.spacing_threshold,
            "spacing_generation": cross_spacing,
        },
        "powerlaw_anchor": anchor,
        "front_size": int(result.pareto_front().shape[0]),
        "mean_g_front": float(np.mean(
            1.0 + 9.0 * result.pareto_set()[:, 1:].sum(axis=1) / (spec.n_var - 1)
        )),
    }
    if with_history:
        record["history"] = cols
        record["nd_points"] = {
            "final_front": [[float(a), float(b)] for a, b in result.pareto_front()],
        }
    return record


def _worker(payload: Dict[str, object]) -> Dict[str, object]:
    spec = RunSpec(**payload["spec"])
    return run_spec(spec, with_history=bool(payload.get("with_history", True)))


def _mp_context():
    try:
        return mp.get_context("fork")
    except ValueError:  # pragma: no cover - non-POSIX platforms
        return mp.get_context("spawn")


def run_suite(
    specs: Iterable[RunSpec],
    workers: int = 1,
    with_history: bool = True,
) -> Dict[str, object]:
    """Run all specs serially (``workers<=1``) or on a process pool.

    Returns ``{"records": [...], "wall_time_s": float, "workers": int}``.
    """
    specs = list(specs)
    payloads = [{"spec": asdict(s), "with_history": with_history} for s in specs]
    t0 = time.perf_counter()
    if workers is None or workers <= 1 or len(specs) <= 1:
        records = [_worker(p) for p in payloads]
    else:
        ctx = _mp_context()
        with ctx.Pool(processes=min(workers, len(specs))) as pool:
            records = pool.map(_worker, payloads)
    wall = time.perf_counter() - t0
    records.sort(key=lambda r: r["key"])
    return {"records": records, "wall_time_s": wall, "workers": int(workers or 1)}


def summarise(records: Sequence[Dict[str, object]]) -> Dict[str, Dict[str, float]]:
    """Aggregate per-problem statistics (mean / median / min / max over seeds)."""
    by_problem: Dict[str, List[Dict[str, object]]] = {}
    for rec in records:
        by_problem.setdefault(rec["spec"]["problem"], []).append(rec)

    out: Dict[str, Dict[str, float]] = {}
    for problem, recs in sorted(by_problem.items()):
        gd = np.array([r["final_metrics"]["gd"] for r in recs], dtype=float)
        igd = np.array([r["final_metrics"]["igd"] for r in recs], dtype=float)
        sp = np.array([r["final_metrics"]["spacing"] for r in recs], dtype=float)
        hv = np.array([r["final_metrics"].get("hypervolume", np.nan) for r in recs],
                      dtype=float)
        cov = np.array([r["final_metrics"]["coverage"] for r in recs], dtype=float)
        gens = [r["convergence"]["gd_generation"] for r in recs]
        gens_ok = [g for g in gens if g is not None]
        anchors = [r["powerlaw_anchor"]["predicted_generation"] for r in recs]
        anchors_ok = [a for a in anchors if a is not None and np.isfinite(a)]
        out[problem] = {
            "n_seeds": len(recs),
            "gd_mean": float(gd.mean()), "gd_median": float(np.median(gd)),
            "gd_max": float(gd.max()), "gd_min": float(gd.min()),
            "igd_mean": float(igd.mean()), "igd_median": float(np.median(igd)),
            "igd_min": float(igd.min()), "igd_max": float(igd.max()),
            "spacing_mean": float(sp.mean()), "spacing_max": float(sp.max()),
            "spacing_min": float(sp.min()),
            "spacing_median": float(np.median(sp)),
            "hypervolume_median": float(np.nanmedian(hv)),
            "coverage_mean": float(cov.mean()),
            "gd_converged_seeds": len(gens_ok),
            "gd_generation_median": float(np.median(gens_ok)) if gens_ok else float("nan"),
            "gd_generation_values": gens,
            "powerlaw_generation_median": (float(np.median(anchors_ok))
                                           if anchors_ok else float("nan")),
            "powerlaw_generation_values": [float(a) if a is not None else None
                                           for a in anchors],
            "powerlaw_k_mean": float(np.mean([r["powerlaw_anchor"]["k"] for r in recs])),
            "powerlaw_r2_mean": float(np.mean([r["powerlaw_anchor"]["r2"] for r in recs])),
            "wall_time_s_mean": float(np.mean([r["wall_time_s"] for r in recs])),
        }
    return out