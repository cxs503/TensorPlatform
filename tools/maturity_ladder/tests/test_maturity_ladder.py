"""Five-level maturity ladder — pytest suite (p251w2-n03).

**Zero-domain-semantics**: all fixtures use synthetic capability names
(``synth.*``) — no real domain vocabulary in the test code.

Coverage matrix
---------------
* Model: five-level ladder, level_rank, EvidenceBundle digest, RollbackPoint,
  LevelTransition direction, CapabilityMaturity as_dict
* Criteria: TransitionCheck evaluation (all ops, boolean, missing metric,
  non-numeric), TransitionCriteria (conjunction, level validation),
  default five-rung registry
* Engine: register, evaluate (next rung / explicit target / top-level),
  upgrade (pass / fail / honest-FAIL), downgrade, rollback, audit log,
  snapshot, level_counts
* Schema: JSON Schema validation (valid / invalid / structural fallback),
  transition record validation
* Determinism: same input → same verdict (no wall clock in verdicts)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.maturity_ladder import (  # noqa: E402
    MATURITY_LEVELS, LEVEL_RANK, MaturityLadderEngine,
    TransitionCriteria, TransitionCheck, EvidenceBundle,
    RollbackPoint, LevelTransition, level_rank,
    DEFAULT_CRITERIA, evaluate_transition, transition_checks,
    LADDER_SCHEMA, validate_ladder_schema, validate_transition_record,
    UPGRADED, DOWNGRADED, HELD, ROLLED_BACK, REJECTED,
)
from tools.maturity_ladder.model import (  # noqa: E402
    CapabilityMaturity, canonical_json, digest, now_iso,
)


# ── Model ─────────────────────────────────────────────────────────────────
def test_five_level_ladder_exact():
    assert MATURITY_LEVELS == (
        "Implemented", "Tested", "Benchmark-verified",
        "Coupled prototype", "Engineering-qualified",
    )
    assert len(MATURITY_LEVELS) == 5
    assert LEVEL_RANK["Implemented"] == 0
    assert LEVEL_RANK["Engineering-qualified"] == 4
    assert level_rank("Tested") == 1
    with pytest.raises(KeyError):
        level_rank("Not-a-level")


def test_evidence_bundle_digest_content_addressed():
    a = EvidenceBundle("synth.cap", "Tested", {"unit_test_pass_rate": 0.95})
    b = EvidenceBundle("synth.cap", "Tested", {"unit_test_pass_rate": 0.95})
    c = EvidenceBundle("synth.cap", "Tested", {"unit_test_pass_rate": 0.90})
    assert a.digest == b.digest          # same content → same digest
    assert a.digest != c.digest          # different content → different digest
    assert len(a.digest) == 64


def test_rollback_point_as_dict():
    rp = RollbackPoint("synth.cap", "Tested", "abc123")
    d = rp.as_dict()
    assert d["capability_id"] == "synth.cap"
    assert d["level_before"] == "Tested"
    assert d["evidence_digest"] == "abc123"
    assert "created_at" in d


def test_level_transition_direction():
    up = LevelTransition("t1", "synth.cap", "Implemented", "Tested")
    assert up.direction == "up"
    down = LevelTransition("t2", "synth.cap", "Tested", "Implemented")
    assert down.direction == "down"
    # rollback: to_level == rollback_point.level_before
    rp = RollbackPoint("synth.cap", "Implemented", "dig")
    rb = LevelTransition("t3", "synth.cap", "Tested", "Implemented",
                         rollback_point=rp)
    assert rb.direction == "rollback"


def test_capability_maturity_as_dict():
    cap = CapabilityMaturity("synth.cap", "Tested")
    d = cap.as_dict()
    assert d["capability_id"] == "synth.cap"
    assert d["current_level"] == "Tested"
    assert d["level_rank"] == 1
    assert d["history"] == []
    assert "updated_at" in d


# ── Criteria ──────────────────────────────────────────────────────────────
def test_transition_check_all_ops():
    c = TransitionCheck("x", ">=", 0.90)
    assert c.evaluate({"x": 0.95})["ok"] is True
    assert c.evaluate({"x": 0.85})["ok"] is False
    c2 = TransitionCheck("x", "<", 0.15)
    assert c2.evaluate({"x": 0.10})["ok"] is True
    assert c2.evaluate({"x": 0.20})["ok"] is False
    c3 = TransitionCheck("x", "==", True)
    assert c3.evaluate({"x": True})["ok"] is True
    assert c3.evaluate({"x": False})["ok"] is False
    c4 = TransitionCheck("x", "!=", 1.0)
    assert c4.evaluate({"x": 2.0})["ok"] is True
    assert c4.evaluate({"x": 1.0})["ok"] is False


def test_transition_check_missing_and_non_numeric():
    c = TransitionCheck("missing", ">=", 0.9)
    r = c.evaluate({})
    assert r["ok"] is False and "missing" in r["error"]
    c2 = TransitionCheck("text", ">=", 0.9)
    r2 = c2.evaluate({"text": "hello"})
    assert r2["ok"] is False and "not numeric" in r2["error"]
    c3 = TransitionCheck("bool", ">=", 0.9)
    r3 = c3.evaluate({"bool": True})
    assert r3["ok"] is False  # bool not numeric for >=


def test_transition_check_unknown_op():
    c = TransitionCheck("x", "≈", 0.9)
    r = c.evaluate({"x": 0.95})
    assert r["ok"] is False and "unknown operator" in r["error"]


def test_transition_criteria_conjunction():
    crit = TransitionCriteria(
        from_level="Implemented", to_level="Tested",
        checks=(
            TransitionCheck("unit_test_pass_rate", ">=", 0.90),
            TransitionCheck("code_coverage", ">=", 0.80),
        ),
    )
    ok = crit.evaluate({"unit_test_pass_rate": 0.95, "code_coverage": 0.85})
    assert ok["passed"] is True and ok["failures"] == []
    bad = crit.evaluate({"unit_test_pass_rate": 0.95, "code_coverage": 0.70})
    assert bad["passed"] is False and len(bad["failures"]) == 1


def test_transition_criteria_level_validation():
    with pytest.raises(ValueError):
        TransitionCriteria(from_level="Tested", to_level="Implemented", checks=())
    with pytest.raises(ValueError):
        TransitionCriteria(from_level="Not-a-level", to_level="Tested", checks=())


def test_default_five_rung_registry():
    assert set(DEFAULT_CRITERIA) == {"L1_TO_L2", "L2_TO_L3", "L3_TO_L4", "L4_TO_L5"}
    assert DEFAULT_CRITERIA["L1_TO_L2"].from_level == "Implemented"
    assert DEFAULT_CRITERIA["L1_TO_L2"].to_level == "Tested"
    assert DEFAULT_CRITERIA["L2_TO_L3"].to_level == "Benchmark-verified"
    assert DEFAULT_CRITERIA["L3_TO_L4"].to_level == "Coupled prototype"
    assert DEFAULT_CRITERIA["L4_TO_L5"].to_level == "Engineering-qualified"
    # Each transition has the right number of checks
    assert len(DEFAULT_CRITERIA["L1_TO_L2"].checks) == 2
    assert len(DEFAULT_CRITERIA["L2_TO_L3"].checks) == 2
    assert len(DEFAULT_CRITERIA["L3_TO_L4"].checks) == 2
    assert len(DEFAULT_CRITERIA["L4_TO_L5"].checks) == 3


def test_transition_checks_serialisable():
    d = transition_checks()
    assert isinstance(d, dict)
    assert "L1_TO_L2" in d
    assert d["L1_TO_L2"]["from_level"] == "Implemented"
    # JSON-serialisable
    json.dumps(d)


def test_evaluate_transition_pure():
    crit = DEFAULT_CRITERIA["L1_TO_L2"]
    r = evaluate_transition(crit, {"unit_test_pass_rate": 0.95, "code_coverage": 0.85})
    assert r["passed"] is True


# ── Engine ─────────────────────────────────────────────────────────────────
def _engine():
    return MaturityLadderEngine()


def _bundle(cap_id, level, **metrics):
    return EvidenceBundle(cap_id, level, metrics)


def test_engine_register_and_level():
    eng = _engine()
    cap = eng.register("synth.cap")
    assert cap.current_level == "Implemented"
    assert eng.level("synth.cap") == "Implemented"
    with pytest.raises(KeyError):
        eng.register("synth.cap")  # duplicate
    with pytest.raises(ValueError):
        eng.register("synth.bad", initial_level="Not-a-level")


def test_engine_evaluate_next_rung():
    eng = _engine()
    eng.register("synth.cap")
    # L1→L2: pass
    v = eng.evaluate("synth.cap", _bundle("synth.cap", "Tested",
                                          unit_test_pass_rate=0.95,
                                          code_coverage=0.85))
    assert v.passed is True and v.to_level == "Tested"
    # L1→L2: fail (coverage too low)
    v2 = eng.evaluate("synth.cap", _bundle("synth.cap", "Tested",
                                           unit_test_pass_rate=0.95,
                                           code_coverage=0.70))
    assert v2.passed is False and v2.to_level == "Tested"
    assert len(v2.failures) == 1


def test_engine_evaluate_explicit_target():
    eng = _engine()
    eng.register("synth.cap")
    # Can't jump to L3 from L1 (no criteria for that jump)
    v = eng.evaluate("synth.cap", _bundle("synth.cap", "Benchmark-verified",
                                          benchmark_error=0.05,
                                          convergence_verified=True),
                     target_level="Benchmark-verified")
    assert v.passed is False
    assert "no transition criteria" in v.reason


def test_engine_evaluate_top_level_noop():
    eng = _engine()
    eng.register("synth.cap", initial_level="Engineering-qualified")
    v = eng.evaluate("synth.cap", _bundle("synth.cap", "Engineering-qualified"))
    assert v.passed is False and "already at top" in v.reason


def test_engine_upgrade_success():
    eng = _engine()
    eng.register("synth.cap")
    ev = _bundle("synth.cap", "Tested", unit_test_pass_rate=0.95, code_coverage=0.85)
    tx = eng.upgrade("synth.cap", ev)
    assert tx.to_level == "Tested"
    assert tx.from_level == "Implemented"
    assert tx.direction == "up"
    assert tx.rollback_point is not None
    assert tx.rollback_point.level_before == "Implemented"
    assert eng.level("synth.cap") == "Tested"
    # Rollback point recorded
    assert len(eng.rollback_points("synth.cap")) == 1


def test_engine_upgrade_failure_honest_fail():
    eng = _engine()
    eng.register("synth.cap")
    ev = _bundle("synth.cap", "Tested", unit_test_pass_rate=0.95, code_coverage=0.70)
    tx = eng.upgrade("synth.cap", ev)
    assert tx.to_level == "Implemented"      # level unchanged
    assert tx.from_level == "Implemented"
    assert "rejected" in tx.reason
    assert eng.level("synth.cap") == "Implemented"
    # Rejection is recorded in history (honest FAIL)
    assert len(eng.capability_history("synth.cap")) == 1


def test_engine_full_ladder_climb():
    """A capability that satisfies every rung's evidence climbs to L5."""
    eng = _engine()
    eng.register("synth.cap")
    # L1→L2
    eng.upgrade("synth.cap", _bundle("synth.cap", "Tested",
                                     unit_test_pass_rate=0.95, code_coverage=0.85))
    assert eng.level("synth.cap") == "Tested"
    # L2→L3
    eng.upgrade("synth.cap", _bundle("synth.cap", "Benchmark-verified",
                                     benchmark_error=0.05, convergence_verified=True))
    assert eng.level("synth.cap") == "Benchmark-verified"
    # L3→L4
    eng.upgrade("synth.cap", _bundle("synth.cap", "Coupled prototype",
                                     coupling_test_passed=True, conservation_error=0.03))
    assert eng.level("synth.cap") == "Coupled prototype"
    # L4→L5
    eng.upgrade("synth.cap", _bundle("synth.cap", "Engineering-qualified",
                                     independent_review_passed=True,
                                     engineering_case_validated=True,
                                     uncertainty_quantified=True))
    assert eng.level("synth.cap") == "Engineering-qualified"
    # At top: no more upgrades
    assert len(eng.audit_log()) == 4
    assert len(eng.rollback_points("synth.cap")) == 4


def test_engine_downgrade():
    eng = _engine()
    eng.register("synth.cap", initial_level="Tested")
    tx = eng.downgrade("synth.cap", "evidence invalidated")
    assert tx.to_level == "Implemented"
    assert tx.direction == "down"
    assert eng.level("synth.cap") == "Implemented"
    with pytest.raises(ValueError):
        eng.downgrade("synth.cap", "cannot go below", target_level="Tested")


def test_engine_rollback():
    eng = _engine()
    eng.register("synth.cap")
    eng.upgrade("synth.cap", _bundle("synth.cap", "Tested",
                                     unit_test_pass_rate=0.95, code_coverage=0.85))
    assert eng.level("synth.cap") == "Tested"
    rp = eng.rollback_points("synth.cap")[0]
    # Rebuild a RollbackPoint from the dict
    rp_obj = RollbackPoint(
        capability_id=rp["capability_id"],
        level_before=rp["level_before"],
        evidence_digest=rp["evidence_digest"],
        created_at=rp["created_at"],
    )
    tx = eng.rollback("synth.cap", rp_obj)
    assert tx.to_level == "Implemented"
    assert tx.direction == "rollback"
    assert eng.level("synth.cap") == "Implemented"
    # Wrong capability → error (unknown capability raises KeyError)
    with pytest.raises(KeyError):
        eng.rollback("synth.other", rp_obj)


def test_engine_audit_log_and_snapshot():
    eng = _engine()
    eng.register("synth.a")
    eng.register("synth.b", initial_level="Tested")
    eng.upgrade("synth.a", _bundle("synth.a", "Tested",
                                   unit_test_pass_rate=0.95, code_coverage=0.85))
    log = eng.audit_log()
    assert len(log) == 1
    assert log[0]["capability_id"] == "synth.a"
    assert log[0]["from_level"] == "Implemented"
    assert log[0]["to_level"] == "Tested"
    snap = eng.snapshot()
    assert snap["capabilities"]["synth.a"] == "Tested"
    assert snap["capabilities"]["synth.b"] == "Tested"
    assert snap["transitions_total"] == 1
    counts = eng.level_counts()
    assert counts["Tested"] == 2
    assert counts["Implemented"] == 0


def test_engine_determinism():
    """Same evidence + same criteria → same verdict (no wall clock)."""
    eng1 = _engine()
    eng2 = _engine()
    eng1.register("synth.cap")
    eng2.register("synth.cap")
    ev = _bundle("synth.cap", "Tested", unit_test_pass_rate=0.95, code_coverage=0.85)
    v1 = eng1.evaluate("synth.cap", ev)
    v2 = eng2.evaluate("synth.cap", ev)
    assert v1.passed == v2.passed
    assert v1.to_level == v2.to_level
    assert json.dumps(v1.as_dict(), sort_keys=True) == json.dumps(v2.as_dict(), sort_keys=True)


# ── Schema ─────────────────────────────────────────────────────────────────
def _valid_declaration():
    return {
        "schema_version": "maturity-ladder/1.0",
        "ladder": list(MATURITY_LEVELS),
        "capabilities": [
            {
                "capability_id": "synth.cap",
                "current_level": "Tested",
                "level_rank": 1,
                "history": [],
                "metadata": {},
                "updated_at": "2026-11-10T00:00:00+00:00",
            }
        ],
        "transitions": [
            {
                "transition_id": "tx-1",
                "capability_id": "synth.cap",
                "from_level": "Implemented",
                "to_level": "Tested",
                "timestamp": "2026-11-10T00:00:00+00:00",
                "evidence_refs": ["abc"],
                "adjudicator": "machine",
                "rollback_point": {
                    "capability_id": "synth.cap",
                    "level_before": "Implemented",
                    "evidence_digest": "abc",
                    "created_at": "2026-11-10T00:00:00+00:00",
                },
                "reason": "all checks passed",
                "direction": "up",
            }
        ],
    }


def test_schema_valid_declaration():
    r = validate_ladder_schema(_valid_declaration())
    assert r["valid"] is True, r["errors"]


def test_schema_invalid_ladder():
    d = _valid_declaration()
    d["ladder"] = ["Implemented", "Tested"]  # wrong length
    r = validate_ladder_schema(d)
    assert r["valid"] is False


def test_schema_invalid_level():
    d = _valid_declaration()
    d["capabilities"][0]["current_level"] = "Not-a-level"
    r = validate_ladder_schema(d)
    assert r["valid"] is False


def test_schema_missing_required():
    d = _valid_declaration()
    del d["transitions"]
    r = validate_ladder_schema(d)
    assert r["valid"] is False


def test_schema_transition_record():
    t = _valid_declaration()["transitions"][0]
    r = validate_transition_record(t)
    assert r["valid"] is True
    # Invalid: bad level
    bad = dict(t)
    bad["to_level"] = "Not-a-level"
    r2 = validate_transition_record(bad)
    assert r2["valid"] is False


def test_schema_structural_fallback():
    """The structural fallback (no jsonschema) catches the same errors."""
    from tools.maturity_ladder.schema import _structural_validate
    r = _structural_validate(_valid_declaration(), LADDER_SCHEMA)
    assert r["valid"] is True
    bad = _valid_declaration()
    bad["ladder"] = ["x", "y", "z", "w", "v"]
    r2 = _structural_validate(bad, LADDER_SCHEMA)
    assert r2["valid"] is False


# ── End-to-end ─────────────────────────────────────────────────────────────
def test_end_to_end_ladder_declaration():
    """A full climb produces a valid ladder declaration (schema-valid)."""
    eng = _engine()
    eng.register("synth.cap")
    eng.upgrade("synth.cap", _bundle("synth.cap", "Tested",
                                     unit_test_pass_rate=0.95, code_coverage=0.85))
    eng.upgrade("synth.cap", _bundle("synth.cap", "Benchmark-verified",
                                     benchmark_error=0.05, convergence_verified=True))
    eng.upgrade("synth.cap", _bundle("synth.cap", "Coupled prototype",
                                     coupling_test_passed=True, conservation_error=0.03))
    eng.upgrade("synth.cap", _bundle("synth.cap", "Engineering-qualified",
                                     independent_review_passed=True,
                                     engineering_case_validated=True,
                                     uncertainty_quantified=True))
    from tools.maturity_ladder.schema import ladder_declaration
    decl = ladder_declaration(
        capabilities=[eng.get("synth.cap").as_dict()],
        transitions=eng.audit_log(),
    )
    r = validate_ladder_schema(decl)
    assert r["valid"] is True, r["errors"]
    assert decl["ladder"] == list(MATURITY_LEVELS)


# ── Structural fallback edge cases ─────────────────────────────────────────
def test_structural_fallback_non_dict():
    from tools.maturity_ladder.schema import _structural_validate
    r = _structural_validate("not-a-dict", LADDER_SCHEMA)
    assert r["valid"] is False
    assert "must be an object" in r["errors"][0]


def test_structural_fallback_missing_required():
    from tools.maturity_ladder.schema import _structural_validate
    r = _structural_validate({}, LADDER_SCHEMA)
    assert r["valid"] is False
    assert any("missing required key" in e for e in r["errors"])


def test_structural_fallback_bad_capabilities():
    from tools.maturity_ladder.schema import _structural_validate
    d = _valid_declaration()
    d["capabilities"] = "not-a-list"
    r = _structural_validate(d, LADDER_SCHEMA)
    assert r["valid"] is False
    assert any("capabilities" in e for e in r["errors"])

    d2 = _valid_declaration()
    d2["capabilities"] = [{"capability_id": "x"}]  # missing current_level/history
    r2 = _structural_validate(d2, LADDER_SCHEMA)
    assert r2["valid"] is False
    assert any("missing" in e for e in r2["errors"])

    d3 = _valid_declaration()
    d3["capabilities"] = ["not-a-dict"]
    r3 = _structural_validate(d3, LADDER_SCHEMA)
    assert r3["valid"] is False


def test_structural_fallback_bad_transitions():
    from tools.maturity_ladder.schema import _structural_validate
    d = _valid_declaration()
    d["transitions"] = "not-a-list"
    r = _structural_validate(d, LADDER_SCHEMA)
    assert r["valid"] is False

    d2 = _valid_declaration()
    d2["transitions"] = [{"transition_id": "x"}]  # missing fields
    r2 = _structural_validate(d2, LADDER_SCHEMA)
    assert r2["valid"] is False
    assert any("missing" in e for e in r2["errors"])

    d3 = _valid_declaration()
    d3["transitions"] = ["not-a-dict"]
    r3 = _structural_validate(d3, LADDER_SCHEMA)
    assert r3["valid"] is False

    d4 = _valid_declaration()
    d4["transitions"] = [dict(d4["transitions"][0], to_level="Not-a-level")]
    r4 = _structural_validate(d4, LADDER_SCHEMA)
    assert r4["valid"] is False


def test_structural_fallback_bad_ladder():
    from tools.maturity_ladder.schema import _structural_validate
    d = _valid_declaration()
    d["ladder"] = "not-a-list"
    r = _structural_validate(d, LADDER_SCHEMA)
    assert r["valid"] is False
    assert any("ladder" in e for e in r["errors"])
