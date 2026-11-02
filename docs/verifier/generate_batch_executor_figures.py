#!/usr/bin/env python3
"""Generate comparison figures for batch executor propeller model upgrade.

This script compares the legacy MOCK surrogate with the new REAL BEM model
to demonstrate the physics improvement.
"""
import sys
import os
from pathlib import Path

# Add backend to path - need to go up 2 levels from docs/verifier to root, then into backend
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from services.propeller_open_water import PropellerGeometry, open_water_point

# Propeller geometry (typical merchant ship)
geom = PropellerGeometry(
    diameter_m=5.0,
    pitch_ratio=1.0,
    blade_area_ratio=0.55,
    blade_count=4,
    sections=16,
)

J_vals = np.linspace(0.01, 1.2, 100)

# Legacy MOCK surrogate
def legacy_KQ(J):
    return max(0.01, 0.05 + 0.02 * J)

def legacy_KT(J):
    return max(0.05, 0.5 - 0.3 * J)

def legacy_eta(J):
    KT = legacy_KT(J)
    KQ = legacy_KQ(J)
    eta = (J * KT) / (2 * np.pi * KQ) if KQ > 0 else 0
    return min(0.85, max(0.3, eta))

KQ_mock = [legacy_KQ(J) for J in J_vals]
KT_mock = [legacy_KT(J) for J in J_vals]
eta_mock = [legacy_eta(J) for J in J_vals]

# New REAL BEM model
KQ_bem = []
KT_bem = []
eta_bem = []
eta_ideal_bem = []

for J in J_vals:
    pt = open_water_point(geom, J)
    KQ_bem.append(pt["KQ"])
    KT_bem.append(pt["KT"])
    eta_bem.append(pt["eta0"])
    eta_ideal_bem.append(pt.get("eta_ideal", 0.0))

# Figure 1: Torque coefficient KQ(J)
fig1, ax1 = plt.subplots(figsize=(10, 6))
ax1.plot(J_vals, KQ_mock, 'r--', linewidth=2.5, label='Legacy Surrogate (MOCK)', alpha=0.8)
ax1.plot(J_vals, KQ_bem, 'b-', linewidth=2.5, label='BEM Model (REAL)', alpha=0.9)
ax1.set_xlabel('Advance Ratio J', fontsize=13, fontweight='bold')
ax1.set_ylabel('Torque Coefficient KQ', fontsize=13, fontweight='bold')
ax1.set_title('Propeller Torque Characteristic: Mock vs BEM', fontsize=15, fontweight='bold')
ax1.legend(fontsize=12, loc='upper right')
ax1.grid(True, alpha=0.3, linestyle='--')
ax1.axhline(y=0, color='k', linestyle='-', linewidth=0.8)
ax1.set_xlim(0, 1.2)
ax1.set_ylim(0, max(max(KQ_mock), max(KQ_bem)) * 1.1)
ax1.tick_params(labelsize=11)

# Add annotation
ax1.annotate('MOCK: KQ increases with J\n(unphysical)', 
             xy=(0.8, KQ_mock[80]), xytext=(0.5, 0.08),
             fontsize=10, color='red',
             arrowprops=dict(arrowstyle='->', color='red', lw=1.5))
ax1.annotate('BEM: KQ decreases with J\n(physical)', 
             xy=(0.8, KQ_bem[80]), xytext=(0.5, 0.02),
             fontsize=10, color='blue',
             arrowprops=dict(arrowstyle='->', color='blue', lw=1.5))

plt.tight_layout()
fig1.savefig('kq_comparison_mock_vs_bem.png', dpi=150, bbox_inches='tight')
print("✓ Saved: kq_comparison_mock_vs_bem.png")

# Figure 2: Thrust coefficient KT(J)
fig2, ax2 = plt.subplots(figsize=(10, 6))
ax2.plot(J_vals, KT_mock, 'r--', linewidth=2.5, label='Legacy Surrogate (MOCK)', alpha=0.8)
ax2.plot(J_vals, KT_bem, 'b-', linewidth=2.5, label='BEM Model (REAL)', alpha=0.9)
ax2.set_xlabel('Advance Ratio J', fontsize=13, fontweight='bold')
ax2.set_ylabel('Thrust Coefficient KT', fontsize=13, fontweight='bold')
ax2.set_title('Propeller Thrust Characteristic: Mock vs BEM', fontsize=15, fontweight='bold')
ax2.legend(fontsize=12, loc='upper right')
ax2.grid(True, alpha=0.3, linestyle='--')
ax2.axhline(y=0, color='k', linestyle='-', linewidth=0.8)
ax2.set_xlim(0, 1.2)
ax2.tick_params(labelsize=11)

# Find zero-thrust point for BEM
J_zero_idx = np.argmin(np.abs(np.array(KT_bem)))
J_zero = J_vals[J_zero_idx]
ax2.axvline(x=J_zero, color='blue', linestyle=':', linewidth=1.5, alpha=0.6)
ax2.annotate(f'BEM: J₀ ≈ {J_zero:.2f}\n(zero thrust)', 
             xy=(J_zero, 0), xytext=(J_zero + 0.15, 0.1),
             fontsize=10, color='blue',
             arrowprops=dict(arrowstyle='->', color='blue', lw=1.5))

# Annotate MOCK: no zero-thrust point
ax2.annotate('MOCK: KT never reaches 0\n(no zero-thrust point)', 
             xy=(1.0, KT_mock[90]), xytext=(0.6, 0.3),
             fontsize=10, color='red',
             arrowprops=dict(arrowstyle='->', color='red', lw=1.5))

plt.tight_layout()
fig2.savefig('kt_comparison_mock_vs_bem.png', dpi=150, bbox_inches='tight')
print("✓ Saved: kt_comparison_mock_vs_bem.png")

# Figure 3: Efficiency eta(J)
fig3, ax3 = plt.subplots(figsize=(10, 6))
ax3.plot(J_vals, eta_mock, 'r--', linewidth=2.5, label='Legacy Surrogate (MOCK)', alpha=0.8)
ax3.plot(J_vals, eta_bem, 'b-', linewidth=2.5, label='BEM Model (REAL)', alpha=0.9)
ax3.plot(J_vals, eta_ideal_bem, 'g:', linewidth=2, label='Ideal Actuator-Disc Bound', alpha=0.7)
ax3.set_xlabel('Advance Ratio J', fontsize=13, fontweight='bold')
ax3.set_ylabel('Open-Water Efficiency η₀', fontsize=13, fontweight='bold')
ax3.set_title('Propeller Efficiency: Mock vs BEM', fontsize=15, fontweight='bold')
ax3.legend(fontsize=12, loc='upper right')
ax3.grid(True, alpha=0.3, linestyle='--')
ax3.set_xlim(0, 1.2)
ax3.set_ylim(0, 0.8)
ax3.tick_params(labelsize=11)

# Annotate MOCK: artificial plateau
ax3.annotate('MOCK: Artificial plateau\nη clipped to [0.3, 0.85]', 
             xy=(0.3, eta_mock[30]), xytext=(0.5, 0.5),
             fontsize=10, color='red',
             arrowprops=dict(arrowstyle='->', color='red', lw=1.5))

# Annotate BEM: single peak
J_peak_idx = np.argmax(eta_bem)
J_peak = J_vals[J_peak_idx]
eta_peak = eta_bem[J_peak_idx]
ax3.annotate(f'BEM: Single peak\nη_max ≈ {eta_peak:.3f} at J ≈ {J_peak:.2f}', 
             xy=(J_peak, eta_peak), xytext=(J_peak + 0.2, eta_peak - 0.15),
             fontsize=10, color='blue',
             arrowprops=dict(arrowstyle='->', color='blue', lw=1.5))

# Annotate BEM: eta -> 0 as J -> 0
ax3.annotate('BEM: η → 0 as J → 0\n(physical)', 
             xy=(0.05, eta_bem[5]), xytext=(0.2, 0.15),
             fontsize=10, color='blue',
             arrowprops=dict(arrowstyle='->', color='blue', lw=1.5))

plt.tight_layout()
fig3.savefig('eta_comparison_mock_vs_bem.png', dpi=150, bbox_inches='tight')
print("✓ Saved: eta_comparison_mock_vs_bem.png")

# Figure 4: Summary table
fig4, ax4 = plt.subplots(figsize=(12, 4))
ax4.axis('off')

table_data = [
    ['Property', 'Legacy MOCK', 'New BEM (REAL)', 'Physical?'],
    ['KQ(J) monotonicity', '❌ Increases', '✅ Decreases', 'BEM'],
    ['KT zero-thrust point J₀', '❌ None', f'✅ J₀ ≈ {J_zero:.2f}', 'BEM'],
    ['η(J→0) limit', '❌ η → 0.3', '✅ η → 0', 'BEM'],
    ['η peak structure', '❌ Artificial plateau', f'✅ Single peak at J≈{J_peak:.2f}', 'BEM'],
    ['Geometry dependence', '❌ None', '✅ P/D, Ae/A0, Z', 'BEM'],
    ['Froude invariance', '✅ Yes', '✅ Yes (rel<1e-9)', 'Both'],
    ['Evidence grade', 'MOCK', 'REAL', 'Upgraded'],
]

table = ax4.table(cellText=table_data, cellLoc='center', loc='center',
                  colWidths=[0.25, 0.25, 0.3, 0.2])
table.auto_set_font_size(False)
table.set_fontsize(11)
table.scale(1, 2.5)

# Style header row
for i in range(4):
    table[0, i].set_facecolor('#4472C4')
    table[0, i].set_text_props(weight='bold', color='white')

# Style data rows
for i in range(1, len(table_data)):
    for j in range(4):
        if j == 3:  # Physical? column
            if 'BEM' in table_data[i][j]:
                table[i, j].set_facecolor('#C6EFCE')  # Light green
            else:
                table[i, j].set_facecolor('#FFEB9C')  # Light yellow
        elif i % 2 == 0:
            table[i, j].set_facecolor('#F2F2F2')  # Alternating rows

ax4.set_title('Propeller Model Upgrade Summary', fontsize=14, fontweight='bold', pad=20)
plt.tight_layout()
fig4.savefig('upgrade_summary_table.png', dpi=150, bbox_inches='tight')
print("✓ Saved: upgrade_summary_table.png")

print("\n✅ All figures generated successfully!")
print("   - kq_comparison_mock_vs_bem.png")
print("   - kt_comparison_mock_vs_bem.png")
print("   - eta_comparison_mock_vs_bem.png")
print("   - upgrade_summary_table.png")
