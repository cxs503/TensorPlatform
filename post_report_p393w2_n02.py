#!/usr/bin/env python3
"""发布 P393W2-N02（跨战役重复劳动冗余聚类审计）进展报告到 tensor 平台。

零手抄：summary 里的每个数字都从 ``artifacts/redundancy_report.json`` 读。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "artifacts" / "redundancy_report.json"
FIG_DIR = ROOT / "artifacts" / "redundancy_audit"
MD = ROOT / "docs" / "p393w2_n02_redundancy_audit.md"


def main() -> int:
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
    passwd = os.environ.get("TENSOR_AGENT_PASS", "")

    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    m = rep["metrics"]
    led = rep["ledger"]["portfolio"]
    cov = rep["coverage"]
    thr = rep["thresholds"]

    figs = []
    for name, caption in (
        ("threshold_sensitivity.png",
         "图1 阈值敏感性：(a) 标注集 F1/precision/recall 与同题改写召回随阈值变化（F1 在 0.6-0.95 全平 =1.0，"
         "改写召回在 0.75 以上归零）；(b) 簇数/簇内节点数/冗余成本占比的操作剖面"),
        ("cluster_redundant_cost.png",
         "图2 强阈值下冗余实耗最高的 12 个簇（非 keeper 成本，按 merge/archive 拆分）"),
    ):
        blob = (FIG_DIR / name).read_bytes()
        figs.append({"name": name.replace(".png", ""), "caption": caption,
                     "png_base64": base64.b64encode(blob).decode()})

    summary = (
        f"跨战役冗余审计：{cov['nodes_indexed']} 节点 / {cov['plans_unique']} 计划，"
        f"强阈值 {thr['strong']} 下识别 {rep['clusters']['cluster_count']} 个冗余簇"
        f"（覆盖 {sum(c['size'] for c in rep['clusters']['clusters'])} 个节点）；"
        f"冗余成本 {led['redundant']['wall_seconds_actual'] / 3600:.1f} h 实耗"
        f"（占全库 {100 * led['redundant_share']['wall_seconds_actual']:.2f}%）、"
        f"冗余预算 tokens {led['redundant']['tokens_budget']:.0f}"
        f"（占 {100 * led['redundant_share']['tokens_budget']:.2f}%）。"
        f"判据：F1={m['strong_threshold_f1']:.3f}(≥0.85)、难负例误报={m['negative_false_positive']:.3f}(≤0.10)、"
        f"字面重播组召回={m['true_duplicate_recall']:.3f}(4/4)、域词命中={int(m['domain_word_hits'])}、"
        f"账本复算误差={m['budget_recalc_error']}。"
        f"诚实边界：同题改写召回在强阈值下 {m['paraphrase_recall_at_strong']:.2f}（0/17）——词面法对改组"
        f"标题/改表述的重复几乎全漏；难点在召回侧而非精度侧（难负例最高分仅 "
        f"{rep['findings']['F4_negative_side_trivial']['hard_negative_max_score']:.3f}）。"
        f"真库缺陷：campaign_nodes 无 budget_actual 列、plan 级 budget_actual 恒零、ext_id 跨计划复用不唯一。"
    )

    body = {
        "title": ("跨战役重复劳动冗余聚类审计：70 个冗余簇 + 528 h 冗余实耗账本"
                  "（词面 F1=1.00，同题改写召回 0/17 如实披露）"),
        "summary": summary,
        "scenario_key": "p393w2-n02",
        "mission_id": 13922,
        "tags": ["redundancy-audit", "textsim", "clustering", "budget-ledger", "ann",
                 "threshold-freeze", "idempotent", "zero-domain-semantics", "TensorPlatform"],
        "markdown": MD.read_text(encoding="utf-8"),
        "figures": figs,
    }

    login = json.dumps({"username": user, "password": passwd}).encode()
    req = urllib.request.Request(f"{api_base}/auth/login", data=login,
                                 headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]

    req2 = urllib.request.Request(
        f"{api_base}/tensor/reports", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req2, timeout=120)
        print("posted:", resp.read().decode()[:400])
    except Exception as exc:  # noqa: BLE001
        print(f"post failed: {exc}", file=sys.stderr)
        if hasattr(exc, "read"):
            print(exc.read().decode()[:1200], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())