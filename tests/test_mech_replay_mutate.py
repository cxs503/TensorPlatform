"""变异器测试：6 类退化注入、检出率、以及「等价变异体不得误报」的负控。"""

from __future__ import annotations

import json
from pathlib import Path

from tools.mech_replay import mechanisms, mutate, replay

ART = Path(__file__).resolve().parents[1] / "artifacts" / "mech_replay"
FIXTURES = ART / "fixtures"


def test_six_mutation_classes_declared():
    assert set(mutate.MUTATION_CLASSES) == {
        "M1_THRESHOLD_RELAX", "M2_EXCEPTION_SWALLOW", "M3_ORDER_SENSITIVE",
        "M4_IDEMPOTENCY_BREAK", "M5_AUDIT_FIELD_LOSS", "M6_CONST_SHORTCUT",
    }
    assert set(mutate.CHANNEL_REQUIREMENTS) == set(mutate.MUTATION_CLASSES)


def test_relax_rules_respects_direction():
    rules = mechanisms.RULES["merge_gate"]
    relaxed = mutate.relax_rules(rules, 1.2)
    by_id = {r.id: r for r in rules}
    for rule in relaxed:
        original = by_id[rule.id]
        if original.threshold is None or original.direction not in ("min", "max"):
            assert rule.threshold == original.threshold
            continue
        if original.direction == "min":
            assert rule.threshold < original.threshold
        else:
            assert rule.threshold > original.threshold


def test_relax_rules_does_not_touch_original_tuple():
    rules = mechanisms.RULES["merge_gate"]
    snapshot = [r.threshold for r in rules]
    mutate.relax_rules(rules, 1.5)
    assert [r.threshold for r in mechanisms.RULES["merge_gate"]] == snapshot


def test_mutant_universe_has_enough_samples_per_class():
    mutants = mutate.build_mutants()
    per_class: dict[str, int] = {}
    for mutant in mutants:
        per_class[mutant.klass] = per_class.get(mutant.klass, 0) + 1
    assert set(per_class) == set(mutate.MUTATION_CLASSES)
    for klass, count in per_class.items():
        assert count >= 10, (klass, count)
    assert len({m.sample_id for m in mutants}) == len(mutants)


def test_mutant_class_covers_expected_mechanism_breadth():
    mutants = mutate.build_mutants()
    for klass in ("M1_THRESHOLD_RELAX", "M2_EXCEPTION_SWALLOW", "M4_IDEMPOTENCY_BREAK",
                  "M5_AUDIT_FIELD_LOSS", "M6_CONST_SHORTCUT"):
        covered = {m.mechanism for m in mutants if m.klass == klass}
        assert covered == set(mechanisms.MECHANISMS), klass


def test_suite_detects_every_mutant_in_required_channel():
    fixtures = replay.load_fixtures(FIXTURES)
    report = mutate.run_suite(fixtures)
    assert report["all_detected"] is True
    for klass, row in report["classes"].items():
        assert row["samples"] >= 10, klass
        assert row["detection_rate"] == 1.0, (klass, row["undetected"])
        assert row["required_channel_ok"] is True, (klass, row["channel_detected"])


def test_equivalent_mutant_is_not_falsely_detected():
    """负控：与基线**等价**的变异体必须判「未检出」，否则检出率是吹出来的。"""
    fixtures = replay.load_fixtures(FIXTURES, "merge_gate")
    identity = mutate.Mutant(
        sample_id="CONTROL:merge_gate:identity",
        mechanism="merge_gate", klass="CONTROL", variant="identity",
        judge=lambda record: mechanisms.judge("merge_gate", record),
    )
    result = mutate.detect(identity, fixtures)
    assert result["detected"] is False
    assert result["evidence"] == []


def test_detect_reports_channel_evidence():
    fixtures = replay.load_fixtures(FIXTURES, "report_gate")
    mut = next(m for m in mutate.build_mutants(["report_gate"])
               if m.variant == "const-pass")
    result = mutate.detect(mut, fixtures)
    assert result["detected"] is True
    assert result["decision_flip"] is True
    assert any(e.startswith("decision_flip:") for e in result["evidence"])


def test_stateful_mutant_detected_by_second_call_only():
    fixtures = replay.load_fixtures(FIXTURES, "budget_gate")
    mut = next(m for m in mutate.build_mutants(["budget_gate"])
               if m.variant == "audit_nonce")
    result = mutate.detect(mut, fixtures)
    assert result["detected"] is True
    assert result["channels"]["idempotency"] is True
    assert result["decision_flip"] is False


def test_matrix_covers_all_mechanisms_and_classes():
    report = mutate.run_suite(replay.load_fixtures(FIXTURES))
    grid = mutate.matrix(report["samples"])
    assert len(grid) == 8
    for mechanism, row in grid.items():
        for klass, cell in row.items():
            assert cell["detected"] == cell["samples"], (mechanism, klass)


def test_mutation_cli_returns_zero_and_writes_json(tmp_path):
    out = tmp_path / "mut.json"
    assert mutate.main(["--fixtures", str(FIXTURES), "--mechanism", "merge_gate",
                        "--json-out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["all_detected"] is True or payload["mutation_detected"] == payload["mutation_samples"]
    assert payload["matrix"]
