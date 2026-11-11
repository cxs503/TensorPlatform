"""
Metric Versioning Service (p488w1-n02).

C4: Every KPI computation records its lineage:
  - input_version: which input data was used
  - code_version: which code commit produced the value
  - reference_version: which reference/baseline was used
  - computed_at: when the computation happened
  - pipeline_run_id: which pipeline run produced this

This enables full reproducibility: given the same versions, the same
computation should produce the same result.

The versioning service maintains a lineage ledger (append-only) that
records every KPI computation with its full provenance chain.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class MetricVersion:
    """A single versioned KPI computation record."""
    metric_key: str
    value: Any
    input_version: str
    code_version: str
    reference_version: str
    computed_at: str
    pipeline_run_id: str = ""
    compute_host: str = ""
    compute_user: str = ""
    input_hash: str = ""
    config_hash: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MetricLineage:
    """Full lineage chain for a single metric key."""
    metric_key: str
    versions: list[MetricVersion] = field(default_factory=list)
    current_version: MetricVersion | None = None

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def first_computed_at(self) -> str | None:
        if self.versions:
            return self.versions[0].computed_at
        return None

    @property
    def last_computed_at(self) -> str | None:
        if self.versions:
            return self.versions[-1].computed_at
        return None

    def to_dict(self) -> dict:
        return {
            "metric_key": self.metric_key,
            "version_count": self.version_count,
            "first_computed_at": self.first_computed_at,
            "last_computed_at": self.last_computed_at,
            "current_value": self.current_version.value if self.current_version else None,
            "versions": [v.to_dict() for v in self.versions],
        }


class MetricVersioningService:
    """
    Append-only ledger for KPI metric versions.

    Thread-safe: uses a simple lock for concurrent access.
    In production, this would be backed by a database table;
    here we use an in-memory ledger with JSON serialization.
    """

    def __init__(self):
        self._ledger: dict[str, list[MetricVersion]] = {}
        self._lock = __import__("threading").Lock()

    def record_version(
        self,
        metric_key: str,
        value: Any,
        input_version: str,
        code_version: str,
        reference_version: str,
        computed_at: str | None = None,
        pipeline_run_id: str = "",
        compute_host: str = "",
        compute_user: str = "",
        input_data: dict | None = None,
        config: dict | None = None,
        notes: str = "",
    ) -> MetricVersion:
        """
        Record a new version of a metric computation.

        Args:
            metric_key: The KPI identifier (e.g., 'repro_rate')
            value: The computed value
            input_version: Version tag for input data
            code_version: Git commit or code version
            reference_version: Version of reference/baseline data
            computed_at: ISO timestamp (defaults to now)
            pipeline_run_id: Pipeline run identifier
            compute_host: Hostname of compute node
            compute_user: User/service that computed
            input_data: Raw input data (for hashing)
            config: Configuration dict (for hashing)
            notes: Free-form notes

        Returns:
            The created MetricVersion record.
        """
        if computed_at is None:
            computed_at = datetime.now(timezone.utc).isoformat()

        input_hash = ""
        if input_data is not None:
            input_hash = hashlib.sha256(
                json.dumps(input_data, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]

        config_hash = ""
        if config is not None:
            config_hash = hashlib.sha256(
                json.dumps(config, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]

        version = MetricVersion(
            metric_key=metric_key,
            value=value,
            input_version=input_version,
            code_version=code_version,
            reference_version=reference_version,
            computed_at=computed_at,
            pipeline_run_id=pipeline_run_id,
            compute_host=compute_host,
            compute_user=compute_user,
            input_hash=input_hash,
            config_hash=config_hash,
            notes=notes,
        )

        with self._lock:
            if metric_key not in self._ledger:
                self._ledger[metric_key] = []
            self._ledger[metric_key].append(version)

        return version

    def get_lineage(self, metric_key: str) -> MetricLineage:
        """Get the full lineage chain for a metric."""
        with self._lock:
            versions = list(self._ledger.get(metric_key, []))
        lineage = MetricLineage(
            metric_key=metric_key,
            versions=versions,
            current_version=versions[-1] if versions else None,
        )
        return lineage

    def get_current_value(self, metric_key: str) -> Any | None:
        """Get the most recent value for a metric."""
        lineage = self.get_lineage(metric_key)
        if lineage.current_version:
            return lineage.current_version.value
        return None

    def get_all_metrics(self) -> dict[str, MetricLineage]:
        """Get lineage for all tracked metrics."""
        with self._lock:
            keys = list(self._ledger.keys())
        return {k: self.get_lineage(k) for k in keys}

    def get_version_count(self) -> int:
        """Total number of version records across all metrics."""
        with self._lock:
            return sum(len(v) for v in self._ledger.values())

    def get_metric_keys(self) -> list[str]:
        """List all tracked metric keys."""
        with self._lock:
            return list(self._ledger.keys())

    def export_ledger(self) -> dict:
        """Export the full ledger as a serializable dict."""
        with self._lock:
            return {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "metric_count": len(self._ledger),
                "total_versions": sum(len(v) for v in self._ledger.values()),
                "metrics": {
                    k: [ver.to_dict() for ver in vers]
                    for k, vers in self._ledger.items()
                },
            }

    def import_snapshot_with_version(
        self,
        snapshot: dict,
        code_version: str = "unknown",
        reference_version: str = "unknown",
    ) -> list[MetricVersion]:
        """
        Import a KPI snapshot and record versions for each metric in it.
        Extracts provenance from the snapshot if available.
        """
        prov = snapshot.get("provenance", {})
        if not isinstance(prov, dict):
            prov = {}

        input_version = (
            prov.get("input_version")
            or snapshot.get("input_version")
            or "unknown"
        )
        computed_at = (
            prov.get("computed_at")
            or snapshot.get("computed_at")
            or datetime.now(timezone.utc).isoformat()
        )
        pipeline_run_id = prov.get("pipeline_run_id", "")
        compute_host = prov.get("compute_host", "")
        compute_user = prov.get("compute_user", "")

        versions = []
        for key, val in snapshot.items():
            if key in ("provenance", "computed_at", "code_version",
                       "input_version", "reference_version",
                       "compute_host", "compute_user", "pipeline_run_id",
                       "snapshot_id", "id"):
                continue
            if isinstance(val, (int, float)):
                v = self.record_version(
                    metric_key=key,
                    value=val,
                    input_version=input_version,
                    code_version=code_version,
                    reference_version=reference_version,
                    computed_at=computed_at,
                    pipeline_run_id=pipeline_run_id,
                    compute_host=compute_host,
                    compute_user=compute_user,
                )
                versions.append(v)
            elif isinstance(val, dict) and "value" in val:
                v = self.record_version(
                    metric_key=key,
                    value=val["value"],
                    input_version=input_version,
                    code_version=code_version,
                    reference_version=reference_version,
                    computed_at=computed_at,
                    pipeline_run_id=pipeline_run_id,
                    compute_host=compute_host,
                    compute_user=compute_user,
                )
                versions.append(v)

        return versions


# ── Module-level singleton ───────────────────────────────────────────
_service_instance: MetricVersioningService | None = None


def get_metric_versioning_service() -> MetricVersioningService:
    """Get or create the singleton metric versioning service."""
    global _service_instance
    if _service_instance is None:
        _service_instance = MetricVersioningService()
    return _service_instance


def reset_metric_versioning_service() -> None:
    """Reset the singleton (for testing)."""
    global _service_instance
    _service_instance = None
