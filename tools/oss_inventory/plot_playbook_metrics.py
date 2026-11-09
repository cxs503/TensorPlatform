#!/usr/bin/env python3
"""Render docs/oss_playbook_metrics.png from the live campaign state.

Every number is read from the database (or from the saved metrics JSON) — no
value is typed by hand, so the figure cannot drift from the ledger.
"""
from __future__ import annotations

import collections
import json
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Keep figure text legible on hosts without a CJK font: use the Latin titles
# for the PNG (the Chinese wording lives in the markdown playbook), and fall
# back gracefully if a CJK face happens to be installed.
_CJK = None
for _cand in ("Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei", "Source Han Sans SC"):
    if any(_cand.lower() in f.lower() for f in matplotlib.font_manager.fontManager.get_font_names()):
        _CJK = _cand
        break
if _CJK:
    matplotlib.rcParams["font.sans-serif"] = [_CJK, "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
import numpy as np

from tools.oss_inventory.playbook_metrics import compute_metrics, fetch_state, classify_slug

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "oss_playbook_metrics.png"
METRICS = ROOT / "artifacts" / "oss_playbook" / "metrics_2026-11-09.json"


def main() -> None:
    state = fetch_state()
    m = compute_metrics(state)
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    METRICS.write_text(json.dumps(m, ensure_ascii=False, indent=2))

    inv = {i["repo"]: i["tier"] for i in state["inventory"]}
    per_repo = collections.Counter(s["repo"] for s in state["solutions"])
    tiers = [1, 2, 3]
    deep = [sum(1 for r, t in inv.items() if t == k and per_repo[r] > 0) for k in tiers]
    zero = [sum(1 for r, t in inv.items() if t == k and per_repo[r] == 0) for k in tiers]

    dup = collections.Counter(classify_slug(s["slug"]) for s in state["solutions"])
    transfer = collections.defaultdict(int)
    for s in state["solutions"]:
        pass
    # transfer lives in the oss_solutions table only; pull it explicitly
    sep = "\x1f"
    out = subprocess.run(
        ["psql", "-h", "/var/run/postgresql", "-U", "foundation", "-d",
         "tensorplatform", "-X", "-q", "-tA", "-F", sep, "-c",
         "select transfer, count(*) from oss_solutions group by 1 order by 2 desc"],
        capture_output=True, text=True, check=True)
    for line in out.stdout.splitlines():
        if line:
            k, v = line.split(sep)
            transfer[k] += int(v)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    x = np.arange(len(tiers))
    axes[0].bar(x, deep, label="deep dived (>=1 [OSS-SOL])", color="#2b7bba")
    axes[0].bar(x, zero, bottom=deep, label="registered, 0 rows", color="#d9534f")
    for i, (d, z) in enumerate(zip(deep, zero)):
        axes[0].text(i, d + z + 0.15, f"{d}/{d + z}", ha="center", fontsize=10)
        if z:
            axes[0].text(i, d + z / 2, str(z), ha="center", va="center",
                         color="white", fontsize=10)
    axes[0].set_xticks(x, [f"tier {k}" for k in tiers])
    axes[0].set_ylabel("repositories")
    axes[0].set_title(f"(a) deep-dive coverage {m['step1_inventory']['repos_deep_dived']}"
                      f"/{m['step1_inventory']['repos_registered']} "
                      f"= {m['step1_inventory']['deep_dive_ratio']:.0%}")
    axes[0].legend(fontsize=8)
    axes[0].set_ylim(0, max(np.array(deep) + np.array(zero)) + 2)

    order = ["gap", "namespaced", "free-label"]
    vals = [dup.get(k, 0) for k in order]
    colors = ["#2b7bba", "#5bc0de", "#f0ad4e"]
    bars = axes[1].bar(order, vals, color=colors)
    for b, v in zip(bars, vals):
        axes[1].text(b.get_x() + b.get_width() / 2, v + 1.5, str(v), ha="center")
    axes[1].set_ylabel("oss_solutions rows")
    axes[1].set_title(f"(b) anchor discipline anchored={m['step3_knowledge']['anchored_ratio']:.0%}"
                      f" (gate {m['gates']['anchored_ratio']['threshold']:.0%})")

    names = list(transfer)
    cols = ["#d9534f" if n not in ("adapt", "reference", "fits", "none") else "#2b7bba"
            for n in names]
    bars = axes[2].bar(names, [transfer[n] for n in names], color=cols)
    for b, n in zip(bars, names):
        axes[2].text(b.get_x() + b.get_width() / 2, transfer[n] + 1, str(transfer[n]),
                     ha="center")
    axes[2].tick_params(axis="x", rotation=25)
    axes[2].set_ylabel("rows")
    axes[2].set_title("(c) transfer vocabulary (red = outside contract)")

    fig.suptitle("OSS -> knowledge -> improvement loop metrics (source: read-only SQL on "
                 "oss_inventory / oss_solutions / tensor_dev_missions)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=140)
    print(f"wrote {OUT}")
    print(json.dumps({k: m["gates"][k] for k in m["gates"]}, ensure_ascii=False))
    print("dup class:", dict(dup), "transfer:", dict(transfer))


if __name__ == "__main__":
    main()