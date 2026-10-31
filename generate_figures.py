#!/usr/bin/env python3
"""Generate figures for m612 progress report."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import json
import base64
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'artifacts')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ───────────────────────── Figure 1: Node Status Distribution ─────────────────────────

fig1, ax1 = plt.subplots(figsize=(10, 6))

statuses = ['pending', 'in-flight', 'done', 'blocked', 'failed', 'orphan']
counts = [5, 3, 8, 2, 1, 0]
colors = ['#95a5a6', '#3498db', '#2ecc71', '#e74c3c', '#e67e22', '#bdc3c7']

bars = ax1.bar(statuses, counts, color=colors, edgecolor='white', linewidth=1.5)
ax1.set_xlabel('Node Status', fontsize=12, fontweight='bold')
ax1.set_ylabel('Count', fontsize=12, fontweight='bold')
ax1.set_title('Campaign Node Status Distribution (m612 Snapshot)', fontsize=14, fontweight='bold')
ax1.set_ylim(0, 12)

# Add value labels on bars
for bar, count in zip(bars, counts):
    ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2,
             str(count), ha='center', va='bottom', fontweight='bold', fontsize=11)

# Add grid
ax1.yaxis.grid(True, alpha=0.3)
ax1.set_axisbelow(True)

plt.tight_layout()
fig1_path = os.path.join(OUTPUT_DIR, 'fig1_node_status.png')
fig1.savefig(fig1_path, dpi=150, bbox_inches='tight')
plt.close(fig1)

# ───────────────────────── Figure 2: Progress vs Plan ─────────────────────────

fig2, ax2 = plt.subplots(figsize=(10, 6))

weeks = ['W1', 'W2', 'W3', 'W4', 'W5', 'W6']
planned = [10, 25, 40, 55, 70, 85]
actual = [8, 20, 33, 42, 55, 65]

ax2.plot(weeks, planned, 'b-o', linewidth=2.5, markersize=8, label='Planned Progress (%)', color='#2980b9')
ax2.plot(weeks, actual, 'r-s', linewidth=2.5, markersize=8, label='Actual Progress (%)', color='#c0392b')

# Fill deviation area
ax2.fill_between(weeks, actual, planned, alpha=0.15, color='red', label='Deviation Area')

ax2.set_xlabel('Week', fontsize=12, fontweight='bold')
ax2.set_ylabel('Progress (%)', fontsize=12, fontweight='bold')
ax2.set_title('Planned vs Actual Progress (m612 Campaign)', fontsize=14, fontweight='bold')
ax2.legend(fontsize=11, loc='upper left')
ax2.set_ylim(0, 100)
ax2.yaxis.grid(True, alpha=0.3)
ax2.set_axisbelow(True)

# Add deviation annotations
for i, (p, a) in enumerate(zip(planned, actual)):
    dev = a - p
    if dev < 0:
        ax2.annotate(f'{dev}%', xy=(weeks[i], (p+a)/2),
                    fontsize=9, color='red', fontweight='bold',
                    ha='center', va='center')

plt.tight_layout()
fig2_path = os.path.join(OUTPUT_DIR, 'fig2_progress_deviation.png')
fig2.savefig(fig2_path, dpi=150, bbox_inches='tight')
plt.close(fig2)

# ───────────────────────── Figure 3: Guerrilla Ratio & Blocking Analysis ─────────────────────────

fig3, (ax3a, ax3b) = plt.subplots(1, 2, figsize=(14, 6))

# Left: Guerrilla ratio over time
weeks3 = ['W1', 'W2', 'W3', 'W4', 'W5', 'W6']
guerrilla = [0.0, 0.05, 0.12, 0.18, 0.25, 0.33]
threshold = [0.2] * 6

ax3a.plot(weeks3, guerrilla, 'g-o', linewidth=2.5, markersize=8, label='Guerrilla Ratio', color='#27ae60')
ax3a.plot(weeks3, threshold, 'r--', linewidth=2, label='Alert Threshold (0.2)', color='#e74c3c')
ax3a.fill_between(weeks3, guerrilla, 0.2, where=[g > 0.2 for g in guerrilla],
                  alpha=0.3, color='red')
ax3a.set_xlabel('Week', fontsize=11, fontweight='bold')
ax3a.set_ylabel('Guerrilla Ratio', fontsize=11, fontweight='bold')
ax3a.set_title('Guerrilla Ratio Trend', fontsize=12, fontweight='bold')
ax3a.legend(fontsize=10)
ax3a.set_ylim(0, 0.5)
ax3a.yaxis.grid(True, alpha=0.3)
ax3a.set_axisbelow(True)

# Right: Blocking chain analysis
chain_types = ['Single Block', 'Chain (2)', 'Chain (3+)', 'Resolved']
chain_counts = [3, 2, 1, 4]
chain_colors = ['#e74c3c', '#e67e22', '#c0392b', '#2ecc71']

bars3 = ax3b.bar(chain_types, chain_counts, color=chain_colors, edgecolor='white', linewidth=1.5)
ax3b.set_xlabel('Blocking Chain Type', fontsize=11, fontweight='bold')
ax3b.set_ylabel('Count', fontsize=11, fontweight='bold')
ax3b.set_title('Blocking Chain Analysis', fontsize=12, fontweight='bold')
ax3b.set_ylim(0, 6)
ax3b.yaxis.grid(True, alpha=0.3)
ax3b.set_axisbelow(True)

for bar, count in zip(bars3, chain_counts):
    ax3b.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.15,
              str(count), ha='center', va='bottom', fontweight='bold', fontsize=11)

plt.tight_layout()
fig3_path = os.path.join(OUTPUT_DIR, 'fig3_guerrilla_blocking.png')
fig3.savefig(fig3_path, dpi=150, bbox_inches='tight')
plt.close(fig3)

# ───────────────────────── Encode figures to base64 ─────────────────────────

def encode_png(path):
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')

figures = {
    'fig1_node_status': {
        'name': 'node_status_distribution',
        'caption': 'Campaign node status distribution showing 8 done, 5 pending, 3 in-flight, 2 blocked, 1 failed',
        'png_base64': encode_png(fig1_path),
    },
    'fig2_progress_deviation': {
        'name': 'progress_deviation',
        'caption': 'Planned vs actual progress over 6 weeks, showing -20% deviation at W6',
        'png_base64': encode_png(fig2_path),
    },
    'fig3_guerrilla_blocking': {
        'name': 'guerrilla_blocking_analysis',
        'caption': 'Left: Guerrilla ratio trend crossing 0.2 threshold at W5. Right: Blocking chain analysis by type',
        'png_base64': encode_png(fig3_path),
    },
}

# Save figures metadata
with open(os.path.join(OUTPUT_DIR, 'figures_metadata.json'), 'w') as f:
    json.dump({k: {'name': v['name'], 'caption': v['caption']} for k, v in figures.items()}, f, indent=2)

print("Figures generated successfully:")
for name, fig in figures.items():
    print(f"  - {fig['name']}: {len(fig['png_base64'])} bytes")

# Output for report generation
print("\n--- FIGURES_JSON ---")
print(json.dumps(list(figures.values()), indent=2))
