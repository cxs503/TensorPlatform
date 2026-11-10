"""Level state machine tests (p262w1-N07): promotion, demotion, hold."""
from __future__ import annotations

import pytest

from tools.capability_auto_audit.evidence_chain import ChainVerdict, CheckResult
from tools.capability_auto_audit.level_machine import (
    DOWNGRADE,
    HOLD,
    UPGRADE,
    InvalidLevel,
    decide,
    level_distribution,
    transition_table,
)


def _verdict(complete: bool, failure_kind=None, missing=("report",), cap="syn.alpha"):
    checks = [CheckResult("report", complete, "detail")]
    return ChainVerdict(capability_id=cap, complete=complete, checks=checks,
                        missing=[] if complete else list(missing),
                        failure_kind=failure_kind,
                        reason_code="CHAIN_COMPLETE" if complete else "REPORT_INCOMPLETE",
                        chain_digest="d" * 64, spec_digest="s" * 64)


@pytest.mark.parametrize("level", ["MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED"])
def test_complete_chain_upgrades_every_non_real_level(level):
    transition = decide(level, _verdict(complete=True))
    assert transition.action == UPGRADE
    assert transition.to_level == "REAL"
    assert transition.changed is True
    assert transition.reason_code == "CHAIN_COMPLETE"


def test_complete_chain_holds_an_existing_real():
    transition = decide("REAL", _verdict(complete=True))
    assert transition.action == HOLD
    assert transition.to_level == "REAL"
    assert transition.changed is False


def test_regression_demotes_real_to_mock():
    transition = decide("REAL", _verdict(complete=False, failure_kind="regression"))
    assert transition.action == DOWNGRADE
    assert transition.to_level == "MOCK"
    assert "REGRESSION" in transition.reason_code


def test_missing_evidence_demotes_real_to_withheld():
    transition = decide("REAL", _verdict(complete=False, failure_kind="evidence"))
    assert transition.action == DOWNGRADE
    assert transition.to_level == "WITHHELD"
    assert "EVIDENCE_LOSS" in transition.reason_code


@pytest.mark.parametrize("level", ["MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED"])
def test_incomplete_chain_never_pushes_a_non_real_down(level):
    transition = decide(level, _verdict(complete=False, failure_kind="evidence"))
    assert transition.action == HOLD
    assert transition.to_level == level
    assert transition.reason_code == f"CHAIN_INCOMPLETE_{level}_HELD"


def test_unknown_level_is_rejected():
    with pytest.raises(InvalidLevel):
        decide("TOTALLY_REAL", _verdict(complete=True))


def test_verdict_type_is_enforced():
    with pytest.raises(TypeError):
        decide("REAL", {"complete": True})  # type: ignore[arg-type]


def test_transition_table_covers_the_whole_policy():
    table = transition_table()
    assert len(table) == 7
    assert {row["action"] for row in table} == {UPGRADE, DOWNGRADE, HOLD}
    assert [row for row in table if row["from"] == "REAL" and row["action"] == DOWNGRADE]


def test_level_distribution_counts_every_level():
    dist = level_distribution(["REAL", "REAL", "MOCK"])
    assert dist["REAL"] == 2 and dist["MOCK"] == 1 and dist["STUB"] == 0
    assert set(dist) == {"NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"}


def test_transition_serialises_for_reports():
    payload = decide("MOCK", _verdict(complete=True)).as_dict()
    assert payload["capability_id"] == "syn.alpha"
    assert payload["changed"] is True
    assert payload["chain_digest"] == "d" * 64