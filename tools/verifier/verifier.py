"""
Independent Verifier — AlphaEvolve-style evaluation engine.

This module implements the VERIFIER role:
  - Loads held-out questions from the question bank (which workers cannot see)
  - Runs the candidate implementation against the questions
  - Computes deviations from reference values
  - Issues pass/fail verdicts based on tolerances
  - Records verdicts in the question bank's audit trail

Key principle: The verifier NEVER trusts self-reported results from the worker.
It independently executes the candidate code with held-out inputs and compares
against reference values from the question bank.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .question_bank import Question, QuestionBank, DEFAULT_QBANK_PATH


@dataclass
class VerificationResult:
    """Result of verifying a single question."""
    question_id: str
    question_title: str
    verdict: str  # "pass", "fail", "error"
    actual_result: dict[str, Any] = field(default_factory=dict)
    expected_result: dict[str, Any] = field(default_factory=dict)
    deviations: dict[str, float] = field(default_factory=dict)
    tolerance_check: dict[str, bool] = field(default_factory=dict)
    error_message: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class VerificationReport:
    """Full verification report for a candidate."""
    candidate_id: str
    candidate_version: str
    verifier_id: str
    domain: str
    results: list[VerificationResult] = field(default_factory=list)
    overall_verdict: str = "pending"  # "pass", "fail", "error", "pending"
    pass_count: int = 0
    fail_count: int = 0
    error_count: int = 0
    total_questions: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_version": self.candidate_version,
            "verifier_id": self.verifier_id,
            "domain": self.domain,
            "overall_verdict": self.overall_verdict,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "error_count": self.error_count,
            "total_questions": self.total_questions,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "results": [
                {
                    "question_id": r.question_id,
                    "question_title": r.question_title,
                    "verdict": r.verdict,
                    "actual_result": r.actual_result,
                    "expected_result": r.expected_result,
                    "deviations": r.deviations,
                    "tolerance_check": r.tolerance_check,
                    "error_message": r.error_message,
                    "elapsed_seconds": r.elapsed_seconds,
                }
                for r in self.results
            ],
        }


class Verifier:
    """
    Independent verifier that evaluates candidates against held-out questions.

    The verifier:
    1. Loads questions from the question bank (workers cannot access this)
    2. For each question, runs the candidate with the question's specification
    3. Compares actual results against expected results
    4. Checks if deviations are within tolerances
    5. Records verdicts in the question bank's audit trail
    """

    def __init__(self, qbank_path: str = DEFAULT_QBANK_PATH,
                 verifier_id: str = "tp-ver-default"):
        self.qbank = QuestionBank(qbank_path)
        self.verifier_id = verifier_id

    def verify_candidate(
        self,
        candidate_id: str,
        candidate_fn: Callable[[dict], dict],
        domain: str,
        n_questions: int = None,
        difficulty: str = None,
        candidate_version: str = None,
    ) -> VerificationReport:
        """
        Verify a candidate function against held-out questions.

        Args:
            candidate_id: Unique identifier for the candidate
            candidate_fn: Function that takes specification dict and returns result dict
            domain: Question domain to test against
            n_questions: Number of questions to use (None = all)
            difficulty: Filter by difficulty level
            candidate_version: Version string for the candidate

        Returns:
            VerificationReport with per-question results
        """
        report = VerificationReport(
            candidate_id=candidate_id,
            candidate_version=candidate_version or "unknown",
            verifier_id=self.verifier_id,
            domain=domain,
            started_at=time.time(),
        )

        # Load held-out questions
        questions = self.qbank.get_exam(domain, n=n_questions, difficulty=difficulty)
        report.total_questions = len(questions)

        if not questions:
            report.overall_verdict = "error"
            report.error_count = 1
            report.results.append(VerificationResult(
                question_id="N/A",
                question_title="No questions available",
                verdict="error",
                error_message=f"No committed questions found for domain '{domain}'",
            ))
            report.finished_at = time.time()
            return report

        for q in questions:
            result = self._verify_single(candidate_id, candidate_fn, q)
            report.results.append(result)

            # Record verdict in question bank
            self.qbank.record_verdict(
                question_id=q.id,
                candidate_id=candidate_id,
                verdict=result.verdict,
                actual_result=result.actual_result,
                deviations=result.deviations,
                details={
                    "tolerance_check": result.tolerance_check,
                    "error_message": result.error_message,
                    "elapsed_seconds": result.elapsed_seconds,
                },
                candidate_version=candidate_version,
                verifier_id=self.verifier_id,
            )

            if result.verdict == "pass":
                report.pass_count += 1
            elif result.verdict == "fail":
                report.fail_count += 1
            else:
                report.error_count += 1

        # Overall verdict: pass only if ALL questions pass
        if report.error_count > 0 and report.pass_count == 0 and report.fail_count == 0:
            report.overall_verdict = "error"
        elif report.fail_count > 0:
            report.overall_verdict = "fail"
        elif report.error_count > 0:
            report.overall_verdict = "error"
        else:
            report.overall_verdict = "pass"

        report.finished_at = time.time()
        return report

    def _verify_single(
        self,
        candidate_id: str,
        candidate_fn: Callable[[dict], dict],
        question: Question,
    ) -> VerificationResult:
        """Verify a single question."""
        t0 = time.time()
        try:
            # Run the candidate with the question's specification
            actual = candidate_fn(question.specification)
            elapsed = time.time() - t0

            if not isinstance(actual, dict):
                return VerificationResult(
                    question_id=question.id,
                    question_title=question.title,
                    verdict="error",
                    error_message=f"Candidate returned {type(actual).__name__}, expected dict",
                    elapsed_seconds=elapsed,
                )

            # Compute deviations
            deviations = {}
            tolerance_check = {}
            for key, expected_val in question.expected_result.items():
                if key in actual:
                    actual_val = actual[key]
                    if isinstance(expected_val, (int, float)) and isinstance(actual_val, (int, float)):
                        if expected_val != 0:
                            dev = abs(actual_val - expected_val) / abs(expected_val)
                        else:
                            dev = abs(actual_val) if actual_val != 0 else 0.0
                        deviations[key] = dev
                        tol = question.tolerances.get(key, 0.05)  # Default 5%
                        tolerance_check[key] = dev <= tol
                    else:
                        # Non-numeric comparison
                        deviations[key] = 0.0 if actual_val == expected_val else 1.0
                        tolerance_check[key] = actual_val == expected_val
                else:
                    deviations[key] = float("inf")
                    tolerance_check[key] = False

            # Determine verdict
            all_pass = all(tolerance_check.values()) if tolerance_check else False
            verdict = "pass" if all_pass else "fail"

            return VerificationResult(
                question_id=question.id,
                question_title=question.title,
                verdict=verdict,
                actual_result=actual,
                expected_result=question.expected_result,
                deviations=deviations,
                tolerance_check=tolerance_check,
                elapsed_seconds=elapsed,
            )

        except Exception as e:
            elapsed = time.time() - t0
            return VerificationResult(
                question_id=question.id,
                question_title=question.title,
                verdict="error",
                error_message=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
                elapsed_seconds=elapsed,
            )


class SubprocessVerifier(Verifier):
    """
    Verifier that runs candidates in a subprocess for isolation.

    This is the recommended verifier for production use:
    - The candidate runs in a separate process
    - Communication via stdin/stdout (JSON protocol)
    - Timeout enforcement
    - No shared memory between verifier and candidate
    """

    def __init__(self, qbank_path: str = DEFAULT_QBANK_PATH,
                 verifier_id: str = "tp-ver-subprocess",
                 timeout_seconds: float = 60.0):
        super().__init__(qbank_path, verifier_id)
        self.timeout = timeout_seconds

    def verify_subprocess(
        self,
        candidate_id: str,
        script_path: str,
        domain: str,
        n_questions: int = None,
        difficulty: str = None,
        candidate_version: str = None,
    ) -> VerificationReport:
        """
        Verify a candidate script against held-out questions.

        The script must:
        1. Read a JSON specification from stdin
        2. Compute the result
        3. Print a JSON result to stdout

        Protocol:
          stdin:  {"specification": {...}}
          stdout: {"result": {...}}
        """
        report = VerificationReport(
            candidate_id=candidate_id,
            candidate_version=candidate_version or "unknown",
            verifier_id=self.verifier_id,
            domain=domain,
            started_at=time.time(),
        )

        questions = self.qbank.get_exam(domain, n=n_questions, difficulty=difficulty)
        report.total_questions = len(questions)

        if not questions:
            report.overall_verdict = "error"
            report.error_count = 1
            report.finished_at = time.time()
            return report

        for q in questions:
            result = self._verify_subprocess_single(candidate_id, script_path, q)
            report.results.append(result)

            self.qbank.record_verdict(
                question_id=q.id,
                candidate_id=candidate_id,
                verdict=result.verdict,
                actual_result=result.actual_result,
                deviations=result.deviations,
                details={
                    "tolerance_check": result.tolerance_check,
                    "error_message": result.error_message,
                    "elapsed_seconds": result.elapsed_seconds,
                },
                candidate_version=candidate_version,
                verifier_id=self.verifier_id,
            )

            if result.verdict == "pass":
                report.pass_count += 1
            elif result.verdict == "fail":
                report.fail_count += 1
            else:
                report.error_count += 1

        if report.error_count > 0 and report.pass_count == 0 and report.fail_count == 0:
            report.overall_verdict = "error"
        elif report.fail_count > 0:
            report.overall_verdict = "fail"
        elif report.error_count > 0:
            report.overall_verdict = "error"
        else:
            report.overall_verdict = "pass"

        report.finished_at = time.time()
        return report

    def _verify_subprocess_single(
        self,
        candidate_id: str,
        script_path: str,
        question: Question,
    ) -> VerificationResult:
        """Run a single question through the subprocess candidate."""
        t0 = time.time()
        try:
            input_data = json.dumps({"specification": question.specification})
            proc = subprocess.run(
                [sys.executable, script_path],
                input=input_data,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env={**os.environ, "TP_VERIFIER_MODE": "1"},
            )

            elapsed = time.time() - t0

            if proc.returncode != 0:
                return VerificationResult(
                    question_id=question.id,
                    question_title=question.title,
                    verdict="error",
                    error_message=f"Exit code {proc.returncode}: {proc.stderr[:500]}",
                    elapsed_seconds=elapsed,
                )

            try:
                output = json.loads(proc.stdout.strip())
                actual = output.get("result", output)
            except json.JSONDecodeError:
                return VerificationResult(
                    question_id=question.id,
                    question_title=question.title,
                    verdict="error",
                    error_message=f"Invalid JSON output: {proc.stdout[:200]}",
                    elapsed_seconds=elapsed,
                )

            # Compute deviations (same logic as parent)
            deviations = {}
            tolerance_check = {}
            for key, expected_val in question.expected_result.items():
                if key in actual:
                    actual_val = actual[key]
                    if isinstance(expected_val, (int, float)) and isinstance(actual_val, (int, float)):
                        if expected_val != 0:
                            dev = abs(actual_val - expected_val) / abs(expected_val)
                        else:
                            dev = abs(actual_val) if actual_val != 0 else 0.0
                        deviations[key] = dev
                        tol = question.tolerances.get(key, 0.05)
                        tolerance_check[key] = dev <= tol
                    else:
                        deviations[key] = 0.0 if actual_val == expected_val else 1.0
                        tolerance_check[key] = actual_val == expected_val
                else:
                    deviations[key] = float("inf")
                    tolerance_check[key] = False

            all_pass = all(tolerance_check.values()) if tolerance_check else False
            verdict = "pass" if all_pass else "fail"

            return VerificationResult(
                question_id=question.id,
                question_title=question.title,
                verdict=verdict,
                actual_result=actual,
                expected_result=question.expected_result,
                deviations=deviations,
                tolerance_check=tolerance_check,
                elapsed_seconds=elapsed,
            )

        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            return VerificationResult(
                question_id=question.id,
                question_title=question.title,
                verdict="error",
                error_message=f"Timeout after {self.timeout}s",
                elapsed_seconds=elapsed,
            )
        except Exception as e:
            elapsed = time.time() - t0
            return VerificationResult(
                question_id=question.id,
                question_title=question.title,
                verdict="error",
                error_message=f"{type(e).__name__}: {e}",
                elapsed_seconds=elapsed,
            )
