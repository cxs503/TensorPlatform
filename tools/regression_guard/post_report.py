#!/usr/bin/env python3
"""Publish the regression-guard mission report to the Tensor platform (:8010).

Credentials come from the environment (``TENSOR_API_BASE``, ``TENSOR_AGENT_USER``,
``TENSOR_AGENT_PASS``); nothing is written to disk except the payload that is
posted, and no secret is ever echoed.

    python3 tools/regression_guard/post_report.py [--mission 10879]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "regression_guard"
DOC = ROOT / "docs" / "REGRESSION_GUARD.md"

FIGURE_CAPTIONS = {
    "fig1_pipeline.png": "变更清单 → 依赖解析（台账+AST 图）→ 缓存探测 → 分批 pytest → 基线差分 → 结构化报告/退出码；"
                         "两条合法的零测试路径（无依赖命中、热缓存）在图中显式标注。",
    "fig2_metrics.png": "左：检出率（求解器 / API）与热缓存命中率，判据要求均为 100%，实测 100%。"
                        "右：111 个无依赖路径负控场景合计选中 0 个测试，误触发率 0.00%（上限 1%）。",
    "fig3_evolution.png": "真实仓四个演示的重执行量：执行数 vs 缓存回放数（D2 热缓存归零，证明“已缓存→不重跑”）。",
    "fig4_fanout.png": "每个受管 artifact 的影响半径：受影响任务数与重跑的测试文件数；基线共记录 129 个用例。",
}


def _login(base: str) -> str:
    user = os.environ["TENSOR_AGENT_USER"]
    password = os.environ["TENSOR_AGENT_PASS"]
    body = json.dumps({"username": user, "password": password}).encode()
    req = urllib.request.Request(
        f"{base}/auth/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data.get("access_token") or data.get("token") or data["data"]["access_token"]


def _figures() -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for path in sorted((ART / "figures").glob("*.png")):
        out.append({
            "name": path.name,
            "caption": FIGURE_CAPTIONS.get(path.name, path.name),
            "png_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        })
    return out


def build_payload(mission: int) -> Dict[str, Any]:
    criteria = json.loads((ART / "criteria.json").read_text(encoding="utf-8"))
    a, b, c, e = (criteria["a_solver_change"], criteria["b_platform_api_change"],
                  criteria["c_false_trigger"], criteria["e_negative_and_cache"])
    markdown = DOC.read_text(encoding="utf-8")
    summary = (
        "在 TensorPlatform 实现跨任务回归守卫：变更清单 + 依赖台账 + 静态 import 图 → 影响解析（每条选择带理由链）"
        "→ 内容寻址缓存 → 分批真实 pytest → 与基线逐用例差分 → 结构化 JSON 报告与退出码 0/1/2。"
        f"真实仓演示：求解器内核变更识别 {a['task_count']} 个依赖任务、检出率 {a['detection_rate']*100:.0f}%、"
        f"重跑 {a['tests_actually_rerun']} 个真实用例；沙箱植入真实数值缺陷后检出 {a['injected_regression_count']} 个回归；"
        f"API 变更检出率 {(b['detection_rate'] or 0)*100:.0f}%；{c['untracked_paths_tested']} 个无依赖路径负控误触发率 "
        f"{c['false_trigger_rate']*100:.2f}%；热缓存重跑 {e['cache_reexecuted_on_warm']} 个（复用成功）。"
    )
    return {
        "title": "跨任务回归守卫（Cross-Mission Regression Guard）：变更→依赖→重跑→比对 自动化流水线",
        "summary": summary,
        "scenario_key": "tensorplatform.regression_guard.cross_mission.v1",
        "mission_id": mission,
        "tags": ["regression-guard", "cross-mission", "dependency-graph", "ast-import-graph",
                 "test-selection", "content-addressed-cache", "fail-closed", "p380w2-n01"],
        "markdown": markdown,
        "figures": _figures(),
        "artifacts": {
            "criteria": str((ART / "criteria.json").relative_to(ROOT)),
            "demos": str((ART / "demo_reports.json").relative_to(ROOT)),
            "ledger": "config/regression_guard/task_ledger.json",
            "tests": "tests/regression_guard/",
        },
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", type=int, default=10879)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = build_payload(args.mission)
    out = Path("/tmp/report.json")
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"payload: {out} ({out.stat().st_size} bytes, {len(payload['figures'])} figures)")
    if args.dry_run:
        return 0
    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010").rstrip("/")
    if not base.endswith("/api/v1"):
        base = base + "/api/v1"
    token = _login(base)
    req = urllib.request.Request(
        f"{base}/tensor/reports",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            print("posted:", resp.status, resp.read()[:400].decode(errors="replace"))
    except urllib.error.HTTPError as exc:
        print("HTTP error:", exc.code, exc.read()[:800].decode(errors="replace"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())