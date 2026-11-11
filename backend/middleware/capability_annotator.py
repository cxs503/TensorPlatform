"""Capability annotator middleware (p488w1-n01).

Intercepts designated API paths, captures request/response payloads,
and records them for capability evidence tracking.

Design principles:
- Zero domain semantics: tracked paths come from configuration, not hardcoded.
- Non-invasive: failures in annotation never block the request.
- Content-addressed: each record carries a SHA-256 digest for replay.

C1 acceptance: intercepts >= 5 API paths recording request/response.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Default tracked paths (configurable via inject_config)
DEFAULT_TRACKED_PATHS: tuple = (
    "/api/v1/calculations/resistance",
    "/api/v1/calculations/propulsion",
    "/api/v1/calculations/seakeeping",
    "/api/v1/calculations/stability",
    "/api/v1/calculations/hydrostatics",
    "/api/v1/calculations/maneuvering",
    "/api/v1/green_navigation/optimize",
)


@dataclass(frozen=True)
class AnnotationRecord:
    """Immutable record of a single API call annotation."""
    path: str
    method: str
    status_code: int
    request_body: Optional[Dict[str, Any]]
    response_body: Optional[Dict[str, Any]]
    reference_value: Optional[float]
    computed_value: Optional[float]
    error_pct: Optional[float]
    timestamp: str
    digest: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "method": self.method,
            "status_code": self.status_code,
            "request_body": self.request_body,
            "response_body": self.response_body,
            "reference_value": self.reference_value,
            "computed_value": self.computed_value,
            "error_pct": self.error_pct,
            "timestamp": self.timestamp,
            "digest": self.digest,
        }


def _compute_digest(record_dict: Dict[str, Any]) -> str:
    """Content-addressed digest for replay verification."""
    canonical = json.dumps(record_dict, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _safe_extract_value(body: Optional[Dict], keys: Sequence[str]) -> Optional[float]:
    """Safely extract a numeric value from a nested dict by key path."""
    if body is None:
        return None
    current = body
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    if isinstance(current, (int, float)):
        return float(current)
    return None


class CapabilityAnnotator:
    """Records API call annotations for capability evidence tracking.

    Usage::

        annotator = CapabilityAnnotator()
        annotator.configure(tracked_paths=["/api/v1/calculations/resistance"])
        record = annotator.annotate(
            path="/api/v1/calculations/resistance",
            method="POST",
            status_code=200,
            request_body={...},
            response_body={...},
        )
    """

    def __init__(self, storage_dir: Optional[str] = None):
        self._tracked_paths: set = set(DEFAULT_TRACKED_PATHS)
        self._records: List[AnnotationRecord] = []
        self._storage_dir = Path(storage_dir) if storage_dir else None
        if self._storage_dir:
            self._storage_dir.mkdir(parents=True, exist_ok=True)

    @property
    def tracked_paths(self) -> frozenset:
        return frozenset(self._tracked_paths)

    @property
    def records(self) -> List[AnnotationRecord]:
        return list(self._records)

    def configure(self, tracked_paths: Sequence[str]) -> None:
        """Replace tracked paths (zero domain semantics: paths are data)."""
        self._tracked_paths = set(tracked_paths)

    def add_tracked_path(self, path: str) -> None:
        """Add a single path to the tracked set."""
        self._tracked_paths.add(path)

    def is_tracked(self, path: str) -> bool:
        """Check if a path should be annotated."""
        return path in self._tracked_paths

    def annotate(
        self,
        path: str,
        method: str = "POST",
        status_code: int = 200,
        request_body: Optional[Dict[str, Any]] = None,
        response_body: Optional[Dict[str, Any]] = None,
        reference_value: Optional[float] = None,
        computed_value: Optional[float] = None,
    ) -> Optional[AnnotationRecord]:
        """Annotate an API call. Returns None if path is not tracked.

        Never raises: annotation failures are logged and swallowed.
        """
        if not self.is_tracked(path):
            return None

        try:
            # Compute error if both values available
            error_pct = None
            if reference_value is not None and computed_value is not None:
                if abs(reference_value) > 1e-12:
                    error_pct = abs(computed_value - reference_value) / abs(reference_value) * 100.0

            timestamp = datetime.now(timezone.utc).isoformat()

            # Build record dict for digest (without digest field itself)
            record_dict = {
                "path": path,
                "method": method,
                "status_code": status_code,
                "request_body": request_body,
                "response_body": response_body,
                "reference_value": reference_value,
                "computed_value": computed_value,
                "error_pct": error_pct,
                "timestamp": timestamp,
            }
            digest = _compute_digest(record_dict)
            record_dict["digest"] = digest

            record = AnnotationRecord(**record_dict)
            self._records.append(record)

            # Persist if storage configured
            if self._storage_dir:
                self._persist(record)

            return record

        except Exception as exc:
            logger.warning("Annotation failed for %s: %s", path, exc)
            return None

    def _persist(self, record: AnnotationRecord) -> None:
        """Persist a single record to disk."""
        if self._storage_dir is None:
            return
        fname = f"annotation_{record.digest[:12]}.json"
        fpath = self._storage_dir / fname
        fpath.write_text(json.dumps(record.to_dict(), indent=2, default=str))

    def get_records_for_path(self, path: str) -> List[AnnotationRecord]:
        """Get all annotation records for a specific path."""
        return [r for r in self._records if r.path == path]

    def get_error_history(self, path: str) -> List[Optional[float]]:
        """Get error percentages for a path (for grade evaluation)."""
        return [r.error_pct for r in self._records if r.path == path and r.error_pct is not None]

    def summary(self) -> Dict[str, Any]:
        """Return summary of all annotations."""
        path_counts: Dict[str, int] = {}
        for r in self._records:
            path_counts[r.path] = path_counts.get(r.path, 0) + 1

        return {
            "total_records": len(self._records),
            "tracked_paths_count": len(self._tracked_paths),
            "paths_with_records": len(path_counts),
            "path_counts": path_counts,
        }


# ---------------------------------------------------------------------------
# FastAPI/Starlette middleware integration
# ---------------------------------------------------------------------------

class CapabilityAnnotationMiddleware:
    """ASGI middleware that auto-annotates tracked API paths.

    Wraps the ASGI app to capture request/response bodies for designated paths.
    """

    def __init__(self, app: Any, annotator: Optional[CapabilityAnnotator] = None):
        self.app = app
        self.annotator = annotator or CapabilityAnnotator()

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not self.annotator.is_tracked(path):
            await self.app(scope, receive, send)
            return

        # Capture request body
        request_body_bytes = b""
        response_body_bytes = b""
        status_code = 200

        async def receive_wrapper():
            nonlocal request_body_bytes
            message = await receive()
            if message.get("type") == "http.request":
                request_body_bytes += message.get("body", b"")
            return message

        async def send_wrapper(message):
            nonlocal response_body_bytes, status_code
            if message.get("type") == "http.response.start":
                status_code = message.get("status", 200)
            elif message.get("type") == "http.response.body":
                response_body_bytes += message.get("body", b"")
            await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)

        # Annotate after response
        req_dict = None
        resp_dict = None
        try:
            if request_body_bytes:
                req_dict = json.loads(request_body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        try:
            if response_body_bytes:
                resp_dict = json.loads(response_body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        method = scope.get("method", "GET")
        self.annotator.annotate(
            path=path,
            method=method,
            status_code=status_code,
            request_body=req_dict,
            response_body=resp_dict,
        )


# Module-level singleton for shared use
_default_annotator: Optional[CapabilityAnnotator] = None


def get_annotator() -> CapabilityAnnotator:
    """Get or create the module-level annotator singleton."""
    global _default_annotator
    if _default_annotator is None:
        _default_annotator = CapabilityAnnotator()
    return _default_annotator


def reset_annotator() -> None:
    """Reset the singleton (for testing)."""
    global _default_annotator
    _default_annotator = None
