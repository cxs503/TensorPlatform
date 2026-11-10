"""
Prediction Submitter — Write-only prediction artifact submission.

The solver submits predictions WITHOUT access to ground truth.
Each submission is timestamped, hashed, and stored immutably.

Key property: The submission endpoint CANNOT decrypt the sealed dataset.
It only accepts prediction artifacts and stores them for later adjudication.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PredictionArtifact:
    """A submitted prediction artifact."""
    submission_id: str                 # unique identifier
    dataset_id: str                    # which sealed dataset this predicts
    solver_name: str                   # who submitted this
    solver_version: str                # version tag
    submit_timestamp: float            # unix timestamp
    submit_timestamp_iso: str          # ISO 8601
    predictions_hash: str              # sha256 of predictions
    predictions_size: int              # bytes
    predictions: Dict[str, Any]        # the actual predictions
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "dataset_id": self.dataset_id,
            "solver_name": self.solver_name,
            "solver_version": self.solver_version,
            "submit_timestamp": self.submit_timestamp,
            "submit_timestamp_iso": self.submit_timestamp_iso,
            "predictions_hash": self.predictions_hash,
            "predictions_size": self.predictions_size,
            "predictions": self.predictions,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


class PredictionSubmitter:
    """
    Accepts prediction submissions (write-only, no ground truth access).

    Usage:
        submitter = PredictionSubmitter(storage_dir="/path/to/submissions")
        artifact = submitter.submit(
            dataset_id="abc123",
            solver_name="holtrop_method",
            solver_version="v1.0",
            predictions={"case_01": {"Ct": 0.0035}, "case_02": {"Ct": 0.0042}},
            metadata={"description": "Holtrop-Mennen 1988"}
        )
    """

    def __init__(self, storage_dir: str | Path):
        """
        Args:
            storage_dir: directory to store submission artifacts
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def submit(
        self,
        dataset_id: str,
        solver_name: str,
        solver_version: str,
        predictions: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PredictionArtifact:
        """
        Submit a prediction artifact.

        Args:
            dataset_id: which sealed dataset this predicts
            solver_name: name of the solver/method
            solver_version: version tag
            predictions: dict of case_id -> predicted values
            metadata: additional metadata

        Returns:
            PredictionArtifact with submission details
        """
        # Serialize predictions
        predictions_json = json.dumps(predictions, sort_keys=True, ensure_ascii=False)
        predictions_bytes = predictions_json.encode("utf-8")

        # Compute hash
        predictions_hash = hashlib.sha256(predictions_bytes).hexdigest()

        # Generate submission ID
        submission_id = hashlib.sha256(
            f"{dataset_id}:{solver_name}:{predictions_hash}:{time.time()}".encode()
        ).hexdigest()[:16]

        # Create artifact
        artifact = PredictionArtifact(
            submission_id=submission_id,
            dataset_id=dataset_id,
            solver_name=solver_name,
            solver_version=solver_version,
            submit_timestamp=time.time(),
            submit_timestamp_iso=datetime.utcnow().isoformat() + "Z",
            predictions_hash=predictions_hash,
            predictions_size=len(predictions_bytes),
            predictions=predictions,
            metadata=metadata or {},
        )

        # Store artifact
        artifact_path = self.storage_dir / f"{submission_id}.submission.json"
        artifact_path.write_text(artifact.to_json(), encoding="utf-8")

        return artifact

    def load_submission(self, submission_id: str) -> PredictionArtifact:
        """Load a submission artifact."""
        artifact_path = self.storage_dir / f"{submission_id}.submission.json"

        if not artifact_path.exists():
            raise FileNotFoundError(f"Submission not found: {artifact_path}")

        artifact_dict = json.loads(artifact_path.read_text(encoding="utf-8"))
        return PredictionArtifact(**artifact_dict)

    def list_submissions(self, dataset_id: Optional[str] = None) -> List[PredictionArtifact]:
        """List all submissions, optionally filtered by dataset_id."""
        submissions = []
        for path in self.storage_dir.glob("*.submission.json"):
            artifact_dict = json.loads(path.read_text(encoding="utf-8"))
            artifact = PredictionArtifact(**artifact_dict)
            if dataset_id is None or artifact.dataset_id == dataset_id:
                submissions.append(artifact)
        return submissions

    def verify_submission_integrity(self, submission_id: str) -> bool:
        """Verify that submission artifact has not been tampered with."""
        try:
            artifact = self.load_submission(submission_id)

            # Recompute hash
            predictions_json = json.dumps(artifact.predictions, sort_keys=True, ensure_ascii=False)
            predictions_bytes = predictions_json.encode("utf-8")
            actual_hash = hashlib.sha256(predictions_bytes).hexdigest()

            return actual_hash == artifact.predictions_hash
        except Exception:
            return False
