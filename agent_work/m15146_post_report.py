#!/usr/bin/env python3
"""m15146：发布交差审计报告（tensor_reports）。"""
import base64
import json
import os
import urllib.request

base = os.environ["TENSOR_API_BASE"]
tok = open("/tmp/tok.txt").read().strip()
ROOT = "/root/tp-agent/work/m15146"

md = open(f"{ROOT}/docs/reports/m15146_audit_m15116.md", encoding="utf-8").read()
png = open(f"{ROOT}/runs/m15146_audit/fig1_triad_check.png", "rb").read()

payload = {
    "title": "【交差审计】m15116 KPI-QC-RECHECK 独立复核通过率计量：三件套齐、数字可复算，判级 L2（verdict id=242）",
    "summary": "board-audit m15146 核验 m15116：分子 274 逐位一致、分母 5315→5334 为预声明 live drift、repro_cmd 实测跑通、报告六段齐；无注册 benchmark case（F1，流程缺口非造假）；落表 tensor_maturity_verdicts id=242 level=2，建议 confirmed 消账。",
    "scenario_key": "KPI-QC-RECHECK",
    "mission_id": 15146,
    "tags": ["board-audit", "KPI-QC-RECHECK", "m15116", "maturity-verdict"],
    "markdown": md,
    "figures": [{
        "name": "fig1_triad_check.png",
        "caption": "三件套核验：主读数报告值 vs 本单两次独立复算（分母漂移带内，分子恒 274）",
        "png_base64": base64.b64encode(png).decode(),
    }],
}
req = urllib.request.Request(base + "/tensor/reports", method="POST")
req.add_header("Authorization", "Bearer " + tok)
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, data=json.dumps(payload).encode(), timeout=60) as r:
    out = json.load(r)
print("posted:", json.dumps(out, ensure_ascii=False)[:400])
