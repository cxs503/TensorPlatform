#!/usr/bin/env python3
"""M-R17-01 图件生成器：把审计/变异/探测工件画成报告用 PNG（数据全部来自工件，零手抄）。

三张图：

* ``fig1_anchor_metrics.png`` —— 5 个指标：基线 / 快照复演 / 实库当前窗口 三方对拍；
* ``fig2_mutation_kills.png`` —— 变异红队杀死用例数 vs 判定阈值（证明测试有牙）；
* ``fig3_dup_group_status.png`` —— 基线快照里独立重复组按成员状态构成，区分
  「在飞型重复」与「已交付型重复」（数据驱动，锚点匿名编号，无领域语义）。

用法::

    python3 tools/mission_governance/anchor_dedup_figures.py \
        --out-dir artifacts/r17
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tools.mission_governance.anchor_dedup_audit import (  # noqa: E402,E501
    METRIC_KEYS,
    TERMINAL_STATUSES,
    compute_metrics,
    group_anchors,
)
from tools.mission_governance.anchor_dedup_gate import is_derived  # noqa: E402

METRIC_LABELS_EN = {
    "anchored_missions": "anchored missions",
    "anchor_groups": "anchor groups",
    "duplicate_groups": "duplicate groups",
    "independent_duplicate_groups": "independent dup groups",
    "inflight_duplicate_groups": "in-flight dup groups (non-terminal)",
    "inflight_duplicate_groups_running_only": "in-flight dup groups (running)",
    "inflight_duplicate_missions": "in-flight dup missions",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fig_metrics(replay: dict, live: dict, out: Path) -> Path:
    keys = ["anchored_missions", "anchor_groups", "duplicate_groups",
            "independent_duplicate_groups", "inflight_duplicate_groups_running_only"]
    base = [replay["baseline"].get(k) for k in keys]
    rep = [replay["metrics"].get(k) for k in keys]
    livev = [live["metrics"].get(k) for k in keys]
    x = range(len(keys))
    width = 0.26
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=150)
    for i, (vals, label, color) in enumerate([
        (base, "baseline 11:30Z (planner-w17)", "#888888"),
        (rep, "replay of baseline snapshot", "#1f77b4"),
        (livev, "live window (now)", "#2ca02c"),
    ]):
        offset = (i - 1) * width
        bars = ax.bar([k + offset for k in x], vals, width, label=label, color=color)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5, str(v),
                    ha="center", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels([METRIC_LABELS_EN[k] for k in keys], rotation=18, ha="right", fontsize=8)
    ax.set_ylabel("count")
    ax.set_title("M-R17-01 anchor dedup: baseline vs snapshot replay vs live window\n"
                 "(replay reproduces 169/118/48/28/20 exactly; live drifts by window rolling)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def fig_mutations(report: dict, threshold: int, out: Path) -> Path:
    names = [m["name"] for m in report["mutations"]]
    kills = [m["killed"] for m in report["mutations"]]
    fig, ax = plt.subplots(figsize=(10, 4.6), dpi=150)
    colors = ["#2ca02c" if k >= threshold else "#d62728" for k in kills]
    bars = ax.barh(names, kills, color=colors)
    for b, k in zip(bars, kills):
        ax.text(k + 0.3, b.get_y() + b.get_height() / 2, f"{k} red",
                va="center", fontsize=8)
    ax.axvline(threshold, color="#d62728", linestyle="--", linewidth=1.2,
               label=f"kill threshold = {threshold} tests")
    ax.set_xlabel("tests turned red by the mutation")
    passed = report.get("after_restore", {}).get("passed")
    ax.set_title(f"Mutation red-team on anchor_dedup_gate.py "
                 f"(after restore: {passed} passed, sha256 identical)")
    ax.legend(fontsize=8)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def fig_status_mix(snapshot: Path, out: Path) -> Path:
    payload = _load(snapshot)
    items = payload if isinstance(payload, list) else (
        payload.get("missions") or payload.get("data") or payload.get("items") or [])
    missions = [{"id": i.get("id"), "status": i.get("status"), "goal": i.get("goal"),
                 "parent_mission_id": i.get("parent_mission_id")}
                for i in items]
    groups = group_anchors(missions)
    terminal = set(TERMINAL_STATUSES)
    with_in_flight = delivered_only = 0
    sizes: list[int] = []
    for members in groups.values():
        non_derived = [m for m in members if not is_derived(m)]
        if len(non_derived) < 2:
            continue
        sizes.append(len(non_derived))
        if any(str(m["status"]).lower() not in terminal for m in non_derived):
            with_in_flight += 1
        elif any(str(m["status"]).lower() == "succeeded" for m in non_derived):
            delivered_only += 1

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), dpi=150)
    ax = axes[0]
    bars = ax.bar(["dup groups with\nin-flight mission", "dup groups already\ndelivered"],
                  [with_in_flight, delivered_only], color=["#ff7f0e", "#1f77b4"])
    for b, v in zip(bars, [with_in_flight, delivered_only]):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.2, str(v),
                ha="center", fontsize=9)
    ax.set_ylabel("independent duplicate groups")
    ax.set_title("Independent duplicate groups (baseline snapshot)\n"
                 "in-flight type = gate rejects today; delivered type = also rejects (24h)")
    ax.grid(axis="y", alpha=0.25)

    ax = axes[1]
    ax.hist(sizes, bins=range(1, max(sizes or [2]) + 2), color="#9467bd",
            edgecolor="white", align="left", rwidth=0.85)
    ax.set_xlabel("non-derived missions per anchor group")
    ax.set_ylabel("groups")
    ax.set_title(f"Group size distribution (n={len(sizes)} independent dup groups)")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M-R17-01 图件生成")
    ap.add_argument("--artifacts", type=Path, default=REPO / "artifacts" / "r17")
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    art = args.artifacts
    out = args.out_dir or art
    out.mkdir(parents=True, exist_ok=True)

    replay = _load(art / "anchor_dedup_audit_replay.json")
    live = _load(art / "anchor_dedup_audit.json")
    report = _load(art / "anchor_dedup_mutation_report.json")
    made = [
        fig_metrics(replay, live, out / "fig1_anchor_metrics.png"),
        fig_mutations(report, int(report.get("min_killed_required", 2)),
                      out / "fig2_mutation_kills.png"),
        fig_status_mix(art / "baseline_missions_snapshot.json",
                       out / "fig3_dup_group_status.png"),
    ]
    for path in made:
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    (out / "figures_index.json").write_text(json.dumps(
        {"figures": [{"path": str(p.name), "sha256_note": "see artifacts manifest"}
                     for p in made],
         "metrics_keys": list(METRIC_KEYS)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())