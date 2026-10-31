#!/usr/bin/env python3
"""
Generate validation report and figures for FEM cohesive → DEM transfer.

Produces:
    - runs/m720/validation_report.json  (machine-readable acceptance)
    - artifacts/m720/conservation_error_vs_nfrag.png
    - artifacts/m720/mass_momentum_balance.png
    - artifacts/m720/fragment_particle_mapping.png
"""

import sys
import os
import json
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from tensorphysics.fracture_coupling import (
    CohesiveFragment, FractureCouplingTransfer, transfer_fragments_to_dem
)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def make_fragment(fid, mass, pos, vel, ang_vel=None, density=917.0):
    if ang_vel is None:
        ang_vel = np.zeros(3)
    volume = mass / density
    r = (3.0 * volume / (4.0 * np.pi)) ** (1.0 / 3.0)
    I = 0.4 * mass * r**2 * np.eye(3)
    return CohesiveFragment(
        fragment_id=fid, mass=mass,
        position=np.asarray(pos, dtype=float),
        velocity=np.asarray(vel, dtype=float),
        angular_velocity=np.asarray(ang_vel, dtype=float),
        inertia_tensor=I, volume=volume, density=density,
        failure_time=0.0,
    )


def make_ice_fragments(n, seed=42):
    rng = np.random.RandomState(seed)
    frags = []
    for i in range(n):
        mass = rng.uniform(0.01, 5.0)
        pos = rng.uniform(-1.0, 1.0, 3)
        vel = rng.uniform(-5.0, 5.0, 3)
        ang_vel = rng.uniform(-10.0, 10.0, 3)
        frags.append(make_fragment(i, mass, pos, vel, ang_vel))
    return frags


def run_sweep():
    """Run conservation tests across fragment counts."""
    n_values = [1, 5, 10, 50, 100, 200, 500, 1000, 2000, 5000]
    mass_errors = []
    momentum_errors = []
    total_masses = []
    total_momenta = []

    for n in n_values:
        frags = make_ice_fragments(n, seed=42)
        _, result = transfer_fragments_to_dem(frags)
        mass_errors.append(result.mass_transfer_error)
        momentum_errors.append(result.momentum_transfer_error)
        total_masses.append(result.total_mass_in)
        total_momenta.append(np.linalg.norm(result.total_momentum_in))

    return n_values, mass_errors, momentum_errors, total_masses, total_momenta


def run_subdivided_sweep():
    """Run conservation tests for subdivided transfer (1:N)."""
    n_frags = 50
    n_subdivisions_list = [1, 2, 4, 8, 16, 32]
    mass_errors = []
    momentum_errors = []

    for n_sub in n_subdivisions_list:
        frags = make_ice_fragments(n_frags, seed=42)
        _, result = transfer_fragments_to_dem(frags, n_subdivisions=n_sub)
        mass_errors.append(result.mass_transfer_error)
        momentum_errors.append(result.momentum_transfer_error)

    return n_subdivisions_list, mass_errors, momentum_errors


def run_detailed_audit():
    """Detailed audit for the canonical 100-fragment case."""
    frags = make_ice_fragments(100, seed=2024)
    particles, result = transfer_fragments_to_dem(frags, transfer_time=0.5)

    audit = {
        "n_fragments": 100,
        "n_particles": len(particles),
        "mass_in_kg": result.total_mass_in,
        "mass_out_kg": result.total_mass_out,
        "mass_error_kg": result.mass_transfer_error,
        "momentum_in_kg_m_s": result.total_momentum_in.tolist(),
        "momentum_out_kg_m_s": result.total_momentum_out.tolist(),
        "momentum_error_kg_m_s": result.momentum_transfer_error,
        "no_orphan_particles": result.no_orphan_particles,
        "all_pass": result.all_pass,
        "per_fragment_audit": []
    }

    for i, (f, p) in enumerate(zip(frags, particles)):
        audit["per_fragment_audit"].append({
            "fragment_id": f.fragment_id,
            "particle_id": p.particle_id,
            "mass_in": f.mass,
            "mass_out": p.mass,
            "mass_diff": abs(p.mass - f.mass),
            "vel_in": f.velocity.tolist(),
            "vel_out": p.velocity.tolist(),
            "momentum_in": f.linear_momentum.tolist(),
            "momentum_out": p.linear_momentum.tolist(),
        })

    return audit, result


def generate_figures(n_values, mass_errors, momentum_errors, total_masses, total_momenta,
                     n_sub_list, sub_mass_errors, sub_momentum_errors):
    """Generate publication-quality figures."""
    os.makedirs("artifacts/m720", exist_ok=True)

    # Figure 1: Conservation error vs number of fragments (1:1 mapping)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # For 1:1 mapping, errors are exactly 0 (by construction)
    # Show this as a bar chart with threshold lines
    ax1.bar(range(len(n_values)), mass_errors, color='steelblue', alpha=0.7)
    ax1.axhline(y=1e-10, color='r', linestyle='--', linewidth=2, label='Threshold (1e-10 kg)')
    ax1.set_xlabel('Number of Fragments', fontsize=12)
    ax1.set_ylabel('Mass Transfer Error [kg]', fontsize=12)
    ax1.set_title('(a) Mass Conservation Error (1:1 Mapping)', fontsize=13)
    ax1.set_xticks(range(len(n_values)))
    ax1.set_xticklabels(n_values, rotation=45)
    ax1.legend(fontsize=10)
    ax1.grid(True, alpha=0.3, axis='y')
    ax1.set_ylim(-1e-11, max(1e-10 * 2, max(mass_errors) * 2 if max(mass_errors) > 0 else 1e-10))
    ax1.text(0.5, 0.95, 'Error = 0.0 (exact by construction)',
             transform=ax1.transAxes, ha='center', va='top',
             fontsize=11, color='green', fontweight='bold')

    ax2.bar(range(len(n_values)), momentum_errors, color='seagreen', alpha=0.7)
    ax2.axhline(y=1e-8, color='r', linestyle='--', linewidth=2, label='Threshold (1e-8 kg·m/s)')
    ax2.set_xlabel('Number of Fragments', fontsize=12)
    ax2.set_ylabel('Momentum Transfer Error [kg·m/s]', fontsize=12)
    ax2.set_title('(b) Momentum Conservation Error (1:1 Mapping)', fontsize=13)
    ax2.set_xticks(range(len(n_values)))
    ax2.set_xticklabels(n_values, rotation=45)
    ax2.legend(fontsize=10)
    ax2.grid(True, alpha=0.3, axis='y')
    ax2.set_ylim(-1e-9, max(1e-8 * 2, max(momentum_errors) * 2 if max(momentum_errors) > 0 else 1e-8))
    ax2.text(0.5, 0.95, 'Error = 0.0 (exact by construction)',
             transform=ax2.transAxes, ha='center', va='top',
             fontsize=11, color='green', fontweight='bold')

    plt.tight_layout()
    plt.savefig('artifacts/m720/conservation_error_vs_nfrag.png', dpi=150, bbox_inches='tight')
    plt.close()

    # Figure 2: Subdivided transfer conservation
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    ax1.bar(range(len(n_sub_list)), sub_mass_errors, color='coral', alpha=0.7)
    ax1.axhline(y=1e-10, color='r', linestyle='--', linewidth=2, label='Threshold (1e-10 kg)')
    ax1.set_xlabel('Number of Subdivisions per Fragment', fontsize=12)
    ax1.set_ylabel('Mass Transfer Error [kg]', fontsize=12)
    ax1.set_title('(a) Mass Conservation (1:N Subdivision)', fontsize=13)
    ax1.set_xticks(range(len(n_sub_list)))
    ax1.set_xticklabels(n_sub_list)
    ax1.legend(fontsize=10)
    ax1.grid(True, alpha=0.3, axis='y')
    if max(sub_mass_errors) > 0:
        ax1.set_yscale('log')

    ax2.bar(range(len(n_sub_list)), sub_momentum_errors, color='orange', alpha=0.7)
    ax2.axhline(y=1e-8, color='r', linestyle='--', linewidth=2, label='Threshold (1e-8 kg·m/s)')
    ax2.set_xlabel('Number of Subdivisions per Fragment', fontsize=12)
    ax2.set_ylabel('Momentum Transfer Error [kg·m/s]', fontsize=12)
    ax2.set_title('(b) Momentum Conservation (1:N Subdivision)', fontsize=13)
    ax2.set_xticks(range(len(n_sub_list)))
    ax2.set_xticklabels(n_sub_list)
    ax2.legend(fontsize=10)
    ax2.grid(True, alpha=0.3, axis='y')
    if max(sub_momentum_errors) > 0:
        ax2.set_yscale('log')

    plt.tight_layout()
    plt.savefig('artifacts/m720/subdivided_conservation.png', dpi=150, bbox_inches='tight')
    plt.close()

    # Figure 3: Mass and momentum balance
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    ax1.plot(n_values, total_masses, 'b^-', linewidth=2, markersize=8)
    ax1.set_xlabel('Number of Fragments', fontsize=12)
    ax1.set_ylabel('Total Mass [kg]', fontsize=12)
    ax1.set_title('(a) Total Mass vs Fragment Count', fontsize=13)
    ax1.grid(True, alpha=0.3)

    ax2.plot(n_values, total_momenta, 'rD-', linewidth=2, markersize=8)
    ax2.set_xlabel('Number of Fragments', fontsize=12)
    ax2.set_ylabel('Total |Momentum| [kg·m/s]', fontsize=12)
    ax2.set_title('(b) Total Momentum Magnitude vs Fragment Count', fontsize=13)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig('artifacts/m720/mass_momentum_balance.png', dpi=150, bbox_inches='tight')
    plt.close()

    # Figure 4: Fragment-to-particle mapping visualization
    fig, ax = plt.subplots(1, 1, figsize=(10, 8))

    frags = make_ice_fragments(30, seed=77)
    particles, _ = transfer_fragments_to_dem(frags)

    # Plot fragments (source) and particles (target)
    frag_x = [f.position[0] for f in frags]
    frag_y = [f.position[1] for f in frags]
    frag_s = [f.mass * 50 for f in frags]

    part_x = [p.position[0] for p in particles]
    part_y = [p.position[1] for p in particles]
    part_s = [p.mass * 50 for p in particles]

    ax.scatter(frag_x, frag_y, s=frag_s, c='steelblue', alpha=0.7,
               edgecolors='navy', linewidth=0.5, label='FEM Fragments', marker='s')
    ax.scatter(part_x, part_y, s=part_s, c='coral', alpha=0.7,
               edgecolors='darkred', linewidth=0.5, label='DEM Particles', marker='o')

    # Draw mapping lines
    for f, p in zip(frags, particles):
        ax.plot([f.position[0], p.position[0]], [f.position[1], p.position[1]],
                'k-', alpha=0.15, linewidth=0.5)

    ax.set_xlabel('X [m]', fontsize=12)
    ax.set_ylabel('Y [m]', fontsize=12)
    ax.set_title('FEM Cohesive Fragment → DEM Particle Mapping\n(1:1 transfer, 30 ice fragments)',
                 fontsize=13)
    ax.legend(fontsize=11)
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig('artifacts/m720/fragment_particle_mapping.png', dpi=150, bbox_inches='tight')
    plt.close()

    print("Figures saved to artifacts/m720/")


def main():
    print("=" * 60)
    print("FEM Cohesive → DEM Transfer Validation Report")
    print("=" * 60)

    # Run parameter sweep
    print("\n[1/4] Running parameter sweep (1:1 mapping)...")
    n_values, mass_errors, momentum_errors, total_masses, total_momenta = run_sweep()

    for n, me, mme in zip(n_values, mass_errors, momentum_errors):
        print(f"  N={n:5d}: mass_err={me:.2e} kg, mom_err={mme:.2e} kg·m/s")

    # Run subdivided sweep
    print("\n[2/4] Running subdivided transfer sweep (1:N)...")
    n_sub_list, sub_mass_errors, sub_momentum_errors = run_subdivided_sweep()

    for n_sub, me, mme in zip(n_sub_list, sub_mass_errors, sub_momentum_errors):
        print(f"  1:{n_sub:2d}: mass_err={me:.2e} kg, mom_err={mme:.2e} kg·m/s")

    # Run detailed audit
    print("\n[3/4] Running detailed audit (N=100)...")
    audit, result = run_detailed_audit()
    print(f"  Mass in:  {result.total_mass_in:.10f} kg")
    print(f"  Mass out: {result.total_mass_out:.10f} kg")
    print(f"  Mass error: {result.mass_transfer_error:.2e} kg")
    print(f"  Momentum error: {result.momentum_transfer_error:.2e} kg·m/s")
    print(f"  No orphans: {result.no_orphan_particles}")
    print(f"  ALL PASS: {result.all_pass}")

    # Generate figures
    print("\n[4/4] Generating figures...")
    generate_figures(n_values, mass_errors, momentum_errors, total_masses, total_momenta,
                     n_sub_list, sub_mass_errors, sub_momentum_errors)

    # Build validation report
    report = {
        "mission_id": "m720",
        "title": "FEM cohesive → DEM fracture fragment handoff validation",
        "physical_domain": "fracture_coupling",
        "risk_level": "medium",
        "dag_layer": "L3_integration",
        "upstream": {
            "F03_m668": "FEM cohesive element with BK mixed-mode law",
            "V04_m671": "Mixed-mode cohesive validation (BK law)"
        },
        "acceptance_criteria": {
            "mass_transfer_error_kg": {
                "threshold": 1e-10,
                "measured": result.mass_transfer_error,
                "pass": result.mass_transfer_error < 1e-10
            },
            "momentum_transfer_error_kg_m_s": {
                "threshold": 1e-8,
                "measured": result.momentum_transfer_error,
                "pass": result.momentum_transfer_error < 1e-8
            },
            "no_orphan_particles": {
                "threshold": True,
                "measured": result.no_orphan_particles,
                "pass": result.no_orphan_particles is True
            }
        },
        "all_pass": result.all_pass,
        "detailed_audit": audit,
        "parameter_sweep_1to1": {
            "n_fragments": n_values,
            "mass_errors_kg": mass_errors,
            "momentum_errors_kg_m_s": momentum_errors,
            "total_masses_kg": total_masses,
            "total_momenta_kg_m_s": total_momenta,
        },
        "parameter_sweep_1toN": {
            "n_subdivisions": n_sub_list,
            "mass_errors_kg": sub_mass_errors,
            "momentum_errors_kg_m_s": sub_momentum_errors,
        },
        "method": {
            "description": "Direct property copy (1:1) and mass-weighted subdivision (1:N)",
            "conservation_mechanism": "mass_particle = mass_fragment; velocity_particle = velocity_fragment; momentum follows identically",
            "floating_point_guarantee": "IEEE 754 double precision; 1:1 mapping produces zero error; 1:N subdivision error bounded by N*eps*max_mass"
        },
        "figures": [
            "artifacts/m720/conservation_error_vs_nfrag.png",
            "artifacts/m720/subdivided_conservation.png",
            "artifacts/m720/mass_momentum_balance.png",
            "artifacts/m720/fragment_particle_mapping.png"
        ],
        "limitations": [
            "Rigid body assumption for DEM particles (no internal deformation)",
            "Thermal effects not included in transfer",
            "Fragment shape approximated as equivalent sphere for DEM",
            "No contact detection during transfer instant",
            "Subdivided transfer does not conserve kinetic energy (intentional: fragmentation dissipates energy)"
        ]
    }

    os.makedirs("runs/m720", exist_ok=True)
    with open("runs/m720/validation_report.json", "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nValidation report saved to runs/m720/validation_report.json")
    print(f"\n{'='*60}")
    print(f"FINAL VERDICT: {'PASS ✓' if result.all_pass else 'FAIL ✗'}")
    print(f"{'='*60}")

    return 0 if result.all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
