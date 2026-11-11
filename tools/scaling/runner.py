"""Measurement protocol, ledger assembly and C1..C6 acceptance verdicts.

Protocol (fixed, so that the ledger is comparable across machines):

* per core count: one warm-up run (page faults / lazy allocation) + ``reps`` timed
  runs; the warm-up is recorded but excluded from statistics;
* the order in which core counts are visited is randomised per repetition round,
  so slow drift (thermal, co-tenant load) does not align with core count;
* every run's ``checksum()`` is recorded verbatim: bitwise identical checksums
  across repetitions *and* across core counts are a hard gate (C1);
* reported time per core count is the median of the timed runs.

Acceptance criteria are evaluated mechanically from the ledger; each verdict
carries the numbers it was decided on so the report can quote them.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np

from tools.scaling.fitting import fit_amdahl, fit_gustafson
from tools.scaling.kernels import KERNELS

__all__ = [
    "measure_kernel",
    "build_ledger",
    "evaluate_acceptance",
    "environment",
    "quota_recommendation",
    "host_precheck",
]


def environment() -> Dict[str, Any]:
    """Host description: scaling numbers are meaningless without it."""
    info: Dict[str, Any] = {
        "hostname": platform.node(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "cpu_count_os": os.cpu_count(),
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    info["cpu_model"] = line.split(":", 1)[1].strip()
                    break
    except Exception:  # pragma: no cover - non-Linux
        pass
    try:
        with open("/proc/loadavg") as fh:
            parts = fh.read().split()
            info["load_1_5_15"] = [float(parts[0]), float(parts[1]), float(parts[2])]
    except Exception:  # pragma: no cover - non-Linux
        pass
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal"):
                    info["mem_total_kb"] = int(line.split()[1])
                    break
    except Exception:  # pragma: no cover
        pass
    return info


def host_precheck(
    *,
    n_max: int = 4,
    target_s: float = 0.5,
    min_speedup: float = 3.0,
) -> Dict[str, Any]:
    """Refuse to emit a ledger from a host that cannot run n processes in parallel.

    Measures an embarrassingly parallel CPU-bound workload (no synchronisation, no
    shared data) at ``1 .. n_max`` processes.  If the observed speedup is far below
    ``n_max`` the host is saturated by co-tenants: every rank then receives the
    same fixed share of the CPU at every core count, speedup collapses towards 1
    and the ledger would measure the *queue*, not the code.

    This is the host-level counterpart of the pseudo-parallel negative control:
    both exist so that "it does not scale" can never be confused with "we did not
    measure it properly".
    """
    import multiprocessing as mp

    from tools.scaling.kernels import _spin_blocks, _time_blocks

    per_block = _time_blocks(50, probe_blocks=50) / 50.0
    n_blocks = max(1, int(round(target_s / per_block)))
    ctx = mp.get_context("fork")

    def run(n_proc: int) -> float:
        barrier = ctx.Barrier(n_proc + 1)
        queue = ctx.Queue()

        def worker():
            try:
                barrier.wait(timeout=120)
                t0 = time.perf_counter()
                buf = (np.arange(32768, dtype=np.int64) % 1009) + 1
                _spin_blocks(buf, n_blocks)
                queue.put(time.perf_counter() - t0)
            except Exception:
                queue.put(float("inf"))

        procs = [ctx.Process(target=worker, daemon=True) for _ in range(n_proc)]
        for p in procs:
            p.start()
        barrier.wait(timeout=120)
        times = [queue.get(timeout=300) for _ in range(n_proc)]
        for p in procs:
            p.join(timeout=30)
        return float(max(times))

    order = list(range(1, n_max + 1))
    rng = np.random.default_rng(11)
    samples: Dict[int, List[float]] = {n: [] for n in order}
    for _ in range(3):
        rng.shuffle(order)
        for n in order:
            samples[n].append(run(n))
    med = {n: float(np.median(samples[n])) for n in order}
    speedup = {n: med[1] / med[n] for n in order}
    ok = speedup[n_max] >= min_speedup
    return {
        "n_max": n_max,
        "target_s": target_s,
        "median_s": {str(n): med[n] for n in order},
        "speedup": {str(n): speedup[n] for n in order},
        "min_speedup": min_speedup,
        "host_ok": bool(ok),
        "load_1_5_15": environment().get("load_1_5_15"),
        "verdict": (
            "host fit for scaling measurement"
            if ok
            else "HOST SATURATED: co-tenant load prevents parallel speedup; "
            "a ledger measured here describes the queue, not the kernels"
        ),
    }


def _calibrate_iterations(kernel, target_s: float, probe_iters: int = 3) -> Dict[str, Any]:
    """Size the iteration count so that the n=1 run lasts roughly ``target_s``."""
    old = getattr(kernel, "n_iter", None)
    if old is None:
        return {}
    kernel.n_iter = probe_iters
    kernel.setup(1)
    kernel.run(1)  # warm
    samples = []
    for _ in range(3):
        kernel.run(1)
        samples.append(kernel.last_elapsed_s)
    per_iter = float(np.median(samples)) / probe_iters
    kernel.n_iter = max(1, int(round(target_s / max(per_iter, 1e-12))))
    kernel.teardown()
    return {
        "calibrated": True,
        "target_serial_s": target_s,
        "per_iteration_s": per_iter,
        "n_iter": kernel.n_iter,
    }


def measure_kernel(
    spec: Dict[str, Any],
    *,
    reps: int = 5,
    seed: int = 20261111,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Run the measurement protocol for one kernel specification."""
    name = spec["name"]
    label = spec.get("label", name)
    mode = spec.get("mode", "strong")
    core_counts = [int(n) for n in spec["core_counts"]]
    params = dict(spec.get("params") or {})
    params.setdefault("mode", mode)
    cls = KERNELS[name]
    kernel = cls(**params)

    calibration: Dict[str, Any] = {}
    if spec.get("calibration") == "iterations":
        calibration = _calibrate_iterations(kernel, float(spec.get("target_serial_s", 1.5)))
    elif hasattr(kernel, "calibrate"):
        calibration = dict(kernel.calibrate())

    rng = np.random.default_rng(seed)
    times: Dict[int, List[float]] = {n: [] for n in core_counts}
    checksums: Dict[int, List[int]] = {n: [] for n in core_counts}
    warmup: Dict[int, float] = {}
    runs: List[Dict[str, Any]] = []
    wall: Dict[int, List[float]] = {n: [] for n in core_counts}

    for rep in range(reps):
        order = list(core_counts)
        rng.shuffle(order)  # randomise visitation order per round
        for n in order:
            kernel.setup(n)
            t_warm = kernel.run(n)
            warmup[n] = float(kernel.last_elapsed_s)
            kernel.run(n)
            elapsed = float(kernel.last_elapsed_s)
            cs = int(kernel.checksum())
            times[n].append(elapsed)
            checksums[n].append(cs)
            runs.append({"n": n, "rep": rep, "elapsed_s": elapsed, "checksum": cs})
            if progress:
                progress(f"{label}: n={n} rep={rep} t={elapsed:.4f}s cs={cs}")
            kernel.teardown()

    median_t = {n: float(np.median(times[n])) for n in core_counts}
    base = median_t[core_counts[0]]
    speedup = {n: (base / median_t[n] if median_t[n] > 0 else float("nan")) for n in core_counts}
    if mode == "strong":
        efficiency = {n: speedup[n] / n for n in core_counts}
    else:
        # weak scaling: work grows with n, so T(1)/T(n) already is the efficiency
        efficiency = {n: speedup[n] for n in core_counts}

    all_cs = [c for n in core_counts for c in checksums[n]]
    checksum_consistent = len(set(all_cs)) == 1
    per_n_consistent = all(len(set(checksums[n])) == 1 for n in core_counts)

    # Amdahl's law assumes a *fixed* problem size: fitting it to weak-scaling data
    # (where the work grows with n) would be a category error and yields negative
    # R^2.  Weak runs therefore get Gustafson only.
    fit = fit_amdahl(times, seed=seed) if (mode == "strong" and 1 in core_counts) else None
    result: Dict[str, Any] = {
        "label": label,
        "kernel": name,
        "mode": mode,
        "core_counts": core_counts,
        "description": kernel.describe() if hasattr(kernel, "describe") else {},
        "calibration": calibration,
        "reps": reps,
        "warmup_s": {str(n): warmup[n] for n in core_counts},
        "times_s": {str(n): times[n] for n in core_counts},
        "median_s": {str(n): median_t[n] for n in core_counts},
        "speedup": {str(n): speedup[n] for n in core_counts},
        "efficiency": {str(n): efficiency[n] for n in core_counts},
        "checksums": {str(n): checksums[n] for n in core_counts},
        "checksum_consistent_all": checksum_consistent,
        "checksum_consistent_per_n": per_n_consistent,
        "runs": runs,
        "amdahl": fit.to_dict() if fit else None,
    }
    if mode == "weak":
        result["gustafson"] = fit_gustafson({n: efficiency[n] for n in core_counts})
    return result


def recompute_entry(entry: Dict[str, Any], *, seed: int = 20261111) -> Dict[str, Any]:
    """Recompute medians / speedups / fits / checksum flags from the raw ledger data.

    The ledger keeps every raw timing and checksum precisely so that an analysis
    fix (e.g. a wrong model applied to weak-scaling data) does not require
    re-measuring the machine.  This function is the single place that derives the
    statistics, and it is what the acceptance verdicts read.
    """
    times = {int(n): [float(v) for v in vals] for n, vals in entry["times_s"].items()}
    checksums = {int(n): [int(c) for c in vals] for n, vals in entry["checksums"].items()}
    core_counts = sorted(times)
    mode = entry["mode"]
    median_t = {n: float(np.median(times[n])) for n in core_counts}
    base = median_t[core_counts[0]]
    speedup = {n: (base / median_t[n] if median_t[n] > 0 else float("nan")) for n in core_counts}
    efficiency = {n: (speedup[n] / n if mode == "strong" else speedup[n]) for n in core_counts}
    all_cs = [c for n in core_counts for c in checksums[n]]
    entry.update(
        {
            "core_counts": core_counts,
            "median_s": {str(n): median_t[n] for n in core_counts},
            "speedup": {str(n): speedup[n] for n in core_counts},
            "efficiency": {str(n): efficiency[n] for n in core_counts},
            "checksum_consistent_all": len(set(all_cs)) == 1,
            "checksum_consistent_per_n": all(len(set(checksums[n])) == 1 for n in core_counts),
        }
    )
    fit = fit_amdahl(times, seed=seed) if (mode == "strong" and 1 in core_counts) else None
    entry["amdahl"] = fit.to_dict() if fit else None
    if mode == "weak":
        entry["gustafson"] = fit_gustafson({n: efficiency[n] for n in core_counts})
    return entry


def recompute_ledger(ledger: Dict[str, Any], *, seed: int = 20261111) -> Dict[str, Any]:
    for entry in ledger["kernels"].values():
        recompute_entry(entry, seed=seed)
    return ledger


def build_ledger(
    plan: Dict[str, Any],
    progress: Optional[Callable[[str], None]] = None,
    host_check: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute every kernel in ``plan`` and assemble the ledger document."""
    reps = int(plan.get("reps", 5))
    seed = int(plan.get("seed", 20261111))
    ledger: Dict[str, Any] = {
        "schema": "scaling_ledger/v1",
        "environment": environment(),
        "host_precheck": host_check,
        "protocol": {
            "reps": reps,
            "core_order": "randomised per repetition round",
            "statistic": "median of timed runs",
            "warmup_runs": 1,
            "checksum": "integer, reduced modulo 2**64 (partition invariant)",
            "timing": "perf_counter around the barrier-bounded parallel region",
            "injected_serial_fraction": "calibrated on the measured machine, not assumed",
        },
        "kernels": {},
    }
    for spec in plan["kernels"]:
        if progress:
            progress(f"== {spec.get('label', spec['name'])} ({spec.get('mode', 'strong')}) ==")
        ledger["kernels"][spec.get("label", spec["name"])] = measure_kernel(
            spec, reps=reps, seed=seed, progress=progress
        )
    return ledger


def quota_recommendation(
    core_counts: Sequence[int],
    efficiency: Dict[str, float],
    *,
    min_efficiency: float = 0.5,
    min_marginal_gain: float = 1.15,
) -> Dict[str, Any]:
    """Largest core count that still buys throughput.

    A core count qualifies when (a) efficiency vs the 1-core baseline is at least
    ``min_efficiency`` and (b) adding it over the previous measured point still
    raises throughput by at least ``min_marginal_gain``.  Above that point extra
    cores are pure wall-clock waste for this kernel.
    """
    ns = sorted(int(n) for n in core_counts)
    eff = {int(k): float(v) for k, v in efficiency.items()}
    recommended = ns[0]
    marginal: Dict[str, float] = {}
    for prev, cur in zip(ns, ns[1:]):
        # throughput ratio = (S(cur)/cur) / (S(prev)/prev) ... expressed via efficiency
        gain = (eff[cur] * cur) / max(eff[prev] * prev, 1e-12)
        marginal[str(cur)] = gain
        if eff[cur] >= min_efficiency and gain >= min_marginal_gain:
            recommended = cur
    return {
        "recommended_max_cores": recommended,
        "min_efficiency": min_efficiency,
        "min_marginal_gain": min_marginal_gain,
        "marginal_throughput_gain": marginal,
        "efficiency": {str(k): eff[k] for k in ns},
    }


def _get(d: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    return d.get(key)


def evaluate_acceptance(ledger: Dict[str, Any], *, figures: Optional[List[str]] = None) -> Dict[str, Any]:
    """Mechanically evaluate C1..C6 from a ledger document."""
    k = ledger["kernels"]
    checks: Dict[str, Any] = {}

    def kernel_by(kernel_name: str, mode: str, label_hint: Optional[str] = None):
        for lab, data in k.items():
            if data["kernel"] != kernel_name:
                continue
            if data["mode"] != mode:
                continue
            if label_hint and label_hint not in lab:
                continue
            return data
        return None

    # ---- C1: bitwise checksum across all repetitions and core counts -------
    c1: Dict[str, Any] = {"criterion": "all checksums bitwise identical per kernel", "detail": {}}
    c1_pass = True
    for lab, data in k.items():
        ok = data["checksum_consistent_all"] if data["mode"] == "strong" else data["checksum_consistent_per_n"]
        c1["detail"][lab] = {
            "mode": data["mode"],
            "consistent": bool(ok),
            "distinct_checksums": len({c for n in data["checksums"] for c in data["checksums"][n]}),
        }
        c1_pass = c1_pass and bool(ok)
    c1["pass"] = bool(c1_pass)

    # ---- C2: strong-scaling efficiency of the compute-bound stencil --------
    stencil_strong = kernel_by("stencil5p", "strong")
    c2: Dict[str, Any] = {"criterion": "stencil strong: E(8) >= 0.70 and E(16) >= 0.50"}
    if stencil_strong:
        eff = stencil_strong["efficiency"]
        e8 = eff.get("8")
        e16 = eff.get("16")
        c2.update({"E8": e8, "E16": e16, "label": stencil_strong["label"]})
        c2["pass"] = bool(e8 is not None and e16 is not None and e8 >= 0.70 and e16 >= 0.50)
    else:
        c2["pass"] = False
        c2["error"] = "stencil strong-scaling measurement missing"
    c2["thresholds"] = {"E8": 0.70, "E16": 0.50}

    # ---- C3: weak-scaling efficiency of the stencil at 8 cores -------------
    stencil_weak = kernel_by("stencil5p", "weak")
    c3: Dict[str, Any] = {"criterion": "stencil weak: E(8) >= 0.85"}
    if stencil_weak:
        e8 = stencil_weak["efficiency"].get("8")
        c3.update({"E8": e8, "label": stencil_weak["label"]})
        c3["pass"] = bool(e8 is not None and e8 >= 0.85)
    else:
        c3["pass"] = False
        c3["error"] = "stencil weak-scaling measurement missing"
    c3["thresholds"] = {"E8": 0.85}

    # ---- C4: Amdahl s recovery of the injected serial fractions -----------
    c4: Dict[str, Any] = {
        "criterion": "serial-fraction families: R2 >= 0.98 and |s_hat - s_injected| <= 3pp (95% CI covers truth)",
        "detail": {},
    }
    c4_pass = True
    families = [d for d in k.values() if d["kernel"] == "serial_frac"]
    target_map = {"s05": 0.05, "s20": 0.20}
    if not families:
        c4["pass"] = False
        c4["error"] = "no serial_frac measurements"
    for data in families:
        truth = float(data["calibration"].get("s_measured", float("nan")))
        fit = data["amdahl"] or {}
        s_hat = fit.get("s")
        err = abs(float(s_hat) - truth) if s_hat is not None else float("nan")
        covers = bool(
            fit.get("ci_low") is not None and fit.get("ci_high") is not None
            and fit["ci_low"] <= truth <= fit["ci_high"]
        )
        ok = bool(
            fit.get("r2", 0.0) >= 0.98 and err <= 0.03 and covers
        )
        c4["detail"][data["label"]] = {
            "s_injected_nominal": data["calibration"].get("target_s"),
            "s_injected_measured": truth,
            "s_hat": s_hat,
            "abs_error_pp": err * 100.0,
            "ci": [fit.get("ci_low"), fit.get("ci_high")],
            "r2": fit.get("r2"),
            "ci_covers_truth": covers,
            "pass": ok,
        }
        c4_pass = c4_pass and ok
    c4["pass"] = bool(c4_pass)

    # ---- C5: pseudo-parallel negative control ----------------------------
    pseudo = kernel_by("pseudo_par", "strong")
    c5: Dict[str, Any] = {
        "criterion": "pseudo-parallel kernel judged serial: s_hat >= 0.90, CI lower bound >= 0.85, E(16) <= 0.15"
    }
    if pseudo:
        fit = pseudo["amdahl"] or {}
        s_hat = fit.get("s")
        ci_low = fit.get("ci_low")
        e16 = pseudo["efficiency"].get("16")
        c5.update(
            {
                "label": pseudo["label"],
                "s_hat": s_hat,
                "ci_low": ci_low,
                "E16": e16,
                "degenerate_flat_objective": fit.get("degenerate_flat_objective"),
            }
        )
        c5["pass"] = bool(
            s_hat is not None
            and s_hat >= 0.90
            and (ci_low is None or ci_low >= 0.85)
            and e16 is not None
            and e16 <= 0.15
        )
    else:
        c5["pass"] = False
        c5["error"] = "pseudo-parallel control missing"

    # ---- C6: figures + quota advice ---------------------------------------
    quotas = {}
    for lab, data in k.items():
        quotas[lab] = quota_recommendation(data["core_counts"], data["efficiency"])
    c6: Dict[str, Any] = {
        "criterion": "per-kernel efficiency curves delivered + core quota recommendation",
        "figures": figures or [],
        "quota": quotas,
        "pass": bool(len(figures or []) >= 2 and all("recommended_max_cores" in q for q in quotas.values())),
    }

    checks.update({"C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5, "C6": c6})
    all_pass = all(bool(checks[c]["pass"]) for c in checks)
    return {"criteria": checks, "all_pass": all_pass, "n_criteria": len(checks), "n_pass": sum(1 for c in checks.values() if c["pass"])}