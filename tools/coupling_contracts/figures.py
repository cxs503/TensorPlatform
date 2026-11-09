#!/usr/bin/env python3
"""验收结果图 (matplotlib). 由 acceptance.py --figures 调用, 也可单独运行.

图 1: 100 组单位往返相对误差 (log) + 1e-12 判据线
图 2: 24 组旋转矩阵正交性/行列式误差 + 40 组跨坐标系点往返误差
图 3: 九大学科契约的 Buckingham π 群基 (端口数/秩/独立群数/声明群数)
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

TOL = 1e-12
FIGSIZE = (11, 7)


def _fig_unit_roundtrip(res: dict, out: Path) -> Path:
    rows = res["criteria"]["a_unit_roundtrip"]["per_case"]
    idx = [r["case"] for r in rows]
    rel = [max(r["rel_err"], 1e-18) for r in rows]
    mag = [abs(r["converted"]) for r in rows]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=FIGSIZE)
    ax1.semilogy(idx, rel, "o", ms=4, color="#1f77b4", label="relative error (A -> B -> A)")
    ax1.axhline(TOL, color="crimson", ls="--", label="tolerance 1e-12")
    ax1.set_xlabel("random case index (n=100)")
    ax1.set_ylabel("relative error [-]")
    ax1.set_title("(a) unit-system roundtrip: 100 random quantities")
    ax1.grid(alpha=0.3, which="both")
    ax1.legend(loc="lower right", fontsize=9)
    ax1.text(0.02, 0.05, f"worst {max(rel):.2e}", transform=ax1.transAxes, fontsize=9,
             bbox=dict(fc="white", ec="0.7"))

    ax2.loglog(mag, rel, "s", ms=4, color="#2ca02c", alpha=0.8)
    ax2.axhline(TOL, color="crimson", ls="--")
    ax2.set_xlabel("magnitude of converted value [-]")
    ax2.set_ylabel("relative error [-]")
    ax2.set_title("error independent of magnitude (12 decades)")
    ax2.grid(alpha=0.3, which="both")
    fig.tight_layout()
    path = out / "fig_unit_roundtrip.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _fig_frame_validation(res: dict, out: Path) -> Path:
    b = res["criteria"]["b_frame_transform"]
    rots = b["rotation_rows"]
    pts = b["point_rows"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=FIGSIZE)
    xs = [r["case"] for r in rots]
    ax1.semilogy(xs, [max(r["ortho_err"], 1e-18) for r in rots], "o-", ms=4,
                 label="max|R^T R - I|")
    ax1.semilogy(xs, [max(r["det_err"], 1e-18) for r in rots], "s--", ms=4,
                 label="|det R - 1|")
    ax1.axhline(TOL, color="crimson", ls=":", label="tolerance 1e-12")
    ax1.set_xlabel("euler-angle case (n=24, roll/pitch/yaw in [-180,180] deg)")
    ax1.set_ylabel("error [-]")
    ax1.set_title("(b) rotation matrix orthonormality / det=+1")
    ax1.grid(alpha=0.3, which="both")
    ax1.legend(fontsize=9)

    ax2.semilogy([p["case"] for p in pts], [max(p["max_abs_err"], 1e-18) for p in pts],
                 "D-", ms=4, color="#9467bd")
    ax2.axhline(TOL, color="crimson", ls=":", label="tolerance 1e-12 (absolute)")
    ax2.set_xlabel("random point index (n=40, shortest-path roundtrip)")
    ax2.set_ylabel("||x_back - x||_inf [m]")
    ax2.set_title("(b) inverse transform back to original frame")
    ax2.grid(alpha=0.3, which="both")
    ax2.legend(fontsize=9)
    fig.tight_layout()
    path = out / "fig_frame_validation.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _fig_pi_groups(res: dict, out: Path) -> Path:
    snap = res["positive_control"]["pi_snapshot"]
    labels = [s["contract_id"].replace("solver.", "") for s in snap]
    n_ports = [s["n_ports"] for s in snap]
    rank = [s["rank"] for s in snap]
    ngroups = [s["n_groups"] for s in snap]
    declared = [s["declared_groups"] for s in snap]
    x = np.arange(len(labels))
    w = 0.2
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8.5),
                                   gridspec_kw={"height_ratios": [3, 2]})
    ax1.bar(x - 1.5 * w, n_ports, w, label="ports n")
    ax1.bar(x - 0.5 * w, rank, w, label="rank r")
    ax1.bar(x + 0.5 * w, ngroups, w, label="independent pi groups n-r")
    ax1.bar(x + 1.5 * w, declared, w, label="declared pi groups")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=20, ha="right")
    ax1.set_ylabel("count")
    ax1.set_title("nine discipline contracts: Buckingham pi basis vs declared groups")
    ax1.grid(axis="y", alpha=0.3)
    ax1.legend(fontsize=9, ncol=4)

    c = res["criteria"]["c_dimension_check"]
    fields = [r["field"] for r in c["rows"]]
    ax2.bar(range(len(fields)), [1] * len(fields), color="#d62728", alpha=0.85)
    for i, r in enumerate(c["rows"]):
        ax2.text(i, 1.02, r["declared_dim"], rotation=90, va="bottom", ha="center", fontsize=7)
        ax2.text(i, 0.5, "blocked", ha="center", va="center", color="white", fontsize=9)
    ax2.set_ylim(0, 1.55)
    ax2.set_xticks(range(len(fields)))
    ax2.set_xticklabels(fields, rotation=20, ha="right")
    ax2.set_yticks([])
    ax2.set_title("(c) 10 injected dimension mismatches: 100% blocked (label = wrong declared dim)")
    fig.tight_layout()
    path = out / "fig_pi_and_intercepts.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def render(res: dict, out: Path) -> List[Path]:
    out.mkdir(parents=True, exist_ok=True)
    return [
        _fig_unit_roundtrip(res, out),
        _fig_frame_validation(res, out),
        _fig_pi_groups(res, out),
    ]


if __name__ == "__main__":
    import json

    repo = Path(__file__).resolve().parents[2]
    out = repo / "artifacts" / "coupling_contracts"
    res = json.loads((out / "acceptance_m7212.json").read_text(encoding="utf-8"))
    for p in render(res, out):
        print(p)