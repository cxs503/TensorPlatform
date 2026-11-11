#!/usr/bin/env python3
"""Post the mission 1688 backfill report to the tensor platform."""
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
    print(f"Login successful, token length: {len(token)}")
except Exception as e:
    print(f"Login failed: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Read report
with open("/root/tp-agent/work/m14407/docs/report_m1688.md") as f:
    md = f.read()

# 3. Post report
report = {
    "title": "Mission 1688 补报：tp-ver-2 验证欠账扫描（Meta-Report）",
    "summary": "补报 mission 1688（tp-ver-2 verifier 席自找活）：原单已完成但缺报告且未留档工件目录。本补报单基于原单声明与既有报告（m1370/m1603/验证欠账台账）推断原单意图，严格遵循零手抄纪律，所有缺失数字标注'未留档'。推断原单发现了湿表面积公式适用范围问题并推动了 m1603 修复。",
    "scenario_key": "report-backfill",
    "mission_id": 1688,
    "tags": ["补报", "verifier", "verification-debt", "meta-report", "honest-fail"],
    "markdown": md,
    "figures": []
}

body = json.dumps(report).encode()
req2 = urllib.request.Request(f"{api_base}/tensor/reports", data=body,
                              headers={"Content-Type": "application/json",
                                       "Authorization": f"Bearer {token}"})
try:
    resp2 = urllib.request.urlopen(req2, timeout=15)
    result = json.loads(resp2.read())
    print(f"Report posted successfully: {result}")
    print(f"Report ID: {result.get('id', 'unknown')}")
except Exception as e:
    print(f"Post failed: {e}", file=sys.stderr)
    if hasattr(e, 'read'):
        error_body = e.read().decode()
        print(f"Error body: {error_body}", file=sys.stderr)
    sys.exit(1)
