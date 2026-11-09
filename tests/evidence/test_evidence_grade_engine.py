#!/usr/bin/env python3
"""Regression tests for the machine-recomputable evidence grading engine (m7794).

The contract under test: a grade is a *function of the artifact structure*, so it
can be re-derived by anyone holding the contract. These tests assert the four
judge criteria (a/b/c/d) plus the failure modes, and they use the synthetic
fixture lane rather than any domain artifact.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (str(ROOT), str(ROOT / "tools")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import evidence_domain_scan as SCAN  # noqa: E402
import evidence_fixtures as FX  # noqa: E402
import evidence_grade as EG  # noqa: E402
import evidence_reconcile as RC  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Materialise the 30-case fixture lane once and grade it."""
    tmp = tmp_path_factory.mktemp("evidence-fixtures")
    manifest = FX.write_fixtures(tmp)
    report = EG.grade_dir(tmp)
    return tmp, manifest, report


def _by_case(report):
    return {row["case_id"]: row for row in report["results"]}


# ── (a) 30/30 reproduction ────────────────────────────────────────────────
def test_fixture_lane_has_declared_shape(built):
    _, manifest, _ = built
    assert manifest["counts"] == {"REAL": 8, "MOCK": 8, "STUB": 6, "WITHHELD": 8}
    assert len(manifest["trap_cases"]) == 6


def test_generation_is_deterministic(tmp_path):
    """Same seed -> identical contracts (no timestamps or paths inside them)."""
    FX.write_fixtures(tmp_path / "a")
    FX.write_fixtures(tmp_path / "b")
    for case_dir in sorted((tmp_path / "a").glob("case-*")):
        other = tmp_path / "b" / case_dir.name / "contract.json"
        assert (case_dir / "contract.json").read_bytes() == other.read_bytes()
    assert (tmp_path / "a" / "labels.json").read_bytes() == \
           (tmp_path / "b" / "labels.json").read_bytes()


def test_all_30_labels_reproduced(built):
    _, manifest, report = built
    derived = _by_case(report)
    mismatches = []
    for case_id, meta in manifest["cases"].items():
        got = derived[case_id]["grade"]
        if got != meta["expected_grade"]:
            mismatches.append((case_id, meta["expected_grade"], got,
                               derived[case_id]["reason_code"]))
    assert not mismatches, f"label/derived mismatches: {mismatches}"
    assert report["cases"] == 30


# ── (b) the same-source-copy trap ─────────────────────────────────────────
def test_same_source_copy_detected_6_of_6(built):
    _, manifest, report = built
    derived = _by_case(report)
    traps = manifest["trap_cases"]
    assert len(traps) == 6
    hits = [cid for cid in traps
            if derived[cid]["grade"] == "WITHHELD"
            and derived[cid]["reason_code"] == "SAME_SOURCE_COPY"]
    assert len(hits) == 6, [(cid, derived[cid]["reason_code"]) for cid in traps]
    # the trap must not be detectable by counting fields: every trap carries a
    # complete-looking artifact set (anchor + 2 paths + residual + control)
    for cid in traps:
        contract = json.loads((built[0] / cid / "contract.json").read_text())
        kinds = [a["kind"] for a in contract["artifacts"]]
        assert kinds.count("independent_path") == 2
        assert {"anchor", "residual", "negative_control"} <= set(kinds)


def test_renamed_method_is_not_independence(built):
    """A copy that renames its method label is still a copy (the trap's core)."""
    tmp, manifest, _ = built
    case = manifest["trap_cases"][0]
    contract = json.loads((tmp / case / "contract.json").read_text())
    methods = [a.get("method") for a in contract["artifacts"]
               if a["kind"] == "independent_path"]
    assert "independent_beta" in methods, methods
    res = EG.grade_contract(contract, base_dir=tmp / case)
    assert res["reason_code"] == "SAME_SOURCE_COPY"


# ── (c) agreement with the human labels ───────────────────────────────────
def test_cohen_kappa_at_least_0_9(built):
    _, manifest, report = built
    derived = _by_case(report)
    pairs = [(meta["expected_grade"], derived[cid]["grade"])
             for cid, meta in manifest["cases"].items()]
    kappa = RC.cohen_kappa(pairs)
    assert kappa["value"] is not None
    assert kappa["value"] >= 0.9, kappa
    matrix = RC.confusion_matrix(pairs)
    off_diagonal = sum(n for expected, row in matrix.items()
                       for got, n in row.items() if got != expected)
    assert off_diagonal == 0, matrix


# ── (d) reconciliation ledger ─────────────────────────────────────────────
def test_reconciliation_synthetic_hits_declared_deviations(built):
    _, manifest, report = built
    recon = RC.reconcile_synthetic(report, manifest)
    assert recon["summary"]["tally"].get("OVERCLAIM") == 3
    assert recon["summary"]["tally"].get("UNDERCLAIM") == 1
    assert recon["expectation_met"] is True
    assert len(recon["inconsistencies"]) == 4


def test_reconciliation_against_platform_self_report():
    snapshot = ROOT / "tests" / "fixtures" / "capability_ledger" / "capabilities_snapshot.json"
    recon = RC.reconcile_file(snapshot, ROOT / "artifacts" / "evidence_grade" / "fixtures")
    assert recon["summary"]["capabilities"] == 21
    # no contract backs a self-declared grade -> UNVERIFIED, never silently accepted
    assert recon["summary"]["tally"].get("UNVERIFIED") == 19
    assert all(row["verdict"] != "CONSISTENT" or row["self_reported"] == "NOT_SUPPORTED"
               for row in recon["rows"])
    json.dumps(recon)  # serialisable


# ── failure modes: the grader must be able to say no ──────────────────────
def _first(built, label):
    _, manifest, _ = built
    for cid, meta in manifest["cases"].items():
        if meta["expected_grade"] == label:
            return cid
    raise AssertionError(label)


def test_malformed_contract_is_unknown():
    res = EG.grade_contract({"schema_version": "evidence-contract/1.0"})
    assert res["grade"] == "UNKNOWN"
    assert res["reason_code"] == "SCHEMA_INVALID"


def test_numeric_claim_without_predeclared_tolerance_is_unknown():
    res = EG.grade_contract({
        "schema_version": "evidence-contract/1.0",
        "capability_id": "cap.alpha.calc",
        "claim": {"kind": "numeric", "statement": "no tolerance declared"},
        "artifacts": [{"id": "api", "kind": "interface",
                       "source": {"kind": "inline", "ref": "f(x)"}}],
    })
    assert res["grade"] == "UNKNOWN"


def test_removing_negative_control_withholds(built):
    tmp, _, _ = built
    case = _first(built, "REAL")
    contract = json.loads((tmp / case / "contract.json").read_text())
    contract["artifacts"] = [a for a in contract["artifacts"]
                             if a["kind"] != "negative_control"]
    res = EG.grade_contract(contract, base_dir=tmp / case)
    assert (res["grade"], res["reason_code"]) == (
        "WITHHELD", "NEGATIVE_CONTROL_UNUSABLE")


def test_inline_anchor_is_hardcoded_mock(built):
    tmp, _, _ = built
    case = _first(built, "REAL")
    contract = json.loads((tmp / case / "contract.json").read_text())
    anchor = next(a for a in contract["artifacts"] if a["kind"] == "anchor")
    anchor["source"] = {"kind": "inline", "ref": "3.14159"}
    anchor["value"] = 3.14159
    res = EG.grade_contract(contract, base_dir=tmp / case)
    assert (res["grade"], res["reason_code"]) == ("MOCK", "HARDCODED_INLINE")


def test_tampered_residual_withholds(built):
    tmp, _, _ = built
    case = _first(built, "REAL")
    contract = json.loads((tmp / case / "contract.json").read_text())
    residual = next(a for a in contract["artifacts"] if a["kind"] == "residual")
    residual["value"] = abs(residual["value"]) + 1e-3
    res = EG.grade_contract(contract, base_dir=tmp / case)
    assert (res["grade"], res["reason_code"]) == (
        "WITHHELD", "RESIDUAL_NOT_RECOMPUTABLE")


def test_artifact_set_cannot_change_after_hashing(built):
    """Swapping the recorded digest (an artifact was edited post hoc) is caught."""
    tmp, _, _ = built
    case = _first(built, "REAL")
    contract = json.loads((tmp / case / "contract.json").read_text())
    contract["provenance"]["sha256"] = "0" * 64
    res = EG.grade_contract(contract, base_dir=tmp / case)
    assert (res["grade"], res["reason_code"]) == (
        "WITHHELD", "PROVENANCE_DIGEST_MISMATCH")


def test_unhashed_extra_path_withholds(built):
    """An added path with no declared sha256 cannot corroborate anything."""
    tmp, _, _ = built
    case = _first(built, "REAL")
    contract = json.loads((tmp / case / "contract.json").read_text())
    contract["artifacts"].append({
        "id": "extra", "kind": "independent_path",
        "source": {"kind": "file", "ref": "path-a.json"},
        "value": 1.0, "method": "sneaked_in"})
    res = EG.grade_contract(contract, base_dir=tmp / case)
    assert (res["grade"], res["reason_code"]) == ("WITHHELD", "PATH_NOT_RECOMPUTABLE")


def test_real_case_trace_records_every_gate(built):
    tmp, _, _ = built
    case = _first(built, "REAL")
    contract = json.loads((tmp / case / "contract.json").read_text())
    res = EG.grade_contract(contract, base_dir=tmp / case)
    assert res["grade"] == "REAL"
    passed: list[str] = []
    for row in res["trace"]:
        if row["status"] == "pass" and row["rule"] not in passed:
            passed.append(row["rule"])
    # every gate up to and including the terminal one is recorded as passed
    assert passed == [r["id"] for r in EG.RULE_DAG], passed
    assert res["metrics"]["residual_recomputed"] <= res["metrics"]["tolerance_limit"]


# ── (e) domain vocabulary gate ────────────────────────────────────────────
def test_domain_scan_finds_nothing_in_the_package():
    result = SCAN.scan_default_targets()
    assert result["scanned_files"] > 50
    assert result["hits"] == [], result["hits"][:5]


def test_domain_scan_positive_control(tmp_path):
    """The scanner must be able to fail: inject a lexicon term and expect a hit."""
    lexicon = SCAN.load_lexicon()
    term = lexicon["cjk_terms"][0]
    probe = tmp_path / "probe.py"
    probe.write_text(f"# {term}\n", encoding="utf-8")
    result = SCAN.scan_paths([probe], lexicon=lexicon)
    assert len(result["hits"]) == 1
    assert result["hits"][0]["term"] == term


def test_schema_is_domain_neutral_and_valid():
    schema = EG.load_schema()
    assert schema["$id"].endswith("evidence_contract.schema.json")
    blob = json.dumps(schema, ensure_ascii=False)
    lexicon = SCAN.load_lexicon()
    for term in lexicon["cjk_terms"]:
        assert term not in blob
    assert "negative_control" in blob and "independent_path" in blob