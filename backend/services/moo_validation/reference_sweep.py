"""Check-pointed sweep driver for the independent-implementation anchor.

Why this exists
---------------
Locating the reference implementation's convergence generation needs many
reference runs (the reference exposes only a final hypervolume history, so its
``GD(G)`` must be sampled by re-running with different generation budgets).
A full bisection for 15 (problem, seed) tasks is ~1 h of CPU, which does not fit
in a single foreground shell call, so the sweep is **restartable**: every
evaluation is written to a JSON state file and the driver resumes from it.

Determinism makes this valid: the reference implementation is a pure function of
``(problem, seed, n_generations)``, and a run with a larger budget reproduces the
same trajectory prefix, so sampling ``GD(G)`` at arbitrary ``G`` is legitimate.

Search policy per task
----------------------
1. coarse ladder ``150, 300, 450, 600`` until ``GD <= threshold`` is observed;
2. bisection on the bracket ``(G_last_above, G_first_below]`` down to 20 %,
   then log-log interpolation between the bracket ends (see :func:`interpolate_crossing`);
3. plateau confirmation at ``G*+10`` (the threshold must still hold 10 generations later).

Usage::

    python -m services.moo_validation.reference_sweep --max-seconds 90 \
        --state ../artifacts/.../reference_sweep_state.json --rounds 2
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .problems import get_problem
from .reference_impl import reference_gd_at

__all__ = ["main", "next_request", "interpolate_crossing", "load_state", "save_state"]

PROBLEMS = ("ZDT1", "ZDT2", "ZDT3")
LADDER = (150, 300, 450, 600)
SECONDS_PER_GENERATION = 0.17  # conservative wall-clock rate on the harness CPU
PLATEAU_OFFSETS = (10,)


def load_state(path: Path, seeds: int, n_var: int, pop_size: int,
               threshold: float) -> Dict[str, object]:
    if path.is_file():
        state = json.loads(path.read_text())
        state["config"].update({"seeds": seeds, "n_var": n_var,
                                "pop_size": pop_size, "threshold": threshold})
        return state
    tasks = {}
    for problem in PROBLEMS:
        for seed in range(seeds):
            tasks[f"{problem}|{seed}"] = {
                "problem": problem, "seed": seed,
                "evaluations": {}, "converged_at": None, "plateau_ok": None,
            }
    return {"config": {"seeds": seeds, "n_var": n_var, "pop_size": pop_size,
                       "threshold": threshold, "ladder": list(LADDER)},
            "tasks": tasks}


def save_state(path: Path, state: Dict[str, object]) -> None:
    path.write_text(json.dumps(state, indent=1))


def next_request(task: Dict[str, object], threshold: float,
                 max_generation: int) -> Optional[int]:
    """Decide the next generation budget to evaluate for one task.

    Sets ``task['finished']``/``task['converged_at']``/``task['plateau_ok']``
    when the search for that task is complete and returns ``None``.
    """
    if task.get("finished"):
        return None
    evals = {int(k): v for k, v in task["evaluations"].items()}
    below = sorted(g for g, m in evals.items() if m["gd"] <= threshold)
    above = sorted(g for g, m in evals.items() if m["gd"] > threshold)

    if not below:
        # coarse ladder until the threshold is crossed at least once
        for g in LADDER:
            if g not in evals and g <= max_generation:
                return g
        task.update({"finished": True, "converged_at": None,
                     "plateau_ok": False, "exhausted": True})
        return None

    lo = max(above) if above else 1
    hi = below[0]
    if hi - lo > max(5, 0.20 * hi):
        probe = int(round((lo + hi) / 2))
        if probe in evals:
            probe += 1
        if probe < hi and probe <= max_generation:
            return probe

    # refinement done -> plateau confirmation (threshold must hold afterwards)
    for off in PLATEAU_OFFSETS:
        g = hi + off
        if g not in evals and g <= max_generation:
            return g
    plateau = all(evals.get(hi + off, {"gd": float("inf")})["gd"] <= threshold
                  for off in PLATEAU_OFFSETS if (hi + off) in evals)
    task["converged_at"] = int(hi)
    task["plateau_ok"] = bool(plateau)
    task["finished"] = True
    return None


def interpolate_crossing(task: Dict[str, object], threshold: float) -> Optional[float]:
    """Log-log interpolation of the crossing generation inside the bracket.

    ``GD`` decays close to a power law locally, so linear interpolation of
    ``log GD`` versus ``log G`` between the bracketing budgets is markedly more
    accurate than the bracket midpoint and needs no extra reference runs.
    """
    evals = {int(k): v["gd"] for k, v in task["evaluations"].items()}
    below = sorted(g for g, v in evals.items() if v <= threshold and np.isfinite(v))
    above = sorted(g for g, v in evals.items() if v > threshold and np.isfinite(v))
    if not below:
        return None
    hi = below[0]
    if not above:
        return float(hi)
    lo = max(above)
    if lo >= hi:
        return float(hi)
    y0, y1 = np.log(max(evals[lo], 1e-12)), np.log(max(evals[hi], 1e-12))
    x0, x1 = np.log(lo), np.log(hi)
    yt = np.log(threshold)
    if abs(y1 - y0) < 1e-12:
        return float(hi)
    t = (yt - y0) / (y1 - y0)
    t = float(min(max(t, 0.0), 1.0))
    return float(np.exp(x0 + t * (x1 - x0)))


def _evaluate(payload: Dict[str, object]) -> Dict[str, object]:
    problem = get_problem(payload["problem"], n_var=int(payload["n_var"]))
    analytic = problem.analytic_front(1000)
    metrics = reference_gd_at(problem, analytic, int(payload["generation"]),
                              int(payload["pop_size"]), int(payload["seed"]))
    return {"key": payload["key"], "generation": int(payload["generation"]),
            "metrics": metrics}


def finalise(task: Dict[str, object], threshold: float) -> None:
    evals = {int(k): v for k, v in task["evaluations"].items()}
    below = sorted(g for g, m in evals.items() if m["gd"] <= threshold)
    if not below:
        task["finished"] = True
        task["converged_at"] = None
        task["plateau_ok"] = False
        return
    g_star = below[0]
    plateau = all(evals.get(g_star + off, {}).get("gd", np.inf) <= threshold
                  for off in PLATEAU_OFFSETS
                  if (g_star + off) in evals)
    task["converged_at"] = g_star if plateau else None
    task["plateau_ok"] = bool(plateau)
    task["finished"] = task["finished"] or bool(plateau)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    parser.add_argument("--max-seconds", type=float, default=90.0)
    parser.add_argument("--workers", type=int, default=15)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--pop-size", type=int, default=100)
    parser.add_argument("--n-var", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=0.01)
    args = parser.parse_args(argv)

    state_path = Path(args.state).resolve()
    state = load_state(state_path, args.seeds, args.n_var, args.pop_size,
                       args.threshold)
    t_start = time.perf_counter()
    max_generation = int((args.max_seconds * 0.92) / SECONDS_PER_GENERATION)

    try:
        ctx = mp.get_context("fork")
    except ValueError:  # pragma: no cover
        ctx = mp.get_context("spawn")

    rounds = 0
    while time.perf_counter() - t_start < args.max_seconds:
        requests = []
        for key, task in state["tasks"].items():
            if task.get("finished"):
                continue
            gen = next_request(task, args.threshold, max_generation)
            if gen is None:
                continue
            requests.append({"key": key, "problem": task["problem"],
                             "seed": task["seed"], "generation": gen,
                             "pop_size": args.pop_size, "n_var": args.n_var,
                             "est_seconds": gen * SECONDS_PER_GENERATION})
        if not requests:
            break
        # drop the most expensive requests if they would overrun the budget
        remaining = args.max_seconds - (time.perf_counter() - t_start)
        requests = [r for r in requests if r["est_seconds"] <= max(remaining * 1.05, 20)]
        if not requests:
            break
        rounds += 1
        with ctx.Pool(processes=min(args.workers, len(requests))) as pool:
            for out in pool.map(_evaluate, requests):
                task = state["tasks"][out["key"]]
                task["evaluations"][str(out["generation"])] = out["metrics"]
        save_state(state_path, state)
        print(f"[round {rounds}] evaluated {len(requests)} budgets "
              f"(gen {sorted({r['generation'] for r in requests})}) "
              f"elapsed={time.perf_counter() - t_start:.0f}s", flush=True)

    save_state(state_path, state)
    done = sum(1 for t in state["tasks"].values() if t.get("finished"))
    total_evals = sum(len(t["evaluations"]) for t in state["tasks"].values())
    print(f"[sweep] rounds={rounds} finished_tasks={done}/{len(state['tasks'])} "
          f"evaluations={total_evals} elapsed={time.perf_counter() - t_start:.0f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())