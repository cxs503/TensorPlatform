#!/usr/bin/env python3
"""台账 API 数据源的可复现证据：跑两次（确定性）+ 与 JSON 快照口径对账 + 图。

产物：docs/verification_debt/ledger_api_source/{numbers.json, artifacts/*.png}
全部数字由本脚本从台账输出读取写出，报告正文零手抄。
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import validation_debt_ledger as L  # noqa: E402

OUT = "docs/verification_debt/ledger_api_source"
#: 图中用 ASCII 学科名（DejaVu Sans 无 CJK 字形，中文直接画会掉字）
DOMAIN_EN = {"平台治理": "platform-governance", "阻力": "resistance", "推进": "propulsion",
             "耐波": "seakeeping", "操纵": "maneuvering", "稳性": "stability",
             "结构": "structure", "振动噪声": "vibration-noise", "绿色航行": "green-shipping",
             "冰区航行": "ice-navigation", "总布置": "general-arrangement"}
LIMIT = 100
KEYS = ("reports_scanned", "reports_with_debt_section", "items_total",
        "clusters_total", "recurrent_clusters_ge2")


def run_once():
    t0 = time.time()
    reps = L.load_reports_api(LIMIT)
    t_fetch = time.time() - t0
    led = L.build_ledger(reps, repo_root=".", search_roots=())
    json_led = L.build_ledger(json.loads(json.dumps(reps)), repo_root=".", search_roots=())
    return reps, led, json_led, t_fetch


def main() -> int:
    os.makedirs(os.path.join(OUT, "artifacts"), exist_ok=True)
    reps1, led1, jled1, t1 = run_once()
    reps2, led2, _jled2, t2 = run_once()
    stats = led1["stats"]
    parity = {k: [led1["stats"][k], jled1["stats"][k]] for k in KEYS}
    determinism = {k: [led1["stats"][k], led2["stats"][k]] for k in KEYS}
    ids = [r["id"] for r in reps1]
    md_missing = [r["id"] for r in reps1 if not (r.get("markdown") or "").strip()]
    rec = sorted((c for c in led1["clusters"] if c["n_reports"] >= 2),
                 key=lambda c: (-c["n_reports"], c["cluster_id"]))

    numbers = {
        "limit": LIMIT, "reports_returned": len(reps1),
        "id_min": min(ids) if ids else None, "id_max": max(ids) if ids else None,
        "reports_missing_markdown": md_missing,
        "fetch_seconds": [round(t1, 2), round(t2, 2)],
        "stats": {k: stats[k] for k in KEYS},
        "by_severity": stats["by_severity"],
        "by_domain": stats["by_domain"],
        "by_capability_top": dict(list(stats["by_capability"].items())[:8]),
        "items_without_domain": stats["items_without_domain"],
        "artifact_refs_total": stats["artifact_refs_total"],
        "artifact_refs_missing": len(stats["artifact_refs_missing"]),
        "parity_api_vs_json": parity,
        "determinism_run1_vs_run2": determinism,
        "parity_ok": all(v[0] == v[1] for v in parity.values()),
        "determinism_ok": all(v[0] == v[1] for v in determinism.values()),
        "recurrent_clusters": [{"cluster_id": c["cluster_id"], "n_reports": c["n_reports"],
                                "report_ids": c["report_ids"],
                                "representative": c["representative"][:160]}
                               for c in rec],
        "recurrent_count": len(rec),
    }

    # 图：左＝欠账条目按学科分布；右＝复发簇（n_reports≥2）的报告数排行
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6))
    dom = sorted(stats["by_domain"].items(), key=lambda kv: kv[1])
    labels = [DOMAIN_EN.get(k, k) for k, _ in dom] or ["(none)"]
    vals = [v for _, v in dom] or [0]
    ax1.barh(labels, vals, color="#3465a4")
    ax1.set_xlabel("debt items"); ax1.set_title("Extracted debt items by discipline")
    ax1.grid(axis="x", alpha=.3)
    if rec:
        ax2.bar([f"#{c['cluster_id']}" for c in rec], [c["n_reports"] for c in rec], color="#cc0000")
        ax2.set_ylabel("independent reports"); ax2.set_title(f"Recurrent debt clusters (n>=2): {len(rec)}")
        ax2.tick_params(axis="x", labelsize=6, rotation=90)
    ax2.grid(axis="y", alpha=.3)
    fig.tight_layout()
    fig_path = os.path.join(OUT, "artifacts", "fig1_ledger_api_census.png")
    fig.savefig(fig_path, dpi=150)

    numbers["figure"] = fig_path
    with open(os.path.join(OUT, "numbers.json"), "w", encoding="utf-8") as fh:
        json.dump(numbers, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: numbers[k] for k in ("reports_returned", "fetch_seconds", "parity_ok",
                                              "determinism_ok", "recurrent_count")}, ensure_ascii=False))
    print("stats", numbers["stats"])
    print("domains", numbers["by_domain"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
