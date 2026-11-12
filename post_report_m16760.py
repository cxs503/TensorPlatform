#!/usr/bin/env python3
"""发布 m16760（KPI-TOOLS-5 周期复测）报告到 tensor_reports（正文 + 3 图内嵌）。"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")
MD = ROOT / "docs" / "kpi" / "KPI_TOOLS_5_measure_m16760.md"
RESULT = ROOT / "docs" / "kpi" / "KPI_TOOLS_5_result.json"


def main() -> int:
    token = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"{API}/auth/login",
        data=json.dumps({"username": USER, "password": PASS}).encode(),
        headers={"Content-Type": "application/json"}), timeout=15).read())["access_token"]

    res = json.loads(RESULT.read_text(encoding="utf-8"))
    md = MD.read_text(encoding="utf-8")

    figures = []
    for f in res.get("figures", []):
        p = ROOT / f["path"]
        if not p.exists():
            continue
        figures.append({"name": p.name, "caption": f["caption"],
                        "png_base64": base64.b64encode(p.read_bytes()).decode()})

    body = {
        "title": (f"KPI-TOOLS-5 周期复测（m16760）：现值 {res['current']}/{res['target']} 类"
                  "外部工具 REAL 可用，缺口 0；暴露部署态 503 与注册表口径错配"),
        "summary": (
            f"只计量不改码：本工作树实跑产出台账+基准表+集成调用，主口径 "
            f"current={res['current']} 类（目标 {res['target']}，gap={res['gap']}，"
            f"超额 {res['surplus']}），27/27 条不变式经独立重判全通过，"
            f"11 条可评相对误差全部 <3%（最大 {res['lenses']['A_primary']['benchmark']['max_rel_err']:.2e}）；"
            "cli verify 重跑 digest/value_digest 双指纹一致；两次规格破坏反证分别落到 "
            "FAIL/WITHHELD。诚实缺口：对运行中平台实测 /external-tools/coverage 与 /benchmark "
            "均 503、/classes 全 UNOBSERVED（部署目录无 latest.json，平台无物化机制），"
            f"且注册表 tool_categories 的扫描口径量的是内部目录（guarded={res['lenses']['B_control']['current_guarded']}）"
            "与「外部工具可用」无关。已提 5 个派生锚点，本单未改任何平台代码/数据。"),
        "scenario_key": "kpi-meter",
        "mission_id": 16760,
        "tags": ["kpi-meter", "KPI-TOOLS-5", "external-tools", "measurement",
                 "honest-gap", "periodic-retest"],
        "markdown": md,
        "figures": figures,
    }
    req = urllib.request.Request(f"{API}/tensor/reports",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    try:
        out = json.loads(urllib.request.urlopen(req, timeout=60).read())
        print("posted:", json.dumps(out, ensure_ascii=False)[:300])
        print(f"figures embedded: {len(figures)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print("post failed:", type(exc).__name__, exc, file=sys.stderr)
        if hasattr(exc, "read"):
            print(exc.read().decode()[:500], file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())