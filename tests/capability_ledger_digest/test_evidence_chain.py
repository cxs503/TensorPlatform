"""Evidence-chain tests: three-gate adjudication, missing evidence, digest stability."""
from __future__ import annotations

import pytest

from capability_ledger_digest.evidence_chain import build_chain, chain_table
from capability_ledger_digest.spec import EvidenceRecord, Thresholds

THR = Thresholds()


def _record(combo: dict) -> EvidenceRecord:
    return EvidenceRecord(
        capability_id=f"cap.{combo['combo']}",
        impl_present=True,
        test_pass_rate=combo["test_pass_rate"],
        benchmark_error_pct=combo["benchmark_error_pct"],
        review_verdict=combo["review_verdict"],
    ).validate()


# ---------------------------------------------------------------- criterion (b)
def test_gate_matrix_adjudication_is_100pct_consistent(gate_matrix):
    thresholds = Thresholds(**gate_matrix["thresholds"])
    disagreements = []
    for combo in gate_matrix["combinations"]:
        chain = build_chain(_record(combo), thresholds)
        if chain.chain_complete != combo["expected_chain_complete"]:
            disagreements.append(
                (combo["combo"], chain.chain_complete, combo["expected_chain_complete"])
            )
    assert len(gate_matrix["combinations"]) == 8
    assert disagreements == [], disagreements


def test_upgrade_only_when_all_three_items_pass(gate_matrix):
    thresholds = Thresholds(**gate_matrix["thresholds"])
    passed_words = []
    for combo in gate_matrix["combinations"]:
        chain = build_chain(_record(combo), thresholds)
        flagged = sum(1 for item in chain.items if item.passed)
        # an upgrade happens iff the chain is complete iff all three items pass
        assert chain.chain_complete == (flagged == 3)
        if chain.chain_complete:
            passed_words.append(combo["combo"])
    assert passed_words == ["111"]


def test_each_failing_item_alone_blocks_upgrade():
    for item in ("test_pass_rate", "benchmark_error_pct", "independent_review"):
        rec = EvidenceRecord(
            capability_id="cap.blocked",
            test_pass_rate=0.95,
            benchmark_error_pct=1.0,
            review_verdict="pass",
        )
        broken = {
            "test_pass_rate": dict(test_pass_rate=0.5),
            "benchmark_error_pct": dict(benchmark_error_pct=16.0),
            "independent_review": dict(review_verdict="fail"),
        }[item]
        chain = build_chain(EvidenceRecord(**{**rec.__dict__, **broken}), THR)
        assert chain.chain_complete is False
        assert chain.failed_items == (item,)


def test_missing_measurement_counts_as_failure():
    chain = build_chain(
        EvidenceRecord(
            capability_id="cap.missing",
            test_pass_rate=None,
            benchmark_error_pct=None,
            review_verdict="missing",
        ),
        THR,
    )
    assert chain.chain_complete is False
    assert set(chain.failed_items) == {
        "test_pass_rate",
        "benchmark_error_pct",
        "independent_review",
    }
    notes = {i.item: i.note for i in chain.items}
    assert "no executable test evidence" in notes["test_pass_rate"]


def test_chain_digest_is_deterministic_and_sensitive():
    base = EvidenceRecord(capability_id="cap.d", test_pass_rate=0.9,
                          benchmark_error_pct=2.0, review_verdict="pass")
    d1 = build_chain(base, THR).sha256()
    d2 = build_chain(base, THR).sha256()
    assert d1 == d2
    flipped = build_chain(
        EvidenceRecord(**{**base.__dict__, "benchmark_error_pct": 19.0}), THR
    )
    assert flipped.sha256() != d1
    # threshold change must also move the digest (no silent re-basing)
    other_thr = Thresholds(benchmark_error_max_pct=25.0)
    assert build_chain(base, other_thr).sha256() != d1


def test_chain_table_renders_every_capability(gate_matrix):
    chains = [build_chain(_record(c), THR) for c in gate_matrix["combinations"]]
    table = chain_table(chains)
    lines = [ln for ln in table.splitlines() if ln.startswith("cap.")]
    assert len(lines) == len(chains)
    assert "cap.111" in table and "PASS" in table and "FAIL" in table