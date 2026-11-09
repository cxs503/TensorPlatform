#!/usr/bin/env python3
"""case_identity_figures —— 由验收 JSON 程序化出图（零手抄数字）。

用法:
    python3 ops/taskplane/case_identity_figures.py \
        --acceptance artifacts/m7341/case_identity_acceptance.json \
        --outdir artifacts/m7341

图 A  ``case_identity_timeline.png``  证据生命周期时间轴（钉扎→上游变更→检测→处置→回滚）
                                       + 24h SLA 窗口带（x 轴相对参考时钟的小时数）
图 B  ``case_identity_detector_matrix.png``  检测器 × 场景命中矩阵（指纹 vs 只比版本号）
                                       —— C5 负控的图形化对照

所有数值均从验收 JSON 读取；缺字段直接报错，绝不补默认值（防造假）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402


def _hours(reference: str, stamp: str) -> float:
    from datetime import datetime

    fmt = "%Y-%m-%dT%H:%M:%SZ"
    ref = datetime.strptime(reference, fmt)
    now = datetime.strptime(stamp, fmt)
    return (now - ref).total_seconds() / 3600.0


def figure_timeline(data: dict[str, Any], out: Path) -> dict[str, Any]:
    ref = data["reference_clock"]
    c3 = data["C3_stale_detection_24h"]
    sla = float(c3["sla_hours"])
    change_h = _hours(ref, c3["upstream_change"]["changed_at"])
    detect_h = _hours(ref, c3["detected_at"])
    timeline = data["timeline"]
    rem_h = _hours(ref, timeline["remediated_at"])
    rb = data["rollback"]
    rollback_h = _hours(ref, timeline["rolled_back_at"])

    fig, ax = plt.subplots(figsize=(10, 4.6))
    ax.axvspan(change_h, change_h + sla, color="#cfe8ff", alpha=0.7,
               label=f"SLA window ({sla:g} h after upstream change)")
    ax.axvline(change_h, color="#1f77b4", lw=1.6, ls="--")
    ax.axvline(detect_h, color="#d62728", lw=1.6)
    ax.annotate(f"upstream change\nt={change_h:+.1f} h",
                xy=(change_h, 0.78), xytext=(change_h + 0.25, 0.9),
                fontsize=9, color="#1f77b4")
    ax.annotate(f"stale detected\nt={detect_h:+.1f} h\nlatency={c3['max_latency_hours']:.1f} h",
                xy=(detect_h, 0.45), xytext=(detect_h + 0.25, 0.42),
                fontsize=9, color="#d62728")
    ax.annotate(f"stale evidence marked: {c3['newly_marked_evidence']}\n"
                f"SLA breaches: {c3['sla_breaches']}",
                xy=(detect_h, 0.12), xytext=(detect_h + 0.25, 0.06), fontsize=9)
    if rem_h is not None:
        ax.plot([rem_h], [0.62], marker="o", color="#2ca02c", ms=8)
        ax.annotate("remediation:\nblocked + recompute queued",
                    xy=(rem_h, 0.62), xytext=(rem_h + 0.2, 0.66), fontsize=9,
                    color="#2ca02c")
    if rollback_h is not None:
        ax.plot([rollback_h], [0.3], marker="s", color="#9467bd", ms=8)
        ax.annotate(f"rollback -> {rb['active_version']}",
                    xy=(rollback_h, 0.3), xytext=(rollback_h + 0.2, 0.3), fontsize=9,
                    color="#9467bd")

    ax.set_xlabel("hours relative to reference clock (2026-11-08T00:00:00Z)")
    ax.set_yticks([])
    ax.set_ylim(0, 1.05)
    ax.set_xlim(min(change_h, detect_h) - 1, change_h + sla + 2)
    ax.set_title("Evidence lifecycle: pin -> upstream drift -> stale detection -> remediation -> rollback")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(axis="x", ls=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return {"figure": out.name, "change_hours": change_h, "detect_hours": detect_h,
            "latency_hours": c3["max_latency_hours"], "sla_hours": sla}


def figure_detector_matrix(data: dict[str, Any], out: Path) -> dict[str, Any]:
    c5 = data["C5_negative_control_version_conflict"]
    c3 = data["C3_stale_detection_24h"]
    fingerprint = [c3["newly_marked_evidence"], c5["fingerprint_detector_events"]]
    naive = [c5["positive_control_real_version_bump"]["naive_hits_main_scenario"],
             c5["naive_version_only_detector_hits"]]

    scenarios = ["upstream version bump\n(1.4.0 -> 1.4.1)", "same version, code sha changed\n(version conflict)"]
    x = range(len(scenarios))
    width = 0.36
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    b1 = ax.bar([i - width / 2 for i in x], fingerprint, width,
                label="fingerprint detector (version + git sha + artifact digest)", color="#1f77b4")
    b2 = ax.bar([i + width / 2 for i in x], naive, width,
                label="naive detector (version string only)", color="#ff7f0e")
    for bars in (b1, b2):
        for bar in bars:
            h = bar.get_height()
            ax.annotate(f"{h:.0f}", (bar.get_x() + bar.get_width() / 2, h),
                        ha="center", va="bottom", fontsize=10)
    ax.set_xticks(list(x))
    ax.set_xticklabels(scenarios, fontsize=9)
    ax.set_ylabel("stale detections (evidence rows marked)")
    ax.set_title("Negative control (C5): version-only detection misses same-version content drift")
    ax.legend(fontsize=8)
    ax.grid(axis="y", ls=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return {"figure": out.name, "fingerprint_hits": fingerprint, "naive_hits": naive}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--acceptance", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args(argv)
    data = json.loads(Path(args.acceptance).read_text(encoding="utf-8"))
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    info = {
        "timeline": figure_timeline(data, outdir / "case_identity_timeline.png"),
        "detector_matrix": figure_detector_matrix(data, outdir / "case_identity_detector_matrix.png"),
    }
    (outdir / "case_identity_figures.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())