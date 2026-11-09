#!/usr/bin/env python3
"""Post the OSS-SPH inventory report for TiSPH_multiphase to the tensor platform."""
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
    print(f"Login OK, token length: {len(token)}")
except Exception as e:
    print(f"Login failed: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Read report
report_path = "/root/tp-agent/work/m8686/docs/oss_inventory_sph/OSS-INV-TiSPH_multiphase.md"
with open(report_path) as f:
    md = f.read()

# 3. Post report
report = {
    "title": "OSS-SPH 深盘·sakamotoyan/TiSPH_multiphase: 隐式多相 SPH 研究代码源码级盘点",
    "summary": "对 tier3 仓库 sakamotoyan/TiSPH_multiphase 进行源码级方法清单盘点。该仓为 Taichi 框架实现的隐式多相 SPH 求解器研究原型(5 stars, 无 license)，含 ISM(提出方法)、JL21(基线)、DFSPH 三套活跃求解器。无测试、无量化验证、无边界处理。提供 3 条 reference 级可迁移方案挂接 GAP-SPH-MULTIPHASE 和 GAP-SPH-GPU-SCALING。",
    "scenario_key": "oss-sph-disc",
    "mission_id": 8686,
    "tags": ["oss-sph", "tier3", "multiphase", "Taichi", "ISM", "DFSPH", "deep-dive", "osssph-w2"],
    "markdown": md
}

body = json.dumps(report).encode()
req2 = urllib.request.Request(f"{api_base}/tensor/reports", data=body,
                              headers={"Content-Type": "application/json",
                                       "Authorization": f"Bearer {token}"})
try:
    resp2 = urllib.request.urlopen(req2, timeout=15)
    result = json.loads(resp2.read())
    print(f"Report posted successfully: {result}")
except Exception as e:
    print(f"Post failed: {e}", file=sys.stderr)
    if hasattr(e, 'read'):
        print(e.read().decode(), file=sys.stderr)
    sys.exit(1)
