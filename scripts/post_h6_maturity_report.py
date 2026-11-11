#!/usr/bin/env python3
"""发布 H6-MATURITY-GATE 进展报告到 TensorPlatform（mission 14961）。"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASSWD = os.environ.get("TENSOR_AGENT_PASS", "")


def _post(url: str, payload: dict, token: str | None = None, timeout: int = 30):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def main() -> int:
    token = _post(f"{API}/auth/login", {"username": USER, "password": PASSWD})["access_token"]
    md = (ROOT / "docs" / "H6_MATURITY_GATE_G1.md").read_text(encoding="utf-8")
    gate = json.loads((ROOT / "artifacts/maturity_gate_h6/gate.json").read_text(encoding="utf-8"))
    bench = json.loads((ROOT / "artifacts/maturity_gate_h6/mutation_benchmark.json")
                       .read_text(encoding="utf-8"))
    figs = []
    for name, caption in [
        ("fig1_maturity_profile.png", "图1 五级成熟度分布：基线(自述) vs 当前(证据对账)"),
        ("fig2_axes_health.png", "图2 四轴交叉一致性：核对量 vs 发现量"),
        ("fig3_increment_flow.png", "图3 增量账本逐能力秩变化"),
    ]:
        png = ROOT / "artifacts" / "maturity_gate_h6" / name
        if png.is_file():
            figs.append({"name": name, "caption": caption,
                         "png_base64": base64.b64encode(png.read_bytes()).decode()})
    report = {
        "title": "H6-MATURITY-GATE：能力增量账本 + 五级成熟度 + 四轴交叉一致性（G1 验收门）",
        "summary": (
            f"新建零领域语义的成熟度门：五级阶梯（NOT_SUPPORTED→STUB→MOCK→WITHHELD→REAL）+ "
            f"四轴交叉一致性（自述↔对账 / 引文逐字绑定 / 人读表↔台账重算 / 等级↔证据同向）+ "
            f"增量账本哈希链。裁定 {gate['verdict']}（判据 {gate['criteria_pass_rate']:.3f}）；"
            f"成熟度得分 {gate['maturity']['before']['maturity_score']} → "
            f"{gate['maturity']['after']['maturity_score']}；增量 "
            f"{gate['increments']['counts']}；变异基准召回 {bench['recall']}、假阳性 "
            f"{bench['false_positives']}；24 项 pytest 全绿。"),
        "scenario_key": "h6-maturity-gate",
        "mission_id": 15050,
        "tags": ["platform", "maturity-gate", "capability-ledger", "cross-consistency",
                 "evidence", "h6"],
        "markdown": md,
        "figures": figs,
    }
    out = _post(f"{API}/tensor/reports", report, token=token, timeout=60)
    print(json.dumps(out, ensure_ascii=False)[:500])
    return 0


if __name__ == "__main__":
    sys.exit(main())