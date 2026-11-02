"""Figure for the gate wiring audit (m2049) — reads the machine ledger only.

Zero hand-copied numbers: every bar / strip label comes from
``artifacts/m2049_gate_wiring/gate_wiring.json``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ORDER = ("WIRED_RUNTIME", "WIRED_CI", "TOOL_ONLY", "TEST_ONLY", "ORPHAN")
COLOR = {
    "WIRED_RUNTIME": "#1b7f3b",
    "WIRED_CI": "#63b179",
    "TOOL_ONLY": "#d9a441",
    "TEST_ONLY": "#e2703a",
    "ORPHAN": "#a4161a",
}


def main(json_path: str, out_png: str) -> int:
    d = json.loads(Path(json_path).read_text(encoding="utf-8"))
    counts = d["verdict_counts"]
    rows = sorted(d["gates"], key=lambda r: (ORDER.index(r["verdict"]), r["module"]))

    fig = plt.figure(figsize=(13.5, 8.2))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 2.3], wspace=0.28)

    ax = fig.add_subplot(gs[0, 0])
    vals = [counts[v] for v in ORDER]
    bars = ax.barh(list(ORDER)[::-1], vals[::-1],
                   color=[COLOR[v] for v in ORDER][::-1], edgecolor="black", lw=0.6)
    for b, v in zip(bars, vals[::-1]):
        ax.text(b.get_width() + 0.25, b.get_y() + b.get_height() / 2, str(v),
                va="center", fontsize=11, fontweight="bold")
    ax.set_xlabel("gate modules")
    ax.set_title(f"A. Wiring verdict distribution (n={d['n_gate_modules']})", fontsize=11)
    ax.set_xlim(0, max(vals) + 2)
    ax.grid(axis="x", alpha=0.3)

    ax2 = fig.add_subplot(gs[0, 1])
    labels = [Path(r["module"]).stem + ("  (" + "/".join(Path(r["module"]).parts[:1]) + ")")
              for r in rows]
    y = range(len(rows))
    ax2.scatter([ORDER.index(r["verdict"]) for r in rows], list(y),
                c=[COLOR[r["verdict"]] for r in rows], s=46, zorder=3)
    for i, r in enumerate(rows):
        if r.get("in_doc_invoked_closure") or r.get("doc_invoked_sites"):
            ax2.plot([4.42], [i], marker=">", color="#3d5a80", ms=6, zorder=3)
    ax2.set_yticks(list(y))
    ax2.set_yticklabels(labels, fontsize=7.2)
    ax2.set_xticks(range(len(ORDER)))
    ax2.set_xticklabels(ORDER, fontsize=8.5)
    ax2.set_xlim(-0.4, 4.8)
    ax2.invert_yaxis()
    ax2.grid(axis="x", alpha=0.3)
    ax2.set_title("B. Per-gate verdict (▸ = invoked by a documented command, out-of-band)",
                  fontsize=11)
    ax2.legend(handles=[Patch(facecolor=COLOR[v], edgecolor="black", label=v) for v in ORDER],
               loc="lower right", fontsize=8, framealpha=0.9)

    fig.suptitle("m2049 · gate wiring reality audit — static reachability from production entrypoints",
                 fontsize=12.5, y=0.975)
    fig.text(0.5, 0.012,
             f"parsed {d['n_py_files']} python files · runtime entrypoints "
             f"{len(d['entrypoints']['runtime'])} · CI-invoked scripts "
             f"{len(d['entrypoints']['ci_scripts'])} · static reachability proves absence of wiring, "
             "not presence of protection",
             ha="center", fontsize=8.4, style="italic", color="#444")
    fig.savefig(out_png, dpi=155, bbox_inches="tight")
    print("wrote", out_png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(
        sys.argv[1] if len(sys.argv) > 1 else "artifacts/m2049_gate_wiring/gate_wiring.json",
        sys.argv[2] if len(sys.argv) > 2 else "artifacts/m2049_gate_wiring/fig_gate_wiring.png",
    ))