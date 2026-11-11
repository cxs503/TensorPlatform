#!/usr/bin/env python3
"""把本门的台账增量落成**追加式**平台台账增量文件（不改动基础台账）。

输入（全部由门自己产出，零手抄）：
  artifacts/campaigns/p457w2/ledger_delta.json          战役条目 vs 基础台账
  artifacts/campaigns/p457w2/platform_ledger_delta.json 平台 21 能力：独立观测 vs 基础台账
  artifacts/campaigns/p457w2/ledger.json                战役证据台账（含每条能力的判据与出处）

输出：``artifacts/capability_ledger/capability_ledger_p457w2_delta.json``
  - ``new_capabilities``：本战役新登记的能力条目（含等级/状态/来源工件 sha256）
  - ``platform_review``：需要平台台账负责人复裁的条目（downgrade/upgrade candidate 与
    基础台账内部矛盾），本文件**不替**基础台账改等级
  - ``content_sha256``：内容哈希，可复算

纪律：基础台账 ``capability_ledger.json`` 只读；任何等级变更必须由 owner/复审判定，
本脚本只提供可审计的增量建议。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # data/campaigns/<c>/ -> repo root


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign-dir", default="artifacts/campaigns/p457w2")
    ap.add_argument("--base-ledger", default="artifacts/capability_ledger/capability_ledger.json")
    ap.add_argument("--out", default="artifacts/capability_ledger/capability_ledger_p457w2_delta.json")
    args = ap.parse_args(argv)

    cdir = REPO / args.campaign_dir
    ledger = json.loads((cdir / "ledger.json").read_text(encoding="utf-8"))
    delta = json.loads((cdir / "ledger_delta.json").read_text(encoding="utf-8"))
    pdelta = json.loads((cdir / "platform_ledger_delta.json").read_text(encoding="utf-8"))
    base_path = REPO / args.base_ledger

    entries = {e["capability_id"]: e for e in ledger["entries"]}
    new_caps = []
    for row in sorted(delta.get("new_real") or [], key=lambda r: r["capability_id"]):
        e = entries[row["capability_id"]]
        new_caps.append({
            "capability_id": e["capability_id"],
            "node_ext_id": e["node_ext_id"],
            "repo": e["repo"],
            "mission_id": e["mission_id"],
            "level": e["level"],
            "status": e["status"],
            "merge_state": e["merge_state"],
            "summary": e.get("summary", ""),
            "blocking_criteria": e.get("blocking_criteria"),
            "evidence": e.get("evidence"),
            "notes": e.get("notes", ""),
        })
    withheld = [{"capability_id": e["capability_id"], "node_ext_id": e["node_ext_id"],
                 "level": e["level"], "reason": "no machine-recomputable acceptance evidence"}
                for e in ledger["entries"] if e["level"] == "WITHHELD"]

    doc = {
        "schema": "tp.capability_ledger.delta/v1",
        "campaign": "p457w2",
        "base_ledger": {"path": args.base_ledger, "sha256": sha256(base_path)},
        "sources": [
            {"path": f"{args.campaign_dir}/ledger.json",
             "sha256": sha256(cdir / "ledger.json")},
            {"path": f"{args.campaign_dir}/ledger_delta.json",
             "sha256": sha256(cdir / "ledger_delta.json")},
            {"path": f"{args.campaign_dir}/platform_ledger_delta.json",
             "sha256": sha256(cdir / "platform_ledger_delta.json")},
            # seed_report.json 不在来源里：它内嵌快照哈希，纳入会形成自指环
            {"path": f"{args.campaign_dir}/coupling.json",
             "sha256": sha256(cdir / "coupling.json")},
        ],
        "policy": [
            "追加式：本文件不修改基础台账，只登记本战役新增条目与待复裁建议",
            "R2：基础台账已有抗辩证据（contradicts）的条目不得被静态观测抬高",
            "R3：未合并分支/外部仓证据 → status=PROVISIONAL，等级不虚高",
        ],
        "new_capabilities": new_caps,
        "n_new_capabilities": len(new_caps),
        "new_real_count": sum(1 for c in new_caps if c["level"] == "REAL"),
        "withheld_registered": withheld,
        "platform_review": {
            "tally": pdelta.get("tally"),
            "downgrade_candidates": [r["capability_id"] for r in pdelta["rows"]
                                     if r["verdict"] == "downgrade_candidate"],
            "upgrade_candidates": [r["capability_id"] for r in pdelta["rows"]
                                   if r["verdict"] == "upgrade_candidate"],
            "ledger_internal_conflicts": pdelta.get("flagged") or [],
        },
    }
    payload = {k: v for k, v in doc.items()}
    doc["content_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"written": str(out), "new_capabilities": len(new_caps),
                      "new_real": doc["new_real_count"],
                      "content_sha256": doc["content_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())