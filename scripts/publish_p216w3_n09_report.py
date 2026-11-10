#!/usr/bin/env python3
"""Publish the p216w3-n09 cross-architecture gate report to the tensor platform.

The markdown is read from the artifact-driven report written by
``tools.cross_arch_gate.report_md`` and the figures from
``artifacts/cross_arch_gate/figures``, so what gets published is exactly what the
pipeline produced — no retyping, no hand-edited numbers.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = ROOT / "docs" / "reports" / "p216w3_n09_cross_arch_gate.md"
FIGDIR = ROOT / "artifacts" / "cross_arch_gate" / "figures"
CRITERIA = ROOT / "artifacts" / "cross_arch_gate" / "criteria.json"

CAPTIONS = {
    "fig1_band": "图 1 逐字段相对差 vs 容差带（对数轴）",
    "fig2_confusion": "图 2 判官混淆矩阵（注入类别 × 判决类别）",
    "fig3_criteria": "图 3 六项机器判据看板",
}

MISSION_ID = 9356


def login(api_base: str) -> str:
    payload = json.dumps({"username": os.environ["TENSOR_AGENT_USER"],
                          "password": os.environ["TENSOR_AGENT_PASS"]}).encode()
    req = urllib.request.Request(f"{api_base}/auth/login", data=payload,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]


def figures():
    out = []
    for path in sorted(FIGDIR.glob("*.png")):
        key = next((k for k in CAPTIONS if k in path.stem), path.stem)
        out.append({"name": path.stem,
                    "caption": CAPTIONS.get(key, path.stem),
                    "png_base64": base64.b64encode(path.read_bytes()).decode()})
    return out


def main() -> int:
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    token = login(api_base)
    criteria = json.loads(CRITERIA.read_text(encoding="utf-8"))
    crit = criteria["criteria"]
    summary = (
        f"跨架构基准复现门成熟化（p216w3-n09）：四类差异（位级一致/容差内/超容差/NaN）字段级判决 + "
        f"六项机器判据 all_pass={criteria['all_pass']}；整数逐位一致率 "
        f"{crit['c1_int_bit_exact_rate']['value']:.4f}，双精度容差带内占比 "
        f"{crit['c2_fp_within_band_fraction']['value']:.4f}，超容差检出率 "
        f"{crit['c3_over_tolerance_detection_rate']['value']:.4f}，误报率 "
        f"{crit['c3_false_positive_rate']['value']:.4f}，NaN 检出率 "
        f"{crit['c4_nan_detection_rate']['value']:.4f}，时延 "
        f"{crit['c5_e2e_latency_s']['value']:.2f}s，零领域语义命中 "
        f"{crit['c6_domain_semantics_hits']['value']}；x86 实测臂待 CI 首次运行（honest gap）。"
        "（本版由同一流水线重新生成：②节变异族计数改为代码生成，替代先前同任务报告。）")
    payload = {
        "title": "【门成熟化】跨架构基准复现门：loong64 vs x86 浮点可复现性与容差带",
        "summary": summary,
        "scenario_key": "cross-arch-repro-gate",
        "mission_id": MISSION_ID,
        "tags": ["cross-arch", "reproducibility", "floating-point", "tolerance-band",
                 "loongarch64", "x86_64", "platform-mechanism", "p216w3-n09"],
        "markdown": MARKDOWN.read_text(encoding="utf-8"),
        "figures": figures(),
    }
    req = urllib.request.Request(f"{api_base}/tensor/reports",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    try:
        result = json.loads(urllib.request.urlopen(req, timeout=60).read())
    except Exception as exc:                                     # pragma: no cover
        print(f"post failed: {exc}", file=sys.stderr)
        if hasattr(exc, "read"):
            print(exc.read().decode()[:2000], file=sys.stderr)
        return 1
    print(json.dumps({"posted": result, "figures": len(payload["figures"]),
                      "markdown_chars": len(payload["markdown"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())