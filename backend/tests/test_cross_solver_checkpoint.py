"""Acceptance tests for the bit-reproducible checkpoint layer.

Covers: state canonicalisation, checkpoint store round-trip in every available
codec, self-verification, single-bit-flip detection + localisation, atomic write,
compression losslessness, coupling/synchronisation semantics, restart equivalence
over 1000 steps in three synthetic coupling scenarios, RNG-stream capture, energy
ledger residual/drift, time-synchronisation verdicts, retention + rollback and
IO throughput measurement/extrapolation.

No domain semantics: the fixtures are synthetic rotations coupled through the
exchange bus (see ``physics/cross_solver/checkpoint_scenarios.py``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import pytest

from physics.cross_solver import checkpoint as ck
from physics.cross_solver.checkpoint_scenarios import SCENARIOS, Scenario


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture()
def store():
    return ck.CheckpointStore()


def _state(step: int = 5, seed: int = 3, rng_state: dict | None = None) -> ck.SolverState:
    rng = np.random.default_rng(seed)
    return ck.SolverState(
        fields={
            "solver_a/q": rng.normal(size=(8, 2)),
            "solver_b/tab": np.arange(12, dtype=np.float64).reshape(3, 4),
            "solver_b/scalar": np.array(1.5),
        },
        step=step,
        time=0.25 * step,
        meta={"scenario": "unit", "sync_points": step // 5},
        rng_state=rng_state,
    )


def _payload_byte_offset(store: ck.CheckpointStore, path: str, entry_index: int = 0) -> int:
    """Absolute file offset of the first payload byte of ``entry_index``."""
    header = store.header(path)
    raw = open(path, "rb").read()
    pos = len(ck.MAGIC)
    header_len = int.from_bytes(raw[pos:pos + 8], "little")
    payload_start = pos + 8 + header_len + 32
    return payload_start + header["entries"][entry_index]["offset"]


def _runner(scenario: Scenario = None) -> ck.CoupledRunner:
    return (scenario or Scenario("s1_two_solver")).runner()


# --------------------------------------------------------------------------- #
# 1. codec negotiation
# --------------------------------------------------------------------------- #
def test_codecs_always_include_none():
    assert ck.CODEC_NONE in ck.available_codecs()


def test_resolve_codec_auto_returns_available():
    assert ck.resolve_codec("auto") in ck.available_codecs()


def test_resolve_codec_rejects_unknown():
    with pytest.raises(ValueError):
        ck.resolve_codec("brotli-42")


def test_zstd_only_if_module_present():
    installed = ck._zstd_module() is not None
    assert (ck.CODEC_ZSTD in ck.available_codecs()) is installed


# --------------------------------------------------------------------------- #
# 2. state canonicalisation
# --------------------------------------------------------------------------- #
def test_state_canonicalises_non_contiguous_input():
    base = np.arange(24, dtype=np.float64).reshape(4, 6)
    view = base[:, ::2]                     # non C-contiguous
    state = ck.SolverState(fields={"solver_a/sliced": view})
    assert state.fields["solver_a/sliced"].flags["C_CONTIGUOUS"]
    assert np.array_equal(state.fields["solver_a/sliced"], view)


def test_state_converts_big_endian_to_little_endian():
    arr = np.arange(4, dtype=">f8")
    state = ck.SolverState(fields={"solver_a/be": arr})
    assert state.fields["solver_a/be"].dtype.byteorder == "<"


def test_state_rejects_object_dtype():
    with pytest.raises(TypeError):
        ck.SolverState(fields={"solver_a/bad": np.array([{"a": 1}], dtype=object)})


def test_state_digest_ignores_input_stride_but_not_values():
    a = ck.SolverState(fields={"solver_a/q": np.arange(6.0)})
    b = ck.SolverState(fields={"solver_a/q": np.arange(6.0)[::-1][::-1]})
    c = ck.SolverState(fields={"solver_a/q": np.arange(6.0) + 1})
    assert a.digest() == b.digest()
    assert a.digest() != c.digest()


def test_state_digest_changes_with_single_bit():
    a = ck.SolverState(fields={"solver_a/q": np.zeros(4)})
    fields = a.arrays()
    fields["solver_a/q"].view(np.uint64)[1] ^= np.uint64(1)
    b = ck.SolverState(fields=fields)
    assert a.digest() != b.digest()
    assert a.array_digest("solver_a/q") != b.array_digest("solver_a/q")


@pytest.mark.parametrize("attr,value", [("step", 6), ("time", 9.75), ("meta", {"scenario": "x"})])
def test_state_digest_covers_metadata(attr, value):
    base = _state()
    changed = _state()
    setattr(changed, attr, value)
    assert base.digest() != changed.digest()


def test_state_digest_covers_rng_stream_position():
    a = _state(rng_state={"bit_generator": "PCG64", "state": {"state": 1}})
    b = _state(rng_state={"bit_generator": "PCG64", "state": {"state": 2}})
    assert a.digest() != b.digest()


def test_state_copy_is_independent():
    a = _state()
    b = a.copy()
    b.fields["solver_a/q"][0, 0] += 1.0
    assert a.digest() != b.digest()


# --------------------------------------------------------------------------- #
# 3. store round-trip / verification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("codec", list(ck.available_codecs()))
def test_roundtrip_lossless_every_codec(store, tmp_path, codec):
    state = _state()
    path = tmp_path / f"state_{codec}.ckpt"
    meta = store.save(state, path, codec=codec)
    restored = store.load(path)
    assert restored.digest() == state.digest()
    assert meta["codec"] == codec
    assert meta["uncompressed_bytes"] == state.nbytes()


def test_save_reports_digest_matching_state(store, tmp_path):
    state = _state()
    meta = store.save(state, tmp_path / "a.ckpt")
    assert meta["digest"] == state.digest()


def test_save_leaves_no_tmp_file(store, tmp_path):
    store.save(_state(), tmp_path / "a.ckpt")
    assert [p.name for p in tmp_path.iterdir()] == ["a.ckpt"]


def test_save_overwrites_existing_file(store, tmp_path):
    path = tmp_path / "a.ckpt"
    store.save(_state(step=1), path)
    store.save(_state(step=2), path)
    assert store.load(path).step == 2


def test_verify_ok_on_fresh_checkpoint(store, tmp_path):
    path = tmp_path / "a.ckpt"
    store.save(_state(), path)
    report = store.verify(path)
    assert report.ok and report.error is None
    assert report.n_entries == 3
    assert all(entry["ok"] for entry in report.entries)


def test_header_digest_endpoint(store, tmp_path):
    state = _state()
    path = tmp_path / "a.ckpt"
    store.save(state, path)
    assert store.digest_of(path) == state.digest()


def test_compression_is_lossless_and_shrinks_redundant_data(store, tmp_path):
    redundant = ck.SolverState(fields={"solver_a/z": np.zeros((1024, 32))}, step=1)
    meta = store.save(redundant, tmp_path / "z.ckpt", codec=ck.CODEC_LZMA)
    assert meta["on_disk_bytes"] < meta["uncompressed_bytes"] / 10
    assert store.load(tmp_path / "z.ckpt").digest() == redundant.digest()


def test_structured_dtype_roundtrip(store, tmp_path):
    table = np.zeros(4, dtype=[("idx", "<i4"), ("weight", "<f8"), ("vec", "<f8", (3,))])
    table["idx"] = [1, 2, 3, 4]
    table["weight"] = [0.5, 0.25, 0.125, 0.0625]
    table["vec"] = np.arange(12, dtype=np.float64).reshape(4, 3)
    state = ck.SolverState(fields={"solver_b/table": table})
    path = tmp_path / "t.ckpt"
    store.save(state, path)
    restored = store.load(path)
    assert restored.digest() == state.digest()
    assert restored.fields["solver_b/table"].dtype.names == ("idx", "weight", "vec")


def test_empty_state_roundtrip(store, tmp_path):
    state = ck.SolverState(fields={}, step=0, time=0.0, meta={"note": "empty"})
    path = tmp_path / "e.ckpt"
    store.save(state, path)
    assert store.load(path).digest() == state.digest()


def test_zero_length_field_roundtrip(store, tmp_path):
    state = ck.SolverState(fields={"solver_a/none": np.zeros((0, 3))})
    path = tmp_path / "n.ckpt"
    store.save(state, path)
    restored = store.load(path)
    assert restored.fields["solver_a/none"].shape == (0, 3)
    assert restored.digest() == state.digest()


# --------------------------------------------------------------------------- #
# 4. corruption detection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry_index", [0, 1])
def test_single_bit_flip_in_payload_is_detected_and_localised(store, tmp_path, entry_index):
    path = tmp_path / "a.ckpt"
    store.save(_state(), path, codec=ck.CODEC_NONE)
    header = store.header(str(path))
    nbytes = header["entries"][entry_index]["nbytes"]
    offset = _payload_byte_offset(store, str(path), entry_index) + min(8, nbytes - 1)
    ck.flip_bit_in_file(path, offset, bit=3)
    with pytest.raises(ck.CheckpointCorruption) as excinfo:
        store.load(path)
    header = store.header(path)
    assert excinfo.value.entry == header["entries"][entry_index]["name"]
    assert excinfo.value.offset is not None
    assert not store.verify(path).ok


def test_single_bit_flip_in_header_is_detected(store, tmp_path):
    path = tmp_path / "a.ckpt"
    store.save(_state(), path)
    ck.flip_bit_in_file(path, len(ck.MAGIC) + 16, bit=0)
    with pytest.raises(ck.CheckpointCorruption):
        store.load(path)
    report = store.verify(path)
    assert not report.ok and "header" in report.error["message"]


def test_bit_flip_located_to_one_block_only(store, tmp_path):
    state = ck.SolverState(fields={"solver_a/q": np.arange(65536, dtype=np.float64)})
    path = tmp_path / "big.ckpt"
    store.save(state, path, codec=ck.CODEC_NONE)
    offset = _payload_byte_offset(store, str(path), 0) + 100_000   # inside entry 0
    ck.flip_bit_in_file(path, offset, bit=1)
    report = store.verify(path)
    assert not report.ok
    bad = report.entries[0]
    assert len(bad["bad_blocks"]) == 1


def test_truncated_file_is_reported(store, tmp_path):
    path = tmp_path / "a.ckpt"
    store.save(_state(), path)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    report = store.verify(path)
    assert not report.ok and report.error is not None


def test_bad_magic_is_rejected(store, tmp_path):
    path = tmp_path / "a.ckpt"
    path.write_bytes(b"NOTACKPT" + b"\x00" * 64)
    with pytest.raises(ck.CheckpointCorruption):
        store.load(path)
    assert not store.verify(path).ok


def test_missing_file_verify_reports_error(store, tmp_path):
    assert not store.verify(tmp_path / "absent.ckpt").ok


def test_flip_bit_bounds_are_checked(store, tmp_path):
    path = tmp_path / "a.ckpt"
    store.save(_state(), path)
    with pytest.raises(ValueError):
        ck.flip_bit_in_file(path, 10 ** 9)
    with pytest.raises(ValueError):
        ck.flip_bit_in_file(path, 0, bit=9)


# --------------------------------------------------------------------------- #
# 5. exchange bus semantics
# --------------------------------------------------------------------------- #
def test_bus_reads_only_committed_values():
    bus = ck.ExchangeBus()
    bus.publish("a/angle", 1.5)
    assert bus.read("a/angle") == 0.0        # not committed yet
    bus.commit()
    assert bus.read("a/angle") == 1.5
    assert bus.pending == {} and bus.commits == 1


def test_bus_serialisation_roundtrip():
    bus = ck.ExchangeBus({"a/angle": 0.25})
    bus.publish("b/angle", -2.0)
    bus.commit()
    bus.publish("c/angle", 7.0)
    fields = bus.to_fields()
    other = ck.ExchangeBus()
    other.load_fields(fields)
    assert other.committed == bus.committed
    assert other.pending == bus.pending
    assert other.commits == bus.commits


# --------------------------------------------------------------------------- #
# 6. runner: sync points, restart equivalence
# --------------------------------------------------------------------------- #
def test_runner_commits_only_on_sync_points():
    runner = _runner(Scenario("s2_three_solver"))       # sync_interval = 5
    runner.advance(24)
    assert runner.sync_log == [5, 10, 15, 20]
    assert runner.bus.commits == 4
    assert not runner.is_sync_point()
    assert runner.next_sync_step() == 25


def test_runner_state_contains_bus_namespace():
    runner = _runner()
    runner.advance(13)                       # past one sync point (interval 10)
    state = runner.state()
    assert "bus/commits" in state.fields
    assert int(state.fields["bus/commits"]) == 1
    assert any(name.startswith("bus/committed/") for name in state.field_names)
    assert any(name.startswith("bus/pending/") for name in state.field_names)
    assert state.step == 13 and state.time == pytest.approx(0.13)


def test_runner_rejects_duplicate_names():
    solver = ck.CoupledSolver(name="dup", step_fn=lambda f, dt, b: None,
                              fields={"q": np.zeros((2, 2))})
    with pytest.raises(ValueError):
        ck.CoupledRunner([solver, solver])


def test_runner_rejects_bad_sync_interval():
    solver = ck.CoupledSolver(name="s", step_fn=lambda f, dt, b: None,
                              fields={"q": np.zeros((2, 2))})
    with pytest.raises(ValueError):
        ck.CoupledRunner([solver], sync_interval=0)


def test_runner_restore_rejects_foreign_field_set(tmp_path, store):
    runner = _runner()
    runner.advance(5)
    state = runner.state()
    bogus = ck.SolverState(fields={**state.fields, "solver_x/q": np.zeros((2, 2))},
                           step=state.step)
    with pytest.raises(KeyError):
        runner.load_state(bogus)


def test_restart_mid_segment_is_bit_identical(tmp_path):
    scenario = Scenario("s1_two_solver")
    path = tmp_path / "mid.ckpt"
    continuous = scenario.runner()
    continuous.advance(40)
    reference_digest = continuous.state().digest()

    restarted = scenario.runner()
    restarted.advance(7)                    # deliberately NOT a sync point
    restarted.checkpoint(path)
    resumed = scenario.runner()
    resumed.restore(path)
    resumed.advance(33)
    assert resumed.state().digest() == reference_digest


@pytest.mark.parametrize("scenario_key", sorted(SCENARIOS))
def test_restart_equivalence_1000_steps(scenario_key, tmp_path):
    result = ck.run_restart_equivalence(
        Scenario(scenario_key).runner, n_steps=1000, restart_at=500,
        tmpdir=tmp_path, codec="none",
    )
    assert result["bit_identical"], json.dumps(result, indent=2)[:2000]
    assert result["restored_step"] == 1000
    assert all(result["field_equal"].values())
    assert result["checkpoint"]["on_disk_bytes"] > 0


def test_rng_stream_position_is_captured(tmp_path):
    scenario = Scenario("s3_stochastic")
    path = tmp_path / "rng.ckpt"
    reference = scenario.runner()
    reference.advance(200)
    restarted = scenario.runner()
    restarted.advance(90)
    restarted.checkpoint(path)
    resumed = scenario.runner()
    resumed.restore(path)
    resumed.advance(110)
    assert resumed.state().digest() == reference.state().digest()
    assert resumed.state().rng_state == reference.state().rng_state


def test_cross_process_bit_reproducibility():
    """A fresh interpreter must reproduce the same digest (no salted hashing)."""
    code = (
        "import sys; sys.path.insert(0, %r);"
        "from physics.cross_solver.checkpoint_scenarios import Scenario;"
        "r = Scenario('s3_stochastic').runner(); r.advance(120);"
        "print(r.state().digest())" % os.path.join(os.path.dirname(__file__), "..")
    )
    digests = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       check=True).stdout.strip()
        for _ in range(2)
    }
    local = Scenario("s3_stochastic").runner()
    local.advance(120)
    digests.add(local.state().digest())
    assert len(digests) == 1


# --------------------------------------------------------------------------- #
# 7. energy ledger
# --------------------------------------------------------------------------- #
def test_ledger_residual_zero_after_checkpoint_roundtrip(store, tmp_path):
    scenario = Scenario("s2_three_solver")
    runner = scenario.runner()
    runner.advance(120)
    before = runner.state()
    path = tmp_path / "e.ckpt"
    store.save(before, path)
    after = store.load(path)
    ledger = scenario.ledger_factory()()
    res = ledger.residual(before, after)
    assert res["abs_residual"] <= 1e-10 and res["ok"]
    assert res["E_before"] > 0.0


def test_ledger_reports_nonzero_residual_for_changed_state():
    scenario = Scenario("s1_two_solver")
    ledger = scenario.ledger_factory()()
    a = scenario.runner().state()
    fields = a.arrays()
    fields["wave/q"] *= 1.01
    b = ck.SolverState(fields=fields, step=a.step, time=a.time, meta=a.meta)
    res = ledger.residual(a, b)
    assert not res["ok"] and res["rel_residual"] > 1e-6


def test_ledger_drift_over_1000_steps_below_0_1_percent():
    scenario = Scenario("s1_two_solver")
    runner = scenario.runner()
    ledger = scenario.ledger_factory()()
    reference = ledger.total(runner.state())
    runner.advance(1000)
    drift = ledger.drift(reference, runner.state(), tolerance=1e-3)
    assert drift["ok"], drift
    assert drift["rel_drift"] < 1e-3


def test_ledger_drift_survives_restart():
    scenario = Scenario("s2_three_solver")
    ledger = scenario.ledger_factory()()
    runner = scenario.runner()
    reference = ledger.total(runner.state())
    runner.advance(1000)
    assert ledger.drift(reference, runner.state())["rel_drift"] < 1e-3


def test_ledger_breakdown_registers_expected_terms():
    scenario = Scenario("s1_two_solver")
    ledger = scenario.ledger_factory()()
    breakdown = ledger.breakdown(scenario.runner().state())
    assert set(breakdown) == {"kinetic_wave", "kinetic_phase", "E_total"}


def test_ledger_missing_field_raises():
    ledger = ck.EnergyLedger().register("t", "absent/field")
    with pytest.raises(KeyError):
        ledger.total(ck.SolverState(fields={"a/q": np.zeros(2)}))


def test_ledger_rejects_unknown_reduction():
    with pytest.raises(ValueError):
        ck.EnergyLedger().register("t", "a/q", reduce="median")


# --------------------------------------------------------------------------- #
# 8. time synchronisation
# --------------------------------------------------------------------------- #
def test_time_synchronizer_flags_sync_points():
    sync = ck.TimeSynchronizer(sync_interval=10)
    assert sync.is_sync_point(500) and not sync.is_sync_point(505)
    assert sync.next_sync_step(505) == 510


def test_sync_point_checkpoints_are_synchronised():
    scenario = Scenario("s1_two_solver")
    a, b = scenario.runner(), scenario.runner()
    a.advance(500)
    b.advance(500)
    verdict = ck.TimeSynchronizer(scenario.sync_interval).compare(a.state(), b.state())
    assert verdict["synchronized"] is True
    assert verdict["reason"] == "sync_point_match"


def test_non_sync_point_checkpoints_are_not_synchronised():
    scenario = Scenario("s1_two_solver")
    a, b = scenario.runner(), scenario.runner()
    a.advance(500)
    b.advance(505)
    verdict = ck.TimeSynchronizer(scenario.sync_interval).compare(a.state(), b.state())
    assert verdict["synchronized"] is False
    assert verdict["reason"] in ("not_sync_point", "step_mismatch")
    assert verdict["digest_equal"] is False


def test_sync_point_with_diverged_state_is_not_synchronised():
    scenario = Scenario("s1_two_solver")
    a = scenario.runner()
    a.advance(500)
    state_a = a.state()
    fields = state_a.arrays()
    fields["wave/q"] *= 1.0001
    state_b = ck.SolverState(fields=fields, step=state_a.step, time=state_a.time,
                             meta=state_a.meta, rng_state=state_a.rng_state)
    verdict = ck.TimeSynchronizer(scenario.sync_interval).compare(state_a, state_b)
    assert verdict["synchronized"] is False and verdict["reason"] == "digest_mismatch"


# --------------------------------------------------------------------------- #
# 9. retention + rollback
# --------------------------------------------------------------------------- #
def test_manager_prunes_to_retention(tmp_path):
    manager = ck.CheckpointManager(tmp_path / "ck", keep=2, codec=ck.CODEC_NONE)
    for step in (10, 20, 30):
        manager.save(ck.SolverState(fields={"a/q": np.zeros(2)}, step=step))
    assert len(manager.paths()) == 2


def test_manager_load_latest_returns_newest(tmp_path):
    manager = ck.CheckpointManager(tmp_path / "ck", keep=3, codec=ck.CODEC_NONE)
    for step in (10, 20, 30):
        manager.save(ck.SolverState(fields={"a/q": np.zeros(2)}, step=step))
    state, info = manager.load_latest()
    assert state.step == 30 and info["rolled_back"] is False


def test_manager_rolls_back_past_corrupt_checkpoint(tmp_path):
    manager = ck.CheckpointManager(tmp_path / "ck", keep=3, codec=ck.CODEC_NONE)
    for step in (10, 20, 30):
        manager.save(ck.SolverState(fields={"a/q": np.arange(8.0)}, step=step))
    newest = manager.latest()
    ck.flip_bit_in_file(newest, len(ck.MAGIC) + 40)
    state, info = manager.load_latest()
    assert state.step == 20
    assert info["rolled_back"] is True
    assert len(info["skipped"]) == 1
    assert manager.rollback_history and manager.rollback_history[-1]["step"] == 20


def test_manager_raises_when_every_checkpoint_is_corrupt(tmp_path):
    manager = ck.CheckpointManager(tmp_path / "ck", keep=3, codec=ck.CODEC_NONE)
    for step in (10, 20):
        manager.save(ck.SolverState(fields={"a/q": np.arange(8.0)}, step=step))
    for path in manager.paths():
        ck.flip_bit_in_file(path, len(ck.MAGIC) + 40)
    with pytest.raises(ck.NoValidCheckpoint):
        manager.load_latest()


def test_manager_rollback_then_continue_run_is_consistent(tmp_path):
    scenario = Scenario("s1_two_solver")
    manager = ck.CheckpointManager(tmp_path / "ck", keep=3, codec=ck.CODEC_NONE)
    reference = scenario.runner()
    reference.advance(100)
    reference_digest = reference.state().digest()

    runner = scenario.runner()
    runner.advance(40)
    manager.save(runner.state())
    runner.advance(20)
    manager.save(runner.state())
    ck.flip_bit_in_file(manager.latest(), len(ck.MAGIC) + 40)
    state, info = manager.load_latest()
    assert info["step"] == 40
    runner.load_state(state)
    runner.advance(60)
    assert runner.state().digest() == reference_digest


# --------------------------------------------------------------------------- #
# 10. IO throughput probe
# --------------------------------------------------------------------------- #
def test_measure_io_throughput_is_lossless(tmp_path):
    state = ck.SolverState(fields={"a/q": np.zeros((256, 32))}, step=1)
    result = ck.measure_io_throughput(state, tmp_path, codec=ck.CODEC_NONE, repeats=2)
    assert result["lossless"] is True
    assert result["write_MBps"] > 0 and result["read_MBps"] > 0
    assert result["repeats"] == 2


def test_extrapolate_uses_measurement():
    projection = ck.extrapolate_to(int(0.5 * 1024 ** 3), write_s=0.5, read_s=0.6,
                                  target_gb=100.0)
    assert projection["projected_write_s"] == pytest.approx(100.0, rel=1e-6)
    assert projection["projected_read_s"] == pytest.approx(120.0, rel=1e-6)
    assert projection["projected_under_60s"] is False


def test_extrapolate_accepts_fast_device():
    projection = ck.extrapolate_to(int(8 * 1024 ** 3), write_s=1.0, read_s=1.0,
                                  target_gb=100.0)
    assert projection["projected_under_60s"] is True


# --------------------------------------------------------------------------- #
# 11. package integration
# --------------------------------------------------------------------------- #
def test_package_exports_checkpoint_layer():
    import physics.cross_solver as package

    for name in ("SolverState", "CheckpointStore", "CoupledRunner", "EnergyLedger",
                 "TimeSynchronizer", "CheckpointManager", "run_restart_equivalence"):
        assert name in package.__all__
        assert hasattr(package, name)