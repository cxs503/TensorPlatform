#!/usr/bin/env python3
"""边界门校准图：把 artifacts 里的判决 JSON 画成两张图（零领域语义：只读工件）。

左图 —— 15 个校准用例的阻断命中数（红=越界/绿=合规，斜线=期望值），
        直观看出"负例红、正例绿"是否成立；
右图 —— 路径分类计数（代码域 / 观察白名单 / 域外 / 自豁免），
        证明白名单放行与"白名单里伪造代码仍判红"这两条同时生效。

用法： python3 tools/boundary/gen_calibration_figure.py \
           --artifact artifacts/boundary/pr_gate_calibration.json \
           --out artifacts/boundary/figures/pr_gate_calibration.png
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]


def build_figure(artifact: Dict[str, Any], out: Path) -> Dict[str, Any]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cases = artifact["cases"]
    labels = [c["id"] for c in cases]
    viol = [c.get("violations", 0) for c in cases]
    news = [c.get("exempted", 0) for c in cases]
    passed = [c.get("got") == "PASS" for c in cases]
    expect_fail = [c["expect"] == "FAIL" for c in cases]

    fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.2), gridspec_kw={"width_ratios": [2.15, 1]})

    ax = axes[0]
    ypos = list(range(len(labels)))[::-1]
    colors = ["#d62728" if not p else "#2ca02c" for p in passed]
    bars = ax.barh(ypos, viol, color=colors, alpha=0.9, edgecolor="black", linewidth=0.6)
    for b, n in zip(bars, news):
        if n:
            b.set_hatch("//")
    for y, v, lab, pf, expf in zip(ypos, viol, labels, passed, expect_fail):
        ax.text(max(v, 0) + 0.8, y, f"{lab}  (expect {'FAIL' if expf else 'PASS'} → got "
                                   f"{'FAIL' if not pf else 'PASS'})",
                va="center", fontsize=7.6)
    ax.set_yticks([])
    ax.set_xlabel("blocking violations in PR added lines")
    ax.set_title("(a) calibration corpus: negative PRs must be red, positive PRs must be green",
                 fontsize=10.5)
    ax.set_xlim(0, max(viol + [1]) * 1.65)
    ax.grid(axis="x", alpha=0.3)

    ax = axes[1]
    counts: Counter = Counter()
    for c in cases:
        if c.get("mode") == "git":
            counts["blocked (code scope)"] += int(c.get("files_touched", 0)) - int(
                c.get("files_whitelisted", 0))
            counts["whitelisted (observation)"] += int(c.get("files_whitelisted", 0))
        else:
            counts["synthetic cases"] += 1
    keys = list(counts)
    ax.bar(keys, [counts[k] for k in keys],
           color=["#1f77b4", "#17becf", "#7f7f7f"][: len(keys)], edgecolor="black", linewidth=0.6)
    for i, k in enumerate(keys):
        ax.text(i, counts[k] + 0.1, str(counts[k]), ha="center", fontsize=9)
    ax.set_ylabel("files")
    ax.set_title("(b) path classification in the PR re-judgement subset", fontsize=10.5)
    ax.grid(axis="y", alpha=0.3)
    plt.setp(ax.get_xticklabels(), rotation=12, fontsize=8.5)

    s = artifact["summary"]
    fig.suptitle(
        f"PR boundary gate calibration — {s['matched']}/{s['scored']} cases matched expectation"
        f"  (lexicon v{artifact['lexicon']['version']}, sha256 {artifact['lexicon']['sha256'][:10]}…)",
        fontsize=11.5,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=96, pil_kwargs={"optimize": True})
    plt.close(fig)
    return {"figure": str(out), "cases": len(cases),
            "negatives": sum(1 for c in cases if c["expect"] == "FAIL"),
            "positives": sum(1 for c in cases if c["expect"] == "PASS")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="边界门校准图生成")
    p.add_argument("--artifact", default=str(REPO_ROOT / "artifacts/boundary/pr_gate_calibration.json"))
    p.add_argument("--out", default=str(REPO_ROOT / "artifacts/boundary/figures/pr_gate_calibration.png"))
    args = p.parse_args(argv)
    artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    info = build_figure(artifact, Path(args.out))
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())