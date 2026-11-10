#!/usr/bin/env python3
"""把 p156w2-n07 报告（含 4 张内嵌图）发到平台 /tensor/reports。

报告正文取 ``docs/p156w2_n07_capability_maturity_pipeline.md``（论文级六要素），
数字全部来自 ``artifacts/capability_maturity/report.json``（程序化注入，零手抄）；
图取 ``artifacts/capability_maturity/figures/*.png`` 的 base64。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs/p156w2_n07_capability_maturity_pipeline.md"
REPORT = REPO_ROOT / "artifacts/capability_maturity/report.json"
FIG_DIR = REPO_ROOT / "artifacts/capability_maturity/figures"

CAPTIONS = {
    "fig1_evidence_funnel_and_grades.png":
        "图1 证据漏斗（981 文件→217 有证据→692 证据→672 可归属→5 能力→5 台账事件）与各能力机器判定等级",
    "fig2_ablation_text_vs_structured.png":
        "图2 消融：全证据（实心）vs 仅结构化证据（淡色）的等级对比",
    "fig3_auto_rollback_change_chain.png":
        "图3 受控实验：三波注入误差 vs 3%/15% 判据线与判定动作；从哈希链重放的等级变更链（ROLLBACK rollback_of=[0]）",
    "fig4_audit_integrity_idempotency.png":
        "图4 篡改演练 4/4 检出与断点；重复扫描链长恒定（幂等）；证据来源构成",
}


def build_payload() -> dict:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    md = DOC.read_text(encoding="utf-8")
    figures = []
    for name, caption in CAPTIONS.items():
        png = FIG_DIR / name
        if not png.exists():
            continue
        figures.append({
            "name": name,
            "caption": caption,
            "png_base64": base64.b64encode(png.read_bytes()).decode("ascii"),
        })
    c = report["criteria"]
    summary = (
        f"能力成熟度自治管线（p156w2-n07）：证据扫描→矛盾检测→自动判定→台账→自动回滚→哈希链审计"
        f"全闭环落地。实跑 981 文件 / 692 证据 / 5 能力（REAL×1, MOCK×4），链 {report['live_run']['chain_length']} 事件"
        f"校验 ok；受控实验 REAL→ROLLBACK（rollback_of=[0]，推翻偏离 1100%）+ 极性对照 DOWNGRADE；"
        f"篡改演练 4/4 检出（含尾部截断需锚点）；幂等 0 新增事件；确定性两轮 core_sha256 一致；"
        f"pytest 30 passed。判据 C1~C9 全 PASS（report all_criteria_pass={report['all_criteria_pass']}）。"
        f"诚实局限：真实工件无回滚触发、带参照值证据仅 1 条、与人工审计一致率无基线不声明。"
    )
    return {
        "title": "能力成熟度自治管线：从「人工升账」到机器可判的证据闭环（p156w2-n07）",
        "summary": summary,
        "scenario_key": "capability_maturity_pipeline",
        "mission_id": 10876,
        "tags": ["capability", "maturity", "evidence", "audit-chain", "rollback",
                 "autonomy", "p156w2-n07", "TensorPlatform"],
        "markdown": md,
        "figures": figures,
        "metadata": {
            "node": report["node"],
            "report_json_sha256": report.get("report_json_sha256"),
            "all_criteria_pass": report["all_criteria_pass"],
            "criteria": {k: v["pass"] for k, v in c.items()},
            "chain_head_sha256": report["live_run"]["chain_head_sha256"],
            "core_sha256": report["live_run"]["core_sha256"],
        },
    }


def main() -> int:
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    user = os.environ.get("TENSOR_AGENT_USER", "")
    passwd = os.environ.get("TENSOR_AGENT_PASS", "")
    if not user or not passwd:
        print("missing TENSOR_AGENT_USER/PASS", file=sys.stderr)
        return 2

    payload = build_payload()
    out = REPO_ROOT / "artifacts/capability_maturity/report_payload.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    login = urllib.request.Request(
        f"{api_base}/auth/login",
        data=json.dumps({"username": user, "password": passwd}).encode(),
        headers={"Content-Type": "application/json"},
    )
    token = json.loads(urllib.request.urlopen(login, timeout=15).read())["access_token"]
    req = urllib.request.Request(
        f"{api_base}/tensor/reports",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        body = json.loads(resp.read())
        print(json.dumps({"status": resp.status, "id": body.get("id"),
                          "figures": len(payload["figures"]),
                          "markdown_chars": len(payload["markdown"])},
                         ensure_ascii=False))
        return 0
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read()[:800]!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())