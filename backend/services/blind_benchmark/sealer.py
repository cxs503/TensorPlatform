"""
Dataset Sealer — Seal benchmark datasets for blind evaluation.

Responsibilities:
  - Accept benchmark dataset (JSON/CSV)
  - Encrypt with adjudicator-only key
  - Generate manifest with sha256, metadata, timestamps
  - Store sealed artifact + manifest

The sealer runs at dataset ingestion time, BEFORE any solver can access the data.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .crypto import seal_data, generate_master_key, SealedEnvelope


@dataclass
class SealManifest:
    """Manifest for a sealed dataset."""
    dataset_id: str                    # unique identifier
    dataset_name: str                  # human-readable name
    seal_timestamp: float              # unix timestamp
    seal_timestamp_iso: str            # ISO 8601 format
    plaintext_sha256: str              # hash of original data
    plaintext_size: int                # bytes
    sealed_size: int                   # bytes (with overhead)
    encryption_algorithm: str          # "SHA256-CTR-HMAC-SHA256"
    key_id: str                        # identifier for the key (not the key itself)
    envelope_metadata: Dict[str, Any] = field(default_factory=dict)  # crypto metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_name": self.dataset_name,
            "seal_timestamp": self.seal_timestamp,
            "seal_timestamp_iso": self.seal_timestamp_iso,
            "plaintext_sha256": self.plaintext_sha256,
            "plaintext_size": self.plaintext_size,
            "sealed_size": self.sealed_size,
            "encryption_algorithm": self.encryption_algorithm,
            "key_id": self.key_id,
            "envelope_metadata": self.envelope_metadata,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


class DatasetSealer:
    """
    Seals benchmark datasets for blind evaluation.

    Usage:
        sealer = DatasetSealer(storage_dir="/path/to/sealed")
        manifest = sealer.seal_dataset(
            dataset={"cases": [...]},
            dataset_name="resistance_benchmark_v3",
            metadata={"source": "ITTC 2010"}
        )
    """

    def __init__(self, storage_dir: str | Path, master_key: Optional[bytes] = None):
        """
        Args:
            storage_dir: directory to store sealed artifacts
            master_key: 32-byte key (generated if not provided)
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        if master_key is None:
            master_key = generate_master_key()
        self.master_key = master_key
        self.key_id = hashlib.sha256(master_key).hexdigest()[:16]

    def seal_dataset(
        self,
        dataset: Dict[str, Any],
        dataset_name: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SealManifest:
        """
        Seal a benchmark dataset.

        Args:
            dataset: benchmark data (dict, will be JSON-serialized)
            dataset_name: human-readable name
            metadata: additional metadata (source, version, etc.)

        Returns:
            SealManifest with all sealing details
        """
        # Serialize dataset
        plaintext = json.dumps(dataset, sort_keys=True, ensure_ascii=False).encode("utf-8")

        # Compute plaintext hash
        plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()

        # Generate dataset ID
        dataset_id = hashlib.sha256(
            f"{dataset_name}:{plaintext_sha256}:{time.time()}".encode()
        ).hexdigest()[:16]

        # Seal the data
        envelope = seal_data(plaintext, self.master_key, metadata=metadata or {})

        # Create manifest (include envelope metadata for reconstruction)
        manifest = SealManifest(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            seal_timestamp=time.time(),
            seal_timestamp_iso=datetime.utcnow().isoformat() + "Z",
            plaintext_sha256=plaintext_sha256,
            plaintext_size=len(plaintext),
            sealed_size=len(envelope.to_bytes()),
            encryption_algorithm="SHA256-CTR-HMAC-SHA256",
            key_id=self.key_id,
            envelope_metadata=envelope.metadata,  # Store crypto metadata
            metadata=metadata or {},
        )

        # Store sealed artifact
        sealed_path = self.storage_dir / f"{dataset_id}.sealed"
        sealed_path.write_bytes(envelope.to_bytes())

        # Store manifest
        manifest_path = self.storage_dir / f"{dataset_id}.manifest.json"
        manifest_path.write_text(manifest.to_json(), encoding="utf-8")

        return manifest

    def load_sealed(self, dataset_id: str) -> tuple[SealedEnvelope, SealManifest]:
        """Load a sealed dataset and its manifest."""
        sealed_path = self.storage_dir / f"{dataset_id}.sealed"
        manifest_path = self.storage_dir / f"{dataset_id}.manifest.json"

        if not sealed_path.exists():
            raise FileNotFoundError(f"Sealed artifact not found: {sealed_path}")
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        manifest_dict = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = SealManifest(**manifest_dict)

        sealed_data = sealed_path.read_bytes()
        # Use envelope_metadata (which has crypto-computed hashes) for reconstruction
        envelope = SealedEnvelope.from_bytes(sealed_data, manifest.envelope_metadata)

        return envelope, manifest

    def verify_seal_integrity(self, dataset_id: str) -> bool:
        """Verify that sealed artifact matches manifest (no tampering)."""
        try:
            envelope, manifest = self.load_sealed(dataset_id)

            # Check sizes
            if envelope.metadata.get("plaintext_size") != manifest.plaintext_size:
                return False

            # Check hash
            if envelope.metadata.get("plaintext_sha256") != manifest.plaintext_sha256:
                return False

            return True
        except Exception:
            return False
