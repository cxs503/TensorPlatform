"""Regression tests for the fleet-capability-baseline evidence audit (m1815 / tp-doc-3).

Design rule: the tests exercise the auditor's *logic* on synthetic artifacts (so they
keep passing after someone repairs the real artifacts), and separately prove that the
checked-in ledger reproduces from the real sources. They never freeze a contradiction.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval_set" / "scripts"))

import audit_baseline_integrity as A  # noqa: E402

MODELS = ["m-a", "m-b"]
TASK_DIFF = {"T1": "easy", "T2": "medium", "T3": "hard"}


# ── fixtures ────────────────────────────────────────────────────────────────
def _report(models=("m-a", "m-b"), passed=(1, 0), totals=(2, 1), rates=(0.5, 0.0),
            ci=None, wall=(3600, 1800)):
    rep = {"generated_at": "2026-01-01T00:00:00+00:00",
           "summary": "covering 2 repos",
           "eval_set": {"total_tasks": 3, "succeeded_source": 2, "failed_source": 1,
                        "repos": ["R1", "R2"],
                        "difficulty_distribution": {"easy": 1, "medium": 1, "hard": 1}},
           "baseline": {}, "confidence_intervals": {}}
    for i, m in enumerate(models):
        n = totals[i]
        p = passed[i]
        lo, hi = A.wilson_ci(p / n if n else 0.0, n)
        rep["baseline"][m] = {
            "total_tasks": n, "passed": p, "pass_rate": rates[i],
            "wall_seconds_total": wall[i], "wall_hours_total": round(wall[i] / 3600, 1),
            "ci_95": ci[i] if ci else [round(lo, 3), round(hi, 3)],
            "by_difficulty": {
                "easy": {"total": n, "passed": p, "pass_rate": (p / n if n else 0.0),
                         "wall_seconds": wall[i]},
                "medium": {"total": 0, "passed": 0, "pass_rate": 0.0, "wall_seconds": 0},
                "hard": {"total": 0, "passed": 0, "pass_rate": 0.0, "wall_seconds": 0},
            },
        }
    return rep


def _results(dry=True, rows=True):
    return {"meta": {"dry_run": dry, "run_at": "2026-01-01T00:00:00+00:00"},
            "results": ([{"task_id": "T1", "model": "m-a", "status": "dry_run" if dry else "succeeded"}]
                        if rows else [])}


def _verified(passed=0, scores=(0.25, 0.25)):
    return {"meta": {"verified_at": "2026-01-01T00:00:00+00:00", "total_tasks": len(scores),
                     "total_passed": passed, "pass_rate": passed / len(scores) if scores else 0.0},
            "model_summary": {"m-a": {"passed": passed, "scores": list(scores)}},
            "verifications": []}


def _table(models=("m-a", "m-b"), passed=(1, 0), totals=(2, 1)):
    return {m: {"overall": {"total": totals[i], "passed": passed[i]}} for i, m in enumerate(models)}


def _evalset():
    return {"meta": {"difficulty_levels": {"easy": "x", "medium": "y", "hard": "z"}},
            "tasks": [
                {"id": "T1", "repo": "R1", "difficulty": "easy", "source_status": "succeeded",
                 "title": "t1", "description": "d1",
                 "acceptance_criteria": ["error within 3% of reference"],
                 "solution_hints": ["use the existing kernel"]},
                {"id": "T2", "repo": "R2", "difficulty": "medium", "source_status": "failed",
                 "title": "t2", "description": "d2",
                 "acceptance_criteria": ["POST /tensor/missions/{id}/reclaim returns 403"],
                 "solution_hints": ["edit tests/test_reclaim.py"]},
                {"id": "T3", "repo": "R2", "difficulty": "hard", "source_status": "succeeded",
                 "title": "t3", "description": "d3",
                 "acceptance_criteria": ["Documentation explains the boundary conditions"],
                 "solution_hints": ["see docs"]},
            ]}


MAPPING_OK = [
    {"task_id": "T1", "source_mission": "m1", "model": "m-a", "status": "succeeded", "wall_seconds": 3600},
    {"task_id": "T2", "source_mission": "m2", "model": "m-a", "status": "failed", "wall_seconds": 0},
    {"task_id": "T3", "source_mission": "m3", "model": "m-b", "status": "failed", "wall_seconds": 1800},
]


# ── Wilson interval ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("p,n,want", [
    (0.5, 10, (0.2366, 0.7634)),
    (0.0, 5, (0.0, 0.4345)),
    (1.0, 1, (0.2065, 1.0)),
    (0.0, 0, (0.0, 0.0)),
])
def test_wilson_ci_reference_values(p, n, want):
    lo, hi = A.wilson_ci(p, n)
    assert lo == pytest.approx(want[0], abs=5e-4)
    assert hi == pytest.approx(want[1], abs=5e-4)


# ── AST extraction (no import side effects) ─────────────────────────────────
def test_extract_mapping_and_models_from_production_script():
    rows = A.extract_historical_mapping(A.GEN_SRC)
    models = A.extract_all_models(A.GEN_SRC)
    assert len(models) == 5
    assert len(rows) >= 10
    assert {"task_id", "source_mission", "model", "status", "wall_seconds"} <= set(rows[0])
    assert all(r["status"] in {"succeeded", "failed"} for r in rows)
    assert all(r["model"] in models for r in rows)


def test_audit_does_not_mutate_its_inputs():
    paths = [A.EVAL_JSON, A.GEN_SRC, A.REPORT_JSON, A.TABLE_JSON, A.RESULTS_JSON, A.VERIFIED_JSON]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    A.build_audit()
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    assert before == after


# ── recomputation ───────────────────────────────────────────────────────────
def test_recompute_from_mapping_counts_and_rates():
    rec = A.recompute_from_mapping(MAPPING_OK, MODELS, task_diff=TASK_DIFF)
    assert rec["m-a"]["overall"] == {"total": 2, "passed": 1, "wall_total": 3600}
    assert rec["m-b"]["overall"]["total"] == 1 and rec["m-b"]["overall"]["passed"] == 0
    assert rec["m-a"]["easy"]["passed"] == 1 and rec["m-a"]["medium"]["passed"] == 0
    assert rec["m-b"]["hard"]["total"] == 1


# ── provenance: a dry run cannot back a pass rate; a real run flips the verdict ──
def test_provenance_flags_dry_run_and_passes_on_live_run():
    base = A.check_provenance(_report(), _table(), _results(dry=True), _verified(),
                              MAPPING_OK, MODELS, _evalset())
    assert {c["id"]: c["verdict"] for c in base}["A1_run_provenance"] == "FAIL"
    live = A.check_provenance(_report(), _table(), _results(dry=False), _verified(),
                              MAPPING_OK, MODELS, _evalset())
    assert {c["id"]: c["verdict"] for c in live}["A1_run_provenance"] == "PASS"


def test_mapping_closure_detects_imputed_denominators():
    rep = _report(totals=(2, 5), passed=(1, 0), rates=(0.5, 0.0))  # m-b claims 5, mapping has 1
    by_id = {c["id"]: c for c in A.check_provenance(rep, _table(totals=(2, 5)), _results(),
                                                    _verified(), MAPPING_OK, MODELS, _evalset())}
    assert by_id["A2_mapping_closure"]["verdict"] == "FAIL"
    assert by_id["A2_mapping_closure"]["numbers"]["imputed_rows"] == 4


def test_estimate_flag_requires_marker_in_machine_readable_artifact():
    rep = _report()
    assert {c["id"]: c["verdict"] for c in A.check_provenance(
        rep, _table(), _results(), _verified(), MAPPING_OK, MODELS, _evalset())
    }["A3_estimate_flag"] == "FAIL"
    rep2 = copy.deepcopy(rep)
    rep2["baseline"]["m-a"]["by_difficulty"]["easy"]["estimated"] = True
    assert {c["id"]: c["verdict"] for c in A.check_provenance(
        rep2, _table(), _results(), _verified(), MAPPING_OK, MODELS, _evalset())
    }["A3_estimate_flag"] == "PASS"


# ── cross-artifact ──────────────────────────────────────────────────────────
def test_cross_artifact_contradictions_are_detected():
    by_id = {c["id"]: c for c in A.check_provenance(
        _report(), _table(passed=(0, 0)), _results(), _verified(), MAPPING_OK, MODELS, _evalset())}
    assert by_id["A8_table_vs_report"]["verdict"] == "FAIL"
    assert by_id["A6_report_vs_verified"]["verdict"] == "FAIL"
    assert by_id["A7_verified_score_floor"]["verdict"] == "FAIL"
    # consistent fixture: table == report, verified agrees, scores vary
    ok = A.check_provenance(_report(), _table(), _results(), _verified(passed=1, scores=(0.5, 0.75)),
                            MAPPING_OK, MODELS, _evalset())
    v = {c["id"]: c["verdict"] for c in ok}
    assert v["A8_table_vs_report"] == "PASS"
    assert v["A6_report_vs_verified"] == "PASS"
    assert v["A7_verified_score_floor"] == "PASS"


# ── arithmetic ─────────────────────────────────────────────────────────────
def test_arithmetic_checks_pass_on_consistent_report_and_flag_bad_rate():
    rep = _report()
    v = {c["id"]: c["verdict"] for c in A.check_arithmetic(rep, MODELS, MAPPING_OK, _evalset())}
    assert v["B1_difficulty_closure"] == "PASS"
    assert v["B2_rate_arithmetic"] == "PASS"
    assert v["B3_wilson_recompute"] == "PASS"
    assert v["B4_wall_hours"] == "PASS"

    bad = _report(rates=(0.5, 0.9))
    v2 = {c["id"]: c["verdict"] for c in A.check_arithmetic(bad, MODELS, MAPPING_OK, _evalset())}
    assert v2["B2_rate_arithmetic"] == "FAIL"

    bad_ci = _report(ci=[[0.0, 1.0], [0.0, 1.0]])
    v3 = {c["id"]: c["verdict"] for c in A.check_arithmetic(bad_ci, MODELS, MAPPING_OK, _evalset())}
    assert v3["B3_wilson_recompute"] == "FAIL"


def test_cell_coverage_is_measured_not_assumed():
    rec = A.check_arithmetic(_report(), MODELS, MAPPING_OK, _evalset())
    cov = next(c for c in rec if c["id"] == "B5_cell_coverage")["numbers"]
    assert cov["observed_cells"] == 3
    assert cov["total_cells"] == 6


# ── eval-set structure ────────────────────────────────────────────────────
@pytest.mark.parametrize("text,tier", [
    ("Mean Cd within 5% of reference value", "measured"),
    ("error < 0.1% for >=1000 triangles", "measured"),
    ("POST /tensor/missions/{id}/reclaim returns 403", "decidable"),
    ("Quality gate function returns pass/fail", "decidable"),
    ("Documentation explains the boundary conditions", "subjective"),
    ("Tutorial includes parameter study (viscosity ratio, surface tension)", "subjective"),
])
def test_criterion_classifier(text, tier):
    assert A.classify_criterion(text) == tier


def test_classification_calibration_is_recorded_and_consistent():
    d = A.classify_criteria(_evalset()["tasks"])
    assert d["n_criteria"] == 3
    assert d["tiers"]["subjective"] == 1
    assert 0.0 <= d["calibration"]["agreement"] <= 1.0


def test_evalset_checks_detect_duplicate_ids_and_empty_fields():
    es = _evalset()
    v = {c["id"]: c["verdict"] for c in A.check_evalset(es, A.RUN_SRC)}
    assert v["D3_id_integrity"] == "PASS"
    assert v["D6_field_completeness"] == "PASS"
    bad = _evalset()
    bad["tasks"][1]["id"] = "T1"
    bad["tasks"][0]["repo"] = ""
    v2 = {c["id"]: c["verdict"] for c in A.check_evalset(bad, A.RUN_SRC)}
    assert v2["D3_id_integrity"] == "FAIL"
    assert v2["D6_field_completeness"] == "FAIL"


def test_hint_leakage_detects_paths_in_hints():
    v = {c["id"]: c for c in A.check_evalset(_evalset(), A.RUN_SRC)}
    assert v["D5_hint_leakage"]["verdict"] == "WARN"
    assert v["D5_hint_leakage"]["numbers"]["hints_with_paths"] >= 1


# ── the checked-in ledger must reproduce from the real sources ─────────────
LEDGER = ROOT / "eval_set" / "results" / "baseline_integrity_audit.json"


@pytest.mark.skipif(not LEDGER.exists(), reason="ledger not generated yet")
def test_checked_in_ledger_reproduces_from_sources():
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = A.extract_historical_mapping(A.GEN_SRC)
    models = A.extract_all_models(A.GEN_SRC)
    task_diff = {t["id"]: t["difficulty"] for t in A.load_json(A.EVAL_JSON)["tasks"]}
    rec = A.recompute_from_mapping(rows, models, task_diff=task_diff)
    for m in models:
        entry = ledger["per_model"][m]
        assert entry["mapping_total"] == rec[m]["overall"]["total"]
        assert entry["mapping_passed"] == rec[m]["overall"]["passed"]
        want = (rec[m]["overall"]["passed"] / rec[m]["overall"]["total"]) if rec[m]["overall"]["total"] else 0.0
        assert entry["mapping_rate"] == pytest.approx(want, abs=1e-4)
    # headline evidence grade is derived, not asserted by hand
    assert ledger["headline"]["baseline_evidence_level"] == "MOCK"
    assert ledger["headline"]["checks_total"] == len(ledger["checks"])


@pytest.mark.skipif(not LEDGER.exists(), reason="ledger not generated yet")
def test_ledger_headline_verdicts_match_a_fresh_run():
    fresh = A.build_audit()
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    assert [c["id"] for c in fresh["checks"]] == [c["id"] for c in ledger["checks"]]
    assert {c["id"]: c["verdict"] for c in fresh["checks"]} == \
           {c["id"]: c["verdict"] for c in ledger["checks"]}, "ledger is stale: re-run the audit"
    assert fresh["per_model"] == ledger["per_model"]