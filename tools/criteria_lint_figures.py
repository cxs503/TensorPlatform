#!/usr/bin/env python3
"""criteria_lint 统计图（p372w2-n06 报告用）。

读 ``quality/criteria_lint/evidence/lint_*.json``（由
``tools/criteria_lint.py --from-db --json-out`` 产出，零手抄），画：

* 图 A：三组口径（plan 372 / p372-wave2=plan 9181 / 人群 300 节点）的
  条目拦截率 + 原因码分布（堆叠条）；
* 图 B：原因码可达性（登记表 9 码中几个可被触发）。

输出：``quality/criteria_lint/evidence/fig_lint_stats.png``、
``.../fig_reason_codes.png``。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "quality" / "criteria_lint" / "evidence"
GROUPS = (("lint_plan372.json", "plan 372 nodes (n=8)"),
          ("lint_plan9181.json", "p372-wave2 nodes (n=8)"),
          ("lint_population300.json", "plans > 372 sample (n=300)"))
CODE_ORDER = ("CRIT_NO_NUMERIC_THRESHOLD", "CRIT_NO_REFERENCE_ANCHOR",
              "CRIT_NO_VERDICT_MARK", "CRIT_VAGUE_QUANTIFIER")


def build(evid: Path = EVID) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows: List[Dict] = []
    for fname, label in GROUPS:
        p = evid / fname
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        agg = d["aggregate"]
        rows.append({"label": label, "agg": agg})
    out: List[str] = []
    if rows:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 3.8), dpi=150)
        labels = [r["label"] for r in rows]
        rates = [r["agg"]["item_block_rate"] for r in rows]
        bars = ax1.bar(range(len(rows)), rates, color="tab:red", alpha=0.75, width=0.55)
        ax1.set_xticks(range(len(rows)))
        ax1.set_xticklabels(labels, fontsize=7, rotation=12)
        ax1.set_ylabel("item block rate")
        ax1.set_ylim(0, 1.05)
        ax1.grid(alpha=0.3, axis="y")
        ax1.set_title("Criteria-lint interception rate")
        for b, r in zip(bars, rows):
            ax1.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.02,
                     f"{b.get_height():.3f}\n({r['agg']['blocked_items']}/"
                     f"{r['agg']['items']})", ha="center", fontsize=6.5)
        bottom = [0.0] * len(rows)
        for code in CODE_ORDER:
            vals = [r["agg"]["reason_code_counts"].get(code, 0) for r in rows]
            if not any(vals):
                continue
            ax2.bar(range(len(rows)), vals, bottom=bottom, width=0.55,
                    label=code.replace("CRIT_", ""))
            bottom = [b + v for b, v in zip(bottom, vals)]
        ax2.set_xticks(range(len(rows)))
        ax2.set_xticklabels(labels, fontsize=7, rotation=12)
        ax2.set_ylabel("emitted reason codes (count)")
        ax2.set_title("Reason-code distribution")
        ax2.grid(alpha=0.3, axis="y")
        ax2.legend(fontsize=6.5)
        p1 = evid / "fig_lint_stats.png"
        fig.tight_layout(); fig.savefig(p1); plt.close(fig); out.append(str(p1))

    cov_p = evid / "reason_code_coverage.json"
    if cov_p.exists():
        cov = json.loads(cov_p.read_text(encoding="utf-8"))
        codes = cov["registry"]
        reach = [1 if cov["reachable"][c] else 0 for c in codes]
        fig, ax = plt.subplots(figsize=(7.0, 3.4), dpi=150)
        ax.bar(range(len(codes)), reach, color=["tab:green" if v else "tab:red" for v in reach],
               width=0.6)
        ax.set_xticks(range(len(codes)))
        ax.set_xticklabels([c.replace("CRIT_", "") for c in codes], fontsize=6.5, rotation=30)
        ax.set_yticks([0, 1]); ax.set_yticklabels(["unreachable", "reachable"])
        ax.set_title(f"Reason-code reachability: {cov['n_reachable']}/{cov['n_registered']} "
                     "triggered by synthetic battery")
        ax.grid(alpha=0.3, axis="y")
        p2 = evid / "fig_reason_codes.png"
        fig.tight_layout(); fig.savefig(p2); plt.close(fig); out.append(str(p2))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="criteria_lint 统计图")
    ap.add_argument("--evid-dir", default=str(EVID))
    args = ap.parse_args(argv)
    print(json.dumps({"figures": [str(Path(p).relative_to(ROOT))
                                  for p in build(Path(args.evid_dir))]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())