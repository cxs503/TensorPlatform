"""p399w2-n02 报告配图：混淆矩阵（前后对照）、逐级召回、置信度分布。

数据全部来自 ``artifacts/capability_ledger_auto/`` 的机器产物（零手抄）::

    eval_validity_p399w2.json           修复后度量
    eval_validity_p399w2_baseline.json  对照基线（code_roots=("src",)）
    ledger_audit_p399w2.json            平台 21 项能力（含置信度/证据根）
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "capability_ledger_auto"
FIG = ROOT / "artifacts" / "capability_ledger_auto" / "figures"
LEVELS = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")


def _load(name: str) -> dict:
    return json.loads((ART / name).read_text(encoding="utf-8"))


def fig_confusion(fixed: dict, base: dict, out: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.0))
    for ax, payload, title in (
        (axes[0], base, "(a) before: code scanned in src/ only"),
        (axes[1], fixed, "(b) after: multi-root scan + weak-evidence split"),
    ):
        mat = np.array([[payload["confusion_matrix"][t][p] for p in LEVELS] for t in LEVELS])
        im = ax.imshow(mat, cmap="Blues", vmin=0, vmax=max(1, mat.max()))
        ax.set_xticks(range(len(LEVELS)))
        ax.set_yticks(range(len(LEVELS)))
        ax.set_xticklabels(LEVELS, rotation=35, ha="right", fontsize=8)
        ax.set_yticklabels(LEVELS, fontsize=8)
        ax.set_xlabel("predicted grade")
        ax.set_ylabel("planted (truth)")
        ax.set_title(f"{title}\naccuracy={payload['accuracy']:.3f}, "
                     f"macro-recall={payload['macro_recall']:.3f}", fontsize=10)
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                ax.text(j, i, str(mat[i, j]), ha="center", va="center", fontsize=9,
                        color="white" if mat[i, j] > mat.max() * 0.6 else "black")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03, label="cases")
    fig.suptitle("Grader eval-validity on 21 labelled synthetic fixtures (5 levels x 3 + 2 variants x 3)")
    fig.tight_layout()
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def fig_recall(fixed: dict, base: dict, out: Path) -> Path:
    x = np.arange(len(LEVELS))
    before = [base["per_level"][l]["recall"] for l in LEVELS]
    after = [fixed["per_level"][l]["recall"] for l in LEVELS]
    width = 0.38
    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    b1 = ax.bar(x - width / 2, before, width, label="before (src/ only)", color="#c0392b")
    b2 = ax.bar(x + width / 2, after, width, label="after (multi-root)", color="#27ae60")
    for bars in (b1, b2):
        for rect in bars:
            h = rect.get_height()
            ax.text(rect.get_x() + rect.get_width() / 2, h + 0.02, f"{h:.2f}",
                    ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(LEVELS, rotation=20, ha="right", fontsize=9)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("recall")
    ax.set_title("Per-level recall before/after the code-scan-root fix\n"
                 f"macro-recall {base['macro_recall']:.2f} -> {fixed['macro_recall']:.2f}; "
                 f"backend_root variant "
                 f"{base['by_variant']['backend_root']['correct']}/{base['by_variant']['backend_root']['total']}"
                 f" -> {fixed['by_variant']['backend_root']['correct']}/{fixed['by_variant']['backend_root']['total']}",
                 fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def fig_confidence(audit: dict, fixed: dict, out: Path) -> Path:
    """置信度是否携带证据信息：平台 21 项 + 算例 21 例，按等级分布。"""
    old_const = {"REAL": 0.90, "MOCK": 0.85, "STUB": 0.85, "WITHHELD": 0.70,
                 "NOT_SUPPORTED": 0.95}
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.8), sharey=True)
    groups = [
        (axes[0], "platform ledger (21 capabilities)",
         _group(audit["rows"])),
        (axes[1], "labelled fixtures (21 cases)",
         _group(fixed["rows"])),
    ]
    rng = np.random.default_rng(7)
    for ax, title, grouped in groups:
        for i, level in enumerate(LEVELS):
            vals = grouped.get(level, [])
            if not vals:
                continue
            jitter = rng.uniform(-0.12, 0.12, size=len(vals))
            ax.scatter(np.full(len(vals), i) + jitter, vals, s=26, alpha=0.75,
                       label="evidence-driven" if i == 0 else None)
            ax.hlines(float(np.mean(vals)), i - 0.28, i + 0.28, color="black", lw=1.6)
            ax.hlines(old_const[level], i - 0.34, i + 0.34, color="#c0392b",
                      lw=1.2, linestyles="--")
        ax.set_xticks(range(len(LEVELS)))
        ax.set_xticklabels(LEVELS, rotation=20, ha="right", fontsize=9)
        ax.set_title(title, fontsize=10)
        ax.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("confidence")
    axes[0].set_ylim(0.25, 1.02)
    axes[0].scatter([], [], s=26, color="tab:blue", label="evidence-driven (this work)")
    axes[0].hlines([], [], [], color="#c0392b", linestyle="--", label="old rule constant")
    axes[0].hlines([], [], [], color="black", lw=1.6, label="mean")
    axes[0].legend(fontsize=7, loc="lower left")
    fig.suptitle("Confidence carries evidence information (was a per-rule constant)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def _group(rows: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for row in rows:
        out.setdefault(row["grade"], []).append(float(row["confidence"]))
    return out


def main() -> int:
    fixed = _load("eval_validity_p399w2.json")
    base = _load("eval_validity_p399w2_baseline.json")
    audit = _load("ledger_audit_p399w2.json")
    FIG.mkdir(parents=True, exist_ok=True)
    outs = [
        fig_confusion(fixed, base, FIG / "fig1_confusion_matrix.png"),
        fig_recall(fixed, base, FIG / "fig2_per_level_recall.png"),
        fig_confidence(audit, fixed, FIG / "fig3_confidence_information.png"),
    ]
    for p in outs:
        print(p.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())