#!/usr/bin/env python3
"""Post the P171-W1-N09 threshold registry report to the tensor platform."""
import base64
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

report = json.load(open(os.path.join(ROOT, "artifacts/criteria/threshold_registry_report.json"), encoding="utf-8"))
md = open(os.path.join(ROOT, "docs/criteria/threshold_registry.md"), encoding="utf-8").read()

figs = []
for name, caption in [
    ("c2_derivation_consistency.png",
     "C2 派生一致性：log-log 误差衰减 vs 声明指数。点线=按声明指数外推的参考线。"),
    ("c2b_axis_coverage.png",
     "C2 轴覆盖：h/N/eps 三轴的判据分布与实测覆盖。"),
    ("c5_rederivation_closure.png",
     "C5 重推导闭环：预测阈值/实测误差比值在更细一档的分布。虚线=[0.5, 2.0] 允许带。"),
    ("controls_and_coverage.png",
     "C3/C4 控制实验 + C6 覆盖率账本。左=20 次篡改注入全部检出；右=来源类型分布。"),
]:
    path = os.path.join(ROOT, "artifacts/criteria/figures", name)
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            figs.append({
                "name": name,
                "caption": caption,
                "png_base64": base64.b64encode(fh.read()).decode("ascii"),
            })

payload = {
    "title": "判据阈值口径注册表与派生一致性门（P171W1-N09）：C1-C7 全绿",
    "summary": (
        "59 条判据入册，七条机器判据全绿：完备性/派生一致性/漂移检出/假阳性/重推导闭环/覆盖率/负控。"
        "零领域语义，全合成夹具。130 项测试通过。"
    ),
    "scenario_key": "p171w1-n09-threshold-registry",
    "mission_id": 11645,
    "tags": ["threshold-registry", "criteria-governance", "drift-gate",
             "derivation-consistency", "zero-domain-semantics"],
    "markdown": md,
    "figures": figs,
}

# Login
login_data = json.dumps({"username": user, "password": passwd}).encode()
req = urllib.request.Request(f"{api_base}/auth/login", data=login_data,
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        token = json.loads(resp.read())["access_token"]
except Exception as exc:
    print(f"Login failed: {exc}")
    raise SystemExit(1)

# Post report
body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
req2 = urllib.request.Request(
    f"{api_base}/tensor/reports", data=body,
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {token}"},
)
try:
    with urllib.request.urlopen(req2, timeout=30) as resp:
        result = json.loads(resp.read())
        print(f"Report posted: {result.get('id', result)}")
except Exception as exc:
    print(f"Post failed: {exc}")
    raise SystemExit(1)
