#!/usr/bin/env python3
"""审计器标定：用人工标注样本量化 ④图件 / ⑥误差判定 两个判定的误判率。

判据（先于实现）：
- 对每个判定给出 TP/FP/FN/TN、precision、recall、accuracy；
- 误差（1-accuracy）若 >15% 则该判定不可用于返工派单，只能作提示（本仓精度标准：>15% 不可接受）；
- 标定结果写 JSON，报告里的误差数字从该 JSON 程序化注入（零手抄）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any, List


def score(pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    tp = sum(1 for p in pairs if p["label"] and p["pred"])
    fp = sum(1 for p in pairs if not p["label"] and p["pred"])
    fn = sum(1 for p in pairs if p["label"] and not p["pred"])
    tn = sum(1 for p in pairs if not p["label"] and not p["pred"])
    n = len(pairs)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    acc = (tp + tn) / n if n else None
    return {
        "n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": prec, "recall": rec, "accuracy": acc,
        "error_rate": None if acc is None else round(1 - acc, 4),
        "false_positive_ids": [p["id"] for p in pairs if not p["label"] and p["pred"]],
        "false_negative_ids": [p["id"] for p in pairs if p["label"] and not p["pred"]],
    }


def calibrate(audit: Dict[str, Any], labels: Dict[str, Any]) -> Dict[str, Any]:
    by_id = {r["report_id"]: r for r in audit["reports"]}
    lab = labels["labels"]
    fig_pairs, err_pairs = [], []
    for rid, lb in lab.items():
        r = by_id.get(int(rid))
        if not r:
            continue
        fig_pairs.append({"id": int(rid), "label": bool(lb["figure_ok"]),
                          "pred": r["elements"]["figures"] == "ok"})
        err_pairs.append({"id": int(rid), "label": bool(lb["error_ok"]),
                          "pred": r["elements"]["error"] == "ok"})
    return {
        "labels_source": labels.get("protocol", ""),
        "sample_size": len(lab),
        "figure_check": score(fig_pairs),
        "error_check": score(err_pairs),
        "usable_for_gating": {
            "figure_check": (score(fig_pairs)["accuracy"] or 0) >= 0.85,
            "error_check": (score(err_pairs)["accuracy"] or 0) >= 0.85,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True, help="audit_*.json")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    audit = json.loads(Path(a.audit).read_text(encoding="utf-8"))
    labels = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    res = calibrate(audit, labels)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())