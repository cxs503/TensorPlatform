#!/usr/bin/env python3
"""发布 p337w1-n03 进展报告到平台（markdown + 5 张图内嵌 base64）。"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")


def login() -> str:
    payload = json.dumps(
        {"username": os.environ["TENSOR_AGENT_USER"], "password": os.environ["TENSOR_AGENT_PASS"]}
    ).encode()
    req = urllib.request.Request(f"{API}/auth/login", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())["access_token"]


def main() -> int:
    md_path = ROOT / "docs/reports/m9953_dedup_evidence_gate.md"
    acc = json.loads((ROOT / "artifacts/m9953/dedup_evidence_gate_acceptance.json").read_text(encoding="utf-8"))
    figures = []
    for fig in acc["figures"]:
        png = (ROOT / fig["path"]).read_bytes()
        figures.append(
            {
                "name": Path(fig["path"]).name,
                "caption": fig["caption"],
                "png_base64": base64.b64encode(png).decode("ascii"),
            }
        )
    cm = acc["C4"]["confusion"]
    summary = (
        f"提案去重证据可复算门成熟化：快照指纹+结构化查询规格+逐规格命中计数/top-k 复算；"
        f"C1 复算一致率 {acc['C1']['consistency_rate']*100:.0f}%（5 条真提案）、"
        f"C2 篡改检出 {acc['C2']['n_caught']}/{acc['C2']['n_cases']} 且原因分类正确、C3 域词命中 {acc['C3']['n_hits']}、"
        f"C4 回放 n={acc['C4']['n_records']} recall {cm['recall']:.3f} precision {cm['precision']:.3f}、"
        f"C5 单次复算最大 {acc['C5']['max_ms']:.0f} ms（预算 2000 ms）且源不可达 fail-closed、"
        f"C6 只读无副作用；all_pass={acc['all_pass']}"
    )
    report = {
        "title": "【p337w1-n03】提案去重证据可复算门：fail-closed 机器门 + 回放判别力",
        "summary": summary,
        "scenario_key": "platform-governance",
        "mission_id": 9953,
        "tags": [
            "proposal-dedup",
            "replay-gate",
            "snapshot-fingerprint",
            "fail-closed",
            "evidence-chain",
            "platform",
        ],
        "markdown": md_path.read_text(encoding="utf-8"),
        "figures": figures,
    }
    token = login()
    body = json.dumps(report, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"{API}/tensor/reports",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())
    print(json.dumps({"posted": result.get("id") or result, "figures": len(figures), "md_bytes": len(report["markdown"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())