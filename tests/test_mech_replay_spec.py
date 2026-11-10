"""声明式内核 / 8 机制规则表 / 独立第二实现 的单元测试。"""

from __future__ import annotations

import copy

import pytest

from tools.mech_replay import mechanisms, reference, spec
from tools.mech_replay.collector import GOOD

MECHS = list(mechanisms.MECHANISMS)


def test_mechanism_registry_covers_eight_core_mechanisms():
    assert len(MECHS) == 8
    assert set(MECHS) == {
        "capability_registration", "evidence_grade", "machine_gate", "report_gate",
        "merge_gate", "autonomy_proposal", "campaign_status", "budget_gate",
    }


def test_every_mechanism_has_rules_and_numeric_threshold():
    for name in MECHS:
        rules = mechanisms.RULES[name]
        assert len(rules) >= 4, name
        assert mechanisms.numeric_rules(name), f"{name} 缺可放宽的数值阈值"
        assert len({r.id for r in rules}) == len(rules), f"{name} 规则 id 重复"


def test_good_records_pass_all_mechanisms():
    for name in MECHS:
        verdict = mechanisms.judge(name, GOOD[name])
        if verdict["decision"] != "pass":
            pytest.fail(f"{name} 基线记录未通过: {reference.spec_codes(verdict)}")


def test_judge_is_deterministic_and_idempotent():
    for name in MECHS:
        first = mechanisms.judge(name, GOOD[name])
        second = mechanisms.judge(name, GOOD[name])
        assert spec.canon(first) == spec.canon(second), name


def test_judge_does_not_mutate_input_record():
    for name in MECHS:
        record = copy.deepcopy(GOOD[name])
        snapshot = spec.canon(record)
        mechanisms.judge(name, record)
        assert spec.canon(record) == snapshot, f"{name} 改了输入记录"


def test_verdict_carries_audit_contract():
    verdict = mechanisms.judge("merge_gate", GOOD["merge_gate"])
    audit = verdict["audit"]
    assert audit["rule_version"] == spec.RULES_VERSION
    assert audit["mechanism"] == "merge_gate"
    assert len(audit["input_digest"]) == 64
    assert audit["checks_total"] == len(mechanisms.RULES["merge_gate"])
    assert audit["checks_failed"] == 0


def test_missing_field_is_explicit_fail_not_silent_pass():
    record = dict(GOOD["merge_gate"])
    record.pop("approvals")
    verdict = mechanisms.judge("merge_gate", record)
    assert verdict["decision"] == "fail"
    assert reference.spec_codes(verdict) == [spec.CODE_MISSING_FIELD]


def test_deep_missing_field_in_collection_is_explicit_fail():
    record = {"nodes": [{"status": "in_flight", "evidence_count": 1},
                        {"status": "pending", "evidence_count": 0, "consecutive_fails": 0}]}
    verdict = mechanisms.judge("campaign_status", record)
    assert verdict["decision"] == "fail"
    assert spec.CODE_MISSING_FIELD in reference.spec_codes(verdict)


def test_zero_denominator_is_type_error_not_crash():
    record = dict(GOOD["budget_gate"])
    record["budget_tokens"] = 0
    verdict = mechanisms.judge("budget_gate", record)
    assert verdict["decision"] == "fail"
    assert reference.spec_codes(verdict) == [spec.CODE_TYPE_ERROR]


def test_threshold_boundary_flips_when_relaxed():
    """每个机制的 boundary-relax 探针必须在严格阈值下 fail、放宽 20% 后 pass。"""
    from tools.mech_replay import mutate

    for name in MECHS:
        probe = _probe_record(name, "boundary-relax")
        strict = mechanisms.judge(name, probe)
        relaxed = mechanisms.judge(name, probe, rules=mutate.relax_rules(
            mechanisms.RULES[name], 1.2))
        assert strict["decision"] == "fail", name
        assert relaxed["decision"] == "pass", name


def _probe_record(mechanism: str, probe_class: str) -> dict:
    from tools.mech_replay.collector import PROBES

    for mech, klass, record, _notes in PROBES:
        if mech == mechanism and klass == probe_class:
            return copy.deepcopy(record)
    raise KeyError((mechanism, probe_class))


def test_reference_implementation_agrees_on_all_good_records():
    for name in MECHS:
        spec_verdict = mechanisms.judge(name, GOOD[name])
        ref_verdict = reference.reference_verdict(name, GOOD[name])
        assert spec_verdict["decision"] == ref_verdict["decision"], name
        assert reference.spec_codes(spec_verdict) == ref_verdict["codes"], name


def test_reference_implementation_agrees_on_probe_matrix():
    from tools.mech_replay.collector import PROBES

    for mechanism, probe_class, record, _notes in PROBES:
        spec_verdict = mechanisms.judge(mechanism, record)
        ref_verdict = reference.reference_verdict(mechanism, record)
        assert spec_verdict["decision"] == ref_verdict["decision"], (mechanism, probe_class)
        assert reference.spec_codes(spec_verdict) == ref_verdict["codes"], (mechanism, probe_class)


def test_aggregation_is_permutation_invariant():
    """顺序置换对（同一集合换序）判词必须一致——本套 8 机制均无顺序语义。"""
    from tools.mech_replay.collector import PROBES

    pairs = {}
    for mechanism, probe_class, record, _notes in PROBES:
        if probe_class.startswith("order-pair"):
            base = probe_class.rsplit("-", 1)[0]
            pairs.setdefault((mechanism, base), []).append(record)
    assert pairs, "探针里没有顺序置换对"
    for (mechanism, _base), records in pairs.items():
        signatures = {spec.pair_signature(mechanisms.judge(mechanism, r)) for r in records}
        assert len(signatures) == 1, f"{mechanism} 判词随输入顺序变化"


def test_reduce_policy_first_breaks_permutation_invariance():
    """反证：一旦归约只看首元素，置换对立刻不一致（M3 变异确实有杀伤力）。"""
    from tools.mech_replay.collector import PROBES

    signatures = set()
    for mechanism, probe_class, record, _notes in PROBES:
        if mechanism == "campaign_status" and probe_class.startswith("order-pair"):
            signatures.add(spec.pair_signature(
                mechanisms.judge(mechanism, record, reduce_policy="first")))
    assert len(signatures) == 2


def test_status_vocabulary_drift_is_caught():
    record = {"nodes": [{"status": "verified", "evidence_count": 8, "consecutive_fails": 0},
                        {"status": "terminated", "evidence_count": 1, "consecutive_fails": 0}]}
    verdict = mechanisms.judge("campaign_status", record)
    assert "CS_STATUS_ENUM" in reference.spec_codes(verdict)


def test_campaign_audit_reports_derived_status():
    verdict = mechanisms.judge("campaign_status", GOOD["campaign_status"])
    assert verdict["audit"]["derived_status"] == "verified"


def test_budget_audit_reports_utilization():
    verdict = mechanisms.judge("budget_gate", GOOD["budget_gate"])
    assert verdict["audit"]["token_utilization"] == pytest.approx(0.5)


def test_unknown_mechanism_raises():
    with pytest.raises(KeyError):
        mechanisms.judge("no_such_mechanism", {})