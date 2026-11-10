"""Bit-level reproducibility tests (AC1).

Three coupling scenarios (one solver / two solvers / three solvers) are run for
1000 master steps.  One run is uninterrupted; the other saves a checkpoint at
step 500, throws the solver objects away, restores from the checkpoint and
continues.  The two final state digests must be equal -- and one of the runs is
continued in a *separate interpreter* so process-local state cannot help.
"""

from __future__ import annotations

import numpy as np
import pytest

from tools.multisolver_ckpt import build_harness, scenario_name
from tools.multisolver_ckpt.determinism import (
    check_reproducibility,
    repro_matrix,
    run_checkpointed,
    run_continuous,
)
from multisolver_ckpt_helpers import grid, make_store  # noqa: F401  (fixture re-export)

STEPS = 1000
AT = 500


@pytest.mark.parametrize("level", (1, 2, 3))
def test_restart_reproduces_the_uninterrupted_run(tmp_path, grid, level):
    result = check_reproducibility(level, steps=STEPS, at=AT, root=tmp_path / "run")
    assert result.mode == "in-process"
    assert result.bit_identical, (
        f"{result.scenario}: continuous={result.continuous_digest} "
        f"restarted={result.restarted_digest}"
    )
    assert result.checkpoint_digest  # the checkpoint itself carries a digest
    assert result.rollbacks == 0


@pytest.mark.parametrize("level", (1, 2, 3))
def test_boundary_energy_residual_is_zero_after_restart(tmp_path, grid, level):
    result = check_reproducibility(level, steps=200, at=100, root=tmp_path / "run")
    assert result.boundary_gate["ok"], result.boundary_gate["detail"]
    assert result.boundary_gate["primary_residual"] == 0.0


@pytest.mark.parametrize("level", (1, 2, 3))
def test_restart_drift_stays_below_the_gate(tmp_path, grid, level):
    result = check_reproducibility(level, steps=STEPS, at=AT, root=tmp_path / "run")
    assert result.drift_gate["ok"], result.drift_gate["detail"]
    assert result.drift_gate["primary_residual"] < 1e-3


def test_cross_process_restart_is_bit_identical(tmp_path, grid):
    result = check_reproducibility(3, steps=STEPS, at=AT, root=tmp_path / "run",
                                   fresh_process=True)
    assert result.mode == "cross-process"
    assert result.bit_identical, (
        f"cross-process digests differ: {result.continuous_digest} vs {result.restarted_digest}"
    )


def test_scenario_construction_is_deterministic():
    for level in (1, 2, 3):
        first, _ = build_harness(level)
        second, _ = build_harness(level)
        assert first.snapshot().digest == second.snapshot().digest
        first.advance_master(3)
        second.advance_master(3)
        assert first.snapshot().digest == second.snapshot().digest


def test_different_seed_changes_the_trajectory():
    a, _ = build_harness(3, seed_base=1000)
    b, _ = build_harness(3, seed_base=2000)
    assert a.snapshot().digest != b.snapshot().digest


def test_checkpoint_at_several_points_all_resume_correctly(tmp_path, grid):
    continuous, _ = run_continuous(2, steps=60)
    for at in (10, 30, 50):
        digest, store, outcome = run_checkpointed(2, steps=60, at=at, root=tmp_path / f"run-{at}")
        assert digest == continuous, f"restart at step {at} diverged"
        assert outcome.step == at


def test_scenario_table_names_match_levels():
    assert scenario_name(1) == "kinetic_only"
    assert scenario_name(2) == "kinetic_field"
    assert scenario_name(3) == "kinetic_field_particles"
    assert scenario_name("kinetic_field") == "kinetic_field"
    with pytest.raises(KeyError):
        scenario_name(9)


def test_repro_matrix_covers_three_scenarios(tmp_path, grid):
    rows = repro_matrix((3, 2, 1), steps=40, at=20, root=tmp_path / "matrix")
    assert [r["scenario"] for r in rows] == ["kinetic_field_particles", "kinetic_field",
                                             "kinetic_only"]
    assert all(r["bit_identical"] for r in rows)


def test_restored_state_matches_array_bytes(tmp_path, grid):
    harness, ledger = build_harness(3)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(40, ledger=ledger)
    saved = store.save(40, harness.snapshot())
    loaded = store.load(40)
    for name in harness.snapshot().names():
        for array_name, arr in harness.snapshot().snapshot(name).arrays.items():
            got = loaded.bundle.snapshot(name).arrays[array_name]
            assert np.array_equal(got, arr)
    assert loaded.digest == saved.state_digest