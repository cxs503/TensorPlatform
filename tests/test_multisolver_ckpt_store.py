"""Store-level acceptance tests (snapshot round-trip, digests, codec, corruption).

Covers AC1 (bit-level identity of a restored state), AC4 (zstd losslessness) and
AC5 (single-bit corruption detection) at the level of the store itself.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from tools.multisolver_ckpt import (
    CheckpointCorruption,
    CheckpointError,
    CheckpointLayoutError,
    CheckpointStore,
    CheckpointUnreadable,
    CouplingTimeGrid,
    SchemaMismatch,
    StateBundle,
    SyncPolicy,
    array_digest,
    build_harness,
    get_codec,
    sha256_bytes,
    state_digest,
    zstd_available,
)
from tools.multisolver_ckpt.codec import available_codecs
from tools.multisolver_ckpt.errors import CodecUnavailable
from multisolver_ckpt_helpers import grid, make_store  # noqa: F401  (fixture re-export)

LEVELS = (1, 2, 3)


@pytest.mark.parametrize("level", LEVELS)
def test_snapshot_roundtrip_is_bit_exact(tmp_path, grid, level):
    harness, ledger = build_harness(level)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(4, ledger=ledger)
    bundle = harness.snapshot()
    saved = store.save(bundle.step, bundle)
    loaded = store.load()
    assert loaded.digest == bundle.digest == saved.state_digest
    # array bytes, not just digests
    for name in bundle.names():
        for array_name, arr in bundle.snapshot(name).arrays.items():
            got = loaded.bundle.snapshot(name).arrays[array_name]
            assert got.dtype == arr.dtype
            assert got.shape == arr.shape
            assert np.array_equal(got, arr)
            assert array_digest(got) == array_digest(arr)


@pytest.mark.parametrize("level", LEVELS)
def test_scalars_and_timing_survive(tmp_path, grid, level):
    harness, ledger = build_harness(level)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(6, ledger=ledger)
    bundle = harness.snapshot()
    store.save(bundle.step, bundle)
    loaded = store.load()
    for name in bundle.names():
        assert loaded.bundle.snapshot(name).scalars == bundle.snapshot(name).scalars
        assert loaded.bundle.snapshot(name).phase == bundle.snapshot(name).phase
        assert loaded.bundle.snapshot(name).time.hex() == bundle.snapshot(name).time.hex()


def test_changing_one_ulp_changes_the_digest(tmp_path, grid):
    harness, ledger = build_harness(3)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(4, ledger=ledger)
    bundle = harness.snapshot()
    store.save(bundle.step, bundle)

    # one ULP of a single component is enough to change the bit-level identity
    mutated = harness.snapshot()
    u = mutated.solvers["nodal"].arrays["u"]
    u[0] = np.nextafter(u[0], np.inf)
    assert mutated.digest != bundle.digest
    assert mutated.solvers["nodal"].digest != bundle.solvers["nodal"].digest
    first = store.save(bundle.step, bundle)
    resaved = store.save(bundle.step, mutated)
    assert resaved.state_digest != first.state_digest
    assert store.load(bundle.step).digest == mutated.digest != bundle.digest


@pytest.mark.skipif(not zstd_available(), reason="zstd backend not installed")
def test_zstd_roundtrip_is_lossless(tmp_path, grid):
    codec = get_codec("zstd", level=3)
    harness, _ = build_harness(3)
    harness.advance_master(2)
    payload = b"".join(
        harness.snapshot().solvers[s].arrays[a].tobytes()
        for s in harness.snapshot().names()
        for a in sorted(harness.snapshot().solvers[s].arrays)
    )
    compressed = codec.compress(payload)
    assert sha256_bytes(codec.decompress(compressed)) == sha256_bytes(payload)
    assert compressed[:4] == bytes.fromhex("28b52ffd")


@pytest.mark.skipif(not zstd_available(), reason="zstd backend not installed")
def test_compressed_checkpoint_holds_zstd_frames(tmp_path, grid):
    harness, ledger = build_harness(2)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(4, ledger=ledger)
    store.save(4, harness.snapshot())
    frames = [p for p in store.dir_of(4).iterdir() if p.suffix == ".zst"]
    assert frames, "no zstd payload written"
    for frame in frames:
        assert frame.read_bytes()[:4] == bytes.fromhex("28b52ffd")


def test_raw_codec_roundtrip_and_identity(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid, codec="raw")
    harness.run(4, ledger=ledger)
    saved = store.save(4, harness.snapshot())
    loaded = store.load(4)
    assert loaded.digest == saved.state_digest
    assert saved.compression_ratio == pytest.approx(1.0)


def test_unknown_codec_is_rejected():
    with pytest.raises(CodecUnavailable):
        get_codec("brotli-9")
    assert "raw" in available_codecs()


@pytest.mark.parametrize("codec_name,expected", [("raw", CheckpointCorruption), ("zstd", CheckpointError)])
def test_single_bit_flip_is_detected(tmp_path, grid, codec_name, expected):
    if codec_name == "zstd" and not zstd_available():
        pytest.skip("zstd backend not installed")
    harness, ledger = build_harness(2)
    store = make_store(tmp_path / "store", ledger, grid, codec=codec_name)
    harness.run(4, ledger=ledger)
    store.save(4, harness.snapshot())
    payload = sorted(p for p in store.dir_of(4).iterdir() if p.suffix in (".zst", ".bin"))[0]
    blob = bytearray(payload.read_bytes())
    blob[len(blob) // 2] ^= 0x01  # exactly one bit
    payload.write_bytes(bytes(blob))
    with pytest.raises(expected):
        store.load(4)


def test_manifest_tamper_is_detected(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(4, ledger=ledger)
    store.save(4, harness.snapshot())
    manifest_path = store.dir_of(4) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["state_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    with pytest.raises(CheckpointCorruption):
        store.manifest_of(4)


def test_foreign_schema_is_rejected(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(4, ledger=ledger)
    store.save(4, harness.snapshot())
    manifest_path = store.dir_of(4) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema"] = "someone-elses.ckpt/9"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    with pytest.raises(SchemaMismatch):
        store.manifest_of(4)


def test_missing_payload_is_unreadable(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(4, ledger=ledger)
    store.save(4, harness.snapshot())
    payload = sorted(p for p in store.dir_of(4).iterdir() if p.suffix in (".zst", ".bin"))[0]
    payload.unlink()
    with pytest.raises(CheckpointUnreadable):
        store.load(4)


def test_missing_store_is_unreadable(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "empty", ledger, grid)
    with pytest.raises(CheckpointUnreadable):
        store.load()


def test_step_must_match_bundle(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(4, ledger=ledger)
    with pytest.raises(CheckpointLayoutError):
        store.save(99, harness.snapshot())
    with pytest.raises(CheckpointLayoutError):
        store.save(-1, harness.snapshot())


def test_manifest_chain_links_checkpoints(tmp_path, grid):
    harness, ledger = build_harness(2)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(12, ledger=ledger, store=store, checkpoint_every=4)
    steps = store.steps()
    assert steps == [4, 8, 12]
    assert store.manifest_of(4)["prev_digest"] is None
    assert store.manifest_of(8)["prev_digest"] == store.manifest_of(4)["digest"]
    assert store.manifest_of(12)["prev_step"] == 8


def test_verify_all_reports_each_step(tmp_path, grid):
    harness, ledger = build_harness(2)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(12, ledger=ledger, store=store, checkpoint_every=4)
    report = store.verify_all()
    assert [r["step"] for r in report] == [4, 8, 12]
    assert all(r["ok"] for r in report)


def test_verify_all_flags_a_broken_step(tmp_path, grid):
    harness, ledger = build_harness(2)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(12, ledger=ledger, store=store, checkpoint_every=4)
    payload = sorted(p for p in store.dir_of(8).iterdir() if p.suffix in (".zst", ".bin"))[0]
    blob = bytearray(payload.read_bytes())
    blob[0] ^= 0xFF
    payload.write_bytes(bytes(blob))
    report = {r["step"]: r for r in store.verify_all()}
    assert report[8]["ok"] is False
    assert report[4]["ok"] is True


def test_prune_keeps_newest(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(20, ledger=ledger, store=store, checkpoint_every=4)
    removed = store.prune(keep=2)
    assert removed == [4, 8, 12]
    assert store.steps() == [16, 20]


def test_repeated_save_is_idempotent_in_state_digest(tmp_path, grid):
    harness, ledger = build_harness(2)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(8, ledger=ledger)
    bundle = harness.snapshot()
    first = store.save(8, bundle)
    second = store.save(8, store.load(8).bundle)
    assert first.state_digest == second.state_digest
    assert first.manifest["solvers"]["kinetic"]["payload"]["sha256"] == \
        second.manifest["solvers"]["kinetic"]["payload"]["sha256"]  # codec is deterministic


def test_no_temporary_directories_left_behind(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid)
    harness.run(8, ledger=ledger, store=store, checkpoint_every=4)
    leftovers = [p.name for p in store.root.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_chunk_metadata_covers_every_byte(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid, chunk_bytes=1024)
    harness.run(4, ledger=ledger)
    store.save(4, harness.snapshot())
    meta = store.manifest_of(4)["solvers"]["kinetic"]["arrays"]["f"]
    covered = sum(c["nbytes"] for c in meta["chunks"])
    assert covered == meta["nbytes"]
    assert [c["index"] for c in meta["chunks"]] == list(range(len(meta["chunks"])))
    assert all(len(c["sha256"]) == 64 for c in meta["chunks"])


def test_corruption_is_localised_to_a_chunk(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid, codec="raw", chunk_bytes=512)
    harness.run(4, ledger=ledger)
    store.save(4, harness.snapshot())
    payload = sorted(p for p in store.dir_of(4).iterdir() if p.suffix == ".bin")[0]
    blob = bytearray(payload.read_bytes())
    offset = 512 + 7  # inside chunk 1
    blob[offset] ^= 0x01
    payload.write_bytes(bytes(blob))
    with pytest.raises(CheckpointCorruption) as info:
        store.load(4)
    assert "chunk" in str(info.value)


def test_state_digest_is_order_independent():
    a = np.arange(12, dtype=np.float64).reshape(3, 4)
    b = np.linspace(-1, 1, 6)
    assert state_digest({"x": a, "y": b}, {"k": 1}) == state_digest({"y": b, "x": a}, {"k": 1})
    assert state_digest({"x": a}, {"k": 1}) != state_digest({"x": a}, {"k": 2})


def test_digest_distinguishes_dtype_and_shape():
    a = np.zeros(8, dtype=np.float64)
    b = np.zeros(8, dtype=np.float32)
    assert array_digest(a) != array_digest(b)
    assert array_digest(a.reshape(2, 4)) != array_digest(a)


def test_store_describe_records_identity(tmp_path, grid):
    harness, ledger = build_harness(1)
    store = make_store(tmp_path / "store", ledger, grid)
    desc = store.describe()
    assert desc["codec"]["codec"] == "zstd"
    assert desc["grid"]["n_sub"] == grid.n_sub
    assert desc["ledger"] == ["E_total"]
    assert desc["sync_policy"] == SyncPolicy.STRICT.value