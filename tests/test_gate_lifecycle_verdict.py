"""C2/C3 tests: automatic adjudication (accuracy vs hand-labelled corpus) and
capability-ledger auto-registration.

The corpus in ``tools/gate_lifecycle/data/golden_cases.json`` was hand-authored
(one expected verdict + status per case) before the engine was run, so the
agreement rate is a real measure and not a self-fulfilling tautology.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.gate_lifecycle.ledger import (
    CapabilityLedger,
    build_record,
    grade_of,
    registration_completeness,
)
from tools.gate_lifecycle.model import Criterion, Node, digest
from tools.gate_lifecycle.verdict import (
    adjudicate,
    evaluate_criterion,
    evaluate_node,
    failure_accounting,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tools" / "gate_lifecycle" / "data" / "golden_cases.json"


def load_corpus():
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def node_with(criteria, node_id="n0", capability="cap"):
    return Node(node_id=node_id,
                criteria=tuple(Criterion.from_dict(c) for c in criteria),
                capability=capability)


def test_golden_corpus_accuracy_at_least_95_percent():
    corpus = load_corpus()
    tpl = corpus["templates"]
    agree, rows = 0, []
    for case in corpus["cases"]:
        node = node_with(tpl[case["tpl"]], node_id=case["id"])
        got = evaluate_node(node, case.get("m", {}), case.get("d", {}))
        ok = got.verdict == case["expect_verdict"] and got.status == case["expect_status"]
        agree += int(ok)
        rows.append((case["id"], case["expect_verdict"], case["expect_status"],
                     got.verdict, got.status, ok))
    n = len(rows)
    accuracy = agree / n
    misses = [r for r in rows if not r[5]]
    assert n >= 50, f"corpus too small: {n}"
    assert accuracy >= 0.95, f"accuracy {accuracy:.4f} ({agree}/{n}); misses={misses}"


def test_corpus_is_labelled_and_digest_stable():
    corpus = load_corpus()
    assert "hand-authored" in corpus["label_source"]
    ids = [c["id"] for c in corpus["cases"]]
    assert len(ids) == len(set(ids))
    d1 = digest(corpus)
    d2 = digest(json.loads(CORPUS.read_text(encoding="utf-8")))
    assert d1 == d2


def test_missing_metric_is_withheld_never_fail():
    node = node_with([{"name": "err", "metric": "err", "op": "le", "threshold": 0.05}])
    res = evaluate_node(node, {})
    assert res.verdict == "WITHHELD"
    assert res.status == "withheld"
    assert res.criteria[0].value is None
    assert "insufficient_evidence" in res.flags


def test_evidence_beats_declaration_overclaim_flagged():
    node = node_with([{"name": "err", "metric": "err", "op": "le", "threshold": 0.05}])
    res = evaluate_node(node, {"err": 0.2}, {"status": "pass"})
    assert res.verdict == "FAIL" and res.status == "failed"
    assert "overclaim" in res.flags


def test_honest_fail_declaration_cannot_buy_a_pass():
    node = node_with([{"name": "err", "metric": "err", "op": "le", "threshold": 0.05}])
    res = evaluate_node(node, {"err": 0.01}, {"honest_fail": True})
    assert res.verdict == "PASS" and res.status == "done"
    assert "declaration_conflict" in res.flags


def test_fail_dominates_withheld_and_optional_is_ignored():
    node = node_with([
        {"name": "a", "metric": "a", "op": "le", "threshold": 1.0},
        {"name": "b", "metric": "b", "op": "le", "threshold": 1.0},
        {"name": "c", "metric": "c", "op": "le", "threshold": 1.0, "required": False},
    ])
    assert evaluate_node(node, {"a": 2.0, "c": 99.0}).verdict == "FAIL"
    assert evaluate_node(node, {"a": 0.5}).verdict == "WITHHELD"
    assert evaluate_node(node, {"a": 0.5, "b": 0.5, "c": 99.0}).verdict == "PASS"


def test_no_required_criteria_cannot_pass():
    node = node_with([{"name": "c", "metric": "c", "op": "le", "threshold": 1.0,
                      "required": False}])
    res = evaluate_node(node, {"c": 0.1})
    assert res.verdict == "WITHHELD"
    assert "no_required_criteria" in res.flags


@pytest.mark.parametrize("raw,state", [
    (None, "WITHHELD"), (float("nan"), "WITHHELD"), (float("inf"), "WITHHELD"),
    ("not-a-number", "WITHHELD"), (True, "WITHHELD"), (0.0, "PASS"), ("0.0", "PASS"),
])
def test_metric_coercion(raw, state):
    crit = Criterion(name="x", metric="x", op="le", threshold=0.0)
    assert evaluate_criterion(crit, {"x": raw}).state == state


def test_adjudicate_returns_flags_for_optional_only():
    verdict, flags = adjudicate([evaluate_criterion(
        Criterion(name="x", metric="x", op="le", threshold=1.0, required=False), {"x": 0.0})])
    assert verdict == "WITHHELD"
    assert "only_optional_criteria" in flags


def test_failure_accounting_excludes_honest_fail():
    ok = node_with([{"name": "e", "metric": "e", "op": "le", "threshold": 0.05}], node_id="n1")
    bad = node_with([{"name": "e", "metric": "e", "op": "le", "threshold": 0.05}], node_id="n2")
    honest = node_with([{"name": "e", "metric": "e", "op": "le", "threshold": 0.05}],
                       node_id="n3")
    results = [
        evaluate_node(ok, {"e": 0.01}),
        evaluate_node(bad, {"e": 0.5}),
        evaluate_node(honest, {"e": 0.5}, {"honest_fail": True}),
        evaluate_node(honest, {}),
    ]
    acc = failure_accounting(results)
    assert acc["executed"] == 4
    assert acc["failed"] == 1
    assert acc["honest_fail"] == 1
    assert acc["withheld"] == 1
    assert acc["denominator"] == 3          # honest FAIL leaves the denominator
    assert acc["failure_rate"] == pytest.approx(1 / 3)
    assert acc["negative_track"] == ["n3"]


def test_grade_rules():
    assert grade_of({"evidence": {"baseline_delta": -0.1,
                                  "structural_guard": ["gate cannot pass without digest"]}}) == "S3"
    assert grade_of({"evidence": {"baseline_delta": -0.1}}) == "S2"
    assert grade_of({"evidence": {}}) == "S1"
    # a non-numeric delta cannot claim S2
    assert grade_of({"evidence": {"baseline_delta": "big"}}) == "S1"


def test_ledger_registration_is_complete_and_idempotent(tmp_path):
    node = node_with([{"name": "e", "metric": "e", "op": "le", "threshold": 0.05}])
    passing = evaluate_node(node, {"e": 0.01})
    failing = evaluate_node(node, {"e": 0.9})
    recs = [build_record(passing, {"baseline_delta": -0.2}, run_id="r1")]
    ledger = CapabilityLedger(tmp_path / "ledger.jsonl")
    first = ledger.register(recs)
    assert first.appended == 1 and first.records == 1
    assert first.by_grade == {"S2": 1}
    second = ledger.register(recs)
    assert second.appended == 0 and second.duplicates == 1

    comp = registration_completeness([passing, failing], ledger.read())
    assert comp["completeness"] == 1.0 and comp["missing"] == []

    # a changed result is a new revision, not a silent overwrite
    changed = build_record(evaluate_node(node, {"e": 0.02}), {"baseline_delta": -0.3}, run_id="r1")
    third = ledger.register([changed])
    assert third.appended == 1
    assert "cap" in set(ledger.latest())            # latest revision kept per capability


def test_ledger_files_are_canonical(tmp_path):
    node = node_with([{"name": "e", "metric": "e", "op": "le", "threshold": 0.05}])
    rec = build_record(evaluate_node(node, {"e": 0.01}), {}, run_id="r")
    ledger = CapabilityLedger(tmp_path / "l.jsonl")
    ledger.register([rec])
    lines = (tmp_path / "l.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert lines[0] == json.dumps(json.loads(lines[0]), sort_keys=True,
                                  separators=(",", ":"), ensure_ascii=False)