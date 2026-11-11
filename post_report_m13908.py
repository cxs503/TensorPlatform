#!/usr/bin/env python3
"""Post the P472W2-N01 audit views report to the tensor platform."""
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
with open("/root/tp-agent/work/m13908/docs/P472W2_N01_audit_readonly_views.md") as f:
    md = f.read()

# 3. Post report
report = {
    "title": "P472W2-N01: 审计取证最小权限只读视图层 — RBAC audit_reader + 六表视图 + 权限矩阵门 + sha256 全等对照",
    "summary": "实现审计取证最小权限只读视图层：PostgreSQL audit_reader 角色（仅 SELECT）+ 6 表审计视图（字段白名单化去 PII）+ 16 项权限矩阵门 + 3 条审计查询 sha256 全等对照（drift=0）+ 越权留痕审计事件表 + 零领域语义验证 + 可回滚迁移。40 项测试全部通过。",
    "scenario_key": "p472w2-n01",
    "mission_id": 13908,
    "tags": ["platform", "rbac", "audit", "security", "views"],
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
    print(f"Report posted: {result}")
except Exception as e:
    print(f"Post failed: {e}", file=sys.stderr)
    sys.exit(1)
