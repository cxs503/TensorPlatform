#!/usr/bin/env python3
"""
Reproduction Script for KCS Bow Form Optimization

Runs the optimization twice with the same seed to verify bit-identical results.
Generates convergence curves and top-5 candidate reports.

Usage:
    python run_optimization.py [--seed SEED] [--max-evals N] [--output-dir DIR]
"""
import argparse
import sys
import os
from pathlib import Path
from typing import Dict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from optimizer.core.optimization_loop import run_optimization
import numpy as np
import json


def verify_reproducibility(seed: int, max_evals: int, output_dir: str):
    """
    Run optimization twice and verify bit-identical results.
    
    Args:
        seed: Random seed
        max_evals: Maximum evaluations
        output_dir: Output directory
    """
    print("\n" + "=" * 70)
    print("REPRODUCIBILITY VERIFICATION")
    print("=" * 70)
    
    # Run 1
    print("\n[Run 1] Starting optimization...")
    result1 = run_optimization(
        seed=seed,
        max_evals=max_evals,
        patience=20,
        output_dir=f"{output_dir}/run1"
    )
    
    # Run 2
    print("\n[Run 2] Starting optimization...")
    result2 = run_optimization(
        seed=seed,
        max_evals=max_evals,
        patience=20,
        output_dir=f"{output_dir}/run2"
    )
    
    # Compare results
    print("\n" + "=" * 70)
    print("COMPARISON")
    print("=" * 70)
    
    # Compare best fitness
    diff_fitness = abs(result1["metadata"]["best_Ct"] - result2["metadata"]["best_Ct"])
    print(f"Best Ct difference: {diff_fitness:.2e}")
    
    # Compare best parameters
    params1 = np.array(result1["best_params"])
    params2 = np.array(result2["best_params"])
    diff_params = np.max(np.abs(params1 - params2))
    print(f"Max parameter difference: {diff_params:.2e}")
    
    # Compare evaluation counts
    diff_evals = abs(result1["metadata"]["total_evaluations"] - result2["metadata"]["total_evaluations"])
    print(f"Evaluation count difference: {diff_evals}")
    
    # Check bit-identical
    is_identical = (diff_fitness == 0.0 and diff_params == 0.0 and diff_evals == 0)
    
    print("\n" + "=" * 70)
    if is_identical:
        print("✓ REPRODUCIBILITY VERIFIED: Results are bit-identical")
    else:
        print("✗ REPRODUCIBILITY FAILED: Results differ")
        print(f"  Run 1: Ct={result1['metadata']['best_Ct']:.10f}, evals={result1['metadata']['total_evaluations']}")
        print(f"  Run 2: Ct={result2['metadata']['best_Ct']:.10f}, evals={result2['metadata']['total_evaluations']}")
    print("=" * 70)
    
    return is_identical, result1, result2


def generate_visualizations(result: Dict, output_dir: str):
    """
    Generate visualization plots for optimization results.
    
    Args:
        result: Optimization result dictionary
        output_dir: Output directory
    """
    try:
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend
        import matplotlib.pyplot as plt
        
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        
        # 1. Convergence curve
        fig, ax = plt.subplots(figsize=(10, 6))
        
        generations = [h["generation"] for h in result["convergence_history"]]
        best_ct = [h["best_fitness"] for h in result["convergence_history"]]
        mean_ct = [h["mean_fitness"] for h in result["convergence_history"]]
        
        ax.semilogy(generations, best_ct, 'b-', linewidth=2, label='Best Ct')
        ax.semilogy(generations, mean_ct, 'r--', linewidth=1, alpha=0.6, label='Mean Ct')
        ax.axhline(y=result["baseline"]["Ct"], color='g', linestyle=':', 
                   linewidth=2, label=f'Baseline Ct = {result["baseline"]["Ct"]:.6f}')
        
        ax.set_xlabel('Generation', fontsize=12)
        ax.set_ylabel('Ct (log scale)', fontsize=12)
        ax.set_title('KCS Bow Form Optimization - Convergence Curve', fontsize=14)
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(out_path / "convergence_curve.png", dpi=150, bbox_inches='tight')
        plt.close()
        
        # 2. Step size evolution
        fig, ax = plt.subplots(figsize=(10, 6))
        
        sigma = [h["sigma"] for h in result["convergence_history"]]
        ax.semilogy(generations, sigma, 'm-', linewidth=2)
        
        ax.set_xlabel('Generation', fontsize=12)
        ax.set_ylabel('Step size σ (log scale)', fontsize=12)
        ax.set_title('CMA-ES Step Size Evolution', fontsize=14)
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(out_path / "step_size_evolution.png", dpi=150, bbox_inches='tight')
        plt.close()
        
        # 3. Top-5 candidates comparison
        fig, ax = plt.subplots(figsize=(10, 6))
        
        top5 = result["top5_candidates"]
        candidates = [f"C{i+1}" for i in range(len(top5))]
        ct_values = [c["Ct"] for c in top5]
        
        bars = ax.bar(candidates, ct_values, color='steelblue', alpha=0.7)
        ax.axhline(y=result["baseline"]["Ct"], color='red', linestyle='--', 
                   linewidth=2, label=f'Baseline = {result["baseline"]["Ct"]:.6f}')
        
        ax.set_xlabel('Candidate', fontsize=12)
        ax.set_ylabel('Ct', fontsize=12)
        ax.set_title('Top-5 Candidates - Resistance Coefficient', fontsize=14)
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3, axis='y')
        
        # Add value labels on bars
        for bar, ct in zip(bars, ct_values):
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height,
                   f'{ct:.6f}', ha='center', va='bottom', fontsize=9)
        
        plt.tight_layout()
        plt.savefig(out_path / "top5_comparison.png", dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"\nVisualizations saved to: {out_path}")
        print(f"  - convergence_curve.png")
        print(f"  - step_size_evolution.png")
        print(f"  - top5_comparison.png")
        
    except ImportError:
        print("\nWarning: matplotlib not available, skipping visualizations")


def main():
    parser = argparse.ArgumentParser(description="KCS Bow Form Optimization")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--max-evals", type=int, default=300, help="Maximum evaluations")
    parser.add_argument("--output-dir", type=str, default="./optimizer/runs/opt_run",
                       help="Output directory")
    parser.add_argument("--no-verify", action="store_true", 
                       help="Skip reproducibility verification")
    parser.add_argument("--no-plots", action="store_true",
                       help="Skip visualization generation")
    
    args = parser.parse_args()
    
    print("\n" + "=" * 70)
    print("KCS BOW FORM OPTIMIZATION - REPRODUCTION SCRIPT")
    print("=" * 70)
    print(f"Seed: {args.seed}")
    print(f"Max evaluations: {args.max_evals}")
    print(f"Output directory: {args.output_dir}")
    
    if args.no_verify:
        # Single run
        result = run_optimization(
            seed=args.seed,
            max_evals=args.max_evals,
            patience=20,
            output_dir=args.output_dir
        )
        
        if not args.no_plots:
            generate_visualizations(result, args.output_dir)
        
        print("\n" + "=" * 70)
        print("OPTIMIZATION COMPLETE")
        print("=" * 70)
        print(f"Best Ct: {result['metadata']['best_Ct']:.6f}")
        print(f"Improvement: {result['metadata']['improvement_pct']:.2f}%")
        print(f"Evaluations: {result['metadata']['total_evaluations']}")
        
    else:
        # Reproducibility verification
        is_identical, result1, result2 = verify_reproducibility(
            args.seed, args.max_evals, args.output_dir
        )
        
        if not args.no_plots:
            generate_visualizations(result1, args.output_dir)
        
        if not is_identical:
            sys.exit(1)
    
    print("\n✓ All tasks completed successfully")


if __name__ == "__main__":
    main()
