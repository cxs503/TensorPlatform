"""
TensorPlatform Verifier — AlphaEvolve-style generator/verifier separation.

This package implements the verifier side of the AlphaEvolve pattern:
  - Question bank (held-out tests, invisible to workers)
  - Independent verifier (evaluates candidates against held-out questions)
  - Credit ledger (records verification outcomes)
  - Question generator (creates questions from specs/literature)

Key principle: Workers CANNOT access the question bank.
Only the verifier role can read questions and issue verdicts.
"""
from .question_bank import Question, QuestionBank, check_worker_access, DEFAULT_QBANK_PATH
from .verifier import Verifier, SubprocessVerifier, VerificationResult, VerificationReport
from .credit_ledger import CreditLedger, CreditEntry
from .generate_questions import (
    generate_ittc57_questions,
    generate_flat_plate_questions,
    generate_holtrop_questions,
    generate_all_questions,
    populate_question_bank,
)

__all__ = [
    "Question", "QuestionBank", "check_worker_access", "DEFAULT_QBANK_PATH",
    "Verifier", "SubprocessVerifier", "VerificationResult", "VerificationReport",
    "CreditLedger", "CreditEntry",
    "generate_ittc57_questions", "generate_flat_plate_questions",
    "generate_holtrop_questions", "generate_all_questions", "populate_question_bank",
]
