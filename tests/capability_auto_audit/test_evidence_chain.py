"""Evidence-chain validator tests (p262w1-N07, acceptance F1).

F1: five known cases (three complete, two incomplete) must be classified 100 %
correctly by the machine -- including *which* link broke and *what kind* of
failure it is (evidence gap vs. regression).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from chain_fixtures import SIX_ELEMENTS, write_case

from tools.capability_auto_audit.evidence_chain import (
    ChainSpec,
    ErrorGate,
    EvidenceChainValidator,
    load_evidence,
    parse_junit_xml,
    parse_pytest_json,
)


# --------------------------------------------------------------------------- #
# parsers
# --------------------------------------------------------------------------- #
def test_parse_junit_counts_only_testcases():
    xml = ('<testsuites><testsuite tests="99">'
           '<testcase name="a"/><testcase name="b"/>'
           '<testcase name="c"><failure/></testcase>'
           '<testcase name="d"><skipped/></testcase>'
           '</testsuite></testsuites>')
    assert parse_junit_xml(xml) == {"total": 4, "passed": 2, "failed": 1,
                                    "errors": 0, "skipped": 1}


def test_parse_pytest_json_normalises_missing_total():
    assert parse_pytest_json({"passed": 4, "failed": 1}) == {
        "total": 5, "passed": 4, "failed": 1, "errors": 0, "skipped": 0}


# --------------------------------------------------------------------------- #
# links
# --------------------------------------------------------------------------- #
def test_complete_chain_verdict(make_case):
    make_case("syn.alpha", pytest_kind="junit", passed=5)
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.alpha"))
    assert verdict.complete is True
    assert verdict.missing == []
    assert verdict.reason_code == "CHAIN_COMPLETE"
    assert [c.name for c in verdict.checks] == ["artifact", "pytest", "report", "error", "review"]
    assert all(c.ok for c in verdict.checks)


def test_absent_directory_reports_every_link_missing(make_case):
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.absent"))
    assert verdict.complete is False
    assert verdict.missing == ["artifact", "pytest", "report", "error", "review"]
    assert verdict.failure_kind == "evidence"


def test_report_missing_element_is_named(make_case):
    make_case("syn.report", elements=("case", "method", "results"))
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.report"))
    assert verdict.complete is False
    assert verdict.reason_code == "REPORT_INCOMPLETE"
    report = {c.name: c for c in verdict.checks}["report"]
    assert report.numbers["missing_elements"] == ["figures", "notes", "error_judgement"]
    assert report.numbers["elements_required"] == 6


def test_blank_report_element_counts_as_missing(make_case):
    make_case("syn.empty")
    report_path = make_case.root / "syn.empty" / "report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["figures"] = "   "
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.empty"))
    assert "report" in verdict.missing


def test_failing_tests_are_a_regression(make_case):
    make_case("syn.regress", passed=4, failed=1)
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.regress"))
    assert verdict.complete is False
    assert verdict.reason_code == "PYTEST_BELOW_THRESHOLD"
    assert verdict.failure_kind == "regression"


def test_too_few_tests_is_only_an_evidence_gap(make_case):
    make_case("syn.thin", passed=2)
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.thin"))
    assert verdict.complete is False
    assert verdict.failure_kind == "evidence"


def test_error_above_tolerance_fails_error_link(make_case):
    make_case("syn.loose", measured=1.10, reference=1.0)
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.loose"))
    error = {c.name: c for c in verdict.checks}["error"]
    assert error.ok is False
    assert error.numbers["rel_error"] == pytest.approx(0.10, abs=1e-12)
    assert verdict.failure_kind == "regression"


def test_tampered_error_claim_is_rejected(make_case):
    """The claimed number must equal the recomputed one -- no hand-written errors."""
    make_case("syn.tamper", claimed_rel_error=0.001)
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.tamper"))
    error = {c.name: c for c in verdict.checks}["error"]
    assert error.ok is False
    assert error.numbers["claim_accepted"] is False
    assert error.numbers["rel_error"] == pytest.approx(0.02, abs=1e-12)
    assert verdict.failure_kind == "regression"


def test_missing_claim_is_an_evidence_gap(make_case):
    make_case("syn.noclaim")
    metrics = make_case.root / "syn.noclaim" / "metrics.json"
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    payload.pop("rel_error")
    metrics.write_text(json.dumps(payload), encoding="utf-8")
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.noclaim"))
    assert "error" in verdict.missing
    assert verdict.failure_kind == "evidence"


def test_review_must_be_independent_and_passing(make_case):
    make_case("syn.review", review={"reviewer": "self", "verdict": "PASS", "independent": False})
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.review"))
    assert verdict.reason_code == "REVIEW_NOT_PASSED"

    make_case("syn.review2", review={"reviewer": "x", "verdict": "REJECT", "independent": True})
    verdict2 = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.review2"))
    assert verdict2.reason_code == "REVIEW_NOT_PASSED"


def test_artifact_without_digest_is_not_an_artifact(make_case):
    make_case("syn.nodigest", artifact_digest="")
    verdict = EvidenceChainValidator().validate(load_evidence(make_case.root, "syn.nodigest"))
    assert "artifact" in verdict.missing


# --------------------------------------------------------------------------- #
# metric-gate integration
# --------------------------------------------------------------------------- #
def test_error_link_delegates_to_platform_metric_gate():
    gate = ErrorGate(ChainSpec())
    assert gate.source == "metric_gate"  # tools.metric_gate is the platform recompute gate
    out = gate.evaluate(1.02, 1.0)
    assert out["value"] == pytest.approx(0.02, abs=1e-12)
    claim = gate.check_claim(0.02, 1.02, 1.0)
    assert claim["accepted"] is True and claim["source"] == "metric_gate"
    assert gate.check_claim(0.5, 1.02, 1.0)["accepted"] is False


def test_error_link_honours_a_custom_expression():
    spec = ChainSpec(error_expression="abs(measured - reference)", error_metric_name="abs_err",
                     max_rel_error=0.05)
    tmp = Path(tempfile.mkdtemp(prefix="chain-inline-"))
    write_case(tmp, "syn.inline", measured=1.10, reference=1.0)
    verdict = EvidenceChainValidator(spec).validate(load_evidence(tmp, "syn.inline", spec))
    error = {c.name: c for c in verdict.checks}["error"]
    # recomputed absolute error 0.10 exceeds the 0.05 tolerance of the custom spec
    assert error.ok is False
    assert error.numbers["rel_error"] == pytest.approx(0.10, abs=1e-12)


# --------------------------------------------------------------------------- #
# spec handling
# --------------------------------------------------------------------------- #
def test_spec_roundtrip_and_digest_is_deterministic():
    repo_spec = Path("config/capability_chain_spec.json")
    spec = ChainSpec.from_file(repo_spec) if repo_spec.is_file() else ChainSpec()
    clone = ChainSpec.from_dict(spec.to_dict())
    assert clone == spec
    assert clone.digest() == spec.digest()
    assert ChainSpec.from_dict({}) == ChainSpec()
    assert ChainSpec(max_rel_error=0.0).max_rel_error == 0.0


def test_spec_rejects_unknown_default_level():
    with pytest.raises(ValueError):
        ChainSpec(default_level="PROBABLY")


# --------------------------------------------------------------------------- #
# F1 -- the five acceptance cases
# --------------------------------------------------------------------------- #
F1_CASES = [
    # (capability id, case kwargs, expected complete, expected failing link)
    ("syn.case_a", dict(pytest_kind="junit", passed=6, measured=1.01), True, None),
    ("syn.case_b", dict(pytest_kind="json", passed=4, measured=0.99), True, None),
    ("syn.case_c", dict(pytest_kind="junit", passed=5, measured=1.02, reference=1.02), True, None),
    ("syn.case_d", dict(pytest_kind="none"), False, "pytest"),            # incomplete #1
    ("syn.case_e", dict(elements=("case", "method")), False, "report"),   # incomplete #2
]


def test_f1_five_known_cases_are_classified_correctly(make_case):
    validator = EvidenceChainValidator()
    rows = []
    for cap_id, kwargs, expected_complete, expected_link in F1_CASES:
        make_case(cap_id, **kwargs)
        verdict = validator.validate(load_evidence(make_case.root, cap_id))
        link_ok = (expected_link in verdict.missing) if expected_link else not verdict.missing
        rows.append((cap_id, verdict.complete, expected_complete, link_ok))
    correct = sum(1 for _, got, want, link_ok in rows if got == want and link_ok)
    assert correct == len(F1_CASES) == 5, rows
    assert sum(1 for row in rows if row[1]) == 3        # three complete
    assert sum(1 for row in rows if not row[1]) == 2    # two incomplete


def test_f1_chain_digest_changes_when_evidence_changes(make_case):
    make_case("syn.dig", measured=1.01)
    validator = EvidenceChainValidator()
    first = validator.validate(load_evidence(make_case.root, "syn.dig")).chain_digest
    make_case("syn.dig", measured=1.005)
    second = validator.validate(load_evidence(make_case.root, "syn.dig")).chain_digest
    assert first != second


def test_six_element_names_are_the_report_contract():
    assert ChainSpec().required_report_elements == SIX_ELEMENTS