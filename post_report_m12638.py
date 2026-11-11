#!/usr/bin/env python3
"""发 AG31W1-N06（多保真度建模框架成熟化）论文级进展报告。

正文取 ``docs/m12638/MULTIFIDELITY_REPORT.md``（数字由 render_report.py 从证据 JSON 注入），
图取 ``docs/m12638/figures/*.png``，摘要里的判据数字从证据 JSON 读取（零手抄）。

用法：MISSION_ID=12638 python3 post_report_m12638.py
"""
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
MISSION = int(os.environ.get("MISSION_ID", "12638"))

DOC = ROOT / "docs/m12638/MULTIFIDELITY_REPORT.md"
EV = ROOT / "docs/m12638/evidence/multifidelity_evidence.json"
FIG_DIR = ROOT / "docs/m12638/figures"

FIGURES = [
    ("fig1_level_pareto.png",
     "图 1　三档保真度（L0 经验 / L1 势流筛选用 / L2 势流验证）的成本-精度 Pareto 前沿；"
     "横轴为实测单次成本（µs 中位数，对数轴），纵轴为对 L2 的 nRMSE。三档互不支配。"),
    ("fig2_forrester.png",
     "图 2　Forrester 解析基准：真值、低保真模型、仅用 10 个高保真点的单保真度 kriging、"
     "以及 10 高保真 + 50 低保真的 co-kriging（nRMSE 3.29% → 0.69%）。"),
    ("fig3_wigley_sweep.png",
     "图 3　Wigley 抛物型船（L/B=10, B/T=1.6）总阻力系数随 Fn 变化：验证过的 L2 参考、"
     "多保真度融合、L1 筛选用、L0 经验；右图为融合在未参与训练的 Fn 点上的误差（红虚线 10% 判据）。"),
    ("fig4_campaign.png",
     "图 4　6 维船型战役（预算 = 100 次 L2）：单保真度 / 自适应分配 / 规划式分配的预测误差对比，"
     "右图给出同一设计下各档单独 GP 的误差——说明信息在 L1 中而自回归链未取出的 honest FAIL 归因。"),
    ("fig5_solver_validation.png",
     "图 5　通用薄船 Michell 求解器（闭式长度积分）与仓库既有 Wigley 闭式解的独立互校"
     "（左，相对偏差 ≤1e-13）与 θ 求积收敛（右）。"),
]


def _login() -> str:
    req = urllib.request.Request(
        f"{API}/auth/login",
        data=json.dumps({"username": USER, "password": PASS}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]


def main() -> int:
    ev = json.loads(EV.read_text(encoding="utf-8"))
    crit = ev["criteria"]
    a = ev["case_a_forrester"]
    c = ev["case_c_adaptive"]
    d = ev["case_d_wigley"]
    costs = ev["level_costs_us"]

    figures = []
    for name, caption in FIGURES:
        p = FIG_DIR / name
        if not p.exists():
            print(f"missing figure {p}", file=sys.stderr)
            continue
        figures.append({"name": name, "caption": caption,
                        "png_base64": base64.b64encode(p.read_bytes()).decode()})

    summary = (
        "多保真度框架四项判据 A/B/D 机器通过、C honest FAIL："
        f"A 解析基准（Forrester，10 HF + 50 LF）co-kriging nRMSE "
        f"{a['co_kriging']['nrmse']*100:.2f}%（判据 ≤5%，单保真度 "
        f"{a['single_fidelity_high_only']['nrmse']*100:.1f}%，提升 "
        f"{a['improvement_vs_single_fidelity']:.1f}×）；"
        f"B 三档保真度成本 {costs['L0_empirical']:.0f}/{costs['L1_potential_coarse']:.0f}/"
        f"{costs['L2_potential_converged']:.0f} µs 与误差单调、全部非支配；"
        f"D Wigley Fn 扫掠融合对密集 L2 误差 {d['errors_on_held_out_points']['multi_fidelity']*100:.2f}%"
        f"（≤10%）、成本 {d['cost']['fraction']*100:.1f}%（≤30%）；"
        f"C 6 维战役自适应分配误差比单保真度高 {c['ratio_mf_over_sf']:.2f}×（判据 ≤0.8×）→ "
        f"honest FAIL，单层诊断显示信息在 L1（{c['single_level_diagnostic']['L1_potential_coarse']['nrmse']*100:.1f}%"
        f" vs 单保真度 {c['single_fidelity']['nrmse']*100:.1f}%）而自回归链因 λ0 偏置经 ρ 放大未能提取；"
        "另：RANS 层面的物理保真度判据 WITHHELD（平台树内无 RANS/LES 求解器，"
        "参考档为收敛薄船势流，证据 JSON 内显式标注）。"
        f"通用薄船求解器与仓库闭式 Wigley 解互校 ≤1e-13；pytest 20 例全绿。"
    )

    payload = {
        "title": "多保真度建模框架成熟化：GP/自回归 co-kriging/Pareto 前沿/预算自适应分配（含 honest FAIL 归因）",
        "summary": summary,
        "scenario_key": "multifidelity_framework_maturity",
        "mission_id": MISSION,
        "tags": ["platform", "multifidelity", "co-kriging", "pareto-front", "budget-allocation",
                 "gaussian-process", "thin-ship", "honest-fail"],
        "markdown": DOC.read_text(encoding="utf-8"),
        "figures": figures,
    }
    token = _login()
    req = urllib.request.Request(
        f"{API}/tensor/reports",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
    print(json.dumps(resp, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())