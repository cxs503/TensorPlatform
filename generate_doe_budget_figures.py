#!/usr/bin/env python
"""Generate figures for the DoE + Budget Adaptation report."""
from __future__ import annotations

import base64
import io
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from services.doe_framework import (
    ParameterSpec, full_factorial, central_composite, box_behnken, d_optimal
)
from services.budget_adaptation import run_budget_simulation


def fig_to_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def generate_doe_comparison():
    """Figure 1: DoE design comparison (3-param, 2D projection)."""
    specs = [
        ParameterSpec(name="α", low=0.0, high=1.0),
        ParameterSpec(name="β", low=0.0, high=1.0),
        ParameterSpec(name="γ", low=0.0, high=1.0),
    ]
    
    fig, axes = plt.subplots(2, 2, figsize=(10, 10))
    
    designs = [
        ("Full Factorial (8 runs)", full_factorial(specs, n_levels=2)),
        ("Central Composite (15 runs)", central_composite(specs)),
        ("Box-Behnken (15 runs)", box_behnken(specs)),
        ("D-Optimal (15 runs)", d_optimal(specs, n_runs=15, seed=42)),
    ]
    
    for ax, (title, result) in zip(axes.flat, designs):
        ax.scatter(result.points[:, 0], result.points[:, 1], 
                   c=result.points[:, 2], cmap='viridis', s=80, edgecolors='black', linewidth=0.5)
        ax.set_xlabel('α (coded)')
        ax.set_ylabel('β (coded)')
        ax.set_title(f'{title}\nD-eff = {result.d_efficiency:.3f}')
        ax.set_xlim(-1.5, 1.5)
        ax.set_ylim(-1.5, 1.5)
        ax.grid(True, alpha=0.3)
        ax.set_aspect('equal')
    
    plt.suptitle('DoE Design Comparison (3 parameters, α-β projection)', fontsize=14, y=1.02)
    plt.tight_layout()
    return fig_to_base64(fig)


def generate_budget_simulation():
    """Figure 2: Budget adaptation simulation results."""
    result = run_budget_simulation(n_tasks=10, seed=42)
    
    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    
    # Panel 1: Decision distribution
    actions = [d['action'] for d in result['decisions']]
    action_counts = {}
    for a in actions:
        action_counts[a] = action_counts.get(a, 0) + 1
    
    colors = {'up_adjust': '#e74c3c', 'down_adjust': '#2ecc71', 
              'terminate': '#95a5a6', 'continue': '#3498db'}
    bars = axes[0].bar(action_counts.keys(), action_counts.values(), 
                       color=[colors.get(a, '#999') for a in action_counts.keys()])
    axes[0].set_title('Budget Decision Distribution')
    axes[0].set_ylabel('Number of Tasks')
    for bar, count in zip(bars, action_counts.values()):
        axes[0].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                     str(count), ha='center', va='bottom', fontweight='bold')
    
    # Panel 2: Accuracy by category
    categories = ['Up-Adjust', 'Down-Adjust', 'Terminate']
    accuracies = [result['accuracy_up'], result['accuracy_down'], result['accuracy_terminate']]
    bars = axes[1].bar(categories, accuracies, color=['#e74c3c', '#2ecc71', '#95a5a6'])
    axes[1].axhline(y=0.8, color='red', linestyle='--', alpha=0.7, label='Target (80%)')
    axes[1].set_title('Decision Accuracy by Category')
    axes[1].set_ylabel('Accuracy')
    axes[1].set_ylim(0, 1.1)
    axes[1].legend()
    for bar, acc in zip(bars, accuracies):
        axes[1].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
                     f'{acc:.1%}', ha='center', va='bottom', fontweight='bold')
    
    # Panel 3: Budget comparison
    budget_types = ['Static', 'Adaptive']
    budgets = [result['static_budget_total'], result['adaptive_budget_total']]
    bars = axes[2].bar(budget_types, budgets, color=['#3498db', '#e67e22'])
    axes[2].set_title('Total Budget Comparison')
    axes[2].set_ylabel('Budget Units')
    for bar, val in zip(bars, budgets):
        axes[2].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 10,
                     f'{val:.0f}', ha='center', va='bottom', fontweight='bold')
    
    plt.suptitle('Budget Adaptation Simulation Results (10 tasks)', fontsize=14, y=1.02)
    plt.tight_layout()
    return fig_to_base64(fig)


def generate_d_efficiency_chart():
    """Figure 3: D-efficiency comparison across designs."""
    specs = [
        ParameterSpec(name="a", low=0.0, high=1.0),
        ParameterSpec(name="b", low=0.0, high=1.0),
        ParameterSpec(name="c", low=0.0, high=1.0),
    ]
    
    designs = []
    efficiencies = []
    
    ff = full_factorial(specs, n_levels=2)
    designs.append('Full Factorial\n(8 runs)')
    efficiencies.append(ff.d_efficiency)
    
    cc = central_composite(specs)
    designs.append('Central Composite\n(15 runs)')
    efficiencies.append(cc.d_efficiency)
    
    bb = box_behnken(specs)
    designs.append('Box-Behnken\n(15 runs)')
    efficiencies.append(bb.d_efficiency)
    
    do = d_optimal(specs, n_runs=15, seed=42)
    designs.append('D-Optimal\n(15 runs)')
    efficiencies.append(do.d_efficiency)
    
    fig, ax = plt.subplots(figsize=(8, 5))
    colors = ['#3498db', '#2ecc71', '#e74c3c', '#9b59b6']
    bars = ax.bar(designs, efficiencies, color=colors, edgecolor='black', linewidth=0.5)
    ax.axhline(y=0.85, color='red', linestyle='--', alpha=0.7, label='Acceptance (0.85)')
    ax.set_title('D-Efficiency Comparison (3 parameters)')
    ax.set_ylabel('D-Efficiency')
    ax.set_ylim(0, 1.1)
    ax.legend()
    for bar, eff in zip(bars, efficiencies):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02,
                f'{eff:.3f}', ha='center', va='bottom', fontweight='bold')
    
    plt.tight_layout()
    return fig_to_base64(fig)


if __name__ == '__main__':
    figures = {
        "doe_comparison": {
            "name": "doe_design_comparison",
            "caption": "DoE design comparison: Full Factorial, Central Composite, Box-Behnken, and D-Optimal designs for 3 parameters. Color encodes the third parameter (γ). D-efficiency values shown in titles.",
            "png_base64": generate_doe_comparison()
        },
        "budget_simulation": {
            "name": "budget_adaptation_results",
            "caption": "Budget adaptation simulation results: (left) decision distribution across 10 tasks, (center) accuracy by category with 80% target line, (right) total budget comparison between static and adaptive approaches.",
            "png_base64": generate_budget_simulation()
        },
        "d_efficiency": {
            "name": "d_efficiency_comparison",
            "caption": "D-efficiency comparison across four design types for 3-parameter problems. Red dashed line indicates the 0.85 acceptance threshold.",
            "png_base64": generate_d_efficiency_chart()
        }
    }
    
    import json
    print(json.dumps(figures, indent=2))
