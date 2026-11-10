#!/usr/bin/env python3
"""Post the m9082 paper-grade report (six elements + embedded figures)."""
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path("/root/tp-agent/work/m9082")
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

login = json.dumps({"username": user, "password": passwd}).encode()
req = urllib.request.Request(f"{api_base}/auth/login", data=login,
                             headers={"Content-Type": "application/json"})
token = json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]

md = (ROOT / "docs/M9082_LONGRUN_SURVIVAL.md").read_text(encoding="utf-8")
ev = json.loads((ROOT / "runs/m9082/evidence.json").read_text(encoding="utf-8"))
figures = []
for fig in ev["figures"]:
    path = ROOT / fig["path"]
    figures.append({
        "name": fig["name"],
        "caption": fig["caption"],
        "png_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    })

report = {
    "title": "【长跑生存层】核心转储取证 + 位级幂等断点续跑 + 资源降级阶梯（m9082 / p148w2-n03）",
    "summary": (
        "新增 TensorPlatform 共性模块 tools/longrun（零领域语义）：ptrace 监护 + gdb 真核取证"
        "（5 种故意崩溃取证完整率 100%）、位级幂等续跑（10/20/35 步续跑与不间断运行 sha256 完全一致，"
        "重复 2 次同键）、资源降级阶梯（占用 0.125x、优雅退出码 75 且末态可续）；"
        "负控实测取证开销中位 −0.65%（仪器自计 0.089%）< 2% 预算。32 个 pytest 用例全绿。"
    ),
    "scenario_key": "crash-forensics-resume",
    "mission_id": 9082,
    "tags": ["long-run", "crash-forensics", "core-dump", "ptrace", "gdb", "checkpoint",
             "idempotent-resume", "graceful-degradation", "platform-infra", "p148w2-n03"],
    "markdown": md,
    "figures": figures,
}

body = json.dumps(report).encode()
req2 = urllib.request.Request(f"{api_base}/tensor/reports", data=body,
                              headers={"Content-Type": "application/json",
                                       "Authorization": f"Bearer {token}"})
try:
    resp = urllib.request.urlopen(req2, timeout=60)
    print("posted:", json.loads(resp.read()))
except Exception as exc:  # noqa: BLE001
    print("post failed:", exc, file=sys.stderr)
    if hasattr(exc, "read"):
        print(exc.read().decode()[:2000], file=sys.stderr)
    sys.exit(1)