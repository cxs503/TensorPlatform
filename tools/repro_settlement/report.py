"""Markdown 报告渲染：逐条归因矩阵与门控数字全部由报告 JSON 程序化注入（零手抄）。"""
from __future__ import annotations

from typing import Any, Mapping

_LABEL_ZH = {"a_resolution": "a 分辨率", "b_solver_version": "b 求解器版本/参数",
             "c_seed": "c 种子/非确定性", "d_baseline_version": "d 基准数据版本",
             "unattributed": "未归因"}


def _fmt(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.4f}"
    return str(x)


def render_markdown(rep: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# 复证缺口清偿报告 — `{rep.get('corpus_id')}`\n")
    lines.append("> 本文件由 `tools.repro_settlement.report` 从 settlement JSON 程序化生成，"
                 "所有数字零手抄。\n")
    lines.append("## 1. 复现率\n")
    lines.append("| 指标 | 清偿前 | 清偿后 |")
    lines.append("|---|---|---|")
    lines.append(f"| repro_rate | {_fmt(rep.get('repro_rate_before'))} | "
                 f"{_fmt(rep.get('repro_rate_after'))} |")
    lines.append(f"| 案数 | {rep.get('n_cases')} | {rep.get('n_cases')} |")
    lines.append(f"| 状态分布 | {rep.get('counts_before')} | {rep.get('counts_after')} |\n")
    lines.append("## 2. 门控 C1..C7\n")
    lines.append("| 门 | 通过 | 关键数字 |")
    lines.append("|---|---|---|")
    for k, v in rep.get("gates", {}).items():
        nums = {kk: vv for kk, vv in v.items() if kk != "ok"}
        lines.append(f"| {k} | {'✅' if v.get('ok') else '❌'} | `{nums}` |")
    lines.append(f"\n**全部通过**: `{rep.get('all_gates_pass')}`；"
                 f"幂等指纹 `digest = {rep.get('digest')}`\n")
    lines.append("## 3. 逐条归因矩阵（C7）\n")
    lines.append("| case | 清偿前 | 判决 | 归因 | 置信度 | 锁 sha256(12) | Δrel | tol | 处理建议 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for c in rep.get("cases", []):
        lines.append("| {cid} | {b} | {v} | {lab} | {conf} | {lk} | {d} | {t} | {fix} |".format(
            cid=c.get("case_id"), b=c.get("status_before"), v=c.get("verdict"),
            lab=_LABEL_ZH.get(c.get("label"), "—"), conf=_fmt(c.get("confidence")),
            lk=(str(c.get("lock_sha256"))[:12] if c.get("lock_sha256") else "—"),
            d=_fmt(c.get("delta_rel")), t=_fmt(c.get("tolerance")),
            fix=(c.get("fix") or "—")))
    lines.append("")
    lines.append("## 4. 出处与局限\n")
    lines.append(f"- corpus 指纹: `{rep.get('corpus_sha256')}`")
    lines.append(f"- corpus 文件 sha256: `{rep.get('corpus_file_sha256')}`")
    lines.append(f"- 环境锁数量: `{len(rep.get('environment_locks') or {})}`")
    lines.append("- 结论适用范围：判决与门控只对本 corpus 的观测记录成立；"
                 "若 corpus 为重建（provenance.source≠live_*），数字不代表上游原始账本。")
    return "\n".join(lines) + "\n"
