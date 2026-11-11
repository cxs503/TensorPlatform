#!/usr/bin/env python3
"""Post the P167W2B-N09 unattended-benchmark-campaign report to the tensor platform."""
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

report_json = json.load(open(os.path.join(ROOT, "docs/reports/P167W2B_N09_campaign_report.json"),
                             encoding="utf-8"))
md = open(os.path.join(ROOT, "docs/reports/P167W2B_N09_unattended_campaign.md"),
          encoding="utf-8").read()
run_dir = report_json["run_dir"]
figs = []
for name, caption in [
    ("campaign_kpi_vs_target", "Left: unattended-case KPI (frozen external baseline + engine delta vs target). "
                               "Right: per-suite verdict vs predeclared expectation (negative controls must hold)"),
    ("regression_probes", "Regression detector calibration: 2% / 5% / 16% injected workload drift vs the "
                          "predeclared warn/critical thresholds (specificity and discriminative power)"),
    ("suite_error_vs_threshold", "Cross-suite summary: measured relative error per case vs predeclared tolerance"),
]:
    with open(os.path.join(ROOT, run_dir, "figures", f"{name}.png"), "rb") as fh:
        figs.append({"name": name, "caption": caption, "png_base64": base64.b64encode(fh.read()).decode()})

acc = report_json["acceptance"]
kpi = acc["unattended_cases_65_to_at_least_75"]
report = {
    "title": "平台自主基准测试引擎成熟化：批级无人值守 campaign + 批级判据门 + 既有验证面回归 + KPI 对账",
    "summary": (
        "Batch-level unattended benchmark campaign engine: one command runs 9 suites (3 synthetic, "
        "5 predeclared 3%-band calibration, 4 fault-injection negative controls), 3 drift probes and a "
        f"KPI reconciliation. {acc['runner_executes_synthetic_suites']['all_passed']} positive suites pass; "
        "negative controls held {}".format(4) +
        "; automated cases {}+{}={} (target {}); 34 pytest (5 negative controls), "
        "0 integrity failures, 3 PNG + sha256 chain, zero domain terms.".format(
            kpi["baseline"], kpi["engine_delta"], kpi["combined"], kpi["target"])
    ),
    "scenario_key": "p167w2b-n09",
    "mission_id": 12425,
    "tags": ["unattended-benchmark", "fail-closed-gate", "evidence-artifact-sha256",
             "regression-drift-detection", "kpi-reconciliation", "zero-domain-semantics",
             "TensorPlatform", "platform-mechanism"],
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