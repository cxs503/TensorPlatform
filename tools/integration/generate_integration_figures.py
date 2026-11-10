#!/usr/bin/env python3
"""Generate integration summary figures for Campaign P180W1."""
import json
import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'artifacts', 'integration')
os.makedirs(OUT_DIR, exist_ok=True)

# Load integration data
with open(os.path.join(OUT_DIR, 'completeness.json')) as f:
    completeness = json.load(f)
with open(os.path.join(OUT_DIR, 'knowledge_graph.json')) as f:
    kg = json.load(f)
with open(os.path.join(OUT_DIR, 'bp_alignment.json')) as f:
    bp = json.load(f)

# ── Figure 1: Node Completeness Dashboard ──
fig, axes = plt.subplots(1, 3, figsize=(16, 5))

# 1a: JSON and PNG counts per node
node_ids = [nr['node_id'] for nr in completeness['node_reports']]
json_counts = [nr['json_count'] for nr in completeness['node_reports']]
png_counts = [nr['png_count'] for nr in completeness['node_reports']]

x = np.arange(len(node_ids))
axes[0].bar(x - 0.15, json_counts, 0.3, label='JSON files', color='steelblue')
axes[0].bar(x + 0.15, png_counts, 0.3, label='PNG files', color='coral')
axes[0].set_xticks(x)
axes[0].set_xticklabels(node_ids, rotation=45, ha='right', fontsize=8)
axes[0].set_ylabel('File count')
axes[0].set_title('(a) Artifact File Counts per Node')
axes[0].legend(fontsize=8)
axes[0].grid(True, alpha=0.3, axis='y')

# 1b: Validation pass/fail
val_pass = [1 if nr.get('has_validation_json') else 0 for nr in completeness['node_reports']]
axes[1].bar(node_ids, val_pass, color=['green' if v else 'red' for v in val_pass], alpha=0.7)
axes[1].set_ylabel('Validation present')
axes[1].set_title('(b) Validation Status')
axes[1].set_ylim(0, 1.5)
axes[1].set_xticklabels(node_ids, rotation=45, ha='right', fontsize=8)

# 1c: Overall completeness pie
complete = completeness['nodes_complete']
incomplete = completeness['total_nodes_expected'] - complete
axes[2].pie([complete, incomplete], labels=['Complete', 'Incomplete'],
            colors=['#2ecc71', '#e74c3c'], autopct='%1.0f%%', startangle=90)
axes[2].set_title('(c) Overall Completeness')

fig.suptitle('Campaign P180W1: Artifact Completeness Dashboard', fontsize=14, fontweight='bold')
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, 'fig_completeness_dashboard.png'), dpi=120, bbox_inches='tight')
plt.close(fig)

# ── Figure 2: Knowledge Graph Statistics ──
fig, axes = plt.subplots(1, 2, figsize=(12, 5))

# 2a: Node types distribution
node_types = kg['statistics']['node_types']
types_labels = list(node_types.keys())
types_values = list(node_types.values())
colors = plt.cm.Set3(np.linspace(0, 1, len(types_labels)))
axes[0].barh(types_labels, types_values, color=colors)
axes[0].set_xlabel('Count')
axes[0].set_title('(a) Knowledge Graph Node Types')
for i, v in enumerate(types_values):
    axes[0].text(v + 0.3, i, str(v), va='center', fontsize=9)

# 2b: Edge types distribution
edge_types = kg['statistics']['edge_types']
et_labels = list(edge_types.keys())
et_values = list(edge_types.values())
# Sort by value
sorted_idx = np.argsort(et_values)[::-1]
et_labels = [et_labels[i] for i in sorted_idx]
et_values = [et_values[i] for i in sorted_idx]
colors2 = plt.cm.viridis(np.linspace(0.2, 0.8, len(et_labels)))
axes[1].barh(et_labels, et_values, color=colors2)
axes[1].set_xlabel('Count')
axes[1].set_title('(b) Knowledge Graph Edge Types')
for i, v in enumerate(et_values):
    axes[1].text(v + 0.3, i, str(v), va='center', fontsize=9)

fig.suptitle(f'Knowledge Graph: {kg["statistics"]["node_count"]} nodes, {kg["statistics"]["edge_count"]} edges',
             fontsize=13, fontweight='bold')
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, 'fig_knowledge_graph_stats.png'), dpi=120, bbox_inches='tight')
plt.close(fig)

# ── Figure 3: BP Alignment Coverage ──
fig, axes = plt.subplots(1, 2, figsize=(14, 6))

# 3a: Clause coverage status
clause_ids = [c['clause_id'] for c in bp['clause_details']]
status_map = {'covered': 1.0, 'partial': 0.5, 'uncovered': 0.0}
status_colors = {'covered': '#2ecc71', 'partial': '#f39c12', 'uncovered': '#e74c3c'}
coverage_vals = [status_map[c['status']] * 100 for c in bp['clause_details']]
colors3 = [status_colors[c['status']] for c in bp['clause_details']]

axes[0].barh(clause_ids, coverage_vals, color=colors3, alpha=0.8)
axes[0].set_xlabel('Coverage (%)')
axes[0].set_title('(a) BP §8.2 Clause Coverage')
axes[0].set_xlim(0, 110)
axes[0].axvline(x=100, color='gray', ls=':', alpha=0.5)

# 3b: Priority breakdown
pb = bp['priority_breakdown']
priorities = ['high', 'medium', 'low']
totals = [pb[p]['total'] for p in priorities]
covered_counts = [pb[p]['covered'] for p in priorities]
uncovered_counts = [t - c for t, c in zip(totals, covered_counts)]

x = np.arange(len(priorities))
axes[1].bar(x, covered_counts, 0.5, label='Covered', color='#2ecc71')
axes[1].bar(x, uncovered_counts, 0.5, bottom=covered_counts, label='Uncovered/Partial', color='#e74c3c')
axes[1].set_xticks(x)
axes[1].set_xticklabels([p.capitalize() for p in priorities])
axes[1].set_ylabel('Number of clauses')
axes[1].set_title('(b) Coverage by Priority')
axes[1].legend()

fig.suptitle(f'BP §8.2 Alignment: {bp["coverage_rate_pct"]}% coverage ({bp["covered_clauses"]}/{bp["total_clauses"]} clauses)',
             fontsize=13, fontweight='bold')
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, 'fig_bp_alignment.png'), dpi=120, bbox_inches='tight')
plt.close(fig)

# ── Figure 4: Cross-Domain Ice Load Consistency ──
fig, ax = plt.subplots(figsize=(10, 6))

# Load N09 cross-validation data
n09_path = os.path.join(os.path.dirname(__file__), '..', '..', 'artifacts', 'campaign_p180w1',
                         'N09_acceptance_gate', 'results.json')
with open(n09_path) as f:
    n09 = json.load(f)

checks = n09['cross_checks']
check_names = [c['check'][:30] for c in checks]
diffs = [c['diff_pct'] for c in checks]
thresholds = [c['threshold_pct'] for c in checks]
colors4 = ['green' if c['pass'] else 'red' for c in checks]

y = np.arange(len(checks))
ax.barh(y, diffs, color=colors4, alpha=0.7, label='Actual diff (%)')
ax.scatter(thresholds, y, marker='|', s=200, c='red', zorder=5, label='Threshold')
ax.set_yticks(y)
ax.set_yticklabels(check_names, fontsize=8)
ax.set_xlabel('Difference (%)')
ax.set_title('Cross-Node Physical Quantity Consistency (C4)')
ax.legend()
ax.grid(True, alpha=0.3, axis='x')

# Add summary text
summary = n09['summary']
textstr = f"Mean diff: {summary['mean_diff_pct']:.1f}%\nMax diff: {summary['max_diff_pct']:.1f}%\nPassed: {summary['passed']}/{summary['total_checks']}"
ax.text(0.98, 0.02, textstr, transform=ax.transAxes, fontsize=9,
        verticalalignment='bottom', horizontalalignment='right',
        bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, 'fig_cross_validation.png'), dpi=120, bbox_inches='tight')
plt.close(fig)

# ── Figure 5: Error Summary Across All Nodes ──
fig, ax = plt.subplots(figsize=(12, 6))

# Collect all error metrics from knowledge graph
err_nodes = [n for n in kg['nodes'] if n['type'] == 'error_metric']
err_names = [n['label'] for n in err_nodes]
err_values = [n['properties'].get('value_pct', 0) for n in err_nodes]

# Sort by value
sorted_idx = np.argsort(err_values)[::-1]
err_names = [err_names[i] for i in sorted_idx]
err_values = [err_values[i] for i in sorted_idx]

colors5 = ['#e74c3c' if v > 10 else '#f39c12' if v > 5 else '#2ecc71' for v in err_values]
ax.barh(err_names, err_values, color=colors5, alpha=0.8)
ax.axvline(x=3, color='green', ls='--', alpha=0.7, label='3% (strict)')
ax.axvline(x=10, color='red', ls='--', alpha=0.7, label='10% (threshold)')
ax.set_xlabel('Error / Difference (%)')
ax.set_title('Error Metrics Summary Across Campaign Nodes')
ax.legend()
ax.grid(True, alpha=0.3, axis='x')

fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, 'fig_error_summary.png'), dpi=120, bbox_inches='tight')
plt.close(fig)

print("All integration figures generated successfully.")
print(f"Output directory: {OUT_DIR}")
for f in sorted(os.listdir(OUT_DIR)):
    if f.endswith('.png'):
        print(f"  - {f}")
