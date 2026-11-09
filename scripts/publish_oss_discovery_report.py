#!/usr/bin/env python3
"""Publish the 发现单 (discovery note) to TensorPlatform tensor_reports.

mission 8307 / scenario_key=oss-inventory. Markdown and figure are read from the
generated artifacts so the report body is byte-identical to the reviewed doc.
"""
import base64
import json
import os
import urllib.request

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
PWD = os.environ["TENSOR_AGENT_PASS"]
ROOT = "/root/tp-agent/work/m8307"
DOC = ROOT + "/docs/oss_inventory_discovery_multiphase.md"
FIG = ROOT + "/artifacts/m8307/fig_discovery_overview.png"
DATA = ROOT + "/artifacts/m8307/shortlist.json"


def post(path, payload, token=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def main():
    tok = post("/auth/login", {"username": USER, "password": PWD})
    token = (tok.get("access_token") or tok.get("token")
             or tok.get("data", {}).get("access_token"))
    if not token:
        raise SystemExit(f"login failed keys={list(tok)}")

    d = json.load(open(DATA))
    s = d["stats"]
    markdown = open(DOC).read()
    fig = [{"name": "fig_discovery_overview.png",
            "caption": "发现单总览：(a) 8 个候选的星数与 tier（含最近 push 年月）；"
                       "(b) 137 仓证据池的 last-push 年份分布与『停更>5 年』判据线；"
                       "(c) 查询召回自检——在盘 24 仓中 16 仓被检索命中（67%）",
            "png_base64": base64.b64encode(open(FIG, "rb").read()).decode()}]

    tiers = s["tier_counts"]
    summary = (
        f"多相/多组元专项发现单：22 条 GitHub 查询 + 权威清单 34 条链接交叉核验，"
        f"{s['repos_with_evidence']} 个清单外仓落证据，最终提名 8 个候选"
        f"（tier1 {tiers['1']}/tier2 {tiers['2']}/tier3 {tiers['3']}），"
        f"与在盘 {s['inventory_size']} 仓零重叠（生成时 assert），另列 {s['n_excluded']} 项明确排除 + "
        f"{s['n_watch']} 项观察。头号候选 lanl/MF-LBM(132★, BSD-3)，"
        "结构性发现=『母体缺位』：在盘 openLBMPM 是 openLB 的多相扩展分支，上游 openLB/openLB 从不在册。"
        "否定性发现：高密度比 SCMP 开源供给极薄（短语查询 0 命中，色梯度/相场命中多为 1–5★ 新建仓）"
        "=> 该方向应按『算法复现』而非『代码借鉴』立单。"
        f"召回自检 {s['recall_inventory_hit_by_queries']}/{s['recall_inventory_total']}"
        f"={s['recall_rate']*100:.0f}%（检索式存在盲区，已如实登记）。"
        "本单只发现不深盘：未 clone/未编译/未跑算例，全部候选 status=suggested。"
    )
    payload = {
        "title": "【盘点】发现单（多相/多组元专项）",
        "summary": summary,
        "scenario_key": "oss-inventory",
        "mission_id": 8307,
        "tags": ["oss-inventory", "lattice-boltzmann", "multiphase", "discovery",
                 "FEAT-OSS-INVENTORY-LBM", "m8307"],
        "markdown": markdown,
        "figures": fig,
    }
    res = post("/tensor/reports", payload, token)
    print(json.dumps(res, ensure_ascii=False)[:800])


if __name__ == "__main__":
    main()