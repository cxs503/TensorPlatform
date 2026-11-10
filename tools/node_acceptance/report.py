"""Report generator: machine verdict -> six-element acceptance report (JSON + MD).

The six elements are the platform report contract: case introduction, method,
results, figures, caveats, error judgement.  Every number in the report is
copied programmatically from :class:`NodeVerdict` / :class:`NodeEvidence` - the
generator never accepts hand-typed figures, and each row keeps the artifact path
plus pointer it came from.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable, Sequence

from .criteria import load_lexicon
from .model import (
    Comparator,
    CriterionOutcome,
    ENGINE_VERSION,
    SCHEMA_VERSION,
    NodeEvidence,
    NodeVerdict,
    Verdict,
    jsonable,
)

SIX_ELEMENTS = ("case_intro", "method", "results", "figures", "caveats", "error_judgement")


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value != 0 and (abs(value) < 1e-3 or abs(value) >= 1e6):
            return f"{value:.6e}"
        return f"{value:.6g}"
    return str(value)


def _is_error_criterion(o: CriterionOutcome, lex: dict) -> bool:
    if o.criterion.hint == "error":
        return True
    if o.criterion.unit == "%" and o.criterion.hint in (None, "error"):
        return True
    return o.criterion.metric_name is not None and any(
        w.lower() in o.criterion.metric_name.lower() for w in lex["metric_hints"]["error"]
    )


def build_acceptance_report(
    verdict: NodeVerdict,
    lex: dict | None = None,
    extra_elements: Sequence[str] | None = None,
) -> dict:
    """Build the six-element acceptance report for one node."""
    lex = lex or load_lexicon()
    ev: NodeEvidence | None = verdict.evidence
    reference = float(lex.get("precision_reference_percent", 3.0))

    roles: dict[str, int] = {}
    if ev:
        for a in ev.artifacts:
            roles[a.role] = roles.get(a.role, 0) + 1

    # ---- 1. case introduction -------------------------------------
    case_intro = {
        "node_id": verdict.node_id,
        "artifact_root": ev.root if ev else None,
        "artifact_inventory": roles,
        "artifact_count": len(ev.artifacts) if ev else 0,
        "metric_leaves": ev.metric_count if ev else 0,
        "criteria_count": len(verdict.outcomes),
        "criteria_sources": sorted({o.criterion.source for o in verdict.outcomes if o.criterion.source}),
        "figure_artifacts": (ev.derived.get("figure_count") if ev else 0),
        "verified_case_count": (ev.derived.get("verified_case_count") if ev else 0),
    }

    # ---- 2. method -------------------------------------------------
    method = {
        "engine_version": ENGINE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "lexicon_schema": lex.get("schema_version"),
        "pipeline": [
            "scan_node_dir: declared globs + artifact roots, sha256 per artifact",
            "flatten_metrics: numeric/bool/status leaves with dotted pointers",
            "derive: max_error, max_duration, test suites, pass flags, verified cases",
            "discover_criteria: criteria declared by the artifacts themselves",
            "aggregate: metric_name -> hint -> declared_tolerance resolution ladder",
            "cross_validate: machine verdict vs human-authored status (separate module)",
        ],
        "resolution_ladder": [
            "metric_name (score 1.00/0.95/0.80 by key match strength)",
            "lexicon hint on derived aggregate (0.50-0.85)",
            "declared tolerance in the same record (0.75, flagged)",
            "unresolved -> UNKNOWN (never guessed)",
        ],
        "verdict_rule": "FAIL sticky; PASS only if every criterion PASS; unknown evidence -> INCOMPLETE",
        "scan_limits": lex.get("scan_limits"),
        "precision_reference_percent": reference,
        "elapsed_s": round(verdict.elapsed_s, 3),
        "evidence_digest": ev.digest() if ev else None,
    }

    # ---- 3. results ------------------------------------------------
    results = []
    for o in verdict.outcomes:
        results.append({
            "criterion": o.criterion.raw,
            "comparator": o.criterion.comparator.value,
            "threshold": o.criterion.threshold,
            "ratio": list(o.criterion.ratio) if o.criterion.ratio else None,
            "unit": o.criterion.unit,
            "observed": o.observation.value,
            "verdict": o.verdict.value,
            "confidence": round(o.confidence, 3),
            "source": o.observation.source or o.criterion.source,
            "pointer": ", ".join(o.observation.matched_keys[:3]),
            "note": o.note,
        })
    results_table = {
        "counts": verdict.counts,
        "node_verdict": verdict.verdict.value,
        "rows": results,
    }

    # ---- 4. figures ------------------------------------------------
    figures = []
    if ev:
        for a in ev.artifacts:
            if a.role == "figure":
                figures.append({"path": a.path, "sha256": a.sha256,
                                "caption": f"artifact figure from node {verdict.node_id}"})
    if extra_elements:
        for p in extra_elements:
            figures.append({"path": p, "sha256": None, "caption": "engine-generated verification figure"})
    figures_block = {
        "figure_count": len(figures),
        "figures": figures[:50],
        "note": ("figures are referenced by path+sha256; none are fabricated by the engine"
                 if figures else "no figure artifact in this node directory"),
    }

    # ---- 5. caveats ------------------------------------------------
    caveats = list(verdict.caveats)
    if ev and ev.metrics and not verdict.outcomes:
        caveats.append("artifacts present but no criterion declared: nothing to judge")
    unresolvable = [o for o in verdict.outcomes if o.verdict is Verdict.UNKNOWN]
    if unresolvable:
        caveats.append(f"{len(unresolvable)} criterion/criteria remain UNKNOWN; the node cannot be called PASS")
    caveats_block = {"caveats": caveats or ["none"], "unknown_criteria": [o.criterion.raw for o in unresolvable]}

    # ---- 6. error judgement ---------------------------------------
    err_rows = []
    for o in verdict.outcomes:
        if not _is_error_criterion(o, lex):
            continue
        obs = o.observation.value
        numeric = obs if isinstance(obs, (int, float)) and not isinstance(obs, bool) else None
        pct = None
        if numeric is not None:
            pct = abs(float(numeric)) if o.criterion.unit == "%" else abs(float(numeric)) * 100.0
        err_rows.append({
            "criterion": o.criterion.raw,
            "observed": obs,
            "observed_percent": None if pct is None else round(pct, 6),
            "threshold": o.criterion.threshold,
            "unit": o.criterion.unit,
            "verdict": o.verdict.value,
            "within_reference_percent": None if pct is None else bool(pct <= reference),
            "source": o.observation.source,
        })
    if err_rows:
        judged = [r for r in err_rows if r["within_reference_percent"] is not None]
        statement = (
            f"{len(judged)}/{len(err_rows)} error-type criteria carry a numeric observation; "
            f"platform reference band is {reference}%"
        )
    else:
        statement = ("no error-type criterion in this node: the 3% reference band cannot be applied "
                     "(declared as not-applicable rather than assumed satisfied)")
    error_block = {
        "reference_percent": reference,
        "answered": bool(err_rows),
        "rows": err_rows,
        "statement": statement,
    }

    report = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "node_id": verdict.node_id,
        "verdict": verdict.verdict.value,
        "confidence": round(verdict.confidence, 4),
        "counts": verdict.counts,
        "six_elements_present": list(SIX_ELEMENTS),
        "case_intro": case_intro,
        "method": method,
        "results": results_table,
        "figures": figures_block,
        "caveats": caveats_block,
        "error_judgement": error_block,
        "audit_chain": {
            "evidence_digest": ev.digest() if ev else None,
            "artifacts": [a.as_dict() for a in (ev.artifacts if ev else [])][:200],
            "scanner_warnings": ev.warnings if ev else [],
        },
    }
    return jsonable(report)


def render_markdown(report: dict) -> str:
    """Render the six-element report as Markdown."""
    ci, me, re_, fg, cv, ej = (report["case_intro"], report["method"], report["results"],
                               report["figures"], report["caveats"], report["error_judgement"])
    L: list[str] = []
    L.append(f"# 节点验收证据报告 · {report['node_id']}")
    L.append("")
    L.append(f"**机器裁定**: `{report['verdict']}` · 判据 {re_['counts']['total']} 条 "
             f"(PASS {re_['counts']['PASS']} / FAIL {re_['counts']['FAIL']} / UNKNOWN {re_['counts']['UNKNOWN']}) · "
             f"置信度 {report['confidence']}")
    L.append("")
    L.append("## 1. 案例介绍")
    L.append("")
    L.append(f"- 节点工件目录: `{ci['artifact_root']}`")
    L.append(f"- 工件清单: {ci['artifact_inventory']} (共 {ci['artifact_count']} 个文件)")
    L.append(f"- 指标叶子数: {ci['metric_leaves']} · 判据来源: {ci['criteria_sources']}")
    L.append(f"- 图件工件: {ci['figure_artifacts']} · 已验证案例目录: {ci['verified_case_count']}")
    L.append("")
    L.append("## 2. 方法")
    L.append("")
    for step in me["pipeline"]:
        L.append(f"1. {step}")
    L.append("")
    L.append(f"判据→指标解析阶梯: {' → '.join(me['resolution_ladder'])}")
    L.append("")
    L.append(f"裁定规则: {me['verdict_rule']}；证据摘要 `{me['evidence_digest']}`；耗时 {me['elapsed_s']}s")
    L.append("")
    L.append("## 3. 结果")
    L.append("")
    L.append("| 判据 | 比较 | 阈值 | 实测 | 裁定 | 来源指针 |")
    L.append("|---|---|---|---|---|---|")
    for r in re_["rows"]:
        thr = r["ratio"] and f"{r['ratio'][0]}/{r['ratio'][1]}" or _fmt(r["threshold"])
        L.append(f"| {r['criterion']} | {r['comparator']} | {thr} | {_fmt(r['observed'])} | "
                 f"{r['verdict']} | `{r['source']}` {r['pointer']} |")
    L.append("")
    L.append("## 4. 图")
    L.append("")
    if fg["figures"]:
        for f in fg["figures"]:
            L.append(f"- `{f['path']}` — {f['caption']}")
    else:
        L.append(f"- {fg['note']}")
    L.append("")
    L.append("## 5. 注意点")
    L.append("")
    for c in cv["caveats"]:
        L.append(f"- {c}")
    L.append("")
    L.append("## 6. 误差判定")
    L.append("")
    L.append(f"{ej['statement']}")
    L.append("")
    if ej["rows"]:
        L.append("| 判据 | 实测 | 实测(%) | 阈值 | 是否<参考带 | 来源 |")
        L.append("|---|---|---|---|---|---|")
        for r in ej["rows"]:
            L.append(f"| {r['criterion']} | {_fmt(r['observed'])} | {_fmt(r['observed_percent'])} | "
                     f"{_fmt(r['threshold'])} | {_fmt(r['within_reference_percent'])} | `{r['source']}` |")
    return "\n".join(L) + "\n"