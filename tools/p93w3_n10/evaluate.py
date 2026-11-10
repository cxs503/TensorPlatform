"""Measure the P93W3-N10 acceptance numbers on the frozen real corpus.

Every reported number is computed here and written to
``runs/p93w3-n10/metrics.json`` (sha256 of the corpus + of this run are stored
alongside), so the report never hand-copies a value.

Inputs (read-only)
------------------
runs/p93w3-n10/corpus/missions_succeeded.json   frozen terminal rows (fetched with the read-only
                                               tools/p92w3_n10/fetch_missions.py --out runs/p93w3-n10/corpus)
runs/p93w3-n10/corpus/missions_succeeded.sha256

Outputs
-------
runs/p93w3-n10/metrics.json          all criteria numbers + distributions
runs/p93w3-n10/state_breakdown.json  per-state counts and example record ids

Usage:  python3 tools/p93w3_n10/evaluate.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/p93w3-n10"
CORPUS = RUN / "corpus" / "missions_succeeded.json"
CORPUS_SHA = RUN / "corpus" / "missions_succeeded.sha256"

sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from api.domain import mvd_checkpoint as mvd               # noqa: E402
from api.domain import terminal_state as ts               # noqa: E402


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def load_corpus() -> tuple[list[dict], str]:
    blob = CORPUS.read_bytes()
    digest = sha256_bytes(blob)
    recorded = CORPUS_SHA.read_text(encoding="utf-8").strip() if CORPUS_SHA.exists() else None
    if recorded and recorded != digest:
        raise SystemExit(f"corpus digest mismatch: file {digest} != recorded {recorded}")
    return json.loads(blob), digest


def scenario_matrix() -> dict:
    """The 20-scenario matrix lives in the test module (single source, no copy)."""
    import test_p93w3_n10_terminal_states as scn

    rows = []
    correct = 0
    for sid, desc, record, expected in scn.SCENARIOS:
        try:
            got = ts.classify_terminal_state(record).state
        except ts.NonTerminalRecord:
            got = "NonTerminalRecord"
        ok = got == expected
        correct += 1 if ok else 0
        rows.append({"id": sid, "description": desc, "expected": expected, "got": got, "correct": ok})
    matrix = {truth: {pred: 0 for pred in scn.STATES} for truth in scn.STATES}
    for _sid, _desc, record, expected in scn.CLASSIFIABLE:
        if expected in matrix:
            try:
                got = ts.classify_terminal_state(record).state
            except ts.NonTerminalRecord:
                continue
            matrix[expected][got] += 1
    return {
        "n_scenarios": len(rows),
        "n_correct": correct,
        "accuracy": round(correct / len(rows), 6),
        "rows": rows,
        "confusion": matrix,
    }


def latency_benchmark(rows: list[dict], repeats: int = 3) -> dict:
    """70% MVD checkpoint trigger latency over every corpus row (wall clock ms)."""
    samples: list[float] = []
    triggered = 0
    passed = 0
    for _ in range(repeats):
        for row in rows:
            out = ts.mvd_checkpoint_for_record(row)
            samples.append(out["latency_ms"])
            triggered += 1 if out["triggered"] else 0
            passed += 1 if out["triggered"] and out["passed"] else 0
    samples.sort()

    def pct(q: float) -> float:
        if not samples:
            return 0.0
        idx = min(len(samples) - 1, max(0, int(round(q * (len(samples) - 1)))))
        return round(samples[idx], 4)

    return {
        "n_calls": len(samples),
        "repeats": repeats,
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "p100_ms": pct(1.0),
        "criterion_s": 1.0,
        "criterion_met": pct(1.0) <= 1000.0,
        "triggered_calls": triggered,
        "triggered_passed": passed,
        "checkpoint_threshold": mvd.BUDGET_THRESHOLD,
        "threshold_pinned_to_contract": (
            ts.budget_thresholds()["mvd_checkpoint"] == mvd.BUDGET_THRESHOLD
        ),
    }


def label_schema(rows: list[dict]) -> dict:
    labels, invalid = [], []
    for i, row in enumerate(rows):
        label = ts.delivery_label(row, allow_non_terminal=True)
        errors = ts.validate_delivery_label(label)
        if errors:
            invalid.append({"index": i, "errors": errors[:3]})
        labels.append(label)
    return {
        "n_labels": len(labels),
        "n_valid": len(labels) - len(invalid),
        "pass_rate": round((len(labels) - len(invalid)) / len(labels), 6) if labels else 0.0,
        "invalid_examples": invalid[:5],
        "schema_path": "backend/contracts/delivery_label.v1.json",
    }


def migration(rows: list[dict]) -> dict:
    t0 = time.perf_counter()
    out = ts.migrate_legacy_succeeded(rows)
    elapsed = time.perf_counter() - t0
    report = dict(out["report"])
    report["elapsed_s"] = round(elapsed, 3)
    legacy_succeeded = sum(1 for r in rows if r.get("status") == "succeeded")
    migrated_forced = sum(
        1 for r in out["records"]
        if isinstance(r, dict)
        and r.get("migration", {}).get("legacy_status") == "succeeded"
        and r.get("terminal_state") == "forced_finish"
    )
    report["legacy_succeeded_rows"] = legacy_succeeded
    report["legacy_succeeded_to_forced_finish"] = migrated_forced
    report["binary_success_rate_before"] = round(legacy_succeeded / len(rows), 6) if rows else 0.0
    report["delivery_rate_after"] = round(report["state_counts"]["succeeded"] / len(rows), 6) if rows else 0.0
    report["success_rate_inflation_pp"] = round(
        (report["binary_success_rate_before"] - report["delivery_rate_after"]) * 100, 3
    )
    return report


def budget_evidence(rows: list[dict], assessments: list) -> dict:
    """Evidence audit: which budget dimension is allowed to prove exhaustion.

    The rejected rule (any utilization ratio >= 1.0 -> forced closure) is scored
    here against the harness marker, the only ground fact about forced closure.
    That precision/recall pair is the reason wall arithmetic is confined to the
    warning band in the contract; ``false_forced_rows_avoided`` is the size of the
    error the ratio-only rule would have introduced.
    """
    dims = [ts.budget_dimensions(row) for row in rows]
    marked = [d for d in dims if d["marker_exhaustion"]]
    wall_cap = [d for d in dims if d["wall_at_cap"]]
    tool_cap = [d for d in dims if d["tool_at_cap"]]
    wall_marked = [d for d in dims if d["closure_cause"] == "wall"]
    wall_cap_and_marked = [d for d in dims if d["wall_at_cap"] and d["marker_exhaustion"]]

    naive_counts = {state: 0 for state in ts.TERMINAL_STATES}
    for row, assessment in zip(rows, assessments):
        utilization = ts.budget_dimensions(row)["utilization"]
        if utilization is not None and utilization >= 1.0:
            naive_counts["forced_finish"] += 1
        elif assessment.completeness < 1.0:
            naive_counts["degraded"] += 1
        else:
            naive_counts["succeeded"] += 1

    evidence_rows = sum(1 for d in dims if d["marker_exhaustion"] or d["tool_at_cap"])
    return {
        "rule": "exhausted iff forced marker OR tool-call counter at cap; wall ratio -> warning band only",
        "marker_exhaustion_rows": len(marked),
        "tool_at_cap_rows": len(tool_cap),
        "wall_at_cap_rows": len(wall_cap),
        "wall_marker_rows": len(wall_marked),
        "wall_cap_with_any_marker": len(wall_cap_and_marked),
        "wall_proxy_precision": round(len(wall_cap_and_marked) / len(wall_cap), 6) if wall_cap else None,
        "wall_proxy_recall": round(
            len([d for d in wall_marked if d["wall_at_cap"]]) / len(wall_marked), 6
        ) if wall_marked else None,
        "evidence_rows": evidence_rows,
        "naive_ratio_only_counts": naive_counts,
        "false_forced_rows_avoided": naive_counts["forced_finish"] - evidence_rows,
    }


def main() -> None:
    rows, digest = load_corpus()
    t_start = time.perf_counter()

    assessments = [ts.classify_terminal_state(row, allow_non_terminal=True) for row in rows]
    summary = ts.summarize_states(assessments)

    # Independent redundancy check: the marker-based weak label must be recalled.
    marked = [i for i, row in enumerate(rows) if ts.has_forced_marker(row)]
    marked_hit = [i for i in marked if assessments[i].state == "forced_finish"]
    unmarked_forced = [
        i for i, row in enumerate(rows)
        if assessments[i].state == "forced_finish" and i not in set(marked)
    ]
    weak_label = {
        "marked_rows": len(marked),
        "marked_to_forced_finish": len(marked_hit),
        "recall": round(len(marked_hit) / len(marked), 6) if marked else None,
        "forced_finish_without_marker": len(unmarked_forced),
        "closure_cause_counts": {
            (cause or "none"): sum(1 for a in assessments if a.closure_cause == cause)
            for cause in ("budget", "wall", "progress", "unknown", None)
        },
    }

    composition = {"budget_marker": 0, "wall_marker": 0, "progress_marker": 0,
                   "tool_cap_only": 0}
    for row, assessment in zip(rows, assessments):
        if assessment.state != "forced_finish":
            continue
        cause = ts.closure_cause(row)
        if cause == "budget":
            composition["budget_marker"] += 1
        elif cause == "wall":
            composition["wall_marker"] += 1
        elif cause == "progress":
            composition["progress_marker"] += 1
        else:
            composition["tool_cap_only"] += 1
    composition_total = sum(composition.values())
    composition["total"] = composition_total
    composition["consistent_with_state_count"] = (
        composition_total == summary["counts"]["forced_finish"]
    )

    breakdown: dict[str, dict] = {}
    for state in ts.TERMINAL_STATES:
        ids = [str(rows[i].get("id")) for i, a in enumerate(assessments) if a.state == state]
        breakdown[state] = {"count": len(ids), "example_ids": ids[:10]}

    metrics = {
        "node": "P93W3-N10",
        "mission_id": 9177,
        "corpus": {
            "path": str(CORPUS.relative_to(ROOT)),
            "sha256": digest,
            "records": len(rows),
            "source": "read-only /tensor/missions?status=succeeded (tools/p92w3_n10/fetch_missions.py --out runs/p93w3-n10/corpus)",
        },
        "criteria": {
            "a_classification_accuracy": scenario_matrix(),
            "b_mvd_trigger_latency": latency_benchmark(rows),
            "c_backward_compat_migration": migration(rows),
            "d_label_schema_validation": label_schema(rows),
        },
        "distributions": summary,
        "weak_label_agreement": weak_label,
        "budget_evidence": budget_evidence(rows, assessments),
        "forced_finish_composition": composition,
        "contract": {
            "path": "backend/contracts/terminal_state_vocab.v1.json",
            "version": ts.contract()["version"],
            "states": list(ts.TERMINAL_STATES),
            "precedence": ts.contract()["precedence"],
            "budget_thresholds": ts.budget_thresholds(),
            "completeness_weights": ts._weights(),
        },
    }

    RUN.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True)
    (RUN / "metrics.json").write_text(payload + "\n", encoding="utf-8")
    (RUN / "state_breakdown.json").write_text(
        json.dumps(breakdown, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "records": len(rows),
        "corpus_sha256": digest,
        "metrics_sha256": sha256_bytes(payload.encode()),
        "accuracy": metrics["criteria"]["a_classification_accuracy"]["accuracy"],
        "mvd_p100_ms": metrics["criteria"]["b_mvd_trigger_latency"]["p100_ms"],
        "zero_data_loss": metrics["criteria"]["c_backward_compat_migration"]["zero_data_loss"],
        "label_pass_rate": metrics["criteria"]["d_label_schema_validation"]["pass_rate"],
        "elapsed_s": round(time.perf_counter() - t_start, 2),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()