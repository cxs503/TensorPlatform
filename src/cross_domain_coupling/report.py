"""Six-element paper-grade markdown rendering for the coupling gate.

Section skeleton is fixed by platform report discipline (background / method /
results / figures / caveats / error verdict); the *content* is injected as a
``narrative`` mapping so that no domain wording lives in this module.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

SECTION_TITLES = (
    ("background", "① 案例介绍（物理背景 / 几何 / 参数表）"),
    ("method", "② 怎么做的（方法 / 流程 / 关键设置 / 工具链）"),
    ("results", "③ 计算结果（数字表，程序化注入）"),
    ("figures", "④ 结果图"),
    ("caveats", "⑤ 注意点（踩过的坑 / 局限 / 适用范围）"),
    ("verdict", "⑥ 误差判定"),
)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        if v == 0:
            return "0"
        if abs(v) < 1e-3 or abs(v) >= 1e5:
            return f"{v:.4e}"
        return f"{v:.6g}"
    return str(v)


def _table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = ["| " + " | ".join(_fmt(r.get(c, "")) for c in columns) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def results_tables(report: Mapping[str, Any]) -> str:
    blocks: List[str] = []
    blocks.append("### 判据总表（程序化注入，零手抄）\n")
    rows = []
    for c in report["criteria"]:
        rows.append(
            {
                "criterion": c["criterion"],
                "tolerance_pct": c.get("tolerance_pct", ""),
                "observed_max_pct": c.get("max_error_pct", c.get("coverage_pct", "")),
                "margin_pct": c.get("margin_pct", ""),
                "verdict": c["verdict"],
            }
        )
    blocks.append(_table(rows, ["criterion", "tolerance_pct", "observed_max_pct", "margin_pct", "verdict"]))

    for c in report["criteria"]:
        if not c["criterion"].startswith("coupling:"):
            continue
        blocks.append(f"\n### {c['criterion']} — {c.get('title','')}\n")
        rows = []
        for idx, (p, i, e) in enumerate(
            zip(c.get("primary", []), c.get("independent", []), c.get("errors_pct", []))
        ):
            rows.append({"i": idx, "primary": p, "independent": i, "err_pct": e, "tol_pct": c["tolerance_pct"]})
        blocks.append(_table(rows, ["i", "primary", "independent", "err_pct", "tol_pct"]))
    e2e = next((c for c in report["criteria"] if c["criterion"] == "end_to_end_regression"), None)
    if e2e and "rows" in e2e:
        blocks.append("\n### end_to_end_regression — 全链路 vs 基线\n")
        blocks.append(_table(e2e["rows"], ["metric", "pipeline", "baseline", "error_pct"]))
    cov = next((c for c in report["criteria"] if c["criterion"] == "interface_contract_coverage"), None)
    if cov:
        blocks.append("\n### interface_contract_coverage — 契约覆盖率\n")
        blocks.append(
            _table(cov["items"], ["coupling", "quantity", "unit", "consumer_unit", "unit_conversion_factor", "covered"])
        )
    return "\n".join(blocks)


def verdict_block(report: Mapping[str, Any], threshold_pct: float = 3.0) -> str:
    lines = ["| criterion | max error % | tolerance % | < 3% ? | verdict |", "|---|---|---|---|---|"]
    for c in report["criteria"]:
        err = c.get("max_error_pct")
        if err is None:
            continue
        lines.append(
            f"| {c['criterion']} | {_fmt(err)} | {_fmt(c['tolerance_pct'])} | "
            f"{'YES' if err < threshold_pct else 'NO'} | {c['verdict']} |"
        )
    lines.append("")
    lines.append(f"- overall_verdict: **{report['overall_verdict']}**")
    lines.append(f"- report_sha256: `{report['report_sha256']}`")
    return "\n".join(lines)


def render_markdown(report: Mapping[str, Any], narrative: Mapping[str, Any], figures: Sequence[Mapping[str, str]] = ()) -> str:
    out: List[str] = []
    title = narrative.get("title", report["pack"]["name"])
    out.append(f"# {title}\n")
    out.append(
        f"**Case pack**: `{report['pack']['name']}` v{report['pack']['version']} · "
        f"spec sha256 `{report['pack']['spec_sha256'][:16]}…` · "
        f"evaluator sha256 `{report['pack']['evaluator_module_sha256'][:16]}…`\n"
    )
    out.append(f"**Overall verdict**: `{report['overall_verdict']}`\n")
    for key, heading in SECTION_TITLES:
        out.append(f"## {heading}\n")
        if key == "results":
            out.append(results_tables(report))
        elif key == "figures":
            if figures:
                for fig in figures:
                    out.append(f"![{fig['name']}]({fig['path']})\n")
                    out.append(f"*{fig.get('caption','')}*\n")
            else:
                out.append(narrative.get("figures_note", "（无图：本节点为纯机制/后端改动，未生成场量图）"))
        elif key == "verdict":
            out.append(narrative.get("verdict", ""))
            out.append("")
            out.append(verdict_block(report))
        else:
            out.append(str(narrative.get(key, "")))
            out.append("")
    return "\n".join(out)


def write_markdown(report: Mapping[str, Any], narrative: Mapping[str, Any], out_path: str | Path,
                   figures: Sequence[Mapping[str, str]] = ()) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_markdown(report, narrative, figures), encoding="utf-8")
    return p
