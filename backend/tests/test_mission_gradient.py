"""Tests for the mission gradient & exploration-budget mechanism (IN19-Q6).

Fixture vocabulary is deliberately synthetic (``dom-alpha``, ``w-beta-2``) so the
suite proves the mechanism is domain-free: nothing here depends on — or
hard-codes — any tenant domain's words.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from services import mission_gradient as mg

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"


# ───────────────────────────── fixtures ─────────────────────────────────

def _honest_fail(worker="w-alpha-1", mission="dom-alpha/m-002", tier="S4",
                 root_cause="dom-alpha step 3 diverged from the reference trace",
                 retrospective=("Cause: fixture scale mismatch propagated into the solver; "
                                "next time validate the synthetic fixture size before the run."),
                 evidence="artifact://dom-alpha/run-002/divergence.json"):
    return {
        "worker_id": worker,
        "mission_id": mission,
        "tier": tier,
        "status": "honest_fail",
        "root_cause": root_cause,
        "retrospective": retrospective,
        "evidence_ref": evidence,
    }


def _plain_fail(worker="w-alpha-1", mission="dom-alpha/m-003", tier="S2"):
    return {"worker_id": worker, "mission_id": mission, "tier": tier, "status": "failed",
            "root_cause": "fixture path typo", "retrospective": "none"}


def _success(worker="w-alpha-1", mission="dom-alpha/m-001", tier="S1"):
    return {"worker_id": worker, "mission_id": mission, "tier": tier, "status": "succeeded"}


# ─────────────────────────────── mix ────────────────────────────────────

def test_default_mix_is_the_registered_starting_ratio():
    mix = mg.validate_mix(None)
    assert mix == {"S1": 0.60, "S2": 0.25, "S3": 0.10, "S4": 0.04, "S5": 0.01}
    assert mg.validate_mix(mg.DEFAULT_MIX) == mix
    assert abs(sum(mix.values()) - 1.0) <= mg.MIX_TOL
    # 60/25/10/4/1 over 100 slots is exact
    assert mg.allocate_quota(100, mix) == {"S1": 60, "S2": 25, "S3": 10, "S4": 4, "S5": 1}


@pytest.mark.parametrize("bad,fragment", [
    ({"S1": 0.5, "S2": 0.2}, "sum to 1.0"),                 # 0.7 total
    ({"S1": 0.6, "S2": 0.6}, "sum to 1.0"),                 # 1.2 total
    ({"S1": -0.1, "S2": 0.6, "S3": 0.5}, "non-negative"),
    ({"S1": 0.5, "S2": 0.5, "S9": 0.0}, "unknown gradient tier"),
    ({}, "non-empty"),
    ({"S1": "0.6", "S2": "0.4"}, "must be a number"),
    ({"S1": 0.5, "S2": 0.5, "S3": float("nan")}, "must be finite"),
])
def test_mix_validation_rejects_invalid_distributions(bad, fragment):
    with pytest.raises(mg.MixError, match=fragment):
        mg.validate_mix(bad)


def test_int_tier_keys_are_accepted_as_aliases():
    # `{1: 0.5, "S3": 0.5}` means S1=0.5, S3=0.5 — a legal 1.0 distribution
    mix = mg.validate_mix({1: 0.5, "S3": 0.5})
    assert mix["S1"] == 0.5 and mix["S3"] == 0.5 and mix["S2"] == 0.0


def test_mix_is_adjustable_and_versioned_by_signature():
    adjusted = {"S1": 0.5, "S2": 0.3, "S3": 0.15, "S4": 0.04, "S5": 0.01}
    mix = mg.validate_mix(adjusted)
    assert mix["S1"] == 0.5
    assert mg.mix_signature(mix) != mg.mix_signature(mg.DEFAULT_MIX)
    assert mg.mix_signature(mg.validate_mix(adjusted)) == mg.mix_signature(mix)  # stable
    # a mix may legitimately run without S5 slots
    no_s5 = mg.validate_mix({"S1": 0.6, "S2": 0.25, "S3": 0.1, "S4": 0.05})
    assert no_s5["S5"] == 0.0


@pytest.mark.parametrize("total,mix", [
    (100, None),
    (20, None),
    (7, None),
    (0, None),
    (13, {"S1": 0.5, "S2": 0.3, "S3": 0.15, "S4": 0.04, "S5": 0.01}),
    (333, None),
])
def test_quota_allocation_is_exhaustive_and_deterministic(total, mix):
    q = mg.allocate_quota(total, mix)
    assert sum(q.values()) == total, "quota must consume exactly the requested slots"
    assert set(q) == set(mg.GRADIENT_TIERS)
    assert q == mg.allocate_quota(total, mix), "same input -> same plan (replayable)"
    # 20 slots: floors give 12/5/2/0/0 = 19, remainder 1 goes to the largest fraction (S4)
    if total == 20 and mix is None:
        assert q == {"S1": 12, "S2": 5, "S3": 2, "S4": 1, "S5": 0}


def test_quota_rejects_negative_total():
    with pytest.raises(mg.GradientError):
        mg.allocate_quota(-1)


# ───────────────────────── credit-tier dispatch ─────────────────────────

def test_min_credit_level_follows_the_tier_index():
    assert [mg.min_credit_level(t) for t in mg.GRADIENT_TIERS] == [1, 2, 3, 4, 5]
    assert mg.normalise_credit_level("L4") == 4
    assert mg.normalise_credit_level(4) == 4
    assert mg.normalise_credit_level("5") == 5
    with pytest.raises(mg.GradientError):
        mg.normalise_credit_level(6)


def test_s4_requires_l4_plus_and_s5_requires_l5():
    workers = [{"worker_id": "w-alpha-1", "credit_level": 3, "inflight": 0},
               {"worker_id": "w-beta-2", "credit_level": 4, "inflight": 0}]
    d4 = mg.dispatch_decide({"mission_id": "dom-alpha/m-010", "tier": "S4"}, workers)
    assert d4["allowed"] is True and d4["reason"] == "ok"
    assert d4["chosen"]["worker_id"] == "w-beta-2"
    assert d4["required_level"] == 4
    assert any(r["reason"] == "credit_below_required" for r in d4["rejected"])

    # S5 to an L4-only pool: hard gate, no fallback
    d5 = mg.dispatch_decide({"mission_id": "dom-alpha/m-011", "tier": "S5"}, workers)
    assert d5["allowed"] is False and d5["reason"] == "credit_gate_blocked"
    assert d5["required_level"] == 5 and d5["chosen"] is None

    d5_ok = mg.dispatch_decide(
        {"mission_id": "dom-alpha/m-011", "tier": "S5"},
        [{"worker_id": "w-gamma-3", "credit_level": "L5", "inflight": 2}])
    assert d5_ok["allowed"] is True and d5_ok["chosen"]["worker_id"] == "w-gamma-3"


def test_dispatch_prefers_credit_then_load_then_stable_id():
    pool = [{"worker_id": "w-beta-2", "credit_level": 4, "inflight": 1},
            {"worker_id": "w-alpha-1", "credit_level": 4, "inflight": 0},
            {"worker_id": "w-delta-4", "credit_level": 4, "inflight": 0}]
    d = mg.dispatch_decide({"mission_id": "dom-beta/m-020", "tier": "S4"}, pool)
    assert d["chosen"]["worker_id"] == "w-alpha-1"  # lightest load, then smallest id
    busy = [{"worker_id": "w-alpha-1", "credit_level": 4, "inflight": 2, "max_inflight": 2}]
    d2 = mg.dispatch_decide({"mission_id": "dom-beta/m-021", "tier": "S4"}, busy)
    assert d2["allowed"] is False and d2["reason"] == "no_available_worker"


def test_dispatch_refuses_when_the_seeding_share_is_exhausted():
    pool = [{"worker_id": "w-alpha-1", "credit_level": 5, "inflight": 0}]
    d = mg.dispatch_decide({"mission_id": "dom-beta/m-022", "tier": "S5"}, pool,
                           quota_remaining={"S5": 0})
    assert d["allowed"] is False and d["reason"] == "tier_share_exhausted"
    # same worker, S1 quota still open -> allowed and the counter is echoed back
    d1 = mg.dispatch_decide({"mission_id": "dom-beta/m-023", "tier": "S1"}, pool,
                            quota_remaining={"S1": 3})
    assert d1["allowed"] is True and d1["quota_remaining"] == 3
    assert mg.dispatch_decide({"mission_id": "x", "tier": "S7"}, pool)["reason"] == "invalid_tier"


# ─────────────────────── exploration budget marker ──────────────────────

def test_exploration_marker_exists_only_on_s4_and_s5():
    for tier in ("S1", "S2", "S3"):
        m = mg.exploration_budget(tier, {"max_tool_calls": 80})
        assert m["exploration"] is False and m["failure_quarantined"] is False
        assert m["honest_fail_allowed"] is False
    s4 = mg.exploration_budget("S4", {"max_tool_calls": 100})
    s5 = mg.exploration_budget("S5", {"max_tool_calls": 100})
    assert s4["exploration"] is True and s4["failure_quarantined"] is True
    assert s4["honest_fail_allowed"] is True
    # 0.04/0.05 = 0.8 of the exploration pool sits on S4, 0.01/0.05 = 0.2 on S5
    assert s4["share_within_exploration"] == pytest.approx(0.8)
    assert s5["share_within_exploration"] == pytest.approx(0.2)
    assert s4["reserve_tool_calls"] == 80 and s5["reserve_tool_calls"] == 20
    assert set(s4["honest_fail_requires"]) == {"root_cause", "retrospective",
                                               "negative_result_entry"}


def test_mission_payload_roundtrip_admission():
    payload = mg.mission_gradient_payload("S3", {"max_tool_calls": 40})
    assert mg.gradient_defaults_ok(payload) is True
    assert payload["gradient"]["min_credit_level"] == 3
    tampered = json.loads(json.dumps(payload))
    tampered["gradient"]["exploration"] = True       # lie: S3 cannot explore
    assert mg.gradient_defaults_ok(tampered) is False
    assert mg.gradient_defaults_ok({"gradient": {"tier": "S9"}}) is False
    assert mg.gradient_defaults_ok(None) is False


# ─────────────────── honest FAIL accounting (append-only) ───────────────

def test_honest_fail_is_quarantined_from_the_failure_rate():
    acct = mg.failure_accounting([_success(), _plain_fail(), _honest_fail()])
    w = acct["workers"][0]
    assert w["attempts"] == 3
    assert w["failures"] == 1                       # the ordinary failure only
    assert w["quarantined_honest_fails"] == 1
    assert w["disciplined_attempts"] == 2
    assert w["rate"] == pytest.approx(0.5)          # 1 failure / 2 disciplined attempts
    assert w["naive_rate"] == pytest.approx(2 / 3)  # the flat counter would have said so
    assert acct["aggregate"]["rate"] == pytest.approx(0.5)
    assert w["negative_results"][0]["root_cause"].startswith("dom-alpha step 3")


def test_honest_fail_without_root_cause_or_retrospective_counts_as_failure():
    thin_rc = _honest_fail(root_cause="oops")
    thin_retro = _honest_fail(mission="dom-alpha/m-004", retrospective="will try harder")
    acct = mg.failure_accounting([thin_rc, thin_retro])
    w = acct["workers"][0]
    assert w["quarantined_honest_fails"] == 0
    assert w["failures"] == 2
    assert w["disqualified_honest_fails"] == 2
    assert w["rate"] == 1.0
    assert mg.classify_outcome(thin_rc)["reason"] == "honest_fail_evidence_incomplete"
    assert "root_cause shorter" in " ".join(mg.classify_outcome(thin_rc)["problems"])


def test_execute_improve_build_tiers_can_never_claim_honest_fail():
    for tier in ("S1", "S2", "S3"):
        v = mg.classify_outcome(_honest_fail(tier=tier))
        assert v["quarantined"] is False
        assert v["reason"] == "no_exploration_budget_on_tier"
    acct = mg.failure_accounting([_honest_fail(tier="S3")])
    assert acct["workers"][0]["failures"] == 1
    assert acct["aggregate"]["rate"] == 1.0


def test_negative_result_track_requires_an_admissible_honest_fail():
    entry = mg.negative_result_entry(_honest_fail(), reuse_tags=["fixture-scale", "fixture-scale", " "])
    assert entry["track"] == "S-N" and entry["tier"] == "S4"
    assert entry["reuse_tags"] == ["fixture-scale"]      # deduped, blanks dropped
    assert entry["immutable"] is True
    with pytest.raises(mg.GradientError):
        mg.negative_result_entry(_honest_fail(root_cause="short"))
    with pytest.raises(mg.GradientError):
        mg.negative_result_entry(_honest_fail(tier="S2"))


def test_honest_fail_cannot_be_overwritten():
    assert mg.guard_honest_fail(None, "succeeded")["allowed"] is True
    assert mg.guard_honest_fail({"status": "failed"}, "succeeded")["allowed"] is True
    guard = mg.guard_honest_fail({"mission_id": "dom-alpha/m-002", "status": "honest_fail"},
                                 "succeeded")
    assert guard["allowed"] is False and guard["reason"] == "honest_fail_is_append_only"
    # re-asserting the same verdict is a no-op, not an overwrite
    assert mg.guard_honest_fail({"status": "honest_fail"}, "honest_fail")["allowed"] is True


# ────────────────────── self declaration + Q1 gate ──────────────────────

def test_mechanism_declares_itself_s3():
    payload = mg.self_declare("dom-alpha/self-1")
    assert payload["declared_tier"] == "S3"
    assert payload["gradient"]["exploration"] is False           # no exploration budget
    assert payload["gradient"]["min_credit_level"] == 3
    assert payload["gradient"]["self_declared"] is True
    assert mg.gradient_defaults_ok(payload) is True


def test_q1_gate_passes_only_when_every_criterion_is_machine_checked_and_true():
    ok = [{"id": "pytest_count", "description": "pytest >= 6", "passed": True, "evidence": "12 passed"},
          {"id": "mix_sum", "description": "mix sums to 1", "passed": True, "evidence": "1.0"}]
    verdict = mg.evaluate_q1(ok, min_criteria=2)
    assert verdict["q1_verdict"] == "pass" and verdict["criteria_passed"] == 2
    assert verdict["honest_fail_override_allowed"] is False

    bad = [dict(ok[0]), {"id": "gating", "description": "S5 needs L5", "passed": False}]
    v2 = mg.evaluate_q1(bad, min_criteria=2)
    assert v2["q1_verdict"] == "not_passed" and "gating" in v2["reason"]

    unverified = [{"id": "eyeball", "description": "looks fine", "passed": None}]
    v3 = mg.evaluate_q1(unverified)
    assert v3["q1_verdict"] == "not_passed"
    assert v3["criteria"][0]["machine_checked"] is False

    assert mg.evaluate_q1([], min_criteria=6)["q1_verdict"] == "not_passed"


# ─────────────────── domain-free + migration contract ───────────────────

DOMAIN_WORDS = ["阻力", "推进", "耐波", "操纵", "稳性", "结构", "振动", "噪声", "冰区", "绿色"]
DOMAIN_WORDS_EN = ["resistance", "propulsion", "seakeeping", "maneuvering", "hydrostatics",
                   "vibration", "noise", "hull", "propeller", "slamming"]


def test_mechanism_source_carries_no_domain_vocabulary():
    sources = [
        BACKEND / "services" / "mission_gradient.py",
        BACKEND / "api" / "mission_gradient.py",
        BACKEND / "migrations" / "103_mission_gradient.sql",
    ]
    for path in sources:
        assert path.exists(), f"missing artefact: {path}"
        blob = path.read_text(encoding="utf-8")
        hits = [w for w in DOMAIN_WORDS if w in blob]
        hits += [w for w in DOMAIN_WORDS_EN if re.search(rf"\b{w}", blob, re.IGNORECASE)]
        assert not hits, f"{path.name} hard-codes domain vocabulary: {hits}"


def test_migration_declares_the_mechanism_contract():
    sql = (BACKEND / "migrations" / "103_mission_gradient.sql").read_text(encoding="utf-8")
    required = [
        "gradient_tier", "exploration_budget", "honest_fail", "fail_root_cause",
        "retrospective", "mission_gradient_mix", "worker_credit_tiers",
        "mission_negative_results", "worker_failure_rate_v1",
        "ck_mission_gradient_mix_sum", "trg_mission_negative_results_append_only",
    ]
    missing = [token for token in required if token not in sql]
    assert not missing, f"migration missing: {missing}"
    # append-only + idempotency discipline
    assert "BEFORE UPDATE OR DELETE ON mission_negative_results" in sql
    assert "DROP TABLE" not in sql.upper()
    assert sql.count("ADD COLUMN IF NOT EXISTS") >= 6
    assert "BEGIN;" in sql and "COMMIT;" in sql

# ────────────────────── API surface + wiring gate ───────────────────────

def test_api_router_exposes_the_mechanism_endpoints():
    from api import mission_gradient as api  # noqa: PLC0415  (import locally: needs app env)

    routes = {(r.path, m) for r in api.router.routes for m in getattr(r, "methods", ())}
    expected = {
        ("/tensor/mission-gradient/mix", "GET"),
        ("/tensor/mission-gradient/mix", "PUT"),
        ("/tensor/mission-gradient/quota", "GET"),
        ("/tensor/mission-gradient/missions/{mission_id}/gradient", "POST"),
        ("/tensor/mission-gradient/missions/{mission_id}/gradient", "GET"),
        ("/tensor/mission-gradient/workers/{worker_id}/credit", "PUT"),
        ("/tensor/mission-gradient/dispatch/preview", "POST"),
        ("/tensor/mission-gradient/negative-results", "POST"),
        ("/tensor/mission-gradient/negative-results", "GET"),
        ("/tensor/mission-gradient/failure-rate", "GET"),
        ("/tensor/mission-gradient/q1/evaluate", "POST"),
        ("/tensor/mission-gradient/self-declaration", "GET"),
    }
    assert expected <= routes, f"missing endpoints: {sorted(expected - routes)}"
    assert api.router.prefix == "/tensor/mission-gradient"


def test_wiring_is_feature_flagged_so_the_live_dispatch_path_is_untouched():
    main_src = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert 'os.getenv("TP_MISSION_GRADIENT", "0") == "1"' in main_src
    gate = main_src.index('os.getenv("TP_MISSION_GRADIENT"')
    mount = main_src.index("_mission_gradient.router")
    assert gate < mount, "router must be mounted only behind the flag"


def test_postgres_verification_artifact_has_no_failing_check():
    """The migration is probed on a real PG scratch db (scripts/mission_gradient_pg_verify.py).

    Skipped runs (no local PostgreSQL) are tolerated; a generated record must be
    fully green — a half-verified DDL contract is not evidence.
    """
    path = REPO_ROOT / "artifacts" / "mission_gradient" / "pg_verify.json"
    if not path.exists():
        pytest.skip("pg_verify.json not generated in this worktree")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("skipped"):
        pytest.skip(f"no local PostgreSQL: {data.get('reason')}")
    assert data["all_passed"] is True, [
        c["check"] for c in data["checks"] if not c["passed"]
    ]
    assert data["checks_total"] >= 15
    assert any(c["check"] == "migration_is_idempotent" for c in data["checks"])


def test_router_mounts_under_the_api_prefix_when_the_flag_is_on():
    """The integration line in main.py is `include_router(router, prefix="/api/v1")`."""
    from fastapi import FastAPI  # noqa: PLC0415

    from api import mission_gradient as api  # noqa: PLC0415

    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    paths = set(app.openapi()["paths"])
    assert "/api/v1/tensor/mission-gradient/mix" in paths
    assert "/api/v1/tensor/mission-gradient/dispatch/preview" in paths
