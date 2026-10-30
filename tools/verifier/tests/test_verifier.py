#!/usr/bin/env python3
"""
Test suite for the AlphaEvolve-style verifier/generator separation.

Tests:
  1. Question bank permission isolation
  2. Worker cannot access question bank
  3. Verifier can load and judge candidates
  4. Credit ledger records verdicts
  5. End-to-end pilot flow
"""
from __future__ import annotations

import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import unittest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.question_bank import Question, QuestionBank, check_worker_access
from verifier.verifier import Verifier, SubprocessVerifier
from verifier.credit_ledger import CreditLedger, CreditEntry
from verifier.generate_questions import (
    generate_ittc57_questions,
    generate_flat_plate_questions,
    populate_question_bank,
)


class TestQuestionBank(unittest.TestCase):
    """Test question bank creation and access."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.qbank_path = os.path.join(self.tmpdir, "test_questions.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_question_bank(self):
        """Question bank can be created with restricted permissions."""
        qbank = QuestionBank(self.qbank_path)
        self.assertTrue(os.path.exists(self.qbank_path))
        st = os.stat(self.qbank_path)
        mode = stat.S_IMODE(st.st_mode)
        self.assertEqual(mode & 0o077, 0, "Question bank should be mode 0o600")

    def test_add_and_retrieve_question(self):
        """Questions can be added and retrieved."""
        qbank = QuestionBank(self.qbank_path)
        q = Question(
            id="",
            domain="resistance",
            difficulty="basic",
            title="Test question",
            specification={"Re": 1e8},
            expected_result={"Cf": 0.00217},
            tolerances={"Cf": 0.01},
            provenance={"source": "test"},
        )
        qid = qbank.add_question(q)
        self.assertIsNotNone(qid)

        retrieved = qbank.get_question(qid)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.domain, "resistance")
        self.assertEqual(retrieved.specification["Re"], 1e8)

    def test_commit_freezes_question(self):
        """Committing a question makes it immutable."""
        qbank = QuestionBank(self.qbank_path)
        q = Question(
            id="",
            domain="resistance",
            difficulty="basic",
            title="Test",
            specification={"Re": 1e8},
            expected_result={"Cf": 0.00217},
            tolerances={"Cf": 0.01},
            provenance={"source": "test"},
        )
        qid = qbank.add_question(q)
        qbank.commit_question(qid)

        retrieved = qbank.get_question(qid)
        self.assertTrue(retrieved.committed)
        self.assertTrue(len(retrieved.commit_hash) > 0)

    def test_get_exam_only_returns_committed(self):
        """get_exam only returns committed questions."""
        qbank = QuestionBank(self.qbank_path)

        # Add committed question
        q1 = Question(
            id="", domain="resistance", difficulty="basic", title="Committed",
            specification={"Re": 1e8}, expected_result={"Cf": 0.00217},
            tolerances={"Cf": 0.01}, provenance={"source": "test"},
        )
        qid1 = qbank.add_question(q1)
        qbank.commit_question(qid1)

        # Add uncommitted question
        q2 = Question(
            id="", domain="resistance", difficulty="basic", title="Uncommitted",
            specification={"Re": 1e7}, expected_result={"Cf": 0.003},
            tolerances={"Cf": 0.01}, provenance={"source": "test"},
        )
        qbank.add_question(q2)

        exam = qbank.get_exam("resistance")
        self.assertEqual(len(exam), 1)
        self.assertEqual(exam[0].title, "Committed")


class TestPermissionIsolation(unittest.TestCase):
    """Test that workers cannot access the question bank."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.qbank_path = os.path.join(self.tmpdir, "test_questions.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_question_bank_permissions(self):
        """Question bank file has restricted permissions."""
        qbank = QuestionBank(self.qbank_path)
        result = check_worker_access(self.qbank_path)
        self.assertTrue(result["secure"], "Question bank should have secure permissions")
        self.assertEqual(result["file_mode"], "0o600")

    def test_worker_cannot_read_insecure_bank(self):
        """An insecure question bank is detected."""
        # Create a file with insecure permissions
        with open(self.qbank_path, "w") as f:
            f.write("test")
        os.chmod(self.qbank_path, 0o644)  # Group-readable

        with self.assertRaises(PermissionError):
            QuestionBank(self.qbank_path)


class TestVerifier(unittest.TestCase):
    """Test the verifier's ability to judge candidates."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.qbank_path = os.path.join(self.tmpdir, "test_questions.db")
        # Populate with ITTC-1957 questions
        qbank = QuestionBank(self.qbank_path)
        questions = generate_ittc57_questions()
        for q in questions:
            qid = qbank.add_question(q)
            qbank.commit_question(qid)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_verifier_passes_correct_implementation(self):
        """Verifier passes a correct ITTC-1957 implementation."""
        def correct_ittc57(spec):
            Re = spec["Re"]
            log_Re = math.log10(Re)
            Cf = 0.075 / (log_Re - 2) ** 2
            return {"Cf": Cf}

        verifier = Verifier(self.qbank_path, "tp-ver-test")
        report = verifier.verify_candidate(
            candidate_id="test-correct",
            candidate_fn=correct_ittc57,
            domain="resistance",
        )

        self.assertEqual(report.overall_verdict, "pass")
        self.assertEqual(report.fail_count, 0)
        self.assertGreater(report.pass_count, 0)

    def test_verifier_fails_incorrect_implementation(self):
        """Verifier fails an incorrect implementation."""
        def wrong_ittc57(spec):
            # Deliberately wrong: returns constant
            return {"Cf": 0.005}

        verifier = Verifier(self.qbank_path, "tp-ver-test")
        report = verifier.verify_candidate(
            candidate_id="test-wrong",
            candidate_fn=wrong_ittc57,
            domain="resistance",
        )

        self.assertEqual(report.overall_verdict, "fail")
        self.assertGreater(report.fail_count, 0)


class TestSubprocessVerifier(unittest.TestCase):
    """Test subprocess-based verification."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.qbank_path = os.path.join(self.tmpdir, "test_questions.db")
        qbank = QuestionBank(self.qbank_path)
        questions = generate_ittc57_questions()
        for q in questions:
            qid = qbank.add_question(q)
            qbank.commit_question(qid)

        # Create a candidate script
        self.candidate_path = os.path.join(self.tmpdir, "candidate.py")
        with open(self.candidate_path, "w") as f:
            f.write('''#!/usr/bin/env python3
import json, math, sys
data = json.loads(sys.stdin.read())
spec = data.get("specification", data)
Re = spec["Re"]
log_Re = math.log10(Re)
Cf = 0.075 / (log_Re - 2) ** 2
print(json.dumps({"result": {"Cf": Cf}}))
''')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_subprocess_verification(self):
        """Subprocess verifier correctly evaluates a candidate script."""
        verifier = SubprocessVerifier(self.qbank_path, "tp-ver-test")
        report = verifier.verify_subprocess(
            candidate_id="test-subprocess",
            script_path=self.candidate_path,
            domain="resistance",
        )

        self.assertEqual(report.overall_verdict, "pass")
        self.assertEqual(report.fail_count, 0)


class TestCreditLedger(unittest.TestCase):
    """Test credit ledger recording."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.tmpdir, "test_ledger.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_and_query(self):
        """Credit entries can be recorded and queried."""
        ledger = CreditLedger(self.ledger_path)
        entry = CreditEntry(
            id="test-001",
            candidate_id="candidate-a",
            candidate_version="1.0",
            mission_id=537,
            verifier_id="tp-ver-test",
            domain="resistance",
            verdict="pass",
            questions_passed=5,
            questions_failed=0,
            questions_total=5,
            pass_rate=1.0,
            evidence_hash="abc123",
        )
        ledger.record(entry)

        results = ledger.query(candidate_id="candidate-a")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].verdict, "pass")

    def test_record_from_report(self):
        """Credit entry can be created from a verification report."""
        ledger = CreditLedger(self.ledger_path)
        report = {
            "candidate_id": "candidate-b",
            "candidate_version": "2.0",
            "verifier_id": "tp-ver-test",
            "domain": "resistance",
            "overall_verdict": "pass",
            "pass_count": 3,
            "fail_count": 0,
            "total_questions": 3,
        }
        entry = ledger.record_from_report(report, mission_id=537)
        self.assertEqual(entry.verdict, "pass")
        self.assertEqual(entry.mission_id, 537)


class TestEndToEnd(unittest.TestCase):
    """End-to-end pilot test."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.qbank_path = os.path.join(self.tmpdir, "questions.db")
        self.ledger_path = os.path.join(self.tmpdir, "ledger.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_pilot_flow(self):
        """
        Full pilot flow:
        1. Verifier generates questions (worker cannot see)
        2. Candidate implements the calculation
        3. Verifier independently tests candidate
        4. Credit ledger records the verdict
        """
        # Step 1: Populate question bank (verifier role)
        counts = populate_question_bank(self.qbank_path)
        self.assertGreater(sum(counts.values()), 0)

        # Step 2: Verify that worker cannot see questions
        # (In production, this is enforced by file permissions + separate user)
        qbank = QuestionBank(self.qbank_path)
        exam = qbank.get_exam("resistance")
        self.assertGreater(len(exam), 0)

        # Step 3: Run verifier against correct candidate
        def correct_candidate(spec):
            formula = spec.get("formula", spec.get("method", ""))
            if formula == "ittc_1957":
                Re = spec["Re"]
                return {"Cf": 0.075 / (math.log10(Re) - 2) ** 2}
            elif formula == "blasius_laminar":
                Re_L = spec["Re_L"]
                return {"Cf": 1.328 / math.sqrt(Re_L)}
            elif formula == "prandtl_schlichting":
                Re_L = spec["Re_L"]
                return {"Cf": 0.455 / (math.log10(Re_L) ** 2.58)}
            elif formula == "holtrop_mennen_form_factor":
                Cb = spec["Cb"]
                L, B, T = spec["L"], spec["B"], spec["T"]
                return {"1+k": 0.93 + 0.487118 * (Cb ** (-0.9249)) * (L/B)**0.15 * (B/T)**0.2 * 0.1}
            return {}

        verifier = Verifier(self.qbank_path, "tp-ver-pilot")
        report = verifier.verify_candidate(
            candidate_id="pilot-candidate",
            candidate_fn=correct_candidate,
            domain="resistance",
        )

        # Step 4: Record in credit ledger
        ledger = CreditLedger(self.ledger_path)
        entry = ledger.record_from_report(
            report=report.to_dict(),
            mission_id=537,
            self_test_result="pass",  # Self-test passed, but doesn't count
        )

        # Verify results
        self.assertEqual(report.overall_verdict, "pass")
        self.assertEqual(entry.verdict, "pass")
        self.assertEqual(entry.mission_id, 537)
        self.assertIsNotNone(entry.evidence_hash)

        # Verify self-test note
        self.assertIn("informational", entry.self_test_note.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
