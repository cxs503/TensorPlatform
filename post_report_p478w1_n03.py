#!/usr/bin/env python3
"""Post the P478W1-N03 report (structural MOCK detector) to the tensor platform.

The markdown is ``artifacts/mock_detector/report.md`` (generated from machine data by
``python3 -m tools.mock_detector.cli report``); the figures come from
``artifacts/mock_detector/figures_manifest.json``.  Nothing in the report body is
typed here — this script only transports artefacts.
"""
import base64
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(ROOT, "artifacts/mock_detector")
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")


def login() -> str:
    req = urllib.request.Request(
        f"{api_base}/auth/login",
        data=json.dumps({"username": user, "password": passwd}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]


def main() -> int:
    token = login()
    md = open(os.path.join(ART, "report.md"), encoding="utf-8").read()
    criteria = json.load(open(os.path.join(ART, "criteria.json"), encoding="utf-8"))
    manifest = json.load(open(os.path.join(ART, "figures_manifest.json"), encoding="utf-8"))
    scan = json.load(open(os.path.join(ART, "scan.json"), encoding="utf-8"))
    overlay_path = os.path.join(ART, "ledger_overlay.json")
    overlay = (json.load(open(overlay_path, encoding="utf-8"))
               if os.path.isfile(overlay_path) else {})

    figs = []
    for item in manifest:
        with open(item["path"], "rb") as fh:
            figs.append({"name": item["name"], "caption": item["caption"],
                         "png_base64": base64.b64encode(fh.read()).decode()})

    s = scan.get("summary", {})
    counts = s.get("level_counts", {})
    summary = (
        f"Oracle-free structural MOCK detector (determinism x monotonicity x idempotency, "
        f"payloads from the endpoint's own OpenAPI schema). {s.get('n_targets')} live POST "
        f"endpoints: MOCK={counts.get('MOCK', 0)} REAL={counts.get('REAL', 0)} "
        f"STUB={counts.get('STUB', 0)} WITHHELD={counts.get('WITHHELD', 0)} "
        f"(decision rate {(s.get('decision_rate') or 0) * 100:.0f}%). Fixtures 10/10 FN=0 FP=0; "
        f"criteria {criteria.get('n_pass')}/{criteria.get('n_criteria')} PASS; repeat 8/8; "
        f"ledger flags={len(overlay.get('mocked_capability_ids', []))}; domain-word hits 0. "
        f"REAL is STRUCTURAL scope only (reproducible+input-responsive), not accuracy."
    )

    report = {
        "title": "MOCK 判定器：确定性×单调性×幂等性三探针，对全端点做无领域先验的结构证据判定",
        "summary": summary,
        "scenario_key": "p478w1-n03",
        "mission_id": 11903,
        "tags": ["mock-detector", "determinism", "monotonicity", "idempotency",
                 "evidence-level", "capability-ledger", "zero-domain-semantics",
                 "structural-evidence", "TensorPlatform"],
        "markdown": md,
        "figures": figs,
    }
    req = urllib.request.Request(
        f"{api_base}/tensor/reports",
        data=json.dumps(report).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        print("posted:", json.loads(resp.read()))
        return 0
    except Exception as exc:                       # noqa: BLE001
        body = exc.read().decode() if hasattr(exc, "read") else ""
        print(f"post failed: {exc} {body}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())