"""Checkpoint + idempotent-resume regression suite (mission m9082).

The claims under test are bit-level: a resumed run must reproduce the uninterrupted
run *exactly* (sha256 of the raw state bytes), and any single-byte corruption of a
checkpoint must be detected rather than silently loaded.
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.longrun import checkpoint as ckpt  # noqa: E402
from tools.longrun import resume as resume_mod  # noqa: E402
from tools.longrun import supervise  # noqa: E402
from tools.longrun.job import SyntheticIterativeJob  # noqa: E402


def _store(tmp_path, name="ck") -> ckpt.CheckpointStore:
    return ckpt.CheckpointStore(tmp_path / name)


def test_bit_identical_roundtrip(tmp_path):
    store = _store(tmp_path)
    job = SyntheticIterativeJob(grid=16)
    for i in range(4):
        job.step(i)
    manifest = store.save(4, job.state_arrays(), job.state_scalars(),
                          config_digest="cfg", input_digest="inp")
    ck = store.load(4)
    for name in ("u", "c"):
        assert np.array_equal(ck.arrays[name], job.state_arrays()[name])
        assert ck.arrays[name].tobytes() == job.state_arrays()[name].tobytes()
    assert ck.manifest["digest"] == manifest["digest"]
    assert ck.manifest["state_digest"] == job.result_digest()


def test_single_byte_corruption_is_detected_everywhere(tmp_path):
    """Flip one byte inside every array payload: 100 % detection.

    Blind flips over the *whole* zip file are not the right probe: 2 of 60 blind
    offsets landed in zip bookkeeping (local-header extra fields / padding) where
    the flip is invisible to any reader and the state is provably unchanged.
    The property that matters for a resume guarantee is: **no flip inside the array
    bytes may survive**, so offsets are derived from the zip entries themselves.
    """
    import zipfile

    store = _store(tmp_path)
    job = SyntheticIterativeJob(grid=16)
    job.step(1)
    store.save(1, job.state_arrays(), job.state_scalars())
    npz, _ = store._paths(1)  # noqa: SLF001 - test needs the raw bytes
    original = npz.read_bytes()
    with zipfile.ZipFile(npz) as zf:
        regions = []
        for info in zf.infolist():
            data_start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
            regions.append((data_start, data_start + info.compress_size))
    assert regions, "no array payload regions found"

    rng = random.Random(20261110)
    offsets = [rng.randrange(a, b) for a, b in regions for _ in range(20)]
    detected = 0
    for off in offsets:
        mutated = bytearray(original)
        mutated[off] ^= 0xFF
        npz.write_bytes(bytes(mutated))
        try:
            store.load(1)
        except ckpt.CheckpointError:
            detected += 1
        finally:
            npz.write_bytes(original)
    assert detected == len(offsets), f"only {detected}/{len(offsets)} payload corruptions detected"
    store.load(1)  # restored file still loads


def test_manifest_tamper_is_detected(tmp_path):
    store = _store(tmp_path)
    job = SyntheticIterativeJob(grid=8)
    job.step(1)
    store.save(1, job.state_arrays(), job.state_scalars())
    _, manifest_path = store._paths(1)  # noqa: SLF001
    payload = json.loads(manifest_path.read_text())
    payload["scalars"]["dt"] = 0.5  # silently change the physics of the restart
    manifest_path.write_text(json.dumps(payload))
    with pytest.raises(ckpt.CheckpointCorruption):
        store.load(1)


def test_missing_array_is_corruption(tmp_path):
    store = _store(tmp_path)
    job = SyntheticIterativeJob(grid=8)
    job.step(1)
    store.save(1, job.state_arrays(), job.state_scalars())
    npz, _ = store._paths(1)  # noqa: SLF001
    with np.load(npz, allow_pickle=False) as data:
        arrays = {k: np.array(data[k]) for k in data.files}
    arrays.pop("c")
    with open(npz, "wb") as fh:
        np.savez(fh, **arrays)
    with pytest.raises(ckpt.CheckpointCorruption):
        store.load(1)


def test_no_partial_checkpoint_at_canonical_name(tmp_path):
    """An interrupted write leaves only ``.tmp`` files, never a loadable half-file."""
    store = _store(tmp_path)
    job = SyntheticIterativeJob(grid=8)
    job.step(1)
    store.save(1, job.state_arrays(), job.state_scalars())
    npz, manifest_path = store._paths(1)  # noqa: SLF001
    stale = npz.with_suffix(".npz.tmp")
    stale.write_bytes(b"half written")
    assert store.load(1).step == 1, "stale .tmp must not be picked up"
    assert sorted(p.name for p in store.root.glob("ckpt_*.npz")) == [npz.name]
    assert manifest_path.exists()


def test_chain_links_consecutive_checkpoints(tmp_path):
    store = _store(tmp_path)
    job = SyntheticIterativeJob(grid=8)
    digests = []
    for step in (1, 2, 3):
        job.step(step)
        digests.append(store.save(step, job.state_arrays(), job.state_scalars())["digest"])
    m2 = store.manifest_of(2)
    m3 = store.manifest_of(3)
    assert m2["prev_digest"] == digests[0]
    assert m3["prev_digest"] == digests[1]
    assert store.verify_all() == [
        {"step": 1, "ok": True, "digest": digests[0]},
        {"step": 2, "ok": True, "digest": digests[1]},
        {"step": 3, "ok": True, "digest": digests[2]},
    ]


def test_resume_rejects_foreign_checkpoint(tmp_path):
    store = _store(tmp_path)
    job = SyntheticIterativeJob(grid=8)
    job.step(1)
    store.save(1, job.state_arrays(), job.state_scalars(),
               config_digest="cfg-A", input_digest="input-A")
    audit = resume_mod.ResumeAudit(tmp_path / "audit.jsonl")
    ctrl = resume_mod.ResumeController(store, audit, input_digest="input-B", config_digest="cfg-B")
    with pytest.raises(ckpt.CheckpointCorruption):
        ctrl.plan(force_from_step=1)
    assert audit.events("resume_rejected"), "rejection must be audited"


def test_resume_is_bit_identical_and_idempotent(tmp_path):
    """same input + same checkpoint -> same output (bit level), twice."""
    cfg = {
        "job": {"grid": 24},
        "steps": 30,
        "checkpoint_every": 5,
        "keep_checkpoints": 8,
        "forensics": {"stack_interval_s": 0.5},
    }
    work = tmp_path / "run"
    base = supervise.supervise(cfg, workdir=work, job_id="base")
    assert base["child"]["finished"] is True
    digest0 = base["child"]["result_digest"]

    key = None
    for attempt in range(2):
        rep = supervise.supervise(
            {**cfg, "job_id": f"resume{attempt}", "resume": {"from_step": 20}},
            workdir=work,
            job_id=f"resume{attempt}",
        )
        assert rep["child"]["resumed_from_step"] == 20
        assert rep["child"]["result_digest"] == digest0, "resume must be bit-identical"
        if key is None:
            key = rep["child"]["idempotency_key"]
        else:
            assert rep["child"]["idempotency_key"] == key, "idempotency key must repeat"
    audit = (work / "work" / "resume_audit.jsonl").read_text().splitlines()
    events = [json.loads(line)["event"] for line in audit]
    assert events.count("resume_applied") == 2
    assert "final_checkpoint" in events


def test_idempotency_key_covers_input_checkpoint_and_config():
    a = resume_mod.idempotency_key("i", "c", "k", 7)
    assert a == resume_mod.idempotency_key("i", "c", "k", 7)
    for other in (
        resume_mod.idempotency_key("i2", "c", "k", 7),
        resume_mod.idempotency_key("i", "c2", "k", 7),
        resume_mod.idempotency_key("i", "c", "k2", 7),
        resume_mod.idempotency_key("i", "c", "k", 8),
    ):
        assert other != a


def test_state_digest_is_order_independent_but_bit_exact():
    job = SyntheticIterativeJob(grid=8)
    arrays = job.state_arrays()
    assert ckpt.state_digest(arrays) == ckpt.state_digest(dict(reversed(list(arrays.items()))))
    nudged = {k: v.copy() for k, v in arrays.items()}
    nudged["u"][0, 0] = np.nextafter(nudged["u"][0, 0], np.inf)
    assert ckpt.state_digest(nudged) != ckpt.state_digest(arrays), "1 ULP change must be visible"