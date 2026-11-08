#!/usr/bin/env python3
"""
Benchmark script for the Multi-Fidelity Cascade Scorer.

Generates:
  - Cascade funnel diagram (candidates per stage)
  - Cost comparison: cascade vs naive
  - Budget-adaptive behavior under varying budget pressure
  - Elimination rate vs candidate pool size

Output: artifacts/cascade_scorer/*.png + benchmark_results.json
"""

import json
import os
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Ensure backend is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.cascade_scorer import (
    Candidate,
    CascadeConfig,
    CascadeScorer,
    FidelityLevel,
    make_synthetic_scorer,
)


def make_candidates(n, param_key="resistance"):
    return [
        Candidate(id=f"c{i:04d}", params={param_key: float(i), "L": 100.0 + i})
        for i in range(n)
    ]


def run_benchmark():
    artifact_dir = Path(__file__).parent.parent / "artifacts" / "cascade_scorer"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    scorer_fn = make_synthetic_scorer("resistance", "min")
    results = {}

    # ── Benchmark 1: Cascade funnel (100 candidates, unlimited budget) ──
    print("Benchmark 1: Cascade funnel...")
    cfg1 = CascadeConfig(
        low_cost=1.0, medium_cost=10.0, high_cost=1000.0,
        low_pass_rate=0.50, medium_pass_rate=0.40,
        total_budget=0.0,
    )
    cands1 = make_candidates(100)
    cs1 = CascadeScorer(cfg1, scorer_fn, scorer_fn, scorer_fn)
    res1 = cs1.run(cands1)

    stages_1 = ["Input", "Low\nFidelity", "Medium\nFidelity", "High\nFidelity"]
    counts_1 = [
        res1.n_candidates,
        res1.stages[0].n_passed,
        res1.stages[1].n_passed,
        res1.stages[2].n_passed,
    ]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(stages_1, counts_1, color=["#2196F3", "#4CAF50", "#FF9800", "#F44336"])
    ax.set_ylabel("Number of Candidates")
    ax.set_title("Multi-Fidelity Cascade Funnel (100 candidates, unlimited budget)")
    for bar, count in zip(bars, counts_1):
        ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 1,
                str(count), ha='center', va='bottom', fontweight='bold')
    ax.axhline(y=20, color='red', linestyle='--', alpha=0.5, label=f"Final: {counts_1[-1]}")
    ax.legend()
    plt.tight_layout()
    fig.savefig(artifact_dir / "cascade_funnel.png", dpi=150)
    plt.close()
    print(f"  Elimination rate (L+M): {res1.elimination_rate_low_medium:.1%}")
    print(f"  Total cost: {res1.total_cost}")
    results["funnel"] = res1.to_dict()

    # ── Benchmark 2: Cost comparison cascade vs naive ──
    print("Benchmark 2: Cost comparison...")
    pool_sizes = [50, 100, 200, 500, 1000]
    cascade_costs = []
    naive_costs = []
    elimination_rates = []

    for n in pool_sizes:
        cfg = CascadeConfig(
            low_cost=1.0, medium_cost=10.0, high_cost=1000.0,
            low_pass_rate=0.50, medium_pass_rate=0.40,
            total_budget=0.0,
        )
        cands = make_candidates(n)
        cs = CascadeScorer(cfg, scorer_fn, scorer_fn, scorer_fn)
        res = cs.run(cands)
        cascade_costs.append(res.total_cost)
        naive_costs.append(n * 1000.0)
        elimination_rates.append(res.elimination_rate_low_medium)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Cost comparison
    x = np.arange(len(pool_sizes))
    width = 0.35
    ax1.bar(x - width/2, naive_costs, width, label="Naive (all high-fidelity)", color="#F44336")
    ax1.bar(x + width/2, cascade_costs, width, label="Cascade", color="#4CAF50")
    ax1.set_xlabel("Pool Size")
    ax1.set_ylabel("Total Cost (budget units)")
    ax1.set_title("Cost: Cascade vs Naive")
    ax1.set_xticks(x)
    ax1.set_xticklabels([str(n) for n in pool_sizes])
    ax1.legend()
    ax1.set_yscale("log")

    # Elimination rate
    ax2.bar(x, [r * 100 for r in elimination_rates], color="#2196F3")
    ax2.axhline(y=80, color='red', linestyle='--', label="80% target")
    ax2.set_xlabel("Pool Size")
    ax2.set_ylabel("Elimination Rate (L+M) [%]")
    ax2.set_title("Elimination Rate vs Pool Size")
    ax2.set_xticks(x)
    ax2.set_xticklabels([str(n) for n in pool_sizes])
    ax2.legend()
    ax2.set_ylim(0, 100)

    plt.tight_layout()
    fig.savefig(artifact_dir / "cost_comparison.png", dpi=150)
    plt.close()
    results["cost_comparison"] = {
        "pool_sizes": pool_sizes,
        "cascade_costs": cascade_costs,
        "naive_costs": naive_costs,
        "elimination_rates": elimination_rates,
    }
    print(f"  Elimination rates: {[f'{r:.1%}' for r in elimination_rates]}")

    # ── Benchmark 3: Budget-adaptive behavior ──
    print("Benchmark 3: Budget-adaptive behavior...")
    budgets = [500, 1000, 2000, 5000, 10000, 50000, 0]  # 0 = unlimited
    budget_labels = ["500", "1K", "2K", "5K", "10K", "50K", "∞"]
    adaptive_costs = []
    adaptive_finals = []
    adaptive_elim = []

    for budget in budgets:
        cfg = CascadeConfig(
            low_cost=1.0, medium_cost=10.0, high_cost=1000.0,
            low_pass_rate=0.50, medium_pass_rate=0.40,
            total_budget=budget,
            budget_adaptive=True,
        )
        cands = make_candidates(200)
        cs = CascadeScorer(cfg, scorer_fn, scorer_fn, scorer_fn)
        res = cs.run(cands)
        adaptive_costs.append(res.total_cost)
        adaptive_finals.append(res.n_final)
        adaptive_elim.append(res.elimination_rate_low_medium)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    ax1.bar(budget_labels, adaptive_costs, color="#FF9800")
    ax1.set_xlabel("Budget Envelope")
    ax1.set_ylabel("Actual Cost Spent")
    ax1.set_title("Budget-Adaptive: Cost vs Budget (200 candidates)")
    ax1.axhline(y=200*1000, color='red', linestyle='--', alpha=0.5, label="Naive cost (200K)")
    ax1.legend()

    ax2.bar(budget_labels, adaptive_finals, color="#9C27B0")
    ax2.set_xlabel("Budget Envelope")
    ax2.set_ylabel("Final Survivors")
    ax2.set_title("Budget-Adaptive: Final Survivors vs Budget")

    plt.tight_layout()
    fig.savefig(artifact_dir / "budget_adaptive.png", dpi=150)
    plt.close()
    results["budget_adaptive"] = {
        "budgets": budget_labels,
        "costs": adaptive_costs,
        "finals": adaptive_finals,
        "elimination_rates": adaptive_elim,
    }
    print(f"  Costs: {adaptive_costs}")
    print(f"  Finals: {adaptive_finals}")

    # ── Benchmark 4: wall_exhausted scenario ──
    print("Benchmark 4: wall_exhausted scenario...")
    cfg_we = CascadeConfig(
        low_cost=1.0, medium_cost=10.0, high_cost=1000.0,
        low_pass_rate=0.50, medium_pass_rate=0.40,
        total_budget=5000.0,
        budget_adaptive=True,
    )
    cands_we = make_candidates(500)
    cs_we = CascadeScorer(cfg_we, scorer_fn, scorer_fn, scorer_fn)
    res_we = cs_we.run(cands_we)

    naive_cost_we = 500 * 1000.0
    savings_we = (1 - res_we.total_cost / naive_cost_we) * 100

    fig, ax = plt.subplots(figsize=(8, 5))
    stages_we = ["Input\n(500)", "Low\nEval", "Low\nPass", "Med\nEval", "Med\nPass", "High\nEval", "High\nPass"]
    # Reconstruct evaluation counts
    low_eval = res_we.stages[0].n_input - res_we.stages[0].n_budget_capped
    med_eval = res_we.stages[1].n_input - res_we.stages[1].n_budget_capped
    high_eval = int(res_we.stages[2].cost_spent / cfg_we.high_cost)
    counts_we = [
        500, low_eval, res_we.stages[0].n_passed,
        med_eval, res_we.stages[1].n_passed,
        high_eval, res_we.stages[2].n_passed,
    ]
    colors = ["#2196F3", "#81C784", "#4CAF50", "#FFB74D", "#FF9800", "#EF9A9A", "#F44336"]
    bars = ax.bar(stages_we, counts_we, color=colors)
    ax.set_ylabel("Candidates")
    ax.set_title(f"wall_exhausted Scenario: 500 candidates, budget=5000\n"
                 f"Cost={res_we.total_cost:.0f} vs Naive={naive_cost_we:.0f} (savings={savings_we:.1f}%)")
    for bar, count in zip(bars, counts_we):
        ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 2,
                str(count), ha='center', va='bottom', fontsize=8)
    plt.tight_layout()
    fig.savefig(artifact_dir / "wall_exhausted.png", dpi=150)
    plt.close()
    results["wall_exhausted"] = {
        "result": res_we.to_dict(),
        "naive_cost": naive_cost_we,
        "savings_pct": savings_we,
    }
    print(f"  Cost: {res_we.total_cost} / {naive_cost_we} (savings={savings_we:.1f}%)")
    print(f"  Elimination rate: {res_we.elimination_rate_low_medium:.1%}")

    # ── Save results JSON ──
    results_path = artifact_dir / "benchmark_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {results_path}")
    print(f"Figures saved to {artifact_dir}/")

    return results


if __name__ == "__main__":
    run_benchmark()
