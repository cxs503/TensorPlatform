"""
Blind Benchmark Holdout — Pre-registration + Sealed Data + Adversarial Scoring

Transforms 'tuning against the answer' into machine-adjudicable FAIL.

Pipeline:
  1. SEAL: Dataset encrypted (authenticated encryption), manifest with sha256
  2. SUBMIT: Solver writes prediction artifact (hash + timestamp), cannot read truth
  3. ADJUDICATE: Decrypt dataset, score predictions, generate report
  4. ADVERSARIAL: Inject known-bad predictions to verify FAIL detection
  5. BACKFILL: Write results to guide.benchmark_cases with full provenance

Security model:
  - Authenticated encryption (encrypt-then-MAC) using stdlib only
  - Key held exclusively by adjudicator role
  - Prediction endpoint has write-only access (no decryption key)

References:
  - Pre-registration in science: Nosek et al. 2019, "The preregistration revolution"
  - Holdout evaluation: ML standard practice (test set isolation)
  - Adversarial testing: Goodfellow et al. 2014, "Explaining and Harnessing Adversarial Examples"
"""

from .crypto import SealedEnvelope, seal_data, unseal_data
from .sealer import DatasetSealer, SealManifest
from .submission import PredictionSubmitter, PredictionArtifact
from .adjudicator import BlindAdjudicator, ScoringReport
from .adversarial import AdversarialFixture, inject_adversarial
from .backfill import BenchmarkBackfill

__all__ = [
    "SealedEnvelope", "seal_data", "unseal_data",
    "DatasetSealer", "SealManifest",
    "PredictionSubmitter", "PredictionArtifact",
    "BlindAdjudicator", "ScoringReport",
    "AdversarialFixture", "inject_adversarial",
    "BenchmarkBackfill",
]
