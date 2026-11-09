"""Adapter to the *second, independent* NSGA-II implementation already in tree.

Purpose: use it as an independent anchor for the convergence-generation
acceptance criterion.  It is a separate code base (``optimizer/ice_class/
nsga2.py``, Deb 2002, its own unit tests), so agreement between the two
implementations is a real cross-check rather than a self-comparison.

The module lives outside ``backend`` and its package ``__init__`` pulls in
domain-specific code, so it is loaded directly from the file to keep this
adapter dependency-free.

The reference implementation exposes only ``hv_history`` (no per-generation
objective snapshot) yet is fully deterministic given ``seed``.  Its
convergence generation is therefore located by a **generation-budget ladder
with bisection**: re-running with a larger ``n_generations`` reproduces exactly
the same trajectory prefix, so ``GD(G)`` can be evaluated at arbitrary ``G``
and the first ``G`` with ``GD(G) <= threshold`` found in ``O(log)`` runs.
"""
from __future__ import annotations

import importlib.util
import pathlib
from typing import Dict, List, Optional

import numpy as np

__all__ = ["reference_available", "reference_gd_at", "reference_converges_at"]

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_REF_PATH = _REPO_ROOT / "optimizer" / "ice_class" / "nsga2.py"


def _load_reference():
    if not _REF_PATH.is_file():
        raise FileNotFoundError(f"reference NSGA-II not found at {_REF_PATH}")
    spec = importlib.util.spec_from_file_location("_intree_ref_nsga2", _REF_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REF = None


def _ref():
    global _REF
    if _REF is None:
        _REF = _load_reference()
    return _REF


def reference_available() -> bool:
    return _REF_PATH.is_file()


def reference_gd_at(
    problem,
    analytic_front: np.ndarray,
    n_generations: int,
    pop_size: int = 100,
    seed: int = 0,
) -> Dict[str, float]:
    """Run the in-tree reference implementation and score its first front."""
    from .metrics import evaluate_front

    mod = _ref()
    opt = mod.NSGA2Optimizer(
        n_objectives=problem.n_obj,
        n_variables=problem.n_var,
        pop_size=pop_size,
        n_generations=n_generations,
        seed=seed,
    )

    def objective_fn(x):
        return problem.evaluate(x[None, :])[0], 0.0

    out = opt.run(objective_fn, problem.lower, problem.upper, verbose=False)
    front = np.asarray(out["pareto_objs"], dtype=float)
    metrics = evaluate_front(front, analytic_front, np.ones(problem.n_obj) * 1.5)
    metrics["generation"] = n_generations
    metrics["wall_time_s"] = float(out["wall_time_s"])
    return metrics


def reference_converges_at(
    problem,
    analytic_front: np.ndarray,
    threshold: float = 0.01,
    gen_budget: int = 400,
    pop_size: int = 100,
    seed: int = 0,
    patience: int = 3,
) -> Optional[int]:
    """Bisection over the generation budget (deterministic prefix property)."""
    if reference_gd_at(problem, analytic_front, gen_budget, pop_size, seed)["gd"] > threshold:
        return None

    lo, hi = 1, gen_budget
    while lo < hi:
        mid = (lo + hi) // 2
        gd = reference_gd_at(problem, analytic_front, mid, pop_size, seed)["gd"]
        if gd <= threshold:
            hi = mid
        else:
            lo = mid + 1
    # Confirm the plateau condition (GD stays below threshold afterwards).
    if patience > 1:
        check = min(lo + patience - 1, gen_budget)
        if reference_gd_at(problem, analytic_front, check, pop_size, seed)["gd"] > threshold:
            return None
    return int(lo)