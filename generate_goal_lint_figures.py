"""Generate figures for goal_lint validation report."""
import json
import base64
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from backend.tools.goal_lint import (
    run_full_validation,
    _build_synthetic_corpus,
    lint_goal,
)

def generate_score_distribution(report):
    """生成分档接受率柱状图。"""
    j1 = report["j1"]
    dist = j1["score_distribution"]
    
    scores = [0, 1, 2, 3, 4]
    counts = [dist[str(s)]["count"] for s in scores]
    pos_counts = [dist[str(s)]["positive_count"] for s in scores]
    acc_rates = [dist[str(s)]["acceptance_rate"] for s in scores]
    
    fig, ax1 = plt.subplots(figsize=(10, 6))
    
    x = np.arange(len(scores))
    width = 0.35
    
    bars1 = ax1.bar(x - width/2, counts, width, label='Total', color='#4472C4', alpha=0.8)
    bars2 = ax1.bar(x + width/2, pos_counts, width, label='Positive', color='#ED7D31', alpha=0.8)
    
    ax1.set_xlabel('Score (0-4)', fontsize=12)
    ax1.set_ylabel('Count', fontsize=12)
    ax1.set_xticks(x)
    ax1.set_xticklabels([f'{s}分' for s in scores])
    ax1.legend(loc='upper left')
    ax1.set_title('Goal Lint Score Distribution (N=120)', fontsize=14)
    
    # Add acceptance rate line
    ax2 = ax1.twinx()
    ax2.plot(x, acc_rates, 'ro-', linewidth=2, markersize=8, label='Acceptance Rate')
    ax2.set_ylabel('Acceptance Rate', fontsize=12, color='red')
    ax2.set_ylim(-0.05, 1.05)
    ax2.legend(loc='upper right')
    
    # Add value labels
    for bar in bars1:
        height = bar.get_height()
        ax1.annotate(f'{int(height)}', xy=(bar.get_x() + bar.get_width()/2, height),
                    xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=9)
    
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def generate_confusion_matrix(report):
    """生成混淆矩阵热力图。"""
    cm = report["j1"]["confusion_matrix"]
    matrix = np.array([[cm["tn"], cm["fp"]], [cm["fn"], cm["tp"]]])
    
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(matrix, cmap='Blues', aspect='auto')
    
    labels = [['TN', 'FP'], ['FN', 'TP']]
    for i in range(2):
        for j in range(2):
            text = ax.text(j, i, f'{labels[i][j]}\n{matrix[i, j]}',
                          ha="center", va="center", color="black" if matrix[i, j] < 30 else "white",
                          fontsize=14)
    
    ax.set_xticks([0, 1])
    ax.set_yticks([0, 1])
    ax.set_xticklabels(['Predicted Negative', 'Predicted Positive'])
    ax.set_yticklabels(['Actual Negative', 'Actual Positive'])
    ax.set_title('Confusion Matrix (Threshold ≥2)', fontsize=14)
    
    plt.colorbar(im, ax=ax, shrink=0.8)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def generate_element_coverage():
    """生成四要素覆盖率图。"""
    corpus = _build_synthetic_corpus()
    results = [lint_goal(item["goal"]) for item in corpus]
    
    elements = ['A: 数值门', 'B: 参考锚', 'C: 负控', 'D: 工件路径']
    pos_present = [0, 0, 0, 0]
    neg_present = [0, 0, 0, 0]
    
    for r, item in zip(results, corpus):
        idx = 0
        if r.score_a.present: pos_present[0] += 1 if item["label"] == 1 else neg_present[0]
        if r.score_b.present: pos_present[1] += 1 if item["label"] == 1 else neg_present[1]
        if r.score_c.present: pos_present[2] += 1 if item["label"] == 1 else neg_present[2]
        if r.score_d.present: pos_present[3] += 1 if item["label"] == 1 else neg_present[3]
    
    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(elements))
    width = 0.35
    
    bars1 = ax.bar(x - width/2, pos_present, width, label='Positive (label=1)', color='#4472C4')
    bars2 = ax.bar(x + width/2, neg_present, width, label='Negative (label=0)', color='#ED7D31')
    
    ax.set_xlabel('Element', fontsize=12)
    ax.set_ylabel('Count', fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels(elements)
    ax.legend()
    ax.set_title('Element Coverage by Label', fontsize=14)
    
    for bar in bars1:
        height = bar.get_height()
        ax.annotate(f'{int(height)}', xy=(bar.get_x() + bar.get_width()/2, height),
                    xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=9)
    
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def main():
    report = run_full_validation()
    
    fig1 = generate_score_distribution(report)
    fig2 = generate_confusion_matrix(report)
    fig3 = generate_element_coverage()
    
    figures = [
        {"name": "score_distribution", "caption": "分档接受率分布图（N=120 合成语料）", "png_base64": fig1},
        {"name": "confusion_matrix", "caption": "混淆矩阵（阈值≥2）", "png_base64": fig2},
        {"name": "element_coverage", "caption": "四要素覆盖率对比", "png_base64": fig3},
    ]
    
    # Save figures as PNG files
    import os
    os.makedirs('/root/tp-agent/work/m10904/artifacts/goal_lint', exist_ok=True)
    
    for fig in figures:
        path = f'/root/tp-agent/work/m10904/artifacts/goal_lint/{fig["name"]}.png'
        with open(path, 'wb') as f:
            f.write(base64.b64decode(fig["png_base64"]))
        print(f'Saved {path}')
    
    # Save report JSON
    report_path = '/root/tp-agent/work/m10904/artifacts/goal_lint/validation_report.json'
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f'Saved {report_path}')
    
    return report, figures


if __name__ == "__main__":
    main()
