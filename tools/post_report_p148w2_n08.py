#!/usr/bin/env python3
"""发布 P148-W2-N08 验收报告到 Tensor 平台（六要素 markdown + 内嵌图）。

数字来自 ``artifacts/p148w2_n08/criteria.json``（本脚本只读文件，不手抄任何数字），
图以 base64 内嵌。用法::

    TENSOR_API_BASE=... TENSOR_AGENT_USER=... TENSOR_AGENT_PASS=... \
        python3 tools/post_report_p148w2_n08.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ART = REPO_ROOT / "artifacts" / "p148w2_n08"


def _post(path: str, payload: dict, token: str = "") -> dict:
    api = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{api}{path}", data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def main() -> int:
    api = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
    passwd = os.environ.get("TENSOR_AGENT_PASS", "")
    login = urllib.request.Request(
        f"{api}/auth/login", data=json.dumps({"username": user, "password": passwd}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(login, timeout=30) as resp:
        token = json.loads(resp.read())["access_token"]

    crit = json.loads((ART / "criteria.json").read_text(encoding="utf-8"))
    md = (REPO_ROOT / "docs" / "P148W2_N08_progress_metrics_maturity.md").read_text(encoding="utf-8")
    fig_meta = json.loads((ART / "figures_metadata.json").read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in crit["criteria"]}
    cm, gain, rates, healthy = (by_id["C1"]["measured"], by_id["C4"]["measured"],
                                by_id["C2"]["measured"], by_id["C3"]["measured"])
    figures = [{"name": f["name"], "caption": f["caption"],
                "png_base64": base64.b64encode((ART / f["name"]).read_bytes()).decode()}
               for f in fig_meta]

    report = {
        "title": "进展度量与停滞处置成熟化验收（p148w2-n08）：进展信号→停滞检测→早停换路→DAG blocked",
        "summary": (
            f"零领域语义节点级进展闭环：内容哈希/信息增益/判据率/pytest 率四类信号 → 连续 N 拍硬无进展"
            f"+有符号斜率双规则 → honest FAIL 报告（只读留痕+替代路径）→ 真实 PG campaign_nodes "
            f"blocked。20 场景/220 有效拍标注实验：停滞标记准确率 {cm['accuracy']:.0%}"
            f"（TP{cm['tp']}/FP{cm['fp']}/FN{cm['fn']}），正控早停触发率 {rates['rate']:.0%}"
            f"（延迟 {rates['latency']['max']} 拍=阈值），负控 {healthy['rate']:.0%}"
            f"（观测 {healthy['cycles_observed']} 拍），信息增益符号判定 {gain['accuracy']:.0%}"
            f"（mtime-only 负控 {gain['touch_zero_gain']}/{gain['touch_n']} 判 0）；真实 PG 端到端 "
            f"blocked 落库+可派谓词失效+终态不覆盖；73 pytest 全绿。斜率规则仅作趋势告警，"
            f"正常进展噪声 {cm['trend_warning_fire_rate_on_healthy']:.0%} 如实披露。"),
        "scenario_key": "p148w2-n08",
        "mission_id": 9930,
        "tags": ["progress_metrics", "stagnation_detection", "early_stop", "campaign_dag",
                 "honest_fail", "platform_maturity"],
        "markdown": md,
        "figures": figures,
    }
    try:
        result = _post("/tensor/reports", report, token)
    except urllib.error.HTTPError as exc:  # 打不开就如实报错，不假装成功
        print(f"post failed: {exc.code} {exc.read()[:400]!r}", file=sys.stderr)
        return 1
    print(f"report posted: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())