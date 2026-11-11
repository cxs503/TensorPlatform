"""
Snapshot Traceability Gate Middleware (p488w1-n02).

C2: Zero-provenance snapshots are fail-closed — rejected at the gate.
Any KPI snapshot submitted without the required provenance fields
(computed_at, code_version, input_version, reference_version) is
rejected with HTTP 422 and a structured error body.

This middleware intercepts POST/PUT requests to KPI snapshot endpoints
and validates provenance before allowing the request through.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# ── Provenance requirements ──────────────────────────────────────────
REQUIRED_PROVENANCE_FIELDS = [
    "computed_at",
    "code_version",
    "input_version",
    "reference_version",
]

# ── Endpoints that require provenance ────────────────────────────────
PROVENANCE_REQUIRED_PATHS = [
    "/tensor/kpi/snapshot",
    "/tensor/kpi/snapshots",
    "/api/v1/kpi/snapshot",
    "/api/v1/kpi/snapshots",
    "/kpi/snapshot",
    "/kpi/snapshots",
]

# ── Audit log (in-memory, flushed periodically) ─────────────────────
_AUDIT_LOG: list[dict] = []
_MAX_AUDIT_SIZE = 10000


def get_audit_log() -> list[dict]:
    """Return a copy of the audit log (for testing/monitoring)."""
    return list(_AUDIT_LOG)


def clear_audit_log() -> None:
    """Clear the audit log (for testing)."""
    _AUDIT_LOG.clear()


def _record_audit(
    request_path: str,
    snapshot_id: str | None,
    decision: str,
    missing_fields: list[str] | None = None,
    reason: str = "",
) -> None:
    """Record an audit entry."""
    entry = {
        "timestamp": time.time(),
        "iso_timestamp": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "path": request_path,
        "snapshot_id": snapshot_id,
        "decision": decision,
        "missing_fields": missing_fields or [],
        "reason": reason,
    }
    _AUDIT_LOG.append(entry)
    if len(_AUDIT_LOG) > _MAX_AUDIT_SIZE:
        _AUDIT_LOG.pop(0)


def _is_provenance_required_path(path: str) -> bool:
    """Check if the request path requires provenance validation."""
    for prefix in PROVENANCE_REQUIRED_PATHS:
        if path.startswith(prefix) or path.endswith(prefix):
            return True
    return False


def _extract_body_sync(request: Request) -> dict | None:
    """
    Try to extract JSON body from request state (set by earlier middleware).
    Returns None if body is not available or not JSON.
    """
    # Check if body was already parsed and cached
    if hasattr(request.state, "parsed_body"):
        return request.state.parsed_body
    return None


def check_provenance_fields(body: dict) -> tuple[bool, list[str]]:
    """
    Check if all required provenance fields are present and non-empty.
    Returns (all_present, missing_fields).
    """
    missing = []
    for field in REQUIRED_PROVENANCE_FIELDS:
        val = body.get(field)
        # Also check nested under 'provenance' key
        if val is None or val == "":
            prov = body.get("provenance", {})
            if isinstance(prov, dict):
                val = prov.get(field)
        if val is None or val == "":
            missing.append(field)
    return len(missing) == 0, missing


class SnapshotTraceabilityGate(BaseHTTPMiddleware):
    """
    Middleware that enforces provenance requirements on KPI snapshot
    submissions. Zero-provenance snapshots are fail-closed (rejected).

    Behavior:
    - GET requests pass through (read operations don't need provenance)
    - POST/PUT to snapshot endpoints: body must contain all required
      provenance fields, otherwise 422 is returned
    - All decisions are logged to the audit trail
    """

    def __init__(self, app, fail_closed: bool = True):
        super().__init__(app)
        self.fail_closed = fail_closed

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only intercept write operations to snapshot endpoints
        if request.method not in ("POST", "PUT", "PATCH"):
            return await call_next(request)

        if not _is_provenance_required_path(request.url.path):
            return await call_next(request)

        # Try to read and validate the body
        try:
            body_bytes = await request.body()
            if not body_bytes:
                _record_audit(
                    request.url.path, None, "REJECT",
                    reason="empty_body",
                )
                if self.fail_closed:
                    return JSONResponse(
                        status_code=422,
                        content={
                            "error": "SNAPSHOT_TRACEABILITY_GATE",
                            "decision": "FAIL_CLOSED",
                            "reason": "empty_body",
                            "message": (
                                "KPI snapshot submission rejected: "
                                "empty body. All snapshots must include "
                                "provenance fields: "
                                + ", ".join(REQUIRED_PROVENANCE_FIELDS)
                            ),
                        },
                    )
                return await call_next(request)

            body = json.loads(body_bytes)
        except (json.JSONDecodeError, Exception) as e:
            _record_audit(
                request.url.path, None, "REJECT",
                reason=f"invalid_json: {str(e)[:100]}",
            )
            if self.fail_closed:
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": "SNAPSHOT_TRACEABILITY_GATE",
                        "decision": "FAIL_CLOSED",
                        "reason": "invalid_json",
                        "message": "KPI snapshot submission rejected: invalid JSON body.",
                    },
                )
            return await call_next(request)

        # Check provenance
        all_present, missing = check_provenance_fields(body)
        snapshot_id = body.get("snapshot_id", body.get("id", "unknown"))

        if not all_present:
            _record_audit(
                request.url.path, snapshot_id, "REJECT",
                missing_fields=missing,
                reason="missing_provenance_fields",
            )
            logger.warning(
                "Snapshot %s rejected: missing provenance fields %s",
                snapshot_id, missing,
            )
            if self.fail_closed:
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": "SNAPSHOT_TRACEABILITY_GATE",
                        "decision": "FAIL_CLOSED",
                        "snapshot_id": snapshot_id,
                        "missing_provenance_fields": missing,
                        "required_fields": REQUIRED_PROVENANCE_FIELDS,
                        "message": (
                            f"KPI snapshot '{snapshot_id}' rejected: "
                            f"missing provenance fields {missing}. "
                            f"All snapshots must include: "
                            + ", ".join(REQUIRED_PROVENANCE_FIELDS)
                        ),
                    },
                )
            return await call_next(request)

        # Provenance OK — pass through
        _record_audit(
            request.url.path, snapshot_id, "ACCEPT",
            reason="provenance_complete",
        )

        # Cache parsed body for downstream use
        request.state.parsed_body = body
        return await call_next(request)


# ── Standalone validation function (for use without middleware) ──────

def validate_snapshot_provenance(snapshot: dict) -> dict:
    """
    Validate provenance of a snapshot dict.
    Returns a structured result dict.

    This is the programmatic API for the gate — usable in tests,
    batch validators, and CLI tools without needing the HTTP middleware.
    """
    all_present, missing = check_provenance_fields(snapshot)
    snapshot_id = snapshot.get("snapshot_id", snapshot.get("id", "unknown"))

    if all_present:
        return {
            "snapshot_id": snapshot_id,
            "decision": "ACCEPT",
            "has_provenance": True,
            "missing_fields": [],
            "fail_closed": False,
        }
    else:
        return {
            "snapshot_id": snapshot_id,
            "decision": "FAIL_CLOSED",
            "has_provenance": False,
            "missing_fields": missing,
            "fail_closed": True,
            "required_fields": REQUIRED_PROVENANCE_FIELDS,
        }
