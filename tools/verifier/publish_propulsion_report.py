#!/usr/bin/env python3
"""Publish the propulsion verification pilot as a platform progress report.

Payload numbers come from the capture/verdict artifacts; figures are inlined as
base64 PNG read from ``artifacts/verifier/propulsion/``.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ART = os.path.join(REPO, "artifacts", "verifier", "propulsion")
DOC = os.path.join(REPO, "docs", "verifier", "PILOT_PROPULSION.md")

API_BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
PASS = os.environ["TENSOR_AGENT_PASS"]
MISSION_ID = int(os.environ.get("MISSION_ID", "1587"))


def post(path: str, body: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API_BASE}{path}", data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw else {}


def main() -> int:
    cap = json.load(open(os.path.join(ART, "platform_capture.json"), encoding="utf-8"))
    ver = json.load(open(os.path.join(ART, "propulsion_verification_verdict.json"),
                         encoding="utf-8"))
    md = open(DOC, encoding="utf-8").read()

    series = ver["capabilities_audited"]["propeller.open_water"]["summary"]
    endo = ver["capabilities_audited"]["calc.propulsion"]["summary"]
    resp = cap["end_to_end"]["response"]

    figures = []
    for name, caption in (
        ("fig1_coefficients_and_ratio.png",
         "图1 平台敞水系数：K_T 与 10K_Q 完全成比例（K_Q/K_T 恒定 0.100），效率仅由 J 线性决定。"),
        ("fig2_efficiency_vs_bound.png",
         "图2 η₀(J) 在三个螺距比下完全重合、J≥0.55 出现 0.85 钳位平台，并在 J=0.35–0.6 越过 Froude 动量理论上限 ηᵢ。"),
        ("fig3_endtoend_and_scoreboard.png",
         "图3 左：端到端效率链 η₀=%.3f、η_D=%.3f 均越过物理上限 1.0；右：%d 条判据记分板（%d 条失败）。"
         % (resp["open_water_eff"], resp["propulsive_eff"],
            series["n_constraints"] + endo["n_constraints"],
            series["n_failed"] + endo["n_failed"])),
    ):
        path = os.path.join(ART, name)
        figures.append({
            "name": name,
            "caption": caption,
            "png_base64": base64.b64encode(open(path, "rb").read()).decode(),
        })

    title = ("推进域验证试点：propeller.open_water / calc.propulsion 对解析界判据全线不合格"
             "（η₀ 越过动量上限、η_D>1、K_T 反推低两个数量级）")
    summary = (
        f"以 {series['n_constraints']+endo['n_constraints']} 条无系数判据（定义恒等式 + Froude 动量界 + "
        f"公开发包络）对审两个已标 REAL 的推进能力，{series['n_failed']+endo['n_failed']} 条失败："
        f"敞水端点 η₀ 恒为 J/(0.2π) 且与螺距比无关（K_Q≡0.1K_T）、J≥0.55 钳位 0.85、"
        f"J=0.4–0.6 越过动量理论上限（最大超界 {ver['capabilities_audited']['propeller.open_water']['constraints'][2]['measured']['worst_excess']['excess']*100:.2f} 个百分点）、"
        f"P/D=1 零推力进速比 1.50（公开敞水图 ≈0.85）；端到端 η₀={resp['open_water_eff']}、"
        f"η_D={resp['propulsive_eff']}、T·V/P_D=1.606、反推 K_T=0.0021。建议两项证据等级由 REAL 降为 MOCK。"
    )

    token = post("/auth/login", {"username": USER, "password": PASS}).get("access_token") \
        or post("/auth/login", {"username": USER, "password": PASS}).get("token")
    payload = {
        "title": title,
        "summary": summary,
        "scenario_key": "verifier/propulsion-verification",
        "mission_id": MISSION_ID,
        "tags": ["verifier", "propulsion", "verification-debt", "audit", "tp-ver-7"],
        "markdown": md,
        "figures": figures,
    }
    out = post("/tensor/reports", payload, token=token)
    print(json.dumps(out, ensure_ascii=False)[:600])
    with open("/tmp/report_propulsion_pilot.json", "w", encoding="utf-8") as fh:
        json.dump({"payload": {k: v for k, v in payload.items() if k != "figures"},
                   "figures": [f["name"] for f in figures], "response": out}, fh, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())