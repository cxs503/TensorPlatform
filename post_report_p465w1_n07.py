#!/usr/bin/env python3
"""把 p465w1-N07 的成熟化报告投递到 tensor 平台（只搬运工件，不手抄数字）。

markdown 来自 ``docs/GOVERNANCE_MATURITY_P465W1_N07.md``（由
``scripts/run_governance_maturity.py`` 从 ``artifacts/governance-maturity/acceptance.json``
程序化渲染），图来自 ``artifacts/governance-maturity/figures/``。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
ACC = os.path.join(ROOT, "artifacts/governance-maturity/acceptance.json")
DOC = os.path.join(ROOT, "docs/GOVERNANCE_MATURITY_P465W1_N07.md")

FIGURES = [
    ("fig1_loss_detection.png", "图1 丢失检测器灵敏度：注入丢包率 vs 检出丢失率（与 y=x 重合）"),
    ("fig2_portrait_error.png", "图2 画像一致性误差随丢包退化（含 5% 判据线）"),
    ("fig3_latency.png", "图3 单 tick 治理链路延迟 P50/P99/Max 与 5 s 预算"),
    ("fig4_audit_matrix.png", "图4 16 项契约一致性审计 diff 矩阵（全 0）"),
    ("fig5_chain_funnel.png", "图5 端到端链路漏斗（发出/落账/画像/工具对拍）"),
]


def main() -> int:
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
    passwd = os.environ.get("TENSOR_AGENT_PASS", "")
    login = urllib.request.Request(
        f"{api_base}/auth/login",
        data=json.dumps({"username": user, "password": passwd}).encode(),
        headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(login, timeout=30).read())["access_token"]

    acc = json.load(open(ACC, encoding="utf-8"))
    md = open(DOC, encoding="utf-8").read()
    c = acc["criteria"]
    nom = acc["e2e_nominal"]
    figs = []
    for name, caption in FIGURES:
        path = os.path.join(ROOT, "artifacts/governance-maturity/figures", name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                figs.append({"name": name, "caption": caption,
                             "png_base64": base64.b64encode(fh.read()).decode()})

    summary = (
        f"N01×N02×N03 治理集成：度量-契约审计 {c['a_metrics_contract_diff']['items']} 项 diff="
        f"{c['a_metrics_contract_diff']['total_diff']}，场交换-契约审计 "
        f"{c['b_field_contract_diff']['items']} 项 diff={c['b_field_contract_diff']['total_diff']}；"
        f"端到端链路 {nom['events']['heartbeats']} 心跳/{nom['events']['emitted']} 事件，"
        f"丢失率 {c['c_e2e']['event_loss_rate']}，画像一致性误差 "
        f"{c['c_e2e']['portrait_consistency_error']*100:.4f}%，P99 单 tick "
        f"{c['d_latency']['p99_ms']:.3f} ms；pytest {c['f_tests_api']['pytest_cases']} 例、"
        f"API {c['f_tests_api']['api_endpoints']} 端点且非法输入 422；"
        f"3 条缺口（conservative 映射 STUB 实测积分偏差 "
        f"{acc['findings_by_id']['conservative_map_is_stub']['evidence']['relative_error']*100:.2f}%、"
        f"未发布契约 solver.seakeeping/manoeuvring、seakeeping 迁移不可执行）如实入账。")

    report = {
        "title": "平台治理成熟化：度量工具 × 三契约源 × 场交换契约 的端到端集成验证（p465w1-N07）",
        "summary": summary,
        "scenario_key": "p465w1-n07",
        "mission_id": 14015,
        "tags": ["governance-maturity", "contract-source", "metrics-tools", "field-exchange",
                 "end-to-end", "fault-injection", "positive-control", "zero-domain-semantics",
                 "TensorPlatform"],
        "markdown": md,
        "figures": figs,
    }
    req = urllib.request.Request(
        f"{api_base}/tensor/reports", data=json.dumps(report).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, timeout=180)
        print("posted:", json.loads(resp.read()))
        return 0
    except Exception as exc:  # noqa: BLE001
        body = exc.read().decode() if hasattr(exc, "read") else ""
        print(f"post failed: {exc} {body}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())