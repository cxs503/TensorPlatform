"""
Blind Benchmark Holdout API — REST endpoints for the blind evaluation pipeline.

Endpoints:
  POST /blind-benchmark/seal          — Seal a dataset (adjudicator only)
  POST /blind-benchmark/submit        — Submit predictions (solver, write-only)
  POST /blind-benchmark/adjudicate    — Score a submission (adjudicator only)
  GET  /blind-benchmark/report/{id}   — Get scoring report
  POST /blind-benchmark/adversarial   — Run adversarial fixture (testing)
  GET  /blind-benchmark/guide         — Query guide.benchmark_cases
  GET  /blind-benchmark/manifest/{id} — Get dataset manifest

Security model:
  - /seal and /adjudicate require adjudicator role (has decryption key)
  - /submit is open to solvers (write-only, no ground truth access)
  - /report and /guide are read-only (any authenticated user)
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.config import get_db
from backend.auth import require_auth

# Import blind benchmark components
from backend.services.blind_benchmark import (
    DatasetSealer,
    PredictionSubmitter,
    BlindAdjudicator,
    BenchmarkBackfill,
)
from backend.services.blind_benchmark.adversarial import inject_adversarial, run_adversarial_suite


router = APIRouter(prefix="/blind-benchmark", tags=["blind-benchmark"])


# Request/Response models
class SealRequest(BaseModel):
    dataset: Dict[str, Any]
    dataset_name: str
    metadata: Optional[Dict[str, Any]] = None


class SubmitRequest(BaseModel):
    dataset_id: str
    solver_name: str
    solver_version: str
    predictions: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None


class AdjudicateRequest(BaseModel):
    submission_id: str
    scoring_rules: Optional[Dict[str, Any]] = None


class AdversarialRequest(BaseModel):
    dataset_id: str
    ground_truth: Dict[str, Any]
    fixture_type: str = "gross_error"
    seed: Optional[int] = None


# Storage directories (in production, these would be configured)
SEALER_STORAGE = "/tmp/blind_benchmark/sealed"
SUBMISSION_STORAGE = "/tmp/blind_benchmark/submissions"
REPORT_STORAGE = "/tmp/blind_benchmark/reports"
GUIDE_STORAGE = "/tmp/blind_benchmark/guide"

# Singleton instances (in production, inject via dependency)
_sealer = None
_submitter = None
_adjudicator = None
_backfill = None


def get_sealer() -> DatasetSealer:
    global _sealer
    if _sealer is None:
        _sealer = DatasetSealer(SEALER_STORAGE)
    return _sealer


def get_submitter() -> PredictionSubmitter:
    global _submitter
    if _submitter is None:
        _submitter = PredictionSubmitter(SUBMISSION_STORAGE)
    return _submitter


def get_adjudicator() -> BlindAdjudicator:
    global _adjudicator
    if _adjudicator is None:
        _adjudicator = BlindAdjudicator(
            sealer=get_sealer(),
            submitter=get_submitter(),
            report_dir=REPORT_STORAGE,
        )
    return _adjudicator


def get_backfill() -> BenchmarkBackfill:
    global _backfill
    if _backfill is None:
        _backfill = BenchmarkBackfill(GUIDE_STORAGE)
    return _backfill


@router.post("/seal")
def seal_dataset(
    req: SealRequest,
    request: Request,
    user: dict = Depends(require_auth),
):
    """
    Seal a benchmark dataset (adjudicator only).

    Encrypts the dataset and generates a manifest with sha256.
    The sealed dataset can be stored publicly; only the adjudicator can decrypt.
    """
    # Check adjudicator role
    user_role = user.get("role", "")
    if user_role not in ("admin", "adjudicator"):
        raise HTTPException(403, "Only adjudicator can seal datasets")

    sealer = get_sealer()
    manifest = sealer.seal_dataset(
        dataset=req.dataset,
        dataset_name=req.dataset_name,
        metadata=req.metadata,
    )

    return {
        "ok": True,
        "manifest": manifest.to_dict(),
        "message": f"Dataset sealed with ID {manifest.dataset_id}",
    }


@router.post("/submit")
def submit_prediction(
    req: SubmitRequest,
    request: Request,
    user: dict = Depends(require_auth),
):
    """
    Submit a prediction artifact (solver, write-only).

    The solver submits predictions WITHOUT access to ground truth.
    Each submission is timestamped, hashed, and stored immutably.
    """
    submitter = get_submitter()
    artifact = submitter.submit(
        dataset_id=req.dataset_id,
        solver_name=req.solver_name,
        solver_version=req.solver_version,
        predictions=req.predictions,
        metadata=req.metadata,
    )

    return {
        "ok": True,
        "submission": {
            "submission_id": artifact.submission_id,
            "predictions_hash": artifact.predictions_hash,
            "submit_timestamp_iso": artifact.submit_timestamp_iso,
        },
        "message": f"Prediction submitted with ID {artifact.submission_id}",
    }


@router.post("/adjudicate")
def adjudicate_submission(
    req: AdjudicateRequest,
    request: Request,
    user: dict = Depends(require_auth),
):
    """
    Adjudicate a submission (adjudicator only).

    Decrypts the sealed dataset, scores the predictions, and generates a report.
    The report is automatically backfilled to guide.benchmark_cases.
    """
    # Check adjudicator role
    user_role = user.get("role", "")
    if user_role not in ("admin", "adjudicator"):
        raise HTTPException(403, "Only adjudicator can adjudicate submissions")

    adjudicator = get_adjudicator()
    backfill = get_backfill()

    # Adjudicate
    report = adjudicator.adjudicate(
        submission_id=req.submission_id,
        scoring_rules=req.scoring_rules,
    )

    # Backfill to guide
    entries = backfill.backfill_report(report)

    return {
        "ok": True,
        "report": {
            "report_id": report.report_id,
            "overall_passed": report.overall_passed,
            "overall_summary": report.overall_summary,
            "adjudicate_timestamp_iso": report.adjudicate_timestamp_iso,
        },
        "guide_entries_created": len(entries),
        "message": f"Adjudication complete. Verdict: {'PASS' if report.overall_passed else 'FAIL'}",
    }


@router.get("/report/{report_id}")
def get_report(report_id: str):
    """Get a scoring report by ID."""
    adjudicator = get_adjudicator()
    try:
        report = adjudicator.load_report(report_id)
        return {"ok": True, "report": report.to_dict()}
    except FileNotFoundError:
        raise HTTPException(404, f"Report not found: {report_id}")


@router.get("/manifest/{dataset_id}")
def get_manifest(dataset_id: str):
    """Get a dataset manifest by ID."""
    sealer = get_sealer()
    try:
        _, manifest = sealer.load_sealed(dataset_id)
        return {"ok": True, "manifest": manifest.to_dict()}
    except FileNotFoundError:
        raise HTTPException(404, f"Manifest not found: {dataset_id}")


@router.post("/adversarial")
def run_adversarial(
    req: AdversarialRequest,
    request: Request,
    user: dict = Depends(require_auth),
):
    """
    Run an adversarial fixture (testing).

    Injects known-bad predictions to verify the adjudicator correctly identifies FAIL.
    """
    adjudicator = get_adjudicator()
    submitter = get_submitter()

    fixture = inject_adversarial(
        adjudicator=adjudicator,
        submitter=submitter,
        dataset_id=req.dataset_id,
        ground_truth=req.ground_truth,
        fixture_type=req.fixture_type,
        seed=req.seed,
    )

    return {
        "ok": True,
        "fixture": fixture.to_dict(),
        "message": f"Adversarial test {'PASSED' if fixture.test_passed else 'FAILED'}",
    }


@router.post("/adversarial/suite")
def run_adversarial_suite_endpoint(
    req: AdversarialRequest,
    request: Request,
    user: dict = Depends(require_auth),
):
    """
    Run a full adversarial test suite.

    Tests: gross_error, systematic_bias, perfect_prediction.
    """
    adjudicator = get_adjudicator()
    submitter = get_submitter()

    fixtures = run_adversarial_suite(
        adjudicator=adjudicator,
        submitter=submitter,
        dataset_id=req.dataset_id,
        ground_truth=req.ground_truth,
    )

    all_passed = all(f.test_passed for f in fixtures)

    return {
        "ok": True,
        "fixtures": [f.to_dict() for f in fixtures],
        "all_tests_passed": all_passed,
        "message": f"Adversarial suite {'PASSED' if all_passed else 'FAILED'}",
    }


@router.get("/guide")
def query_guide(
    solver_name: Optional[str] = None,
    verdict: Optional[str] = None,
    benchmark_name: Optional[str] = None,
):
    """Query guide.benchmark_cases with optional filters."""
    backfill = get_backfill()
    entries = backfill.query_entries(
        solver_name=solver_name,
        verdict=verdict,
        benchmark_name=benchmark_name,
    )
    return {"ok": True, "entries": entries, "count": len(entries)}
