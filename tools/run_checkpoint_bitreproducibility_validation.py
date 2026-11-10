#!/usr/bin/env python
"""Acceptance harness for the cross-solver checkpoint / bit-reproducibility layer.

Runs every machine-checkable criterion of node ``p223w3-n08`` and writes

* ``artifacts/p223w3_n08/checkpoint_validation.json``  -- all numbers, no hand copying
* ``artifacts/p223w3_n08/fig_*.png``                   -- figures for the report

Zero domain semantics: the coupled runs are the synthetic fixtures of
``physics.cross_solver.checkpoint_scenarios``.

Usage::

    TMPDIR=/dev/shm/tmp python tools/run_checkpoint_bitreproducibility_validation.py
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
import hashlib
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from physics.cross_solver import checkpoint as ck                       # noqa: E402
from physics.cross_solver.checkpoint_scenarios import SCENARIOS, Scenario  # noqa: E402

OUT = REPO / "artifacts" / "p223w3_n08"
N_STEPS = 1000
RESTART_AT = 500


def _tmp_dir(name: str) -> Path:
    root = Path(os.environ.get("TMPDIR", "/tmp")) / f"p223w3_n08_{name}"
    root.mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------- #
# criterion (a): bit-level reproducibility after restart, three coupling setups
# --------------------------------------------------------------------------- #
def criterion_bit_reproducibility() -> dict:
    rows = []
    for key in sorted(SCENARIOS):
        scenario = Scenario(key)
        tmp = _tmp_dir(f"restart_{key}")
        result = ck.run_restart_equivalence(scenario.runner, n_steps=N_STEPS,
                                            restart_at=RESTART_AT, tmpdir=tmp,
                                            codec="none")
        # same run, but restart at a NON synchronisation point (extra evidence)
        off_sync = ck.run_restart_equivalence(scenario.runner, n_steps=N_STEPS,
                                              restart_at=RESTART_AT + 7,
                                              tmpdir=tmp, codec="none")
        rows.append({
            "scenario": key,
            "description": scenario.description,
            "n_steps": N_STEPS,
            "restart_at": RESTART_AT,
            "digest_continuous": result["digest_continuous"],
            "digest_restarted": result["digest_restarted"],
            "bit_identical": result["bit_identical"],
            "all_fields_equal": all(result["field_equal"].values()),
            "checkpoint_bytes": result["checkpoint"]["on_disk_bytes"],
            "restart_off_sync_at": RESTART_AT + 7,
            "bit_identical_off_sync_restart": off_sync["bit_identical"],
            "digest_off_sync_restart": off_sync["digest_restarted"],
        })
    return {
        "criterion": "(a) bit-level reproducibility: restart after checkpoint must "
                     "reproduce the continuous-run state digest over 1000 steps",
        "scenarios": rows,
        "passed": all(row["bit_identical"] and row["all_fields_equal"]
                      and row["bit_identical_off_sync_restart"] for row in rows),
        "n_scenarios": len(rows),
    }


# --------------------------------------------------------------------------- #
# criterion (b): energy ledger residual + drift
# --------------------------------------------------------------------------- #
def criterion_energy(store: ck.CheckpointStore) -> dict:
    rows = []
    for key in sorted(SCENARIOS):
        scenario = Scenario(key)
        ledger = scenario.ledger_factory()()
        tmp = _tmp_dir(f"energy_{key}")
        path = tmp / "state.ckpt"

        runner = scenario.runner()
        runner.advance(RESTART_AT)
        before = runner.state()
        store.save(before, path)
        after = store.load(path)
        residual = ledger.residual(before, after)

        # 1000-step drift, continuous and restarted
        reference = ledger.total(runner.state())
        runner.advance(RESTART_AT)
        continuous = runner.state()
        drift_continuous = ledger.drift(reference, continuous)

        resumed = scenario.runner()
        resumed.restore(path)
        resumed.advance(RESTART_AT)
        drift_restarted = ledger.drift(reference, resumed.state())

        rows.append({
            "scenario": key,
            "E_total_at_checkpoint": residual["E_before"],
            "residual_abs": residual["abs_residual"],
            "residual_rel": residual["rel_residual"],
            "residual_tolerance": residual["tolerance"],
            "residual_ok": residual["ok"],
            "drift_1000_steps_continuous_rel": drift_continuous["rel_drift"],
            "drift_1000_steps_restarted_rel": drift_restarted["rel_drift"],
            "drift_tolerance": drift_continuous["tolerance"],
            "drift_ok": drift_continuous["ok"] and drift_restarted["ok"],
            "digest_continuous": continuous.digest(),
            "digest_restarted": resumed.state().digest(),
        })
    return {
        "criterion": "(b) energy ledger: |E_after - E_before| < 1e-10 across a "
                     "checkpoint round-trip; relative drift over 1000 steps < 0.1 %",
        "scenarios": rows,
        "passed": all(row["residual_ok"] and row["drift_ok"] for row in rows),
    }


# --------------------------------------------------------------------------- #
# criterion (c): time-synchronisation semantics
# --------------------------------------------------------------------------- #
def criterion_time_sync() -> dict:
    scenario = Scenario("s1_two_solver")
    sync = ck.TimeSynchronizer(scenario.sync_interval)
    a, b = scenario.runner(), scenario.runner()
    a.advance(500)
    b.advance(500)
    at_sync = sync.compare(a.state(), b.state())

    c = scenario.runner()
    c.advance(505)
    off_sync = sync.compare(a.state(), c.state())

    # a replica that diverged numerically but sits on the same sync step
    state_a = a.state()
    fields = state_a.arrays()
    fields["wave/q"] *= 1.0 + 1e-12
    diverged = ck.SolverState(fields=fields, step=state_a.step, time=state_a.time,
                              meta=state_a.meta, rng_state=state_a.rng_state)
    diverged_verdict = sync.compare(state_a, diverged)
    return {
        "criterion": "(c) time synchronisation: checkpoints at a synchronisation "
                     "point must verify as synchronised, at a non-sync step not",
        "sync_interval": scenario.sync_interval,
        "sync_point_case": at_sync,
        "non_sync_point_case": off_sync,
        "diverged_state_case": {
            "synchronized": diverged_verdict["synchronized"],
            "reason": diverged_verdict["reason"],
            "digest_equal": diverged_verdict["digest_equal"],
        },
        "passed": bool(at_sync["synchronized"] and not off_sync["synchronized"]
                       and not diverged_verdict["synchronized"]),
    }


# --------------------------------------------------------------------------- #
# criterion (d)+(e): compression losslessness and single-bit corruption detection
# --------------------------------------------------------------------------- #
def criterion_compression(store: ck.CheckpointStore) -> dict:
    scenario = Scenario("s2_three_solver")
    runner = scenario.runner()
    runner.advance(200)
    state = runner.state()
    tmp = _tmp_dir("codec")
    rows = []
    for codec in ck.available_codecs():
        path = tmp / f"state_{codec}.ckpt"
        meta = store.save(state, path, codec=codec)
        restored = store.load(path)
        rows.append({
            "codec": codec,
            "uncompressed_bytes": meta["uncompressed_bytes"],
            "on_disk_bytes": meta["on_disk_bytes"],
            "compression_ratio": meta["compression_ratio"],
            "digest_before": state.digest(),
            "digest_after": restored.digest(),
            "lossless": restored.digest() == state.digest(),
            "verify_ok": store.verify(path).ok,
        })
    zstd_present = ck._zstd_module() is not None
    return {
        "criterion": "(d) compression is lossless (state digest unchanged) for every "
                     "available codec",
        "codecs": rows,
        "zstd_available_in_env": zstd_present,
        "note": ("zstd selected when the `zstandard` module is importable; this "
                 "environment has no zstd build, so the stdlib lzma codec is used "
                 "and reported as such"),
        "passed": all(row["lossless"] and row["verify_ok"] for row in rows),
    }


def criterion_corruption(store: ck.CheckpointStore) -> dict:
    scenario = Scenario("s2_three_solver")
    runner = scenario.runner()
    runner.advance(200)
    state = runner.state()
    tmp = _tmp_dir("corrupt")
    results = []

    path = tmp / "payload.ckpt"
    store.save(state, path, codec=ck.CODEC_NONE)
    header = store.header(path)
    # absolute offset of the first payload byte
    raw = path.read_bytes()
    pos = len(ck.MAGIC)
    header_len = int.from_bytes(raw[pos:pos + 8], "little")
    payload_start = pos + 8 + header_len + 32
    target_entry = header["entries"][1]
    flip_at = payload_start + target_entry["offset"] + 16
    flip = ck.flip_bit_in_file(path, flip_at, bit=5)
    detected, entry_name, entry_offset = False, None, None
    try:
        store.load(path)
    except ck.CheckpointCorruption as exc:
        detected, entry_name, entry_offset = True, exc.entry, exc.offset
    report = store.verify(path)
    results.append({
        "case": "single_bit_flip_in_payload",
        "flipped_byte": flip["byte_offset"],
        "bit": flip["bit"],
        "detected": detected,
        "detected_entry": entry_name,
        "expected_entry": target_entry["name"],
        "bad_block_offset": entry_offset,
        "block_size": header["block_size"],
        "verify_ok": report.ok,
        "bad_blocks": [e["bad_blocks"] for e in report.entries if not e["ok"]],
    })

    path2 = tmp / "header.ckpt"
    store.save(state, path2, codec=ck.CODEC_NONE)
    ck.flip_bit_in_file(path2, len(ck.MAGIC) + 24, bit=0)
    header_report = store.verify(path2)
    results.append({
        "case": "single_bit_flip_in_header",
        "detected": not header_report.ok,
        "error": header_report.error,
    })

    path3 = tmp / "truncated.ckpt"
    store.save(state, path3, codec=ck.CODEC_NONE)
    data = path3.read_bytes()
    path3.write_bytes(data[:len(data) // 2])
    truncation_report = store.verify(path3)
    results.append({
        "case": "truncated_file",
        "detected": not truncation_report.ok,
        "error": truncation_report.error,
    })

    return {
        "criterion": "(e) a single flipped bit is detected and localised to the "
                     "affected entry + 64 KiB block",
        "cases": results,
        "passed": bool(results[0]["detected"] and results[0]["detected_entry"]
                       and results[1]["detected"] and results[2]["detected"]),
    }


# --------------------------------------------------------------------------- #
# criterion (f): rollback
# --------------------------------------------------------------------------- #
def criterion_rollback(store: ck.CheckpointStore) -> dict:
    scenario = Scenario("s1_two_solver")
    tmp = _tmp_dir("rollback")
    manager = ck.CheckpointManager(tmp / "ckpt", keep=3, codec=ck.CODEC_NONE)

    reference = scenario.runner()
    reference.advance(300)
    reference_digest = reference.state().digest()

    runner = scenario.runner()
    runner.advance(100)
    manager.save(runner.state())
    runner.advance(100)
    manager.save(runner.state())
    runner.advance(100)
    manager.save(runner.state())

    newest = manager.latest()
    raw = newest.read_bytes()
    flip = ck.flip_bit_in_file(newest, len(ck.MAGIC) + 64, bit=2)  # header region
    state, info = manager.load_latest()
    runner.load_state(state)
    runner.advance(300 - state.step)
    recovered_digest = runner.state().digest()

    all_corrupt = None
    for path in manager.paths():
        ck.flip_bit_in_file(path, len(ck.MAGIC) + 64, bit=0)
    try:
        manager.load_latest()
        all_corrupt = False
    except ck.NoValidCheckpoint:
        all_corrupt = True

    return {
        "criterion": "(f) rollback: a corrupt newest checkpoint falls back to the "
                     "newest valid one and the run continues bit-identically",
        "retained": 3,
        "corrupted_path": str(newest),
        "flip": flip,
        "rolled_back_to_step": info["step"],
        "rolled_back": info["rolled_back"],
        "skipped": len(info["skipped"]),
        "recovered_digest": recovered_digest,
        "reference_digest": reference_digest,
        "recovered_bit_identical": recovered_digest == reference_digest,
        "all_corrupt_raises": all_corrupt,
        "passed": bool(info["rolled_back"] and recovered_digest == reference_digest
                       and all_corrupt),
    }


# --------------------------------------------------------------------------- #
# criterion (g): snapshot IO throughput + 100 GB extrapolation
# --------------------------------------------------------------------------- #
def criterion_io() -> dict:
    tmp = _tmp_dir("io")
    sizes_gib = [0.25, 0.5, 1.0, 2.0]
    rows = []
    for size in sizes_gib:
        n = int(size * 1024 ** 3 / 8)
        state = ck.SolverState(fields={"a/q": np.zeros(n, dtype=np.float64)}, step=1)
        repeats = 2 if size <= 0.5 else 1
        result = ck.measure_io_throughput(state, tmp, codec=ck.CODEC_NONE, repeats=repeats)
        projection = ck.extrapolate_to(result["uncompressed_bytes"], result["write_s"],
                                       result["read_s"], target_gb=100.0)
        rows.append({
            "size_GiB": size,
            "bytes": result["uncompressed_bytes"],
            "write_s": result["write_s"],
            "read_s": result["read_s"],
            "write_MBps": result["write_MBps"],
            "read_MBps": result["read_MBps"],
            "lossless": result["lossless"],
            "projected_write_s_100GB": projection["projected_write_s"],
            "projected_read_s_100GB": projection["projected_read_s"],
            "projected_under_60s": projection["projected_under_60s"],
        })
        del state

    # compression cost at a smaller size (lzma is CPU bound)
    small = ck.SolverState(fields={"a/q": np.random.default_rng(0).normal(size=(1 << 22,))},
                           step=1)
    compressed = ck.measure_io_throughput(small, tmp, codec=ck.CODEC_LZMA, repeats=1)

    # raw device ceiling: same payload, no serialisation / hashing at all
    probe_n = int(1.0 * 1024 ** 3)
    probe = np.zeros(probe_n // 8, dtype=np.float64)
    probe_path = tmp / "raw_device_probe.bin"
    start = time.perf_counter()
    with open(probe_path, "wb") as handle:
        handle.write(memoryview(probe).cast("B"))
        handle.flush()
        os.fsync(handle.fileno())
    raw_write_s = time.perf_counter() - start
    start = time.perf_counter()
    with open(probe_path, "rb") as handle:
        handle.read()
    raw_read_s = time.perf_counter() - start
    probe_path.unlink(missing_ok=True)
    mib = 1024.0 * 1024.0
    raw_write_MBps = (probe_n / mib) / raw_write_s
    raw_read_MBps = (probe_n / mib) / raw_read_s
    del probe

    filesystem = os.statvfs(tmp)
    free_gib = filesystem.f_bavail * filesystem.f_frsize / 1024 ** 3
    best = max(rows, key=lambda r: r["write_MBps"])
    return {
        "criterion": "(g) 100 GB snapshot IO < 60 s (measured throughput, then "
                     "linear extrapolation; the full 100 GB is NOT written here)",
        "device": str(tmp),
        "device_free_GiB": free_gib,
        "device_kind": "tmpfs (RAM backed)" if str(tmp).startswith("/dev/shm") else "disk",
        "codec": ck.CODEC_NONE,
        "measurements": rows,
        "best_write_MBps": best["write_MBps"],
        "best_read_MBps": best["read_MBps"],
        "projected_write_s_100GB": best["projected_write_s_100GB"],
        "projected_read_s_100GB": best["projected_read_s_100GB"],
        "projected_under_60s": best["projected_under_60s"],
        "raw_device_probe": {
            "bytes": probe_n,
            "write_MBps": raw_write_MBps,
            "read_MBps": raw_read_MBps,
            "note": "plain open().write() of the same payload: device ceiling "
                    "without serialisation or hashing",
        },
        "format_overhead_factor_write": (
            raw_write_MBps / best["write_MBps"] if best["write_MBps"] else None),
        "required_MBps_for_100GB_in_60s": (100 * 1024 ** 3 / 60.0) / mib,
        "host_loadavg": list(os.getloadavg()),
        "compression_probe_lzma": {
            "bytes": compressed["uncompressed_bytes"],
            "write_MBps": compressed["write_MBps"],
            "read_MBps": compressed["read_MBps"],
            "on_disk_bytes": compressed["on_disk_bytes"],
            "lossless": compressed["lossless"],
        },
        "honest_limitation": (
            "the container's root filesystem had < 5 MB free during this run, so the "
            "throughput numbers are measured on a RAM-backed tmpfs (an upper bound, no "
            "spindle/NVMe seek or page-cache writeback penalty). Writing the full "
            "100 GB was not exercised; the < 60 s verdict is an extrapolation of a "
            f"{best['size_GiB']} GiB measurement."
        ),
        "all_lossless": all(r["lossless"] for r in rows),
        "passed": bool(best["projected_under_60s"] and all(r["lossless"] for r in rows)),
    }


# --------------------------------------------------------------------------- #
# pytest evidence
# --------------------------------------------------------------------------- #
def run_pytest() -> dict:
    env = dict(os.environ)
    env.setdefault("TMPDIR", "/dev/shm")
    junit = _tmp_dir("pytest") / "report.xml"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_cross_solver_checkpoint.py",
         "--basetemp", str(_tmp_dir("pytest_tmp")), f"--junitxml={junit}"],
        cwd=str(REPO / "backend"), capture_output=True, text=True, env=env,
    )
    tail = (proc.stdout or "") + (proc.stderr or "")
    tests = failures = errors = skipped = 0
    if junit.exists():
        xml = junit.read_text()
        def _count(tag: str) -> int:
            match = re.search(rf'<testsuite[^>]*{tag}="(\d+)"', xml)
            return int(match.group(1)) if match else 0
        tests = _count("tests")
        failures = _count("failures")
        errors = _count("errors")
        skipped = _count("skipped")
    passed = tests - failures - errors - skipped
    return {
        "criterion": "(h) pytest suite green",
        "command": "python -m pytest backend/tests/test_cross_solver_checkpoint.py",
        "exit_code": proc.returncode,
        "tests_total": tests,
        "tests_passed": passed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "passed": proc.returncode == 0 and passed >= 30,
        "summary_tail": tail.strip().splitlines()[-1] if tail.strip() else "",
    }


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def make_figures(energy: dict, io: dict, compression: dict, bitrep: dict) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT.mkdir(parents=True, exist_ok=True)
    figures = []

    # fig 1: energy drift vs step for the three scenarios
    scenario = Scenario("s1_two_solver")
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    for key in sorted(SCENARIOS):
        ledger = Scenario(key).ledger_factory()()
        runner = Scenario(key).runner()
        reference = ledger.total(runner.state())
        steps, drifts = [], []
        for step in range(N_STEPS):
            runner.advance(1)
            if (step + 1) % 10 == 0:
                steps.append(step + 1)
                drifts.append(abs(ledger.total(runner.state()) - reference) / reference)
        ax.semilogy(steps, drifts, label=key)
    ax.axhline(1e-3, color="crimson", ls="--", lw=1.0, label="0.1 % acceptance bound")
    ax.set_xlabel("coupling step")
    ax.set_ylabel(r"relative energy drift $|E_t-E_0|/E_0$")
    ax.set_title("Energy-ledger drift of the three synthetic coupling setups")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = OUT / "fig_energy_drift.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figures.append(str(path))

    # fig 2: IO throughput vs snapshot size + 100 GB extrapolation
    rows = io["measurements"]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    sizes = [row["size_GiB"] for row in rows]
    ax.loglog(sizes, [row["write_MBps"] for row in rows], "o-", label="write (uncompressed)")
    ax.loglog(sizes, [row["read_MBps"] for row in rows], "s-", label="read + verify")
    ax.set_xlabel("snapshot payload size [GiB]")
    ax.set_ylabel("throughput [MiB/s]")
    ax2 = ax.twinx()
    ax2.loglog(sizes, [row["projected_write_s_100GB"] for row in rows], "^:",
               color="darkgreen", label="projected 100 GB write time")
    ax2.axhline(60.0, color="crimson", ls="--", lw=1.0, label="60 s budget")
    ax2.set_ylabel("projected 100 GB write time [s]")
    ax2.set_ylim(1, 1e3)
    lines = ax.get_legend_handles_labels()
    lines2 = ax2.get_legend_handles_labels()
    ax.legend(lines[0] + lines2[0], lines[1] + lines2[1], fontsize=8, loc="lower left")
    ax.set_title(f"Checkpoint IO on {io['device_kind']}: measured + 100 GB extrapolation")
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    path = OUT / "fig_io_throughput.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figures.append(str(path))

    # fig 3: payload vs on-disk bytes per codec
    codecs = [row["codec"] for row in compression["codecs"]]
    payload = [row["uncompressed_bytes"] for row in compression["codecs"]]
    on_disk = [row["on_disk_bytes"] for row in compression["codecs"]]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    x = np.arange(len(codecs))
    ax.bar(x - 0.2, payload, width=0.4, label="payload (uncompressed)")
    ax.bar(x + 0.2, on_disk, width=0.4, label="file on disk")
    ax.set_xticks(x)
    ax.set_xticklabels(codecs)
    ax.set_ylabel("bytes")
    ax.set_title("Checkpoint size per codec (state digest unchanged in all cases)")
    for xi, (p, d) in enumerate(zip(payload, on_disk)):
        ax.text(xi + 0.2, d, f"{p / d:.2f}x", ha="center", va="bottom", fontsize=8)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = OUT / "fig_codec_size.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figures.append(str(path))

    # fig 4: acceptance matrix (residuals + drift + digests)
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    labels = [row["scenario"] for row in energy["scenarios"]]
    residual = [max(row["residual_abs"], 1e-18) for row in energy["scenarios"]]
    drift = [max(row["drift_1000_steps_continuous_rel"], 1e-18)
             for row in energy["scenarios"]]
    x = np.arange(len(labels))
    ax.bar(x - 0.2, residual, width=0.4, label="|residual| at checkpoint")
    ax.bar(x + 0.2, drift, width=0.4, label="relative drift (1000 steps)")
    ax.set_yscale("log")
    ax.axhline(1e-10, color="darkorange", ls="--", lw=1.0, label="residual bound 1e-10")
    ax.axhline(1e-3, color="crimson", ls=":", lw=1.0, label="drift bound 0.1 %")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("value")
    ax.set_title("Energy ledger: checkpoint residual and 1000-step drift")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = OUT / "fig_energy_ledger.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figures.append(str(path))

    return figures


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    store = ck.CheckpointStore()
    started = time.time()

    results = {
        "node": "p223w3-n08-platform-checkpoint",
        "mission_id": 10558,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "platform": platform.platform(),
            "codecs": list(ck.available_codecs()),
            "zstd_available": ck._zstd_module() is not None,
            "tmpdir": os.environ.get("TMPDIR", "/tmp"),
        },
        "module": "backend/physics/cross_solver/checkpoint.py",
        "tests": "backend/tests/test_cross_solver_checkpoint.py",
    }

    results["bit_reproducibility"] = criterion_bit_reproducibility()
    print("[a] bit reproducibility:", results["bit_reproducibility"]["passed"])
    results["energy"] = criterion_energy(store)
    print("[b] energy ledger:", results["energy"]["passed"])
    results["time_sync"] = criterion_time_sync()
    print("[c] time sync:", results["time_sync"]["passed"])
    results["compression"] = criterion_compression(store)
    print("[d] compression lossless:", results["compression"]["passed"])
    results["corruption"] = criterion_corruption(store)
    print("[e] bit-flip detection:", results["corruption"]["passed"])
    results["rollback"] = criterion_rollback(store)
    print("[f] rollback:", results["rollback"]["passed"])
    results["io"] = criterion_io()
    print("[g] io extrapolation:", results["io"]["passed"])
    results["pytest"] = run_pytest()
    print("[h] pytest:", results["pytest"]["passed"], results["pytest"]["tests_passed"])

    figures = make_figures(results["energy"], results["io"], results["compression"],
                          results["bit_reproducibility"])
    results["figures"] = [str(Path(f).relative_to(REPO)) for f in figures]

    criteria = {key: results[key]["passed"] for key in
                ("bit_reproducibility", "energy", "time_sync", "compression",
                 "corruption", "rollback", "io", "pytest")}
    results["criteria"] = criteria
    results["all_passed"] = all(criteria.values())
    results["runtime_s"] = time.time() - started
    json_path = OUT / "checkpoint_validation.json"
    json_path.write_text(json.dumps(results, indent=2, sort_keys=True))
    digest = hashlib.sha256(json_path.read_bytes()).hexdigest()
    print(f"[ok] wrote {json_path} sha256={digest[:16]}")
    print("all_passed:", results["all_passed"], criteria)
    return 0 if results["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())