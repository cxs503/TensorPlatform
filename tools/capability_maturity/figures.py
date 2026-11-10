"""两张（及以上）机读产物图：能力审计矩阵、升级路径图、升级前后误差对照。"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

LEVEL_COLOR = {"REAL": "#2e7d32", "WITHHELD": "#f9a825", "MOCK": "#c62828",
               "STUB": "#8e24aa", "NOT_SUPPORTED": "#616161"}
STATUS_COLOR = {"done": "#2e7d32", "planned": "#90caf9", "blocked": "#e0e0e0"}


def audit_matrix(rows: Sequence[Any], counts: Mapping[str, int], out: Path) -> Path:
    """图 1：21 项能力的「自述 vs 对账」等级矩阵。"""
    fig, ax = plt.subplots(figsize=(9, max(6, 0.34 * len(rows) + 1.6)))
    levels = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
    rank = {lv: i for i, lv in enumerate(levels)}
    labels = [r.cap_id for r in rows]
    for j, (key, getter) in enumerate((("自述 / declared", lambda r: r.declared),
                                       ("对账 / reconciled", lambda r: r.reconciled))):
        for i, r in enumerate(rows):
            lv = getter(r)
            ax.add_patch(plt.Rectangle((j - 0.42, i - 0.42), 0.84, 0.84,
                                       color=LEVEL_COLOR.get(lv, "#bdbdbd"), alpha=0.9))
            ax.text(j, i, f"{lv}\n(r{rank[lv]})", ha="center", va="center",
                    fontsize=6.2, color="white")
    ax.set_xlim(-0.6, 1.6)
    ax.set_ylim(-0.7, len(rows) - 0.3)
    ax.invert_yaxis()
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["declared\n(registry claim)", "reconciled\n(audited)"], fontsize=8)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels, fontsize=6.5, family="monospace")
    ax.set_title("Fig.1 Capability audit matrix: declared vs reconciled evidence level (21 capabilities)\n"
                 f"reconciled: REAL={counts.get('REAL', 0)}  WITHHELD={counts.get('WITHHELD', 0)}"
                 f"  MOCK={counts.get('MOCK', 0)}  STUB={counts.get('STUB', 0)}", fontsize=9)
    ax.legend(handles=[Patch(color=LEVEL_COLOR[lv], label=f"{lv}(rank {rank[lv]})")
                       for lv in levels], fontsize=7, loc="lower right", ncol=3)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def upgrade_path_graph(paths: Sequence[Any], coverage: Mapping[str, Any],
                       promoted: Sequence[str], out: Path) -> Path:
    """图 2：升级路径图——每项能力的四阶状态（done/planned/blocked）。"""
    stage_names = [s["stage"] for s in paths[0].stages] if paths else []
    fig, ax = plt.subplots(figsize=(10, max(6, 0.32 * len(paths) + 1.8)))
    for i, p in enumerate(paths):
        for j, st in enumerate(p.stages):
            status = "done" if p.capability_id in promoted else st["status"]
            ax.add_patch(plt.Rectangle((j - 0.45, i - 0.4), 0.9, 0.8,
                                       color=STATUS_COLOR.get(status, "#eeeeee"),
                                       ec="#616161", lw=0.4))
            if status == "done":
                ax.text(j, i, "done", ha="center", va="center", fontsize=6.5,
                        color="white")
            elif status == "blocked":
                ax.text(j, i, "⛔", ha="center", va="center", fontsize=7, color="#9e9e9e")
    ax.set_xlim(-0.6, len(stage_names) - 0.4)
    ax.set_ylim(-0.7, len(paths) - 0.3)
    ax.invert_yaxis()
    ax.set_xticks(range(len(stage_names)))
    ax.set_xticklabels([f"{k+1}.{n}" for k, n in enumerate(stage_names)], fontsize=7.5)
    ax.set_yticks(range(len(paths)))
    ax.set_yticklabels([f"{p.capability_id} [{p.from_level}]" for p in paths],
                       fontsize=6.2, family="monospace")
    ax.set_title("Fig.2 Upgrade path graph: algorithm -> unit test -> benchmark -> evidence registration\n"
                 f"coverage {coverage['covered_count']}/{coverage['required_count']} = "
                 f"{coverage['coverage_pct']}%; {len(promoted)} completed in this repo, "
                 "shaded = blocked (implementation lives in a domain repo)", fontsize=9)
    ax.legend(handles=[Patch(color=STATUS_COLOR["done"], label="done (all 4 stages here)"),
                       Patch(color=STATUS_COLOR["planned"], label="planned (actionable)"),
                       Patch(color=STATUS_COLOR["blocked"], label="blocked (domain repo)")],
              fontsize=7, loc="lower right", ncol=3)
    fig.tight_layout()
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def error_improvement(trials: Sequence[Mapping[str, Any]], tolerance: float,
                      out: Path) -> Path:
    """图 3：升级前后逐例误差对照（对数纵轴，含 15% 容差线）。"""
    caps = [t for t in trials if t["max_baseline_error_pct"] is not None]
    if not caps:
        return out
    fig, axes = plt.subplots(1, len(caps), figsize=(4.6 * len(caps), 4.2), squeeze=False)
    for ax, t in zip(axes[0], caps):
        ids = [c["case_id"] for c in t["cases"]]
        new = [max(c["error_pct"], 1e-16) for c in t["cases"]]
        old = [max(c["baseline_error_pct"], 1e-16) if c["baseline_error_pct"] is not None
               else 1e-16 for c in t["cases"]]
        x = range(len(ids))
        ax.bar([i - 0.2 for i in x], old, width=0.4, label="old impl (MOCK/STUB)",
               color="#c62828", alpha=0.85)
        ax.bar([i + 0.2 for i in x], new, width=0.4, label="upgraded impl", color="#2e7d32")
        ax.axhline(tolerance, ls="--", color="#1565c0", lw=1.2,
                   label=f"tolerance {tolerance:g}%")
        ax.set_yscale("log")
        ax.set_xticks(list(x))
        ax.set_xticklabels(ids, rotation=45, ha="right", fontsize=6.5)
        ax.set_ylabel("relative error [%], log scale", fontsize=8)
        ax.set_title(t["capability_id"], fontsize=8, family="monospace")
        ax.legend(fontsize=6)
    fig.suptitle("Fig.3 Per-case error before/after upgrade (reference from an independent provider)", fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out
