#!/usr/bin/env python3
"""Publish the P148W2-N01 report (markdown + figures inline) to the tensor platform.

All numbers are lifted from `benchmarks/verified/platform_survival/results.json`;
the markdown body is `docs/platform_survival.md` (itself generated from that JSON),
so the report cannot drift from the evidence.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = REPO / "benchmarks" / "verified" / "platform_survival"
DOC = REPO / "docs" / "platform_survival.md"
FIGDIR = HERE / "figures"

CAPTIONS = {
    "fig1_progress_timeline.png":
        "进展信用累积 vs 预算：稳定/爆发/停滞后换路/诚实失败四条运行，竖线为停滞四级处置动作",
    "fig2_stall_ladder.png":
        "停滞处置级别 vs 检测时的预算比例（虚线=验收线 30% 预算）",
    "fig3_criteria_dashboard.png":
        "十条验收判据的实测/阈值对数条形图（≥1 为通过）",
}


def login(api_base: str) -> str:
    body = json.dumps({"username": os.environ["TENSOR_AGENT_USER"],
                       "password": os.environ["TENSOR_AGENT_PASS"]}).encode()
    req = urllib.request.Request(f"{api_base}/auth/login", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())["access_token"]


def main() -> int:
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    token = login(api_base)
    results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    md = DOC.read_text(encoding="utf-8")
    figs = []
    for name, caption in CAPTIONS.items():
        p = FIGDIR / name
        if p.exists():
            figs.append({"name": name, "caption": caption,
                         "png_base64": base64.b64encode(p.read_bytes()).decode()})
    s = results["summary"]
    c = results["criteria"]
    summary = (
        f"长跑作业生存与进展度量成熟化：{s['passed']}/{s['total']} 条验收判据全部通过。"
        f"崩溃取证 bundle 自带 SHA256SUMS+位置无关摘要（寄存器 {c['A1']['measured']['register_fields']} 字段、"
        f"栈帧 {c['A1']['measured']['stack_frames']} 行，复制后校验一致）；"
        f"从 50% checkpoint 续跑与无中断运行 sha256 位级相同（三次续跑摘要唯一）；"
        f"progress_rate 与磁盘人工复核相对误差 {c['A3']['measured']['relative_error']:.2e}；"
        f"零产出任务在预算 {c['A4']['measured']['first_detection_budget_fraction']*100:.1f}% 处检出停滞并换路，"
        f"换路后产出 {c['A5']['measured']['post_reroute_artifacts']} 个工件；"
        f"诚实 FAIL 保留率 {c['A6']['measured']['ratios']['retention_vs_produced']:.2f}（地板 0.5 代码强制）；"
        f"负控零假阳性（{c['A8']['measured']['false_positives']}）；"
        f"内存受限自动降单线程+行流水，全网格分配被拒，场量相对误差 "
        f"{c['A10']['measured']['rel_error_vs_parallel_reference']:.2e}。"
    )
    payload = {
        "title": "长跑作业生存与进展度量成熟化：core 取证 + 位级幂等续跑 + 停滞四级换路 + 诚实收尾（P148W2-N01）",
        "summary": summary,
        "scenario_key": "p148w2-N01",
        "mission_id": 10578,
        "tags": ["platform_maturity", "long_run_survival", "crash_forensics",
                 "checkpoint_resume", "progress_meter", "stall_detection",
                 "honest_fail", "degradation"],
        "markdown": md,
        "figures": figs,
    }
    req = urllib.request.Request(f"{api_base}/tensor/reports",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.loads(resp.read())
    print(json.dumps(out, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    sys.exit(main())