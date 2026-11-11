#!/usr/bin/env python3
"""Post the mission 1715 backfill report to the tensor platform."""
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
    print(f"Login successful", file=sys.stderr)
except Exception as e:
    print(f"Login failed: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Read report
with open("/root/tp-agent/work/m14440/docs/report_m1715.md") as f:
    md = f.read()

# 3. Post report
report = {
    "title": "【补报】Mission 1715: tp-gen-3 自主工时扫描（honest-FAIL 元报告）",
    "summary": "Mission 1715 补报：原单成功交付但缺报告。honest-FAIL（预算耗尽，验证链未闭合）。基于嵌入的原单声明撰写元报告，缺失数据标注'未留档'，零编造。",
    "scenario_key": "report-backfill",
    "mission_id": 1715,
    "tags": ["补报", "honest-FAIL", "meta-report", "tp-gen-3", "自主工时"],
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
    print(f"Report ID: {result.get('id', 'N/A')}")
except Exception as e:
    print(f"Post failed: {e}", file=sys.stderr)
    if hasattr(e, 'read'):
        print(e.read().decode(), file=sys.stderr)
    sys.exit(1)
