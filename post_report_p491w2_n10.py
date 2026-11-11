#!/usr/bin/env python3
"""发布 p491w2-N10（战役集成与验收门）进展报告到 tensor 平台。

数字全部取自 ``artifacts/p491w2_maturity/gate.json``（程序化注入，零手抄）；
markdown 来自 ``docs/P491W2-N10_INTEGRATION_GATE.md``（同一份 CLI 产物）。
"""

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")
OUT = ROOT / "artifacts" / "p491w2_maturity"
MISSION_ID = 13929
REVISION_NOTE = ("本报告为 id 13402 的修订版：新增会话级 TEMP 表实跑 SQL 冒烟"
                 "（幂等 + 回滚干净）与集成节点自身机制正控条目；两层裁定结论不变。")


def main() -> int:
    gate = json.loads((OUT / "gate.json").read_text(encoding="utf-8"))
    maturity = json.loads((OUT / "maturity_plan.json").read_text(encoding="utf-8"))
    contracts = gate["contracts_summary"]
    presence = gate["presence_summary"]
    delivery = gate["delivery_criteria"]

    login = json.dumps({"username": USER, "password": PASS}).encode()
    req = urllib.request.Request(f"{API}/auth/login", data=login,
                                 headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]

    figures = []
    for item in gate["figures"]:
        figures.append({
            "name": Path(item["name"]).stem,
            "caption": item.get("caption") or item["name"],
            "png_base64": base64.b64encode(Path(item["path"]).read_bytes()).decode(),
        })

    smoke = gate.get("sql_smoke") or {}
    summary = (
        REVISION_NOTE + " " +
        f"战役 p491w2 集成节点：证据层 {gate['campaign']['verdict']}"
        f"（{gate['campaign']['nodes_absent'].__len__()}/{gate['campaign']['nodes_total']} 节点"
        f"无可复算证据），交付层 {gate['delivery_verdict']}"
        f"（a={delivery['a']['status']} b={delivery['b']['status']} c={delivery['c']['status']} "
        f"d={delivery['d']['status']} e={delivery['e']['status']} f={delivery['f']['status']}）；"
        f"实测交付 {presence['nodes_delivered']}/{presence['nodes_total']} 节点"
        f"（artifact {presence['artifact_total']} 份，协议符合 "
        f"{len(presence['protocol_conformant_keys'])} 份），infra 阻塞 "
        f"{len(presence['infra_blocked_keys'])} 个；契约 {contracts['contracts_total']} 条、"
        f"违反 {contracts['violations_total']} 条（含 1 条报告-任务链错配）、接口契约真被检查 "
        f"{contracts['interface_contracts_checked']}/{contracts['interface_contracts_total']} 条"
        f"（缺证据即 WITHHELD，反空真）；台账 REAL {maturity['real_total']}"
        f"（上游 {maturity.get('real_upstream_total', 0)} + 自身机制正控 "
        f"{len(maturity.get('self_registered') or [])}），附 INSERT + rollback。"
        "SQL 冒烟（会话级 TEMP 表）：first/second/after_rollback = "
        f"{smoke.get('after_first')}/{smoke.get('after_second')}/{smoke.get('after_rollback')}；"
        "诚实声明：上游本次无一交付协议证据，故 a/e 硬失败、b 因报告链断裂失败，该 FAIL 不被覆盖。"
    )

    payload = {
        "title": "战役集成与验收门（p491w2-N10）：异构证据收集 + 跨节点契约 + 台账成熟化单"
                 "（修订：SQL 冒烟 + 自身正控）",
        "summary": summary,
        "scenario_key": "p491w2-n10",
        "mission_id": MISSION_ID,
        "tags": ["campaign-integration", "evidence-gate", "withheld", "anti-vacuity",
                 "feature-ledger", "honest-fail", "TensorPlatform", "audit"],
        "markdown": (ROOT / "docs" / "P491W2-N10_INTEGRATION_GATE.md").read_text(encoding="utf-8"),
        "figures": figures,
    }
    body = json.dumps(payload).encode()
    req2 = urllib.request.Request(f"{API}/tensor/reports", data=body,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}"})
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