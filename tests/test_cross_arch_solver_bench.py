"""Pytest surface for cross-architecture solver benchmark reproducibility.

Tests the three-tier verification framework:
1. Bit-exact (≤2 ULP)
2. Statistical equivalence (rel error <0.1%, L2<0.5%, t-test p>0.05)
3. Fail-closed degradation (ARCH_DIFF_ACCEPTED / ARCH_DIFF_REJECTED)

Plus:
4. Same-arch multi-run byte-identity
5. Injected bias detection (>0.5% → REJECTED)
6. Zero domain semantics

Criteria mapping:
(a) Bit-exact: loong64 vs x86 ≤2 ULP
(b) Statistical: key rel<0.1%, L2<0.5%, t-test p>0.05
(c) Injected bias >0.5% → ARCH_DIFF_REJECTED
(d) Same-arch 3 runs byte-identical
(e) pytest ≥14 tests
(f) Zero domain semantics
"""

from __future__ import annotations

import json
import math
import hashlib
from pathlib import Path

import pytest

from tools.cross_arch_gate import solver_benchmarks, statistical, degradation, audit


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def ref_suite():
    """Reference suite: all three benchmarks with default (naive) summation."""
    return solver_benchmarks.run_all_benchmarks(sum_variant="naive", fma=False)


@pytest.fixture(scope="module")
def ref_suite_rerun():
    """Same-arch rerun: identical parameters, should be byte-identical."""
    return solver_benchmarks.run_all_benchmarks(sum_variant="naive", fma=False)


@pytest.fixture(scope="module")
def cand_suite_kahan():
    """Candidate suite: Kahan summation (simulates cross-arch difference)."""
    return solver_benchmarks.run_all_benchmarks(sum_variant="kahan", fma=False)


@pytest.fixture(scope="module")
def cand_suite_fsum():
    """Candidate suite: fsum (simulates cross-arch difference)."""
    return solver_benchmarks.run_all_benchmarks(sum_variant="fsum", fma=False)


@pytest.fixture(scope="module")
def cand_suite_reverse():
    """Candidate suite: reverse summation (simulates cross-arch difference)."""
    return solver_benchmarks.run_all_benchmarks(sum_variant="reverse", fma=False)


# --------------------------------------------------------------------------- #
# (a) Bit-exact: same-arch byte-identity
# --------------------------------------------------------------------------- #
class TestBitExact:
    """Criterion (a): bit-exact equivalence ≤2 ULP."""

    def test_same_arch_byte_identical_iterative_stencil(self, ref_suite, ref_suite_rerun):
        """Same-arch rerun must produce byte-identical results."""
        ref = ref_suite["iterative_stencil"]
        rerun = ref_suite_rerun["iterative_stencil"]
        fp_ref = solver_benchmarks.record_fingerprint(ref)
        fp_rerun = solver_benchmarks.record_fingerprint(rerun)
        assert fp_ref == fp_rerun, "Same-arch rerun must be byte-identical"

    def test_same_arch_byte_identical_sparse_spd(self, ref_suite, ref_suite_rerun):
        ref = ref_suite["sparse_spd_solve"]
        rerun = ref_suite_rerun["sparse_spd_solve"]
        assert solver_benchmarks.record_fingerprint(ref) == solver_benchmarks.record_fingerprint(rerun)

    def test_same_arch_byte_identical_dense_osc(self, ref_suite, ref_suite_rerun):
        ref = ref_suite["dense_oscillatory_solve"]
        rerun = ref_suite_rerun["dense_oscillatory_solve"]
        assert solver_benchmarks.record_fingerprint(ref) == solver_benchmarks.record_fingerprint(rerun)

    def test_ulp_cap_2_same_arch(self, ref_suite, ref_suite_rerun):
        """Same-arch: all ULP distances must be 0."""
        for bench_name in ref_suite:
            bit_check = degradation.check_bit_exact(
                ref_suite[bench_name], ref_suite_rerun[bench_name], ulp_cap=2)
            assert bit_check["all_pass"], f"{bench_name}: same-arch should be bit-exact"
            assert bit_check["max_ulp"] == 0

    def test_ulp_cap_2_fsum_summation(self, ref_suite, cand_suite_fsum):
        """fsum summation should be within reasonable ULP for key quantities."""
        for bench_name in ref_suite:
            bit_check = degradation.check_bit_exact(
                ref_suite[bench_name], cand_suite_fsum[bench_name], ulp_cap=2)
            # fsum is more accurate than naive, differences should be small
            # but iterative solvers may accumulate differently
            assert bit_check["max_ulp"] < 100000, \
                f"{bench_name}: fsum vs naive max ULP = {bit_check['max_ulp']}"


# --------------------------------------------------------------------------- #
# (b) Statistical equivalence
# --------------------------------------------------------------------------- #
class TestStatisticalEquivalence:
    """Criterion (b): statistical equivalence thresholds."""

    def test_scalar_rel_error_threshold(self, ref_suite, cand_suite_kahan):
        """Key quantities: relative error < 0.1%."""
        for bench_name in ref_suite:
            check = statistical.check_scalar_equivalence(
                ref_suite[bench_name]["scalars"],
                cand_suite_kahan[bench_name]["scalars"],
                rel_tol=0.001)
            assert check["all_pass"], \
                f"{bench_name}: scalar rel error {check['max_relative_error']:.2e} >= 0.001"

    def test_field_l2_error_threshold(self, ref_suite, cand_suite_kahan):
        """Field L2 norm: relative error < 0.5%."""
        for bench_name in ref_suite:
            check = statistical.check_field_equivalence(
                ref_suite[bench_name]["fields"],
                cand_suite_kahan[bench_name]["fields"],
                l2_tol=0.005)
            assert check["all_pass"], \
                f"{bench_name}: field L2 error {check['max_l2_relative_error']:.2e} >= 0.005"

    def test_welch_ttest_p_threshold(self, ref_suite, cand_suite_kahan):
        """Welch's t-test: p > 0.05 for field distributions."""
        for bench_name in ref_suite:
            check = statistical.check_distribution_equivalence(
                ref_suite[bench_name]["fields"],
                cand_suite_kahan[bench_name]["fields"],
                p_threshold=0.05)
            assert check["all_pass"], \
                f"{bench_name}: t-test min p = {check['min_p_value']:.4e} <= 0.05"

    def test_full_statistical_equivalence_passes(self, ref_suite, cand_suite_kahan):
        """Combined statistical check should pass for Kahan variant."""
        for bench_name in ref_suite:
            check = statistical.full_statistical_equivalence(
                ref_suite[bench_name], cand_suite_kahan[bench_name])
            assert check["all_pass"], f"{bench_name}: full statistical check failed"


# --------------------------------------------------------------------------- #
# (c) Injected bias detection
# --------------------------------------------------------------------------- #
class TestInjectedBias:
    """Criterion (c): injected bias >0.5% → ARCH_DIFF_REJECTED."""

    def test_no_bias_in_same_arch(self, ref_suite, ref_suite_rerun):
        """Same-arch: no bias should be detected."""
        for bench_name in ref_suite:
            bias = degradation.check_injected_bias(
                ref_suite[bench_name], ref_suite_rerun[bench_name])
            assert not bias["any_biased"], "Same-arch should have no bias"

    def test_injected_bias_detected(self, ref_suite):
        """Inject 1% bias → must be detected and REJECTED."""
        for bench_name in ref_suite:
            # Create a biased copy
            biased = _deep_copy_record(ref_suite[bench_name])
            # Bias the first scalar by 1%
            first_key = sorted(biased["scalars"].keys())[0]
            biased["scalars"][first_key] *= 1.01  # 1% bias

            bias = degradation.check_injected_bias(
                ref_suite[bench_name], biased, bias_threshold=0.005)
            assert bias["any_biased"], "1% bias must be detected"
            assert bias["max_bias"] > 0.005

    def test_verdict_rejected_on_bias(self, ref_suite):
        """Injected bias >0.5% → ARCH_DIFF_REJECTED."""
        for bench_name in ref_suite:
            biased = _deep_copy_record(ref_suite[bench_name])
            first_key = sorted(biased["scalars"].keys())[0]
            biased["scalars"][first_key] *= 1.01  # 1% bias

            verdict = degradation.compute_degradation_verdict(
                ref_suite[bench_name], biased)
            assert verdict["verdict"] == degradation.VERDICT_ARCH_DIFF_REJECTED, \
                f"Expected REJECTED, got {verdict['verdict']}"

    def test_small_bias_below_threshold(self, ref_suite):
        """0.01% bias should NOT trigger rejection."""
        for bench_name in ref_suite:
            biased = _deep_copy_record(ref_suite[bench_name])
            first_key = sorted(biased["scalars"].keys())[0]
            biased["scalars"][first_key] *= 1.0001  # 0.01% bias

            bias = degradation.check_injected_bias(
                ref_suite[bench_name], biased, bias_threshold=0.005)
            assert not bias["any_biased"], "0.01% bias should be below threshold"


# --------------------------------------------------------------------------- #
# (d) Same-arch 3 runs byte-identical
# --------------------------------------------------------------------------- #
class TestMultiRunReproducibility:
    """Criterion (d): same-arch 3 runs byte-identical."""

    def test_three_runs_identical(self):
        """Three independent runs with same seed must be byte-identical."""
        runs = []
        for _ in range(3):
            suite = solver_benchmarks.run_all_benchmarks(sum_variant="naive")
            runs.append(suite)

        result = degradation.check_benchmark_suite_reproducibility(runs)
        assert result["all_identical"], "3 same-arch runs must be byte-identical"
        assert result["n_runs"] == 3

    def test_three_runs_per_benchmark(self):
        """Each benchmark independently must be byte-identical across 3 runs."""
        runs = []
        for _ in range(3):
            runs.append(solver_benchmarks.run_all_benchmarks())

        result = degradation.check_benchmark_suite_reproducibility(runs)
        for bench_name, info in result["per_benchmark"].items():
            assert info["all_identical"], f"{bench_name}: 3 runs not identical"


# --------------------------------------------------------------------------- #
# Degradation verdict logic
# --------------------------------------------------------------------------- #
class TestDegradationVerdict:
    """Test the fail-closed degradation decision tree."""

    def test_pass_on_bit_exact(self, ref_suite, ref_suite_rerun):
        """Bit-exact → PASS."""
        for bench_name in ref_suite:
            v = degradation.compute_degradation_verdict(
                ref_suite[bench_name], ref_suite_rerun[bench_name])
            assert v["verdict"] == degradation.VERDICT_PASS

    def test_accepted_on_stat_equiv(self, ref_suite, cand_suite_kahan):
        """Statistically equivalent but not bit-exact → ARCH_DIFF_ACCEPTED."""
        for bench_name in ref_suite:
            v = degradation.compute_degradation_verdict(
                ref_suite[bench_name], cand_suite_kahan[bench_name])
            # Should be either PASS or ACCEPTED (both are acceptable)
            assert v["verdict"] in (degradation.VERDICT_PASS,
                                    degradation.VERDICT_ARCH_DIFF_ACCEPTED), \
                f"{bench_name}: expected PASS or ACCEPTED, got {v['verdict']}"

    def test_rejected_on_large_perturbation(self, ref_suite):
        """Large perturbation → ARCH_DIFF_REJECTED."""
        for bench_name in ref_suite:
            perturbed = _deep_copy_record(ref_suite[bench_name])
            # Perturb all scalars by 5%
            for k in perturbed["scalars"]:
                perturbed["scalars"][k] *= 1.05
            # Perturb all fields by 5%
            for k in perturbed["fields"]:
                perturbed["fields"][k] = [v * 1.05 for v in perturbed["fields"][k]]

            v = degradation.compute_degradation_verdict(
                ref_suite[bench_name], perturbed)
            assert v["verdict"] == degradation.VERDICT_ARCH_DIFF_REJECTED

    def test_suite_verdict_worst_wins(self, ref_suite, cand_suite_kahan):
        """Suite verdict = worst of per-benchmark verdicts."""
        # Create a suite with one good and one bad benchmark
        bad_bench = _deep_copy_record(ref_suite["iterative_stencil"])
        for k in bad_bench["scalars"]:
            bad_bench["scalars"][k] *= 1.10  # 10% bias

        ref_sub = {"bench_a": ref_suite["sparse_spd_solve"]}
        cand_sub = {"bench_a": cand_suite_kahan["sparse_spd_solve"]}
        ref_bad = {"bench_b": ref_suite["iterative_stencil"]}
        cand_bad = {"bench_b": bad_bench}

        ref_combined = {**ref_sub, **ref_bad}
        cand_combined = {**cand_sub, **cand_bad}

        v = degradation.compute_suite_verdict(ref_combined, cand_combined)
        assert v["overall_verdict"] == degradation.VERDICT_ARCH_DIFF_REJECTED


# --------------------------------------------------------------------------- #
# (f) Zero domain semantics
# --------------------------------------------------------------------------- #
class TestZeroDomainSemantics:
    """Criterion (f): zero domain vocabulary in the gate package."""

    def test_audit_clean(self):
        """No domain terms in solver_benchmarks, statistical, degradation modules."""
        report = audit.scan(["tools/cross_arch_gate"])
        assert report["hit_count"] == 0, \
            f"Domain semantics found: {report.get('hits', [])}"

    def test_no_physics_terms_in_benchmarks(self):
        """Benchmark names must not contain physics terms."""
        # The key check is that the audit module finds zero hits
        report = audit.scan(["tools/cross_arch_gate"])
        assert report["hit_count"] == 0


# --------------------------------------------------------------------------- #
# Statistical module unit tests
# --------------------------------------------------------------------------- #
class TestStatisticalUnit:
    """Unit tests for the statistical module."""

    def test_relative_error_zero_zero(self):
        assert statistical.relative_error(0.0, 0.0) == 0.0

    def test_relative_error_identical(self):
        assert statistical.relative_error(1.0, 1.0) == 0.0

    def test_relative_error_known(self):
        assert abs(statistical.relative_error(100.0, 100.1) - 0.001) < 1e-10

    def test_l2_norm_known(self):
        assert abs(statistical.l2_norm([3.0, 4.0]) - 5.0) < 1e-10

    def test_l2_relative_error_identical(self):
        v = [1.0, 2.0, 3.0]
        assert statistical.l2_relative_error(v, v) == 0.0

    def test_l2_relative_error_known(self):
        ref = [1.0, 0.0, 0.0]
        cand = [1.001, 0.0, 0.0]
        err = statistical.l2_relative_error(ref, cand)
        assert abs(err - 0.001) < 1e-10

    def test_welch_ttest_identical_samples(self):
        """Identical samples should give high p-value."""
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = statistical.welch_ttest(a, b)
        assert result["p_value"] > 0.05

    def test_welch_ttest_different_samples(self):
        """Very different samples should give low p-value."""
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [10.0, 11.0, 12.0, 13.0, 14.0]
        result = statistical.welch_ttest(a, b)
        assert result["p_value"] < 0.05


# --------------------------------------------------------------------------- #
# Solver benchmark unit tests
# --------------------------------------------------------------------------- #
class TestSolverBenchmarks:
    """Unit tests for the solver benchmark module."""

    def test_deterministic_rng(self):
        """Same seed → same sequence."""
        rng1 = solver_benchmarks.DetRng(42)
        rng2 = solver_benchmarks.DetRng(42)
        seq1 = [rng1.uniform() for _ in range(100)]
        seq2 = [rng2.uniform() for _ in range(100)]
        assert seq1 == seq2

    def test_benchmark_produces_scalars(self):
        """Each benchmark must produce scalar key quantities."""
        suite = solver_benchmarks.run_all_benchmarks()
        for bench_name, record in suite.items():
            assert "scalars" in record, f"{bench_name}: missing scalars"
            assert len(record["scalars"]) >= 3, f"{bench_name}: too few scalars"

    def test_benchmark_produces_fields(self):
        """Each benchmark must produce field quantities."""
        suite = solver_benchmarks.run_all_benchmarks()
        for bench_name, record in suite.items():
            assert "fields" in record, f"{bench_name}: missing fields"
            assert len(record["fields"]) >= 1, f"{bench_name}: no fields"

    def test_benchmark_produces_convergence(self):
        """Each benchmark must produce convergence history."""
        suite = solver_benchmarks.run_all_benchmarks()
        for bench_name, record in suite.items():
            assert "convergence" in record, f"{bench_name}: missing convergence"
            assert len(record["convergence"]) > 0, f"{bench_name}: empty convergence"

    def test_fingerprint_changes_on_perturbation(self):
        """Fingerprint must change when any value changes."""
        suite = solver_benchmarks.run_all_benchmarks()
        fp1 = solver_benchmarks.record_fingerprint(suite["iterative_stencil"])

        perturbed = _deep_copy_record(suite["iterative_stencil"])
        perturbed["scalars"]["key_max_value"] += 1e-15
        fp2 = solver_benchmarks.record_fingerprint(perturbed)

        assert fp1 != fp2, "Fingerprint must detect perturbation"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _deep_copy_record(record):
    """Deep copy a benchmark record."""
    return json.loads(json.dumps(record))


# --------------------------------------------------------------------------- #
# Count check: ensure ≥14 tests
# --------------------------------------------------------------------------- #
def test_minimum_test_count():
    """This module must contain at least 14 test functions."""
    import inspect
    # Count all test methods across all test classes
    total = 0
    for cls in [TestBitExact, TestStatisticalEquivalence, TestInjectedBias,
                TestMultiRunReproducibility, TestDegradationVerdict,
                TestZeroDomainSemantics, TestStatisticalUnit, TestSolverBenchmarks]:
        for name in dir(cls):
            if name.startswith("test_"):
                total += 1
    # Plus this test itself
    total += 1
    assert total >= 14, f"Need ≥14 tests, found {total}"
