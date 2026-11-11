"""Tests for the parallel scalability ledger harness (tools/scaling).

Covered:
* deterministic partitioning and mod-2**64 reduction (the C1 foundation),
* bitwise partition-invariant checksums for every kernel family,
* Amdahl parameter recovery on synthetic data with known truth,
* the pseudo-parallel negative control being flagged as serial,
* acceptance logic rejecting bad ledgers (C1/C2/C5 negative tests),
* worker exceptions surfacing instead of hanging the coordinator.
"""
from __future__ import annotations

import numpy as np
import pytest

from tools.scaling.engine import WorkerError, WorkerPool
from tools.scaling.fitting import amdahl_speedup, fit_amdahl, fit_gustafson
from tools.scaling.kernels import (
    HaloRingKernel,
    PseudoParallelKernel,
    SerialFractionKernel,
    StencilKernel,
)
from tools.scaling.protocol import combine_mod2_64, split_ranges
from tools.scaling.runner import evaluate_acceptance, host_precheck, quota_recommendation


# ---------------------------------------------------------------------------
# protocol primitives
# ---------------------------------------------------------------------------
def test_split_ranges_is_balanced_and_deterministic():
    for total, parts in [(10, 3), (7, 7), (1, 4), (0, 2), (4096, 32)]:
        ranges = split_ranges(total, parts)
        assert len(ranges) == parts
        assert ranges[0][0] == 0
        assert ranges[-1][1] == total
        sizes = [hi - lo for lo, hi in ranges]
        assert max(sizes) - min(sizes) <= 1
        # contiguous, no gaps / overlaps
        for (a0, a1), (b0, b1) in zip(ranges, ranges[1:]):
            assert a1 == b0
        assert ranges == split_ranges(total, parts)


def test_split_ranges_rejects_bad_input():
    with pytest.raises(ValueError):
        split_ranges(10, 0)
    with pytest.raises(ValueError):
        split_ranges(-1, 2)


def test_combine_mod2_64_is_regrouping_invariant():
    values = [2**63 - 1, 2**63, -5, 123456789012345]
    a = combine_mod2_64(values)
    b = combine_mod2_64(list(reversed(values)))
    assert a == b
    assert 0 <= a < 2**64


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------
def test_worker_exception_is_reported_not_hung():
    def body(rank, n):
        if rank == 1:
            raise RuntimeError("boom")
        return {"rank": rank}

    pool = WorkerPool(2, body, timeout=20)
    try:
        with pytest.raises(WorkerError):
            pool.run()
    finally:
        pool.close()


def test_pool_runs_expected_number_of_ranks():
    # `seen` must travel back through the payload queue: children are forks, so a
    # parent-side list would stay empty (a classic fork-semantics trap).
    def body(rank, n):
        return {"rank": rank, "n": n}

    with WorkerPool(3, body, timeout=20) as pool:
        res = pool.run()
    assert sorted(p["rank"] for p in res.payloads) == [0, 1, 2]
    assert all(p["n"] == 3 for p in res.payloads)
    assert res.elapsed_s >= 0.0


# ---------------------------------------------------------------------------
# kernels: checksum must be bitwise identical for every partition
# ---------------------------------------------------------------------------
def test_stencil_checksum_is_partition_invariant():
    k = StencilKernel(nx=128, ny=96, n_iter=3)
    sums = []
    for n in (1, 2, 3, 4):
        k.setup(n)
        k.run(n)
        sums.append(k.checksum())
        k.teardown()
    assert len(set(sums)) == 1, sums


def test_stencil_checksum_stable_across_repeats():
    # Reproducibility of the measurement protocol: each timed run starts from a
    # freshly initialised state, so the fingerprint must repeat exactly.  (Running
    # twice *without* setup would legitimately advance the stencil.)
    k = StencilKernel(nx=64, ny=64, n_iter=2)
    prints = []
    for _ in range(3):
        k.setup(2)
        k.run(2)
        prints.append(k.checksum())
        k.teardown()
    assert len(set(prints)) == 1, prints


def test_halo_ring_checksum_is_partition_invariant():
    k = HaloRingKernel(total_elems=256, n_iter=5)
    sums = []
    for n in (1, 2, 4):
        k.setup(n)
        k.run(n)
        sums.append(k.checksum())
        k.teardown()
    assert len(set(sums)) == 1, sums


def test_serial_fraction_checksum_is_partition_invariant():
    k = SerialFractionKernel(target_s=0.1, target_total_s=0.05, parallel_blocks=20, serial_blocks=4)
    sums = []
    for n in (1, 2, 4):
        k.setup(n)
        k.run(n)
        sums.append(k.checksum())
        k.teardown()
    assert len(set(sums)) == 1, sums


def test_pseudo_parallel_kernel_runs_and_is_deterministic():
    k = PseudoParallelKernel(total_blocks=20)
    sums = []
    for n in (1, 2, 4):
        k.setup(n)
        k.run(n)
        sums.append(k.checksum())
        k.teardown()
    assert len(set(sums)) == 1, sums


def test_serial_fraction_calibration_hits_injected_fraction():
    k = SerialFractionKernel(target_s=0.2, target_total_s=0.3)
    calib = k.calibrate()
    k.teardown()
    # The feedback loop targets the measured machine, so the recovered fraction
    # must track the request.  The tolerance is widened by the machine's own
    # fixed per-run overhead (a busy host cannot resolve a small s -- that is a
    # property of the host, not of the harness, and is reported in the ledger).
    total = calib["t_parallel_s"] + calib["t_serial_s"]
    tol = 0.04 + 2.0 * calib["fixed_overhead_s"] / max(total, 1e-9)
    assert abs(calib["s_measured"] - 0.2) <= tol, (calib, tol)
    assert calib["t_parallel_s"] > 0
    assert calib["t_serial_s"] > 0


# ---------------------------------------------------------------------------
# host fitness precheck
# ---------------------------------------------------------------------------
def test_host_precheck_reports_speedup_and_verdict():
    out = host_precheck(n_max=2, target_s=0.05, min_speedup=0.5)
    assert set(out["speedup"]) == {"1", "2"}
    assert out["speedup"]["1"] == pytest.approx(1.0, abs=1e-9)
    assert 0.0 < out["speedup"]["2"] < 10.0
    assert out["host_ok"] is bool(out["speedup"]["2"] >= 0.5)
    assert isinstance(out["verdict"], str)


def test_host_precheck_rejects_impossible_gate():
    # A gate no host can meet must flip the verdict to "not fit" (the refusal path
    # the CLI takes on a saturated machine).
    out = host_precheck(n_max=2, target_s=0.05, min_speedup=1e6)
    assert out["host_ok"] is False
    assert "SATURATED" in out["verdict"]


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------
def _synthetic_times(s_true: float, ns=(1, 2, 4, 8, 16, 32), t1=1.0, noise=0.0, seed=3):
    rng = np.random.default_rng(seed)
    times = {}
    for n in ns:
        model = t1 * (s_true + (1.0 - s_true) / n)
        reps = [model * (1.0 + noise * rng.standard_normal()) for _ in range(5)]
        times[n] = [max(t, 1e-6) for t in reps]
    return times


@pytest.mark.parametrize("s_true", [0.0, 0.05, 0.10, 0.20, 0.50])
def test_fit_amdahl_recovers_injected_fraction(s_true):
    fit = fit_amdahl(_synthetic_times(s_true, noise=0.01), n_boot=200)
    assert abs(fit.s - s_true) <= 0.03, (s_true, fit.s)
    assert fit.r2 >= 0.98
    # s is bounded below by 0, so a tiny floating-point slack is expected at s=0
    assert fit.ci_low - 1e-9 <= s_true <= fit.ci_high + 1e-9


def test_fit_amdahl_flags_serial_workload():
    # speedup ~1 at every core count == all work serial
    fit = fit_amdahl(_synthetic_times(0.999, noise=0.01), n_boot=200)
    assert fit.s >= 0.90
    assert fit.ci_low >= 0.85


def test_amdahl_speedup_limits():
    assert np.isclose(amdahl_speedup(np.array([1.0]), 0.3)[0], 1.0)
    assert amdahl_speedup(np.array([1e6]), 0.3)[0] == pytest.approx(1 / 0.3, rel=1e-3)


def test_gustafson_fit_on_perfect_weak_scaling():
    eff = {1: 1.0, 2: 1.0, 4: 1.0, 8: 1.0}
    out = fit_gustafson(eff)
    assert out["s_gustafson"] == pytest.approx(0.0, abs=0.02)
    assert out["r2"] > 0.99


# ---------------------------------------------------------------------------
# quota + acceptance logic (negative controls included)
# ---------------------------------------------------------------------------
def test_quota_recommendation_caps_at_saturation():
    eff = {"1": 1.0, "2": 0.95, "4": 0.90, "8": 0.40, "16": 0.1}
    q = quota_recommendation([1, 2, 4, 8, 16], eff)
    assert q["recommended_max_cores"] == 4


def _entry(kernel_name, mode, label, times, checksums=None, core_counts=(1, 2, 4, 8, 16, 32)):
    from tools.scaling.fitting import fit_amdahl

    fit = fit_amdahl(times, n_boot=100)
    med = {n: float(np.median(times[n])) for n in times}
    base = med[min(times)]
    speedup = {n: base / med[n] for n in times}
    eff = {n: (speedup[n] / n if mode == "strong" else speedup[n]) for n in times}
    cs = checksums or {"all": 42}
    if "all" in cs:
        checksums = {str(n): [cs["all"]] * 5 for n in times}
    all_cs = {c for n in checksums for c in checksums[n]}
    return {
        "label": label,
        "kernel": kernel_name,
        "mode": mode,
        "core_counts": list(core_counts),
        "calibration": {"s_measured": 0.05, "target_s": 0.05},
        "checksums": {str(k): v for k, v in checksums.items()},
        "checksum_consistent_all": len(all_cs) == 1,
        "checksum_consistent_per_n": all(len(set(v)) == 1 for v in checksums.values()),
        "efficiency": {str(n): eff[n] for n in times},
        "speedup": {str(n): speedup[n] for n in times},
        "amdahl": fit.to_dict(),
    }


def _good_ledger():
    kernels = {
        "stencil5p_strong": _entry(
            "stencil5p", "strong", "stencil5p_strong", _synthetic_times(0.02, noise=0.005)
        ),
        "stencil5p_weak": _entry(
            "stencil5p", "weak", "stencil5p_weak", _synthetic_times(0.01, noise=0.005)
        ),
        "serial_frac_s05": _entry(
            "serial_frac", "strong", "serial_frac_s05", _synthetic_times(0.05, noise=0.005)
        ),
        "pseudo_par_control": _entry(
            "pseudo_par", "strong", "pseudo_par_control", _synthetic_times(0.999, noise=0.005)
        ),
    }
    return {"kernels": kernels}


def test_acceptance_passes_on_synthetic_good_ledger():
    verdict = evaluate_acceptance(_good_ledger(), figures=["a.png", "b.png"])
    # stencil at s=0.02 scales well -> C2/C3 pass; C1/C4/C5/C6 pass by construction
    assert verdict["criteria"]["C1"]["pass"]
    assert verdict["criteria"]["C2"]["pass"]
    assert verdict["criteria"]["C4"]["pass"]
    assert verdict["criteria"]["C5"]["pass"]
    assert verdict["criteria"]["C6"]["pass"]


def test_acceptance_fails_on_checksum_mismatch():
    ledger = _good_ledger()
    ledger["kernels"]["stencil5p_strong"]["checksum_consistent_all"] = False
    ledger["kernels"]["stencil5p_strong"]["checksums"]["8"] = [1, 2, 3, 4, 5]
    verdict = evaluate_acceptance(ledger, figures=["a.png", "b.png"])
    assert verdict["criteria"]["C1"]["pass"] is False


def test_acceptance_fails_when_parallel_kernel_does_not_scale():
    ledger = _good_ledger()
    # stencil that barely scales -> C2 must go red
    ledger["kernels"]["stencil5p_strong"] = _entry(
        "stencil5p", "strong", "stencil5p_strong", _synthetic_times(0.6, noise=0.005)
    )
    verdict = evaluate_acceptance(ledger, figures=["a.png", "b.png"])
    assert verdict["criteria"]["C2"]["pass"] is False


def test_acceptance_fails_when_pseudo_control_looks_parallel():
    ledger = _good_ledger()
    ledger["kernels"]["pseudo_par_control"] = _entry(
        "pseudo_par", "strong", "pseudo_par_control", _synthetic_times(0.02, noise=0.005)
    )
    verdict = evaluate_acceptance(ledger, figures=["a.png", "b.png"])
    assert verdict["criteria"]["C5"]["pass"] is False


def test_acceptance_requires_figures():
    ledger = _good_ledger()
    verdict = evaluate_acceptance(ledger, figures=[])
    assert verdict["criteria"]["C6"]["pass"] is False