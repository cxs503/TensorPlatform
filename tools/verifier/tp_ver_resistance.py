#!/usr/bin/env python3
"""
tp-ver-resistance — Independent verification runner for resistance calculations.

This script is the VERIFIER side of the AlphaEvolve separation:
  1. Loads held-out questions from the question bank (workers cannot see these)
  2. Runs the candidate implementation against each question
  3. Computes deviations from reference values
  4. Issues pass/fail verdicts
  5. Records results in the credit ledger

Usage:
  python tp_ver_resistance.py --candidate <path_to_candidate.py> \
                              --candidate-id <id> \
                              [--mission-id <id>] \
                              [--self-test-result <pass|fail>]

The candidate must implement the subprocess protocol:
  stdin:  {"specification": {...}}
  stdout: {"result": {...}}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.question_bank import QuestionBank
from verifier.verifier import SubprocessVerifier
from verifier.credit_ledger import CreditLedger
from verifier.generate_questions import populate_question_bank


def main():
    parser = argparse.ArgumentParser(
        description="Independent verifier for resistance calculations"
    )
    parser.add_argument(
        "--candidate", required=True,
        help="Path to candidate script (subprocess protocol)"
    )
    parser.add_argument(
        "--candidate-id", required=True,
        help="Unique identifier for this candidate"
    )
    parser.add_argument(
        "--candidate-version", default="1.0.0",
        help="Version string for the candidate"
    )
    parser.add_argument(
        "--mission-id", type=int, default=None,
        help="Mission ID for traceability (optional)"
    )
    parser.add_argument(
        "--self-test-result", default=None, choices=["pass", "fail", "error"],
        help="Self-test result (informational only, NOT used for acceptance)"
    )
    parser.add_argument(
        "--domain", default="resistance",
        help="Question domain to verify against"
    )
    parser.add_argument(
        "--n-questions", type=int, default=None,
        help="Number of questions to use (None = all committed)"
    )
    parser.add_argument(
        "--qbank-path", default=None,
        help="Path to question bank (default: tools/verifier/question_bank/questions.db)"
    )
    parser.add_argument(
        "--populate", action="store_true",
        help="Populate question bank before verification"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output path for verification report (JSON)"
    )
    args = parser.parse_args()

    # Resolve question bank path
    if args.qbank_path is None:
        args.qbank_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "question_bank", "questions.db"
        )

    # Populate question bank if requested
    if args.populate:
        print(f"[verifier] Populating question bank at {args.qbank_path}")
        counts = populate_question_bank(args.qbank_path)
        print(f"[verifier] Question bank populated: {json.dumps(counts)}")

    # Initialize verifier
    verifier = SubprocessVerifier(
        qbank_path=args.qbank_path,
        verifier_id="tp-ver-resistance",
        timeout_seconds=30.0,
    )

    # Check question bank has questions
    qbank = QuestionBank(args.qbank_path)
    n_available = qbank.question_count(args.domain)
    n_committed = len(qbank.list_questions(domain=args.domain, committed_only=True))
    print(f"[verifier] Question bank: {n_available} total, {n_committed} committed for domain '{args.domain}'")

    if n_committed == 0:
        print("[verifier] ERROR: No committed questions available. Run with --populate first.")
        sys.exit(1)

    # Run verification
    print(f"[verifier] Starting verification of candidate '{args.candidate_id}'")
    print(f"[verifier] Candidate script: {args.candidate}")
    print(f"[verifier] Domain: {args.domain}")
    print(f"[verifier] Questions: {args.n_questions or 'all committed'}")
    print()

    report = verifier.verify_subprocess(
        candidate_id=args.candidate_id,
        script_path=os.path.abspath(args.candidate),
        domain=args.domain,
        n_questions=args.n_questions,
        candidate_version=args.candidate_version,
    )

    # Print results
    print("=" * 70)
    print(f"VERIFICATION REPORT: {args.candidate_id}")
    print("=" * 70)
    print(f"  Verifier:       {report.verifier_id}")
    print(f"  Domain:         {report.domain}")
    print(f"  Overall Verdict: {report.overall_verdict.upper()}")
    print(f"  Questions:      {report.pass_count}/{report.total_questions} passed")
    print(f"  Duration:       {report.finished_at - report.started_at:.2f}s")
    print()

    for r in report.results:
        status_icon = {"pass": "✓", "fail": "✗", "error": "⚠"}.get(r.verdict, "?")
        print(f"  {status_icon} [{r.verdict.upper():4s}] {r.question_title}")
        if r.verdict == "pass":
            for key, dev in r.deviations.items():
                print(f"       {key}: deviation={dev:.4e} (within tolerance)")
        elif r.verdict == "fail":
            for key, dev in r.deviations.items():
                passed = r.tolerance_check.get(key, False)
                mark = "✓" if passed else "✗"
                print(f"       {mark} {key}: deviation={dev:.4e} expected={r.expected_result.get(key)}")
        else:
            print(f"       Error: {r.error_message[:100]}")
    print()

    # Record in credit ledger
    ledger = CreditLedger()
    entry = ledger.record_from_report(
        report=report.to_dict(),
        mission_id=args.mission_id,
        self_test_result=args.self_test_result,
    )
    print(f"[credit_ledger] Entry recorded: {entry.id}")
    print(f"[credit_ledger] Verdict: {entry.verdict}")
    print(f"[credit_ledger] Pass rate: {entry.pass_rate:.1%}")
    if args.self_test_result:
        print(f"[credit_ledger] Self-test (informational): {args.self_test_result}")
        print(f"[credit_ledger] NOTE: Self-test does NOT affect acceptance verdict")
    print()

    # Save report if requested
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(report.to_dict(), f, indent=2, default=str)
        print(f"[verifier] Report saved to {args.output}")

    # Exit code reflects verdict
    if report.overall_verdict == "pass":
        print("[verifier] CANDIDATE ACCEPTED ✓")
        sys.exit(0)
    else:
        print(f"[verifier] CANDIDATE REJECTED ({report.overall_verdict})")
        sys.exit(1)


if __name__ == "__main__":
    main()
