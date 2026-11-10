"""Throughput benchmark tests (AC7 harness).

AC7 itself (100 GiB in <60 s) is measured and extrapolated by
``tools/multisolver_ckpt.report.stage_bench``; these tests only guard the
*instrument*: the benchmark must round-trip losslessly, report honest rates, and
refuse to run when the filesystem cannot hold the payload.
"""

from __future__ import annotations

import pytest

from tools.multisolver_ckpt import bench_io
from tools.multisolver_ckpt.bench_io import (
    AC7_PAYLOAD_GIB,
    AC7_TARGET_SECONDS,
    GIB,
    BenchResult,
    check_headroom,
    make_payload,
    run_bench,
    run_size_sweep,
)

SMALL = 32 * 1024 * 1024  # 32 MiB


def test_payload_is_split_and_deterministic():
    a = make_payload(SMALL, n_solvers=3)
    b = make_payload(SMALL, n_solvers=3)
    assert a.names() == ("solver0", "solver1", "solver2")
    assert a.digest == b.digest
    total = sum(int(snap.arrays[k].nbytes) for snap in a.solvers.values() for k in snap.arrays)
    assert 0 < total <= SMALL
    with pytest.raises(ValueError):
        make_payload(4, n_solvers=3)


def test_bench_roundtrip_is_lossless(tmp_path):
    result = run_bench(tmp_path / "bench", SMALL, n_solvers=3)
    assert isinstance(result, BenchResult)
    assert result.lossless
    per = (SMALL // 3) - (SMALL // 3) % 8
    assert result.payload_bytes == per * 3 <= SMALL
    assert result.save_seconds > 0 and result.load_seconds > 0
    assert result.save_gibs > 0 and result.load_gibs > 0


def test_bench_reports_compression_ratio(tmp_path):
    result = run_bench(tmp_path / "bench", SMALL, n_solvers=2)
    data = result.to_dict()
    # standard-normal float64 does not compress; the ratio must be near 1, not 0
    assert 0.5 < data["compression_ratio"] < 1.5
    assert data["stored_bytes"] > 0
    assert data["measured_total_gibs"] > 0


def test_extrapolation_maths_is_explicit(tmp_path):
    result = run_bench(tmp_path / "bench", SMALL, n_solvers=1)
    ext = result.extrapolation()
    assert ext["payload_gib"] == AC7_PAYLOAD_GIB
    assert ext["target_seconds"] == AC7_TARGET_SECONDS
    assert ext["required_gibs"] == pytest.approx(AC7_PAYLOAD_GIB / AC7_TARGET_SECONDS)
    assert ext["save_seconds_extrapolated"] == pytest.approx(
        AC7_PAYLOAD_GIB / result.save_gibs)
    assert ext["save_meets_target"] == (ext["save_seconds_extrapolated"] < AC7_TARGET_SECONDS)
    assert ext["speedup_needed_save"] >= 1.0


def test_headroom_guard_refuses_impossible_payloads(tmp_path):
    with pytest.raises(ValueError):
        check_headroom(tmp_path, 10_000 * GIB)
    info = check_headroom(tmp_path, SMALL)
    assert info["free_bytes"] > info["payload_bytes"]


def test_size_sweep_reports_every_size(tmp_path):
    rows = run_size_sweep(tmp_path / "sweep", [8 * 1024 * 1024, 16 * 1024 * 1024],
                          n_solvers=2)
    assert len(rows) == 2
    assert all(r["lossless"] for r in rows)
    assert [r["payload_bytes"] for r in rows] == sorted(r["payload_bytes"] for r in rows)
    assert all("extrapolation" in r for r in rows)


def test_component_profile_decomposes_the_cost(tmp_path):
    profile = bench_io.component_profile(SMALL, workdir=tmp_path / "profile")
    assert profile["lossless"]
    for key in ("hash_seconds", "compress_seconds", "write_seconds", "read_seconds",
                "decompress_seconds"):
        assert profile[key] > 0.0
    for key in ("hash_mbps", "compress_mbps", "write_mbps", "read_mbps", "decompress_mbps"):
        assert profile[key] > 0.0
    assert 0.5 < profile["compression_ratio"] < 1.5


def test_worker_sweep_uses_more_payload_files(tmp_path):
    rows = bench_io.run_worker_sweep(tmp_path / "workers", 8 * 1024 * 1024,
                                     workers=(1, 2), n_solvers=4)
    assert [r["workers"] for r in rows] == [1, 2]
    assert all(r["lossless"] for r in rows)
    assert all(r["n_solvers"] == 4 for r in rows)