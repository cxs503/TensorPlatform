#!/usr/bin/env python3
"""Post the governance-suite maturation report to the tensor platform.

Reads the machine-generated ledgers produced by
``python3 -m tools.governance_maturity.run_maturity`` so that every number in
the posted report is programmatic (no hand-copied values).
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
MISSION = int(os.environ.get("MISSION_ID", "10864"))

FIG_DIR = ROOT / "artifacts/governance_maturity/figures"
FIGURES = [
    ("mutation_kill_rate.png", "图 1　AST 变异杀伤率与 Wilson 95% 区间（等价变异体不计入分母）。"),
    ("mutation_by_operator.png", "图 2　分算子杀伤率：各测试面对不同语法破坏的敏感度。"),
    ("gate_power_by_class.png", "图 3　判据腐败注入的 gate power（虚线为 0.9 验收线）。"),
    ("writeback_detection.png", "图 4　度量回写门各腐化类检出率。"),
    ("contract_confusion.png", "图 5　工件契约混淆矩阵（valid + 单缺陷腐化语料）。"),
]


def _login() -> str:
    req = urllib.request.Request(
        f"{API}/auth/login",
        data=json.dumps({"username": USER, "password": PASS}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]


def main() -> int:
    summary = json.loads((ROOT / "artifacts/governance_maturity/summary.json").read_text(encoding="utf-8"))
    markdown = (ROOT / "docs/P315W1-N10_governance_suite_maturity.md").read_text(encoding="utf-8")
    figures = []
    for name, caption in FIGURES:
        path = FIG_DIR / name
        if not path.exists():
            print(f"missing figure {path}", file=sys.stderr)
            continue
        figures.append({"name": name, "caption": caption,
                        "png_base64": base64.b64encode(path.read_bytes()).decode()})

    checks = summary["checks"]
    title = ("平台治理套件成熟化 v1：工件注册表 × AST 变异计量 × 判据 gate power × 度量回写门")
    summary_line = (
        f"四仪器全部机器可复算：契约校验准确率 {checks['a_artifact_registry']['accuracy']:.4f}"
        f"（{checks['a_artifact_registry']['modules']} 模块/{checks['a_artifact_registry']['repos']} 仓）；"
        f"变异杀伤率 {checks['b_mutation_kill_rate']['overall_kill_rate']:.3f}"
        f"（{checks['b_mutation_kill_rate']['targets']} 目标，每目标 ≥"
        f"{checks['b_mutation_kill_rate']['min_semantic_mutants']} 变异体，8 分片合并）；"
        f"负控 precision {checks['d_equivalent_controls']['precision']:.3f}；"
        f"gate_power {checks['c_gate_power']['gate_power']:.3f}"
        f"（{checks['c_gate_power']['criteria']} 条判据，干净语料误拒 "
        f"{checks['c_gate_power']['false_positive_rate']:.3f}）；"
        f"回退检测 {checks['e_writeback_gate']['rollback_detection_rate']:.3f} + 幂等写"
        f"{'通过' if checks['e_writeback_gate']['idempotency']['ledger_unchanged'] else '失败'}；"
        f"零领域语义命中 {checks['f_domain_semantics']['hits']}；pytest "
        f"{checks['g_pytest']['collected']} 例。"
    )
    if not summary["all_checks_pass"]:
        summary_line += " honest FAIL 项：" + ",".join(
            k for k, v in checks.items() if not v["pass"])

    payload = {
        "title": title,
        "summary": summary_line,
        "scenario_key": "platform_governance_maturity",
        "mission_id": MISSION,
        "tags": ["platform", "governance", "mutation-testing", "gate-power", "metric-writeback"],
        "markdown": markdown,
        "figures": figures,
    }
    token = _login()
    req = urllib.request.Request(
        f"{API}/tensor/reports",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
    print(json.dumps(resp, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())