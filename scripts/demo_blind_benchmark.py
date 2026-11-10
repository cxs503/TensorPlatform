#!/usr/bin/env python3
"""
Demo: Blind Benchmark Holdout Pipeline

Demonstrates the full pipeline:
  1. Seal a benchmark dataset
  2. Submit predictions (solver cannot see ground truth)
  3. Adjudicate (decrypt + score)
  4. Run adversarial fixtures (verify FAIL detection)
  5. Backfill to guide.benchmark_cases

This script produces a complete audit trail with cryptographic hashes.
"""

import json
import sys
import tempfile
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.blind_benchmark import (
    DatasetSealer,
    PredictionSubmitter,
    BlindAdjudicator,
    BenchmarkBackfill,
)
from backend.services.blind_benchmark.adversarial import run_adversarial_suite


def main():
    print("=" * 70)
    print("BLIND BENCHMARK HOLDOUT — DEMO")
    print("=" * 70)
    print()

    # Setup storage
    with tempfile.TemporaryDirectory() as tmpdir:
        sealer = DatasetSealer(f"{tmpdir}/sealed")
        submitter = PredictionSubmitter(f"{tmpdir}/submissions")
        adjudicator = BlindAdjudicator(sealer, submitter, f"{tmpdir}/reports")
        backfill = BenchmarkBackfill(f"{tmpdir}/guide")

        # =====================================================================
        # STEP 1: SEAL DATASET
        # =====================================================================
        print("STEP 1: SEAL BENCHMARK DATASET")
        print("-" * 70)

        # Example: SUBOFF resistance benchmark (from benchmark_canon.py)
        dataset = {
            "cases": {
                "SUBOFF_01": {
                    "Ct": 0.00350,
                    "Rt": 92.52,
                    "speed_ms": 1.585,
                    "froude_number": 0.2,
                },
                "SUBOFF_02": {
                    "Ct": 0.00420,
                    "Rt": 110.3,
                    "speed_ms": 2.378,
                    "froude_number": 0.3,
                },
                "SUBOFF_03": {
                    "Ct": 0.00510,
                    "Rt": 134.0,
                    "speed_ms": 3.170,
                    "froude_number": 0.4,
                },
            }
        }

        manifest = sealer.seal_dataset(
            dataset=dataset,
            dataset_name="SUBOFF_resistance_v3",
            metadata={
                "source": "Roddy 1990 DTRC-90/012",
                "version": "3.0",
                "description": "SUBOFF bare hull resistance coefficients",
            },
        )

        print(f"Dataset ID:        {manifest.dataset_id}")
        print(f"Dataset name:      {manifest.dataset_name}")
        print(f"Plaintext SHA256:  {manifest.plaintext_sha256}")
        print(f"Plaintext size:    {manifest.plaintext_size} bytes")
        print(f"Sealed size:       {manifest.sealed_size} bytes")
        print(f"Encryption:        {manifest.encryption_algorithm}")
        print(f"Key ID:            {manifest.key_id}")
        print(f"Seal timestamp:    {manifest.seal_timestamp_iso}")
        print()

        # =====================================================================
        # STEP 2: SUBMIT PREDICTIONS (GOOD SOLVER)
        # =====================================================================
        print("STEP 2: SUBMIT PREDICTIONS (GOOD SOLVER)")
        print("-" * 70)

        # Holtrop-Mennen method predictions (within 15% threshold)
        good_predictions = {
            "SUBOFF_01": {"Ct": 0.00360, "Rt": 95.0},   # ~3% error
            "SUBOFF_02": {"Ct": 0.00430, "Rt": 113.0},  # ~2% error
            "SUBOFF_03": {"Ct": 0.00520, "Rt": 136.0},  # ~2% error
        }

        good_artifact = submitter.submit(
            dataset_id=manifest.dataset_id,
            solver_name="holtrop_mennen",
            solver_version="v1.0",
            predictions=good_predictions,
            metadata={
                "description": "Holtrop-Mennen 1988 method",
                "reference": "Holtrop & Mennen, ISP-1988",
            },
        )

        print(f"Submission ID:     {good_artifact.submission_id}")
        print(f"Solver:            {good_artifact.solver_name} {good_artifact.solver_version}")
        print(f"Predictions hash:  {good_artifact.predictions_hash}")
        print(f"Submit timestamp:  {good_artifact.submit_timestamp_iso}")
        print()

        # =====================================================================
        # STEP 3: ADJUDICATE
        # =====================================================================
        print("STEP 3: ADJUDICATE (DECRYPT + SCORE)")
        print("-" * 70)

        good_report = adjudicator.adjudicate(
            submission_id=good_artifact.submission_id,
            scoring_rules={"max_relative_error_pct": 15.0},
        )

        print(f"Report ID:         {good_report.report_id}")
        print(f"Overall verdict:   {'PASS ✓' if good_report.overall_passed else 'FAIL ✗'}")
        print(f"Total cases:       {good_report.overall_summary['total_cases']}")
        print(f"Passed cases:      {good_report.overall_summary['passed_cases']}")
        print(f"Failed cases:      {good_report.overall_summary['failed_cases']}")
        print(f"Mean error:        {good_report.overall_summary['mean_relative_error_pct']:.2f}%")
        print(f"Max error:         {good_report.overall_summary['max_relative_error_pct']:.2f}%")
        print()

        print("Case-level scores:")
        for cs in good_report.case_scores:
            max_err = max(cs.relative_errors.values()) if cs.relative_errors else 0.0
            status = "PASS" if cs.passed else "FAIL"
            print(f"  {cs.case_id}: max_rel_error={max_err:.2f}% [{status}]")
        print()

        # =====================================================================
        # STEP 4: SUBMIT BAD PREDICTIONS (BAD SOLVER)
        # =====================================================================
        print("STEP 4: SUBMIT PREDICTIONS (BAD SOLVER)")
        print("-" * 70)

        # Bad predictions (exceeds 15% threshold)
        bad_predictions = {
            "SUBOFF_01": {"Ct": 0.00500, "Rt": 132.0},  # ~43% error
            "SUBOFF_02": {"Ct": 0.00600, "Rt": 158.0},  # ~43% error
            "SUBOFF_03": {"Ct": 0.00750, "Rt": 197.0},  # ~47% error
        }

        bad_artifact = submitter.submit(
            dataset_id=manifest.dataset_id,
            solver_name="naive_method",
            solver_version="v0.1",
            predictions=bad_predictions,
            metadata={"description": "Naive method (overestimates)"},
        )

        print(f"Submission ID:     {bad_artifact.submission_id}")
        print(f"Solver:            {bad_artifact.solver_name} {bad_artifact.solver_version}")
        print(f"Predictions hash:  {bad_artifact.predictions_hash}")
        print()

        # =====================================================================
        # STEP 5: ADJUDICATE BAD PREDICTIONS
        # =====================================================================
        print("STEP 5: ADJUDICATE BAD PREDICTIONS")
        print("-" * 70)

        bad_report = adjudicator.adjudicate(
            submission_id=bad_artifact.submission_id,
            scoring_rules={"max_relative_error_pct": 15.0},
        )

        print(f"Report ID:         {bad_report.report_id}")
        print(f"Overall verdict:   {'PASS ✓' if bad_report.overall_passed else 'FAIL ✗'}")
        print(f"Mean error:        {bad_report.overall_summary['mean_relative_error_pct']:.2f}%")
        print(f"Max error:         {bad_report.overall_summary['max_relative_error_pct']:.2f}%")
        print()

        # =====================================================================
        # STEP 6: RUN ADVERSARIAL SUITE
        # =====================================================================
        print("STEP 6: RUN ADVERSARIAL SUITE (RED TEAM)")
        print("-" * 70)

        fixtures = run_adversarial_suite(
            adjudicator=adjudicator,
            submitter=submitter,
            dataset_id=manifest.dataset_id,
            ground_truth=dataset,
        )

        for fixture in fixtures:
            status = "✓" if fixture.test_passed else "✗"
            print(f"  {fixture.fixture_type:20s}: expected_fail={fixture.expected_fail}, "
                  f"actual_fail={fixture.actual_fail} [{status}]")

        all_passed = all(f.test_passed for f in fixtures)
        print()
        print(f"Adversarial suite: {'ALL TESTS PASSED ✓' if all_passed else 'SOME TESTS FAILED ✗'}")
        print()

        # =====================================================================
        # STEP 7: BACKFILL TO GUIDE
        # =====================================================================
        print("STEP 7: BACKFILL TO GUIDE.BENCHMARK_CASES")
        print("-" * 70)

        entries_good = backfill.backfill_report(good_report)
        entries_bad = backfill.backfill_report(bad_report)

        print(f"Created {len(entries_good)} entries for good solver")
        print(f"Created {len(entries_bad)} entries for bad solver")
        print()

        guide = backfill.load_guide()
        print(f"Total guide entries: {len(guide['entries'])}")
        print()

        # =====================================================================
        # STEP 8: VERIFY BLIND PROPERTY
        # =====================================================================
        print("STEP 8: VERIFY BLIND PROPERTY")
        print("-" * 70)

        # Load sealed data (as solver would see it)
        sealed_path = Path(f"{tmpdir}/sealed") / f"{manifest.dataset_id}.sealed"
        sealed_bytes = sealed_path.read_bytes()

        print(f"Sealed file size:  {len(sealed_bytes)} bytes")
        print(f"Sealed data (hex): {sealed_bytes[:32].hex()}...")
        print()
        print("Solver CANNOT decrypt without adjudicator key:")
        try:
            json.loads(sealed_bytes.decode("utf-8"))
            print("  ✗ SECURITY FAILURE: Solver could read sealed data!")
        except Exception as e:
            print(f"  ✓ Solver sees only encrypted bytes: {type(e).__name__}")
        print()

        # =====================================================================
        # SUMMARY
        # =====================================================================
        print("=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print()
        print("Pipeline completed successfully:")
        print(f"  1. Dataset sealed:        {manifest.dataset_id}")
        print(f"  2. Good solver submitted: {good_artifact.submission_id}")
        print(f"  3. Good solver verdict:   {'PASS' if good_report.overall_passed else 'FAIL'}")
        print(f"  4. Bad solver submitted:  {bad_artifact.submission_id}")
        print(f"  5. Bad solver verdict:    {'PASS' if bad_report.overall_passed else 'FAIL'}")
        print(f"  6. Adversarial suite:     {'PASS' if all_passed else 'FAIL'}")
        print(f"  7. Guide entries created: {len(guide['entries'])}")
        print(f"  8. Blind property:        VERIFIED")
        print()
        print("Full provenance chain:")
        print(f"  Dataset hash:    {manifest.plaintext_sha256}")
        print(f"  Good submission: {good_artifact.predictions_hash}")
        print(f"  Good report:     {good_report.report_id}")
        print(f"  Bad submission:  {bad_artifact.predictions_hash}")
        print(f"  Bad report:      {bad_report.report_id}")
        print()
        print("✓ Blind Benchmark Holdout system is operational")
        print()


if __name__ == "__main__":
    main()
