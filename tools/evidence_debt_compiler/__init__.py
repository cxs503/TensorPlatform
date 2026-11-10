"""Evidence debt compiler: WITHHELD→REAL automatic upgrade pipeline (P519-N01).

Public surface::

    from tools.evidence_debt_compiler import (
        EvidenceDebtCompiler,
        EvidenceDebtExtractor,
        BaselineClassifier,
        VerificationTaskGenerator,
        KPICalculator,
    )

    compiler = EvidenceDebtCompiler(db_path=":memory:")
    result = compiler.run(reports=[...])
    # result["kpi"]["closure_rate"] gives the upgrade ratio

Zero-domain-semantics: all domain vocabulary arrives via injected configuration.
Idempotent: repeated runs skip already-processed debts.
KPI invariant: n_upgraded + n_failed + n_remaining = n_WITHHELD_initial.
"""
from __future__ import annotations

from .classifier import BaselineClassifier
from .extractor import EvidenceDebtExtractor
from .kpi import KPICalculator
from .model import (
    BaselineType,
    ClassifiedDebt,
    DebtStatus,
    ExtractedDebt,
    LevelUpdate,
    PipelineKPI,
    VerificationOutcome,
    VerificationResult,
    VerificationTask,
)
from .orchestrator import EvidenceDebtCompiler
from .task_generator import VerificationTaskGenerator

__all__ = [
    "BaselineClassifier",
    "BaselineType",
    "ClassifiedDebt",
    "DebtStatus",
    "EvidenceDebtCompiler",
    "EvidenceDebtExtractor",
    "ExtractedDebt",
    "KPICalculator",
    "LevelUpdate",
    "PipelineKPI",
    "VerificationOutcome",
    "VerificationResult",
    "VerificationTask",
    "VerificationTaskGenerator",
]
