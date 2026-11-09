"""Generate figures for SIR-PC2 full-chain integration report."""

import math
import os
import base64
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from backend.services.sir_pc2.full_chain import (
    SIRFullChainOrchestrator, SevenLegGeometry,
    run_full_verification, verify_energy_conservation,
)
from backend.services.sir_pc2.adapters import (
    IceSheetAddedMassAdapter, FluidDEMCouplingAdapter,
    GeometricNonlinearFEMAdapter,
)


def generate_energy_conservation_plot(save_path: str) -> str:
    """Figure 1: Energy conservation over simulation time."""
    orchestrator = SIRFullChainOrchestrator()
    steps = orchestrator.run(n_steps=100)

    times = [s.time for s in steps]
    energies = [s.energy.total() for s in steps]
    e0 = energies[0]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(times, energies, 'b-', linewidth=2, label='Total Energy')
    ax.axhline(y=e0, color='r', linestyle='--', alpha=0.7, label='Initial Energy')

    errors = [abs(e - e0) / abs(e0) if abs(e0) > 1e-15 else 0 for e in energies]
    ax2 = ax.twinx()
    ax2.plot(times, errors, 'g-', linewidth=1.5, alpha=0.7, label='Relative Error')
    ax2.set_ylabel('Relative Energy Error', color='g')
    ax2.tick_params(axis='y', labelcolor='g')

    ax.set_xlabel('Time [s]')
    ax.set_ylabel('Total Energy [J]')
    ax.set_title('SIR-PC2 Full-Chain Energy Conservation\n(LBM → FEM → DEM → Rigid Body → LBM)')
    ax.legend(loc='upper left')
    ax2.legend(loc='upper right')
    ax.grid(True, alpha=0.3)

    final_error = errors[-1]
    ax.text(0.02, 0.98, f'Final error: {final_error:.6f}',
            transform=ax.transAxes, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    return save_path


def generate_seven_leg_geometry_plot(save_path: str) -> str:
    """Figure 2: Seven-leg SIR ship geometry."""
    geometry = SevenLegGeometry()
    leg_positions = geometry.leg_positions()

    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection='3d')

    L, B, D = geometry.length, geometry.beam, geometry.draft
    t = np.linspace(0, 1, 50)
    x_hull = -L/2 + t * L
    y_hull = B/2 * np.sin(np.pi * t)
    z_hull = -D * (1 - t * 0.5)
    ax.plot(x_hull, y_hull, z_hull, 'b-', linewidth=3, label='Hull centerline')
    ax.plot(x_hull, -y_hull, z_hull, 'b-', linewidth=3)

    for i, pos in enumerate(leg_positions):
        ax.scatter(*pos, c='red', s=200, marker='o', zorder=5,
                   label=f'Leg {i+1}' if i == 0 else '')
        z_leg = np.linspace(pos[2] - geometry.leg_length/2, pos[2] + geometry.leg_length/2, 20)
        x_leg = np.full_like(z_leg, pos[0])
        y_leg = np.full_like(z_leg, pos[1])
        ax.plot(x_leg, y_leg, z_leg, 'r-', linewidth=2, alpha=0.7)

    ax.set_xlabel('X [m] (Longitudinal)')
    ax.set_ylabel('Y [m] (Transverse)')
    ax.set_zlabel('Z [m] (Vertical)')
    ax.set_title('SIR Seven-Leg Ship Geometry\n(Ice-breaking configuration)')
    ax.legend(loc='upper right')
    ax.view_init(elev=20, azim=45)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    return save_path


def generate_coupling_diagnostics_plot(save_path: str) -> str:
    """Figure 3: Coupling diagnostics across all seven legs."""
    orchestrator = SIRFullChainOrchestrator()
    steps = orchestrator.run(n_steps=100)

    # Skip step 0 which has no diagnostics
    steps_with_diag = [s for s in steps if s.diagnostics]
    times = [s.time for s in steps_with_diag]
    diagnostics = [s.diagnostics for s in steps_with_diag]

    fig, axes = plt.subplots(2, 3, figsize=(15, 10))

    speeds = [d.get('rb_speed', 0) for d in diagnostics]
    axes[0, 0].plot(times, speeds, 'b-', linewidth=1.5)
    axes[0, 0].set_xlabel('Time [s]')
    axes[0, 0].set_ylabel('Speed [m/s]')
    axes[0, 0].set_title('Ship Speed')
    axes[0, 0].grid(True, alpha=0.3)

    rolls = [d.get('rb_roll_angle', 0) for d in diagnostics]
    axes[0, 1].plot(times, np.degrees(rolls), 'r-', linewidth=1.5)
    axes[0, 1].axhline(y=15, color='orange', linestyle='--', alpha=0.5, label='15° threshold')
    axes[0, 1].set_xlabel('Time [s]')
    axes[0, 1].set_ylabel('Roll Angle [deg]')
    axes[0, 1].set_title('Ship Roll Angle')
    axes[0, 1].legend()
    axes[0, 1].grid(True, alpha=0.3)

    added_masses = [d.get('ice_sheet_added_mass', 0) for d in diagnostics]
    axes[0, 2].plot(times, added_masses, 'g-', linewidth=1.5)
    axes[0, 2].set_xlabel('Time [s]')
    axes[0, 2].set_ylabel('Added Mass [kg/m²]')
    axes[0, 2].set_title('Ice Sheet Added Mass')
    axes[0, 2].grid(True, alpha=0.3)

    nat_freqs = [d.get('ice_sheet_natural_frequency', 0) for d in diagnostics]
    axes[1, 0].plot(times, nat_freqs, 'm-', linewidth=1.5)
    axes[1, 0].set_xlabel('Time [s]')
    axes[1, 0].set_ylabel('Natural Frequency [Hz]')
    axes[1, 0].set_title('Ice-Water Coupled Frequency')
    axes[1, 0].grid(True, alpha=0.3)

    mom_residuals = [d.get('fluid_dem_momentum_residual', 0) for d in diagnostics]
    axes[1, 1].plot(times, mom_residuals, 'c-', linewidth=1.5)
    axes[1, 1].set_xlabel('Time [s]')
    axes[1, 1].set_ylabel('Momentum Residual [kg·m/s]')
    axes[1, 1].set_title('Fluid-DEM Momentum Conservation')
    axes[1, 1].grid(True, alpha=0.3)

    fem_errors = [d.get('geo_fem_energy_error', 0) for d in diagnostics]
    axes[1, 2].plot(times, fem_errors, 'k-', linewidth=1.5)
    axes[1, 2].set_xlabel('Time [s]')
    axes[1, 2].set_ylabel('Energy Error [ratio]')
    axes[1, 2].set_title('Geometric Nonlinear FEM Error')
    axes[1, 2].grid(True, alpha=0.3)

    plt.suptitle('SIR-PC2 Seven-Leg Coupling Diagnostics', fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    return save_path


def generate_stokes_verification_plot(save_path: str) -> str:
    """Figure 4: Stokes drag verification."""
    adapter = FluidDEMCouplingAdapter()

    radii = np.linspace(0.01, 1.0, 50)
    velocities = np.array([0.1, 0, 0])

    drag_magnitudes = []
    for R in radii:
        drag = adapter.compute_stokes_drag(R, velocities)
        drag_magnitudes.append(np.linalg.norm(drag))

    mu = adapter.fluid_viscosity
    v = 0.1
    analytical = [6 * math.pi * mu * R * v for R in radii]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(radii, drag_magnitudes, 'b-', linewidth=2, label='Computed Stokes Drag')
    ax.plot(radii, analytical, 'r--', linewidth=1.5, alpha=0.7, label='Analytical (6πμRv)')
    ax.set_xlabel('Particle Radius [m]')
    ax.set_ylabel('Drag Force [N]')
    ax.set_title('Stokes Drag Verification\n(Fluid-DEM Bidirectional Coupling, F06/m706)')
    ax.legend()
    ax.grid(True, alpha=0.3)

    errors = [abs(c - a) / a if a > 0 else 0 for c, a in zip(drag_magnitudes, analytical)]
    max_error = max(errors)
    ax.text(0.02, 0.98, f'Max relative error: {max_error:.2e}',
            transform=ax.transAxes, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='lightgreen', alpha=0.5))

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    return save_path


def encode_png_base64(png_path: str) -> str:
    with open(png_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')


if __name__ == '__main__':
    output_dir = '/root/tp-agent/work/m714/artifacts/sir_pc2'
    os.makedirs(output_dir, exist_ok=True)

    print("Generating Figure 1: Energy Conservation...")
    generate_energy_conservation_plot(f'{output_dir}/fig1_energy_conservation.png')

    print("Generating Figure 2: Seven-Leg Geometry...")
    generate_seven_leg_geometry_plot(f'{output_dir}/fig2_seven_leg_geometry.png')

    print("Generating Figure 3: Coupling Diagnostics...")
    generate_coupling_diagnostics_plot(f'{output_dir}/fig3_coupling_diagnostics.png')

    print("Generating Figure 4: Stokes Verification...")
    generate_stokes_verification_plot(f'{output_dir}/fig4_stokes_verification.png')

    print("All figures generated successfully.")
