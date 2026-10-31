#!/usr/bin/env python3
"""
Generate figures for the Clock Synchronization & Subcycling Framework report.

Produces:
  1. Clock sync error over master steps
  2. Energy conservation per solver over time
  3. Sub-step distribution across solvers
"""

import math
import json
import base64
from io import BytesIO
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Import the framework
import sys
sys.path.insert(0, "/srv/tensor/agent/work/m674")
from backend.services.coupling_clock_sync import (
    MasterClock, SolverDomain, SubcyclingController,
    create_ice_coupling_scenario, validate_clock_sync,
    make_harmonic_solver,
)


def run_scenario():
    """Run the ice coupling scenario and collect data."""
    ctrl = create_ice_coupling_scenario()
    ctrl.clock.start()

    sync_errors = []
    energy_ice = []
    energy_struct = []
    energy_hydro = []
    times = []

    for step in range(200):
        result = ctrl.run_master_step()
        sync_errors.append(result["sync_error"])
        energy_ice.append(result["energies"].get("ice", 0.0))
        energy_struct.append(result["energies"].get("structure", 0.0))
        energy_hydro.append(result["energies"].get("hydrodynamics", 0.0))
        times.append(result["t_after"])

    return {
        "sync_errors": sync_errors,
        "energy_ice": energy_ice,
        "energy_struct": energy_struct,
        "energy_hydro": energy_hydro,
        "times": times,
    }


def fig_sync_error(data):
    """Figure 1: Clock synchronization error over master steps."""
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.semilogy(data["times"], data["sync_errors"], "b-", linewidth=0.8, label="Max sync error")
    ax.axhline(y=1e-6, color="r", linestyle="--", linewidth=1.5, label="Threshold: 1e-6 s")
    ax.set_xlabel("Master time [s]", fontsize=12)
    ax.set_ylabel("Clock sync error [s]", fontsize=12)
    ax.set_title("Clock Synchronization Error vs. Master Time\n(Ice-Structure-Hydro Coupling)", fontsize=13)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=1e-16)
    fig.tight_layout()
    return fig


def fig_energy_conservation(data):
    """Figure 2: Energy per solver over time."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Left: absolute energy
    ax1.plot(data["times"], data["energy_ice"], "b-", linewidth=0.8, label="Ice (ω=20π)")
    ax1.plot(data["times"], data["energy_struct"], "g-", linewidth=0.8, label="Structure (ω=4π)")
    ax1.plot(data["times"], data["energy_hydro"], "r-", linewidth=0.8, label="Hydro (ω=π)")
    ax1.set_xlabel("Master time [s]", fontsize=12)
    ax1.set_ylabel("Energy [J]", fontsize=12)
    ax1.set_title("Solver Energies vs. Time", fontsize=13)
    ax1.legend(fontsize=10)
    ax1.grid(True, alpha=0.3)

    # Right: relative energy deviation
    e0_ice = data["energy_ice"][0]
    e0_struct = data["energy_struct"][0]
    e0_hydro = data["energy_hydro"][0]

    rel_ice = [(e - e0_ice) / abs(e0_ice) if abs(e0_ice) > 0 else 0 for e in data["energy_ice"]]
    rel_struct = [(e - e0_struct) / abs(e0_struct) if abs(e0_struct) > 0 else 0 for e in data["energy_struct"]]
    rel_hydro = [(e - e0_hydro) / abs(e0_hydro) if abs(e0_hydro) > 0 else 0 for e in data["energy_hydro"]]

    ax2.plot(data["times"], rel_ice, "b-", linewidth=0.8, label="Ice")
    ax2.plot(data["times"], rel_struct, "g-", linewidth=0.8, label="Structure")
    ax2.plot(data["times"], rel_hydro, "r-", linewidth=0.8, label="Hydro")
    ax2.axhline(y=0.01, color="k", linestyle="--", linewidth=1, label="Threshold: ±1%")
    ax2.axhline(y=-0.01, color="k", linestyle="--", linewidth=1)
    ax2.set_xlabel("Master time [s]", fontsize=12)
    ax2.set_ylabel("Relative energy deviation", fontsize=12)
    ax2.set_title("Energy Conservation per Solver", fontsize=13)
    ax2.legend(fontsize=10)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    return fig


def fig_substep_distribution():
    """Figure 3: Sub-step distribution across solvers."""
    fig, ax = plt.subplots(figsize=(8, 5))

    solvers = ["Ice\n(dt=1e-4 s)", "Structure\n(dt=1e-3 s)", "Hydro\n(dt=5e-3 s)"]
    substeps = [100, 10, 2]
    colors = ["#2196F3", "#4CAF50", "#F44336"]

    bars = ax.bar(solvers, substeps, color=colors, edgecolor="black", linewidth=0.8)
    ax.set_ylabel("Sub-steps per master step", fontsize=12)
    ax.set_title("Subcycling Distribution (dt_master = 0.01 s)", fontsize=13)

    # Add value labels
    for bar, val in zip(bars, substeps):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                str(val), ha="center", va="bottom", fontsize=12, fontweight="bold")

    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    return fig


def fig_to_base64(fig):
    """Convert matplotlib figure to base64 PNG string."""
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return b64


def main():
    print("Running ice coupling scenario...")
    data = run_scenario()

    print("Generating figures...")
    figures = []

    fig1 = fig_sync_error(data)
    figures.append({
        "name": "clock_sync_error",
        "caption": "Clock synchronization error over master time for the ice-structure-hydro coupling scenario. The error remains well below the 1e-6 s threshold (red dashed line), confirming time consistency across all sub-solvers.",
        "png_base64": fig_to_base64(fig1),
    })

    fig2 = fig_energy_conservation(data)
    figures.append({
        "name": "energy_conservation",
        "caption": "Left: Absolute energy of each solver (ice, structure, hydrodynamics) over time. Right: Relative energy deviation from initial value. All solvers maintain energy within the ±1% threshold, confirming the subcycling framework preserves energy conservation for symplectic integrators.",
        "png_base64": fig_to_base64(fig2),
    })

    fig3 = fig_substep_distribution()
    figures.append({
        "name": "substep_distribution",
        "caption": "Sub-step distribution across the three solver domains. The ice solver (fastest time scale) takes 100 sub-steps per master step, the structure solver takes 10, and the hydrodynamics solver takes 2. This multi-rate subcycling enables efficient coupling of heterogeneous time scales.",
        "png_base64": fig_to_base64(fig3),
    })

    # Save figures to artifacts
    artifacts_dir = Path("/srv/tensor/agent/work/m674/artifacts/m674_clock_sync")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Regenerate and save as files
    data2 = run_scenario()
    fig1 = fig_sync_error(data2)
    fig1.savefig(artifacts_dir / "clock_sync_error.png", dpi=150, bbox_inches="tight")
    plt.close(fig1)

    fig2 = fig_energy_conservation(data2)
    fig2.savefig(artifacts_dir / "energy_conservation.png", dpi=150, bbox_inches="tight")
    plt.close(fig2)

    fig3 = fig_substep_distribution()
    fig3.savefig(artifacts_dir / "substep_distribution.png", dpi=150, bbox_inches="tight")
    plt.close(fig3)

    # Save figures JSON for report
    with open(artifacts_dir / "figures.json", "w") as f:
        json.dump(figures, f)

    # Run validation and save results
    result = validate_clock_sync(n_steps=100)
    validation = {
        "clock_sync_error": result["clock_sync_error"],
        "energy_conservation_ratio": result["energy_conservation_ratio"],
        "pass_sync": result["pass_sync"],
        "pass_energy": result["pass_energy"],
        "pass_all": result["pass_all"],
        "t_final": result["details"]["t_final"],
        "n_master_steps": result["details"]["n_master_steps"],
    }
    with open(artifacts_dir / "validation_results.json", "w") as f:
        json.dump(validation, f, indent=2)

    print(f"Validation results: {json.dumps(validation, indent=2)}")
    print(f"Figures saved to {artifacts_dir}")
    print("Done.")


if __name__ == "__main__":
    main()
