#!/usr/bin/env python3
"""把 IN55W3-N07（mission 10363）判级可靠性报告发到平台。

全部数字来自 ``artifacts/m10363/grader_reliability.json``（本 commit 内），
正文来自 ``docs/IN55W3-N07_grader_reliability.md``（同一工件的渲染结果），
图来自 ``artifacts/m10363/figures/*.png``——**零手抄**：本脚本只做拼装与发送。

    python3 post_report_m10363.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent
ARTIFACT = WORKTREE / "artifacts/m10363/grader_reliability.json"
DOC = WORKTREE / "docs/IN55W3-N07_grader_reliability.md"
FIGDIR = WORKTREE / "artifacts/m10363/figures"

API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")


def build_body() -> dict:
    art = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    md = DOC.read_text(encoding="utf-8")
    crit = {c["id"]: c for c in art["criteria"]}
    cov = art["coverage"]
    ex = art.get("exploratory", {}) or {}
    inj = art["injection"]
    rec = [r for r in art["recovery"]["rows"] if r.get("status") == "ok"]
    max_bias = max(abs(r["bias"]) for r in rec)
    min_cov = min(r["ci_coverage"] for r in rec)

    figures = []
    for f in art.get("figures", []):
        png = (WORKTREE / f["path"]).read_bytes() if not Path(f["path"]).is_absolute() \
            else Path(f["path"]).read_bytes()
        figures.append({"name": f["name"], "caption": f["caption"],
                        "png_base64": base64.b64encode(png).decode("ascii")})

    summary = (
        "判级可靠性门落库：Krippendorff α(ordinal)/Gwet AC/ICC + 按单元 bootstrap CI + φ_g 方差分解 + "
        "可控真值合成恢复夹具（α_true∈{0.2,0.5,0.8}×200 重复）+ α<0.667 强制第三审判的决策门。"
        f"六判据 J1..J6 全部 {art['adjudication']['overall']}：α 恢复 max|bias|={max_bias:.4f}、"
        f"95%CI 覆盖率最低 {min_cov:.2f}、ICC 功效 {crit['J3']['value']['power']:.2f}"
        f"（FP={crit['J3']['value']['false_positive_rate']:.3f}）、注入 ±0.5 级判级者 Δα 相对误差 "
        f"{inj['relative_error']:.3f}。真实台账 honest WITHHELD：{cov['n_records']} 条有效记录里"
        f"**跨判级者配对 = {cov['n_cross_grader_pairs']}**（没有任何 mission 被两个不同判级者评过），"
        f"故 α 不可算；唯一双评来自被判据拒收的尝试（{ex.get('n_pairable_missions')} 个），"
        f"探索性 α_S={ex.get('alpha_ordinal', float('nan')):.3f}"
        f"（CP 精确区间 {ex.get('pair_agreement_S', {}).get('lo', float('nan')):.2f}–"
        f"{ex.get('pair_agreement_S', {}).get('hi', float('nan')):.3f}）、α_E={ex.get('alpha_e_ordinal', float('nan')):.3f}"
        f"——S 档判级稳定、E 档系统性不一致（E 一致率仅 "
        f"{ex.get('pair_agreement_E', {}).get('n_agree')}/{ex.get('pair_agreement_E', {}).get('n_pairs')}）。"
        f"决策门：{art['gate']['verdict']['status']}，{art['gate']['bucket_counts']['single']} 个单评 mission "
        "全部标 UNRELIABLE、禁作升账依据，并给出「再补 "
        f"{art['gate']['to_reach_min_pairable']['needed_extra_second_evaluations']} 次第二判级即可出数」的最小投入。"
    )

    appendix = (
        "\n\n## 附：机器口径（自动注入，防摘要与工件漂移）\n\n"
        f"* 工件 `{art['artifact_path']}`；生成时间 {art['generated_at']}；耗时 {art['elapsed_s']}s。\n"
        f"* 判据状态：`{json.dumps(art['adjudication']['by_id'], ensure_ascii=False)}`；"
        f"总裁决 `{art['adjudication']['overall']}`。\n"
        f"* 覆盖率：记录 {cov['n_records']}（有判级者身份 {cov['n_keyed']}，UNKEYED {cov['n_unkeyed']}，"
        f"不插补）、mission {cov['n_missions']}、可配对 {cov['n_pairable_missions']}、"
        f"跨判级者配对 {cov['n_cross_grader_pairs']}、判级者键 {cov['n_graders']}。\n"
        f"* φ_g = {art['variance'].get('phi_g'):.3f}"
        f"（95% CI {art['variance_ci'].get('lo'):.3f}–{art['variance_ci'].get('hi'):.3f}），"
        f"多评 mission = {art['variance'].get('n_multi_graded_missions')} → mission 分量不可识别。\n"
        f"* 最小审数（CI 半宽 ≤0.10）："
        + "; ".join(
            f"α_true={r['alpha_true']:.1f}/m={r['n_raters']}: "
            f"{r.get('min_n_units_measured') or ('外推 ' + str(r.get('min_n_units_extrapolated')))}"
            for r in art["min_rating_table"]["rows"] if r.get("status") != "unreachable")
        + "。\n"
        f"* 复算：`{art['reproduce']['command']}`；测试 `{art['reproduce']['tests']}`。\n"
        "* 纪律：单审判级禁作台账升账依据；WITHHELD ≠ PASS；不对判级者排名或画像。\n"
    )

    return {
        "title": "判级者间一致性门（Krippendorff α / Gwet AC / ICC / φ_g）+ S×E 台账首份可靠度区间 — IN55W3-N07",
        "summary": summary,
        "scenario_key": "platform/grader-reliability-gate",
        "mission_id": 10363,
        "tags": ["inter-rater-reliability", "krippendorff-alpha", "gwet-ac", "icc",
                 "bootstrap-ci", "synthetic-recovery", "decision-gate", "sxe-ledger",
                 "honest-withheld", "TensorPlatform"],
        "markdown": md + appendix,
        "figures": figures,
    }


def main() -> int:
    body = build_body()
    login = json.dumps({"username": USER, "password": PASS}).encode()
    req = urllib.request.Request(f"{API}/auth/login", data=login,
                                 headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]
    post = urllib.request.Request(f"{API}/tensor/reports", data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}"})
    try:
        result = json.loads(urllib.request.urlopen(post, timeout=120).read())
    except Exception as exc:  # 打印服务端消息
        if hasattr(exc, "read"):
            print(exc.read().decode()[:1500], file=sys.stderr)
        raise
    print("report posted:", json.dumps(result, ensure_ascii=False)[:500])
    return 0


if __name__ == "__main__":
    sys.exit(main())