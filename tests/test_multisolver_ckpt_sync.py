"""Coupling-instant (sync point) acceptance tests (AC3).

Requirement: a checkpoint taken at a non-synchronised instant captures solvers
on different logical times, so it is *inconsistent* -- its digest differs, a
coupled restart must refuse it under the strict policy, and resuming from it
provably changes the trajectory.  A checkpoint at a synchronised instant is
consistent: resuming reproduces the uninterrupted run bit-for-bit.
"""

from __future__ import annotations

import pytest

from tools.multisolver_ckpt import (
    CheckpointStore,
    CouplingTimeGrid,
    NonSyncPointError,
    RestartSupervisor,
    SyncGuard,
    SyncPolicy,
    SyncReport,
    assess_sync,
    build_harness,
)
from multisolver_ckpt_helpers import grid, make_store  # noqa: F401  (fixture re-export)


# ── grid contract ─────────────────────────────────────────────────────────
def test_grid_rejects_invalid_steps():
    with pytest.raises(ValueError):
        CouplingTimeGrid(dt_master=0.0, n_sub=4)
    with pytest.raises(ValueError):
        CouplingTimeGrid(dt_master=-1.0, n_sub=4)
    with pytest.raises(ValueError):
        CouplingTimeGrid(dt_master=1.0, n_sub=0)


def test_grid_sub_dt_must_divide():
    g = CouplingTimeGrid(dt_master=1.0, n_sub=4)
    assert g.sub_dt(4) == 0.25
    assert g.sub_dt(2) == 0.5
    assert g.sub_dt(1) == 1.0
    with pytest.raises(ValueError):
        g.sub_dt(3)


def test_grid_times_are_exact(grid):
    assert grid.master_time(0) == 0.0
    assert grid.master_time(4).hex() == (4.0).hex()
    assert grid.digest() == CouplingTimeGrid(dt_master=1.0, n_sub=4).digest()
    assert grid.digest() != CouplingTimeGrid(dt_master=1.0, n_sub=2).digest()


# ── detection ─────────────────────────────────────────────────────────────
def test_fresh_harness_is_a_sync_point(grid):
    harness, _ = build_harness(3)
    report = assess_sync(harness.snapshot(), grid)
    assert report.sync_point
    assert report.phase_spread == 0
    assert report.time_spread == 0.0
    assert report.reason == ""


def test_master_boundary_is_a_sync_point(grid):
    harness, _ = build_harness(3)
    harness.advance_master(5)
    assert assess_sync(harness.snapshot(), grid).sync_point


def test_mid_interval_is_not_a_sync_point(grid):
    harness, _ = build_harness(3)
    harness.advance_master(4)
    harness.tick()  # exactly one global sub-step past the boundary
    report = assess_sync(harness.snapshot(), grid)
    assert not report.sync_point
    assert report.phase_spread > 0
    assert report.time_spread > 0.0
    assert "off the master grid" in report.reason
    assert "NOT a coupling instant" in report.explain()


def test_each_sub_step_inside_the_interval_is_non_sync(grid):
    harness, _ = build_harness(3)
    harness.advance_master(1)
    for expected_phase in range(1, grid.n_sub):
        harness.tick()
        report = assess_sync(harness.snapshot(), grid)
        assert not report.sync_point
    harness.tick()  # completes the interval
    assert assess_sync(harness.snapshot(), grid).sync_point


# ── policy ────────────────────────────────────────────────────────────────
def test_strict_save_refuses_mid_interval(tmp_path, grid):
    harness, ledger = build_harness(3)
    store = make_store(tmp_path / "store", ledger, grid, policy=SyncPolicy.STRICT)
    harness.advance_master(4)
    harness.tick()
    with pytest.raises(NonSyncPointError):
        store.save(4, harness.snapshot())
    assert store.steps() == []


def test_warn_save_records_non_sync(tmp_path, grid):
    harness, ledger = build_harness(3)
    store = make_store(tmp_path / "store", ledger, grid, policy=SyncPolicy.WARN)
    harness.advance_master(4)
    harness.tick()
    saved = store.save(4, harness.snapshot())
    assert saved.manifest["sync"]["sync_point"] is False
    assert saved.manifest["sync"]["policy_verdict"] == "non_sync"
    assert saved.manifest["sync"]["phase_spread"] > 0
    assert saved.manifest["sync"]["grid"]["n_sub"] == grid.n_sub


def test_strict_load_refuses_recorded_non_sync(tmp_path, grid):
    harness, ledger = build_harness(3)
    writer = make_store(tmp_path / "store", ledger, grid, policy=SyncPolicy.WARN)
    harness.advance_master(4)
    harness.tick()
    writer.save(4, harness.snapshot())
    strict = make_store(tmp_path / "store", ledger, grid, policy=SyncPolicy.STRICT)
    with pytest.raises(NonSyncPointError):
        strict.load(4)


def test_allow_policy_loads_non_sync_state(tmp_path, grid):
    harness, ledger = build_harness(3)
    writer = make_store(tmp_path / "store", ledger, grid, policy=SyncPolicy.WARN)
    harness.advance_master(4)
    harness.tick()
    saved = writer.save(4, harness.snapshot())
    relaxed = make_store(tmp_path / "store", ledger, grid, policy=SyncPolicy.ALLOW)
    loaded = relaxed.load(4)
    assert loaded.digest == saved.state_digest


def test_guard_switches_policy(grid):
    guard = SyncGuard(grid, SyncPolicy.STRICT)
    harness, _ = build_harness(3)
    harness.advance_master(2)
    harness.tick()
    with pytest.raises(NonSyncPointError):
        guard.check_save(harness.snapshot())
    guard.policy = SyncPolicy.WARN
    assert guard.check_save(harness.snapshot()).sync_point is False


# ── digests and resumed trajectories ─────────────────────────────────────
def test_non_sync_digest_differs_from_sync_digest(tmp_path, grid):
    harness, ledger = build_harness(3)
    store = make_store(tmp_path / "store", ledger, grid, policy=SyncPolicy.WARN)
    harness.advance_master(4)
    sync_digest = store.save(4, harness.snapshot()).state_digest
    harness.tick()
    mid_digest = store.save(4, harness.snapshot()).state_digest
    assert sync_digest != mid_digest


def test_sync_point_restart_reproduces_and_mid_interval_does_not(tmp_path, grid):
    horizon = 12
    reference, _ = build_harness(3)
    reference.advance_master(horizon)
    continuous = reference.snapshot().digest

    harness, ledger = build_harness(3)
    store = make_store(tmp_path / "sync", ledger, grid, policy=SyncPolicy.WARN)
    harness.advance_master(4)
    sync_saved = store.save(4, harness.snapshot())
    harness.tick()
    mid_store = make_store(tmp_path / "mid", ledger, grid, policy=SyncPolicy.WARN)
    mid_saved = mid_store.save(4, harness.snapshot())

    sync_resume, sync_ledger = build_harness(3)
    RestartSupervisor(store).resume_into(sync_resume, prefer=sync_saved.step)
    sync_resume.run(horizon - sync_resume.master_index, ledger=sync_ledger)
    assert sync_resume.snapshot().digest == continuous

    mid_resume, mid_ledger = build_harness(3)
    mid_resume.restore(mid_store.load(mid_saved.step).bundle)
    mid_resume.run(horizon - mid_resume.master_index, ledger=mid_ledger)
    assert mid_resume.snapshot().digest != continuous


def test_resaving_a_sync_state_is_digest_stable(tmp_path, grid):
    harness, ledger = build_harness(2)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(8, ledger=ledger)
    first = store.save(8, harness.snapshot())
    second = store.save(8, store.load(8).bundle)
    assert first.state_digest == second.state_digest


def test_sync_report_manifest_roundtrip(grid):
    harness, _ = build_harness(3)
    harness.advance_master(3)
    report = assess_sync(harness.snapshot(), grid)
    again = SyncReport.from_manifest(report.to_manifest(), grid)
    assert again.sync_point == report.sync_point
    assert again.phases == report.phases
    assert again.times == report.times
    assert again.master_step == report.master_step


def test_grid_is_recorded_in_the_manifest(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(4, ledger=ledger)
    saved = store.save(4, harness.snapshot())
    assert saved.manifest["grid"]["n_sub"] == grid.n_sub
    assert saved.manifest["grid"]["dt_master_hex"] == grid.dt_master.hex()
    assert saved.manifest["grid"]["digest"] == grid.digest()