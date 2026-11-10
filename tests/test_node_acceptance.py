"""Tests for the node-acceptance evidence aggregator.

All fixtures use synthetic, invented domain vocabulary ("widget", "sprocket",
"frobnicator") so a domain word can never hide in this suite - TensorPlatform
must stay domain-neutral.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.node_acceptance import (
    SIX_ELEMENTS,
    Comparator,
    Verdict,
    aggregate,
    build_acceptance_report,
    cross_validate,
    discover_all,
    evaluate_node,
    flatten_metrics,
    load_lexicon,
    node_verdict_from_outcomes,
    parse_criteria,
    parse_criterion,
    render_markdown,
    scan_node_dir,
    summarise,
)
from tools.node_acceptance.cross_validate import extract_human_claims
from tools.node_acceptance.model import Criterion, CriterionOutcome, Observation

LEX = load_lexicon()


def build_node(root: Path, files: dict[str, dict]) -> Path:
    """Create a synthetic node directory with the given relative JSON files."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, payload in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    (root / "figures").mkdir(exist_ok=True)
    (root / "figures" / "widget_flux.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return root


# ---------------------------------------------------------------------------
# 1-6: criterion parsing
# ---------------------------------------------------------------------------

def test_parse_ratio_criterion():
    crits = parse_criterion("agreement >= 4/5", lexicon=LEX)
    assert crits[0].comparator is Comparator.RATIO_GE
    assert crits[0].ratio == (4, 5)
    assert crits[0].judgeable


def test_parse_identifier_form():
    crits = parse_criterion("widget_flux_residual < 1e-3", lexicon=LEX)
    assert crits[0].comparator is Comparator.LT
    assert crits[0].threshold == pytest.approx(1e-3)
    assert crits[0].metric_name == "widget_flux_residual"
    assert crits[0].parse_confidence >= 0.9


def test_parse_chinese_duration_and_percent():
    dur = parse_criterion("sprocket 扫描+聚合耗时≤30s", lexicon=LEX)[0]
    assert dur.comparator is Comparator.LE and dur.threshold == 30.0 and dur.unit == "s"
    pct = parse_criterion("frobnicator 误差 < 3%", lexicon=LEX)[0]
    assert pct.comparator is Comparator.LT and pct.threshold == 3.0 and pct.unit == "%"


def test_parse_count_and_all_green_yields_numeric_first():
    crits = parse_criterion("pytest ≥ 12 项全绿", lexicon=LEX)
    assert crits[0].comparator is Comparator.GE and crits[0].threshold == 12.0
    assert any(c.comparator is Comparator.ALL_TRUE for c in crits)


def test_parse_approx_without_tolerance_is_not_judgeable():
    crit = parse_criterion("kappa ≈ 76", lexicon=LEX)[0]
    assert crit.comparator is Comparator.APPROX
    assert not crit.judgeable


def test_parse_presence_elements():
    crit = parse_criterion("报告生成包含六要素", lexicon=LEX)[0]
    assert crit.comparator is Comparator.PRESENT
    assert crit.judgeable


# ---------------------------------------------------------------------------
# 7-9: scanner
# ---------------------------------------------------------------------------

def test_scanner_flattens_metrics_and_hashes_artifacts(tmp_path):
    root = build_node(tmp_path / "m9001", {
        "validation_report.json": {
            "criteria": [{"description": "widget residual below tolerance",
                          "target": "widget_residual < 1e-6"}],
            "widget_residual": 4e-7,
            "verdict": "PASS",
        },
    })
    ev = scan_node_dir(root)
    assert ev.metric_count >= 2
    assert any(m.key == "widget_residual" for m in ev.metrics)
    assert any(a.path == "validation_report.json" and len(a.sha256) == 64 for a in ev.artifacts)
    assert ev.digest() == scan_node_dir(root).digest()  # deterministic


def test_scanner_missing_directory_is_reported():
    ev = scan_node_dir("/nonexistent/node/9002")
    assert ev.warnings and "node_dir_missing" in ev.warnings[0]
    assert ev.metric_count == 0


def test_flatten_metrics_marks_status_strings():
    metrics = flatten_metrics({"a": {"status": "FAIL", "passed": True, "value": 2.5}}, "x.json", LEX)
    kinds = {(m.key, m.kind, m.value) for m in metrics}
    assert ("a.status", "status", False) in kinds
    assert ("a.passed", "bool", True) in kinds
    assert ("a.value", "number", 2.5) in kinds


# ---------------------------------------------------------------------------
# 10-14: judgement rules
# ---------------------------------------------------------------------------

def _evidence_with(tmp_path, files):
    return scan_node_dir(build_node(tmp_path / "m9003", files))


def test_percent_fraction_is_rescaled(tmp_path):
    ev = _evidence_with(tmp_path, {"validation_report.json": {
        "relative_error": 0.02, "verdict": "PASS"}})
    crit = parse_criterion("frobnicator 误差 < 3%", lexicon=LEX)[0]
    outcome = aggregate(ev, [crit]).outcomes[0]
    assert outcome.verdict is Verdict.PASS
    assert outcome.observation.value == pytest.approx(2.0)
    assert outcome.observation.scaling == "fraction->percent"


def test_declared_tolerance_record_is_judged(tmp_path):
    ev = _evidence_with(tmp_path, {"validation_report.json": {
        "anchors": [{"description": "widget ledger closed", "relative_error": 1e-14,
                     "tolerance": 1e-12, "passed": True}],
    }})
    crits = discover_all(ev, LEX)
    assert crits, "record-local tolerance must be discovered"
    verdict = aggregate(ev, crits, LEX)
    assert verdict.outcomes[0].verdict is Verdict.PASS


def test_documented_deviation_becomes_mechanical_conflict(tmp_path):
    ev = _evidence_with(tmp_path, {"validation_report.json": {
        "anchors": [{
            "description": "sprocket fit residual recorded in the run log",
            "relative_error": 0.05, "tolerance": 0.03, "passed": True,
            "note": "the fit is documented as the party that exceeds 3 %, the gate is kept as-is on purpose",
        }],
    }})
    verdict = aggregate(ev, discover_all(ev, LEX), LEX)
    assert verdict.outcomes[0].verdict is Verdict.MECHANICAL_CONFLICT
    assert verdict.verdict is Verdict.INCOMPLETE


def test_undeclared_direction_violation_is_unknown_not_fail(tmp_path):
    ev = _evidence_with(tmp_path, {"validation_report.json": {
        "anchors": [{"relative_error": 0.5, "tolerance": 0.03, "note": "no flag anywhere"}],
    }})
    verdict = aggregate(ev, discover_all(ev, LEX), LEX)
    assert verdict.outcomes[0].verdict is Verdict.UNKNOWN
    assert verdict.verdict is not Verdict.FAIL


def test_node_verdict_rules():
    def o(v):
        return CriterionOutcome(criterion=Criterion(raw="x"), verdict=v)

    assert node_verdict_from_outcomes([]) is Verdict.NO_CRITERIA
    assert node_verdict_from_outcomes([o(Verdict.PASS), o(Verdict.PASS)]) is Verdict.PASS
    assert node_verdict_from_outcomes([o(Verdict.PASS), o(Verdict.UNKNOWN)]) is Verdict.INCOMPLETE
    assert node_verdict_from_outcomes([o(Verdict.PASS), o(Verdict.FAIL)]) is Verdict.FAIL
    assert node_verdict_from_outcomes([o(Verdict.MECHANICAL_CONFLICT)]) is Verdict.INCOMPLETE


# ---------------------------------------------------------------------------
# 15-16: report
# ---------------------------------------------------------------------------

def test_report_contains_six_elements_and_markdown_sections(tmp_path):
    root = build_node(tmp_path / "m9004", {"validation_report.json": {
        "criteria": [{"target": "widget_residual < 1e-6"}],
        "widget_residual": 4e-7, "verdict": "PASS"}})
    verdict, report, check = evaluate_node(str(root))
    for key in SIX_ELEMENTS:
        assert key in report, key
    assert report["six_elements_present"] == list(SIX_ELEMENTS)
    assert report["error_judgement"]["answered"] is True
    md = render_markdown(report)
    for heading in ("## 1. 案例介绍", "## 2. 方法", "## 3. 结果", "## 4. 图", "## 5. 注意点", "## 6. 误差判定"):
        assert heading in md
    assert check.machine_verdict is verdict.verdict


def test_report_without_error_criterion_declares_not_applicable(tmp_path):
    root = build_node(tmp_path / "m9005", {"validation_report.json": {
        "criteria": [{"target": "verified_cases >= 3"}],
        "verified_cases": 5, "verdict": "PASS"}})
    _, report, _ = evaluate_node(str(root))
    assert report["error_judgement"]["answered"] is False
    assert "not-applicable" in report["error_judgement"]["statement"] or \
           "cannot be applied" in report["error_judgement"]["statement"]


# ---------------------------------------------------------------------------
# 17-19: cross validation
# ---------------------------------------------------------------------------

def test_cross_validation_agreement_and_disagreement_classes(tmp_path):
    agree_root = build_node(tmp_path / "m9006", {"validation_report.json": {
        "criteria": [{"target": "widget_residual < 1e-6"}],
        "widget_residual": 4e-7, "verdict": "PASS"}})
    _, _, agree = evaluate_node(str(agree_root))
    assert agree.agreement == "agree_certified"

    bad_root = build_node(tmp_path / "m9007", {"validation_report.json": {
        "criteria": [{"target": "widget_residual < 1e-6"}],
        "widget_residual": 4e-3, "verdict": "PASS"}})
    _, _, bad = evaluate_node(str(bad_root))
    assert bad.agreement == "disagree"

    partial_root = build_node(tmp_path / "m9008", {"validation_report.json": {
        "criteria": [{"target": "widget_residual < 1e-6"}, {"target": "sprocket rig is stable"}],
        "widget_residual": 4e-7, "verdict": "PASS"}})
    _, _, partial = evaluate_node(str(partial_root))
    assert partial.agreement in ("agree_no_contradiction", "disagree")


def test_human_claim_extraction_prefers_validation_report(tmp_path):
    root = build_node(tmp_path / "m9009", {
        "validation_report.json": {"criteria": [{"target": "x < 1", "verdict": "PASS"}]},
        "benchmarks/verified/x.json": {"passed": False},
    })
    ev = scan_node_dir(root)
    items, node_claim = extract_human_claims(ev)
    assert node_claim is not None and node_claim.source == "validation_report.json"


def test_summarise_reports_all_classes():
    from tools.node_acceptance.cross_validate import NodeCrossCheck

    checks = [
        NodeCrossCheck("a", Verdict.PASS, Verdict.PASS, "agree_certified"),
        NodeCrossCheck("b", Verdict.INCOMPLETE, Verdict.PASS, "agree_no_contradiction"),
        NodeCrossCheck("c", Verdict.FAIL, Verdict.PASS, "disagree"),
        NodeCrossCheck("d", Verdict.NO_CRITERIA, Verdict.PASS, "undetermined"),
    ]
    s = summarise(checks)
    assert s["agree_certified"] == 1 and s["disagree"] == 1
    assert s["consistency"] == 2          # certified + no-contradiction
    assert s["agreement_strict"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# 20-22: scope isolation, zero-domain-vocabulary, discovery noise control
# ---------------------------------------------------------------------------

def test_scopes_are_namespaced_per_artifact(tmp_path):
    root = build_node(tmp_path / "m9010", {
        "validation_report.json": {"criteria": [{"target": "widget_residual < 1e-6"}],
                                   "widget_residual": 1e-9, "verdict": "PASS"},
        "artifacts/other.json": {"widget_residual": 9.0},
    })
    verdict, _, _ = evaluate_node(str(root))
    obs = verdict.outcomes[0].observation
    assert obs.source == "validation_report.json"      # never the decoy file
    assert verdict.outcomes[0].verdict is Verdict.PASS


def test_criteria_only_come_from_allowlisted_roles(tmp_path):
    root = build_node(tmp_path / "m9011", {
        "validation_report.json": {"verdict": "PASS"},
        "artifacts/unrelated_payload.json": {
            "criteria": [{"target": "frobnicator_residual < 1e-9"}],
            "frobnicator_residual": 5.0,
        },
    })
    ev = scan_node_dir(root)
    assert discover_all(ev, LEX) == [], "criteria must not leak from non-allowlisted artifacts"


def test_package_contains_no_domain_vocabulary():
    lex_path = Path(__file__).resolve().parents[1] / "tools" / "evidence_domain_lexicon.json"
    if not lex_path.exists():
        pytest.skip("platform domain lexicon unavailable")
    import re

    lex = json.loads(lex_path.read_text(encoding="utf-8"))
    pats = [re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in lex.get("ascii_terms", [])]
    pats += [re.compile(re.escape(t)) for t in lex.get("cjk_terms", [])]
    pkg = Path(__file__).resolve().parents[1] / "tools" / "node_acceptance"
    hits = []
    for path in pkg.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pat in pats:
            if pat.search(text):
                hits.append((path.name, pat.pattern))
    assert hits == [], f"domain vocabulary leaked into the engine: {hits}"


def test_parse_criteria_accepts_descriptor_dicts():
    crits = parse_criteria([
        {"description": "widget suite green", "target": "widget_residual < 1e-6"},
        "verified_cases >= 3",
    ], source="cli", lexicon=LEX)
    assert {c.comparator for c in crits} >= {Comparator.LT, Comparator.GE}
    assert all(c.source == "cli" for c in crits)