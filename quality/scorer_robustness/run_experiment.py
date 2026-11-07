#!/usr/bin/env python3
"""Run the full scorer robustness experiment and generate report artifacts."""

import json
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from quality.scorer_robustness.scorer import ReportScorer
from quality.scorer_robustness.experiment import RobustnessExperiment
from quality.scorer_robustness.corpus import get_corpus
from quality.scorer_robustness.visualization import generate_all_figures


def main():
    """Run experiment and save results."""
    print("=" * 60)
    print("Scorer Robustness Experiment")
    print("=" * 60)

    # Setup
    scorer = ReportScorer(seed=42)
    corpus = get_corpus()
    exp = RobustnessExperiment(scorer, seed=42)

    print(f"\nCorpus size: {len(corpus)} reports")
    print(f"Running full experiment...")

    # Run experiment
    result = exp.run_full_experiment(
        corpus,
        n_rhetorical=30,
        n_number_fab=30,
        n_citation_inf=30,
        n_negative=30,
        n_positive=30,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)

    s = result.summary
    print(f"\nRhetorical Inflation:")
    print(f"  Flip rate: {s['rhetorical_flip_rate']:.1%} (threshold: ≤5%)")
    print(f"  Cohen's d: {s['rhetorical_cohens_d']:.4f} (threshold: |d|<0.2)")

    print(f"\nNumber Fabrication:")
    print(f"  Flip rate: {s['number_fabrication_flip_rate']:.1%} (threshold: ≤5%)")
    print(f"  Cohen's d: {s['number_fabrication_cohens_d']:.4f} (threshold: |d|<0.2)")

    print(f"\nCitation Inflation:")
    print(f"  Flip rate: {s['citation_inflation_flip_rate']:.1%} (threshold: ≤5%)")
    print(f"  Cohen's d: {s['citation_inflation_cohens_d']:.4f} (threshold: |d|<0.2)")

    print(f"\nNegative Control:")
    print(f"  Flip rate: {s['negative_control_flip_rate']:.1%} (threshold: =0%)")

    print(f"\nPositive Control:")
    print(f"  Flip rate: {s['positive_control_flip_rate']:.1%} (threshold: >20%)")

    print(f"\n" + "=" * 60)
    print("ACCEPTANCE CRITERIA")
    print("=" * 60)
    print(f"  All flip rates ≤5%: {'PASS' if s['all_flip_rates_le_5pct'] else 'FAIL'}")
    print(f"  All |d| < 0.2:      {'PASS' if s['all_cohens_d_lt_02'] else 'FAIL'}")
    print(f"  Negative control =0: {'PASS' if s['negative_control_flip_rate_zero'] else 'FAIL'}")
    print(f"  Positive control >20%: {'PASS' if s['positive_control_flip_rate_gt_20pct'] else 'FAIL'}")

    # Save results JSON
    results_dict = result.to_dict()
    results_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiment_results.json")
    with open(results_path, "w") as f:
        json.dump(results_dict, f, indent=2)
    print(f"\nResults saved to: {results_path}")

    # Generate figures
    print("\nGenerating figures...")
    figures = generate_all_figures(results_dict)
    print(f"Generated {len(figures)} figures")

    # Save figures as separate files for report
    import base64
    figures_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "figures")
    os.makedirs(figures_dir, exist_ok=True)
    for fig in figures:
        png_path = os.path.join(figures_dir, f"{fig['name']}.png")
        with open(png_path, "wb") as f:
            f.write(base64.b64decode(fig["png_base64"]))
        print(f"  Saved: {png_path}")

    print("\n" + "=" * 60)
    print("Experiment complete!")
    print("=" * 60)

    return result


if __name__ == "__main__":
    main()
