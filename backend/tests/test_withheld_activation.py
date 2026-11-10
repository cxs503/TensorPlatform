"""Tests for the WITHHELD reference-activation mechanism.

Scope: the verdict state machine (activation / reference update / withdrawal),
the audit contract, idempotence under replay and concurrency, and the write-time
counter recompute gate.  Every fixture is synthetic (``synthetic_a`` /
``synthetic_b``) — the mechanism is platform infrastructure and must stay free of
domain vocabulary.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from services.withheld_activation import fixtures
from services.withheld_activation import acceptance as acc
from services.withheld_activation.cli import main as cli_main
from services.withheld_activation.detector import (
    adjudicate,
    reactivate,
    relative_error_pct,
    scan,
    watch,
    withdraw,
)
from services.withheld_activation.ledger import (
    TRIGGER_ACTIVATION,
    TRIGGER_COUNTER_REPAIR,
    TRIGGER_REFERENCE_UPDATE,
    TRIGGER_WITHDRAWAL,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_WITHHELD,
    CounterMismatch,
    CriterionLedger,
    ReferenceStore,
    ReferenceValueError,
    recompute_counters,
)

ALPHA = {"quantity": "synthetic_quantity_alpha", "reference_value": 101.0,
         "source": "synthetic_source_a", "uncertainty_pct": 1.0}
BETA = {"quantity": "synthetic_quantity_beta", "reference_value": 50.0,
        "source": "synthetic_source_b", "uncertainty_pct": 2.0}


@pytest.fixture()
def seeded(tmp_path: Path):
    """Ledger with two subjects, no reference truth yet (both WITHHELD)."""
    fixtures.seed(tmp_path)
    return fixtures.open_pair(tmp_path)


# --------------------------------------------------------------------------- #
# adjudication
# --------------------------------------------------------------------------- #
def test_relative_error_pct_is_relative_to_reference():
    assert relative_error_pct(100.0, 101.0) == pytest.approx(100.0 / 101.0, rel=1e-12)
    assert relative_error_pct(-4.0, 4.0) == pytest.approx(200.0)
    with pytest.raises(ZeroDivisionError):
        relative_error_pct(1.0, 0.0)


def test_adjudicate_withheld_without_reference():
    outcome = adjudicate(100.0, None, 3.0)
    assert outcome.verdict == VERDICT_WITHHELD
    assert outcome.error_pct is None
    assert outcome.reason == "no_reference_truth"


def test_adjudicate_pass_on_tight_error_and_fail_on_loose_error():
    passing = adjudicate(100.0, {"reference_value": 101.0}, 3.0)   # 0.99 % <= 3 %
    failing = adjudicate(40.0, {"reference_value": 50.0}, 5.0)     # 20 % > 5 %
    assert passing.verdict == VERDICT_PASS
    assert passing.error_pct == pytest.approx(100.0 / 101.0, rel=1e-12)
    assert failing.verdict == VERDICT_FAIL
    assert failing.error_pct == pytest.approx(20.0)


@pytest.mark.parametrize("bad", [0.0, float("nan"), float("inf")])
def test_adjudicate_treats_unusable_reference_as_withheld(bad):
    assert adjudicate(10.0, {"reference_value": bad}, 3.0).verdict == VERDICT_WITHHELD


# --------------------------------------------------------------------------- #
# reference table
# --------------------------------------------------------------------------- #
def test_reference_store_digest_is_content_addressed(tmp_path: Path):
    store = ReferenceStore(tmp_path / "reference_truth.json")
    first = store.upsert("synthetic_a", **ALPHA)
    digest_first = store.digest("synthetic_a")
    # same values, new revision -> same digest (idempotent content addressing)
    store.upsert("synthetic_a", **ALPHA)
    assert store.digest("synthetic_a") == digest_first
    assert store.row("synthetic_a")["revision"] == 2
    # changed value -> new digest
    store.upsert("synthetic_a", **{**ALPHA, "reference_value": 102.0})
    assert store.digest("synthetic_a") != digest_first
    assert first["reference_value"] == 101.0


@pytest.mark.parametrize("value", [0.0, float("nan"), "not-a-number", None])
def test_reference_store_rejects_degenerate_values(tmp_path: Path, value):
    store = ReferenceStore(tmp_path / "reference_truth.json")
    with pytest.raises(ReferenceValueError):
        store.upsert("synthetic_a", quantity="synthetic_quantity_alpha",
                     reference_value=value)


def test_reference_store_delete_reports_whether_row_existed(tmp_path: Path):
    store = ReferenceStore(tmp_path / "reference_truth.json")
    store.upsert("synthetic_a", **ALPHA)
    assert store.delete("synthetic_a") is True
    assert store.delete("synthetic_a") is False
    assert store.digest("synthetic_a") is None


# --------------------------------------------------------------------------- #
# activation / update / withdrawal
# --------------------------------------------------------------------------- #
def test_activation_flips_withheld_to_pass_with_one_audit_record(seeded):
    ledger, store = seeded
    assert scan(ledger, store)["audit_appended"] == 0          # nothing to do yet
    report = reactivate(ledger, store, "synthetic_a", **ALPHA)
    assert report["audit_appended"] == 1
    event = report["events"][0]
    assert event["trigger"] == TRIGGER_ACTIVATION
    assert event["actor"] == "auto"
    assert (event["old_verdict"], event["new_verdict"]) == (VERDICT_WITHHELD, VERDICT_PASS)
    assert len(event["sha256"]) == 64
    assert event["sha256"] == store.digest("synthetic_a")
    assert ledger.criteria["synthetic_a"]["verdict"] == VERDICT_PASS
    assert ledger.criteria["synthetic_b"]["verdict"] == VERDICT_WITHHELD  # untouched


def test_audit_record_carries_full_contract(seeded):
    ledger, store = seeded
    reactivate(ledger, store, "synthetic_b", **BETA)
    record = ledger.audit_records()[0]
    for field in ("seq", "ts", "actor", "trigger", "subject_id", "old_verdict",
                  "new_verdict", "old_digest", "new_digest", "sha256",
                  "computed_value", "reference_value", "tolerance_pct", "error_pct",
                  "reason", "idempotency_key"):
        assert field in record
    assert record["subject_id"] == "synthetic_b"
    assert record["new_verdict"] == VERDICT_FAIL
    assert record["error_pct"] == pytest.approx(20.0)
    assert record["actor"] == "auto"


def test_rescan_is_idempotent(seeded):
    ledger, store = seeded
    reactivate(ledger, store, "synthetic_a", **ALPHA)
    for _ in range(3):
        report = scan(ledger, store)
        assert report["audit_appended"] == 0
        assert report["noop_subjects"] == ["synthetic_a", "synthetic_b"]
    assert len(ledger.audit_records()) == 1


def test_reference_update_readjudicates_and_audits(seeded):
    ledger, store = seeded
    reactivate(ledger, store, "synthetic_a", **ALPHA)          # PASS at 0.99 %
    report = reactivate(ledger, store, "synthetic_a",
                        **{**ALPHA, "reference_value": 110.0})  # 10 % > 3 %
    assert report["audit_appended"] == 1
    event = report["events"][0]
    assert event["trigger"] == TRIGGER_REFERENCE_UPDATE
    assert (event["old_verdict"], event["new_verdict"]) == (VERDICT_PASS, VERDICT_FAIL)
    assert ledger.criteria["synthetic_a"]["error_pct"] == pytest.approx(100.0 * 10.0 / 110.0)


def test_withdrawal_falls_back_to_withheld_and_is_audited(seeded):
    ledger, store = seeded
    reactivate(ledger, store, "synthetic_a", **ALPHA)
    digest_activated = store.digest("synthetic_a")
    report = withdraw(ledger, store, "synthetic_a")
    assert report["audit_appended"] == 1
    event = report["events"][0]
    assert event["trigger"] == TRIGGER_WITHDRAWAL
    assert (event["old_verdict"], event["new_verdict"]) == (VERDICT_PASS, VERDICT_WITHHELD)
    assert event["sha256"] == digest_activated          # digest of the removed row
    assert ledger.criteria["synthetic_a"]["reference_digest"] is None
    assert ledger.criteria["synthetic_a"]["error_pct"] is None


def test_verdict_cannot_only_ratchet_up(seeded):
    """PASS must be revocable: removal returns to WITHHELD, re-adding re-judges."""
    ledger, store = seeded
    reactivate(ledger, store, "synthetic_a", **ALPHA)
    withdraw(ledger, store, "synthetic_a")
    reactivate(ledger, store, "synthetic_a", **ALPHA)
    verdicts = [r["new_verdict"] for r in ledger.audit_records()]
    assert verdicts == [VERDICT_PASS, VERDICT_WITHHELD, VERDICT_PASS]
    assert [r["trigger"] for r in ledger.audit_records()] == [
        TRIGGER_ACTIVATION, TRIGGER_WITHDRAWAL, TRIGGER_ACTIVATION]


def test_dry_run_reports_without_writing(seeded):
    ledger, store = seeded
    store.upsert("synthetic_a", **ALPHA)
    before = Path(ledger.state_path).read_bytes()
    report = scan(ledger, store, dry_run=True)
    assert report["audit_appended"] == 0
    assert [e["new_verdict"] for e in report["events"]] == [VERDICT_PASS]
    assert Path(ledger.state_path).read_bytes() == before
    assert ledger.audit_records() == []


# --------------------------------------------------------------------------- #
# write-time recompute gate
# --------------------------------------------------------------------------- #
def test_counters_are_recomputed_from_base_records(tmp_path: Path):
    fixtures.seed(tmp_path)
    ledger, store = fixtures.open_pair(tmp_path)
    reactivate(ledger, store, "synthetic_a", **ALPHA)
    counters = ledger.recomputed_counters()
    assert counters["subjects"] == 2
    assert (counters["pass"], counters["withheld"], counters["fail"]) == (1, 1, 0)
    assert counters["audit_entries"] == 1
    assert counters["activations"] == 1
    assert counters["withdrawals"] == 0
    assert ledger.declared_counters == counters
    assert recompute_counters(ledger.criteria, ledger.audit_records()) == counters


def test_gate_refuses_forged_counter_block(tmp_path: Path):
    fixtures.seed(tmp_path, with_references=True)
    ledger, store = fixtures.open_pair(tmp_path)
    scan(ledger, store)
    document = json.loads(Path(ledger.state_path).read_text(encoding="utf-8"))
    document["counters"]["pass"] += 1
    document["counters"]["fail"] -= 1
    Path(ledger.state_path).write_text(json.dumps(document), encoding="utf-8")

    reloaded = CriterionLedger(ledger.state_path)
    report = reloaded.gate_report()
    assert report["ok"] is False
    assert report["deltas"] == {"pass": 1, "fail": -1}
    with pytest.raises(CounterMismatch) as excinfo:
        scan(reloaded, store)
    assert excinfo.value.deltas == {"pass": 1, "fail": -1}
    # refusal must not mutate state or audit (both subjects activated: PASS + FAIL)
    assert len(reloaded.audit_records()) == 2
    assert json.loads(Path(ledger.state_path).read_text(encoding="utf-8"))["counters"]["pass"] == 2


def test_repair_rebuilds_counters_and_audits_the_delta(tmp_path: Path):
    fixtures.seed(tmp_path, with_references=True)
    ledger, store = fixtures.open_pair(tmp_path)
    scan(ledger, store)
    document = json.loads(Path(ledger.state_path).read_text(encoding="utf-8"))
    document["counters"]["pass"] = 99
    Path(ledger.state_path).write_text(json.dumps(document), encoding="utf-8")

    reloaded = CriterionLedger(ledger.state_path)
    result = reloaded.repair_counters()
    assert result["repaired"] is True
    assert result["deltas"] == {"pass": 98}
    assert reloaded.gate_report()["ok"] is True
    repair_records = [r for r in reloaded.audit_records()
                      if r["trigger"] == TRIGGER_COUNTER_REPAIR]
    assert len(repair_records) == 1
    assert repair_records[0]["counter_deltas"] == {"pass": 98}
    assert reloaded.recomputed_counters()["counter_repairs"] == 1


# --------------------------------------------------------------------------- #
# idempotence under replay and concurrency
# --------------------------------------------------------------------------- #
def test_concurrent_scan_on_same_digest_change_writes_one_record(tmp_path: Path):
    fixtures.seed(tmp_path)
    store = ReferenceStore(tmp_path / "reference_truth.json")
    store.upsert("synthetic_a", **ALPHA)
    reports = []
    errors = []
    barrier = threading.Barrier(4)

    def _worker():
        ledger = CriterionLedger(tmp_path / "criteria.json")
        local_store = ReferenceStore(tmp_path / "reference_truth.json")
        barrier.wait(timeout=10)
        try:
            reports.append(scan(ledger, local_store))
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert sum(r["audit_appended"] for r in reports) == 1
    final = CriterionLedger(tmp_path / "criteria.json")
    assert len(final.audit_records()) == 1
    assert final.gate_report()["ok"] is True


def test_watch_stops_on_event_and_flips_without_manual_scan(tmp_path: Path):
    fixtures.seed(tmp_path)
    ledger, store = fixtures.open_pair(tmp_path)

    def _feed():
        ReferenceStore(tmp_path / "reference_truth.json").upsert("synthetic_a", **ALPHA)

    timer = threading.Timer(0.15, _feed)
    timer.start()
    report = watch(ledger, store, interval_s=0.05, timeout_s=10, stop_on_event=True)
    timer.cancel()
    assert len(report["events"]) == 1
    assert report["events"][0]["trigger"] == TRIGGER_ACTIVATION
    assert report["verdicts"]["synthetic_a"] == VERDICT_PASS
    assert report["passes"] >= 1


# --------------------------------------------------------------------------- #
# CLI contract
# --------------------------------------------------------------------------- #
def test_cli_scan_exit_zero_and_gate_exit_two_on_tamper(tmp_path: Path, capsys):
    fixtures.seed(tmp_path, with_references=True)
    ledger, store = fixtures.open_pair(tmp_path)
    scan(ledger, store)
    assert cli_main(["scan", "--state-dir", str(tmp_path)]) == 0
    assert cli_main(["gate", "--state-dir", str(tmp_path)]) == 0

    document = json.loads(Path(ledger.state_path).read_text(encoding="utf-8"))
    document["counters"]["audit_entries"] = 7
    Path(ledger.state_path).write_text(json.dumps(document), encoding="utf-8")
    capsys.readouterr()                                   # drop earlier CLI output
    assert cli_main(["gate", "--state-dir", str(tmp_path)]) == 2
    printed = capsys.readouterr().out
    assert '"ok": false' in printed
    assert '"audit_entries": 7' in printed        # declared (forged) value echoed back
    assert '"audit_entries": 2' in printed        # recomputed value next to it
    assert '"audit_entries": 5' in printed        # ... and the reported delta
    assert cli_main(["scan", "--state-dir", str(tmp_path)]) == 2
    assert '"refused": true' in capsys.readouterr().out


def test_cli_recount_without_repair_reports_but_does_not_write(tmp_path: Path, capsys):
    fixtures.seed(tmp_path, with_references=True)
    ledger, store = fixtures.open_pair(tmp_path)
    scan(ledger, store)
    document = json.loads(Path(ledger.state_path).read_text(encoding="utf-8"))
    document["counters"]["withheld"] = 5
    Path(ledger.state_path).write_text(json.dumps(document), encoding="utf-8")
    assert cli_main(["recount", "--state-dir", str(tmp_path)]) == 0
    assert json.loads(Path(ledger.state_path).read_text(encoding="utf-8"))["counters"]["withheld"] == 5
    assert cli_main(["recount", "--state-dir", str(tmp_path), "--repair"]) == 0
    assert CriterionLedger(ledger.state_path).gate_report()["ok"] is True


# --------------------------------------------------------------------------- #
# acceptance criteria (A..F) end to end
# --------------------------------------------------------------------------- #
def test_acceptance_all_criteria_pass(tmp_path: Path):
    report = acc.run(tmp_path / "acceptance", figures=True)
    failures = {k: v for k, v in report["criteria"].items() if not v["passed"]}
    assert not failures, json.dumps(failures, indent=1)
    assert report["all_passed"] is True
    numbers = report["criteria"]["A"]["numbers"]
    assert numbers["latency_s"] is not None and numbers["latency_s"] <= acc.ACTIVATION_BUDGET_S
    assert report["criteria"]["E"]["numbers"]["hit_count"] == 0
    assert Path(report["figure"]).exists()
    assert (tmp_path / "acceptance" / "acceptance.json").exists()