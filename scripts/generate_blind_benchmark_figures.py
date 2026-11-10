#!/usr/bin/env python3
"""
Generate figures for Blind Benchmark Holdout report.
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from pathlib import Path


def generate_pipeline_figure(output_path: str):
    """Generate pipeline architecture diagram."""
    fig, ax = plt.subplots(1, 1, figsize=(12, 8))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis('off')

    # Title
    ax.text(5, 9.5, 'Blind Benchmark Holdout Pipeline',
            ha='center', va='top', fontsize=16, fontweight='bold')

    # Boxes
    boxes = [
        (1, 7.5, 2, 1, 'Benchmark\nDataset', '#ADD8E6'),
        (4, 7.5, 2, 1, 'SEALER\n(encrypt)', '#90EE90'),
        (7, 7.5, 2, 1, 'Sealed\nDataset', '#FFFFE0'),
        (1, 5, 2, 1, 'Solver\n(predictions)', '#F08080'),
        (4, 5, 2, 1, 'SUBMIT\n(write-only)', '#90EE90'),
        (7, 5, 2, 1, 'Submission\nArtifact', '#FFFFE0'),
        (4, 2.5, 2, 1, 'ADJUDICATOR\n(decrypt+score)', '#90EE90'),
        (7, 2.5, 2, 1, 'Scoring\nReport', '#FFFFE0'),
        (4, 0.5, 2, 1, 'BACKFILL\nto guide', '#90EE90'),
    ]

    for x, y, w, h, text, color in boxes:
        rect = mpatches.FancyBboxPatch((x, y), w, h,
                                        boxstyle="round,pad=0.1",
                                        facecolor=color,
                                        edgecolor='black',
                                        linewidth=2)
        ax.add_patch(rect)
        ax.text(x + w/2, y + h/2, text, ha='center', va='center',
                fontsize=10, fontweight='bold')

    # Arrows
    arrows = [
        (3, 8, 4, 8),
        (6, 8, 7, 8),
        (8, 7.5, 8, 6),
        (3, 5.5, 4, 5.5),
        (6, 5.5, 7, 5.5),
        (8, 5, 8, 3.5),
        (6, 3, 7, 3),
        (5, 2.5, 5, 1.5),
    ]

    for x1, y1, x2, y2 in arrows:
        ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle='->', lw=2, color='gray'))

    # Adversarial box
    rect = mpatches.FancyBboxPatch((0.5, 0.5), 2, 1,
                                    boxstyle="round,pad=0.1",
                                    facecolor='#FFCCCC',
                                    edgecolor='red',
                                    linewidth=2, linestyle='--')
    ax.add_patch(rect)
    ax.text(1.5, 1, 'ADVERSARIAL\n(red team)', ha='center', va='center',
            fontsize=9, fontweight='bold', color='red')

    # Legend
    legend_elements = [
        mpatches.Patch(facecolor='#90EE90', edgecolor='black', label='Processing'),
        mpatches.Patch(facecolor='#FFFFE0', edgecolor='black', label='Artifact'),
        mpatches.Patch(facecolor='#ADD8E6', edgecolor='black', label='Input'),
        mpatches.Patch(facecolor='#F08080', edgecolor='black', label='External'),
        mpatches.Patch(facecolor='#FFCCCC', edgecolor='red', label='Testing', linestyle='--'),
    ]
    ax.legend(handles=legend_elements, loc='lower right', fontsize=9)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"Saved: {output_path}")


def generate_results_figure(output_path: str):
    """Generate results comparison figure."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Left: Error distribution
    ax = axes[0]
    cases = ['SUBOFF_01', 'SUBOFF_02', 'SUBOFF_03']
    good_errors = [2.86, 2.45, 1.96]
    bad_errors = [42.86, 43.26, 47.06]

    x = np.arange(len(cases))
    width = 0.35

    bars1 = ax.bar(x - width/2, good_errors, width, label='Good Solver', color='#2ecc71')
    bars2 = ax.bar(x + width/2, bad_errors, width, label='Bad Solver', color='#e74c3c')

    ax.axhline(y=15, color='orange', linestyle='--', linewidth=2, label='Threshold (15%)')

    ax.set_xlabel('Case ID', fontsize=11)
    ax.set_ylabel('Relative Error (%)', fontsize=11)
    ax.set_title('Prediction Error by Case', fontsize=12, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(cases)
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    # Add value labels
    for bar in bars1:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{height:.1f}%', ha='center', va='bottom', fontsize=9)

    for bar in bars2:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{height:.1f}%', ha='center', va='bottom', fontsize=9)

    # Right: Adversarial test results
    ax = axes[1]
    fixture_types = ['Gross\nError', 'Systematic\nBias', 'Perfect\nPrediction']
    expected = [True, True, False]
    actual = [True, True, False]
    test_passed = [True, True, True]

    x = np.arange(len(fixture_types))
    width = 0.35

    bars1 = ax.bar(x - width/2, [int(e) for e in expected], width,
                   label='Expected FAIL', color='#3498db', alpha=0.7)
    bars2 = ax.bar(x + width/2, [int(a) for a in actual], width,
                   label='Actual FAIL', color='#9b59b6', alpha=0.7)

    ax.set_xlabel('Fixture Type', fontsize=11)
    ax.set_ylabel('FAIL Detected (0/1)', fontsize=11)
    ax.set_title('Adversarial Test Results', fontsize=12, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(fixture_types)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(['PASS', 'FAIL'])
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    # Add checkmarks for passed tests
    for i, passed in enumerate(test_passed):
        if passed:
            ax.text(i, 1.1, '✓', ha='center', va='bottom',
                    fontsize=16, color='green', fontweight='bold')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"Saved: {output_path}")


def generate_provenance_figure(output_path: str):
    """Generate provenance chain figure."""
    fig, ax = plt.subplots(1, 1, figsize=(12, 4))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 4)
    ax.axis('off')

    # Title
    ax.text(6, 3.7, 'Cryptographic Provenance Chain',
            ha='center', va='top', fontsize=14, fontweight='bold')

    # Chain elements
    elements = [
        (0.5, 2, 'Dataset\nSHA256', '02de9a...c747', '#3498db'),
        (3, 2, 'Submission\nSHA256', 'd24ae6...17de', '#2ecc71'),
        (5.5, 2, 'Report\nID', '9f8860...f754', '#9b59b6'),
        (8, 2, 'Guide\nEntry', 'c1a2b3...d4e5', '#e74c3c'),
    ]

    for x, y, label, hash_val, color in elements:
        # Box
        rect = mpatches.FancyBboxPatch((x, y-0.5), 2, 1.5,
                                        boxstyle="round,pad=0.1",
                                        facecolor=color,
                                        alpha=0.3,
                                        edgecolor=color,
                                        linewidth=2)
        ax.add_patch(rect)

        # Label
        ax.text(x + 1, y + 0.6, label, ha='center', va='center',
                fontsize=10, fontweight='bold')

        # Hash
        ax.text(x + 1, y - 0.1, hash_val, ha='center', va='center',
                fontsize=8, family='monospace')

    # Arrows
    for i in range(len(elements) - 1):
        x1 = elements[i][0] + 2
        x2 = elements[i+1][0]
        ax.annotate('', xy=(x2, 2), xytext=(x1, 2),
                    arrowprops=dict(arrowstyle='->', lw=2, color='gray'))

    # Caption
    ax.text(6, 0.3, 'Each step cryptographically linked → immutable audit trail',
            ha='center', va='center', fontsize=10, style='italic')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    output_dir = Path("/root/tp-agent/work/m10874/artifacts/blind_benchmark")
    output_dir.mkdir(parents=True, exist_ok=True)

    generate_pipeline_figure(str(output_dir / "pipeline_architecture.png"))
    generate_results_figure(str(output_dir / "results_comparison.png"))
    generate_provenance_figure(str(output_dir / "provenance_chain.png"))

    print("\nAll figures generated successfully!")
