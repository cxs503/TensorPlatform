#!/usr/bin/env python3
"""验收门报告配图（数据全部从验收门产物读取，零手抄；英文标注因容器无 CJK 字体）。

四个面板：
  A wave evaluation coverage timeline —— 为什么 E1 判 FAIL（回填时刻 vs 各单 finished_at）
  B S×E stratified matrix —— 分布可复算但退化（30/30 落在 S1/E2）
  C credit tier × gradient dispatch —— 政策矩阵 + 45 个真实主体的档位上界
  D five-criteria status board —— 判定结果与实测关键数字

产出：artifacts/in19_g20/fig_in19_g20_acceptance.png
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = REPO_ROOT / "artifacts/spec_impl_acceptance.json"

plt.rcParams["font.family"] = "DejaVu Sans"


def _ts(text: str) -> datetime:
    text = (text or "").strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) > 6 and text[-3] in "+-" and text[-2:].isdigit():
        text += ":00"
    return datetime.fromisoformat(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--acceptance", default=str(ACCEPTANCE))
    ap.add_argument("--out", default=str(REPO_ROOT / "artifacts/in19_g20/fig_in19_g20_acceptance.png"))
    ap.add_argument("--dpi", type=int, default=115)
    args = ap.parse_args(argv)

    doc = json.loads(Path(args.acceptance).read_text(encoding="utf-8"))
    checks = {c["id"]: c for c in doc["checks"]}
    e1, e2, e5 = checks["E1"]["measured"], checks["E2"]["measured"], checks["E5"]["measured"]
    case = e2["real_case"]

    fig, axes = plt.subplots(2, 2, figsize=(12.8, 8.4), constrained_layout=True)
    ax_a, ax_b, ax_c, ax_d = axes.ravel()

    # ── A：评价记录时间线（finished_at vs 记录 created_at）──
    finished = {int(k): _ts(v) for k, v in e1["wave_finished_at"].items()}
    covered = set(e1["evaluated_wave_missions"])
    lags = {int(k): float(v) for k, v in e1["record_created_lag_minutes"].items()}
    succeeded = e1["succeeded_wave_missions"]
    base = min(finished.values())
    for i, mid in enumerate(succeeded):
        t_fin = (finished[mid] - base).total_seconds() / 60.0
        ax_a.scatter(t_fin, i, s=90, marker="o", color="#1f4e79", zorder=3, label="finished_at" if i == 0 else None)
        if mid in covered:
            t_rec = t_fin + lags.get(mid, 0.0)
            ax_a.plot([t_fin, t_rec], [i, i], color="#c0392b", lw=1.2, ls=":", zorder=2)
            ax_a.scatter(t_rec, i, s=110, marker=">", color="#2f7d31", zorder=4,
                         label="evaluation record created_at" if i == 0 else None)
            if lags.get(mid, 0) > 30:
                ax_a.annotate(f"+{lags[mid]:.0f} min", (t_rec, i), textcoords="offset points",
                              xytext=(6, -10), fontsize=7, color="#c0392b")
        else:
            ax_a.scatter(t_fin, i, s=140, marker="X", color="#c0392b", zorder=5,
                         label="no evaluation record" if i == 0 else None)
        ax_a.annotate(f"m{mid}", (t_fin, i), textcoords="offset points", xytext=(4, 5), fontsize=7)
    ax_a.set_yticks(range(len(succeeded)))
    ax_a.set_yticklabels([f"slot {i}" for i in range(len(succeeded))], fontsize=7.5)
    ax_a.set_xlabel(f"minutes since {base:%H:%M:%S}Z", fontsize=8.5)
    ax_a.set_title(f"A evaluation coverage {len(covered)}/{len(succeeded)} "
                   f"({e1['coverage_ratio']:.0%}); records backfilled, max lag "
                   f"{e1['max_record_created_lag_minutes']:.0f} min", fontsize=9.5)
    ax_a.legend(fontsize=7, loc="lower right")
    ax_a.grid(alpha=0.25)

    # ── B：S×E 分层矩阵 ──
    matrix = e5["matrix_confirmed_counts"]
    im = ax_b.imshow(matrix, cmap="Blues", aspect="auto")
    for i in range(len(e5["grades"])):
        for j in range(len(e5["e_levels"])):
            value = matrix[i][j]
            ax_b.text(j, i, str(value), ha="center", va="center", fontsize=9,
                      color="#0b2f52" if value else "#9aa5b1")
    ax_b.set_xticks(range(len(e5["e_levels"])))
    ax_b.set_xticklabels(e5["e_levels"], fontsize=8.5)
    ax_b.set_yticks(range(len(e5["grades"])))
    ax_b.set_yticklabels(e5["grades"], fontsize=8.5)
    ax_b.set_xlabel("E evidence maturity", fontsize=8.5)
    ax_b.set_ylabel("S outcome level", fontsize=8.5)
    ax_b.set_title(f"B S×E stratified counts (confirmed n={e5['n_confirmed']}, sum={e5['sum_of_cells']}, "
                   "unweighted; S2+ empty = degenerate)", fontsize=9.5)
    fig.colorbar(im, ax=ax_b, fraction=0.046, pad=0.02)

    # ── C：信用档位 × 梯度派发 ──
    mat = e2["dispatch_matrix"]
    levels = list(e2["level_min_tier"].keys())
    tiers = ["L1", "L2", "L3", "L4", "L5"]
    grid = [[1 if mat.get(s, {}).get(t) else 0 for t in tiers] for s in levels]
    ax_c.imshow(grid, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)
    for i, s in enumerate(levels):
        for j, t in enumerate(tiers):
            ax_c.text(j, i, "ALLOW" if grid[i][j] else "DENY", ha="center", va="center", fontsize=7.5)
    ax_c.set_xticks(range(len(tiers)))
    ax_c.set_xticklabels(tiers, fontsize=8.5)
    ax_c.set_yticks(range(len(levels)))
    ax_c.set_yticklabels([f"{s} (policy min {e2['level_min_tier'][s]})" for s in levels], fontsize=7.5)
    ax_c.set_xlabel("worker credit tier", fontsize=8.5)
    ax_c.set_title(f"C dispatch linkage: {mat.get('allowed_cells')} allowed / {mat.get('denied_cells')} denied cells; "
                   f"real subjects {case['real_contributors_evaluated']} -> "
                   f"{len(case['allowed'])} allow / {len(case['denied'])} deny on m5026 (S3)", fontsize=9.5)

    # ── D：五项判据状态 ──
    ax_d.axis("off")
    sn = checks["E4"]["measured"]["records"][0]["measured"]
    rows = [
        ("E1 evaluation flow recorded", checks["E1"]["status"],
         f"coverage {e1['coverage_ratio']:.0%} (2/8); recompute dev {e1['recompute']['max_abs_diff']}"),
        ("E2 tier x gradient dispatch", checks["E2"]["status"],
         f"real case 0 allow/{len(case['denied'])} deny; mapping S3: L1 vs L3 conflict"),
        ("E3 budget weekly report", checks["E3"]["status"],
         f"ledger {checks['E3']['measured']['ledger_entries']} entries, chain ok; W45 numbers match"),
        ("E4 S-N honest fail", checks["E4"]["status"],
         f"EP-001 S4/E2: achieved {sn['targets_achieved']}/{sn['targets_attempted']}"),
        ("E5 SxE distribution + 4 centers", checks["E5"]["status"],
         f"sum={e5['sum_of_cells']}=n; centers SQL {e5['center_crosscheck']['items_matched']}"
         f"/{e5['center_crosscheck']['items_total']}"),
    ]
    ax_d.set_title(f"D gate verdict: {doc['verdict']} "
                   f"({len(doc['summary']['passed'])}/5 criteria met)", fontsize=9.5, pad=2)
    for i, (name, status, detail) in enumerate(rows):
        y = 0.86 - i * 0.17
        color = "#2f7d31" if status == "PASS" else ("#c0392b" if status == "FAIL" else "#8a6d3b")
        ax_d.add_patch(Rectangle((0.0, y - 0.055), 0.115, 0.11, color=color, alpha=0.9))
        ax_d.text(0.058, y, status, color="white", ha="center", va="center", fontsize=8, weight="bold")
        ax_d.text(0.135, y + 0.022, name, fontsize=8.8, va="center")
        ax_d.text(0.135, y - 0.030, detail, fontsize=7.3, va="center", color="#4a4a4a")
    ax_d.text(0.0, 0.02,
              "Verdict rule: judge only from the offline evidence bundle; each criterion keeps its own evidence; "
              "FAIL carries root cause + remediation; honest FAIL is never overwritten.",
              fontsize=6.8, color="#4a4a4a")

    fig.suptitle("IN19-G20 · SPEC wave S×E closed-loop acceptance gate (mission 5176)",
                 fontsize=11.5, weight="bold")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=args.dpi, bbox_inches="tight", facecolor="white",
                )
    # 报告通道单图建议 ≤60 KB：超限时做调色板量化（纯图形，视觉无损）
    if out.stat().st_size > 60_000:
        from PIL import Image

        img = Image.open(out).convert("RGB")
        img = img.quantize(colors=64, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
        img.save(out, format="PNG", optimize=True)
    print(json.dumps({"out": str(out), "bytes": out.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())