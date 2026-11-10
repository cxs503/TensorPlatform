#!/usr/bin/env python3
"""发布 p307w1-n04 多物理场耦合守恒契约框架报告 (mission 9997)."""
import base64
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

login = urllib.request.Request(
    f"{api_base}/auth/login",
    data=json.dumps({"username": user, "password": passwd}).encode(),
    headers={"Content-Type": "application/json"},
)
token = json.loads(urllib.request.urlopen(login, timeout=15).read())["access_token"]

md = open(os.path.join(ROOT, "docs/reports/p307w1_n04_coupling_conservation.md"),
          encoding="utf-8").read()
figs = []
with open(os.path.join(ROOT, "artifacts/coupling_conservation/conservation_acceptance.png"), "rb") as fh:
    figs.append({
        "name": "conservation_acceptance",
        "caption": ("Acceptance: (left) injected leak vs reported residual on log-log identity line "
                    "with 1e-10 tolerance; (middle) clock offset vs reported gap; (right) chain-vs-direct "
                    "transitivity mismatch (conservative = exact 0, 1e-6 leaky intermediate)."),
        "png_base64": base64.b64encode(fh.read()).decode(),
    })

acc = json.load(open(os.path.join(ROOT, "artifacts/coupling_conservation/acceptance.json"), encoding="utf-8"))
crit = {c["id"]: c for c in acc["criteria"]}

report = {
    "title": "多物理场耦合守恒契约测试框架（修订）：精确有理数界面记账 · 七类交换违约 · 时钟语义裁决 · 链式传递性",
    "summary": (
        "Zero-domain multi-physics coupling conservation contract framework. 7/7 criteria PASS: "
        f"dim-mismatch detection {crit['a']['numbers']['detection_rate']:.0%} (0 false alarms); "
        "exact-conservative interface residual 0 (<1e-10), 40-step cumulative 0; injected violations "
        "detected 100% above the 1e-10 floor, quantification error 0; clock offsets detected 100% with "
        "exact gaps; chain-vs-direct transitivity mismatch 0 (<1e-12), 1e-6 leaky interface exact; "
        "zero domain-word hits; 36 pytest cases."
    ),
    "scenario_key": "p307w1-n04",
    "mission_id": 9997,
    "tags": [
        "multi-physics-coupling", "conservation-contract", "exact-rational-ledger",
        "dimensional-analysis", "clock-semantics", "transitivity", "zero-domain-semantics",
        "TensorPlatform", "acceptance-criteria", "p307w1-n04",
    ],
    "markdown": md,
    "figures": figs,
}

req = urllib.request.Request(
    f"{api_base}/tensor/reports",
    data=json.dumps(report).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
)
try:
    resp = urllib.request.urlopen(req, timeout=60)
    print("posted:", json.loads(resp.read()))
except Exception as exc:  # noqa: BLE001
    body = exc.read().decode() if hasattr(exc, "read") else ""
    print(f"post failed: {exc} {body}", file=sys.stderr)
    raise SystemExit(1)