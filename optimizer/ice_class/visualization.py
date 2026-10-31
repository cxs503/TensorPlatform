"""
Pareto Front Visualization for Ice-Class Hull Form Optimization.

Generates:
  1. 3D Pareto front (R_ice vs R_ow vs GM)
  2. 2D projection pairs (R_ice-R_ow, R_ice-GM, R_ow-GM)
  3. Design space parallel coordinates
  4. Convergence curve (hypervolume vs generation)
"""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from pathlib import Path
from typing import Dict, Optional

from .optimization import run_ice_class_optimization


def generate_all_figures(output_dir: str = None, seed: int = 42) -> Dict[str, str]:
    """
    Generate all visualization figures.

    Args:
        output_dir: Directory for saving figures
        seed: Random seed for optimization

    Returns:
        Dict mapping figure name to file path
    """
    if output_dir is None:
        output_dir = str(Path(__file__).parent.parent.parent / "artifacts" / "ice_class")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Run optimization
    result = run_ice_class_optimization(
        pop_size=40, n_generations=50, seed=seed, use_surrogate=True, verbose=False
    )

    pareto_pop = result["pareto_pop"]
    pareto_objs = result["pareto_objs"]

    # Convert objectives: [R_ice, R_ow, -GM] → [R_ice, R_ow, GM]
    if len(pareto_objs) > 0:
        R_ice = pareto_objs[:, 0]
        R_ow = pareto_objs[:, 1]
        GM = -pareto_objs[:, 2]
    else:
        return {}

    figures = {}

    # ── Figure 1: 3D Pareto Front ──
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection='3d')
    scatter = ax.scatter(R_ice, R_ow, GM, c=GM, cmap='viridis', s=60, alpha=0.8, edgecolors='k', linewidth=0.5)
    ax.set_xlabel('Ice Resistance R_ice [kN]', fontsize=11)
    ax.set_ylabel('Open Water Resistance R_ow [kN]', fontsize=11)
    ax.set_zlabel('Metacentric Height GM [m]', fontsize=11)
    ax.set_title('Ice-Class Hull Form: 3D Pareto Front\n(NSGA-II, 50 gen, pop=40)', fontsize=13)
    fig.colorbar(scatter, ax=ax, label='GM [m]', shrink=0.6)
    plt.tight_layout()
    fpath = str(out_path / "pareto_3d.png")
    plt.savefig(fpath, dpi=150, bbox_inches='tight')
    plt.close()
    figures["pareto_3d"] = fpath

    # ── Figure 2: 2D Projection Pairs ──
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    # R_ice vs R_ow
    axes[0].scatter(R_ice, R_ow, c='steelblue', s=50, alpha=0.8, edgecolors='k', linewidth=0.5)
    axes[0].set_xlabel('Ice Resistance R_ice [kN]', fontsize=11)
    axes[0].set_ylabel('Open Water Resistance R_ow [kN]', fontsize=11)
    axes[0].set_title('R_ice vs R_ow Tradeoff', fontsize=12)
    axes[0].grid(True, alpha=0.3)

    # R_ice vs GM
    axes[1].scatter(R_ice, GM, c='coral', s=50, alpha=0.8, edgecolors='k', linewidth=0.5)
    axes[1].set_xlabel('Ice Resistance R_ice [kN]', fontsize=11)
    axes[1].set_ylabel('Metacentric Height GM [m]', fontsize=11)
    axes[1].set_title('R_ice vs GM Tradeoff', fontsize=12)
    axes[1].grid(True, alpha=0.3)

    # R_ow vs GM
    axes[2].scatter(R_ow, GM, c='seagreen', s=50, alpha=0.8, edgecolors='k', linewidth=0.5)
    axes[2].set_xlabel('Open Water Resistance R_ow [kN]', fontsize=11)
    axes[2].set_ylabel('Metacentric Height GM [m]', fontsize=11)
    axes[2].set_title('R_ow vs GM Tradeoff', fontsize=12)
    axes[2].grid(True, alpha=0.3)

    plt.suptitle('Ice-Class Hull Form: Pareto Front 2D Projections', fontsize=14, y=1.02)
    plt.tight_layout()
    fpath = str(out_path / "pareto_2d_projections.png")
    plt.savefig(fpath, dpi=150, bbox_inches='tight')
    plt.close()
    figures["pareto_2d_projections"] = fpath

    # ── Figure 3: Design Space Parallel Coordinates ──
    var_names = ['L [m]', 'B [m]', 'T [m]', 'Cb', 'α_bow [°]', 'α_stem [°]']
    fig, ax = plt.subplots(figsize=(12, 6))

    # Normalize to [0, 1] for parallel coordinates
    from .constraints import CONSTRAINT_BOUNDS
    lb = np.array([CONSTRAINT_BOUNDS[k][0] for k in ["L", "B", "T", "Cb", "alpha_bow", "alpha_stem"]])
    ub = np.array([CONSTRAINT_BOUNDS[k][1] for k in ["L", "B", "T", "Cb", "alpha_bow", "alpha_stem"]])
    normalized = (pareto_pop - lb) / (ub - lb)

    for i in range(len(pareto_pop)):
        color = plt.cm.viridis(GM[i] / GM.max() if GM.max() > 0 else 0.5)
        ax.plot(range(6), normalized[i], color=color, alpha=0.6, linewidth=1.5)

    ax.set_xticks(range(6))
    ax.set_xticklabels(var_names, fontsize=10)
    ax.set_ylabel('Normalized Value', fontsize=11)
    ax.set_ylim(-0.05, 1.05)
    ax.set_title('Design Space: Parallel Coordinates (color = GM)', fontsize=13)
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    fpath = str(out_path / "design_space_parallel.png")
    plt.savefig(fpath, dpi=150, bbox_inches='tight')
    plt.close()
    figures["design_space_parallel"] = fpath

    # ── Figure 4: Convergence Curve ──
    fig, ax = plt.subplots(figsize=(10, 6))
    hv_history = result["hv_history"]
    ax.plot(range(1, len(hv_history) + 1), hv_history, 'b-', linewidth=2, label='Hypervolume')
    ax.set_xlabel('Generation', fontsize=11)
    ax.set_ylabel('Hypervolume Indicator', fontsize=11)
    ax.set_title('NSGA-II Convergence: Hypervolume vs Generation', fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=11)

    # Mark convergence point
    if result["convergence"]["converged"]:
        ax.axhline(y=hv_history[-1], color='r', linestyle='--', alpha=0.5, label='Converged HV')

    plt.tight_layout()
    fpath = str(out_path / "convergence_curve.png")
    plt.savefig(fpath, dpi=150, bbox_inches='tight')
    plt.close()
    figures["convergence_curve"] = fpath

    return figures


if __name__ == "__main__":
    figures = generate_all_figures()
    for name, path in figures.items():
        print(f"  {name}: {path}")
