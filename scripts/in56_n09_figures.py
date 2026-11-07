"""IN56-N09 出图：从 artifacts/IN56_N09/analysis.json 出 4 张判据图（英文标注，仓库无 CJK 字体）。"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts/IN56_N09"
OUT = ART / "figures"
ARMS = ("T1", "T2", "T3")
ARM_TITLE = {"T1": "T1 wording inflation", "T2": "T2 fabricated numbers",
             "T3": "T3 fake citations"}
COLOR = {"T1": "#4C78A8", "T2": "#E45756", "T3": "#54A24B"}
SORDER = ("S-N", "S1", "S2", "S3", "S4", "S5")


def load() -> dict:
    return json.loads((ART / "analysis.json").read_text(encoding="utf-8"))


def fig_grades(a: dict) -> Path:
    """S 级分布：对照侧的分布取自同一批配对（三臂的 c 完全相同）。"""
    arms = ("C",) + ARMS
    labels = {"C": "Control", "T1": "T1 wording", "T2": "T2 numbers", "T3": "T3 citations"}
    counts = {arm: {s: 0 for s in SORDER} for arm in arms}
    for p in a["arms"]["T2"]["pairs"]:
        counts["C"][p["c"]] = counts["C"].get(p["c"], 0) + 1
    for arm in ARMS:
        for p in a["arms"][arm]["pairs"]:
            counts[arm][p["t"]] = counts[arm].get(p["t"], 0) + 1
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    palette = {"S-N": "#B0B0B0", "S1": "#CFE8F3", "S2": "#7FCDBB", "S3": "#41B6C4",
               "S4": "#2C7FB8", "S5": "#253494"}
    bottom = np.zeros(len(arms))
    for s in SORDER:
        vals = np.array([counts[arm].get(s, 0) for arm in arms], dtype=float)
        if vals.sum() == 0:
            continue
        ax.bar(range(len(arms)), vals, bottom=bottom, label=s, color=palette[s])
        bottom += vals
    ax.set_xticks(range(len(arms)))
    ax.set_xticklabels([labels[x] for x in arms])
    ax.set_ylabel("bases (of 40)")
    ax.set_title("Consensus S-grade distribution by arm (IN56-N09)")
    ax.legend(ncol=6, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    fig.tight_layout()
    p = OUT / "fig1_grade_dist_by_arm.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    return p


def fig_effect(a: dict) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.9))
    mde = a["mde"]["mde_dz"]
    for i, arm in enumerate(ARMS):
        blk = a["arms"][arm]
        lo, hi = blk["cohen_d_ci95"]
        axes[0].plot([lo, hi], [i, i], color=COLOR[arm], lw=3, solid_capstyle="round")
        axes[0].plot([blk["cohen_d"]], [i], "o", color=COLOR[arm], ms=8)
        f, (flo, fhi) = blk["flip"]["flip_rate"], blk["flip_ci95_exact"]
        axes[1].plot([flo, fhi], [i, i], color=COLOR[arm], lw=3, solid_capstyle="round")
        axes[1].plot([f], [i], "o", color=COLOR[arm], ms=8)
    axes[0].axvline(0.0, color="k", lw=0.8)
    axes[0].axvline(0.2, color="r", ls="--", lw=1.0, label="equivalence margin d=0.2")
    axes[0].axvline(mde, color="gray", ls=":", lw=1.0, label=f"MDE d_z={mde}")
    axes[0].set_yticks(range(len(ARMS)))
    axes[0].set_yticklabels([ARM_TITLE[x] for x in ARMS], fontsize=8)
    axes[0].set_xlabel("Cohen d (S-rank, treat vs control), 95% cluster bootstrap CI")
    axes[0].set_title("Effect size")
    axes[0].legend(fontsize=7, loc="lower right")
    axes[1].axvline(a["mde"]["mde_flip_rate"], color="gray", ls=":",
                    label=f"MDE flip rate={a['mde']['mde_flip_rate']}")
    axes[1].set_yticks(range(len(ARMS)))
    axes[1].set_yticklabels([ARM_TITLE[x] for x in ARMS], fontsize=8)
    axes[1].set_xlabel("S-grade flip rate (exact Clopper-Pearson 95% CI)")
    axes[1].set_title("Flip rate")
    axes[1].legend(fontsize=7, loc="lower right")
    fig.suptitle("Does surface form move the grade? (n=40 paired bases)", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    p = OUT / "fig2_effect_and_flip.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    return p


def fig_paired(a: dict, strata: dict) -> Path:
    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    pairs = a["arms"]["T2"]["pairs"]
    rank = {"S-N": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4, "S5": 5}
    rng = np.random.default_rng(7)
    for p in pairs:
        y0, y1 = rank[p["c"]], rank[p["t"]]
        stratum = strata.get(str(p["base_id"]), "report")
        color = "#E45756" if y1 > y0 else ("#999999" if y1 == y0 else "#4C78A8")
        ax.plot([0, 1], [y0 + rng.normal(0, 0.03), y1 + rng.normal(0, 0.03)],
                color=color, alpha=0.75, lw=1.2,
                marker="o", ms=4, mfc=color, mec="none",
                ls="-" if stratum == "report" else "--")
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["control", "T2 fabricated numbers"])
    ax.set_yticks(sorted(set(rank.values())))
    ax.set_yticklabels(["S-N", "S1", "S2", "S3", "S4", "S5"])
    ax.set_ylabel("consensus S-grade")
    ax.set_title("Paired per-base grade under T2 (red = upgrade, dashed = summary-only stratum)")
    fig.tight_layout()
    p = OUT / "fig3_paired_T2.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    return p


def fig_strata_controls(a: dict) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.8))
    strata = a["strata"]
    names = list(strata)
    x = np.arange(len(ARMS))
    w = 0.8 / max(1, len(names))
    for i, s in enumerate(names):
        vals = [strata[s]["arms"][arm]["flip"]["flip_rate"] for arm in ARMS]
        axes[0].bar(x + i * w, vals, width=w, label=f"{s} stratum (n={strata[s]['n_bases']})")
    axes[0].set_xticks(x + w * (len(names) - 1) / 2)
    axes[0].set_xticklabels([ARM_TITLE[a_] for a_ in ARMS], fontsize=8)
    axes[0].set_ylabel("S-grade flip rate")
    axes[0].set_title("Flip rate by pre-registered stratum")
    axes[0].legend(fontsize=7)
    # 负控 + 污染量
    rep = a["replay"]
    arms = list(a["structural_findings"]["artifact_verify_by_arm"])
    nf = [a["structural_findings"]["artifact_verify_by_arm"][k]["mean_not_found_local"]
          for k in arms]
    axes[1].bar(range(len(arms)), nf, color=["#999999", "#4C78A8", "#E45756", "#54A24B"])
    axes[1].set_xticks(range(len(arms)))
    axes[1].set_xticklabels(["Control", "T1", "T2", "T3"], fontsize=8)
    axes[1].set_ylabel("unverifiable citations per base")
    axes[1].set_title(f"Citation probe pollution (replay consistency "
                      f"{rep['exact_match_cells']}/{rep['cells']} cells exact)")
    fig.tight_layout()
    p = OUT / "fig4_strata_and_controls.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    return p


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    a = load()
    corpus = json.loads((ART / "base_corpus.json").read_text(encoding="utf-8"))
    strata = {int(b["mission_id"]): b["stratum"] for b in corpus}
    paths = [fig_grades(a), fig_effect(a), fig_paired(a, strata), fig_strata_controls(a)]
    meta = [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size} for p in paths]
    (OUT / "figures.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())