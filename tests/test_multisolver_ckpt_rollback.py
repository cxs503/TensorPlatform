"""Restart-supervision acceptance tests (AC5 detection + AC6 rollback).

Requirement: a single corrupted checkpoint must never be loaded, the supervisor
must fall back to the newest *verified* checkpoint automatically, quarantine the
bad one, keep an append-only audit trail, and lose no data: the continuation
from the fallback reproduces the uninterrupted run exactly.
"""

from __future__ import annotations

import json

import pytest

from tools.multisolver_ckpt import (
    CheckpointStore,
    RestartFailed,
    RestartSupervisor,
    build_harness,
)
from tools.multisolver_ckpt.determinism import run_continuous
from multisolver_ckpt_helpers import grid, make_store  # noqa: F401  (fixture re-export)


def _flip_a_bit(store: CheckpointStore, step: int) -> str:
    payload = sorted(p for p in store.dir_of(step).iterdir() if p.suffix in (".zst", ".bin"))[0]
    blob = bytearray(payload.read_bytes())
    blob[len(blob) // 2] ^= 0x01
    payload.write_bytes(bytes(blob))
    return payload.name


@pytest.fixture()
def populated(tmp_path, grid):
    harness, ledger = build_harness(3)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(300, ledger=ledger, store=store, checkpoint_every=100)
    return store, ledger


def test_restart_uses_the_newest_valid_checkpoint(populated):
    store, _ = populated
    supervisor = RestartSupervisor(store)
    harness, _ = build_harness(3)
    outcome = supervisor.resume_into(harness)
    assert outcome.ok
    assert outcome.step == 300
    assert outcome.rollbacks == []
    assert harness.master_index == 300
    assert harness.snapshot().digest == outcome.digest


def test_corrupt_latest_triggers_rollback_and_quarantine(populated):
    store, _ = populated
    _flip_a_bit(store, 300)
    supervisor = RestartSupervisor(store)
    harness, _ = build_harness(3)
    outcome = supervisor.resume_into(harness)
    assert outcome.step == 200
    assert len(outcome.rollbacks) == 1
    event = outcome.rollbacks[0]
    assert event.attempted_step == 300
    assert event.error_type in {"CheckpointCorruption", "CheckpointUnreadable"}
    assert event.quarantined_path and "corrupt" in event.quarantined_path
    assert store.steps() == [100, 200]


def test_rolled_back_continuation_loses_no_data(populated, grid):
    store, _ = populated
    _flip_a_bit(store, 300)
    supervisor = RestartSupervisor(store)
    harness, ledger = build_harness(3)
    outcome = supervisor.resume_into(harness)
    harness.run(300 - harness.master_index, ledger=ledger)
    continuous, _ = run_continuous(3, steps=300)
    assert harness.snapshot().digest == continuous
    assert outcome.digest != harness.snapshot().digest  # rolled back, then advanced


def test_rollback_reports_restored_digest_equals_recorded(populated):
    store, _ = populated
    _flip_a_bit(store, 300)
    outcome = RestartSupervisor(store).restart()
    assert outcome.digest == store.manifest_of(outcome.step)["state_digest"]


def test_multiple_corrupt_checkpoints(populated):
    store, _ = populated
    _flip_a_bit(store, 300)
    _flip_a_bit(store, 200)
    supervisor = RestartSupervisor(store)
    outcome = supervisor.restart()
    assert outcome.step == 100
    assert [r.attempted_step for r in outcome.rollbacks] == [300, 200]


def test_all_corrupt_fails_loudly(populated):
    store, _ = populated
    for step in store.steps():
        _flip_a_bit(store, step)
    supervisor = RestartSupervisor(store)
    with pytest.raises(RestartFailed):
        supervisor.restart()
    events = supervisor.audit_events("restart_failed")
    assert events and "failed verification" in events[-1]["reason"]
    assert events[-1]["tried"] == [300, 200, 100]


def test_empty_store_fails_loudly(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid)
    supervisor = RestartSupervisor(store)
    with pytest.raises(RestartFailed):
        supervisor.restart()
    assert supervisor.audit_events("restart_failed")


def test_prefer_restores_exactly_that_step(populated):
    store, _ = populated
    outcome = RestartSupervisor(store).restart(prefer=100)
    assert outcome.step == 100


def test_at_or_before_limits_candidates(populated):
    store, _ = populated
    outcome = RestartSupervisor(store).restart(at_or_before=200)
    assert outcome.step == 200


def test_prefer_unknown_step_is_an_error(populated):
    store, _ = populated
    with pytest.raises(RestartFailed):
        RestartSupervisor(store).restart(prefer=42)


def test_audit_trail_is_append_only_jsonl(populated):
    store, _ = populated
    _flip_a_bit(store, 300)
    supervisor = RestartSupervisor(store)
    supervisor.restart()
    supervisor.restart(prefer=100)
    lines = supervisor.audit_path.read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in lines]
    assert [e["event"] for e in events] == ["rollback", "restart_ok", "restart_ok"]
    assert all("ts" in e for e in events)
    assert events[0]["attempted_step"] == 300
    assert events[0]["fallback_step"] == 200


def test_quarantined_directory_keeps_its_reason(populated):
    store, _ = populated
    _flip_a_bit(store, 300)
    outcome = RestartSupervisor(store).restart()
    reason_file = (__import__("pathlib").Path(outcome.rollbacks[0].quarantined_path)
                   / "QUARANTINE.txt")
    assert reason_file.is_file()
    text = reason_file.read_text(encoding="utf-8")
    assert "step=300" in text and "reason=" in text
    assert "300" not in [str(s) for s in store.steps()]


def test_quarantine_can_be_disabled(populated):
    store, _ = populated
    _flip_a_bit(store, 300)
    supervisor = RestartSupervisor(store, quarantine_corrupt=False)
    outcome = supervisor.restart()
    assert outcome.step == 200
    assert outcome.rollbacks[0].quarantined_path is None
    assert 300 in store.steps()  # bad checkpoint left in place, still never loaded


def test_max_attempts_bounds_the_search(populated):
    store, _ = populated
    for step in store.steps():
        _flip_a_bit(store, step)
    supervisor = RestartSupervisor(store, max_attempts=2)
    with pytest.raises(RestartFailed):
        supervisor.restart()
    assert len(supervisor.audit_events("rollback")) == 2


def test_restart_into_harness_validates_solver_set(populated):
    store, _ = populated
    other, _ = build_harness(2)
    with pytest.raises(ValueError):
        RestartSupervisor(store).resume_into(other)