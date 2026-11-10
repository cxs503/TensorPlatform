#!/usr/bin/env python3
"""发布 P143W2C-N03 报告到平台（六要素 md + 3 图内嵌 base64）。"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASSWORD = os.environ.get("TENSOR_AGENT_PASS", "")
MISSION_ID = int(os.environ.get("MISSION_ID", "9189"))

CAPTIONS = {
    "fig1_three_class_and_codes.png":
        "图1 三分类回填判定（240 条历史评价）与 fail-closed reason code 分布",
    "fig2_tamper_and_idempotency.png":
        "图2 单字节篡改后授权数 146→145；首跑/二跑写入行数（幂等 0 变动）",
    "fig3_settlement_clearing.png":
        "图3 结算面 chain_blocked 160→62：98 行由内容可追溯链解封",
}


def login() -> str:
    body = json.dumps({"username": USER, "password": PASSWORD}).encode()
    req = urllib.request.Request(f"{API}/auth/login", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["access_token"]


CAPTIONS_SUPPLEMENT = {
    "fig4_real_db_probe.png":
        "图4 真实库 evidence_refs 形态分布与三分类（全量 4632 条只读体检，全部 fail-closed 封顶）",
}


def build_payload(supplement: bool) -> dict:
    doc = ("docs/backfill_wiring_P143W2C_N03_real_db.md" if supplement
           else "docs/backfill_wiring_P143W2C_N03.md")
    md = (REPO / doc).read_text(encoding="utf-8")
    fig_dir = REPO / "artifacts/backfill_wiring/p143w2c_n03/figures"
    captions = dict(CAPTIONS, **CAPTIONS_SUPPLEMENT)
    names = ["fig4_real_db_probe.png"] if supplement else sorted(
        p.name for p in fig_dir.glob("*.png") if p.name != "fig4_real_db_probe.png")
    figures = [{
        "name": name,
        "caption": captions.get(name, name),
        "png_base64": base64.b64encode((fig_dir / name).read_bytes()).decode(),
    } for name in names]
    if supplement:
        return {
            "title": "P143W2C-N03 补测：真实库只读扩样（4632 条历史评价 0 条可内容追溯）",
            "summary": (
                "在平台真实 PostgreSQL 上只读（default_transaction_read_only=on）全量体检 "
                "outcome_evaluations 4632 条：授权 0 / 封顶 4632 / 校验失败 0；根因是历史 "
                "evidence_refs 为 mission:N#result.field 形式的 DB 指针而非工件目录，"
                "从未 manifest 化 → fail-closed 全部封顶；真实库 S3+confirmed 为 0、chain_blocked 0。"
            ),
            "scenario_key": "evidence-chain-backfill",
            "mission_id": MISSION_ID,
            "tags": ["evidence-chain", "real-db", "read-only", "fail-closed",
                     "structural-debt", "backfill"],
            "markdown": md,
            "figures": figures,
        }
    return {
        "title": "P143W2C-N03 run-manifest 接进回填器：五元证据链内容可追溯化",
        "summary": (
            "240 条历史评价回填：完整链授权 146 / 缺件封顶 55 / 校验失败 39（与夹具口径精确相等）；"
            "单字节篡改检出、二跑 0 行变动、手写 SQL 精确对拍、S3+confirmed 行数不增；"
            "结算面 chain_blocked 160→62（清偿 98 行）。pytest 35 项含红队 5 例全绿。"
        ),
        "scenario_key": "evidence-chain-backfill",
        "mission_id": MISSION_ID,
        "tags": ["evidence-chain", "run-manifest", "backfill", "fail-closed",
                 "idempotency", "tamper-detection", "settlement"],
        "markdown": md,
        "figures": figures,
    }


def post(payload: dict) -> dict:
    token = login()
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{API}/tensor/reports", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--supplement", action="store_true",
                        help="发布真实库只读补测报告（fig4）")
    args = parser.parse_args()
    result = post(build_payload(args.supplement))
    print("posted id:", result.get("id"), "figures:", [f["name"] for f in result.get("figures", [])])
    return 0


if __name__ == "__main__":
    sys.exit(main())