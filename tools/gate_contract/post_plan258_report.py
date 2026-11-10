#!/usr/bin/env python3
"""发布 plan 258 门证据契约报告（六要素 + 内嵌两张图）。"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "gate_contract"


def main() -> int:
    api = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    login = urllib.request.Request(
        f"{api}/auth/login",
        data=json.dumps({"username": os.environ["TENSOR_AGENT_USER"],
                         "password": os.environ["TENSOR_AGENT_PASS"]}).encode(),
        headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(login, timeout=15).read())["access_token"]
    md = (ROOT / "docs" / "gate_contract_plan258.md").read_text(encoding="utf-8")
    figures = []
    for name, caption in (("fig_verdicts.png", "九门 gate.json 复算判定分布（PASS/FAIL/WITHHELD）"),
                          ("fig_negcontrol_matrix.png", "五类伪证据检出矩阵，假通过 0")):
        path = OUT / name
        figures.append({"name": name, "caption": caption,
                        "png_base64": base64.b64encode(path.read_bytes()).decode("ascii")})
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    body = {
        "title": "验收门证据契约成熟化：gate.json 强制化 + 缺失即 WITHHELD + 注入式负控（plan 258 九节点回填）",
        "summary": (f"gate.json 契约（判据/阈值/单位/工件+sha256/指针/运行命令/环境指纹）+ 平台侧 gate_verify "
                    f"（结构→工件存在→sha256→指针复算→阈值；三值判定）+ dev_acceptance 强制前门。"
                    f"plan258 九节点回填覆盖 {summary['C1_backfill']['coverage']:.0%}，复算 "
                    f"PASS {summary['C1_backfill']['grades']['PASS']}/FAIL {summary['C1_backfill']['grades']['FAIL']}"
                    f"/WITHHELD {summary['C1_backfill']['grades']['WITHHELD']}（静默通过 {summary['C1_backfill']['silent_pass']}）；"
                    f"负控 {summary['C2_negative_control']['n_cases']} 例假通过 {summary['C2_negative_control']['false_pass']}；"
                    f"台账 20 条抽样回溯 ratio {summary['C5_ledger']['ratio']:.2f}；域词扫描 0 命中；25 项 pytest 全绿。"),
        "scenario_key": "gate-contract-maturity",
        "mission_id": 9148,
        "tags": ["gate-contract", "evidence", "withheld", "negative-control", "dev-acceptance",
                 "plan258", "platform-mechanism"],
        "markdown": md,
        "figures": figures,
    }
    req = urllib.request.Request(f"{api}/tensor/reports", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    res = json.loads(urllib.request.urlopen(req, timeout=60).read())
    print(json.dumps(res, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
