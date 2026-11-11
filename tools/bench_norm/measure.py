"""Measurement layer: runs the timing kernel on the current host and emits
schema observations.  The timed region is measured *inside* the kernel
(CLOCK_MONOTONIC around the time-stepping loop) so process start-up, dynamic
linking and JSON serialisation never enter the metric.

Ordering: each repetition is one full sweep over the workload grid with the
grid order shuffled by a seeded RNG.  This decorrelates slow machine drifts
(thermal / co-tenant load) from workload identity -- a fixed order would load
the same workload into the same "hot" or "cold" slot on every sweep.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .schema import Observation

HERE = Path(__file__).resolve().parent
KERNEL_SRC = HERE / "kernels" / "lbm_channel.c"


def _cpu_model() -> str:
    try:
        txt = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
        for line in txt.splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def host_fingerprint() -> Dict[str, Any]:
    gcc = shutil.which("gcc") or shutil.which("cc")
    gcc_ver = None
    if gcc:
        try:
            gcc_ver = subprocess.check_output([gcc, "--version"], text=True,
                                              timeout=20).splitlines()[0]
        except Exception:
            gcc_ver = None
    return {
        "host": platform.node(),
        "isa": platform.machine(),
        "cpu_model": _cpu_model(),
        "nproc": os.cpu_count(),
        "python": sys.version.split()[0],
        "uname": " ".join(platform.uname()),
        "compiler": gcc_ver,
        "loadavg": os.getloadavg() if hasattr(os, "getloadavg") else None,
        "timestamp": time.time(),
    }


def build_kernel(out_dir: str | Path, cc: str | None = None, flags: str = "-O2") -> Dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cc = cc or shutil.which("gcc") or shutil.which("cc")
    if not cc:
        raise RuntimeError("no C compiler found")
    binary = out / "lbm_channel"
    cmd = [cc] + flags.split() + ["-o", str(binary), str(KERNEL_SRC), "-lm"]
    t0 = time.time()
    subprocess.run(cmd, check=True, capture_output=True)
    sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    return {
        "binary": str(binary),
        "cc": cc,
        "flags": flags,
        "cmd": cmd,
        "kernel_sha256": sha,
        "kernel_src_sha256": hashlib.sha256(KERNEL_SRC.read_bytes()).hexdigest(),
        "build_seconds": time.time() - t0,
    }


def run_once(binary: str | Path, wl: Dict[str, Any], timeout: float = 300.0) -> Dict[str, Any]:
    cmd = [str(binary), str(wl["nx"]), str(wl["ny"]), str(wl["steps"]),
           repr(float(wl.get("tau", 0.8))), repr(float(wl.get("g", 1e-4))),
           "--warmup", str(wl.get("warmup", 100)), "--repeat", "1"]
    out = subprocess.check_output(cmd, text=True, timeout=timeout)
    return json.loads(out.strip().splitlines()[-1])


def measure_grid(binary: str | Path, workloads: Sequence[Dict[str, Any]],
                 reps: int, arm: str, host: str, isa: str,
                 seed: int = 4242, timeout: float = 600.0,
                 progress: bool = False) -> List[Dict[str, Any]]:
    if reps < 2:
        raise ValueError("reps must be >= 2 for a noise estimate")
    rng = random.Random(seed)
    records: List[Dict[str, Any]] = []
    order = list(range(len(workloads)))
    for r in range(reps):
        rng.shuffle(order)
        for i in order:
            wl = workloads[i]
            res = run_once(binary, wl, timeout=timeout)
            records.append(Observation(
                arm=arm, isa=isa, host=host,
                workload=wl["name"],
                features={k: float(wl[k]) for k in ("nx", "ny", "steps")},
                repetition=r,
                t_seconds=float(res["elapsed_s"]),
                source="measured",
                checksum=res.get("checksum"),
                meta={"repeat_index_in_rep": i, "role": wl.get("role", "evaluation"),
                      "physics": {
                    "u_max_num": res.get("u_max_num"), "u_max_ana": res.get("u_max_ana"),
                    "l2_err": res.get("l2_err"), "mass_drift": res.get("mass_drift"),
                    "tau": wl.get("tau", 0.8), "g": wl.get("g", 1e-4),
                }},
            ).to_dict())
            if progress:
                print(f"[measure] arm={arm} rep={r} wl={wl['name']} t={res['elapsed_s']:.4f}s",
                      flush=True)
    return records


def summarise_noise(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-workload run-to-run coefficient of variation; calibrates sim noise."""
    from collections import defaultdict
    vals: Dict[str, List[float]] = defaultdict(list)
    for r in records:
        vals[(r["arm"], r["workload"])].append(float(r["t_seconds"]))
    import statistics
    cvs = {f"{a}|{w}": (statistics.stdev(v) / statistics.mean(v) if len(v) > 1 else 0.0)
           for (a, w), v in vals.items()}
    return {
        "per_workload_cv": cvs,
        "median_cv": statistics.median(cvs.values()) if cvs else None,
        "max_cv": max(cvs.values()) if cvs else None,
        "n_groups": len(cvs),
    }