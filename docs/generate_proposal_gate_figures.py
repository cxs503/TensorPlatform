"""Generate figures for proposal gate report."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

def fig_architecture():
    """Generate architecture diagram."""
    fig, ax = plt.subplots(1, 1, figsize=(12, 8))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 8)
    ax.axis('off')
    
    # Title
    ax.text(6, 7.5, 'Proposal Gate Architecture', fontsize=16, fontweight='bold', ha='center')
    
    # API Layer
    api_box = mpatches.FancyBboxPatch((1, 6), 10, 1, boxstyle="round,pad=0.1", 
                                       facecolor='#ADD8E6', edgecolor='black', linewidth=2)
    ax.add_patch(api_box)
    ax.text(6, 6.5, 'API Layer: /proposals/gate/*', fontsize=12, ha='center', va='center', fontweight='bold')
    
    # Gate boxes
    gate_colors = ['#FFB3BA', '#BAFFC9', '#BAE1FF']
    gate_labels = ['Gate 1:\nSchema\nContract', 'Gate 2:\nDeduplicator\n(NFKC+3-gram)', 'Gate 3:\nLedger\n(SQLite/PG)']
    
    for i, (color, label) in enumerate(zip(gate_colors, gate_labels)):
        x = 1.5 + i * 3.5
        box = mpatches.FancyBboxPatch((x, 3.5), 2.5, 2, boxstyle="round,pad=0.1",
                                       facecolor=color, edgecolor='black', linewidth=2)
        ax.add_patch(box)
        ax.text(x + 1.25, 4.5, label, fontsize=10, ha='center', va='center')
    
    # Arrows between gates
    for i in range(2):
        x = 4 + i * 3.5
        ax.annotate('', xy=(x + 0.5, 4.5), xytext=(x, 4.5),
                    arrowprops=dict(arrowstyle='->', lw=2, color='black'))
    
    # Arrow from API to Gate 1
    ax.annotate('', xy=(2.75, 5.5), xytext=(6, 6),
                arrowprops=dict(arrowstyle='->', lw=2, color='black'))
    
    # Config box
    config_box = mpatches.FancyBboxPatch((1, 1), 10, 1.5, boxstyle="round,pad=0.1",
                                          facecolor='#FFFFBA', edgecolor='black', linewidth=2)
    ax.add_patch(config_box)
    ax.text(6, 2, 'Configuration (Zero Domain Semantics)\n'
            'proposal_gate_config.json | proposal_v1.schema.json | proposal_v2.schema.json',
            fontsize=10, ha='center', va='center')
    
    # Arrows from config to gates
    for i in range(3):
        x = 2.75 + i * 3.5
        ax.annotate('', xy=(x, 3.5), xytext=(x, 2.5),
                    arrowprops=dict(arrowstyle='->', lw=1.5, color='gray', linestyle='dashed'))
    
    # Verdict labels
    verdicts = ['quarantined', 'duplicate', 'accepted']
    for i, v in enumerate(verdicts):
        x = 2.75 + i * 3.5
        ax.text(x, 3.2, f'→ {v}', fontsize=9, ha='center', color='darkred' if v != 'accepted' else 'darkgreen')
    
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'proposal_gate_architecture.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: proposal_gate_architecture.png")


def fig_test_results():
    """Generate test results chart."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    
    # Test categories
    categories = ['Schema\nValidation', 'Text\nNormalization', 'Signature', 'Dedup\n(Mock DB)', 'Config', 'Zero Domain\nSemantics']
    passed = [8, 9, 3, 2, 3, 2]
    skipped = [0, 0, 0, 0, 0, 2]
    
    x = np.arange(len(categories))
    width = 0.6
    
    bars1 = ax1.bar(x, passed, width, label='Passed', color='#4CAF50')
    bars2 = ax1.bar(x, skipped, width, bottom=passed, label='Skipped', color='#FFC107')
    
    ax1.set_xlabel('Test Category', fontsize=11)
    ax1.set_ylabel('Number of Tests', fontsize=11)
    ax1.set_title('Test Results by Category (27 passed, 2 skipped)', fontsize=12, fontweight='bold')
    ax1.set_xticks(x)
    ax1.set_xticklabels(categories, fontsize=9)
    ax1.legend()
    ax1.set_ylim(0, 12)
    
    # Add value labels
    for bar in bars1:
        height = bar.get_height()
        if height > 0:
            ax1.text(bar.get_x() + bar.get_width()/2., height/2, f'{int(height)}',
                    ha='center', va='center', fontweight='bold', color='white')
    
    # Verdict distribution (example)
    verdicts = ['accepted', 'rejected', 'quarantined', 'duplicate']
    counts = [65, 10, 15, 10]
    colors = ['#4CAF50', '#F44336', '#FF9800', '#9C27B0']
    
    wedges, texts, autotexts = ax2.pie(counts, labels=verdicts, colors=colors,
                                        autopct='%1.1f%%', startangle=90)
    ax2.set_title('Example Ledger Verdict Distribution', fontsize=12, fontweight='bold')
    
    for text in autotexts:
        text.set_fontsize(10)
        text.set_fontweight('bold')
    
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'proposal_gate_test_results.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: proposal_gate_test_results.png")


def fig_dedup_algorithm():
    """Generate deduplication algorithm flow diagram."""
    fig, ax = plt.subplots(1, 1, figsize=(10, 6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis('off')
    
    ax.text(5, 5.5, 'Deduplication Algorithm Flow', fontsize=14, fontweight='bold', ha='center')
    
    steps = [
        (1, 4.5, 'Input\nProposal', '#E3F2FD'),
        (3, 4.5, 'NFKC\nNormalize', '#FFF3E0'),
        (5, 4.5, 'Char 3-gram\nExtraction', '#E8F5E9'),
        (7, 4.5, 'Jaccard\nSimilarity', '#F3E5F5'),
        (9, 4.5, 'Score ≥\nThreshold?', '#FFEBEE'),
    ]
    
    for x, y, label, color in steps:
        box = mpatches.FancyBboxPatch((x-0.8, y-0.5), 1.6, 1, boxstyle="round,pad=0.05",
                                       facecolor=color, edgecolor='black', linewidth=1.5)
        ax.add_patch(box)
        ax.text(x, y, label, fontsize=8, ha='center', va='center')
    
    # Arrows
    for i in range(4):
        x1 = steps[i][0] + 0.8
        x2 = steps[i+1][0] - 0.8
        ax.annotate('', xy=(x2, 4.5), xytext=(x1, 4.5),
                    arrowprops=dict(arrowstyle='->', lw=1.5))
    
    # Decision branches
    ax.text(9, 3.3, 'Yes', fontsize=9, ha='center', color='green', fontweight='bold')
    ax.text(9, 2.5, '→ duplicate', fontsize=9, ha='center', color='red')
    
    ax.text(7.5, 3.3, 'No', fontsize=9, ha='center', color='orange', fontweight='bold')
    ax.text(7.5, 2.5, '→ accepted', fontsize=9, ha='center', color='green')
    
    # Dependency overlap
    dep_box = mpatches.FancyBboxPatch((3, 1.5), 4, 0.8, boxstyle="round,pad=0.05",
                                       facecolor='#FFF9C4', edgecolor='black', linewidth=1.5)
    ax.add_patch(dep_box)
    ax.text(5, 1.9, 'Dependency Overlap (deps_hint set Jaccard)', fontsize=9, ha='center', va='center')
    
    ax.annotate('', xy=(5, 2.3), xytext=(5, 4),
                arrowprops=dict(arrowstyle='->', lw=1, color='gray', linestyle='dashed'))
    
    # Formula
    ax.text(5, 0.7, 'Combined Score = 0.7 × text_sim + 0.3 × dep_sim  (if dep_overlap ≥ threshold)',
            fontsize=9, ha='center', style='italic', 
            bbox=dict(boxstyle='round,pad=0.3', facecolor='lightyellow', edgecolor='gray'))
    
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'proposal_gate_dedup_algorithm.png'), dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: proposal_gate_dedup_algorithm.png")


if __name__ == '__main__':
    fig_architecture()
    fig_test_results()
    fig_dedup_algorithm()
    print("All figures generated successfully.")
