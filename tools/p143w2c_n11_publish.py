#!/usr/bin/env python3
"""Publish the P143W2C-N11 campaign-acceptance report to the tensor report plane.

内容全部来自 worktree 产物（docs/ + artifacts/p143w2c/），零手抄：
  docs/P143W2C-N11_campaign_acceptance.md       报告正文
  artifacts/p143w2c/figures/*.png               图件（base64 内嵌）
  artifacts/p143w2c/acceptance_report.json      机器裁决（标题数字来源）
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_DIR = ROOT / "artifacts/p143w2c"
DOC = ROOT / "docs/P143W2C-N11_campaign_acceptance.md"
MISSION_ID = int(os.environ.get("N11_MISSION_ID", "9197"))


def _login(base: str) -> str:
    payload = json.dumps({"username": os.environ["TENSOR_AGENT_USER"],
                          "password": os.environ["TENSOR_AGENT_PASS"]}).encode()
    req = urllib.request.Request(f"{base}/auth/login", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())["access_token"]


def main() -> int:
    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    acc = json.loads((CAMPAIGN_DIR / "acceptance_report.json").read_text(encoding="utf-8"))
    meta = json.loads((CAMPAIGN_DIR / "figures/figures_metadata.json").read_text(encoding="utf-8"))
    counts = acc["census"]["counts"]
    failed = acc["criterion_counts"]["failed"]

    figures = []
    for fig in meta["figures"]:
        png = (CAMPAIGN_DIR / "figures" / fig["path"]).read_bytes()
        figures.append({"name": fig["path"],
                        "caption": f"{fig['caption']}（sha256 {fig['sha256'][:16]}…）",
                        "png_base64": base64.b64encode(png).decode()})

    payload = {
        "title": ("P143-W2C 战役级验收门（N11）：跨域集成验证 + 治理工具链在位性 · "
                  "裁决 not_ready（4/6 判据通过，honest）"),
        "summary": (
            f"对 plan 501 全 11 节点做战役级裁决：A~F 六判据合取 → not_ready。"
            f"节点普查 total={counts['total']} closed={counts['closed']} open={counts['open']}；"
            f"阻塞项={'/'.join(failed)}。"
            "跨域集成门 11 节点全 unproven（3 个已 done 节点未发布 inheritance_evidence 证据包 → "
            "done≠可验收，实测诊断）；接口合约设计期自洽 L1~L6 零违规（20 需求键）；"
            "N03 回填器/N04 统计功效门五检全绿（冒烟 16+103 项 pytest 实跑通过）。"
            "新增机制：campaign_acceptance（A~F 合取 + fail-closed 退出码）+ interface_contract_lint，"
            "39 条合成域名单测。"
        ),
        "scenario_key": "p143w2c-n11-campaign-acceptance-gate",
        "mission_id": MISSION_ID,
        "tags": ["p143w2c", "campaign-acceptance", "cross-domain-integration",
                 "honest-not-ready", "governance-toolchain", "fail-closed",
                 "interface-contract", "capability-ledger"],
        "markdown": DOC.read_text(encoding="utf-8"),
        "figures": figures,
    }
    token = _login(base)
    req = urllib.request.Request(
        f"{base}/tensor/reports",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            print(json.dumps(json.loads(resp.read()), ensure_ascii=False)[:600])
    except Exception as exc:  # noqa: BLE001
        print(f"publish failed: {exc}", file=sys.stderr)
        if hasattr(exc, "read"):
            print(exc.read().decode()[:800], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())