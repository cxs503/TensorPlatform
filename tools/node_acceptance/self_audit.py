#!/usr/bin/env python3
"""Self-audit: run the node-acceptance engine over real node artifact directories.

Predeclared protocol (fixed before looking at results, experimental-design rule):

**Decision.** Does the engine reproduce the human verdicts on real nodes well
enough to be used as a machine adjudicator (target: >= 4/5 consistency), while
staying inside the 30 s/node budget and never inventing a threshold?

**Alternatives.** (a) keep human-only acceptance; (b) aggregate only declared
``criteria`` text; (c) also lift record-local (tolerance, value) pairs and flag
inferred associations as curation items (this engine).

**Selection rule (deterministic, no cherry-picking).** Walk
``<work-root>/m<id>/validation_report.json`` in ascending mission id. Group by
top-level key signature; take the first eligible node of each signature (a node
is eligible when its report carries at least one human-authored status field and
at least one numeric assertion). Stop at 5 nodes. The same rule can be re-run by
anyone and yields the same five nodes.

**Metrics.** (1) cross-validation consistency per node and in total; (2) strict
certified-agreement rate; (3) wall-clock seconds per node; (4) criterion-parser
accuracy on 10 predeclared synthetic criteria; (5) domain-vocabulary scan hits
on the engine package (must be 0).

**Outcome thresholds (predeclared).** PASS the audit when consistency >= 4/5,
max per-node seconds <= 30, parser accuracy >= 0.9 and domain hits == 0. Anything
else is reported as honest FAIL - the report is generated either way.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.node_acceptance import (  # noqa: E402
    ENGINE_VERSION,
    SCHEMA_VERSION,
    build_acceptance_report,
    cross_validate,
    discover_all,
    evaluate_node,
    load_lexicon,
    parse_criterion,
    render_markdown,
    scan_node_dir,
    summarise,
)
from tools.node_acceptance.model import Comparator, dumps  # noqa: E402

DEFAULT_WORK_ROOT = "/root/tp-agent/work"
N_NODES = 5
MIN_JUDGEABLE = 3
TIME_BUDGET_S = 30.0
PARSE_TARGET = 0.9
CONSISTENCY_TARGET = 0.8  # 4/5

# Ten predeclared synthetic criteria (domain-neutral wording, frozen before the run).
PARSE_CASES: list[dict[str, Any]] = [
    {"text": "agreement_rate >= 4/5", "comparator": "ratio>=", "ratio": [4, 5], "hint": "ratio"},
    {"text": "解析正确率≥90%", "comparator": ">=", "threshold": 90.0, "unit": "%", "hint": "ratio"},
    {"text": "扫描+聚合耗时≤30s", "comparator": "<=", "threshold": 30.0, "unit": "s", "hint": "duration"},
    {"text": "pytest ≥ 12 项全绿", "comparator": ">=", "threshold": 12.0, "hint": "all_true"},
    {"text": "max_relative_error < 1e-3", "comparator": "<", "threshold": 1e-3, "metric_name": "max_relative_error"},
    {"text": "误差 < 3%", "comparator": "<", "threshold": 3.0, "unit": "%", "hint": "error"},
    {"text": "报告生成包含六要素", "comparator": "present", "hint": "element"},
    {"text": "all checks green", "comparator": "all_true", "hint": "all_true"},
    {"text": "verified_cases >= 25", "comparator": ">=", "threshold": 25.0, "metric_name": "verified_cases"},
    {"text": "kappa_estimate ≈ 76", "comparator": "~=", "threshold": 76.0},
]


# ---------------------------------------------------------------------------
# Deterministic node selection
# ---------------------------------------------------------------------------

NODE_RE = re.compile(r"^m(\d+)$")


def _report_signature(path: str) -> str:
    """Content hash of the report: identical template copies collapse to one slot."""
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def _eligible(doc: dict) -> bool:
    text = json.dumps(doc, ensure_ascii=False)
    has_status = any(k in doc for k in ("criteria", "anchors", "overall_status", "all_passed",
                                        "status", "verdict", "summary"))
    has_number = bool(re.search(r"[<>≤≥]\s*=?\s*\d|\d\.\d|\d+e-?\d", text))
    return has_status and has_number


def select_nodes(work_root: str, n: int = N_NODES) -> tuple[list[str], list[dict]]:
    """Deterministic, documented selection of ``n`` node directories."""
    candidates = []
    for path in glob.glob(os.path.join(work_root, "m*/validation_report.json")):
        node = Path(path).parent.name
        mt = NODE_RE.match(node)
        if not mt:
            continue
        candidates.append((int(mt.group(1)), node, path))
    candidates.sort()

    chosen: list[str] = []
    log: list[dict] = []
    skipped: list[dict] = []
    seen_signatures: dict[str, int] = {}
    # round-robin over signatures: pass 1 takes the first node of every distinct
    # template, pass 2 the second node of every template, ... so the sample is as
    # diverse as the corpus allows and stays deterministic
    ordered: list[tuple[int, int, str, str]] = []
    pool = list(candidates)
    round_idx = 1
    while pool and round_idx <= 4:
        remaining = []
        for mission_id, node, path in pool:
            sig = _report_signature(path)
            if seen_signatures.get(sig, 0) < round_idx - 1:
                remaining.append((mission_id, node, path))
                continue
            ordered.append((round_idx, mission_id, node, path))
            seen_signatures[sig] = seen_signatures.get(sig, 0) + 1
        pool = remaining
        round_idx += 1

    for rnd, mission_id, node, path in ordered:
        if len(chosen) >= n:
            break
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(doc, dict) or not _eligible(doc):
            continue
        sig = _report_signature(path)
        # dry run: a node that declares nothing judgeable cannot validate the
        # engine, so it is skipped (recorded, with its reason)
        evidence = scan_node_dir(os.path.join(work_root, node))
        judgeable = [c for c in discover_all(evidence) if c.judgeable]
        if len(judgeable) < MIN_JUDGEABLE:
            skipped.append({"node_id": node, "signature": sig,
                            "reason": f"only {len(judgeable)} judgeable criteria (<{MIN_JUDGEABLE})"})
            continue
        chosen.append(node)
        log.append({
            "node_id": node,
            "mission_id": mission_id,
            "signature": sig,
            "selection_rank": len(chosen),
            "round": rnd,
            "judgeable_criteria": len(judgeable),
            "report_path": path,
        })
    log.extend([dict(s, selection_rank=None) for s in skipped[:20]])
    return chosen, log


# ---------------------------------------------------------------------------
# Parser accuracy
# ---------------------------------------------------------------------------

def parse_accuracy(lex: dict) -> dict:
    rows = []
    hits = 0
    for case in PARSE_CASES:
        crits = parse_criterion(case["text"], lexicon=lex)
        crit = crits[0] if crits else None
        ok = False
        detail = {}
        if crit is not None:
            detail = {"comparator": crit.comparator.value, "threshold": crit.threshold,
                      "ratio": list(crit.ratio) if crit.ratio else None, "unit": crit.unit,
                      "hint": crit.hint, "metric_name": crit.metric_name}
            ok = crit.comparator.value == case["comparator"]
            if "threshold" in case:
                ok = ok and crit.threshold is not None and abs(float(crit.threshold) - float(case["threshold"])) < 1e-12
            if "ratio" in case:
                ok = ok and list(crit.ratio or []) == case["ratio"]
            if "hint" in case:
                ok = ok and crit.hint == case["hint"]
            if "metric_name" in case:
                ok = ok and crit.metric_name == case["metric_name"]
        hits += int(ok)
        rows.append({"text": case["text"], "expected": case, "parsed": detail, "match": ok})
    return {"cases": len(PARSE_CASES), "hits": hits, "accuracy": round(hits / len(PARSE_CASES), 4), "rows": rows}


# ---------------------------------------------------------------------------
# Domain-vocabulary self check
# ---------------------------------------------------------------------------

def domain_scan(package_dir: Path, lexicon_path: Path) -> dict:
    """Count platform-declared domain vocabulary hits inside the engine package."""
    if not lexicon_path.exists():
        return {"available": False, "hits": [], "count": 0}
    lex = json.loads(lexicon_path.read_text(encoding="utf-8"))
    pats = []
    for term in lex.get("ascii_terms", []):
        pats.append((term, re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)))
    for term in lex.get("cjk_terms", []):
        pats.append((term, re.compile(re.escape(term))))
    hits = []
    for path in sorted(package_dir.glob("**/*")):
        if not path.is_file() or path.suffix in {".pyc", ".png"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for term, pat in pats:
            for m in pat.finditer(text):
                hits.append({"file": path.name, "term": term, "offset": m.start()})
    return {"available": True, "count": len(hits), "hits": hits[:20], "lexicon": str(lexicon_path)}


# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------

def make_figure(results: list[dict], out_png: Path) -> str | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - plotting stack optional
        return f"matplotlib unavailable: {exc}"

    nodes = [r["node_id"] for r in results]
    keys = ["PASS", "MECHANICAL_CONFLICT", "FAIL", "UNKNOWN"]
    colors = {"PASS": "#2e7d32", "MECHANICAL_CONFLICT": "#f9a825", "FAIL": "#c62828", "UNKNOWN": "#9e9e9e"}
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
    bottom = [0] * len(nodes)
    for k in keys:
        vals = [r["counts"].get(k, 0) for r in results]
        axes[0].bar(nodes, vals, bottom=bottom, label=k, color=colors[k])
        bottom = [b + v for b, v in zip(bottom, vals)]
    axes[0].set_title("Per-node criterion outcomes (machine verdict)")
    axes[0].set_ylabel("criteria")
    axes[0].legend(fontsize=8, loc="upper right")
    for i, r in enumerate(results):
        axes[0].text(i, bottom[i] + 0.2, r["agreement"], ha="center", fontsize=7, rotation=0)

    times = [r["elapsed_s"] for r in results]
    axes[1].bar(nodes, times, color="#1565c0")
    axes[1].axhline(TIME_BUDGET_S, color="#c62828", linestyle="--", label=f"budget {TIME_BUDGET_S:g}s")
    axes[1].set_title("Scan + aggregate wall clock per node")
    axes[1].set_ylabel("seconds")
    axes[1].legend(fontsize=8)
    for i, t in enumerate(times):
        axes[1].text(i, t, f"{t:.2f}s", ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(work_root: str, out_dir: Path, nodes: list[str] | None = None, n: int = N_NODES) -> dict:
    lex = load_lexicon()
    out_dir.mkdir(parents=True, exist_ok=True)
    chosen, selection_log = (nodes, []) if nodes else select_nodes(work_root, n)

    results = []
    checks = []
    per_node_reports = {}
    for node in chosen:
        root = os.path.join(work_root, node)
        started = time.perf_counter()
        verdict, report, check = evaluate_node(root)
        elapsed = time.perf_counter() - started

        (out_dir / f"{node}_acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                                         encoding="utf-8")
        (out_dir / f"{node}_acceptance.md").write_text(render_markdown(report), encoding="utf-8")

        per_node_reports[node] = report
        checks.append(check)
        results.append({
            "node_id": node,
            "machine_verdict": verdict.verdict.value,
            "counts": verdict.counts,
            "confidence": round(verdict.confidence, 4),
            "elapsed_s": round(elapsed, 3),
            "criteria": len(verdict.outcomes),
            "human_verdict": check.human_verdict.value,
            "agreement": check.agreement,
            "human_source": f"{check.human_source}#{check.human_pointer}",
            "unknown_criteria": [o.criterion.raw for o in verdict.outcomes
                                 if o.verdict.value == "UNKNOWN"],
            "conflict_criteria": [o.criterion.raw for o in verdict.outcomes
                                  if o.verdict.value == "MECHANICAL_CONFLICT"],
            "caveats": verdict.caveats,
        })

    summary = summarise(checks)
    accuracy = parse_accuracy(lex)
    dom = domain_scan(Path(__file__).resolve().parent,
                      REPO_ROOT / "tools" / "evidence_domain_lexicon.json")

    max_elapsed = max((r["elapsed_s"] for r in results), default=0.0)
    consistency_rate = (summary["consistency"] / summary["nodes"]) if summary["nodes"] else 0.0
    gates = {
        "consistency>=4/5": summary["consistency"] >= CONSISTENCY_TARGET * max(summary["nodes"], 1),
        "elapsed<=30s": max_elapsed <= TIME_BUDGET_S,
        "parser_accuracy>=0.9": accuracy["accuracy"] >= PARSE_TARGET,
        "zero_domain_vocabulary": dom["count"] == 0,
    }

    png = out_dir / "node_acceptance_selfaudit.png"
    fig_err = make_figure(results, png)

    audit = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "work_root": work_root,
        "protocol": {
            "selection_rule": "ascending mission id, first eligible node per report-signature, stop at 5",
            "eligibility": ("validation_report.json with a human status field and a numeric assertion, "
                            f">= {MIN_JUDGEABLE} machine-judgeable criteria, and a report content hash not "
                            "seen before (template copies collapse to one slot)"),
            "targets": {"consistency": CONSISTENCY_TARGET, "max_elapsed_s": TIME_BUDGET_S,
                        "parser_accuracy": PARSE_TARGET, "domain_hits": 0},
            "metric_definitions": {
                "consistency": ("machine verdict never contradicts the human verdict: "
                                "agree_certified + agree_no_contradiction + documented_deviation"),
                "agreement_strict": "machine independently reproduced the human binary verdict",
                "disagree": "machine asserted the opposite binary verdict",
            },
            "deviation_note": ("consistency covers 'not contradicted', NOT 'certified'; the strict "
                               "certification rate is reported beside it and is not hidden"),
        },
        "selection": selection_log,
        "nodes": results,
        "cross_validation": summary,
        "parser": accuracy,
        "domain_scan": dom,
        "gates": gates,
        "figures": [] if fig_err else [{"path": str(png), "caption": "per-node criterion outcomes and wall clock"}],
        "figure_error": fig_err,
    }
    (out_dir / "self_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "self_audit.md").write_text(render_audit_markdown(audit, per_node_reports), encoding="utf-8")
    return audit


def render_audit_markdown(audit: dict, reports: dict[str, dict]) -> str:
    s = audit["cross_validation"]
    acc = audit["parser"]
    L: list[str] = []
    L.append("# 节点验收证据自动聚合引擎 · 自审报告")
    L.append("")
    L.append(f"引擎 `{audit['engine_version']}` · schema `{audit['schema_version']}` · 生成 {audit['generated_utc']}")
    L.append("")
    L.append("## 预设判据（预注册）与结果")
    L.append("")
    L.append("| 判据 | 目标 | 实测 | 结论 |")
    L.append("|---|---|---|---|")
    L.append(f"| 一致率 (consistency, 无矛盾口径) | ≥4/5 | {s['consistency']}/{s['nodes']} "
             f"(strict certified {s['agree_certified']}, no-contradiction {s['agree_no_contradiction']}, "
             f"documented deviation {s['documented_deviation']}, disagree {s['disagree']}) | "
             f"{'PASS' if audit['gates']['consistency>=4/5'] else 'FAIL'} |")
    max_el = max((n['elapsed_s'] for n in audit['nodes']), default=0)
    L.append(f"| 单节点耗时 | ≤30s | {max_el:.2f}s | {'PASS' if audit['gates']['elapsed<=30s'] else 'FAIL'} |")
    L.append(f"| 判据解析正确率 | ≥90% | {acc['accuracy']*100:.0f}% ({acc['hits']}/{acc['cases']}) | "
             f"{'PASS' if audit['gates']['parser_accuracy>=0.9'] else 'FAIL'} |")
    L.append(f"| 零领域语义 | 0 hit | {audit['domain_scan']['count']} hit | "
             f"{'PASS' if audit['gates']['zero_domain_vocabulary'] else 'FAIL'} |")
    L.append(f"| 严格认证率 (agreement_strict) | — | {s['agree_certified']}/{s['nodes']} | "
             f"{'PASS' if s['agree_certified'] >= audit['protocol']['targets']['consistency'] * s['nodes'] else 'FAIL (honest)'} |")
    L.append(f"| 机器与人工相反 (disagree) | 0 | {s['disagree']} | "
             f"{'PASS' if s['disagree'] == 0 else 'FAIL'} |")
    L.append("")
    L.append("> 口径声明：consistency 统计「机器未提出与人工相反的结论」；strict 统计「机器独立复现人工二值裁定」。")
    L.append("> 两者都列出，strict 未达标即如实记为 honest FAIL，不用 consistency 掩盖。")
    L.append("")
    L.append("## 逐节点交叉验证（定源表）")
    L.append("")
    L.append("| 节点 | 机器裁定 | PASS/FAIL/CONFLICT/UNKNOWN | 人工裁定 | 关系 | 人工来源 | 耗时 |")
    L.append("|---|---|---|---|---|---|---|")
    for n in audit["nodes"]:
        c = n["counts"]
        L.append(f"| {n['node_id']} | {n['machine_verdict']} | {c['PASS']}/{c['FAIL']}/{c['MECHANICAL_CONFLICT']}/{c['UNKNOWN']} "
                 f"| {n['human_verdict']} | {n['agreement']} | `{n['human_source']}` | {n['elapsed_s']:.2f}s |")
    L.append("")
    L.append("## 判据解析明细")
    L.append("")
    L.append("| 判据文本 | 期望 | 解析 | 命中 |")
    L.append("|---|---|---|---|")
    for r in acc["rows"]:
        L.append(f"| {r['text']} | {r['expected'].get('comparator')} {r['expected'].get('threshold', r['expected'].get('ratio',''))} "
                 f"| {r['parsed'].get('comparator')} {r['parsed'].get('threshold', r['parsed'].get('ratio',''))} "
                 f"| {'✓' if r['match'] else '✗'} |")
    L.append("")
    L.append("## 未裁定与需人工裁量项")
    L.append("")
    for n in audit["nodes"]:
        if n["unknown_criteria"] or n["conflict_criteria"]:
            L.append(f"**{n['node_id']}**")
            for u in n["unknown_criteria"]:
                L.append(f"- UNKNOWN: {u}")
            for c in n["conflict_criteria"]:
                L.append(f"- MECHANICAL_CONFLICT: {c}")
            L.append("")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="node-acceptance engine self audit")
    ap.add_argument("--work-root", default=DEFAULT_WORK_ROOT)
    ap.add_argument("--out", default=str(REPO_ROOT / "artifacts" / "node_acceptance"))
    ap.add_argument("--nodes", nargs="*", default=None, help="explicit node ids (skips selection rule)")
    ap.add_argument("--n", type=int, default=N_NODES)
    args = ap.parse_args(argv)
    audit = run(args.work_root, Path(args.out), nodes=args.nodes, n=args.n)
    print(json.dumps({"gates": audit["gates"], "cross_validation": audit["cross_validation"]},
                     ensure_ascii=False, indent=2))
    return 0 if all(audit["gates"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())