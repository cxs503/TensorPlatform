#!/usr/bin/env python3
"""Generate figures for the Ice Resistance UQ report."""

import sys
sys.path.insert(0, 'backend')

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from services.ice_resistance_uq import (
    run_ice_resistance_uq, run_all_ships_uq, SHIP_TRIAL_DATA,
    lindqvist_ice_resistance, UncertainInput, build_pce, pce_statistics,
    sobol_indices, monte_carlo_reference, smolyak_sparse_grid,
)

# Run UQ for all ships
results = run_all_ships_uq(max_level=3, max_poly_order=3)

# =====================================================================
# Figure 1: Sobol Sensitivity Indices (bar chart)
# =====================================================================
fig, axes = plt.subplots(1, 3, figsize=(15, 5))
ship_names = ["MV_Arctic", "Polarstern", "Terry_Fox"]
param_names = ["Ice Strength\n(MPa)", "Ice Thickness\n(m)", "Friction\nCoeff.", "Ship Speed\n(m/s)"]
colors_fo = ['#2196F3', '#4CAF50', '#FF9800', '#9C27B0']
colors_to = ['#64B5F6', '#81C784', '#FFB74D', '#CE93D8']

for idx, ship_name in enumerate(ship_names):
    r = results[ship_name]
    fo = [r.sobol_first_order[k] for k in ['ice_strength_MPa', 'ice_thickness_m', 'friction_coeff', 'ship_speed_ms']]
    to = [r.sobol_total_order[k] for k in ['ice_strength_MPa', 'ice_thickness_m', 'friction_coeff', 'ship_speed_ms']]

    x = np.arange(len(param_names))
    width = 0.35

    ax = axes[idx]
    bars1 = ax.bar(x - width/2, fo, width, label='First-order $S_i$', color=colors_fo, edgecolor='black', linewidth=0.5)
    bars2 = ax.bar(x + width/2, to, width, label='Total-order $S_{T_i}$', color=colors_to, edgecolor='black', linewidth=0.5)

    ax.set_ylabel('Sobol Index', fontsize=11)
    ax.set_title(ship_name.replace('_', ' '), fontsize=13, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(param_names, fontsize=9)
    ax.legend(fontsize=9)
    ax.set_ylim(0, 1.0)
    ax.grid(axis='y', alpha=0.3)

    # Add value labels
    for bar in bars1:
        h = bar.get_height()
        if h > 0.01:
            ax.text(bar.get_x() + bar.get_width()/2., h + 0.02, f'{h:.3f}', ha='center', va='bottom', fontsize=8)
    for bar in bars2:
        h = bar.get_height()
        if h > 0.01:
            ax.text(bar.get_x() + bar.get_width()/2., h + 0.02, f'{h:.3f}', ha='center', va='bottom', fontsize=8)

plt.suptitle('Sobol Sensitivity Indices for Ice Resistance UQ', fontsize=14, fontweight='bold', y=1.02)
plt.tight_layout()
plt.savefig('docs/ice_uq/fig1_sobol_indices.png', dpi=150, bbox_inches='tight')
plt.close()
print("Figure 1 saved: fig1_sobol_indices.png")

# =====================================================================
# Figure 2: PCE vs MC Validation (scatter + CI)
# =====================================================================
fig, axes = plt.subplots(1, 3, figsize=(15, 5))

for idx, ship_name in enumerate(ship_names):
    r = results[ship_name]
    trial = SHIP_TRIAL_DATA[ship_name]
    ship = trial["params"]

    uncertain_inputs = [
        UncertainInput("ice_strength_MPa", nominal=trial["nominal_ice_strength_MPa"], std_relative=0.20),
        UncertainInput("ice_thickness_m", nominal=trial["nominal_ice_thickness_m"], std_relative=0.15),
        UncertainInput("friction_coeff", nominal=trial["nominal_friction_coeff"], std_relative=0.25),
        UncertainInput("ship_speed_ms", nominal=trial["nominal_speed_ms"], std_relative=0.10),
    ]

    def ice_model(ice_strength, ice_thickness, friction, speed):
        return lindqvist_ice_resistance(ship, ice_thickness, ice_strength, friction, speed)

    pce = build_pce(ice_model, uncertain_inputs, max_level=3, max_poly_order=3)

    # MC samples
    rng = np.random.default_rng(42)
    n_samples = 5000
    mc_values = []
    pce_values = []
    for _ in range(n_samples):
        x_phys = np.array([inp.mean + inp.std * rng.standard_normal() for inp in uncertain_inputs])
        x_phys[0] = max(0.3, x_phys[0])
        x_phys[1] = max(0.1, x_phys[1])
        x_phys[2] = max(0.02, min(0.5, x_phys[2]))
        x_phys[3] = max(0.5, x_phys[3])

        y_mc = ice_model(*x_phys)
        x_std = np.array([(x_phys[d] - uncertain_inputs[d].mean) / uncertain_inputs[d].std for d in range(4)])
        y_pce_val = 0.0
        for j, alpha in enumerate(pce["multi_indices"]):
            psi = 1.0
            for d in range(4):
                h_prev2 = 1.0; h_prev1 = x_std[d]
                if alpha[d] == 0: psi *= 1.0
                elif alpha[d] == 1: psi *= x_std[d]
                else:
                    for n in range(1, alpha[d]):
                        h_curr = x_std[d] * h_prev1 - n * h_prev2
                        h_prev2 = h_prev1; h_prev1 = h_curr
                    psi *= h_prev1
            y_pce_val += pce["coefficients"][j] * psi

        mc_values.append(y_mc)
        pce_values.append(y_pce_val)

    ax = axes[idx]
    ax.scatter(mc_values, pce_values, alpha=0.3, s=8, c='#2196F3')

    # Perfect prediction line
    lims = [min(min(mc_values), min(pce_values)), max(max(mc_values), max(pce_values))]
    ax.plot(lims, lims, 'r--', linewidth=1.5, label='Perfect prediction')

    # CI band
    ax.axhline(y=r.ci_lower_kN, color='orange', linestyle=':', alpha=0.7, label=f'95% CI: [{r.ci_lower_kN:.0f}, {r.ci_upper_kN:.0f}] kN')
    ax.axhline(y=r.ci_upper_kN, color='orange', linestyle=':', alpha=0.7)

    ax.set_xlabel('MC Resistance (kN)', fontsize=11)
    ax.set_ylabel('PCE Resistance (kN)', fontsize=11)
    ax.set_title(ship_name.replace('_', ' '), fontsize=13, fontweight='bold')
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

plt.suptitle('PCE Surrogate vs Monte Carlo Validation', fontsize=14, fontweight='bold', y=1.02)
plt.tight_layout()
plt.savefig('docs/ice_uq/fig2_pce_vs_mc.png', dpi=150, bbox_inches='tight')
plt.close()
print("Figure 2 saved: fig2_pce_vs_mc.png")

# =====================================================================
# Figure 3: Resistance PDF (PCE-derived)
# =====================================================================
fig, axes = plt.subplots(1, 3, figsize=(15, 5))

for idx, ship_name in enumerate(ship_names):
    r = results[ship_name]
    trial = SHIP_TRIAL_DATA[ship_name]
    ship = trial["params"]

    uncertain_inputs = [
        UncertainInput("ice_strength_MPa", nominal=trial["nominal_ice_strength_MPa"], std_relative=0.20),
        UncertainInput("ice_thickness_m", nominal=trial["nominal_ice_thickness_m"], std_relative=0.15),
        UncertainInput("friction_coeff", nominal=trial["nominal_friction_coeff"], std_relative=0.25),
        UncertainInput("ship_speed_ms", nominal=trial["nominal_speed_ms"], std_relative=0.10),
    ]

    def ice_model(ice_strength, ice_thickness, friction, speed):
        return lindqvist_ice_resistance(ship, ice_thickness, ice_strength, friction, speed)

    # MC histogram
    mc_ref = monte_carlo_reference(ice_model, uncertain_inputs, n_samples=100000)

    rng = np.random.default_rng(42)
    mc_samples = []
    for _ in range(100000):
        x_phys = np.array([inp.mean + inp.std * rng.standard_normal() for inp in uncertain_inputs])
        x_phys[0] = max(0.3, x_phys[0])
        x_phys[1] = max(0.1, x_phys[1])
        x_phys[2] = max(0.02, min(0.5, x_phys[2]))
        x_phys[3] = max(0.5, x_phys[3])
        mc_samples.append(ice_model(*x_phys))

    ax = axes[idx]
    ax.hist(mc_samples, bins=60, density=True, alpha=0.6, color='#2196F3', label='MC histogram')

    # PCE normal approximation
    from scipy.stats import norm
    x_range = np.linspace(r.ci_lower_kN - 2*r.std_resistance_kN,
                          r.ci_upper_kN + 2*r.std_resistance_kN, 200)
    pdf_pce = norm.pdf(x_range, r.mean_resistance_kN, r.std_resistance_kN)
    ax.plot(x_range, pdf_pce, 'r-', linewidth=2, label='PCE Normal approx.')

    # Mark mean and CI
    ax.axvline(r.mean_resistance_kN, color='green', linestyle='-', linewidth=1.5, label=f'PCE mean={r.mean_resistance_kN:.0f} kN')
    ax.axvline(r.ci_lower_kN, color='orange', linestyle='--', linewidth=1, label=f'95% CI lower={r.ci_lower_kN:.0f} kN')
    ax.axvline(r.ci_upper_kN, color='orange', linestyle='--', linewidth=1, label=f'95% CI upper={r.ci_upper_kN:.0f} kN')

    # Trial mean
    ax.axvline(r.trial_mean_kN, color='purple', linestyle=':', linewidth=2, label=f'Trial mean={r.trial_mean_kN:.0f} kN')

    ax.set_xlabel('Ice Resistance (kN)', fontsize=11)
    ax.set_ylabel('Probability Density', fontsize=11)
    ax.set_title(ship_name.replace('_', ' '), fontsize=13, fontweight='bold')
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

plt.suptitle('Ice Resistance Probability Distribution (PCE vs MC)', fontsize=14, fontweight='bold', y=1.02)
plt.tight_layout()
plt.savefig('docs/ice_uq/fig3_resistance_pdf.png', dpi=150, bbox_inches='tight')
plt.close()
print("Figure 3 saved: fig3_resistance_pdf.png")

# =====================================================================
# Figure 4: Smolyak Sparse Grid Visualization (2D slice)
# =====================================================================
fig, axes = plt.subplots(1, 2, figsize=(12, 5))

# 2D grid
nodes_2d, weights_2d = smolyak_sparse_grid(3, 2)
ax = axes[0]
sizes = np.abs(weights_2d) * 500
ax.scatter(nodes_2d[:, 0], nodes_2d[:, 1], s=sizes, c=weights_2d, cmap='RdBu_r', edgecolors='black', linewidth=0.5)
ax.set_xlabel('$\\xi_1$ (Ice Strength)', fontsize=11)
ax.set_ylabel('$\\xi_2$ (Ice Thickness)', fontsize=11)
ax.set_title('2D Smolyak Sparse Grid (Level 3)', fontsize=13, fontweight='bold')
ax.grid(alpha=0.3)
ax.set_aspect('equal')
ax.text(0.02, 0.98, f'N = {len(weights_2d)} points', transform=ax.transAxes, fontsize=10, va='top',
        bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

# 4D grid projected to first 2 dims
nodes_4d, weights_4d = smolyak_sparse_grid(3, 4)
ax = axes[1]
sizes = np.abs(weights_4d) * 300
sc = ax.scatter(nodes_4d[:, 0], nodes_4d[:, 1], s=sizes, c=weights_4d, cmap='RdBu_r', edgecolors='black', linewidth=0.5, alpha=0.7)
ax.set_xlabel('$\\xi_1$ (Ice Strength)', fontsize=11)
ax.set_ylabel('$\\xi_2$ (Ice Thickness)', fontsize=11)
ax.set_title('4D Smolyak Grid Projected ($\\xi_1$ vs $\\xi_2$)', fontsize=13, fontweight='bold')
ax.grid(alpha=0.3)
ax.set_aspect('equal')
ax.text(0.02, 0.98, f'N = {len(weights_4d)} points (4D)', transform=ax.transAxes, fontsize=10, va='top',
        bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

plt.suptitle('Smolyak Sparse Grid Quadrature Points', fontsize=14, fontweight='bold', y=1.02)
plt.tight_layout()
plt.savefig('docs/ice_uq/fig4_smolyak_grid.png', dpi=150, bbox_inches='tight')
plt.close()
print("Figure 4 saved: fig4_smolyak_grid.png")

print("\nAll figures generated successfully!")
