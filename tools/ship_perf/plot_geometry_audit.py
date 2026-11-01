#!/usr/bin/env python3
"""Before/after figures for the m1404 geometry validation-debt audit.

Produces (all values computed, none typed by hand):
  artifacts/m1404/geometry_before_after.json
  artifacts/m1404/fig_geometry_error_before_after.png
  artifacts/m1404/fig_stated_vs_implied_cb.png
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[2]
DOMAIN = REPO / "backend" / "api" / "domain"
OUT = REPO / "artifacts" / "m1404"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


GC = _load("geometry_consistency", DOMAIN / "geometry_consistency.py")

# Historical (pre-audit) records exactly as shipped in v1.0 of the dataset/preset.
BEFORE = [
    ("DTMB-5415 dataset", dict(length=5.72, beam=0.759, draft=0.246, cb=0.508,
                               displacement=466.3, displacement_unit="kg")),
    ("KVLCC2 dataset", dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098,
                            displacement=307000.0, displacement_unit="t")),
    ("DTMB-5415 preset", dict(length=5.72, beam=0.759, draft=0.246, cb=0.508,
                              displacement=466.3, displacement_unit="m3")),
    ("KVLCC2 preset", dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098,
                           displacement=307000.0, displacement_unit="m3")),
]
AFTER = [
    ("DTMB-5415 dataset", dict(length=5.72, beam=0.759, draft=0.246, cb=0.508,
                               displacement=556.1, displacement_unit="kg")),
    ("KVLCC2 dataset", dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098,
                            displacement=320474.0, displacement_unit="t")),
    ("DTMB-5415 preset", dict(length=5.72, beam=0.759, draft=0.246, cb=0.508,
                              displacement=0.54247, displacement_unit="m3")),
    ("KVLCC2 preset", dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098,
                           displacement=312657.6, displacement_unit="m3")),
]


def run(rows):
    out = {}
    for label, kw in rows:
        c = GC.check_merchant_hull(label, **kw)
        out[label] = c.as_dict()
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    before, after = run(BEFORE), run(AFTER)
    payload = {
        "gate_rule": "Cb_stated == displacement_volume/(L*B*T)",
        "tolerance_pct": {"pass": GC.DEFAULT_TOL_PCT, "warn": GC.DEFAULT_WARN_PCT},
        "before": before,
        "after": after,
    }
    (OUT / "geometry_before_after.json").write_text(json.dumps(payload, indent=2))

    labels = list(before)
    dev_b = [before[k]["rel_dev_pct"] for k in labels]
    dev_a = [after[k]["rel_dev_pct"] for k in labels]

    fig, ax = plt.subplots(figsize=(9, 4.6))
    x = range(len(labels))
    w = 0.38
    ax.bar([i - w / 2 for i in x], dev_b, w, label="before (v1.0)", color="#c0392b")
    ax.bar([i + w / 2 for i in x], dev_a, w, label="after (v1.1)", color="#27ae60")
    ax.axhline(0, color="k", lw=0.8)
    ax.axhline(GC.DEFAULT_TOL_PCT, color="grey", ls="--", lw=0.8)
    ax.axhline(-GC.DEFAULT_TOL_PCT, color="grey", ls="--", lw=0.8)
    ax.set_yscale("symlog", linthresh=1.0)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=12, fontsize=8)
    ax.set_ylabel("Cb deviation from identity  [%]  (symlog)")
    ax.set_title("Geometry gate: Cb/L/B/T identity deviation before vs after fix")
    for i, v in enumerate(dev_b):
        ax.annotate(f"{v:+.1f}%", (i - w / 2, v), ha="center", va="bottom", fontsize=7)
    for i, v in enumerate(dev_a):
        ax.annotate(f"{v:+.2f}%", (i + w / 2, v), ha="center", va="bottom", fontsize=7)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "fig_geometry_error_before_after.png", dpi=150)

    fig2, ax2 = plt.subplots(figsize=(5.6, 5.0))
    for tag, data, mk in (("before", before, "x"), ("after", after, "o")):
        st = [data[k]["stated_cb"] for k in labels]
        im = [data[k]["implied_cb"] for k in labels]
        ax2.scatter(st, im, marker=mk, label=tag, s=60)
    lim = (0.0, 1.1)
    ax2.plot(lim, lim, color="grey", ls="--", lw=1, label="identity")
    ax2.set_xlim(*lim)
    ax2.set_ylim(*lim)
    ax2.set_xlabel("stated Cb")
    ax2.set_ylabel("implied Cb = V/(L*B*T)")
    ax2.set_title("Stated vs implied block coefficient")
    ax2.legend(fontsize=8)
    fig2.tight_layout()
    fig2.savefig(OUT / "fig_stated_vs_implied_cb.png", dpi=150)

    print(json.dumps({k: {"before": before[k]["rel_dev_pct"],
                          "after": after[k]["rel_dev_pct"]} for k in labels}, indent=1))


if __name__ == "__main__":
    main()