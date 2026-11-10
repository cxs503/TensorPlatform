#!/usr/bin/env python3
"""Post the p305w1-n09 capability reconciliation report to the tensor platform."""
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

md = open(os.path.join(ROOT, "docs/reports/p305w1_n09_capability_reconcile_engine.md"), encoding="utf-8").read()
figs = []
for name, caption in [
    ("fig1_levels", "Declared vs derived evidence levels (21-capability ledger)"),
    ("fig2_confusion", "Derived level vs external adjudication (confusion matrix)"),
    ("fig3_deltas", "Per-capability level delta (negative = overclaim)"),
]:
    with open(os.path.join(ROOT, "artifacts/capability_reconcile", f"{name}.png"), "rb") as fh:
        figs.append({"name": name, "caption": caption, "png_base64": base64.b64encode(fh.read()).decode()})

report = {
    "title": "能力台账三源自动对账引擎（成熟化）：code/test/doc → 等级 · 过度声明 · 调整建议 · sha256 审计锚",
    "summary": (
        "Three-source capability-ledger reconciliation engine (code/test/doc -> grade, overclaim, "
        "suggestions, sha256 anchor + schema + CLI gate). Synthetic gold 12 cases: agreement 1.0, "
        "overclaim accuracy 1.0, 18/18 pytest. Real 21-cap ledger: coverage 1.0, 4 hard overclaims, "
        "anchor reproducible, zero domain hardcoding; honest FAIL on real-ledger agreement 0.33."
    ),
    "scenario_key": "p305w1-n09",
    "mission_id": 9347,
    "tags": [
        "capability-ledger", "three-source-reconciliation", "overclaim-detection",
        "evidence-grade", "sha256-audit-chain", "schema-validation", "zero-domain-semantics",
        "TensorPlatform", "honest-FAIL",
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
    sys.exit(1)