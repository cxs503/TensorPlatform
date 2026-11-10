#!/usr/bin/env python
"""Publish the P167W2-N10 report to the tensor platform (and archive it locally).

The payload is assembled from the audit evidence bundle + the markdown report +
the generated figures.  If the platform API is unreachable the payload is still
written to ``tensor_reports/p167w2_n10_report.json`` so the report is never lost
(the archive is the fallback, not a substitute).
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_MD = os.path.join(ROOT, "docs", "p167w2-n10_cross_solver_conservation.md")
EVIDENCE = os.path.join(ROOT, "artifacts", "p167w2-n10", "audit_evidence.json")
ARCHIVE = os.path.join(ROOT, "tensor_reports", "p167w2_n10_report.json")

FIGURES = [
    ("fig1_four_ledger_residuals", "FEM→DEM 六账本残差：精确多面体路径 vs 等效球替代模型"),
    ("fig2_interface_flux_conservation", "LBM→FVM 耦合面通量守恒：网格研究与三种耦合面取向"),
    ("fig3_clock_synchronisation", "时钟同步语义：保守屏障滞后 vs 乐观回滚重放等价"),
]


def build_payload() -> dict:
    markdown = open(REPORT_MD, encoding="utf-8").read()
    ev = json.load(open(EVIDENCE, encoding="utf-8"))
    figures = []
    for name, caption in FIGURES:
        path = os.path.join(ROOT, "artifacts", "p167w2-n10", f"{name}.png")
        with open(path, "rb") as handle:
            figures.append({"name": name, "caption": caption,
                            "png_base64": base64.b64encode(handle.read()).decode()})
    return {
        "title": "P167W2-N10 跨库场交换守恒性验证面：FEM→DEM 四账本 + LBM→FVM 插值守恒 + 时钟同步语义",
        "summary": (
            f"精确凸多面体几何内核（四面体矩）驱动的三类跨库交接验证面："
            f"FEM→DEM 断裂交接六账本（质量/线动量/角动量/能量/全张量惯量/体积）最差残差 "
            f"{max(ev['fem_to_dem']['worst_residual'].values()):.2e}；LBM→FVM 库存 remap 质量残差 "
            f"{ev['lbm_to_fvm']['inventory']['mass_residual']:.2e}、耦合面通量残差 "
            f"{max(v['exact']['flux_residual'] for v in ev['lbm_to_fvm']['interface_flux'].values()):.2e}；"
            f"保守屏障偏差 {ev['clock']['audit']['conservative']['max_barrier_skew']:.2e}、"
            f"乐观回滚重放等价偏差 {ev['clock']['audit']['optimistic_rollback']['deviation_from_replay']:.2e}。"
            f"{ev['n_checks'] - ev['n_failed']}/{ev['n_checks']} 项机器判据通过。"
            f"顺带修复一个永不失败的伪守恒门（旧 LBM→FVM 通量检查恒为 0）。"),
        "scenario_key": "cross_solver_conservation",
        "mission_id": 9705,
        "tags": ["cross-solver", "conservation", "field-exchange", "clock-sync",
                 "multi-physics-coupling", "zero-domain-semantics", "p167w2-n10"],
        "markdown": markdown,
        "figures": figures,
        "metadata": {
            "node": "p167w2-n10-cross-solver-conservation",
            "repo": "TensorPlatform",
            "branch": "agent/m9705",
            "evidence": "artifacts/p167w2-n10/audit_evidence.json",
            "checks_passed": f"{ev['n_checks'] - ev['n_failed']}/{ev['n_checks']}",
            "capability_card": "docs/capability_registry/cards/CAP-007-platform-cross-solver-field-exchange-conservation.json",
        },
    }


def main() -> int:
    payload = build_payload()
    os.makedirs(os.path.dirname(ARCHIVE), exist_ok=True)
    with open(ARCHIVE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(f"archived payload -> {ARCHIVE} "
          f"({len(payload['markdown'])} md chars, {len(payload['figures'])} figures)")

    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
    password = os.environ.get("TENSOR_AGENT_PASS", "")
    try:
        login = urllib.request.Request(
            f"{base}/auth/login",
            data=json.dumps({"username": user, "password": password}).encode(),
            headers={"Content-Type": "application/json"})
        token = json.loads(urllib.request.urlopen(login, timeout=10).read())["access_token"]
    except (urllib.error.URLError, KeyError, TimeoutError, OSError) as exc:
        print(f"report API unreachable ({exc}); payload archived only", file=sys.stderr)
        return 2

    post = urllib.request.Request(
        f"{base}/tensor/reports",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    try:
        response = urllib.request.urlopen(post, timeout=60)
        print("report posted:", response.status, response.read()[:400].decode("utf-8", "replace"))
        return 0
    except urllib.error.URLError as exc:
        print(f"post failed ({exc}); payload archived only", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())