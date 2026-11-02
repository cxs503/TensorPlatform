"""
Main Optimization Loop for KCS Bow Form

Integrates:
- KCS bow parameterization (m546)
- Resistance evaluation (m547)
- CMA-ES optimizer
- Geometric constraints
- Convergence tracking
"""
import numpy as np
import json
import os
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime

from ..interfaces.kcs_bow_param import (
    generate_geometry, check_geometric_constraints, 
    get_parameter_names, PARAM_BOUNDS, KCS_BASELINE
)
from ..interfaces.resistance_eval import evaluate_resistance, get_baseline_resistance
from .cmaes import optimize


class NumpyEncoder(json.JSONEncoder):
    """Custom encoder for numpy types."""
    def default(self, obj):
        if isinstance(obj, (np.integer, np.int64)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64)):
            return float(obj)
        elif isinstance(obj, (np.bool_,)):
            return bool(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def objective_function(params: np.ndarray, seed: int = None) -> float:
    """
    Objective function for optimization: minimize Ct at design Fn.
    
    Applies geometric constraints via penalty method.
    
    Args:
        params: 8-dim parameter vector in [-1, 1]
        seed: Random seed for reproducibility
    
    Returns:
        Fitness value (Ct, or penalty if invalid)
    """
    # Generate geometry
    geom = generate_geometry(params, seed=seed)
    
    # Check geometric constraints
    valid, reason = check_geometric_constraints(geom)
    if not valid:
        return 1e6  # Large penalty for invalid geometry
    
    # Evaluate resistance
    eval_result = evaluate_resistance(geom, KCS_BASELINE["Fn_design"], seed=seed)
    
    if not eval_result["valid"]:
        return 1e6  # Penalty for invalid evaluation
    
    # Check Holtrop domain
    if not eval_result["in_holtrop_domain"]:
        return 1e5  # Smaller penalty for out-of-domain
    
    return eval_result["Ct"]


def run_optimization(seed: int = 42, max_evals: int = 300, 
                    patience: int = 20, output_dir: str = None) -> Dict:
    """
    Run full optimization loop.
    
    Args:
        seed: Random seed for reproducibility
        max_evals: Maximum evaluations (200-500 per spec)
        patience: Convergence patience (N generations without improvement)
        output_dir: Directory for output files
    
    Returns:
        Optimization result dictionary
    """
    print("=" * 70)
    print("KCS Bow Form Optimization - Closed Loop")
    print("=" * 70)
    print(f"Seed: {seed}")
    print(f"Max evaluations: {max_evals}")
    print(f"Convergence patience: {patience} generations")
    print(f"Design Fn: {KCS_BASELINE['Fn_design']}")
    print(f"Parameters: 8 (KCS bow deformation)")
    print("=" * 70)
    
    # Get baseline
    baseline = get_baseline_resistance()
    baseline_Ct = baseline["Ct"]
    print(f"\nBaseline Ct: {baseline_Ct:.6f}")
    print(f"Baseline valid: {baseline['valid']}")
    print(f"Baseline in Holtrop domain: {baseline['in_holtrop_domain']}")
    print(f"Objective physics: {baseline.get('objective_evidence_level')} <- "
          f"{baseline.get('objective_physics_source')}")
    
    # Run optimization
    print("\nStarting optimization...")
    result = optimize(
        objective_fn=lambda x: objective_function(x, seed=seed),
        dim=8,
        bounds=PARAM_BOUNDS,
        max_evals=max_evals,
        sigma0=0.3,
        seed=seed,
        patience=patience,
        verbose=True
    )
    
    print("\n" + "=" * 70)
    print("Optimization Complete")
    print("=" * 70)
    print(f"Total evaluations: {result['evaluations']}")
    print(f"Generations: {result['generations']}")
    print(f"Best Ct: {result['best_fitness']:.6f}")
    print(f"Improvement: {(1 - result['best_fitness']/baseline_Ct)*100:.2f}%")
    print(f"Converged: {result['converged']}")
    print(f"Reason: {result['convergence_reason']}")
    
    # Get top-5 candidates from history
    top5 = extract_top5_candidates(result["history"], seed)
    
    # Prepare output
    output = {
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "seed": seed,
            "max_evals": max_evals,
            "patience": patience,
            "design_Fn": KCS_BASELINE["Fn_design"],
            "baseline_Ct": float(baseline_Ct),
            "objective_evidence_level": baseline.get("evidence_level", "UNKNOWN"),
            "objective_physics_source": baseline.get("physics_source", ""),
            "best_Ct": float(result["best_fitness"]),
            "improvement_pct": float((1 - result["best_fitness"]/baseline_Ct)*100),
            "total_evaluations": int(result["evaluations"]),
            "generations": int(result["generations"]),
            "converged": bool(result["converged"]),
            "convergence_reason": str(result["convergence_reason"]),
            # m1610: provenance of the objective physics actually used
            "objective_evidence_level": baseline.get("objective_evidence_level", "WITHHELD"),
            "objective_physics_source": baseline.get("objective_physics_source", "unknown"),
        },
        "baseline": {
            "Ct": float(baseline["Ct"]),
            "valid": bool(baseline["valid"]),
            "in_holtrop_domain": bool(baseline["in_holtrop_domain"])
        },
        "objective_evidence_level": baseline.get("objective_evidence_level", "WITHHELD"),
        "objective_physics_source": baseline.get("objective_physics_source", "unknown"),
        "best_params": result["best_params"].tolist(),
        "best_params_named": dict(zip(get_parameter_names(), result["best_params"].tolist())),
        "top5_candidates": top5,
        "convergence_history": result["history"]
    }
    
    # Save outputs
    if output_dir:
        save_outputs(output, output_dir)
    
    return output


def extract_top5_candidates(history: List[Dict], seed: int) -> List[Dict]:
    """
    Extract top-5 candidates from optimization history.
    
    All candidates must pass geometric constraints and Holtrop domain check.
    
    Args:
        history: Optimization history
        seed: Random seed for reproducibility
    
    Returns:
        List of top-5 candidate dictionaries
    """
    # Collect all unique candidates
    candidates = []
    seen = set()
    
    for entry in history:
        params = np.array(entry["best_params"])
        params_tuple = tuple(np.round(params, 6))
        
        if params_tuple in seen:
            continue
        seen.add(params_tuple)
        
        # Validate candidate
        geom = generate_geometry(params, seed=seed)
        valid, reason = check_geometric_constraints(geom)
        
        if not valid:
            continue
        
        eval_result = evaluate_resistance(geom, KCS_BASELINE["Fn_design"], seed=seed)
        
        if not eval_result["valid"] or not eval_result["in_holtrop_domain"]:
            continue
        
        candidates.append({
            "params": params.tolist(),
            "params_named": dict(zip(get_parameter_names(), params.tolist())),
            "Ct": float(eval_result["Ct"]),
            "components": {k: float(v) for k, v in eval_result["components"].items()},
            "geometry": {
                "volume_ratio": float(geom["volume_ratio"]),
                "Cb": float(geom["Cb"]),
                "watertight": bool(geom["watertight"])
            },
            "objective_evidence_level": eval_result.get("objective_evidence_level", "WITHHELD"),
            "objective_physics_source": eval_result.get("objective_physics_source", "unknown"),
            "generation": int(entry["generation"]),
            "eval_count": int(entry["eval_count"])
        })
    
    # Sort by Ct and take top 5
    candidates.sort(key=lambda x: x["Ct"])
    return candidates[:5]


def save_outputs(output: Dict, output_dir: str):
    """Save optimization outputs to files."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Save full result
    with open(out_path / "optimization_result.json", "w") as f:
        json.dump(output, f, indent=2, cls=NumpyEncoder)
    
    # Save convergence curve
    convergence = [
        {
            "generation": int(h["generation"]),
            "eval_count": int(h["eval_count"]),
            "best_Ct": float(h["best_fitness"]),
            "mean_Ct": float(h["mean_fitness"]),
            "sigma": float(h["sigma"])
        }
        for h in output["convergence_history"]
    ]
    with open(out_path / "convergence_curve.json", "w") as f:
        json.dump(convergence, f, indent=2)
    
    # Save top-5 candidates
    with open(out_path / "top5_candidates.json", "w") as f:
        json.dump(output["top5_candidates"], f, indent=2, cls=NumpyEncoder)
    
    # Save summary report
    with open(out_path / "summary.txt", "w") as f:
        f.write("KCS Bow Form Optimization Summary\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"Timestamp: {output['metadata']['timestamp']}\n")
        f.write(f"Seed: {output['metadata']['seed']}\n")
        f.write(f"Design Fn: {output['metadata']['design_Fn']}\n\n")
        
        f.write("Baseline:\n")
        f.write(f"  Ct = {output['baseline']['Ct']:.6f}\n\n")
        
        f.write("Best Result:\n")
        f.write(f"  Ct = {output['metadata']['best_Ct']:.6f}\n")
        f.write(f"  Improvement = {output['metadata']['improvement_pct']:.2f}%\n")
        f.write(f"  Evaluations = {output['metadata']['total_evaluations']}\n")
        f.write(f"  Generations = {output['metadata']['generations']}\n")
        f.write(f"  Converged = {output['metadata']['converged']}\n\n")
        
        f.write("Best Parameters:\n")
        for name, value in output['best_params_named'].items():
            f.write(f"  {name}: {value:.4f}\n")
        f.write("\n")
        
        f.write("Top-5 Candidates:\n")
        for i, cand in enumerate(output['top5_candidates'], 1):
            f.write(f"\n  #{i}: Ct = {cand['Ct']:.6f}\n")
            f.write(f"      Generation: {cand['generation']}, Eval: {cand['eval_count']}\n")
            f.write(f"      Volume ratio: {cand['geometry']['volume_ratio']:.4f}\n")
            f.write(f"      Cb: {cand['geometry']['Cb']:.4f}\n")
    
    print(f"\nOutputs saved to: {out_path}")


if __name__ == "__main__":
    # Run optimization with default settings
    result = run_optimization(
        seed=42,
        max_evals=300,
        patience=20,
        output_dir="./runs/run_001"
    )
