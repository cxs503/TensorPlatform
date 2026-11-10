#!/usr/bin/env python3
"""Acceptance runner for p262w1-N07 (capability ledger auto-audit).

Rebuilds every acceptance number from scratch inside
``artifacts/capability_auto_audit/``:

* F1 -- five synthetic evidence chains (three complete, two incomplete) run
  through the validator, verdict matrix dumped
* F2 -- promotion latency of one complete chain
* F3 -- demotion latency after the verified artifact is deleted
* F4 -- ten mixed operations -> ten sha256-chained audit entries, chain re-verified
* F5 -- zero-domain-semantics scan of the delivered modules
* F6 -- pytest count of the delivered test package

Usage::

    python3 -m tools.capability_auto_audit.n07_acceptance

The synthetic demo capabilities are named ``demo.case_*`` on purpose: they are
mechanism fixtures, not claims about any physical capability.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .append_only_log import AppendOnlyAuditLog
from .auto_audit_engine import AutoAuditEngine
from .evidence_chain import (
    ChainSpec,
    EvidenceChainValidator,
    load_evidence,
    sha256_file,
    sha256_json,
)
from .level_machine import decide, transition_table

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "config" / "capability_chain_spec.json"


def load_spec() -> ChainSpec:
    """The deployment spec (config file) -- falls back to dataclass defaults."""
    return ChainSpec.from_file(SPEC_PATH) if SPEC_PATH.is_file() else ChainSpec()
OUT = ROOT / "artifacts" / "capability_auto_audit"
SIX = ("case", "method", "results", "figures", "notes", "error_judgement")

DELIVERED_MODULES = (
    "tools/capability_auto_audit/evidence_chain.py",
    "tools/capability_auto_audit/level_machine.py",
    "tools/capability_auto_audit/append_only_log.py",
    "tools/capability_auto_audit/auto_audit_engine.py",
    "tools/capability_auto_audit/api.py",
)


# --------------------------------------------------------------------------- #
# synthetic evidence bundles
# --------------------------------------------------------------------------- #
def build_case(base: Path, cap_id: str, *, pytest_kind: str = "junit", passed: int = 5,
               failed: int = 0, elements: tuple = SIX, measured: float = 1.02,
               reference: float = 1.0, claimed=None, independent: bool = True,
               verdict_word: str = "PASS") -> Path:
    case_dir = base / cap_id
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "artifact.json").write_text(json.dumps({
        "case_id": f"case::{cap_id}", "inputs_sha256": sha256_json({"case": cap_id, "v": 1}),
    }), encoding="utf-8")

    if pytest_kind == "junit":
        cases = [f'<testcase name="t{i}"/>' for i in range(passed)]
        cases += [f'<testcase name="f{i}"><failure/></testcase>' for i in range(failed)]
        (case_dir / "pytest.xml").write_text(
            f'<?xml version="1.0"?><testsuites><testsuite tests="{len(cases)}">'
            + "".join(cases) + "</testsuite></testsuites>", encoding="utf-8")
    elif pytest_kind == "json":
        (case_dir / "pytest.json").write_text(json.dumps(
            {"passed": passed, "failed": failed, "total": passed + failed}), encoding="utf-8")

    if elements is not None:
        (case_dir / "report.json").write_text(
            json.dumps({k: f"content::{k}" for k in elements}), encoding="utf-8")

    rel = abs(measured - reference) / abs(reference)
    (case_dir / "metrics.json").write_text(json.dumps({
        "measured": measured, "reference": reference,
        "rel_error": rel if claimed is None else claimed,
    }), encoding="utf-8")
    (case_dir / "review.json").write_text(json.dumps({
        "reviewer": "independent-reviewer-1", "verdict": verdict_word,
        "independent": independent,
    }), encoding="utf-8")
    return case_dir


F1_CASES: List[Tuple[str, Dict[str, Any], bool, str | None]] = [
    ("demo.case_a", dict(pytest_kind="junit", passed=6, measured=1.01), True, None),
    ("demo.case_b", dict(pytest_kind="json", passed=4, measured=0.99), True, None),
    ("demo.case_c", dict(pytest_kind="junit", passed=5, reference=1.0, measured=1.02), True, None),
    ("demo.case_d", dict(pytest_kind="none"), False, "pytest"),
    ("demo.case_e", dict(elements=("case", "method")), False, "report"),
]


# --------------------------------------------------------------------------- #
# criteria
# --------------------------------------------------------------------------- #
def criterion_f1(demo_dir: Path) -> Dict[str, Any]:
    validator = EvidenceChainValidator(load_spec())
    rows = []
    correct = 0
    for cap_id, kwargs, expected_complete, expected_link in F1_CASES:
        build_case(demo_dir, cap_id, **kwargs)
        verdict = validator.validate(load_evidence(demo_dir, cap_id))
        link_ok = (expected_link in verdict.missing) if expected_link else not verdict.missing
        ok = (verdict.complete == expected_complete) and link_ok
        correct += int(ok)
        rows.append({
            "capability_id": cap_id,
            "expected_complete": expected_complete,
            "predicted_complete": verdict.complete,
            "expected_failing_link": expected_link,
            "predicted_missing": verdict.missing,
            "reason_code": verdict.reason_code,
            "failure_kind": verdict.failure_kind,
            "chain_digest": verdict.chain_digest,
            "checks": {c.name: c.ok for c in verdict.checks},
            "correct": ok,
        })
    payload = {
        "criterion": "F1",
        "cases": len(F1_CASES),
        "correct": correct,
        "accuracy": correct / len(F1_CASES),
        "complete_cases": sum(1 for r in rows if r["expected_complete"]),
        "incomplete_cases": sum(1 for r in rows if not r["expected_complete"]),
        "rows": rows,
        "pass": correct == len(F1_CASES),
    }
    return payload


def _fresh_engine(state_dir: Path, demo_dir: Path) -> AutoAuditEngine:
    if state_dir.exists():
        shutil.rmtree(state_dir)
    return AutoAuditEngine(verified_dir=demo_dir,
                           state_path=state_dir / "ledger_state.json",
                           log_path=state_dir / "audit_log.jsonl",
                           spec=load_spec())


def criterion_f2(state_dir: Path, demo_dir: Path) -> Dict[str, Any]:
    engine = _fresh_engine(state_dir, demo_dir)
    cap = "demo.latency_up"
    start = time.perf_counter()
    build_case(demo_dir, cap, passed=5, measured=1.01)
    engine.watch_once()
    elapsed = time.perf_counter() - start
    outcome = engine.outcomes[-1]
    return {
        "criterion": "F2",
        "capability_id": cap,
        "from_level": outcome.from_level,
        "to_level": outcome.to_level,
        "action": outcome.action,
        "elapsed_s": round(elapsed, 6),
        "deadline_s": 5.0,
        "pass": outcome.to_level == "REAL" and elapsed <= 5.0,
    }


def criterion_f3(state_dir: Path, demo_dir: Path) -> Dict[str, Any]:
    engine = _fresh_engine(state_dir, demo_dir)
    cap = "demo.latency_down"
    build_case(demo_dir, cap, passed=5, measured=1.01)
    engine.watch_once()
    before = engine.level_of(cap)
    start = time.perf_counter()
    shutil.rmtree(demo_dir / cap)
    engine.watch_once()
    elapsed = time.perf_counter() - start
    outcome = engine.outcomes[-1]
    return {
        "criterion": "F3",
        "capability_id": cap,
        "level_before": before,
        "level_after": engine.level_of(cap),
        "action": outcome.action,
        "reason_code": outcome.reason_code,
        "elapsed_s": round(elapsed, 6),
        "deadline_s": 5.0,
        "pass": before == "REAL" and outcome.to_level == "WITHHELD" and elapsed <= 5.0,
    }


def criterion_f4(state_dir: Path, demo_dir: Path) -> Dict[str, Any]:
    engine = _fresh_engine(state_dir, demo_dir)
    caps = ["demo.ops_a", "demo.ops_b"]
    for cap in caps:
        build_case(demo_dir, cap, passed=5, measured=1.01)
    operations = []
    for round_index in range(5):
        for cap in caps:
            case_dir = demo_dir / cap
            if not case_dir.is_dir():
                build_case(demo_dir, cap, passed=5, measured=1.01)
            elif round_index % 2 == 1:
                shutil.rmtree(case_dir)
            outcome = engine.audit(cap)
            operations.append({"seq": outcome.log_seq, "capability_id": cap,
                               "from": outcome.from_level, "to": outcome.to_level,
                               "action": outcome.action, "reason_code": outcome.reason_code})
    verify = engine.log.verify()
    recomputed_ok = all(e.recompute_sha256() == e.sha256 for e in engine.log.entries)
    seqs = [e.seq for e in engine.log.entries]
    prev_chain_ok = all(
        engine.log.entries[i].prev_sha256 == (engine.log.entries[i - 1].sha256 if i else "0" * 64)
        for i in range(len(engine.log.entries)))
    return {
        "criterion": "F4",
        "operations": len(operations),
        "entries": verify["entries"],
        "log_sha256_verified": bool(verify["ok"]),
        "all_entry_sha256_recomputed": recomputed_ok,
        "prev_chain_intact": prev_chain_ok,
        "sequence_contiguous": seqs == list(range(1, len(seqs) + 1)),
        "head_sha256": verify["head_sha256"],
        "operation_trace": operations,
        "pass": (len(operations) == 10 and verify["entries"] == 10 and verify["ok"]
                 and recomputed_ok and prev_chain_ok),
    }


def criterion_f5() -> Dict[str, Any]:
    lexicon = json.loads((ROOT / "tools" / "evidence_domain_lexicon.json").read_text(encoding="utf-8"))
    ascii_terms = lexicon.get("ascii_terms", [])
    cjk_terms = lexicon.get("cjk_terms", [])
    import ast

    hits = []
    scanned_strings = 0
    for relative in DELIVERED_MODULES:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                scanned_strings += 1
                text = node.value
                low = text.lower()
                for term in ascii_terms:
                    if re.search(rf"\b{re.escape(term)}\b", low):
                        hits.append({"file": relative, "term": term, "context": text[:60]})
                for term in cjk_terms:
                    if term in text:
                        hits.append({"file": relative, "term": term, "context": text[:60]})
    return {
        "criterion": "F5",
        "modules_scanned": len(DELIVERED_MODULES),
        "string_constants_scanned": scanned_strings,
        "ascii_terms_checked": len(ascii_terms),
        "cjk_terms_checked": len(cjk_terms),
        "hits": hits,
        "hit_count": len(hits),
        "pass": not hits,
    }


def criterion_f6() -> Dict[str, Any]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/capability_auto_audit",
                           "-q", "--no-header", "-p", "no:cacheprovider"],
                          cwd=ROOT, capture_output=True, text=True, timeout=900)
    tail = proc.stdout.strip().splitlines()
    summary = tail[-1] if tail else ""
    match = re.search(r"(\d+) passed", summary)
    passed = int(match.group(1)) if match else 0
    return {
        "criterion": "F6",
        "pytest_passed": passed,
        "pytest_returncode": proc.returncode,
        "pytest_summary": summary,
        "threshold": 15,
        "pass": proc.returncode == 0 and passed >= 15,
    }


# --------------------------------------------------------------------------- #
# figure
# --------------------------------------------------------------------------- #
def make_figure(f1: Dict[str, Any], f2: Dict[str, Any], f3: Dict[str, Any],
                f4: Dict[str, Any], out_png: Path) -> Dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    checks = ["artifact", "pytest", "report", "error", "review"]
    matrix = np.zeros((len(f1["rows"]), len(checks)))
    for i, row in enumerate(f1["rows"]):
        for j, name in enumerate(checks):
            matrix[i, j] = 1.0 if row["checks"][name] else 0.0

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 4.6), dpi=150)
    ax = axes[0]
    ax.imshow(matrix, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(checks)))
    ax.set_xticklabels(checks, rotation=20, ha="right")
    ax.set_yticks(range(len(f1["rows"])))
    ax.set_yticklabels([f"{r['capability_id']}\n({'complete' if r['expected_complete'] else 'incomplete'})"
                        for r in f1["rows"]], fontsize=7)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax.text(j, i, "ok" if matrix[i, j] else "missing", ha="center", va="center",
                    fontsize=6, color="black")
    ax.set_title(f"F1 evidence-chain matrix -- {f1['correct']}/{f1['cases']} correct "
                 f"({f1['accuracy']*100:.0f}%)", fontsize=9)

    ax = axes[1]
    labels = ["promotion (F2)", "demotion (F3)"]
    values = [f2["elapsed_s"], f3["elapsed_s"]]
    bars = ax.bar(labels, values, color=["#2a7ab5", "#c0504d"], width=0.5)
    ax.set_yscale("log")
    ax.axhline(5.0, color="black", linestyle="--", linewidth=1.0, label="5 s acceptance line")
    ax.set_ylabel("latency [s, log scale]")
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value, f"{value*1000:.1f} ms",
                ha="center", va="bottom", fontsize=8)
    ax.legend(fontsize=7)
    ax.set_title("F2/F3 auto-audit latency vs. deadline", fontsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    # -- panel (c): the level state machine, drawn from the transition table ----
    ax = axes[2]
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    positions = {
        "REAL": (5.0, 8.2),
        "WITHHELD": (2.4, 4.6),
        "MOCK": (5.0, 4.6),
        "STUB": (7.6, 4.6),
        "NOT_SUPPORTED": (2.4, 1.2),
    }
    for level, (x, y) in positions.items():
        color = "#c9e2b3" if level == "REAL" else "#d9d9d9"
        ax.scatter([x], [y], s=2600, c=color, edgecolors="black", zorder=2)
        ax.text(x, y, level, ha="center", va="center", fontsize=7.5, zorder=3)

    def arrow(src: str, dst: str, label: str, rad: float, color: str) -> None:
        x1, y1 = positions[src]
        x2, y2 = positions[dst]
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", color=color, lw=1.4,
                                    connectionstyle=f"arc3,rad={rad}",
                                    shrinkA=26, shrinkB=26))
        mid_x, mid_y = (x1 + x2) / 2 + rad * (y2 - y1) * 0.55, (y1 + y2) / 2 - rad * (x2 - x1) * 0.55
        ax.text(mid_x, mid_y, label, fontsize=6.5, color=color, ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.85))

    for level in ("WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED"):
        arrow(level, "REAL", "chain complete", 0.12, "#2a7ab5")
    arrow("REAL", "MOCK", "regression", -0.35, "#c0504d")
    arrow("REAL", "WITHHELD", "evidence loss", 0.32, "#c0504d")
    ax.text(5.0, 9.6, "level state machine (REAL + complete chain = HOLD)", ha="center",
            fontsize=8.5)
    ax.text(5.0, 0.3,
            f"F4: {f4['entries']} ops -> {f4['entries']} chained entries, "
            f"sha256 verified={f4['log_sha256_verified']}",
            ha="center", fontsize=7, color="#333333")

    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png)
    plt.close(fig)
    return {"path": str(out_png.relative_to(ROOT)), "sha256": sha256_file(out_png)}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run() -> Dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    demo_dir = OUT / "demo_verified"
    state_dir = OUT / "demo_state"
    if demo_dir.exists():
        shutil.rmtree(demo_dir)
    demo_dir.mkdir(parents=True)

    f1 = criterion_f1(demo_dir)
    f2 = criterion_f2(state_dir, demo_dir)
    f3 = criterion_f3(state_dir, demo_dir)
    f4 = criterion_f4(state_dir, demo_dir)
    f5 = criterion_f5()
    f6 = criterion_f6()
    figure = make_figure(f1, f2, f3, f4, OUT / "fig_n07_overview.png")

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as exc:  # pragma: no cover
        commit = f"unavailable: {exc}"

    criteria = {f["criterion"]: {k: v for k, v in f.items() if k != "rows"}
                for f in (f1, f2, f3, f4, f5, f6)}
    summary = {
        "node": "p262w1-N07-capability-auto-audit",
        "title": "能力台账自动升账/降级机制（证据链 + append-only sha256 审计）",
        "git_commit": commit,
        "spec": load_spec().to_dict(),
        "spec_path": str(SPEC_PATH.relative_to(ROOT)),
        "spec_digest": load_spec().digest(),
        "transition_table": transition_table(),
        "criteria": criteria,
        "f1_matrix": f1["rows"],
        "f4_operation_trace": f4["operation_trace"],
        "figure": figure,
        "all_passed": all(item["pass"] for item in criteria.values()),
    }
    summary["content_digest"] = sha256_json({k: v for k, v in summary.items()
                                             if k not in ("content_digest", "git_commit")})
    out_json = OUT / "acceptance_summary.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sidecar = OUT / "acceptance_summary.sha256"
    sidecar.write_text(f"{sha256_file(out_json)}  acceptance_summary.json\n", encoding="utf-8")

    # persist the F4 audit log as a first-class artifact next to the summary
    shutil.copyfile(state_dir / "audit_log.jsonl", OUT / "f4_audit_log.jsonl")
    (OUT / "f4_audit_log.head.json").write_text(json.dumps({
        "entries": len(AppendOnlyAuditLog(OUT / "f4_audit_log.jsonl")),
        "head_sha256": AppendOnlyAuditLog(OUT / "f4_audit_log.jsonl").head_sha256,
        "log_sha256": sha256_file(OUT / "f4_audit_log.jsonl"),
    }, indent=2) + "\n", encoding="utf-8")

    for name in ("f1_matrix.json",):
        (OUT / name).write_text(json.dumps(f1["rows"], indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
    return summary


def main() -> int:
    summary = run()
    print(json.dumps({k: summary["criteria"][k]["pass"] for k in summary["criteria"]}, indent=2))
    print(f"all_passed={summary['all_passed']} commit={summary['git_commit'][:12]}")
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())