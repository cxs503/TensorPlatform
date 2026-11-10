"""跨 suite 汇总图（数据源自 result.json，图上每个数字都是工件里的数字）。

两个面板：
  (a) 逐算例相对误差（柱）+ 预声明阈值（短横标记）—— 直接读出"余量"；
  (b) 判据余量 threshold/actual（>1 = 通过）—— 一眼看出负对照（负对照余量 ≪1）。

用法::

    python3 -m tools.benchmark_runner.report_figure \
        --root artifacts/benchmark_runner --out artifacts/benchmark_runner/summary.png
"""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, List, Optional

from . import artifact as art


def load_rows(root: str | pathlib.Path,
              only_suites: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """读工件树里的 result.json；``only_suites`` 可按 suite 名白名单过滤。"""
    rows: List[Dict[str, Any]] = []
    for res in sorted(pathlib.Path(root).glob("*/*/result.json")):
        d = art.read_json(res)
        if only_suites and d.get("suite") not in only_suites:
            continue
        for c in d.get("cases", []):
            met = c.get("metrics") or {}
            thr = None
            for chk in c.get("checks", []):
                if chk.get("metric") == "rel_error" and chk.get("op") == "<=" \
                        and isinstance(chk.get("threshold"), (int, float)):
                    thr = float(chk["threshold"])
            rows.append({
                "suite": d.get("suite"),
                "run_id": d.get("run_id"),
                "case_id": c.get("case_id"),
                "passed": bool(c.get("passed")),
                "status": c.get("status"),
                "rel_error": met.get("rel_error"),
                "threshold": thr,
            })
    return rows


def render(rows: List[Dict[str, Any]], out_png: str | pathlib.Path,
           title: str = "unattended benchmark runner — gate outcome") -> pathlib.Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    p = pathlib.Path(out_png)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13.2, 4.8), gridspec_kw={"width_ratios": [1.7, 1.0]})

    ids = [r["case_id"] for r in rows]
    x = list(range(len(rows)))
    vals, floors = [], []
    pos = [float(r["rel_error"]) for r in rows
           if isinstance(r["rel_error"], (int, float)) and r["rel_error"] > 0]
    floor = (min(pos) / 10.0) if pos else 1e-18
    for r in rows:
        v = r["rel_error"]
        vals.append(float(v) if isinstance(v, (int, float)) and v > 0 else floor)
    colors = ["#2e7d32" if r["passed"] else "#c62828" for r in rows]
    ax.bar(x, vals, color=colors, edgecolor="black", linewidth=0.4, label="measured rel. error")
    for i, r in enumerate(rows):
        if r["threshold"]:
            ax.plot([i - 0.42, i + 0.42], [r["threshold"]] * 2, color="#1565c0",
                    linewidth=2.2, solid_capstyle="butt")
    ax.plot([], [], color="#1565c0", linewidth=2.2, label="predeclared threshold")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(ids, rotation=55, ha="right", fontsize=6.8)
    ax.set_ylabel("relative error (log scale)")
    ax.set_title("(a) per-case error vs predeclared threshold (green=pass, red=fail)", fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(fontsize=7, loc="lower left")

    margins, mcolors, mlabels = [], [], []
    exact = 0
    for r in rows:
        v, t = r["rel_error"], r["threshold"]
        if not isinstance(v, (int, float)) or not t:
            continue
        if v <= 0:
            exact += 1
            continue
        margins.append(t / float(v))
        mcolors.append("#2e7d32" if r["passed"] else "#c62828")
        mlabels.append(r["case_id"])
    if margins:
        ax2.barh(list(range(len(margins))), margins, color=mcolors, edgecolor="black", linewidth=0.4)
        ax2.axvline(1.0, color="#1565c0", linestyle="--", linewidth=1.2, label="gate boundary (margin=1)")
        ax2.set_xscale("log")
        ax2.set_yticks(list(range(len(margins))))
        ax2.set_yticklabels(mlabels, fontsize=6.8)
        ax2.set_xlabel("gate margin = threshold / measured (log, >1 passes)")
        ax2.set_title(f"(b) gate margin per case{(' · %d exact cases omitted' % exact) if exact else ''}",
                      fontsize=9)
        ax2.grid(True, axis="x", alpha=0.3)
        ax2.legend(fontsize=7, loc="lower right")
    else:
        ax2.text(0.5, 0.5, "no paired (threshold, measurement)", ha="center", va="center")
        ax2.set_axis_off()

    n_pass = sum(1 for r in rows if r["passed"])
    fig.suptitle(f"{title} · {n_pass}/{len(rows)} cases pass", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(p, dpi=135)
    plt.close(fig)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="跨 suite 汇总图")
    ap.add_argument("--root", default="artifacts/benchmark_runner")
    ap.add_argument("--out", default="artifacts/benchmark_runner/summary.png")
    ap.add_argument("--suites", default=None, help="逗号分隔的 suite 白名单")
    args = ap.parse_args(argv)
    only = [s.strip() for s in args.suites.split(",") if s.strip()] if args.suites else None
    rows = load_rows(args.root, only_suites=only)
    p = render(rows, args.out)
    print(json.dumps({"rows": len(rows), "passed": sum(1 for r in rows if r["passed"]),
                      "figure": str(p), "sha256": art.sha256_file(p)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())