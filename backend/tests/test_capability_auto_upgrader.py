"""Test suite of the capability auto-upgrader (P362-W2-N01).

Covers the promotion gate (window, both thresholds and their boundaries), the
regression/demotion path and its floor, the anti-gaming guards (replay, flapping,
insufficient evidence), the hash-chained ledger (completeness, idempotence, tamper
detection in both stores), the ``audit_capabilities`` listener, and the
zero-domain-semantics guard.

Every fixture is synthetic (``synth.*`` capability ids, synthetic junit files);
no domain vocabulary appears in this file — which the last test verifies.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.capability_auto_upgrader import (  # noqa: E402
    LEVELS,
    BenchmarkEvidence,
    CapabilityAutoUpgrader,
    EvidenceError,
    EvidenceRun,
    TestEvidence,
    UpgradePolicy,
    analyse,
    canonical_json,
    decide,
    judge_run,
)
from services.capability_auto_upgrader import ledger as ledger_mod  # noqa: E402
from services.capability_auto_upgrader import scanner  # noqa: E402
from services.capability_auto_upgrader.watcher import AuditSnapshot  # noqa: E402

CAP = "synth.bench_alpha"
PKG_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "capability_auto_upgrader")


# ------------------------------------------------------------------ helpers ---
def make_run(
    seq,
    passed=40,
    failed=0,
    errors=0,
    measured=1.02,
    reference=1.0,
    n_samples=32,
    report_sha="a" * 64,
    cap=CAP,
):
    return EvidenceRun(
        capability_id=cap,
        seq=seq,
        tests=TestEvidence(passed=passed, failed=failed, errors=errors, report_sha256=report_sha),
        benchmark=BenchmarkEvidence(measured=measured, reference=reference, n_samples=n_samples),
    )


def write_junit(path, cases):
    """Write a minimal junit report: ``cases`` = [(kind, name)] with kind in pass/fail/skip/error."""
    body = []
    for i, (kind, name) in enumerate(cases):
        if kind == "pass":
            body.append(f'<testcase classname="synth" name="{name}" time="0.01"/>')
        elif kind == "fail":
            body.append(
                f'<testcase classname="synth" name="{name}" time="0.01">'
                f'<failure message="assertion">boom</failure></testcase>'
            )
        elif kind == "error":
            body.append(
                f'<testcase classname="synth" name="{name}" time="0.01">'
                f'<error message="exception">boom</error></testcase>'
            )
        elif kind == "skip":
            body.append(
                f'<testcase classname="synth" name="{name}" time="0.0"><skipped/></testcase>'
            )
        else:  # pragma: no cover - guard against typos in tests
            raise AssertionError(kind)
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" '
        f'tests="{len(cases)}">' + "".join(body) + "</testsuite></testsuites>"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    return path


def open_upgrader(tmp_path, **policy_kw):
    policy = UpgradePolicy(**policy_kw) if policy_kw else UpgradePolicy()
    return CapabilityAutoUpgrader(tmp_path, policy=policy)


def seed(up, level="MOCK", cap=CAP):
    return up.ingest_audit_snapshot({"capabilities": [{"capability_id": cap, "level": level}]})


def promote(up, cap=CAP, start_seq=1):
    """Feed three qualifying runs and return the last record_run result."""
    result = None
    for offset in range(3):
        result = up.record_run(make_run(start_seq + offset, cap=cap))
    return result


# ------------------------------------------------------- policy & model ------
def test_policy_defaults_match_the_node_rule():
    policy = UpgradePolicy()
    assert policy.promotion_window == 3
    assert policy.min_pass_rate == pytest.approx(0.90)
    assert policy.max_error_pct == pytest.approx(15.0)
    assert policy.min_level == "MOCK"
    assert len(policy.digest) == 64
    assert policy.digest == UpgradePolicy().digest


@pytest.mark.parametrize(
    "kwargs",
    [
        {"promotion_window": 0},
        {"demotion_window": -1},
        {"min_pass_rate": 1.5},
        {"max_error_pct": 0},
        {"min_level": "REAL"},
        {"min_level": "nonsense"},
    ],
)
def test_policy_rejects_invalid_configuration(kwargs):
    with pytest.raises(EvidenceError):
        UpgradePolicy(**kwargs)


def test_policy_rejects_unknown_keys_and_round_trips():
    with pytest.raises(EvidenceError):
        UpgradePolicy.from_mapping({"promotion_windows": 3})
    policy = UpgradePolicy(promotion_window=4, max_error_pct=7.5)
    assert UpgradePolicy.from_mapping(policy.as_dict()).digest == policy.digest


def test_junit_report_becomes_test_evidence(tmp_path):
    report = write_junit(
        tmp_path / "junit.xml",
        [("pass", "t1"), ("pass", "t2"), ("pass", "t3"), ("fail", "t4"), ("error", "t5"), ("skip", "t6")],
    )
    evidence = TestEvidence.from_junit_xml(report)
    assert (evidence.passed, evidence.failed, evidence.errors, evidence.skipped) == (3, 1, 1, 1)
    assert evidence.executed == 5                # skips carry no verdict
    assert evidence.pass_rate == pytest.approx(3 / 5)
    assert len(evidence.report_sha256) == 64


def test_junit_missing_or_unparsable_report_raises(tmp_path):
    with pytest.raises(EvidenceError):
        TestEvidence.from_junit_xml(tmp_path / "absent.xml")
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuite><testcase>", encoding="utf-8")
    with pytest.raises(EvidenceError):
        TestEvidence.from_junit_xml(broken)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"measured": 1.0, "reference": 0.0, "n_samples": 10},
        {"measured": float("nan"), "reference": 1.0, "n_samples": 10},
        {"measured": 1.0, "reference": 1.0, "n_samples": 0},
        {"measured": 1.0, "reference": 1.0, "n_samples": 10, "source_sha256": "not-a-hash"},
    ],
)
def test_benchmark_evidence_rejects_untrustworthy_records(kwargs):
    with pytest.raises(EvidenceError):
        BenchmarkEvidence(**kwargs)


def test_error_pct_is_derived_not_declared():
    evidence = BenchmarkEvidence(measured=1.08, reference=1.0, n_samples=16)
    assert evidence.error_pct == pytest.approx(8.0)
    record = evidence.as_dict()
    record["error_pct"] = 0.0                     # a producer trying to certify itself
    assert BenchmarkEvidence.from_record(record).error_pct == pytest.approx(8.0)


# ----------------------------------------------------- digest / dedupe -------
def test_run_digest_is_content_addressed_and_replay_is_detected():
    a = make_run(1)
    b = EvidenceRun(
        capability_id=CAP,
        seq=1,
        tests=a.tests,
        benchmark=a.benchmark,
        recorded_at="2000-01-01T00:00:00+00:00",   # different wall clock only
    )
    assert a.digest == b.digest
    from services.capability_auto_upgrader.model import dedupe_runs

    assert len(dedupe_runs([a, b])) == 1


def test_dedupe_rejects_two_different_runs_sharing_a_sequence_number():
    from services.capability_auto_upgrader.model import dedupe_runs

    with pytest.raises(EvidenceError):
        dedupe_runs([make_run(2, passed=40), make_run(2, passed=41)])


# --------------------------------------------------------------- promotion ---
def test_no_promotion_before_the_window_is_satisfied(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        up.record_run(make_run(1))
        result = up.record_run(make_run(2))
        assert result["status"] == "hold"
        assert up.levels()[CAP] == "MOCK"
        assert result["decision"]["reason_codes"] == ["pass_streak=2<3"]


def test_promotion_to_real_after_three_consecutive_qualifying_runs(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        result = promote(up)
        assert result["status"] == "applied"
        assert result["decision"]["action"] == "promote"
        assert up.levels()[CAP] == "REAL"

        upgrade_events = [e for e in up.ledger.events() if e["direction"] == ledger_mod.DIRECTION_UPGRADE]
        assert len(upgrade_events) == 1
        event = upgrade_events[0]
        for key in ledger_mod.REQUIRED_KEYS:
            assert key in event, key
        assert event["level_before"] == "MOCK" and event["level_after"] == "REAL"
        assert len(event["sha256"]) == 64 and len(event["prev_sha256"]) == 64
        assert event["prev_sha256"] == up.ledger.events()[0]["sha256"]     # chain intact
        assert event["policy_digest"] == up.policy.digest
        assert len(event["window_digests"]) == 3
        assert event["evidence"]["run"]["digest"] == result["run_digest"]
        assert event["evidence"]["policy"] == up.policy.as_dict()
        assert up.verify()["ok"] is True


@pytest.mark.parametrize("passed,failed,expected_level", [(90, 10, "REAL"), (89, 11, "MOCK")])
def test_pass_rate_boundary_is_ninety_percent(tmp_path, passed, failed, expected_level):
    with open_upgrader(tmp_path) as up:
        seed(up)
        result = None
        for seq in range(1, 4):
            result = up.record_run(make_run(seq, passed=passed, failed=failed))
        assert result["streaks"]["pass"] == (3 if expected_level == "REAL" else 0)
        assert up.levels()[CAP] == expected_level


def test_accuracy_boundary_is_strictly_greater_than_fifteen_percent(tmp_path):
    policy = UpgradePolicy()
    on_the_bound = make_run(1, measured=1.15)
    assert round(on_the_bound.benchmark.error_pct, 6) == pytest.approx(15.0)
    assert judge_run(on_the_bound, policy).kind == "pass"          # 15.0 % is accepted

    over = make_run(1, measured=1.1501)
    assert judge_run(over, policy).kind == "fail_accuracy"


def test_window_counts_distinct_runs_only(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        run = make_run(1)
        first = up.record_run(run)
        replays = [up.record_run(make_run(1)) for _ in range(3)]
        assert first["status"] == "hold"
        assert all(r["status"] == "duplicate_run" for r in replays)
        assert up.levels()[CAP] == "MOCK"
        analysis = analyse(up.store.runs(CAP), up.policy)
        assert analysis.pass_streak == 1


def test_flapping_never_promotes(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        for seq in range(1, 7):
            up.record_run(make_run(seq, passed=40 if seq % 2 else 30, failed=0 if seq % 2 else 10))
        assert up.levels()[CAP] == "MOCK"
        assert not [e for e in up.ledger.events() if e["direction"] == ledger_mod.DIRECTION_UPGRADE]


# -------------------------------------------------------------- regression ---
def test_regression_demotes_one_rung_without_rewriting_history(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        promote(up)
        assert up.levels()[CAP] == "REAL"

        failing = make_run(4, passed=30, failed=10, measured=1.4)
        result = up.record_run(failing)
        assert result["status"] == "applied"
        assert result["decision"]["action"] == "demote"
        assert up.levels()[CAP] == "WITHHELD"

        events = up.ledger.events()
        downgrades = [e for e in events if e["direction"] == ledger_mod.DIRECTION_DOWNGRADE]
        assert len(downgrades) == 1
        assert downgrades[0]["level_before"] == "REAL"
        assert downgrades[0]["level_after"] == "WITHHELD"
        assert downgrades[0]["trigger_digest"] == failing.digest
        assert "test_pass_rate_below_floor" in downgrades[0]["reason_codes"]

        # history is append-only: the earlier promotion is still there, unchanged
        upgrades = [e for e in events if e["direction"] == ledger_mod.DIRECTION_UPGRADE]
        assert len(upgrades) == 1 and upgrades[0]["level_after"] == "REAL"
        assert up.verify()["ok"] is True


def test_demotion_stops_at_the_floor_and_never_declares_an_empty_shell(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        promote(up)
        for seq, failed in ((4, 10), (5, 11), (6, 12)):
            up.record_run(make_run(seq, passed=30, failed=failed, measured=1.4))
        assert up.levels()[CAP] == "MOCK"          # REAL -> WITHHELD -> MOCK -> floor
        last = up.ledger.events()[-1]
        result = up.record_run(make_run(7, passed=30, failed=13, measured=1.5))
        assert result["decision"]["action"] == "hold"
        assert "demotion_floor_reached" in result["decision"]["reason_codes"]
        assert last["level_after"] == "MOCK"


def test_demotion_window_is_configurable(tmp_path):
    with open_upgrader(tmp_path, demotion_window=2) as up:
        seed(up)
        promote(up)
        first = up.record_run(make_run(4, passed=30, failed=10, measured=1.4))
        assert first["decision"]["action"] == "hold"
        assert up.levels()[CAP] == "REAL"
        second = up.record_run(make_run(5, passed=30, failed=11, measured=1.4))
        assert second["decision"]["action"] == "demote"
        assert up.levels()[CAP] == "WITHHELD"


def test_insufficient_evidence_breaks_the_streak_but_never_demotes(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        promote(up)
        result = up.record_run(make_run(4, passed=0, failed=0, errors=0))
        assert result["decision"]["action"] == "hold"
        assert "insufficient_evidence_blocks_demotion" in result["decision"]["reason_codes"]
        assert up.levels()[CAP] == "REAL"
        assert result["streaks"]["insufficient"] == 1 and result["streaks"]["fail"] == 0


def test_sequence_numbers_must_be_monotonic(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        up.record_run(make_run(1))
        with pytest.raises(EvidenceError):
            up.record_run(make_run(3))


# ------------------------------------------------------------------ ledger ---
def test_ledger_append_is_idempotent_per_decision(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        promote(up)
        decision = decide("MOCK", analyse(up.store.runs(CAP), up.policy), up.policy)
        event = {
            "capability_id": CAP,
            "direction": ledger_mod.DIRECTION_UPGRADE,
            "level_before": decision.level_before,
            "level_after": decision.level_after,
            "reason_codes": list(decision.reason_codes),
            "policy_digest": up.policy.digest,
            "window_digests": list(decision.window_digests),
            "trigger_digest": up.store.runs(CAP)[-1].digest,
            "operator": "test",
        }
        before = len(up.ledger.events())
        assert up.ledger.append(event)["noop"] is True
        assert len(up.ledger.events()) == before


def test_ledger_rejects_incomplete_or_unexplained_events(tmp_path):
    with open_upgrader(tmp_path) as up:
        with pytest.raises(ledger_mod.LedgerError):
            up.ledger.append({"capability_id": CAP})
        with pytest.raises(ledger_mod.LedgerError):
            up.ledger.append(
                {
                    "capability_id": CAP,
                    "direction": ledger_mod.DIRECTION_UPGRADE,
                    "level_before": "MOCK",
                    "level_after": "REAL",
                    "reason_codes": [],
                    "policy_digest": up.policy.digest,
                    "window_digests": [],
                    "trigger_digest": "x",
                    "operator": "test",
                }
            )


def test_ledger_detects_tampering_in_the_jsonl(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        promote(up)
        lines = up.ledger.events_path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[-1])
        tampered["level_after"] = "REAL"
        tampered["level_before"] = "STUB"
        lines[-1] = canonical_json(tampered)
        up.ledger.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = up.verify()
        assert report["ok"] is False
        assert any("sha256 mismatch" in p for p in report["ledger"]["problems"])


def test_ledger_detects_tampering_in_the_sqlite_mirror(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        promote(up)
        row = up.ledger.rows()[0]
        payload = dict(row["payload"])
        payload["level_after"] = "REAL"
        up.ledger._conn.execute(
            "UPDATE feature_ledger SET payload = ? WHERE ledger_id = ?",
            (canonical_json(payload), row["ledger_id"]),
        )
        up.ledger._conn.commit()
        report = up.verify()
        assert report["ok"] is False
        assert any("SQLite payload differs" in p for p in report["ledger"]["problems"])


# ------------------------------------------------------------ audit listener --
def test_audit_listener_registers_capabilities_and_records_changes(tmp_path):
    with open_upgrader(tmp_path) as up:
        first = up.ingest_audit_snapshot(
            {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "capabilities": [
                    {"capability_id": "synth.audit_beta", "level": "MOCK"},
                    {"capability_id": "synth.audit_gamma", "level": "WITHHELD"},
                ],
            }
        )
        assert first["registered"] == ["synth.audit_beta", "synth.audit_gamma"]
        assert up.levels() == {"synth.audit_beta": "MOCK", "synth.audit_gamma": "WITHHELD"}
        assert len(first["snapshot_digest"]) == 64

        second = up.ingest_audit_snapshot(
            {
                "capabilities": [
                    {"capability_id": "synth.audit_beta", "level": "REAL"},
                    {"capability_id": "synth.audit_gamma", "level": "WITHHELD"},
                ]
            }
        )
        assert [c["kind"] for c in second["changes"]] == ["level_changed"]
        assert second["mismatches"] == ["synth.audit_beta"]
        # the external report is recorded, never applied
        assert up.levels()["synth.audit_beta"] == "MOCK"
        directions = [e["direction"] for e in up.ledger.events()]
        assert directions.count(ledger_mod.DIRECTION_OBSERVED) == 1
        assert directions.count(ledger_mod.DIRECTION_MISMATCH) == 1
        assert up.verify()["ok"] is True


def test_audit_payload_shapes_and_validation(tmp_path):
    snapshot = AuditSnapshot.from_api_payload([{"id": "synth.one", "evidence_level": "stub"}])
    assert snapshot.levels == {"synth.one": "STUB"}
    with pytest.raises(EvidenceError):
        AuditSnapshot.from_api_payload({"items": [{"capability_id": "synth.two"}]})
    with pytest.raises(EvidenceError):
        AuditSnapshot.from_api_payload({"items": [{"capability_id": "synth.two", "level": "GREAT"}]})
    with pytest.raises(EvidenceError):
        AuditSnapshot.from_api_payload("not-a-payload")


def test_removed_audit_rows_are_recorded_without_dropping_the_level(tmp_path):
    with open_upgrader(tmp_path) as up:
        seed(up)
        up.ingest_audit_snapshot({"capabilities": []})
        assert up.levels()[CAP] == "MOCK"
        last = up.ledger.events()[-1]
        assert last["direction"] == ledger_mod.DIRECTION_OBSERVED
        assert "audit_row_removed" in last["reason_codes"]


# ------------------------------------------------- zero domain semantics ------
def test_module_and_tests_carry_no_domain_vocabulary():
    deny = scanner.load_deny_list()
    assert len(deny["whole_word_ascii"]) >= 20 and len(deny["substring_any"]) >= 5
    report = scanner.scan_files([PKG_DIR, os.path.abspath(__file__)], deny=deny)
    assert report["ok"] is True, report["hits"]
    assert report["n_files"] >= len(LEVELS)

# ---------------------------------------------------------------- HTTP adapter --
def test_http_adapter_exposes_the_same_mechanism(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.capability_auto_upgrader.api import create_router

    app = FastAPI()
    app.include_router(create_router(str(tmp_path)), prefix="/auto")
    client = TestClient(app)

    registered = client.post("/auto/audit-snapshot", json={"capabilities": [{"capability_id": CAP, "level": "MOCK"}]})
    assert registered.status_code == 200
    assert registered.json()["registered"] == [CAP]

    for seq in (1, 2, 3):
        response = client.post(
            "/auto/runs",
            json={
                "capability_id": CAP,
                "seq": seq,
                "tests": {"passed": 40, "failed": 0, "report_sha256": "c" * 64},
                "benchmark": {"measured": 1.02, "reference": 1.0, "n_samples": 32},
            },
        )
        assert response.status_code == 200, response.text
    assert response.json()["level"] == "REAL"

    status = client.get("/auto/status").json()
    assert status["levels"][CAP] == "REAL"
    assert client.get("/auto/verify").json()["ok"] is True
    events = client.get(f"/auto/events?capability_id={CAP}").json()
    assert any(e["direction"] == ledger_mod.DIRECTION_UPGRADE for e in events["items"])


def test_http_adapter_rejects_malformed_and_out_of_order_runs(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.capability_auto_upgrader.api import create_router

    app = FastAPI()
    app.include_router(create_router(str(tmp_path)), prefix="/auto")
    client = TestClient(app)

    client.post("/auto/audit-snapshot", json=[{"capability_id": CAP, "level": "MOCK"}])
    bad = client.post(
        "/auto/runs",
        json={
            "capability_id": CAP,
            "seq": 1,
            "tests": {"passed": 40},
            "benchmark": {"measured": 1.02, "reference": 0.0, "n_samples": 32},
        },
    )
    assert bad.status_code == 422

    ok = client.post(
        "/auto/runs",
        json={
            "capability_id": CAP,
            "seq": 1,
            "tests": {"passed": 40},
            "benchmark": {"measured": 1.02, "reference": 1.0, "n_samples": 32},
        },
    )
    assert ok.status_code == 200
    skipped = client.post(
        "/auto/runs",
        json={
            "capability_id": CAP,
            "seq": 3,
            "tests": {"passed": 40},
            "benchmark": {"measured": 1.02, "reference": 1.0, "n_samples": 32},
        },
    )
    assert skipped.status_code == 409
