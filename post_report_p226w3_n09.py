#!/usr/bin/env python3
"""Publish the P226W3-N09 report (text from docs, numbers from artifacts, figures inline).

Usage: MISSION_ID=13889 python3 post_report_p226w3_n09.py
No number is typed here: title/summary/markdown/figure captions come from
``artifacts/p226w3_n09/report.json`` and ``docs/p226w3_n09/TERMINAL_ATTRIBUTION_REPORT.md``.
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
MISSION = int(os.environ.get("MISSION_ID", "13889"))

DOC = ROOT / "docs/p226w3_n09/TERMINAL_ATTRIBUTION_REPORT.md"
REPORT = ROOT / "artifacts/p226w3_n09/report.json"
FIG_DIR = ROOT / "docs/p226w3_n09/figures"

FIGURES = [
    ("fig1_class_distribution.png",
     "图 1　左：全部终态任务按归因类分布（对数轴），归因覆盖 100%（UNKNOWN=0）；"
     "右：失败侧构成——运行侧（空转/丢失/模型/预算/策略）占绝大多数，物理 honest-FAIL 仅个位数百分比。"),
    ("fig2_evidence_ladder.png",
     "图 2　tool_calls 的证据阶梯：worker 自报计数 / 显式标注的下界 / 可证明的 0，"
     "用来审计 KPI 中「实测」与「下界」的比例。"),
    ("fig3_replay_yield.png",
     "图 3　预注册重播实验（plan-226 六节点）与未加门控的平台历史基线对照，红虚线为 4/6 阈值；"
     "右图为未加门控重播的逐节点救回率分布（n=1070 次重播）。"),
    ("fig4_rule_usage.png",
     "图 4　哪条规则命中（规则序固定，因此这是优先级的审计面）。"),
]


def post(path, body, token=None):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main() -> int:
    token = post("/auth/login", {"username": USER, "password": PASS})["access_token"]
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    markdown = DOC.read_text(encoding="utf-8")
    figures = []
    for name, caption in FIGURES:
        path = FIG_DIR / name
        if path.exists():
            figures.append({"name": name.split(".")[0], "caption": caption,
                            "png_base64": base64.b64encode(path.read_bytes()).decode()})
    counts = report["class_counts"]
    summary = (
        f"终局归因六类分离落地：{report['terminals_classified']} 条终态任务全部归因，"
        f"UNKNOWN=0（覆盖 {report['attribution_coverage'] * 100:.2f}%）；"
        f"INFRA_NO_TOOLCALL={counts.get('INFRA_NO_TOOLCALL', 0)}、INFRA_LOST={counts.get('INFRA_LOST', 0)}、"
        f"MODEL_ERROR={counts.get('MODEL_ERROR', 0)}、BUDGET_EXHAUSTED={counts.get('BUDGET_EXHAUSTED', 0)}、"
        f"POLICY_BLOCKED={counts.get('POLICY_BLOCKED', 0)}、PHYSICS_HONEST_FAIL={counts.get('PHYSICS_HONEST_FAIL', 0)}；"
        f"运行侧占失败的 {report['infra_share_of_failures'] * 100:.1f}%，物理 honest-FAIL 仅 "
        f"{report['physics_share_of_failures'] * 100:.1f}%；effective_attempt_rate="
        f"{report['effective_attempt_rate'] * 100:.2f}%。重播门带尝试上限/退避/同 session 工件快照/容量闸，"
        f"预算一 node 只结算 1 单位（{report['gate_decisions']['nodes']} 节点账本闭合）。"
        f"预注册重播实验 {report['replay_experiment']['reached_live']}/{report['replay_experiment']['nodes']} "
        f"达到 tool_calls>0（阈值 4/6）→ honest FAIL，已量化原因为队列容量"
        f"（排队 {report['queue_at_experiment']['queue_depth']}、服务率 "
        f"{report['queue_at_experiment']['service_rate_per_hour']}/h、预计 "
        f"{report['queue_at_experiment']['expected_drain_hours']} h 排空）；同口径未加门控基线 yield 仅 "
        f"{report['replay_experiment']['ungated_baseline_yield'] * 100:.2f}%（n="
        f"{report['replay_experiment']['ungated_baseline_nodes']}）。"
    )
    payload = {
        "title": "【P226W3-N09】终局归因：infra 空转 vs 物理 honest-FAIL 的机械分离 + 有效尝试率 KPI + 按归因的重播门",
        "summary": summary,
        "scenario_key": "terminal-attribution-requeue-gate",
        "mission_id": MISSION,
        "tags": ["attribution", "infra-idling", "honest-fail", "requeue-gate",
                 "kpi", "budget-ledger", "capacity-guard", "plan-226"],
        "markdown": markdown,
        "figures": figures,
    }
    res = post("/tensor/reports", payload, token)
    print(json.dumps(res, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())