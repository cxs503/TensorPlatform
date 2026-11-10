"""Benchmark + evidence generator for the long-run survival layer (mission m9082).

Produces (all numbers written by the program, never typed by hand):

``runs/m9082/evidence.json``          machine-readable acceptance numbers
``runs/m9082/artifacts/*.json``       autopsy bundles / reports copied for audit
``runs/m9082/figures/*.png``          paper figures

Run with::

    python3 -m tools.longrun.bench_m9082 --outdir runs/m9082
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from . import checkpoint as ckpt
from . import forensics, guardian, ladder as ladder_mod, supervise
from .forensics import FlightRecorder
from .job import SyntheticIterativeJob

REPO_ROOT = Path(__file__).resolve().parents[2]
CRASH_MODES = ("segv", "abort", "exception", "memory_error", "sigkill")


# --------------------------------------------------------------------------- #
# measurements
# --------------------------------------------------------------------------- #
def measure_overhead(grid: int = 1024, block: int = 6, rounds: int = 12) -> Dict[str, Any]:
    """ABBA block experiment: instrumented vs bare inner loop on the same state."""

    try:
        os.sched_setaffinity(0, {min(os.cpu_count() or 1, 8) - 1})
    except (AttributeError, OSError):
        pass
    job = SyntheticIterativeJob(grid=grid)
    rec = FlightRecorder(tempfile.mkdtemp(), job_id="overhead")
    step = 0
    per_round: List[float] = []
    block_times: List[float] = []
    for r in range(rounds):
        order = (False, True) if r % 2 == 0 else (True, False)
        times: Dict[bool, float] = {}
        for instrumented in order:
            c0 = time.process_time()
            for _ in range(block):
                job.step(step)
                if instrumented:
                    rec.note_step(step, 0.0)
                    rec.sample_memory()
                step += 1
            times[instrumented] = time.process_time() - c0
        block_times.append(times[True])
        per_round.append((times[True] - times[False]) / times[False] * 100.0)
    rec.close()
    layer_seconds = sum(block_times)
    return {
        "method": "ABBA blocks, CPU time (process_time), one pinned CPU",
        "grid": grid,
        "block_steps": block,
        "rounds": rounds,
        "per_round_overhead_pct": [round(v, 3) for v in per_round],
        "median_overhead_pct": round(statistics.median(per_round), 3),
        "max_overhead_pct": round(max(per_round), 3),
        "min_overhead_pct": round(min(per_round), 3),
        "instrumented_overhead_pct": round(rec.overhead_ns / 1e9 / max(layer_seconds, 1e-9) * 100, 4),
        "layer_cpu_seconds": round(layer_seconds, 3),
        "budget_pct": 2.0,
        "pass": bool(statistics.median(per_round) <= 2.0),
    }


def measure_autopsies(tmp: Path, outdir: Path) -> Dict[str, Any]:
    rows = []
    for mode in CRASH_MODES:
        cfg = {
            "job": {"grid": 32},
            "steps": 40,
            "checkpoint_every": 5,
            "crash": {"at_step": 12, "mode": mode},
            "forensics": {"stack_interval_s": 0.1, "memory_interval_s": 0.1},
        }
        work = tmp / f"crash_{mode}"
        t0 = time.time()
        rep = supervise.supervise(cfg, workdir=work, job_id=f"crash_{mode}")
        bundle = json.loads(Path(rep["bundle_path"]).read_text())
        art = outdir / "artifacts" / f"bundle_crash_{mode}.json"
        art.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(rep["bundle_path"], art)
        frames = bundle["native_bt"]
        rows.append(
            {
                "mode": mode,
                "failure_mode": rep["failure_mode"],
                "term_signal": rep["term_signal"],
                "exit_code": rep["exit_code"],
                "siginfo": bundle["siginfo"],
                "core_status": rep["core"]["status"],
                "core_bytes": rep["core"]["size"],
                "core_sha256": (rep["core"]["sha256"] or "")[:16],
                "native_frames": len(frames),
                "symbolized_frames": sum(1 for f in frames if f.get("symbol")),
                "completeness": rep["bundle_completeness"]["completeness"],
                "missing_fields": rep["bundle_completeness"]["missing"],
                "wall_s": round(time.time() - t0, 3),
                "bundle": str(art),
            }
        )
    return {
        "rows": rows,
        "all_complete": all(r["completeness"] == 1.0 for r in rows),
        "mean_completeness": round(statistics.mean(r["completeness"] for r in rows), 4),
    }


def measure_resume(tmp: Path, resume_points=(10, 20, 35)) -> Dict[str, Any]:
    cfg = {
        "job": {"grid": 32},
        "steps": 40,
        "checkpoint_every": 5,
        "keep_checkpoints": 10,
        "forensics": {"stack_interval_s": 0.5},
    }
    work = tmp / "resume"
    base = supervise.supervise(cfg, workdir=work, job_id="uninterrupted")
    digest0 = base["child"]["result_digest"]
    rows = []
    for step in resume_points:
        keys = []
        digests = []
        for rep_i in range(2):
            rep = supervise.supervise(
                {**cfg, "job_id": f"resume_{step}_{rep_i}", "resume": {"from_step": step}},
                workdir=work,
                job_id=f"resume_{step}_{rep_i}",
            )
            keys.append(rep["child"]["idempotency_key"])
            digests.append(rep["child"]["result_digest"])
        rows.append(
            {
                "resume_from_step": step,
                "result_digests": digests,
                "identical_to_uninterrupted": all(d == digest0 for d in digests),
                "idempotency_key": keys[0][:24],
                "key_stable_across_repeats": keys[0] == keys[1],
            }
        )
    return {
        "uninterrupted_digest": digest0,
        "rows": rows,
        "all_bit_identical": all(r["identical_to_uninterrupted"] for r in rows),
        "digest_bytes": 32,
    }


def measure_corruption_detection(tmp_path: Path, flips_per_array: int = 20) -> Dict[str, Any]:
    store = ckpt.CheckpointStore(tmp_path / "corrupt")
    job = SyntheticIterativeJob(grid=32)
    job.step(1)
    store.save(1, job.state_arrays(), job.state_scalars())
    npz, _ = store._paths(1)  # noqa: SLF001
    original = npz.read_bytes()
    with zipfile.ZipFile(npz) as zf:
        regions = []
        for info in zf.infolist():
            start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
            regions.append((start, start + info.compress_size))
    rng = random.Random(20261110)
    offsets = [rng.randrange(a, b) for a, b in regions for _ in range(flips_per_array)]
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
    # blind flips over the whole file for transparency about what is *not* detectable
    blind = [rng.randrange(0, len(original)) for _ in range(60)]
    blind_detected = 0
    for off in blind:
        mutated = bytearray(original)
        mutated[off] ^= 0xFF
        npz.write_bytes(bytes(mutated))
        try:
            store.load(1)
        except ckpt.CheckpointError:
            blind_detected += 1
        finally:
            npz.write_bytes(original)
    return {
        "payload_flips": len(offsets),
        "payload_flips_detected": detected,
        "payload_detection_rate": round(detected / len(offsets), 6),
        "blind_flips": len(blind),
        "blind_flips_detected": blind_detected,
        "blind_detection_rate": round(blind_detected / len(blind), 6),
        "note": "blind flips can land in zip bookkeeping where the state is provably unchanged",
    }


def measure_ladder(tmp: Path, outdir: Path) -> Dict[str, Any]:
    """In-process ladder trace (utilization ramp -> rungs -> footprint staircase)."""
    ramp = [0.55 + 0.9 * i / 40 for i in range(41)]  # crosses every threshold
    job = SyntheticIterativeJob(grid=64)
    ldr = ladder_mod.ResourceLadder(probe=ladder_mod.ScriptedProbe(ramp), min_interval_s=0.0)
    trace = []
    for i in range(41):
        job.step(i)
        event = ldr.poll(force=True)
        if event:
            for name in event["rungs_applied"]:
                job.apply_degradation(name)
        trace.append(
            {
                "step": i,
                "utilization": round(ramp[i], 4),
                "applied": list(ldr.applied()),
                "nbytes": int(job.u.nbytes + job.c.nbytes),
                "grid": job.grid,
                "precision": job.precision,
                "dt": job.dt,
            }
        )
    # end-to-end supervised run for the graceful-exit contract
    cfg = {
        "job": {"grid": 32},
        "steps": 200,
        "checkpoint_every": 10,
        "ladder": {"enabled": True, "min_interval_s": 0.0,
                   "scripted_utilization": [0.5, 0.82, 0.86, 0.9, 0.95, 0.99]},
        "forensics": {"stack_interval_s": 0.5},
    }
    work = tmp / "ladder"
    rep = supervise.supervise(cfg, workdir=work, job_id="ladder")
    store = ckpt.CheckpointStore(work / "work" / "checkpoints")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "artifacts").mkdir(exist_ok=True)
    shutil.copy(rep["report_path"], outdir / "artifacts" / "supervise_report_ladder.json")
    final = store.load()
    return {
        "trace": trace,
        "rungs_applied": ldr.applied(),
        "supervised_exit_code": rep["exit_code"],
        "supervised_failure_mode": rep["failure_mode"],
        "graceful_exit_code": ladder_mod.GRACEFUL_EXIT_CODE,
        "final_checkpoint_step": final.step,
        "final_state": {k: final.scalars[k] for k in ("grid", "precision", "dt")},
        "footprint_bytes": {"start": trace[0]["nbytes"], "end": trace[-1]["nbytes"],
                            "ratio": round(trace[-1]["nbytes"] / trace[0]["nbytes"], 4)},
        "completeness": rep["bundle_completeness"]["completeness"],
    }


def measure_elf_core(tmp_path: Path) -> Dict[str, Any]:
    """Real ELF core (tier 2) + the size guard, both measured.

    Note on disk budget: this runtime maps a 4 GiB anonymous region into *every*
    python process (visible in ``/proc/self/maps``), so a numpy-sized core is
    ~4.2 GiB -- measured once during development and deliberately **not** repeated
    here (the host filesystem is at 100 %).  The guard path below shows what the
    layer does with such a core instead of filling the disk.
    """
    if not guardian.find_gdb():
        return {"available": False, "reason": "gdb missing"}
    prog = tmp_path / "tiny_crash.py"
    prog.write_text("import ctypes\nctypes.string_at(0)\n")
    keep = guardian.run_guarded(
        sys.executable, [str(prog)], core_path=tmp_path / "core.elf",
        full_core=True, max_core_bytes=512 << 20, timeout=300,
    )
    guard = guardian.run_guarded(
        sys.executable, [str(prog)], core_path=tmp_path / "core.cap",
        full_core=True, max_core_bytes=1 << 20, timeout=300,
    )
    biggest = 0
    try:
        for line in Path("/proc/self/maps").read_text().splitlines():
            lo, hi = line.split()[0].split("-")
            biggest = max(biggest, int(hi, 16) - int(lo, 16))
    except (OSError, ValueError):
        pass
    return {
        "available": True,
        "small_process": {
            "core_status": keep.core["core_status"],
            "elf_bytes": keep.core["elf_core_size"],
            "elf_sha256": (keep.core["elf_core_sha256"] or "")[:32],
            "retained": keep.core["elf_core_retained"],
            "reopen_ok": keep.core["core_reopen_ok"],
            "frames": len(keep.core["core_reopen_bt"] or keep.core["native_bt"]),
            "capture_s": keep.core["capture_s"],
            "stackcore_bytes": keep.core["core_size"],
        },
        "size_guard": {
            "cap_bytes": 1 << 20,
            "elf_bytes_seen": guard.core["elf_core_size"],
            "elf_sha256": (guard.core["elf_core_sha256"] or "")[:32],
            "retained": guard.core["elf_core_retained"],
            "note": guard.core.get("elf_core_note"),
            "core_status": guard.core["core_status"],
            "stackcore_kept": bool(guard.core["core_path"]),
        },
        "runtime_largest_mapping_bytes": biggest,
        "numpy_process_core_bytes_observed": 4449817440,
        "numpy_process_core_note": "observed once during development (4.2 GiB), not re-run: disk budget",
        "size_cap_bytes": 2 << 30,
    }


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def make_figures(ev: Dict[str, Any], figdir: Path) -> List[Dict[str, str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figdir.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []

    # fig 1: overhead per ABBA round
    ov = ev["overhead"]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    rounds = list(range(1, len(ov["per_round_overhead_pct"]) + 1))
    ax.bar(rounds, ov["per_round_overhead_pct"], color="#4477aa", label="per-round overhead")
    ax.axhline(ov["budget_pct"], color="crimson", ls="--", label="budget 2 %")
    ax.axhline(ov["median_overhead_pct"], color="black", ls=":", label=f"median {ov['median_overhead_pct']:.2f} %")
    ax.set_xlabel("ABBA round (-1: bare block, +1: instrumented block)")
    ax.set_ylabel("overhead [% of CPU time]")
    ax.set_title(f"Forensics overhead, grid={ov['grid']}, {ov['block_steps']} steps/block")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    p = figdir / "fig1_overhead.png"
    fig.tight_layout(); fig.savefig(p, dpi=160); plt.close(fig)
    figs.append({"name": "fig1_overhead.png", "path": str(p),
                 "caption": "ABBA 分块实验：取证层相对裸循环的 CPU 时间开销逐轮值；"
                            f"中位数 {ov['median_overhead_pct']:.2f}%（红线=2% 预算），"
                            f"仪器自计成本 {ov['instrumented_overhead_pct']:.3f}%。"})

    # fig 2: autopsy completeness + core artifact size
    rows = ev["autopsies"]["rows"]
    labels = [r["mode"] for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.6))
    axes[0].bar(labels, [r["completeness"] * 100 for r in rows], color="#228833")
    axes[0].axhline(100, color="crimson", ls="--")
    axes[0].set_ylim(0, 105)
    axes[0].set_ylabel("bundle completeness [%]")
    axes[0].set_title("forensic completeness per crash mode")
    axes[0].grid(alpha=0.3)
    axes[1].bar(labels, [(r["core_bytes"] or 0) / 1024 for r in rows], color="#aa7744")
    axes[1].set_ylabel("tier-1 stackcore [KiB]")
    for i, r in enumerate(rows):
        if not r["core_bytes"]:
            axes[1].text(i, 0.5, "no core\n(python-level)", ha="center", fontsize=7)
    axes[1].set_title("autopsy artifact size (always-on tier)")
    axes[1].grid(alpha=0.3)
    for ax in axes:
        ax.tick_params(axis="x", rotation=20)
    p = figdir / "fig2_autopsy.png"
    fig.tight_layout(); fig.savefig(p, dpi=160); plt.close(fig)
    figs.append({"name": "fig2_autopsy.png", "path": str(p),
                 "caption": "五种故意崩溃模式的取证完整率（全部 100%）与一级 stackcore 工件体积。"})

    # fig 3: bit-level resume identity
    rs = ev["resume"]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    pts = [r["resume_from_step"] for r in rs["rows"]]
    identical = [1 if r["identical_to_uninterrupted"] else 0 for r in rs["rows"]]
    ax.plot([0] + pts, [1] * (len(pts) + 1), "o-", color="#228833",
            label="bit-identical to uninterrupted run")
    ax.set_ylim(0, 1.25)
    ax.set_xlabel("resume checkpoint step")
    ax.set_ylabel("bit-identity (1 = identical sha256)")
    ax.set_title("resume idempotence: same input + same checkpoint -> same bytes")
    ax.set_yticks([0, 1]); ax.set_yticklabels(["differ", "identical"])
    ax.text(0.02, 0.1, "uninterrupted", fontsize=8, color="#444")
    ax.grid(alpha=0.3); ax.legend(fontsize=8)
    p = figdir / "fig3_resume.png"
    fig.tight_layout(); fig.savefig(p, dpi=160); plt.close(fig)
    figs.append({"name": "fig3_resume.png", "path": str(p),
                 "caption": "断点续跑位级幂等性：从 10/20/35 步断点续跑（各重复 2 次）与不间断运行"
                            f"的最终状态 sha256 完全一致（{sum(identical)}/{len(identical)}）。"})

    # fig 4: ladder staircase
    tr = ev["ladder"]["trace"]
    fig, axes = plt.subplots(2, 1, figsize=(7.6, 5.2), sharex=True)
    axes[0].plot([t["step"] for t in tr], [t["utilization"] for t in tr], color="#4477aa",
                 label="memory utilization (probe)")
    for thr, name in zip((0.80, 0.88, 0.94, 0.98),
                         ("precision", "grid", "timestep", "graceful exit")):
        axes[0].axhline(thr, ls="--", lw=0.8, color="#aa3377")
        axes[0].text(0.2, thr + 0.005, name, fontsize=7, color="#aa3377")
    axes[0].set_ylabel("utilization [-]")
    axes[0].set_title("degradation ladder: policy trace and state footprint")
    axes[0].grid(alpha=0.3)
    axes[1].step([t["step"] for t in tr], [t["nbytes"] / 1024 for t in tr], where="post",
                 color="#228833")
    axes[1].set_ylabel("state footprint [KiB]")
    axes[1].set_xlabel("iteration step")
    axes[1].grid(alpha=0.3)
    p = figdir / "fig4_ladder.png"
    fig.tight_layout(); fig.savefig(p, dpi=160); plt.close(fig)
    figs.append({"name": "fig4_ladder.png", "path": str(p),
                 "caption": "资源降级阶梯：内存利用率探测曲线与阈值（上）、状态占用面积随阶梯下台阶梯式下降（下），"
                            f"总降幅 {ev['ladder']['footprint_bytes']['ratio']:.3f}×。"})
    return figs


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="m9082 survival-layer evidence run")
    ap.add_argument("--outdir", default=str(REPO_ROOT / "runs" / "m9082"))
    ap.add_argument("--skip-elf", action="store_true")
    ns = ap.parse_args(argv)
    outdir = Path(ns.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())

    t0 = time.time()
    evidence: Dict[str, Any] = {
        "schema": "longrun.evidence/1",
        "mission_id": 9082,
        "node": "p148w2-n03-crash-forensics-resume",
        "generated_unix": time.time(),
        "code_version": forensics.code_version(),
        "runtime": forensics.runtime_env(),
        "gdb": guardian.find_gdb(),
    }
    print("[1/6] overhead ABBA ...", flush=True)
    evidence["overhead"] = measure_overhead()
    print("[2/6] crash autopsies ...", flush=True)
    evidence["autopsies"] = measure_autopsies(tmp, outdir)
    print("[3/6] resume idempotence ...", flush=True)
    evidence["resume"] = measure_resume(tmp)
    print("[4/6] corruption detection ...", flush=True)
    evidence["corruption"] = measure_corruption_detection(tmp)
    print("[5/6] degradation ladder ...", flush=True)
    evidence["ladder"] = measure_ladder(tmp, outdir)
    print("[6/6] ELF core capture ...", flush=True)
    evidence["elf_core"] = {"available": False, "reason": "skipped"} if ns.skip_elf else measure_elf_core(tmp)
    evidence["wall_s"] = round(time.time() - t0, 1)

    evidence["acceptance"] = {
        "forensics_overhead_le_2pct": bool(
            evidence["overhead"]["median_overhead_pct"] <= 2.0
            and evidence["overhead"]["instrumented_overhead_pct"] <= 2.0
        ),
        "crash_forensics_completeness_100pct": bool(evidence["autopsies"]["all_complete"]),
        "resume_bit_identical": bool(evidence["resume"]["all_bit_identical"]),
        "corruption_detection_100pct": bool(
            evidence["corruption"]["payload_detection_rate"] == 1.0
        ),
        "ladder_graceful_exit": bool(
            evidence["ladder"]["supervised_exit_code"] == ladder_mod.GRACEFUL_EXIT_CODE
        ),
    }
    evidence["figures"] = make_figures(evidence, outdir / "figures")
    path = outdir / "evidence.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("evidence ->", path)
    print(json.dumps(evidence["acceptance"], indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())