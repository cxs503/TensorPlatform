"""Completeness scoring: exact values, contradiction handling, fail-closed scan."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.platform_governance_gates.evidence import (
    REQUIREMENTS,
    CapabilityEvidence,
    Contradiction,
    EvidenceItem,
    evaluate,
    scan_evidence_roots,
)

def item(kind: str, ref: str, **kwargs) -> EvidenceItem:
    sha = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    return EvidenceItem(kind=kind, ref=ref, sha256=sha, **kwargs)


def _items_complete():
    return [
        item("verification", "reports/v.json", independent=True),
        item("cross_check", "artifacts/x.json"),
        item("benchmark", "benchmarks/b.json"),
    ]


def test_completeness_exact_score_one():
    report = evaluate(CapabilityEvidence("demo.a", "MOCK", _items_complete()))
    assert report.valid is True
    assert report.score == 1.0
    assert all(report.checks.values())
    assert report.missing == []


def test_completeness_exact_score_five_of_six():
    bundle = CapabilityEvidence("demo.b", "MOCK", [
        item("verification", "reports/v.json", independent=True),
        item("benchmark", "benchmarks/b.json"),
    ])
    report = evaluate(bundle)
    assert report.score == round(5 / 6, 6)
    assert report.checks["cross_consistent"] is False
    assert report.missing == ["cross_consistent"]


def test_completeness_exact_score_three_of_six():
    report = evaluate(CapabilityEvidence("demo.c", "WITHHELD", [
        item("cross_check", "artifacts/x.json")]))
    assert report.score == 0.5
    assert report.checks["cross_consistent"] is True


def test_malformed_kind_and_digest_fail_closed():
    bad_digest = EvidenceItem(kind="verification", ref="reports/v.json",
                              sha256="not-a-digest", independent=True)
    unknown_kind = item("mystery", "artifacts/m.json")
    report = evaluate(CapabilityEvidence("demo.d", "REAL",
                                         [bad_digest, unknown_kind]))
    assert report.score == 0.0
    assert report.admissible == 0
    assert report.valid is True  # structurally valid claim, zero admissible evidence


def test_declared_digest_mismatch_is_contradiction_and_caps_score():
    tampered = item("verification", "reports/v.json", independent=True,
                    declared_sha256="0" * 64)
    bundle = CapabilityEvidence("demo.e", "REAL", _items_complete()[:-1] + [tampered])
    report = evaluate(bundle)
    assert report.contradictions
    assert report.contradictions[0]["kind"] == "digest_mismatch"
    assert report.score <= 0.5
    assert report.checks["cross_consistent"] is False


def test_explicit_contradiction_lowers_score():
    bundle = CapabilityEvidence(
        "demo.f", "REAL", _items_complete(),
        contradictions=[Contradiction(kind="replication_failed",
                                      ref="reports/v.json", reason="demo")],
    )
    report = evaluate(bundle)
    assert len(report.contradictions) == 1
    assert report.score <= 0.5


def test_capability_id_required_fail_closed():
    report = evaluate(CapabilityEvidence("", "REAL", _items_complete()))
    assert report.valid is False
    assert report.score == 0.0
    assert report.invalid_reason


def test_scan_roots_aggregates_by_slug_and_computes_digest(tmp_path: Path):
    roots = {}
    for name in ("reports", "artifacts", "benchmarks", "verified"):
        roots[name] = tmp_path / name
        roots[name].mkdir()
    (roots["reports"] / "demo.cap__verification__v.json").write_text(json.dumps({
        "capability_id": "demo.cap", "kind": "verification", "ok": True,
        "independent": True, "current_level": "MOCK"}), encoding="utf-8")
    (roots["artifacts"] / "demo.cap__cross_check__x.json").write_text(json.dumps({
        "capability_id": "demo.cap", "kind": "cross_check", "ok": True}),
        encoding="utf-8")
    (roots["benchmarks"] / "demo.cap__benchmark__b.json").write_text(json.dumps({
        "capability_id": "demo.cap", "kind": "benchmark", "ok": True}),
        encoding="utf-8")
    bundles = scan_evidence_roots(list(roots.values()))
    assert set(bundles) == {"demo.cap"}
    report = evaluate(bundles["demo.cap"])
    assert report.score == 1.0
    assert report.admissible == 3
    assert len(report.evidence_digest) == 64


def test_scan_ignores_unlabelled_files(tmp_path: Path):
    (tmp_path / "noise.json").write_text(json.dumps({"hello": "world"}),
                                         encoding="utf-8")
    assert scan_evidence_roots([tmp_path]) == {}


def test_requirements_contract_is_four_plus_two():
    assert len(REQUIREMENTS) == 6
    assert REQUIREMENTS[:4] == (
        "independent_verification", "benchmark_artifact",
        "sha256_verified", "cross_consistent",
    )