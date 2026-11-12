#!/usr/bin/env python3
"""m15482 报告发布：把 docs/board_audit/m15482/audit_report.md + 图发到 tensor_reports。"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

WT = Path("/root/tp-agent/work/m15482")
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
ENV = dict(l.strip().split("=", 1) for l in open("/root/tp-agent/agent.env")
           if l.strip() and not l.startswith("#") and "=" in l)


def call(path, payload=None, token=None):
    req = urllib.request.Request(API + path, method="POST" if payload is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(req, data=data, timeout=120) as r:
        return json.loads(r.read() or b"{}")


def main() -> int:
    tok = call("/auth/login", {"username": ENV["TENSOR_AGENT_USER"],
                               "password": ENV["TENSOR_AGENT_PASS"]})["access_token"]
    md = (WT / "docs/board_audit/m15482/audit_report.md").read_text()
    png = (WT / "runs/m15482_audit/fig_tau_recovery.png").read_bytes()
    payload = {
        "title": "m15482 交差核验：m15444 / GAP-PP-5-gpu-cluster 判级 L2（独立复跑逐位再现 τ=0.89971）",
        "summary": ("board-audit 对 m15444 三件套逐项机器核验：run_artifacts 10/10 真；τ 恢复 ref 0.9 vs got 0.89971（0.032% < 15%）本单独立复跑逐位一致；"
                    "tensor_benchmark_cases 该 anchor 0 行（正式基准缺位）；repro_cmd 矩阵与工件不符且依赖 TensorLBM 未提交补丁（干净 HEAD 复跑 38.89% FAIL）。判级 L2，落表 verdict#327。"),
        "scenario_key": "board-audit",
        "mission_id": 15482,
        "tags": ["board-audit", "交差核验", "GAP-PP-5-gpu-cluster", "m15444", "maturity", "differentiable-LBM"],
        "markdown": md,
        "figures": [{"name": "fig_tau_recovery.png",
                     "caption": "(a) m15444 真实工件 loss 收敛；(b) τ 恢复三方对照（ref/交付态/干净 HEAD）；(c) 墙钟复现性",
                     "png_base64": base64.b64encode(png).decode()}],
    }
    r = call("/tensor/reports", payload, tok)
    print("report id:", r.get("id"), "status:", r.get("status"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())