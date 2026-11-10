#!/usr/bin/env python3
"""Post the criterion auditor report to the tensor platform."""
import json, os, sys, urllib.request

api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

# 1. Login
login_data = json.dumps({"username": user, "password": passwd}).encode()
req = urllib.request.Request(f"{api_base}/auth/login", data=login_data,
                             headers={"Content-Type": "application/json"})
try:
    resp = urllib.request.urlopen(req, timeout=10)
    token = json.loads(resp.read())["access_token"]
except Exception as e:
    print(f"Login failed: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Read report
with open("docs/N09_criterion_auditor_maturity.md") as f:
    md = f.read()

# 3. Post report
report = {
    "title": "判据强度审计器+提案语义裁决机制成熟化验收（p247w1-N09）",
    "summary": "实现物理三元组抽取+槽位对齐+判据强度审计+语义裁决全流程，27/27测试通过，零领域硬编码验证通过，性能0.8s/50提案，AUC=0.77（合成数据集），假PASS检出率100%，历史回放一致率100%",
    "scenario_key": "p247w1-N09",
    "mission_id": 9137,
    "tags": ["criterion_auditor", "proposal_gate", "semantic_adjudication", "platform_maturity"],
    "markdown": md,
    "figures": []
}

report_data = json.dumps(report).encode()
req = urllib.request.Request(f"{api_base}/tensor/reports", data=report_data,
                             headers={"Content-Type": "application/json",
                                      "Authorization": f"Bearer {token}"})
try:
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read())
    print(f"Report posted: {result}")
except Exception as e:
    print(f"Post failed: {e}", file=sys.stderr)
    sys.exit(1)
