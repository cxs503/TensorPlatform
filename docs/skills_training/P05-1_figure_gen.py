#!/usr/bin/env python3
"""Generate comparison figures for P05-1 caching experiment."""
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import base64
import io

# Load results
with open("/root/tp-agent/work/m6346/docs/skills_training/P05-1_results.json") as f:
    results = json.load(f)

with open("/root/tp-agent/work/m6346/docs/skills_training/P05-1_histogram.json") as f:
    hist = json.load(f)

fig, axes = plt.subplots(2, 2, figsize=(14, 10))
fig.suptitle("P05-1: LRU+Fixed-TTL vs LFU+Adaptive-TTL Caching Comparison", fontsize=14, fontweight='bold')

# 1. Hit rate comparison (bar chart with error bars)
ax = axes[0, 0]
strategies = ['LRU\n(Fixed TTL)', 'LFU\n(Adaptive TTL)']
hit_means = [results['lru']['aggregate']['hit_rate']['mean'],
             results['lfu']['aggregate']['hit_rate']['mean']]
hit_stds = [results['lru']['aggregate']['hit_rate']['std'],
            results['lfu']['aggregate']['hit_rate']['std']]
bars = ax.bar(strategies, hit_means, yerr=hit_stds, capsize=5,
              color=['#2196F3', '#FF9800'], edgecolor='white', width=0.5)
ax.set_ylabel('Hit Rate')
ax.set_ylim(0.70, 0.80)
for bar, val in zip(bars, hit_means):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.003,
            f'{val:.4f}', ha='center', va='bottom', fontsize=9)
ax.set_title('Hit Rate (mean +/- 1sd across 5 reps)')

# 2. Stale rate comparison
ax = axes[0, 1]
stale_means = [results['lru']['aggregate']['stale_rate']['mean'],
               results['lfu']['aggregate']['stale_rate']['mean']]
stale_stds = [results['lru']['aggregate']['stale_rate']['std'],
              results['lfu']['aggregate']['stale_rate']['std']]
bars = ax.bar(strategies, stale_means, yerr=stale_stds, capsize=5,
              color=['#2196F3', '#FF9800'], edgecolor='white', width=0.5)
ax.set_ylabel('Stale Hit Rate')
ax.set_ylim(0.50, 0.72)
for bar, val in zip(bars, stale_means):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.003,
            f'{val:.4f}', ha='center', va='bottom', fontsize=9)
ax.set_title('Stale Data Rate (hits serving data >120s old)')

# 3. Latency CDF from histogram data
ax = axes[1, 0]
lru_edges = np.array(hist['lru']['edges'])
lru_counts = np.array(hist['lru']['counts'])
lfu_edges = np.array(hist['lfu']['edges'])
lfu_counts = np.array(hist['lfu']['counts'])

lru_cdf = np.cumsum(lru_counts) / lru_counts.sum()
lfu_cdf = np.cumsum(lfu_counts) / lfu_counts.sum()

ax.plot(lru_edges, lru_cdf, '#2196F3', linewidth=2, label='LRU+Fixed-TTL')
ax.plot(lfu_edges, lfu_cdf, '#FF9800', linewidth=2, label='LFU+Adaptive-TTL')
ax.axvline(x=results['lru']['full_distribution']['p99'], color='#2196F3', linestyle=':', alpha=0.7, label='LRU P99')
ax.axvline(x=results['lfu']['full_distribution']['p99'], color='#FF9800', linestyle=':', alpha=0.7, label='LFU P99')
ax.set_xlabel('Latency (ms)')
ax.set_ylabel('CDF')
ax.set_xlim(0, 60)
ax.set_ylim(0, 1.02)
ax.legend(loc='lower right', fontsize=8)
ax.set_title('Latency CDF (dotted lines = P99)')
ax.grid(True, alpha=0.3)

# 4. Tail latency comparison (P50/P95/P99/P999)
ax = axes[1, 1]
percentiles = ['P50', 'P95', 'P99', 'P999']
lru_vals = [results['lru']['full_distribution']['p50'],
            results['lru']['full_distribution']['p95'],
            results['lru']['full_distribution']['p99'],
            results['lru']['full_distribution']['p999']]
lfu_vals = [results['lfu']['full_distribution']['p50'],
            results['lfu']['full_distribution']['p95'],
            results['lfu']['full_distribution']['p99'],
            results['lfu']['full_distribution']['p999']]

x = np.arange(len(percentiles))
width = 0.3
ax.bar(x - width/2, lru_vals, width, label='LRU+Fixed-TTL', color='#2196F3')
ax.bar(x + width/2, lfu_vals, width, label='LFU+Adaptive-TTL', color='#FF9800')
ax.set_xticks(x)
ax.set_xticklabels(percentiles)
ax.set_ylabel('Latency (ms)')
ax.set_title('Tail Latency Comparison')
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3, axis='y')

plt.tight_layout()

# Save figure
buf = io.BytesIO()
plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
buf.seek(0)
png_bytes = buf.read()
png_base64 = base64.b64encode(png_bytes).decode('ascii')

with open("/root/tp-agent/work/m6346/docs/skills_training/P05-1_figure.png", "wb") as f:
    f.write(png_bytes)

print(f"Figure saved: {len(png_bytes)} bytes, base64 length: {len(png_base64)}")

# Save base64 for report embedding
with open("/root/tp-agent/work/m6346/docs/skills_training/P05-1_figure_b64.txt", "w") as f:
    f.write(png_base64)
