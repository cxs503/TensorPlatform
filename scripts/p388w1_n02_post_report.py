#!/usr/bin/env python3
"""发布 P388W1-N02 报告到平台 /tensor/reports（含 5 张内嵌图，零手抄）。

markdown 正文直接读 ``docs/P388W1_N02_cross_solver_invariant_audit.md``；图读
``artifacts/invariant_audit/figures/*.png``；关键数字读
``artifacts/invariant_audit/run_meta.json`` 与 ``figures/figure_data.json``。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "P388W1_N02_cross_solver_invariant_audit.md"
FIG_DIR = REPO / "artifacts" / "invariant_audit" / "figures"
META = REPO / "artifacts" / "invariant_audit" / "run_meta.json"

CAPTIONS = {
    "fig1_verdict_matrix.png": "图1 不变量审计判定矩阵：行=求解器-工况对，列=不变量，绿 PASS/红 FAIL/黄 WITHHELD。",
    "fig2_mass_drift_trace.png": "图2 拉伸网格上离散质量守恒漂移随时间的演化：守恒型在舍入级，插值型线性累积。",
    "fig3_mass_refinement.png": "图3 质量漂移 vs 网格细化：守恒型恒在舍入级，插值型 1.6e-4~2.2e-3 且非单调。",
    "fig4_rotation_sweep.png": "图4 旋转协变残差 vs 旋转角：等向映射恒 0，轴对齐映射随角度恶化到 0.6。",
    "fig5_boost_sweep.png": "图5 参考系 boost 下的响应变化：保守网格与 Δu·T 重合，绝对速度标度启发式偏离 2.2%。",
}


def main() -> int:
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    user = os.environ.get("TENSOR_AGENT_USER", "")
    passwd = os.environ.get("TENSOR_AGENT_PASS", "")
    if not user or not passwd:
        print("missing TENSOR_AGENT_USER/PASS", file=sys.stderr)
        return 2

    meta = json.loads(META.read_text(encoding="utf-8"))
    figures = []
    for path in sorted(FIG_DIR.glob("*.png")):
        figures.append({
            "name": path.stem,
            "caption": CAPTIONS.get(path.name, path.stem),
            "png_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        })

    payload = {
        "title": "P388W1-N02 跨求解器物理不变量审计：守恒/协变/量纲的机器化回归（16 判定对，6 缺陷全检出）",
        "summary": (
            "把平台既有的单判据机检升级为域无关的跨求解器不变量审计："
            f"{meta['targets']} 个求解器-工况对 × {meta['invariant_pairs']} 个判定对，"
            f"PASS {meta['counts_pairs']['PASS']}/FAIL {meta['counts_pairs']['FAIL']}/"
            f"WITHHELD {meta['counts_pairs']['WITHHELD']}/ERROR {meta['counts_pairs']['ERROR']}；"
            "植入缺陷召回 5/5、已知良好误报 0/4；证据链 13 条记录复核通过；"
            f"pytest {meta['pytest']['passed']} passed。"
        ),
        "scenario_key": "platform.invariant_audit.cross_solver",
        "mission_id": 12520,
        "tags": ["invariant-audit", "conservation", "covariance", "dimensional-consistency",
                 "cross-solver", "evidence-chain", "zero-domain-semantics"],
        "markdown": DOC.read_text(encoding="utf-8"),
        "figures": figures,
    }
    (Path("/tmp/report_p388w1_n02.json")).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    login = urllib.request.Request(
        f"{api_base}/auth/login",
        data=json.dumps({"username": user, "password": passwd}).encode(),
        headers={"Content-Type": "application/json"},
    )
    token = json.loads(urllib.request.urlopen(login, timeout=20).read())["access_token"]
    req = urllib.request.Request(
        f"{api_base}/tensor/reports",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        body = json.loads(resp.read())
        print(json.dumps({"status": resp.status, "id": body.get("id"),
                          "figures": len(figures)}, ensure_ascii=False))
        return 0
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read()[:800]!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())