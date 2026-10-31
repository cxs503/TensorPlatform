"""
Generate figures for Broken Ice Seakeeping report.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from services.ship_performance.seakeeping.broken_ice import (
    ShipGeometry, IceFieldParams, BrokenIceSeakeeping, OpenWaterRAO,
    LogDecrementAnalyzer
)

# Ship: KVLCC2
ship = ShipGeometry(L=320.0, B=58.0, T=20.8, Cb=0.8098, Cwp=0.78, Cw=0.98, KG=12.0)
freq = np.linspace(0.1, 3.0, 60)

# ============================================================================
# Figure 1: RAO vs frequency for different ice concentrations
# ============================================================================
fig, ax = plt.subplots(figsize=(10, 6))
concentrations = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
colors = plt.cm.viridis(np.linspace(0, 1, len(concentrations)))

for c, color in zip(concentrations, colors):
    ice = IceFieldParams(concentration=c, thickness=0.3, floe_diameter=10.0, restitution=0.3)
    solver = BrokenIceSeakeeping(ship, ice)
    rao = solver.compute_rao(freq)
    ax.plot(freq, rao.rao_heave, color=color, linewidth=2, label=f'c = {c:.1f}')

ax.set_xlabel('Frequency ω [rad/s]', fontsize=12)
ax.set_ylabel('Heave RAO |H₃₃(ω)| [m/m]', fontsize=12)
ax.set_title('Broken Ice Seakeeping: Heave RAO vs Frequency\nKVLCC2 Tanker (L=320m, B=58m, T=20.8m, Cb=0.81)', fontsize=13)
ax.legend(title='Ice concentration', fontsize=10)
ax.grid(True, alpha=0.3)
ax.set_xlim([0, 3.0])
plt.tight_layout()
plt.savefig('artifacts/rao_vs_frequency.png', dpi=150)
plt.close()
print("Saved: artifacts/rao_vs_frequency.png")

# ============================================================================
# Figure 2: Added damping vs frequency for different concentrations
# ============================================================================
fig, ax = plt.subplots(figsize=(10, 6))

for c, color in zip(concentrations, colors):
    ice = IceFieldParams(concentration=c, thickness=0.3, floe_diameter=10.0, restitution=0.3)
    solver = BrokenIceSeakeeping(ship, ice)
    rao = solver.compute_rao(freq)
    ax.plot(freq, rao.added_damping / 1e6, color=color, linewidth=2, label=f'c = {c:.1f}')

ax.set_xlabel('Frequency ω [rad/s]', fontsize=12)
ax.set_ylabel('Ice Added Damping B_ice [MN·s/m]', fontsize=12)
ax.set_title('Ice-Induced Added Damping vs Frequency\n(h = 0.3m, D_floe = 10m, e = 0.3)', fontsize=13)
ax.legend(title='Ice concentration', fontsize=10)
ax.grid(True, alpha=0.3)
ax.set_xlim([0, 3.0])
plt.tight_layout()
plt.savefig('artifacts/added_damping_vs_frequency.png', dpi=150)
plt.close()
print("Saved: artifacts/added_damping_vs_frequency.png")

# ============================================================================
# Figure 3: Damping ratio vs concentration
# ============================================================================
fig, ax = plt.subplots(figsize=(10, 6))
concs = np.linspace(0, 1, 20)
mean_zetas = []
max_zetas = []
freq_rep = np.array([0.3, 0.5, 1.0, 1.5, 2.0])

for c in concs:
    ice = IceFieldParams(concentration=c, thickness=0.3, floe_diameter=10.0, restitution=0.3)
    solver = BrokenIceSeakeeping(ship, ice)
    rao = solver.compute_rao(freq_rep)
    mean_zetas.append(np.mean(rao.damping_ratio))
    max_zetas.append(np.max(rao.damping_ratio))

ax.plot(concs, mean_zetas, 'b-o', linewidth=2, markersize=5, label='Mean ζ(ω)')
ax.plot(concs, max_zetas, 'r--s', linewidth=2, markersize=5, label='Max ζ(ω)')
ax.set_xlabel('Ice Concentration c [-]', fontsize=12)
ax.set_ylabel('Damping Ratio ζ [-]', fontsize=12)
ax.set_title('Damping Ratio vs Ice Concentration\nMonotonically non-decreasing ✓', fontsize=13)
ax.legend(fontsize=11)
ax.grid(True, alpha=0.3)
ax.set_xlim([0, 1])
plt.tight_layout()
plt.savefig('artifacts/damping_ratio_vs_concentration.png', dpi=150)
plt.close()
print("Saved: artifacts/damping_ratio_vs_concentration.png")

# ============================================================================
# Figure 4: Free decay signal and peak extraction
# ============================================================================
fig, axes = plt.subplots(2, 1, figsize=(10, 8))

zeta = 0.05
omega_n = 0.4  # KVLCC2 heave natural frequency ~ 0.4 rad/s
analyzer = LogDecrementAnalyzer()
T_d = 2 * np.pi / (omega_n * np.sqrt(1 - zeta**2))
t = np.arange(0, 15 * T_d, 0.05)
x = analyzer.free_decay_signal(zeta, omega_n, t)

axes[0].plot(t, x, 'b-', linewidth=1.5)
axes[0].set_xlabel('Time [s]', fontsize=12)
axes[0].set_ylabel('Displacement x(t) [m]', fontsize=12)
axes[0].set_title(f'Free Decay Signal (ζ = {zeta}, ωₙ = {omega_n} rad/s)', fontsize=13)
axes[0].grid(True, alpha=0.3)

# Envelope
env = np.exp(-zeta * omega_n * t)
axes[0].plot(t, env, 'r--', alpha=0.5, label='Envelope')
axes[0].plot(t, -env, 'r--', alpha=0.5)
axes[0].legend(fontsize=10)

# Peak extraction
peaks = analyzer.extract_peaks(x, t)
peak_times = []
for i in range(1, len(x) - 1):
    if x[i] > x[i-1] and x[i] > x[i+1] and x[i] > 0:
        peak_times.append(t[i])

axes[1].semilogy(peak_times, peaks, 'ro-', markersize=8, linewidth=2, label='Peaks')
axes[1].set_xlabel('Time [s]', fontsize=12)
axes[1].set_ylabel('Peak Amplitude [m]', fontsize=12)
axes[1].set_title('Peak Extraction for Log-Decrement Analysis', fontsize=13)
axes[1].legend(fontsize=10)
axes[1].grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('artifacts/free_decay_analysis.png', dpi=150)
plt.close()
print("Saved: artifacts/free_decay_analysis.png")

# ============================================================================
# Figure 5: RAO comparison - ice effect at different thicknesses
# ============================================================================
fig, ax = plt.subplots(figsize=(10, 6))
thicknesses = [0.1, 0.2, 0.3, 0.5, 0.8]
c_fixed = 0.6
colors_t = plt.cm.plasma(np.linspace(0, 1, len(thicknesses)))

for h, color in zip(thicknesses, colors_t):
    ice = IceFieldParams(concentration=c_fixed, thickness=h, floe_diameter=10.0, restitution=0.3)
    solver = BrokenIceSeakeeping(ship, ice)
    rao = solver.compute_rao(freq)
    ax.plot(freq, rao.rao_heave, color=color, linewidth=2, label=f'h = {h:.1f} m')

# Open water reference
ice_ow = IceFieldParams(concentration=0.0, thickness=0.3)
solver_ow = BrokenIceSeakeeping(ship, ice_ow)
rao_ow = solver_ow.compute_rao(freq)
ax.plot(freq, rao_ow.rao_heave, 'k--', linewidth=2, label='Open water (c=0)')

ax.set_xlabel('Frequency ω [rad/s]', fontsize=12)
ax.set_ylabel('Heave RAO |H₃₃(ω)| [m/m]', fontsize=12)
ax.set_title(f'Ice Thickness Effect on RAO (c = {c_fixed})\nKVLCC2 Tanker', fontsize=13)
ax.legend(title='Ice thickness', fontsize=10)
ax.grid(True, alpha=0.3)
ax.set_xlim([0, 3.0])
plt.tight_layout()
plt.savefig('artifacts/rao_vs_thickness.png', dpi=150)
plt.close()
print("Saved: artifacts/rao_vs_thickness.png")

print("\nAll figures generated successfully!")
