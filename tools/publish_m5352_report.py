#!/usr/bin/env python3
"""发布 m5352 论文级进展报告到平台（:8010 /tensor/reports）。"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path("/root/tp-agent/work/m5352")
ENV = Path("/root/tp-agent/agent.env")


def load_env(p: Path) -> dict:
    cfg = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip()
    return cfg


CFG = load_env(ENV)
BASE = CFG["TENSOR_API_BASE"].rstrip("/")


def post(path: str, body: dict, token: str | None = None) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=240) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    tok = post("/auth/login", {"username": CFG["TENSOR_AGENT_USER"],
                               "password": CFG["TENSOR_AGENT_PASS"]})
    token = tok.get("access_token") or tok.get("token") or (tok.get("data") or {}).get("access_token")
    if not token:
        print("login failed:", json.dumps(tok, ensure_ascii=False)[:400])
        return 2

    md = (ROOT / "docs/reports/m5352_ledger_contract_and_replay_gate.md").read_text(encoding="utf-8")
    caps = {
        "m5352_consumer_contract.png": "图1 能力台账消费者契约门：四消费者 sha256 一致性 × 五组演练（基线/重定位/schema越界/台账篡改/全灭）",
        "m5352_replay_gate.png": "图2 复现门：分域复现率配 Wilson 95%CI，及五类不可复现根因账本（本次真实命中0，五类由已知答案负控证明可判）",
        "m5352_lbm_poiseuille.png": "图3 D2Q9 通道 Poiseuille 干净 venv 迭代解 vs 解析解，相对误差 0.03%–0.17%",
    }
    figs = []
    for name, caption in caps.items():
        p = Path("/tmp/m5352_figs") / name
        figs.append({"name": name, "caption": caption,
                     "png_base64": base64.b64encode(p.read_bytes()).decode()})

    figures_data = json.loads((ROOT / "docs/reports/m5352_figures.json").read_text(encoding="utf-8"))
    rep = figures_data["replay_gate"]["clean_venv"]
    payload = {
        "title": "m5352 证据治理成熟化：能力台账消费者契约门 + 已交付声明复现门",
        "summary": (
            "两道机器可裁的门：A) 四消费者（工具/CLI//health/报告gate）经单一解析器解析到同一台账 sha256 "
            "618d37ff…，重定位不抛异常且降级，schema 越界与台账漂移必红；B) 24 单分层抽样（6 域×4）在"
            f"零第三方包 venv 里复跑，复现率 {rep['reproduction_rate']:.0%}（24/24，Wilson 95%CI "
            f"{rep['ci95'][0]:.3f}–{rep['ci95'][1]:.3f}），五类根因自检 5/5 可判，注入篡改必判真实数值漂移并转红。"
            "诚实声明：本次抽样真实漂移命中 0（门的可红性由负控与篡改注入证明），结构/阻力域判据为自洽性检查，"
            "TensorLBM/FEM/DEM 真实交付代码未在本工作树检出，未做跨仓复跑。"
        ),
        "scenario_key": "evidence-governance",
        "mission_id": 5352,
        "tags": ["证据治理", "能力台账", "复现门", "消费者契约", "pytest", "m5352"],
        "markdown": md,
        "figures": figs,
    }
    raw = json.dumps(payload).encode()
    print(f"payload {len(raw)/1024:.1f} KiB")
    if len(raw) > 250 * 1024:
        print("payload 过大，拒绝提交（平台 >250KB 会静默失败）")
        return 3
    res = post("/tensor/reports", payload, token)
    print(json.dumps(res, ensure_ascii=False)[:600])
    rid = res.get("id") or (res.get("data") or {}).get("id")
    if rid:
        req = urllib.request.Request(f"{BASE}/tensor/reports/{rid}?mission_id=5352")
        req.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(req, timeout=120) as resp:
            got = json.loads(resp.read().decode())
        title = got.get("title") or (got.get("data") or {}).get("title")
        print("回查 OK:", rid, title)
    return 0


if __name__ == "__main__":
    sys.exit(main())