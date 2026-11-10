"""
Generate verification figures for P167W2-N10 cross-library conservation campaign.

Produces:
1. Four-ledger conservation bar chart (FEM→DEM)
2. LBM→FVM mass flux conservation comparison
3. Clock synchronization timeline (conservative vs optimistic)
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import json
import os
import sys

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.coupling.fem_to_dem_transfer import (
    FEMtoDEMTransfer, FailureFace, FEMElementState
)
from src.coupling.lbm_to_fvm_interpolation import (
    LBMtoFVMInterpolator, LBMCell, FVMCell
)
from src.coupling.clock_sync import (
    ClockSynchronizer, SolverClock, SyncMode
)


def generate_four_ledger_figure(output_path):
    """Generate four-ledger conservation bar chart."""
    # Create synthetic test case
    faces = [
        FailureFace(
            vertices=np.array([[0, 0, 0], [1, 0, 0], [0.5, 0.5, 0.5]]),
            normal=np.array([0, -1, 1]),
            area=0.5,
        ),
        FailureFace(
            vertices=np.array([[0, 1, 0], [1, 1, 0], [0.5, 0.5, 0.5]]),
            normal=np.array([0, 1, -1]),
            area=0.5,
        ),
    ]
    fem_state = FEMElementState(
        mass=1.0,
        velocity=np.array([1.0, 0.5, 0.0]),
        angular_velocity=np.array([0.0, 0.0, 0.5]),
        inertia_tensor=np.eye(3) * 0.1,
        elastic_strain_energy=0.01,
        centroid=np.array([0.5, 0.5, 0.5]),
    )

    # Run transfer with different fragment counts
    n_fragments_list = [2, 3, 4, 5, 8]
    mass_residuals = []
    momentum_residuals = []
    energy_residuals = []
    inertia_residuals = []

    for n in n_fragments_list:
        transfer = FEMtoDEMTransfer(tolerance=0.05)
        fragments, ledger = transfer.transfer(fem_state, faces, n_fragments=n)
        mass_residuals.append(ledger.mass_residual)
        momentum_residuals.append(ledger.momentum_residual)
        energy_residuals.append(ledger.energy_residual)
        inertia_residuals.append(ledger.inertia_residual)

    # Plot
    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(n_fragments_list))
    width = 0.2

    bars1 = ax.bar(x - 1.5*width, mass_residuals, width, label='Mass', color='#2196F3')
    bars2 = ax.bar(x - 0.5*width, momentum_residuals, width, label='Momentum', color='#4CAF50')
    bars3 = ax.bar(x + 0.5*width, energy_residuals, width, label='Energy', color='#FF9800')
    bars4 = ax.bar(x + 1.5*width, inertia_residuals, width, label='Inertia', color='#9C27B0')

    # Tolerance line
    ax.axhline(y=0.05, color='red', linestyle='--', alpha=0.7, label='Tolerance (5%)')

    ax.set_xlabel('Number of DEM Fragments', fontsize=12)
    ax.set_ylabel('Relative Residual', fontsize=12)
    ax.set_title('FEM→DEM Four-Ledger Conservation Verification\n(Synthetic Geometry, Zero Domain Semantics)', fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels([str(n) for n in n_fragments_list])
    ax.legend(fontsize=10)
    ax.set_yscale('log')
    ax.yaxis.set_major_formatter(mticker.ScalarFormatter())
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_path}")


def generate_lbm_fvm_figure(output_path):
    """Generate LBM→FVM mass flux conservation comparison."""
    # Create synthetic LBM grid
    lbm_cells = []
    dx = 0.1
    for i in range(4):
        for j in range(4):
            for k in range(4):
                center = np.array([i * dx, j * dx, k * dx])
                velocity = np.array([1.0 - (j * dx) ** 2, 0.0, 0.0])
                density = 1.0 + 0.01 * np.sin(i * j * k)
                pressure = density * 0.333
                lbm_cells.append(LBMCell(
                    center=center, dx=dx, velocity=velocity,
                    density=density, pressure=pressure,
                ))

    # Create synthetic FVM grids at different resolutions
    resolutions = [3, 4, 5, 6, 8]
    flux_residuals_nn = []
    flux_residuals_vw = []
    flux_residuals_fc = []

    for res in resolutions:
        fvm_cells = []
        spacing = 4 * dx / res
        for i in range(res):
            for j in range(res):
                for k in range(res):
                    center = np.array([i * spacing, j * spacing, k * spacing])
                    volume = spacing ** 3
                    fvm_cells.append(FVMCell(center=center, volume=volume))

        # Nearest neighbor
        interp_nn = LBMtoFVMInterpolator(tolerance=0.10)
        result_nn = interp_nn.nearest_neighbor_interpolate(lbm_cells, fvm_cells)
        flux_residuals_nn.append(result_nn.flux_residual)

        # Volume weighted
        fvm_cells_vw = [FVMCell(center=c.center, volume=c.volume) for c in fvm_cells]
        interp_vw = LBMtoFVMInterpolator(tolerance=0.10)
        result_vw = interp_vw.volume_weighted_interpolate(lbm_cells, fvm_cells_vw)
        flux_residuals_vw.append(result_vw.flux_residual)

        # Flux corrected
        fvm_cells_fc = [FVMCell(center=c.center, volume=c.volume) for c in fvm_cells]
        interp_fc = LBMtoFVMInterpolator(tolerance=0.10)
        result_fc = interp_fc.flux_corrected_interpolate(lbm_cells, fvm_cells_fc)
        flux_residuals_fc.append(result_fc.flux_residual)

    # Plot
    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(resolutions))
    width = 0.25

    ax.bar(x - width, flux_residuals_nn, width, label='Nearest Neighbor', color='#2196F3')
    ax.bar(x, flux_residuals_vw, width, label='Volume Weighted', color='#4CAF50')
    ax.bar(x + width, flux_residuals_fc, width, label='Flux Corrected', color='#FF9800')

    ax.axhline(y=0.03, color='red', linestyle='--', alpha=0.7, label='Tolerance (3%)')

    ax.set_xlabel('FVM Grid Resolution (N×N×N)', fontsize=12)
    ax.set_ylabel('Mass Flux Residual', fontsize=12)
    ax.set_title('LBM→FVM Mass Flux Conservation\n(Synthetic Geometry, Zero Domain Semantics)', fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r}³" for r in resolutions])
    ax.legend(fontsize=10)
    ax.set_yscale('log')
    ax.yaxis.set_major_formatter(mticker.ScalarFormatter())
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_path}")


def generate_clock_sync_figure(output_path):
    """Generate clock synchronization timeline comparison."""
    # Conservative mode
    sync_c = ClockSynchronizer(mode=SyncMode.CONSERVATIVE)
    c1 = SolverClock(solver_id="fast", dt=0.005, max_time=0.1)
    c2 = SolverClock(solver_id="slow", dt=0.02, max_time=0.1)
    sync_c.register_solver(c1)
    sync_c.register_solver(c2)
    results_c = sync_c.run_simulation(max_steps=20)

    # Optimistic mode
    sync_o = ClockSynchronizer(mode=SyncMode.OPTIMISTIC, checkpoint_interval=1)
    o1 = SolverClock(solver_id="fast", dt=0.005, max_time=0.1)
    o2 = SolverClock(solver_id="slow", dt=0.02, max_time=0.1)
    sync_o.register_solver(o1)
    sync_o.register_solver(o2)
    results_o = sync_o.run_simulation(max_steps=20)

    # Plot
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    # Conservative
    steps_c = list(range(len(results_c)))
    times_fast_c = [r.solver_times.get("fast", 0) for r in results_c]
    times_slow_c = [r.solver_times.get("slow", 0) for r in results_c]
    ax1.plot(steps_c, times_fast_c, 'b-o', markersize=4, label='Fast (dt=0.005)')
    ax1.plot(steps_c, times_slow_c, 'r-s', markersize=4, label='Slow (dt=0.02)')
    ax1.set_ylabel('Simulation Time', fontsize=11)
    ax1.set_title('Conservative Synchronization (Barrier)', fontsize=12)
    ax1.legend(fontsize=10)
    ax1.grid(True, alpha=0.3)

    # Optimistic
    steps_o = list(range(len(results_o)))
    times_fast_o = [r.solver_times.get("fast", 0) for r in results_o]
    times_slow_o = [r.solver_times.get("slow", 0) for r in results_o]
    ax2.plot(steps_o, times_fast_o, 'b-o', markersize=4, label='Fast (dt=0.005)')
    ax2.plot(steps_o, times_slow_o, 'r-s', markersize=4, label='Slow (dt=0.02)')
    ax2.set_xlabel('Sync Step', fontsize=11)
    ax2.set_ylabel('Simulation Time', fontsize=11)
    ax2.set_title('Optimistic Synchronization (with Rollback)', fontsize=12)
    ax2.legend(fontsize=10)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts")
    os.makedirs(output_dir, exist_ok=True)

    generate_four_ledger_figure(os.path.join(output_dir, "four_ledger_conservation.png"))
    generate_lbm_fvm_figure(os.path.join(output_dir, "lbm_fvm_mass_flux.png"))
    generate_clock_sync_figure(os.path.join(output_dir, "clock_sync_timeline.png"))

    print("All figures generated successfully.")
