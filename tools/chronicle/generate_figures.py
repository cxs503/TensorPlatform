#!/usr/bin/env python3
"""
Generate figures for the system research report.
Creates publication-quality charts from extracted metrics.

Usage:
    python3 generate_figures.py --metrics metrics.json --output-dir figures/
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from datetime import datetime
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    print("matplotlib not available, skipping figure generation", file=sys.stderr)


def generate_throughput_chart(metrics, output_dir):
    """Generate hourly throughput chart."""
    if not HAS_MPL:
        return None
    
    hourly = metrics.get('hourly_throughput', [])
    if not hourly:
        return None
    
    hours = []
    counts = []
    for h in hourly:
        ts = h['hour']
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace('+00:00', ''))
        hours.append(ts)
        counts.append(h['missions_completed'])
    
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(hours, counts, width=0.03, color='#2E86AB', alpha=0.8)
    ax.set_xlabel('Time (UTC)', fontsize=11)
    ax.set_ylabel('Missions Completed', fontsize=11)
    ax.set_title('Hourly Mission Throughput (2026-11-06 ~ 11-07)', fontsize=13, fontweight='bold')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M\n%m/%d'))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))
    plt.xticks(rotation=0, fontsize=9)
    ax.grid(axis='y', alpha=0.3)
    ax.set_axisbelow(True)
    
    plt.tight_layout()
    path = output_dir / 'fig1_throughput.png'
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    return str(path)


def generate_repo_distribution_chart(metrics, output_dir):
    """Generate repository distribution pie chart."""
    if not HAS_MPL:
        return None
    
    repos = metrics.get('by_repository', [])
    if not repos:
        return None
    
    labels = [r['repo'] for r in repos]
    sizes = [r['missions'] for r in repos]
    
    fig, ax = plt.subplots(figsize=(8, 8))
    colors = plt.cm.Set3(range(len(labels)))
    wedges, texts, autotexts = ax.pie(sizes, labels=labels, autopct='%1.1f%%',
                                       colors=colors, startangle=90)
    for text in texts:
        text.set_fontsize(9)
    for autotext in autotexts:
        autotext.set_fontsize(8)
        autotext.set_fontweight('bold')
    
    ax.set_title('Mission Distribution by Repository', fontsize=13, fontweight='bold')
    
    plt.tight_layout()
    path = output_dir / 'fig2_repo_distribution.png'
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    return str(path)


def generate_campaign_status_chart(metrics, output_dir):
    """Generate campaign node status chart."""
    if not HAS_MPL:
        return None
    
    nodes = metrics.get('campaign_nodes', [])
    if not nodes:
        return None
    
    # Aggregate by plan_id
    plans = {}
    for n in nodes:
        pid = n['plan_id']
        if pid not in plans:
            plans[pid] = {'done': 0, 'in-flight': 0, 'blocked': 0}
        status = n['status']
        if status in plans[pid]:
            plans[pid][status] = n['node_count']
    
    plan_ids = sorted(plans.keys())
    done = [plans[p]['done'] for p in plan_ids]
    inflight = [plans[p]['in-flight'] for p in plan_ids]
    blocked = [plans[p]['blocked'] for p in plan_ids]
    
    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(plan_ids))
    width = 0.6
    
    ax.bar(x, done, width, label='Done', color='#2CA02C')
    ax.bar(x, inflight, width, bottom=done, label='In-Flight', color='#FF7F0E')
    ax.bar(x, blocked, width, bottom=[d+i for d,i in zip(done, inflight)], 
           label='Blocked', color='#D62728')
    
    ax.set_xlabel('Campaign Plan ID', fontsize=11)
    ax.set_ylabel('Node Count', fontsize=11)
    ax.set_title('Campaign Node Status by Plan', fontsize=13, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels([f'Plan {p}' for p in plan_ids])
    ax.legend()
    ax.grid(axis='y', alpha=0.3)
    ax.set_axisbelow(True)
    
    plt.tight_layout()
    path = output_dir / 'fig3_campaign_status.png'
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    return str(path)


def generate_model_usage_chart(metrics, output_dir):
    """Generate model usage distribution chart."""
    if not HAS_MPL:
        return None
    
    models = metrics.get('by_model', [])
    if not models:
        return None
    
    labels = [m['model'] for m in models]
    counts = [m['count'] for m in models]
    
    fig, ax = plt.subplots(figsize=(9, 5))
    y_pos = range(len(labels))
    bars = ax.barh(y_pos, counts, color='#A23B72', alpha=0.8)
    
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel('Mission Count', fontsize=11)
    ax.set_title('AI Model Usage Distribution', fontsize=13, fontweight='bold')
    ax.invert_yaxis()
    
    # Add count labels
    for bar, count in zip(bars, counts):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2,
                str(count), va='center', fontsize=9, fontweight='bold')
    
    ax.grid(axis='x', alpha=0.3)
    ax.set_axisbelow(True)
    
    plt.tight_layout()
    path = output_dir / 'fig4_model_usage.png'
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    return str(path)


def main():
    parser = argparse.ArgumentParser(description='Generate research report figures')
    parser.add_argument('--metrics', required=True, help='Metrics JSON file')
    parser.add_argument('--output-dir', default='figures', help='Output directory')
    args = parser.parse_args()
    
    metrics = json.loads(Path(args.metrics).read_text())
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    figures = []
    
    fig1 = generate_throughput_chart(metrics, output_dir)
    if fig1:
        figures.append({'name': 'fig1_throughput', 'path': fig1,
                       'caption': 'Hourly mission throughput over the 48-hour observation window'})
    
    fig2 = generate_repo_distribution_chart(metrics, output_dir)
    if fig2:
        figures.append({'name': 'fig2_repo_distribution', 'path': fig2,
                       'caption': 'Distribution of missions across the 12 repositories in the Tensor ecosystem'})
    
    fig3 = generate_campaign_status_chart(metrics, output_dir)
    if fig3:
        figures.append({'name': 'fig3_campaign_status', 'path': fig3,
                       'caption': 'Campaign DAG node status across 7 active campaign plans'})
    
    fig4 = generate_model_usage_chart(metrics, output_dir)
    if fig4:
        figures.append({'name': 'fig4_model_usage', 'path': fig4,
                       'caption': 'AI model usage distribution showing model diversity'})
    
    # Write metadata
    meta_path = output_dir / 'figures_metadata.json'
    meta_path.write_text(json.dumps(figures, indent=2))
    
    print(f"Generated {len(figures)} figures in {output_dir}/")
    for f in figures:
        print(f"  - {f['name']}: {f['path']}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
