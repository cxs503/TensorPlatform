"""判据治理成熟度评分：覆盖率 / 可派生率 / 漂移检出率 / 假阳性安全度 四维。

四维均映射到 [0,1]，门槛 0.7（任务书 C4）。评分不是自报：每个维度的分子分母都
来自注册表校验、派生引擎与漂移门的**计量输出**，本模块只做除法与阈值裁判。
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

DIMENSIONS: Tuple[str, ...] = ("coverage", "derivability", "drift_detection", "false_positive_safety")

#: 图内标签一律用 ASCII：容器内 matplotlib 缺 CJK 字体，中文标签会渲染成方框
#: （本轮实测踩到的坑）。中文定义留在报告表格里。
DIMENSION_LABELS = {
    "coverage": "coverage",
    "derivability": "derivability",
    "drift_detection": "drift detection",
    "false_positive_safety": "1 - false positive rate",
}

GATE_THRESHOLD = 0.7


def score(registry_kpis: Dict[str, object], derivation_summary: Dict[str, object],
          gate_result: Dict[str, object]) -> Dict[str, object]:
    """由三份计量输出计算四维成熟度。"""
    coverage = float(registry_kpis["coverage"])
    derivability = float(derivation_summary["derivability_rate"])
    detection = float(gate_result["detection_rate"])
    fp_rate = float(gate_result["false_positive_rate"])
    safety = 1.0 - fp_rate

    dims = {
        "coverage": {
            "score": coverage,
            "numerator": registry_kpis["registered_total"],
            "denominator": registry_kpis["expected_total"],
            "definition": "入册判据数 / 声明判据总数",
        },
        "derivability": {
            "score": derivability,
            "numerator": derivation_summary["analytic_derivable"],
            "denominator": derivation_summary["analytic_total"],
            "definition": "分析型判据中推导链可复算者 / 分析型判据总数",
        },
        "drift_detection": {
            "score": detection,
            "numerator": gate_result["tamper_detected"],
            "denominator": gate_result["tamper_total"],
            "definition": "程序化篡改被检出数 / 篡改变体总数",
        },
        "false_positive_safety": {
            "score": safety,
            "numerator": gate_result["clean_total"] - gate_result["false_positive"],
            "denominator": gate_result["clean_total"],
            "definition": "1 − 假阳性率（未改动副本被误告警的比例）",
        },
    }
    failing = [name for name, row in dims.items() if row["score"] < GATE_THRESHOLD]
    return {
        "dimensions": dims,
        "gate_threshold": GATE_THRESHOLD,
        "min_score": min(row["score"] for row in dims.values()),
        "mean_score": sum(row["score"] for row in dims.values()) / len(dims),
        "failing": failing,
        "verdict": "PASS" if not failing else "FAIL",
    }


def radar_figure(maturity: Dict[str, object], path: str, title: str = "Criteria governance maturity"):
    """四维雷达图（matplotlib Agg，无显示依赖）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    names: List[str] = list(DIMENSIONS)
    labels = [DIMENSION_LABELS[n] for n in names]
    scores = [float(maturity["dimensions"][n]["score"]) for n in names]
    angles = np.linspace(0.0, 2.0 * np.pi, len(names), endpoint=False).tolist()
    angles += angles[:1]
    values = scores + scores[:1]

    fig = plt.figure(figsize=(7.2, 6.4), dpi=150)
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_offset(np.pi / 2.0)
    ax.set_theta_direction(-1)
    ax.plot(angles, values, "o-", linewidth=2.0, color="#1f6feb", label="maturity score")
    ax.fill(angles, values, alpha=0.25, color="#1f6feb")
    gate = float(maturity["gate_threshold"])
    ax.plot(angles, [gate] * len(angles), "--", linewidth=1.2, color="#d29922", label=f"gate {gate}")
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0.0, 1.05)
    ax.set_yticks([0.2, 0.4, 0.6, 0.7, 0.8, 1.0])
    ax.set_yticklabels(["0.2", "0.4", "0.6", "0.7", "0.8", "1.0"], fontsize=7)
    for angle, value, name in zip(angles[:-1], scores, names):
        ax.annotate(f"{value:.3f}", (angle, min(value + 0.06, 1.02)), ha="center", fontsize=8)
    ax.set_title(title, pad=18, fontsize=11)
    ax.legend(loc="lower right", bbox_to_anchor=(1.15, -0.08), fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def convergence_figure(series: Dict[str, Dict[str, Sequence[float]]], path: str,
                       declared_exponent: float = 2.0) -> str:
    """细化扫描误差 vs h 的双对数图（含声明阶参考斜率）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(6.4, 5.0), dpi=150)
    colors = ["#1f6feb", "#d1242f", "#1a7f37", "#8250df"]
    hmax = max(max(s["hs"]) for s in series.values())
    emax = max(max(s["errors"]) for s in series.values())
    for idx, (label, payload) in enumerate(sorted(series.items())):
        hs = np.asarray(payload["hs"], dtype=float)
        errors = np.asarray(payload["errors"], dtype=float)
        slope = float(np.polyfit(np.log(hs), np.log(errors), 1)[0])
        ax.loglog(hs, errors, "o-", color=colors[idx % len(colors)], linewidth=1.8,
                  label=f"{label} (p={slope:.3f})")
    ref_h = np.array([hmax * 0.9, hmax * 0.9 / 16.0])
    ref_e = emax * (ref_h / ref_h[0]) ** declared_exponent
    ax.loglog(ref_h, ref_e / ref_e[-1] * (emax * 1e-3), "k--", linewidth=1.0,
              label=f"reference slope p={declared_exponent:.1f}")
    ax.set_xlabel("h (grid spacing)")
    ax.set_ylabel("error (relative)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    ax.set_title("Refinement scan: observed decay exponents", fontsize=11)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path