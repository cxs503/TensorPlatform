"""证据等级状态机 v2 测试（C1–C7）。

零领域语义：能力 ID / 锚 ID 一律用合成名（synth.*），不含任何域词条。
运行：
    PYTHONPATH=src python -m pytest tests/capability_ledger_auto/test_evidence_state_machine.py -q
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from capability_ledger_auto.anchors import (  # noqa: E402
    UNDECIDABLE as ANCHOR_UNDECIDABLE,
    VerificationAnchor,
    judge_anchor,
)
from capability_ledger_auto.provenance import (  # noqa: E402
    DOWNGRADE,
    HOLD,
    INITIAL,
    REJECTED,
    UNDECIDABLE_LOCK,
    UPGRADE,
    ProvenanceChain,
)
from capability_ledger_auto.scanner import ScanResult, CodeEvidence, TestEvidence  # noqa: E402
from capability_ledger_auto.state_machine import (  # noqa: E402
    ALLOW,
    BLOCK,
    BLOCK_UNDECIDABLE,
    DEFAULT_POLICY,
    EXTENDED_STATES,
    RULES,
    STATE_SEMANTICS,
    UNDECIDABLE,
    CapabilityAlreadyRegistered,
    Counterexample,
    DowngradeWithoutTrigger,
    EvidenceLevelLedger,
    EvidencePolicy,
    EvidenceStateMachine,
    ManualOverrideRejected,
    Observation,
    admissible_rank,
    classify_transition,
    decide,
    digest_of,
    gate_decision,
)

TOL = DEFAULT_POLICY.tolerance  # 0.15（平台精度标准：误差 >15% 不可接受）

FIXED_CLOCK = lambda: "2026-11-10T00:00:00+00:00"  # noqa: E731


def anchor(aid, kind="analytic", err=0.02, unc=0.0, key="", has_ref=True):
    return VerificationAnchor(
        anchor_id=aid, kind=kind, rel_error=err, uncertainty=unc,
        independence_key=key or aid, has_reference_value=has_ref,
    )


def three_pass_anchors():
    return (
        anchor("a1", kind="analytic", err=0.02, key="ds-1"),
        anchor("a2", kind="experiment", err=0.05, key="ds-2"),
        anchor("a3", kind="benchmark", err=0.09, key="ds-3"),
    )


def impl_obs(cap="synth.node", **kw):
    base = dict(
        capability_id=cap, has_implementation=True, has_tests=True,
        has_reports=True, test_pass_rate=0.95, trace_count=6,
    )
    base.update(kw)
    return Observation(**base)


# ── C1：五态语义与转换条件 ─────────────────────────────────────
class TestC1StateSemantics:
    def test_all_five_states_have_definitions(self):
        for s in ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED"):
            assert s in EXTENDED_STATES
            assert STATE_SEMANTICS[s]
        assert UNDECIDABLE in EXTENDED_STATES
        assert len(EXTENDED_STATES) == 6

    def test_admissible_rank_order(self):
        order = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
        ranks = [admissible_rank(s) for s in order]
        assert ranks == sorted(ranks)
        assert admissible_rank(UNDECIDABLE) < admissible_rank("NOT_SUPPORTED")

    def test_not_supported(self):
        d = decide(Observation(capability_id="synth.void"))
        assert d.state == "NOT_SUPPORTED" and d.rule_id == "R01_NO_TRACE"

    def test_stub(self):
        d = decide(Observation(capability_id="synth.stub", has_implementation=True,
                               trace_count=1, stub_markers=("STUB: NotImplementedError",)))
        assert d.state == "STUB" and d.rule_id == "R02_STUB_MARKER"

    def test_mock_by_counterexample(self):
        obs = impl_obs(anchors=three_pass_anchors()[:1],
                       counterexamples=(Counterexample("cex-1", "新算例否证", digest_of({"x": 1}),
                                                       rel_error=0.31),))
        d = decide(obs)
        assert d.state == "MOCK" and d.rule_id == "R03_COUNTEREXAMPLE"

    def test_mock_by_test_failure(self):
        d = decide(impl_obs(test_pass_rate=0.5))
        assert d.state == "MOCK" and d.rule_id == "R04_TEST_FAILURE"

    def test_withheld_when_only_two_anchors(self):
        d = decide(impl_obs(anchors=three_pass_anchors()[:2]))
        assert d.state == "WITHHELD" and d.rule_id == "R08_WITHHELD_INSUFFICIENT_ANCHORS"

    def test_withheld_when_no_anchor(self):
        d = decide(impl_obs(has_tests=False))
        assert d.state == "WITHHELD" and d.rule_id == "R09_WITHHELD_NO_ANCHOR"

    def test_real_when_thresholds_met(self):
        d = decide(impl_obs(anchors=three_pass_anchors()))
        assert d.state == "REAL" and d.rule_id == "R06_REAL_THRESHOLD_MET"
        assert d.criteria["independent_decisive_pass_anchors"] == 3
        assert d.criteria["distinct_pass_kinds"] == 3


# ── C2：不可判态 fail-closed，不可判 ≠ WITHHELD ────────────────
class TestC2UndecidableFailClosed:
    def test_anchor_straddling_tolerance_is_undecidable(self):
        # rel_error=0.15, uncertainty=0.03 → band=0.06 跨过 tol=0.15
        v = judge_anchor(anchor("u1", err=0.15, unc=0.03), tol=TOL, coverage_z=2.0)
        assert v.verdict == ANCHOR_UNDECIDABLE
        assert v.adverse is False  # 中心估计等于判决线，不算偏坏
        # 偏坏侧：rel_error 略大于 tol 但带内
        v2 = judge_anchor(anchor("u2", err=0.16, unc=0.05), tol=TOL, coverage_z=2.0)
        assert v2.verdict == ANCHOR_UNDECIDABLE and v2.adverse is True

    def test_missing_reference_value_is_undecidable(self):
        v = judge_anchor(anchor("u3", err=None, has_ref=False), tol=TOL)
        assert v.verdict == ANCHOR_UNDECIDABLE
        assert "无参考值" in v.reason

    def test_single_undecidable_anchor_not_promoted_to_withheld(self):
        obs = impl_obs(anchors=(anchor("u4", err=0.15, unc=0.03),))
        d = decide(obs)
        assert d.state == UNDECIDABLE
        assert d.rule_id == "R07_UNDECIDABLE_INCONCLUSIVE"
        assert d.state != "WITHHELD"

    def test_adverse_undecidable_blocks_real_evidence(self):
        anchors = three_pass_anchors() + (anchor("u5", err=0.20, unc=0.10),)
        d = decide(impl_obs(anchors=anchors))
        assert d.state == UNDECIDABLE
        assert d.rule_id == "R05_UNDECIDABLE_ADVERSE"
        assert d.criteria["independent_decisive_pass_anchors"] == 3

    def test_gate_is_fail_closed(self):
        assert gate_decision("REAL") == ALLOW
        assert gate_decision("WITHHELD") == ALLOW  # 默认 min_rank=WITHHELD
        assert gate_decision("MOCK") == BLOCK
        assert gate_decision(UNDECIDABLE) == BLOCK_UNDECIDABLE
        assert gate_decision(UNDECIDABLE, min_rank=0) == BLOCK_UNDECIDABLE  # 永不放行
        assert admissible_rank(UNDECIDABLE) < admissible_rank("NOT_SUPPORTED")

    def test_undecidable_distinct_from_withheld_semantics(self):
        w = decide(impl_obs(has_tests=False))
        u = decide(impl_obs(anchors=(anchor("u6", err=0.15, unc=0.05),)))
        assert (w.state, u.state) == ("WITHHELD", UNDECIDABLE)
        assert STATE_SEMANTICS[w.state] != STATE_SEMANTICS[u.state]
        assert gate_decision(w.state) != gate_decision(u.state)


# ── C3：升级规则（≥3 独立锚，机器自动执行） ──────────────────
class TestC3UpgradeRules:
    def test_mock_to_real_needs_three_independent_anchors(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(impl_obs(anchors=three_pass_anchors()[:1]))
        assert ledger.state_of("synth.node") == "WITHHELD"
        ledger.observe(impl_obs(anchors=three_pass_anchors()[:2]))
        assert ledger.state_of("synth.node") == "WITHHELD"
        d = ledger.observe(impl_obs(anchors=three_pass_anchors()))
        assert d.state == "REAL" and d.transition == UPGRADE

    def test_duplicate_sources_do_not_count_as_independent(self):
        dup = (
            anchor("d1", kind="analytic", err=0.02, key="same-dataset"),
            anchor("d2", kind="experiment", err=0.03, key="same-dataset"),
            anchor("d3", kind="benchmark", err=0.04, key="same-dataset"),
        )
        d = decide(impl_obs(anchors=dup))
        assert d.criteria["independent_decisive_pass_anchors"] == 1
        assert d.criteria["collapsed_same_source_anchors"] == 2
        assert d.state == "WITHHELD"

    def test_single_kind_blocks_real(self):
        same_kind = (
            anchor("k1", kind="analytic", err=0.02, key="k-1"),
            anchor("k2", kind="analytic", err=0.03, key="k-2"),
            anchor("k3", kind="analytic", err=0.04, key="k-3"),
        )
        d = decide(impl_obs(anchors=same_kind))
        assert d.state == "WITHHELD"
        assert d.criteria["distinct_pass_kinds"] == 1

    def test_upgrade_requires_machine_rule_id(self):
        d = decide(impl_obs(anchors=three_pass_anchors()))
        assert d.rule_id in RULES
        assert d.criteria["independent_decisive_pass_anchors"] >= DEFAULT_POLICY.min_independent_anchors


# ── C4：降级规则（新反例触发自动降级 + 记录原因） ──────────────
class TestC4DowngradeRules:
    def test_new_counterexample_downgrades_real_to_mock(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(impl_obs(anchors=three_pass_anchors()))
        assert ledger.state_of("synth.node") == "REAL"
        cex = Counterexample("cex-2", "新基准算例否证", digest_of({"case": "b"}), rel_error=0.42)
        d = ledger.observe(impl_obs(anchors=three_pass_anchors(), counterexamples=(cex,)))
        assert d.state == "MOCK"
        assert d.transition == DOWNGRADE
        assert d.prev_state == "REAL"
        entry = ledger.chain.entries[-1]
        assert entry.rule_id == "R03_COUNTEREXAMPLE"
        assert any("反例 cex-2" in r for r in entry.reasons)

    def test_counterexample_below_tolerance_is_not_decisive(self):
        cex = Counterexample("cex-3", "轻微偏差", digest_of({"case": "c"}), rel_error=0.05)
        d = decide(impl_obs(anchors=three_pass_anchors(), counterexamples=(cex,)))
        assert d.state == "REAL"
        assert d.criteria["counterexample_count"] == 0

    def test_downgrade_without_evidence_digest_is_rejected(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(impl_obs(anchors=three_pass_anchors()))
        with pytest.raises(DowngradeWithoutTrigger):
            # 手工构造一条无证据摘要的降级观测：MOCK 由测试失败触发，但拿掉所有摘要
            ledger.observe(Observation(
                capability_id="synth.node", has_implementation=True, has_tests=True,
                test_pass_rate=0.0, trace_count=3,
            ))

    def test_undecidable_lock_transition(self):
        assert classify_transition("REAL", UNDECIDABLE) == UNDECIDABLE_LOCK
        assert classify_transition(UNDECIDABLE, "REAL") == UPGRADE
        assert classify_transition("REAL", "REAL") == HOLD
        assert classify_transition(None, "MOCK") == INITIAL

    def test_degradation_reason_recorded_in_provenance(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(impl_obs(anchors=three_pass_anchors()))
        ledger.observe(Observation(
            capability_id="synth.node", has_implementation=True, has_tests=True,
            has_reports=True, test_pass_rate=0.20, trace_count=6,
            anchors=three_pass_anchors(),
        ))
        hist = ledger.history("synth.node")
        assert [h.new_state for h in hist] == ["REAL", "MOCK"]
        assert hist[-1].transition == DOWNGRADE
        assert hist[-1].reasons


# ── C5：零人工覆盖 ─────────────────────────────────────────────
class TestC5NoManualOverride:
    def test_manual_override_always_raises(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(impl_obs())  # WITHHELD
        with pytest.raises(ManualOverrideRejected) as ei:
            ledger.manual_override("synth.node", "REAL", actor="human")
        assert ei.value.code == "MANUAL_OVERRIDE_FORBIDDEN"
        assert ledger.state_of("synth.node") == "WITHHELD"

    def test_manual_override_even_to_same_state_raises(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(impl_obs(anchors=three_pass_anchors()))
        with pytest.raises(ManualOverrideRejected):
            ledger.manual_override("synth.node", "REAL", actor="human")

    def test_override_attempt_is_audited_but_changes_nothing(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(impl_obs())
        with pytest.raises(ManualOverrideRejected):
            ledger.manual_override("synth.node", "REAL", actor="human", reason="测试想走捷径")
        entries = ledger.chain.entries
        assert entries[-1].outcome == REJECTED
        assert entries[-1].transition == REJECTED
        assert entries[-1].new_state is None
        assert entries[-1].rule_id == "R_MANUAL_OVERRIDE"
        assert ledger.verify() == (True, None)

    def test_every_change_has_machine_rule(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(Observation(capability_id="synth.void"))
        for cap, anchors in (("synth.a", ()), ("synth.b", three_pass_anchors())):
            if not anchors:
                ledger.register(impl_obs(cap=cap, has_tests=False))
            else:
                ledger.register(impl_obs(cap=cap, anchors=anchors))
        for e in ledger.chain.entries:
            assert e.rule_id in RULES
            assert e.criteria  # 判据快照非空
            assert e.actor == "machine"


# ── C6：与能力发现联动（初始等级由测试证据决定） ──────────────
class TestC6DiscoveryIntegration:
    def test_initial_state_from_scan_result(self):
        scan_pass = ScanResult(
            capability_id="synth.scan_a", code_count=2, test_count=1, report_count=1,
            has_implementation=True, has_tests=True, has_reports=True,
            test_pass_rate=0.97, report_verdicts=["PASS"],
            code_evidence=[CodeEvidence("/tmp/a.py", 1, "function", "def f", "h1", "h2")],
            test_evidence=[TestEvidence("/tmp/t.py", "test_f", 0.97, 30, 29, "h3")],
        )
        obs = Observation.from_scan(scan_pass, anchors=three_pass_anchors())
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        d = ledger.register(obs)
        assert d.state == "REAL" and d.transition == INITIAL

    def test_initial_state_from_failing_scan_is_mock(self):
        scan_fail = ScanResult(
            capability_id="synth.scan_b", code_count=1, test_count=1,
            has_implementation=True, has_tests=True, test_pass_rate=0.10,
        )
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        d = ledger.register(Observation.from_scan(scan_fail))
        assert d.state == "MOCK"

    def test_registration_is_not_free_choice(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(Observation(capability_id="synth.scan_c", has_implementation=True,
                                    trace_count=2, anchors=(anchor("only-1"),)))
        assert ledger.state_of("synth.scan_c") == "WITHHELD"
        with pytest.raises(CapabilityAlreadyRegistered):
            ledger.register(Observation(capability_id="synth.scan_c"))


# ── C7：审计日志（provenance 链） ──────────────────────────────
class TestC7Provenance:
    def test_chain_records_every_change_and_verifies(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(impl_obs(anchors=three_pass_anchors()[:1]))
        ledger.observe(impl_obs(anchors=three_pass_anchors()))
        ledger.observe(impl_obs(anchors=three_pass_anchors(),
                                counterexamples=(Counterexample("cex-9", "反例",
                                                                digest_of({"k": 9}), rel_error=0.5),)))
        assert len(ledger.chain) == 3
        assert ledger.verify() == (True, None)
        seqs = [e.seq for e in ledger.chain.entries]
        assert seqs == [0, 1, 2]
        for prev, cur in zip(ledger.chain.entries, ledger.chain.entries[1:]):
            assert cur.prev_hash == prev.entry_hash

    def test_tampering_detected(self):
        chain = ProvenanceChain(clock=FIXED_CLOCK)
        chain.append(capability_id="synth.t", prev_state=None, new_state="WITHHELD",
                     rule_id="R09_WITHHELD_NO_ANCHOR", transition=INITIAL, reasons=["r"])
        chain.append(capability_id="synth.t", prev_state="WITHHELD", new_state="REAL",
                     rule_id="R06_REAL_THRESHOLD_MET", transition=UPGRADE, reasons=["ok"])
        assert chain.verify() == (True, None)
        forged = list(chain.entries)
        forged[1] = replace(forged[1], new_state="MOCK")  # 手改等级
        ok, bad = chain.verify_entries(forged)
        assert ok is False and bad == 1

    def test_roundtrip_serialization(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        ledger.register(impl_obs(anchors=three_pass_anchors()[:1]))
        payload = ledger.chain.to_dict()
        assert payload["chain_valid"] is True
        restored = ProvenanceChain.from_dict(payload)
        assert restored.verify() == (True, None)
        assert restored.head_hash == ledger.chain.head_hash

    def test_evidence_digests_present(self):
        ledger = EvidenceLevelLedger(clock=FIXED_CLOCK)
        d = ledger.register(impl_obs(anchors=three_pass_anchors()))
        assert len(d.evidence_digests) == 3
        assert all(len(x) == 64 for x in d.evidence_digests)
        assert ledger.chain.entries[0].evidence_digests == d.evidence_digests


# ── 判据阈值的可配置性 ────────────────────────────────────────
class TestPolicyConfigurable:
    def test_custom_policy_changes_verdict(self):
        strict = EvidenceStateMachine(EvidencePolicy(min_independent_anchors=4))
        d = strict.evaluate(impl_obs(anchors=three_pass_anchors()))
        assert d.state == "WITHHELD"
        loose = EvidenceStateMachine(EvidencePolicy(tolerance=0.05))
        d2 = loose.evaluate(impl_obs(anchors=three_pass_anchors()))
        # 0.09 > 0.05 的锚变成决定性 FAIL → MOCK
        assert d2.state == "MOCK"

    def test_policy_validation(self):
        with pytest.raises(ValueError):
            EvidencePolicy(tolerance=0.0)
        with pytest.raises(ValueError):
            EvidencePolicy(coverage_z=-1.0)
        with pytest.raises(ValueError):
            EvidencePolicy(min_independent_anchors=0)

    def test_anchor_validation(self):
        with pytest.raises(ValueError):
            VerificationAnchor(anchor_id="", kind="analytic")
        with pytest.raises(ValueError):
            VerificationAnchor(anchor_id="x", kind="nonsense")
        with pytest.raises(ValueError):
            VerificationAnchor(anchor_id="x", kind="analytic", rel_error=-0.1)