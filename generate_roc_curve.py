#!/usr/bin/env python3
"""生成 ROC 曲线图（不依赖 sklearn）。"""

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from tools.criterion_auditor import Adjudicator
from tools.criterion_auditor.config_loader import load_config


def compute_roc_points(y_true: list, y_score: list) -> tuple[list, list, float]:
    """计算 ROC 曲线的 FPR, TPR 和 AUC。"""
    # 二值化：duplicate=1, 其他=0
    y_true_binary = [1 if y == 1 else 0 for y in y_true]
    # 反转分数（因为低分=重复）
    y_score_inv = [1.0 - s for s in y_score]

    # 排序
    pairs = list(zip(y_true_binary, y_score_inv))
    pairs.sort(key=lambda x: x[1], reverse=True)

    # 计算 ROC 点
    total_pos = sum(1 for y, _ in pairs if y == 1)
    total_neg = sum(1 for y, _ in pairs if y == 0)

    if total_pos == 0 or total_neg == 0:
        return [0, 1], [0, 1], 0.5

    fpr_list = [0.0]
    tpr_list = [0.0]
    tp = 0
    fp = 0
    auc_sum = 0.0

    for y, score in pairs:
        if y == 1:
            tp += 1
        else:
            fp += 1
            auc_sum += tp
        fpr_list.append(fp / total_neg)
        tpr_list.append(tp / total_pos)

    auc = auc_sum / (total_pos * total_neg)
    return fpr_list, tpr_list, auc


def main():
    # 加载配置和数据
    cfg = load_config()
    regression_path = Path(__file__).parent / "config" / "criterion_auditor_regression.json"
    regression_data = json.loads(regression_path.read_text(encoding="utf-8"))

    samples = regression_data["samples"]
    adj = Adjudicator(cfg)

    y_true = []
    y_score = []

    print(f"Processing {len(samples)} samples...")

    for i, s in enumerate(samples):
        if i % 10 == 0:
            print(f"  {i}/{len(samples)}")

        # 清空池，只留其他样本
        adj.clear_pool()
        for other in samples:
            if other["id"] != s["id"]:
                adj.add_to_pool(other["id"], other["title"] + " " + other["text"],
                               domain_hint=other.get("domain_hint"))

        result = adj.adjudicate(
            s["id"],
            s["title"] + " " + s["text"],
            domain_hint=s.get("domain_hint"),
        )

        # label: duplicate=1, novel=0, borderline=0.5
        label = s["label"]
        if label == "duplicate":
            y_true.append(1)
        elif label == "novel":
            y_true.append(0)
        else:  # borderline
            y_true.append(0)  # 简化为二分类

        # score: 新颖度分数（越低越可能是重复）
        y_score.append(result.final_score)

    # 计算 ROC
    fpr, tpr, auc = compute_roc_points(y_true, y_score)

    print(f"\nResults:")
    print(f"  AUC = {auc:.4f}")
    print(f"  Total samples: {len(samples)}")
    print(f"  Duplicates: {sum(1 for y in y_true if y == 1)}")
    print(f"  Novels: {sum(1 for y in y_true if y == 0)}")

    # 绘图
    plt.figure(figsize=(8, 6))
    plt.plot(fpr, tpr, color="darkorange", lw=2, label=f"ROC curve (AUC = {auc:.2f})")
    plt.plot([0, 1], [0, 1], color="navy", lw=2, linestyle="--", label="Random")
    plt.xlim([0.0, 1.0])
    plt.ylim([0.0, 1.05])
    plt.xlabel("False Positive Rate")
    plt.ylabel("True Positive Rate")
    plt.title("Receiver Operating Characteristic - Criterion Auditor\n(Physical Triple Alignment + Strength Audit)")
    plt.legend(loc="lower right")
    plt.grid(alpha=0.3)

    # 保存
    artifacts_dir = Path(__file__).parent / "artifacts" / "criterion_auditor"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    output_path = artifacts_dir / "roc_curve.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\nROC curve saved to: {output_path}")

    plt.close()

    # 保存 ROC 数据
    roc_data = {
        "auc": auc,
        "fpr": fpr,
        "tpr": tpr,
        "total_samples": len(samples),
        "duplicates": sum(1 for y in y_true if y == 1),
        "novels": sum(1 for y in y_true if y == 0),
    }
    roc_json_path = artifacts_dir / "roc_data.json"
    roc_json_path.write_text(json.dumps(roc_data, indent=2), encoding="utf-8")
    print(f"ROC data saved to: {roc_json_path}")

    return 0 if auc >= 0.5 else 1


if __name__ == "__main__":
    sys.exit(main())
