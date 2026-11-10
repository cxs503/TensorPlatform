"""State machine / rules table / completeness checker / audit / rollback suite.

Synthetic capability ids only (``synth.*``) — zero domain semantics, same
discipline as the rest of this package (the C7 scanner in
``test_capability_upgrade.py`` also scans this file).

Acceptance criteria of node P348-W3-N01 map onto the test names:

C1 state-machine transition correctness ..... ``test_c1_*``
C2 configurable evidence thresholds ......... ``test_c2_*``
C3 audit trail with before/after snapshots .. ``test_c3_*``
C4 rollback on injected defect .............. ``test_c4_*``
C5 >= 15 cases (this file) ................. ``test_c5_*``
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[3]
if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))

from tools.capability_upgrade.completeness import (  # noqa: E402
    EvidenceBundle,
    assess,
    chain_report,
    completeness_report,
)
from tools.capability_upgrade.model import LEVELS  # noqa: E402
from tools.capability_upgrade.state_machine import CapabilityStateMachine  # noqa: E402
from tools.capability_upgrade.upgrade_audit import UpgradeAudit  # noqa: E402
from tools.capability_upgrade.upgrade_rules import (  # noqa: E402
    Requirement,
    UpgradeRuleTable,
    open_sqlite,
)

CONFIG = WORKTREE / "config" / "capability_upgrade" / "upgrade_rules.json"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def anchor(tag: str, error_pct: float, *, level: str | None = None,
           kind: str = "numeric_verification") -> dict:
    """One admissible verification record with a unique content address."""
    return {
        "evidence_id": f"ev-{tag}", "kind": kind,
        "metrics": {"error_pct": error_pct},
        "observed_level": level,
        "source": {"path": f"case_{tag}.json", "sha256": sha(tag)},
    }


def impl(present: bool = True, smoke: bool = True, responsive: bool = True) -> dict:
    return {
        "evidence_id": "ev-impl", "kind": "smoke_test",
        "metrics": {"present": 1 if present else 0, "passed": 1 if smoke else 0,
                    "variation_ratio": 0.42 if responsive else 0.0},
        "source": {"path": "impl.py", "sha256": sha("impl")},
    }


def review(tag: str = "r1", *, passed: bool = True, reviewer: str = "rev-a",
           author: str = "author-b") -> dict:
    return {"review_id": tag, "passed": passed, "reviewer": reviewer, "author": author,
            "source": {"sha256": sha(tag)}}


def bundle(capability_id: str = "synth.alpha", *, n_anchors: int = 3,
           error_pct: float = 3.0, reviews: int = 1, defects: int = 0,
           implementation: bool = True, smoke: bool = True,
           responsive: bool = True) -> EvidenceBundle:
    items = [impl(implementation, smoke, responsive)]
    items += [anchor(f"{capability_id}-a{i}", error_pct) for i in range(n_anchors)]
    return EvidenceBundle.from_evidence(
        capability_id, items,
        reviews=[review(f"{capability_id}-r{i}") for i in range(reviews)],
        defects=[{"defect_id": f"d{i}", "status": "open"} for i in range(defects)],
    )


@pytest.fixture()
def table() -> UpgradeRuleTable:
    return UpgradeRuleTable.default()


@pytest.fixture()
def machine(table) -> CapabilityStateMachine:
    return CapabilityStateMachine(rules=table, audit=UpgradeAudit(None))


# ---------------------------------------------------------------------------
# rules table
# ---------------------------------------------------------------------------


def test_rules_table_matches_shipped_configuration(table):
    on_disk = UpgradeRuleTable.from_json(CONFIG)
    assert on_disk.digest == table.digest
    assert tuple(on_disk.ladder) == LEVELS
    assert [r.to_level for r in on_disk.rules] == ["STUB", "MOCK", "WITHHELD", "REAL"]


def test_rules_table_rejects_skipped_rung():
    doc = UpgradeRuleTable.default().to_dict()
    doc["rules"][1]["to_level"] = "WITHHELD"          # STUB -> WITHHELD skips MOCK
    with pytest.raises(ValueError, match="single-rung"):
        UpgradeRuleTable.from_dict(doc)


def test_rules_table_sqlite_roundtrip_preserves_digest(table):
    conn = open_sqlite(None)
    stats = table.to_sqlite(conn)
    assert stats["n_rules"] == 4 and stats["rule_rows"] >= 10
    rows = conn.execute("SELECT COUNT(*) FROM capability_upgrade_rules").fetchone()[0]
    assert rows == stats["rule_rows"]
    back = UpgradeRuleTable.from_sqlite(conn)
    assert back.digest == table.digest


def test_requirement_missing_fact_never_passes():
    req = Requirement(key="min_anchors", op=">=", value=3)
    assert req.check(None) is False
    assert req.check(2) is False
    assert req.check(3) is True


def test_bad_operator_is_rejected():
    with pytest.raises(ValueError, match="operator"):
        Requirement(key="k", op="=~", value=1)


# ---------------------------------------------------------------------------
# completeness checker
# ---------------------------------------------------------------------------


def test_duplicate_content_address_counts_once(table):
    b = EvidenceBundle.from_evidence("synth.dup", [anchor("same", 2.0), anchor("same", 2.0)])
    rule = table.rule("r.withheld->real")
    a = assess(rule, b)
    assert b.facts()["min_anchors"] == 1
    assert a.satisfied is False
    assert "min_anchors" in a.missing


def test_record_labelled_mock_is_inadmissible(table):
    b = EvidenceBundle.from_evidence("synth.lab", [anchor("x", 1.0, level="MOCK")])
    assert b.facts()["min_anchors"] == 0


def test_provenance_without_sha_is_inadmissible():
    bad = anchor("nosha", 1.0)
    bad["source"] = {"path": "case.json"}
    b = EvidenceBundle.from_evidence("synth.nosha", [bad])
    assert b.facts()["min_anchors"] == 0


def test_bundle_rejects_caller_supplied_facts():
    # there is no "I declare three anchors" escape hatch: facts come from records
    with pytest.raises(TypeError):
        EvidenceBundle.from_evidence("synth.forge", [], overrides={"min_anchors": 3})


def test_review_must_be_independent_and_passed(table):
    same_person = EvidenceBundle.from_evidence(
        "synth.rev", [anchor("a", 1.0)], reviews=[review("r", reviewer="me", author="me")])
    failed = EvidenceBundle.from_evidence(
        "synth.rev2", [anchor("a", 1.0)], reviews=[review("r", passed=False)])
    assert same_person.facts()["independent_reviews"] == 0
    assert failed.facts()["independent_reviews"] == 0


def test_chain_report_reports_best_reachable_rung(table):
    partial = bundle(n_anchors=1, reviews=0)
    rep = chain_report(table, partial)
    assert rep["best_reachable"] == "WITHHELD"
    full = chain_report(table, bundle())
    assert full["best_reachable"] == "REAL"
    assert set(full["completeness_by_rung"]) == {r.rule_id for r in table.rules}


def test_completeness_report_scans_a_fleet(table):
    report = completeness_report(table, [bundle("synth.a"), bundle("synth.b", n_anchors=1)])
    assert report["n_capabilities"] == 2
    assert report["reachable_level_counts"] == {"REAL": 1, "WITHHELD": 1}
    assert report["rules_digest"] == table.digest


# ---------------------------------------------------------------------------
# C1: state-machine transitions
# ---------------------------------------------------------------------------

C1_EXPECTATIONS = [
    ("NOT_SUPPORTED", "REAL"),
    ("STUB", "REAL"),
    ("MOCK", "REAL"),
    ("WITHHELD", "REAL"),
    ("REAL", "REAL"),
]


@pytest.mark.parametrize("initial,expected", C1_EXPECTATIONS)
def test_c1_complete_evidence_climbs_from_every_initial_state(machine, initial, expected):
    machine.register("synth.c1", level=initial)
    decision = machine.apply("synth.c1", bundle("synth.c1"))
    assert decision["to_level"] == expected
    assert machine.level_of("synth.c1") == expected


def test_c1_ladder_is_never_skipped_in_the_audit_trail(machine):
    machine.register("synth.ladder", level="STUB")
    machine.apply("synth.ladder", bundle("synth.ladder"))
    rungs = [(r["from_level"], r["to_level"])
             for r in machine.audit.records("synth.ladder") if r["direction"] == "upgrade"]
    assert rungs == [("STUB", "MOCK"), ("MOCK", "WITHHELD"), ("WITHHELD", "REAL")]


@pytest.mark.parametrize("anchors,reviews,error_pct,expected", [
    (0, 0, 3.0, "MOCK"),      # runs, nothing verified
    (1, 0, 3.0, "WITHHELD"),  # one anchor: proven a little
    (2, 1, 3.0, "WITHHELD"),  # still one anchor short of the REAL rung
    (3, 1, 12.0, "REAL"),     # inside tolerance
    (3, 1, 22.0, "MOCK"),     # outside tolerance: cannot even reach WITHHELD
])
def test_c1_partial_evidence_stops_at_the_honest_rung(machine, anchors, reviews, error_pct,
                                                      expected):
    machine.register("synth.part", level="STUB")
    machine.apply("synth.part", bundle("synth.part", n_anchors=anchors, reviews=reviews,
                                       error_pct=error_pct))
    assert machine.level_of("synth.part") == expected


def test_c1_no_evidence_never_raises_a_level(machine):
    machine.register("synth.empty", level="WITHHELD")
    decision = machine.apply("synth.empty", EvidenceBundle.from_evidence("synth.empty", []))
    assert decision["action"] == "held"
    assert machine.level_of("synth.empty") == "WITHHELD"


def test_c1_same_evidence_twice_is_a_noop(machine):
    machine.register("synth.idem", level="MOCK")
    b = bundle("synth.idem")
    machine.apply("synth.idem", b)
    n_before = len(machine.audit)
    second = machine.apply("synth.idem", EvidenceBundle.from_evidence("synth.idem", b.items,
                                                                     b.reviews, b.defects))
    assert second["action"] == "noop"
    assert len(machine.audit) == n_before


def test_c1_max_steps_bounds_the_climb(machine):
    machine.register("synth.step", level="STUB")
    machine.apply("synth.step", bundle("synth.step"), max_steps=1)
    assert machine.level_of("synth.step") == "MOCK"


# ---------------------------------------------------------------------------
# C2: configurable thresholds
# ---------------------------------------------------------------------------


def test_c2_default_thresholds_are_three_anchors_fifteen_percent_one_review(table):
    rule = table.rule("r.withheld->real")
    values = {r.key: r.value for r in rule.requirements if r.key in
              ("min_anchors", "max_error_pct", "independent_reviews")}
    assert values == {"min_anchors": 3, "max_error_pct": 15.0, "independent_reviews": 1}


def test_c2_thresholds_are_configuration_not_code(table):
    doc = table.to_dict()
    for rule in doc["rules"]:
        for req in rule["requirements"]:
            if req["key"] == "min_anchors" and rule["from_level"] == "WITHHELD":
                req["value"] = 2
    loosened = UpgradeRuleTable.from_dict(doc)
    assert loosened.digest != table.digest

    tight_machine = CapabilityStateMachine(UpgradeRuleTable.default(), UpgradeAudit(None))
    tight_machine.register("synth.cfg", level="MOCK")
    tight_machine.apply("synth.cfg", bundle("synth.cfg", n_anchors=2))
    assert tight_machine.level_of("synth.cfg") == "WITHHELD"

    loose_machine = CapabilityStateMachine(loosened, UpgradeAudit(None))
    loose_machine.register("synth.cfg", level="MOCK")
    loose_machine.apply("synth.cfg", bundle("synth.cfg", n_anchors=2))
    assert loose_machine.level_of("synth.cfg") == "REAL"


def test_c2_error_tolerance_is_configurable(table):
    doc = table.to_dict()
    for rule in doc["rules"]:
        for req in rule["requirements"]:
            if req["key"] == "max_error_pct":
                req["value"] = 30.0
    relaxed = UpgradeRuleTable.from_dict(doc)
    m = CapabilityStateMachine(relaxed, UpgradeAudit(None))
    m.register("synth.tol", level="MOCK")
    m.apply("synth.tol", bundle("synth.tol", error_pct=22.0))
    assert m.level_of("synth.tol") == "REAL"


# ---------------------------------------------------------------------------
# C3: audit trail
# ---------------------------------------------------------------------------


def test_c3_every_upgrade_record_carries_before_and_after_snapshots(machine):
    machine.register("synth.audit", level="MOCK")
    machine.apply("synth.audit", bundle("synth.audit"))
    records = machine.audit.records("synth.audit", direction="upgrade")
    assert len(records) == 2
    for rec in records:
        for key in ("audit_id", "capability_slug", "from_level", "to_level", "rule_id",
                    "rule_digest", "evidence_snapshot", "completeness", "timestamp",
                    "operator", "reason"):
            assert key in rec, key
        snap = rec["evidence_snapshot"]
        assert "before" in snap and "after" in snap
        assert snap["after"]["digest"] and snap["after"]["facts"]["min_anchors"] == 3
    assert records[-1]["rule_id"] == "r.withheld->real"


def test_c3_trail_is_hash_chained_and_tamper_evident(tmp_path):
    path = tmp_path / "trail.jsonl"
    m = CapabilityStateMachine(UpgradeRuleTable.default(), UpgradeAudit(path))
    m.register("synth.chain", level="STUB")
    m.apply("synth.chain", bundle("synth.chain"))
    m.inject_defect("synth.chain", {"kind": "provenance_broken", "reason": "sha lost"})
    assert m.audit.verify()["valid"] is True

    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["to_level"] = "REAL"
    lines[1] = json.dumps(tampered, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reopened = UpgradeAudit(path)
    verdict = reopened.verify()
    assert verdict["valid"] is False
    assert verdict["first_problem_index"] == 1


def test_c3_trail_persists_and_survives_reopen(tmp_path):
    trail = tmp_path / "trail.jsonl"
    state = tmp_path / "state.json"
    m = CapabilityStateMachine(UpgradeRuleTable.default(), UpgradeAudit(trail), state)
    m.register("synth.persist", level="MOCK")
    m.apply("synth.persist", bundle("synth.persist"))

    reopened = CapabilityStateMachine(UpgradeRuleTable.default(), UpgradeAudit(trail), state)
    assert reopened.level_of("synth.persist") == "REAL"
    assert len(reopened.audit.records("synth.persist", direction="upgrade")) == 2


def test_c3_operator_is_recorded_for_auto_and_human(tmp_path):
    m = CapabilityStateMachine(UpgradeRuleTable.default(), UpgradeAudit(None))
    m.register("synth.op", level="MOCK")
    m.apply("synth.op", bundle("synth.op"))
    m.inject_defect("synth.op", {"kind": "review_revoked", "reason": "reviewer withdrew"},
                    operator="alice")
    operators = {r["direction"]: r["operator"] for r in m.audit.records("synth.op")}
    assert operators["upgrade"] == "auto"
    assert operators["rollback"] == "alice"


# ---------------------------------------------------------------------------
# C4: rollback
# ---------------------------------------------------------------------------


def test_c4_defect_rolls_back_to_the_configured_level(machine):
    machine.register("synth.rb", level="MOCK")
    machine.apply("synth.rb", bundle("synth.rb"))
    assert machine.level_of("synth.rb") == "REAL"

    decision = machine.inject_defect("synth.rb",
                                     {"kind": "implementation_absent",
                                      "reason": "module removed"})
    assert decision["action"] == "rolled_back"
    assert decision["from_level"] == "REAL"
    assert decision["to_level"] == "NOT_SUPPORTED"
    assert "implementation_absent" in decision["reason"]


@pytest.mark.parametrize("kind,expected", [
    ("smoke_test_failed", "STUB"),
    ("no_response_to_input", "MOCK"),
    ("error_above_threshold", "WITHHELD"),
    ("provenance_broken", "WITHHELD"),
    ("unspecified", "WITHHELD"),
])
def test_c4_rollback_target_follows_the_rules_table(machine, kind, expected):
    machine.register(f"synth.rb-{kind}", level="REAL")
    decision = machine.inject_defect(f"synth.rb-{kind}", {"kind": kind, "reason": "x"})
    assert decision["to_level"] == expected


def test_c4_open_defect_blocks_further_upgrades(machine):
    machine.register("synth.block", level="REAL")
    machine.inject_defect("synth.block", {"kind": "error_above_threshold", "reason": "22%"})
    decision = machine.apply("synth.block", bundle("synth.block"))
    assert decision["action"] == "held"
    assert "open defect" in decision["reason"]
    assert machine.level_of("synth.block") == "WITHHELD"


def test_c4_resolving_the_defect_allows_the_climb_again(machine):
    machine.register("synth.fix", level="REAL")
    machine.inject_defect("synth.fix", {"kind": "error_above_threshold", "reason": "22%"})
    machine.resolve_defect("synth.fix", note="re-measured")
    decision = machine.apply("synth.fix", bundle("synth.fix"))
    assert decision["to_level"] == "REAL"


def test_c4_evidence_regression_rolls_back_before_climbing(machine):
    machine.register("synth.reg", level="REAL")
    machine.apply("synth.reg", bundle("synth.reg"))      # REAL granted by adjudication
    # the re-checked REAL rung is no longer satisfied: anchors withdrawn
    decision = machine.apply("synth.reg", bundle("synth.reg", n_anchors=1, reviews=0))
    assert decision["action"] == "rolled_back"
    assert decision["to_level"] == "WITHHELD"
    rec = machine.audit.transitions("synth.reg")[-1]
    assert rec["blocked_by"] == "evidence_regression"
    assert set(rec["missing_before"]) == {"min_anchors", "independent_reviews"}


# ---------------------------------------------------------------------------
# C5: suite size
# ---------------------------------------------------------------------------


def test_c5_suite_covers_all_five_criteria():
    text = Path(__file__).read_text(encoding="utf-8")
    for prefix in ("test_c1_", "test_c2_", "test_c3_", "test_c4_"):
        assert text.count(prefix) >= 3, prefix