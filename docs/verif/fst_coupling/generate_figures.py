"""
Generate verification figures for FST coupling report.

Creates:
1. Velocity profile comparison
2. Temperature field comparison (partitioned vs monolithic)
3. Biot number scaling
4. Dittus-Boelter correlation validation
5. Convergence history
6. Thermal stress validation
"""

import sys
import os
import numpy as np
import matplotlib.pyplot as plt

# Add core module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'docs', 'verif', 'fst_coupling'))

from fst_coupling_core import (
    FluidProperties, SolidProperties,
    ChannelFlowSolver, ThermalSolver, StructuralSolver,
    PartitionedCoupling, MonolithicCoupling,
    BiotAnalysis
)


def plot_velocity_profile():
    """Plot Poiseuille velocity profile."""
    fluid = FluidProperties()
    flow = ChannelFlowSolver(fluid, L=1.0, H0=0.01)
    
    dp_dx = -100.0
    H = 0.01
    T = 300.0
    
    sol = flow.solve_poiseuille(dp_dx, H, T)
    
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(sol['u'], sol['y']*1000, 'b-', linewidth=2, label='Numerical')
    
    # Analytical parabolic profile
    u_analytical = (-dp_dx / (2 * sol['mu'])) * (H**2/4 - sol['y']**2)
    ax.plot(u_analytical, sol['y']*1000, 'r--', linewidth=2, label='Analytical')
    
    ax.set_xlabel('Velocity u (m/s)', fontsize=12)
    ax.set_ylabel('y (mm)', fontsize=12)
    ax.set_title('Poiseuille Flow Velocity Profile', fontsize=14)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, max(sol['u'])*1.1)
    
    plt.tight_layout()
    plt.savefig('artifacts/fst_coupling/velocity_profile.png', dpi=150)
    plt.close()
    print("✓ Generated velocity_profile.png")


def plot_coupling_comparison():
    """Compare partitioned vs monolithic coupling."""
    fluid = FluidProperties()
    solid = SolidProperties()
    flow = ChannelFlowSolver(fluid, L=1.0, H0=0.01)
    thermal = ThermalSolver(fluid, solid)
    struct = StructuralSolver(solid)
    
    part = PartitionedCoupling(flow, thermal, struct)
    mono = MonolithicCoupling(flow, thermal, struct)
    
    # Sweep Biot number (via wall thickness)
    t_walls = np.logspace(-4, -2, 20)
    Q_in = 1e-4
    T_fluid = 350.0
    T_solid = 300.0
    
    T_part = []
    T_mono = []
    Bi_array = []
    
    for t_wall in t_walls:
        sol_p = part.solve(Q_in, T_fluid, T_solid, t_wall)
        sol_m = mono.solve(Q_in, T_fluid, T_solid, t_wall)
        
        T_part.append(sol_p['T_avg_final'])
        T_mono.append(sol_m['T_avg_final'])
        
        # Compute Biot number
        h = sol_p['h_final']
        Bi = h * t_wall / solid.k
        Bi_array.append(Bi)
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    
    # Temperature comparison
    ax1.semilogx(Bi_array, T_part, 'bo-', linewidth=2, markersize=6, label='Partitioned')
    ax1.semilogx(Bi_array, T_mono, 'r^-', linewidth=2, markersize=6, label='Monolithic')
    ax1.set_xlabel('Biot Number Bi', fontsize=12)
    ax1.set_ylabel('Average Temperature (K)', fontsize=12)
    ax1.set_title('Coupling Strategy Comparison', fontsize=14)
    ax1.legend(fontsize=11)
    ax1.grid(True, alpha=0.3)
    
    # Relative error
    rel_err = [abs(Tp - Tm) / Tm * 100 for Tp, Tm in zip(T_part, T_mono)]
    ax2.semilogx(Bi_array, rel_err, 'g-', linewidth=2)
    ax2.axhline(5.0, color='r', linestyle='--', linewidth=1.5, label='5% threshold')
    ax2.set_xlabel('Biot Number Bi', fontsize=12)
    ax2.set_ylabel('Relative Error (%)', fontsize=12)
    ax2.set_title('Partitioned vs Monolithic Error', fontsize=14)
    ax2.legend(fontsize=11)
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('artifacts/fst_coupling/coupling_comparison.png', dpi=150)
    plt.close()
    print("✓ Generated coupling_comparison.png")


def plot_dittus_boelter():
    """Validate Dittus-Boelter correlation."""
    fluid = FluidProperties()
    solid = SolidProperties()
    thermal = ThermalSolver(fluid, solid)
    
    Pr = 7.0
    D_h = 0.02
    
    Re_range = np.logspace(2, 5, 50)
    Nu_computed = []
    Nu_ref = []
    
    for Re in Re_range:
        h = thermal.compute_heat_transfer_coefficient(Re, Pr, D_h)
        Nu = h * D_h / fluid.k_ref
        Nu_computed.append(Nu)
        
        if Re < 2300:
            Nu_ref.append(4.36)
        else:
            Nu_ref.append(0.023 * Re**0.8 * Pr**0.4)
    
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.loglog(Re_range, Nu_computed, 'b-', linewidth=2, label='Computed')
    ax.loglog(Re_range, Nu_ref, 'r--', linewidth=2, label='Dittus-Boelter / Laminar')
    ax.axvline(2300, color='g', linestyle=':', linewidth=1.5, label='Transition Re=2300')
    
    ax.set_xlabel('Reynolds Number Re', fontsize=12)
    ax.set_ylabel('Nusselt Number Nu', fontsize=12)
    ax.set_title('Dittus-Boelter Correlation Validation', fontsize=14)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3, which='both')
    
    plt.tight_layout()
    plt.savefig('artifacts/fst_coupling/dittus_boelter.png', dpi=150)
    plt.close()
    print("✓ Generated dittus_boelter.png")


def plot_convergence_history():
    """Plot convergence history for partitioned coupling."""
    fluid = FluidProperties()
    solid = SolidProperties()
    flow = ChannelFlowSolver(fluid, L=1.0, H0=0.01)
    thermal = ThermalSolver(fluid, solid)
    struct = StructuralSolver(solid)
    
    part = PartitionedCoupling(flow, thermal, struct)
    
    Q_in = 1e-4
    T_fluid = 350.0
    T_solid = 300.0
    
    # Different coupling strengths
    t_walls = [0.001, 0.005, 0.01]
    colors = ['b', 'g', 'r']
    labels = ['Bi ≈ 0.2', 'Bi ≈ 1', 'Bi ≈ 2']
    
    fig, ax = plt.subplots(figsize=(8, 6))
    
    for t_wall, color, label in zip(t_walls, colors, labels):
        sol = part.solve(Q_in, T_fluid, T_solid, t_wall)
        iters = [h['iter'] for h in sol['history']]
        changes = [h['rel_change_T'] for h in sol['history']]
        
        ax.semilogy(iters, changes, f'{color}o-', linewidth=2, markersize=5, label=label)
    
    ax.axhline(1e-6, color='k', linestyle='--', linewidth=1.5, label='Tolerance')
    ax.set_xlabel('Iteration', fontsize=12)
    ax.set_ylabel('Relative Change in T', fontsize=12)
    ax.set_title('Partitioned Coupling Convergence', fontsize=14)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('artifacts/fst_coupling/convergence_history.png', dpi=150)
    plt.close()
    print("✓ Generated convergence_history.png")


def plot_thermal_stress():
    """Validate thermal stress computation."""
    solid = SolidProperties()
    struct = StructuralSolver(solid)
    
    DeltaT_range = np.linspace(0, 200, 50)
    sigma_computed = []
    sigma_analytical = []
    
    for DeltaT in DeltaT_range:
        sol = struct.thermal_stress(DeltaT, constrained=True)
        sigma_computed.append(sol['sigma_th'] / 1e6)  # MPa
        
        sigma_ref = solid.E * solid.alpha * DeltaT / (1 - solid.nu)
        sigma_analytical.append(sigma_ref / 1e6)  # MPa
    
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(DeltaT_range, sigma_computed, 'bo-', linewidth=2, markersize=5, label='Computed')
    ax.plot(DeltaT_range, sigma_analytical, 'r--', linewidth=2, label='Analytical')
    
    ax.set_xlabel('Temperature Change ΔT (K)', fontsize=12)
    ax.set_ylabel('Thermal Stress σ (MPa)', fontsize=12)
    ax.set_title('Thermal Stress Validation', fontsize=14)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('artifacts/fst_coupling/thermal_stress.png', dpi=150)
    plt.close()
    print("✓ Generated thermal_stress.png")


def plot_biot_regimes():
    """Plot Biot number regimes and coupling strategies."""
    fluid = FluidProperties()
    solid = SolidProperties()
    thermal = ThermalSolver(fluid, solid)
    struct = StructuralSolver(solid)
    biot = BiotAnalysis(thermal, struct)
    
    h_range = np.logspace(0, 5, 100)
    L_char = 0.01
    
    Bi_array = [biot.compute_biot(h, L_char) for h in h_range]
    
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.loglog(h_range, Bi_array, 'b-', linewidth=2)
    
    # Regime boundaries
    ax.axhline(0.1, color='g', linestyle='--', linewidth=1.5, alpha=0.7, label='Bi = 0.1')
    ax.axhline(10, color='r', linestyle='--', linewidth=1.5, alpha=0.7, label='Bi = 10')
    
    # Annotate regimes
    ax.text(10, 0.02, 'Weak Coupling\n(Bi << 1)', fontsize=10, color='green',
            ha='center', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    ax.text(1000, 0.02, 'Moderate\n(Bi ≈ 1)', fontsize=10, color='orange',
            ha='center', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    ax.text(100000, 0.02, 'Strong Coupling\n(Bi >> 1)', fontsize=10, color='red',
            ha='center', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    ax.set_xlabel('Heat Transfer Coefficient h (W/m²·K)', fontsize=12)
    ax.set_ylabel('Biot Number Bi', fontsize=12)
    ax.set_title('Biot Number Regimes', fontsize=14)
    ax.legend(fontsize=11, loc='upper left')
    ax.grid(True, alpha=0.3, which='both')
    ax.set_ylim(1e-3, 1e3)
    
    plt.tight_layout()
    plt.savefig('artifacts/fst_coupling/biot_regimes.png', dpi=150)
    plt.close()
    print("✓ Generated biot_regimes.png")


if __name__ == "__main__":
    print("Generating FST coupling verification figures...")
    print()
    
    plot_velocity_profile()
    plot_coupling_comparison()
    plot_dittus_boelter()
    plot_convergence_history()
    plot_thermal_stress()
    plot_biot_regimes()
    
    print()
    print("All figures generated successfully!")
    print("Figures saved to: artifacts/fst_coupling/")
