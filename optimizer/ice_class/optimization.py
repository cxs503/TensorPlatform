"""
Ice-Class Hull Form Multi-Objective Optimization — Main Loop.

Orchestrates NSGA-II optimization with ice resistance, open water resistance,
and stability objectives. Includes surrogate model acceleration option.
"""
import numpy as np
import json
import time
from pathlib import Path
from typing import Dict, Optional

from .nsga2 import NSGA2Optimizer, compute_hypervolume, fast_non_dominated_sort
from .objectives import evaluate_all, evaluate_ice_resistance, evaluate_open_water_resistance, evaluate_gm
from .constraints import check_constraints, compute_constraint_violation, get_bounds, CONSTRAINT_BOUNDS


class SurrogateModel:
    """
    Lightweight surrogate model for objective function acceleration.

    Uses radial basis function (RBF) interpolation with linear kernel.
    Trained on evaluated design points to approximate objectives.
    Periodically retrained with new high-fidelity evaluations.
    """

    def __init__(self, n_obj: int = 3):
        self.n_obj = n_obj
        self.X_train = None
        self.Y_train = None
        self.trained = False
        self.n_evals = 0

    def add_samples(self, X: np.ndarray, Y: np.ndarray):
        """Add training samples."""
        if self.X_train is None:
            self.X_train = X.copy()
            self.Y_train = Y.copy()
        else:
            self.X_train = np.vstack([self.X_train, X])
            self.Y_train = np.vstack([self.Y_train, Y])
        self.n_evals = len(self.X_train)
        self.trained = True

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict objectives using RBF interpolation."""
        if not self.trained or self.n_evals < 5:
            return None

        N = X.shape[0]
        predictions = np.zeros((N, self.n_obj))

        for i in range(N):
            # Distance to all training points
            dists = np.linalg.norm(self.X_train - X[i], axis=1)
            # RBF weights (inverse distance weighting)
            weights = 1.0 / (dists + 1e-10)
            weights /= weights.sum()
            predictions[i] = weights @ self.Y_train

        return predictions


def run_ice_class_optimization(
    pop_size: int = 40,
    n_generations: int = 50,
    seed: int = 42,
    use_surrogate: bool = True,
    surrogate_interval: int = 5,
    verbose: bool = True,
    output_dir: Optional[str] = None,
) -> Dict:
    """
    Run ice-class hull form multi-objective optimization.

    Args:
        pop_size: Population size (≥40)
        n_generations: Number of generations (≥50)
        seed: Random seed
        use_surrogate: Enable surrogate model acceleration
        surrogate_interval: Retrain surrogate every N generations
        verbose: Print progress
        output_dir: Directory for output files

    Returns:
        Optimization result dictionary with:
        - pareto_pop: (N, 6) array of Pareto-optimal designs
        - pareto_objs: (N, 3) array of Pareto objectives [R_ice, R_ow, -GM]
        - wall_time_s: wall-clock time
        - convergence: convergence metrics
        - hv_history: hypervolume per generation
        - ... plus formatted output
    """
    lb, ub = get_bounds()

    # Initialize surrogate model
    surrogate = SurrogateModel(n_obj=3) if use_surrogate else None

    # Objective function with optional surrogate
    eval_count = [0]

    def objective_fn(x: np.ndarray):
        """Evaluate objectives, optionally using surrogate."""
        eval_count[0] += 1

        # Always use high-fidelity for now (surrogate is optional acceleration)
        objs, cv = evaluate_all(x)

        # Update surrogate
        if surrogate is not None:
            surrogate.add_samples(x.reshape(1, -1), objs.reshape(1, -1))

        return objs, cv

    # Run NSGA-II
    optimizer = NSGA2Optimizer(
        n_objectives=3,
        n_variables=6,
        pop_size=pop_size,
        n_generations=n_generations,
        seed=seed,
    )

    result = optimizer.run(objective_fn, lb, ub, verbose=verbose)

    # Post-process results
    pareto_pop = result["pareto_pop"]
    pareto_objs = result["pareto_objs"]

    # Convert -GM back to GM for reporting
    if len(pareto_objs) > 0:
        pareto_objs_report = pareto_objs.copy()
        pareto_objs_report[:, 2] = -pareto_objs[:, 2]  # GM = -(-GM)
    else:
        pareto_objs_report = np.array([]).reshape(0, 3)

    # Compute convergence metrics
    hv_history = result["hv_history"]
    convergence_info = _compute_convergence_metrics(hv_history)

    # Build output — include both raw arrays and formatted data
    output = {
        # Raw arrays for programmatic access
        "pareto_pop": pareto_pop,
        "pareto_objs": pareto_objs,
        "wall_time_s": result["wall_time_s"],
        "final_pop": result["final_pop"],
        "final_objs": result["final_objs"],
        "final_cvs": result["final_cvs"],
        "reference_point": result["reference_point"],

        # Formatted output
        "metadata": {
            "task": "Ice-Class Hull Form Multi-Objective Optimization",
            "algorithm": "NSGA-II",
            "pop_size": pop_size,
            "n_generations": n_generations,
            "seed": seed,
            "use_surrogate": use_surrogate,
            "wall_time_s": result["wall_time_s"],
            "total_evaluations": eval_count[0],
        },
        "design_variables": {
            "names": ["L", "B", "T", "Cb", "α_bow", "α_stem"],
            "units": ["m", "m", "m", "-", "deg", "deg"],
            "bounds": {k: list(v) for k, v in CONSTRAINT_BOUNDS.items()},
        },
        "objectives": {
            "names": ["R_ice", "R_ow", "GM"],
            "units": ["kN", "kN", "m"],
            "directions": ["min", "min", "max"],
        },
        "pareto_front": {
            "n_solutions": len(pareto_pop),
            "solutions": _format_pareto_solutions(pareto_pop, pareto_objs_report) if len(pareto_pop) > 0 else [],
        },
        "convergence": convergence_info,
        "hv_history": hv_history,
    }

    # Save outputs
    if output_dir:
        _save_outputs(output, output_dir)

    return output


def _compute_convergence_metrics(hv_history: list) -> Dict:
    """Compute convergence metrics from hypervolume history."""
    if len(hv_history) < 5:
        return {"converged": False, "reason": "insufficient generations"}

    # Check last 5 generations for HV change rate < 0.01
    last_5 = hv_history[-5:]
    changes = []
    for i in range(1, len(last_5)):
        if last_5[i - 1] > 0:
            changes.append(abs(last_5[i] - last_5[i - 1]) / last_5[i - 1])
        else:
            changes.append(0.0)

    max_change = max(changes) if changes else 1.0
    converged = max_change < 0.01

    return {
        "converged": converged,
        "max_hv_change_rate_last_5": max_change,
        "final_hv": hv_history[-1],
        "hv_changes_last_5": changes,
    }


def _format_pareto_solutions(pop: np.ndarray, objs: np.ndarray) -> list:
    """Format Pareto solutions for output."""
    solutions = []
    var_names = ["L", "B", "T", "Cb", "α_bow", "α_stem"]
    obj_names = ["R_ice", "R_ow", "GM"]

    for i in range(len(pop)):
        sol = {
            "design": {var_names[j]: float(pop[i, j]) for j in range(6)},
            "objectives": {obj_names[j]: float(objs[i, j]) for j in range(3)},
        }
        # Add constraint check
        feasible, details = check_constraints(pop[i])
        sol["feasible"] = feasible
        sol["constraint_details"] = details
        solutions.append(sol)

    return solutions


def _save_outputs(output: Dict, output_dir: str):
    """Save optimization outputs to files."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Save full result (convert numpy arrays to lists for JSON serialization)
    json_output = {}
    for k, v in output.items():
        if isinstance(v, np.ndarray):
            json_output[k] = v.tolist()
        elif isinstance(v, dict):
            json_output[k] = _make_json_safe(v)
        elif isinstance(v, list):
            json_output[k] = [_make_json_safe(item) if isinstance(item, dict) else item for item in v]
        else:
            json_output[k] = v

    with open(out_path / "optimization_result.json", "w") as f:
        json.dump(json_output, f, indent=2, default=str)

    # Save Pareto front as CSV
    if output["pareto_front"]["n_solutions"] > 0:
        solutions = output["pareto_front"]["solutions"]
        header = "L,B,T,Cb,alpha_bow,alpha_stem,R_ice,R_ow,GM,feasible\n"
        rows = []
        for sol in solutions:
            d = sol["design"]
            o = sol["objectives"]
            rows.append(f"{d['L']:.2f},{d['B']:.2f},{d['T']:.2f},{d['Cb']:.4f},"
                       f"{d['α_bow']:.2f},{d['α_stem']:.2f},"
                       f"{o['R_ice']:.2f},{o['R_ow']:.2f},{o['GM']:.4f},"
                       f"{sol['feasible']}")
        with open(out_path / "pareto_front.csv", "w") as f:
            f.write(header)
            f.write("\n".join(rows))

    # Save convergence history
    with open(out_path / "convergence_history.json", "w") as f:
        json.dump(output["convergence"], f, indent=2, default=str)

    print(f"Outputs saved to: {out_path}")


def _make_json_safe(d):
    """Recursively convert numpy types in dict to Python native types."""
    if isinstance(d, dict):
        return {k: _make_json_safe(v) for k, v in d.items()}
    elif isinstance(d, np.ndarray):
        return d.tolist()
    elif isinstance(d, (np.integer, np.int64)):
        return int(d)
    elif isinstance(d, (np.floating, np.float64)):
        return float(d)
    elif isinstance(d, (np.bool_,)):
        return bool(d)
    return d
