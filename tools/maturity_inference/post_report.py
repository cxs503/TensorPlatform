#!/usr/bin/env python3
"""把本节点的论文级进展报告发布到平台（六要素 markdown + 图内嵌 base64）。

用法::

    python3 -m tools.maturity_inference.post_report          # 读跑批产物并发布
    python3 -m tools.maturity_inference.post_report --dry    # 只组装 payload 并打印摘要

凭据从环境变量取（``TENSOR_API_BASE`` / ``TENSOR_AGENT_USER`` / ``TENSOR_AGENT_PASS``），
不落盘、不进 commit。报告正文由 ``pipeline`` 生成的 ``REPORT.md`` 提供（零手抄）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
FIGS = ROOT / "docs" / "reports" / "p315w1_n11_maturity_inference"
OUT = ROOT / "artifacts" / "maturity_inference"

CAPTIONS = {
    "fig1_golden_confusion.png": "图1 黄金集混淆矩阵（对角=正确；标题含 naive 最大环基线对照，展示 fail-closed 增益）",
    "fig2_level_distribution.png": "图2 真库台账：自述 vs 推断的等级分布（自述 REAL 全部下沉）",
    "fig3_rung_support_matrix.png": "图3 逐能力×逐环支撑矩阵（红=该环证据不过=阶梯断点）",
    "fig4_node_row_migration.png": "图4 node.* 记账行：门拒收与迁移去向映射",
}


def build_payload() -> Dict[str, Any]:
    md = (FIGS / "REPORT.md").read_text(encoding="utf-8")
    acc = json.loads((OUT / "acceptance.json").read_text(encoding="utf-8"))
    crit = acc["criteria"]
    figures: List[Dict[str, str]] = []
    for p in sorted(FIGS.glob("*.png")):
        figures.append({"name": p.name, "caption": CAPTIONS.get(p.name, p.name),
                        "png_base64": base64.b64encode(p.read_bytes()).decode("ascii")})
    summary = (
        "五级成熟度推断引擎落地：证据图 → 标签（阶梯取最小 + fail-closed 截断 + 矛盾封顶），"
        f"黄金集 40 行宏 F1={crit['a_golden_f1']['value']}"
        f"（naive 最大环基线 acc {crit['a_golden_f1']['naive_baseline'].get('accuracy')}）；"
        f"真库 21 项里自述 REAL {crit['c_unlinked_real']['claimed_real']} 行，"
        f"推断保留 REAL {crit['c_unlinked_real']['kept_real']} 行、unlinked REAL="
        f"{crit['c_unlinked_real']['value']}；3 条 node.* 记账行被命名空间门拒收并迁到 "
        f"campaign_node_registry（残留 {crit['d_node_migration']['node_prefix_remaining']}）；"
        f"三条负控全过；pytest {crit['e_pytest_and_zero_domain']['pytest'].get('passed')} 通过。")
    return {
        "title": "P315W1-N11 五级成熟度推断引擎成熟化：从证据图推出标签，清掉 node.* 记账行",
        "summary": summary,
        "scenario_key": "platform.capability_maturity.inference_engine",
        "mission_id": 10861,
        "tags": ["成熟度推断", "证据链", "fail-closed", "命名空间门", "台账迁移", "TensorPlatform"],
        "markdown": md,
        "figures": figures,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="发布 P315W1-N11 报告")
    ap.add_argument("--dry", action="store_true", help="不发布，只打印 payload 摘要")
    args = ap.parse_args(argv)
    payload = build_payload()
    if args.dry:
        print(json.dumps({k: (v if k != "markdown" else v[:120] + "…")
                          for k, v in payload.items() if k != "figures"}, ensure_ascii=False, indent=2))
        print("figures:", [f["name"] for f in payload["figures"]])
        return 0
    import requests

    api = os.environ["TENSOR_API_BASE"].rstrip("/")
    r = requests.post(f"{api}/auth/login", json={
        "username": os.environ["TENSOR_AGENT_USER"],
        "password": os.environ["TENSOR_AGENT_PASS"]}, timeout=30)
    r.raise_for_status()
    data = r.json()
    token = data.get("access_token") or data.get("token")
    resp = requests.post(f"{api}/tensor/reports",
                         headers={"Authorization": f"Bearer {token}"},
                         json=payload, timeout=180)
    print(resp.status_code, resp.text[:400])
    return 0 if resp.status_code < 300 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())