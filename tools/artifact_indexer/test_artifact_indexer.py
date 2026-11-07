"""Pytest suite for the artifact sha256 indexer (IN15-O5, m4963).

Covers: correctness vs hashlib, incremental skip (mtime 未变跳过),
idempotent no-op update, pruning, tamper detection via spot check,
dry-run purity, multi-root (cross-repo) keys, mtime-only change.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools.artifact_indexer import indexer as ix


def _w(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _idx(root: Path) -> Path:
    return root / ix.DEFAULT_INDEX_NAME


# 1 ------------------------------------------------------------------
def test_build_hashes_match_hashlib(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "sub" / "b.bin"
    _w(a, b"alpha" * 1000)
    _w(b, b"beta" * 7)
    stats = ix.build_index([tmp_path], _idx(tmp_path))
    assert stats["hashed"] == 2 and stats["skipped"] == 0
    doc = ix.load_index(_idx(tmp_path))
    assert doc.entries[f"{tmp_path}/a.bin"]["sha256"] == hashlib.sha256(b"alpha" * 1000).hexdigest()
    assert doc.entries[f"{tmp_path}/sub/b.bin"]["sha256"] == hashlib.sha256(b"beta" * 7).hexdigest()
    assert doc.entries[f"{tmp_path}/a.bin"]["size"] == 5000
    # the index file itself must never be indexed
    assert not any(ix.DEFAULT_INDEX_NAME in k for k in doc.entries)


# 2 ------------------------------------------------------------------
def test_incremental_skips_unchanged_and_rehashes_changed(tmp_path):
    a = tmp_path / "a.bin"
    c = tmp_path / "c.bin"
    _w(a, b"one")
    _w(c, b"three")
    ix.build_index([tmp_path], _idx(tmp_path))
    # touch a (content identical, mtime forced forward) + rewrite c
    st = a.stat()
    os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    _w(c, b"three-CHANGED")
    stats = ix.build_index([tmp_path], _idx(tmp_path))
    assert stats["skipped"] == 0  # both mtimes moved
    assert stats["hashed"] == 2
    doc = ix.load_index(_idx(tmp_path))
    assert doc.entries[f"{tmp_path}/a.bin"]["sha256"] == hashlib.sha256(b"one").hexdigest()
    assert doc.entries[f"{tmp_path}/c.bin"]["sha256"] == hashlib.sha256(b"three-CHANGED").hexdigest()

    # now nothing changes: everything must be skipped (mtime 未变跳过)
    stats2 = ix.build_index([tmp_path], _idx(tmp_path))
    assert stats2["skipped"] == 2 and stats2["hashed"] == 0 and not stats2["changed"]


# 3 ------------------------------------------------------------------
def test_second_update_is_idempotent_byte_identical(tmp_path):
    for name, data in [("x", b"x" * 10), ("y", b"y" * 20)]:
        _w(tmp_path / name, data)
    ix.build_index([tmp_path], _idx(tmp_path))
    first = _idx(tmp_path).read_bytes()
    stats = ix.build_index([tmp_path], _idx(tmp_path))
    assert stats["hashed"] == 0 and stats["skipped"] == 2 and not stats["changed"]
    assert _idx(tmp_path).read_bytes() == first  # byte-identical, no rewrite


# 4 ------------------------------------------------------------------
def test_prunes_deleted_files(tmp_path):
    a = tmp_path / "a.bin"
    _w(a, b"keep")
    _w(tmp_path / "gone.bin", b"bye")
    ix.build_index([tmp_path], _idx(tmp_path))
    (tmp_path / "gone.bin").unlink()
    stats = ix.build_index([tmp_path], _idx(tmp_path))
    assert stats["pruned"] == 1
    doc = ix.load_index(_idx(tmp_path))
    assert f"{tmp_path}/gone.bin" not in doc.entries
    assert f"{tmp_path}/a.bin" in doc.entries


# 5 ------------------------------------------------------------------
def test_spot_check_detects_tamper(tmp_path):
    for i in range(5):
        _w(tmp_path / f"f{i}.bin", f"payload-{i}".encode())
    ix.build_index([tmp_path], _idx(tmp_path))
    ok = ix.spot_check(_idx(tmp_path), n=5, seed=42)
    assert ok["sampled"] == 5 and ok["passed"] == 5 and not ok["failed"]
    # tamper one file, sample all -> exactly one failure
    (tmp_path / "f2.bin").write_bytes(b"TAMPERED")
    bad = ix.spot_check(_idx(tmp_path), n=5, seed=42)
    assert bad["passed"] == 4 and len(bad["failed"]) == 1
    assert bad["failed"][0]["key"].endswith("f2.bin")
    assert bad["failed"][0]["reason"] == "hash_mismatch"


# 6 ------------------------------------------------------------------
def test_dry_run_writes_nothing(tmp_path):
    _w(tmp_path / "a.bin", b"abc")
    stats = ix.build_index([tmp_path], _idx(tmp_path), dry_run=True)
    assert stats["dry_run"] is True and stats["hashed"] == 1
    assert not _idx(tmp_path).exists()  # nothing written
    # and a real build afterwards still sees a cold index
    stats2 = ix.build_index([tmp_path], _idx(tmp_path))
    assert stats2["added"] == 1


# 7 ------------------------------------------------------------------
def test_multiple_roots_cross_repo_keys(tmp_path):
    r1 = tmp_path / "repoA" / "artifacts"
    r2 = tmp_path / "repoB" / "artifacts"
    _w(r1 / "x.json", b'{"k": 1}')
    _w(r2 / "y.json", b'{"k": 2}')
    idx = tmp_path / "combined_index.json"
    stats = ix.build_index([r1, r2], idx)
    assert stats["hashed"] == 2
    doc = ix.load_index(idx)
    assert f"{r1}/x.json" in doc.entries
    assert f"{r2}/y.json" in doc.entries
    assert doc.roots == [str(r1), str(r2)]
    res = ix.spot_check(idx, n=2, seed=0)
    assert res["passed"] == 2


# 8 ------------------------------------------------------------------
def test_mtime_only_change_rehashes_same_digest(tmp_path):
    a = tmp_path / "a.bin"
    _w(a, b"stable-content")
    ix.build_index([tmp_path], _idx(tmp_path))
    st = a.stat()
    os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    stats = ix.build_index([tmp_path], _idx(tmp_path))
    assert stats["hashed"] == 1  # mtime moved -> re-derived
    doc = ix.load_index(_idx(tmp_path))
    assert doc.entries[f"{tmp_path}/a.bin"]["sha256"] == hashlib.sha256(b"stable-content").hexdigest()


# 9 ------------------------------------------------------------------
def test_verify_all_flags_missing_file(tmp_path):
    _w(tmp_path / "a.bin", b"aa")
    _w(tmp_path / "b.bin", b"bb")
    ix.build_index([tmp_path], _idx(tmp_path))
    (tmp_path / "b.bin").unlink()
    res = ix.verify_all(_idx(tmp_path))
    assert res["passed"] == 1 and len(res["failed"]) == 1
    assert res["failed"][0]["reason"] == "missing_on_disk"


# 10 -----------------------------------------------------------------
def test_cli_build_and_check_roundtrip(tmp_path, capsys):
    from tools.artifact_indexer.__main__ import main

    for i in range(3):
        _w(tmp_path / f"f{i}.bin", f"data-{i}".encode())
    idx = tmp_path / "idx.json"
    rc = main(["build", "--root", str(tmp_path), "--index", str(idx)])
    assert rc == 0
    capsys.readouterr()  # drain build output
    rc = main(["check", "--index", str(idx), "--n", "2", "--seed", "7"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sampled"] == 2 and out["passed"] == 2
    (tmp_path / "f0.bin").write_bytes(b"EVIL")
    rc = main(["check", "--index", str(idx), "--n", "3", "--seed", "7"])
    assert rc == 1
