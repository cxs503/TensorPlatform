#!/usr/bin/env python3
"""Post the OSS-SPH-W2 sarracen inventory report to the tensor platform."""
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
report_path = os.path.join(os.path.dirname(__file__), "docs", "oss-sph-w2", "sarracen_inventory.md")
with open(report_path) as f:
    md = f.read()

# 3. Post report
report = {
    "title": "OSS-SPH深盘波2: ttricco/sarracen 普查报告 — SPH后处理/可视化库源码级审计",
    "summary": "完成ttricco/sarracen v1.4.1源码级普查：GPL-3.0 Python SPH分析可视化库，466提交/14201行代码/4823行测试。核心能力=3种SPH核+精确积分(Petkova 2018)+CPU多线程/GPU CUDA双后端+7格式reader。定位为后处理库非求解器。提出3条可迁移方案挂接GAP-SPH-WCSPH-VALIDATION和GAP-SPH-GPU-SCALING。",
    "scenario_key": "oss-sph-w2-sarracen",
    "mission_id": 8681,
    "tags": ["oss-sph", "sarracen", "tier3", "inventory", "sph-kernels", "gpu-cuda", "postprocessing"],
    "markdown": md
}

report_data = json.dumps(report, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(
    f"{api_base}/tensor/reports",
    data=report_data,
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    },
    method="POST"
)
try:
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    print(f"Report submitted: {json.dumps(result, indent=2, ensure_ascii=False)[:500]}")
except Exception as e:
    print(f"Report submission failed: {e}", file=sys.stderr)
    if hasattr(e, 'read'):
        print(f"Response: {e.read().decode()[:500]}", file=sys.stderr)
    sys.exit(1)
