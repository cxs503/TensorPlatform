#!/usr/bin/env python3
"""P145-N08: Synthetic fixture suite — 30 labelled cases, 6 same-source traps.

Validates the evidence grading engine against a deterministic fixture set:
  (a) 30/30 fixture grade reproduction rate = 100%
  (b) 6/6 same-source-copy traps detected
  (c) Cohen's κ ≥ 0.9 vs human labels
  (d) reconciliation inconsistency list emitted as JSON
  (e) domain-word scan = 0 hits
  (f) missing capabilities.json → UNKNOWN (not exception)

These criteria map to the campaign node acceptance criteria (a)-(f).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import evidence_grade as EG
import evidence_fixtures as FX
import evidence_reconcile as RC
import evidence_domain_scan as SCAN


# ── fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Build the 30-case fixture tree once per module."""
    tmp = tmp_path_factory.mktemp("synthetic_fixtures")
    manifest = FX.write_fixtures(tmp, seed=FX.DEFAULT_SEED)
    report = EG.grade_dir(tmp)
    return tmp, manifest, report


# ── (a) 30/30 reproduction ──────────────────────────────────────────────────
def test_fixture_count_is_30(built):
    _, manifest, report = built
    assert report["cases"] == 30
    assert len(manifest["cases"]) == 30


def test_grade_tally_matches_spec(built):
    _, _, report = built
    tally = report["tally"]
    assert tally.get("REAL", 0) == 8
    assert tally.get("MOCK", 0) == 8
    assert tally.get("STUB", 0) == 6
    assert tally.get("WITHHELD", 0) == 8
    assert sum(tally.values()) == 30


def test_every_case_grade_matches_label(built):
    """100% reproduction: every derived grade matches the expected label."""
    _, manifest, report = built
    for res in report["results"]:
        case_id = res["case_id"]
        expected = manifest["cases"][case_id]["expected_grade"]
        assert res["grade"] == expected, (
            f"{case_id}: expected {expected}, got {res['grade']} "
            f"(reason={res.get('reason_code')})"
        )


def test_every_case_reason_code_matches(built):
    """Not just the grade but the reason code must match construction."""
    _, manifest, report = built
    for res in report["results"]:
        case_id = res["case_id"]
        expected_code = manifest["cases"][case_id]["expected_reason_code"]
        assert res["reason_code"] == expected_code, (
            f"{case_id}: expected reason {expected_code}, got {res['reason_code']}"
        )


# ── (b) 6/6 same-source traps ──────────────────────────────────────────────
def test_same_source_traps_detected(built):
    """All 6 same-source-copy traps must be caught (WITHHELD + SAME_SOURCE_COPY)."""
    _, manifest, report = built
    trap_ids = set(manifest["trap_cases"])
    assert len(trap_ids) == 6, f"expected 6 trap cases, got {len(trap_ids)}"

    detected = set()
    for res in report["results"]:
        if res["reason_code"] == "SAME_SOURCE_COPY":
            detected.add(res["case_id"])

    assert detected == trap_ids, (
        f"missed traps: {trap_ids - detected}, false positives: {detected - trap_ids}"
    )


def test_trap_cases_are_withheld(built):
    """Same-source traps must degrade to WITHHELD, not REAL."""
    _, manifest, report = built
    for res in report["results"]:
        if res["case_id"] in manifest["trap_cases"]:
            assert res["grade"] == "WITHHELD"


# ── (c) Cohen's κ ≥ 0.9 ────────────────────────────────────────────────────
def _cohens_kappa(labels_a, labels_b, categories):
    """Compute Cohen's κ for two label sequences over the same cases."""
    n = len(labels_a)
    assert n == len(labels_b)
    if n == 0:
        return 1.0
    po = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    freq_a = {c: 0 for c in categories}
    freq_b = {c: 0 for c in categories}
    for a, b in zip(labels_a, labels_b):
        freq_a[a] = freq_a.get(a, 0) + 1
        freq_b[b] = freq_b.get(b, 0) + 1
    pe = sum(freq_a[c] / n * freq_b[c] / n for c in categories)
    if pe >= 1.0:
        return 1.0
    return (po - pe) / (1.0 - pe)


def test_cohen_kappa_perfect(built):
    """κ between engine and construction labels must be ≥ 0.9 (expect 1.0)."""
    _, manifest, report = built
    categories = ["REAL", "MOCK", "STUB", "WITHHELD"]
    expected_labels = []
    derived_labels = []
    for res in report["results"]:
        case_id = res["case_id"]
        expected_labels.append(manifest["cases"][case_id]["expected_grade"])
        derived_labels.append(res["grade"])
    kappa = _cohens_kappa(expected_labels, derived_labels, categories)
    assert kappa >= 0.9, f"Cohen's κ = {kappa}, expected ≥ 0.9"
    assert kappa == 1.0, f"Cohen's κ = {kappa}, expected perfect 1.0"


# ── (d) reconciliation inconsistency JSON ───────────────────────────────────
def test_reconciliation_synthetic_has_overclaims(built):
    """The synthetic reconciliation must detect deliberate overclaims."""
    _, manifest, report = built
    recon = RC.reconcile_synthetic(report, manifest)
    assert recon["expectation_met"] is True
    tally = recon["summary"]["tally"]
    assert tally.get("OVERCLAIM", 0) == 3
    assert tally.get("UNDERCLAIM", 0) == 1


def test_reconciliation_vs_self_report(built):
    """Reconciliation against the platform self-report must produce JSON."""
    snapshot = ROOT / "tests" / "fixtures" / "capability_ledger" / "capabilities_snapshot.json"
    if not snapshot.exists():
        pytest.skip("capabilities snapshot not available")
    recon = RC.reconcile_file(snapshot, built[0])
    # serialisable
    json.dumps(recon)
    assert recon["summary"]["capabilities"] >= 1


# ── (e) domain-word scan = 0 hits ──────────────────────────────────────────
def test_domain_scan_zero_hits(built):
    """The evidence package must contain zero domain vocabulary."""
    result = SCAN.scan_default_targets()
    assert result["scanned_files"] > 50, (
        f"only {result['scanned_files']} files scanned; fixtures may not be materialised"
    )
    assert result["hits"] == [], f"domain words found: {result['hits'][:5]}"


# ── (f) missing capabilities → UNKNOWN ─────────────────────────────────────
def test_schema_validation_catches_missing_fields():
    """A contract missing required fields must be UNKNOWN, not crash."""
    res = EG.grade_contract({})
    assert res["grade"] == "UNKNOWN"
    assert res["reason_code"] == "SCHEMA_INVALID"


def test_schema_validation_catches_bad_version():
    res = EG.grade_contract({"schema_version": "wrong/0.0"})
    assert res["grade"] == "UNKNOWN"


# ── structural integrity ────────────────────────────────────────────────────
def test_manifest_has_all_required_keys(built):
    _, manifest, _ = built
    for key in ("schema_version", "seed", "counts", "trap_cases",
                "case_capability", "capability_case", "cases"):
        assert key in manifest, f"manifest missing key: {key}"


def test_all_contracts_are_schema_valid(built):
    """Every generated contract must pass schema validation."""
    tmp, _, report = built
    schema = EG.load_schema()
    for res in report["results"]:
        contract_path = tmp / res["case_id"] / "contract.json"
        contract = json.loads(contract_path.read_text())
        errors = EG.validate_contract(contract, schema)
        assert errors == [], f"{res['case_id']}: schema errors: {errors}"


def test_provenance_digest_is_recomputable(built):
    """Contracts with provenance must have a recomputable digest."""
    tmp, _, report = built
    for res in report["results"]:
        contract_path = tmp / res["case_id"] / "contract.json"
        contract = json.loads(contract_path.read_text())
        if "provenance" not in contract:
            continue  # STUB/interface_only contracts have no provenance
        expected = EG.provenance_digest(contract["artifacts"])
        actual = contract.get("provenance", {}).get("sha256")
        assert actual == expected, (
            f"{res['case_id']}: provenance mismatch"
        )



def test_real_cases_have_repro_cmd(built):
    """All REAL cases must have a repro_cmd (otherwise they'd be MOCK)."""
    tmp, _, report = built
    for res in report["results"]:
        if res["grade"] == "REAL":
            contract_path = tmp / res["case_id"] / "contract.json"
            contract = json.loads(contract_path.read_text())
            assert "repro_cmd" in contract
            assert len(contract["repro_cmd"]) > 0


def test_stub_cases_have_no_numeric_artifacts(built):
    """STUB cases must only have interface artifacts (no numeric claims)."""
    tmp, _, report = built
    for res in report["results"]:
        if res["grade"] == "STUB":
            contract_path = tmp / res["case_id"] / "contract.json"
            contract = json.loads(contract_path.read_text())
            kinds = {a["kind"] for a in contract["artifacts"]}
            assert kinds == {"interface"}, f"{res['case_id']}: unexpected kinds: {kinds}"
