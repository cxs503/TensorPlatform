"""
Truth Gates Metering Middleware

Records per-mission actual tool_calls / tokens / wall_seconds from the
infrastructure perspective. This is the ground truth for reconciliation.

Only the platform middleware writes to metered_usage — agent accounts
are blocked at the API layer (see truth_gates.py submit_metered_usage).
"""
from __future__ import annotations

import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class TruthGatesMeterMiddleware(BaseHTTPMiddleware):
    """
    Middleware that tracks API call metrics per mission.

    For each request to /tensor/missions/* endpoints, it records:
      - tool_calls: number of API calls made during the mission
      - tokens: estimated token usage (from request/response bodies)
      - wall_seconds: time spent processing

    The metrics are stored in-memory and flushed to the database
    periodically or on mission completion.
    """

    def __init__(self, app, db_session_factory=None):
        super().__init__(app)
        self.db_session_factory = db_session_factory
        # In-memory tracking: {mission_id: {tool_calls, tokens, start_time}}
        self._mission_metrics: dict[int, dict] = {}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()

        # Extract mission_id from path if applicable
        mission_id = self._extract_mission_id(request)

        # Process request
        response = await call_next(request)

        elapsed = time.time() - start_time

        # Track metrics for mission-related endpoints
        if mission_id and self._is_mission_endpoint(request.url.path):
            if mission_id not in self._mission_metrics:
                self._mission_metrics[mission_id] = {
                    "tool_calls": 0,
                    "tokens": 0,
                    "start_time": start_time,
                    "wall_seconds": 0.0,
                    "api_calls_detail": {},
                }

            metrics = self._mission_metrics[mission_id]
            metrics["tool_calls"] += 1
            metrics["wall_seconds"] += elapsed

            # Estimate tokens from request body size (rough heuristic)
            # In production, this would come from LLM API responses
            content_length = int(request.headers.get("content-length", 0))
            estimated_tokens = content_length // 4  # ~4 chars per token
            metrics["tokens"] += estimated_tokens

            # Track per-endpoint breakdown
            endpoint = request.url.path
            metrics["api_calls_detail"][endpoint] = metrics["api_calls_detail"].get(endpoint, 0) + 1

        return response

    def _extract_mission_id(self, request: Request) -> int | None:
        """Extract mission_id from URL path like /tensor/missions/{id}/..."""
        path = request.url.path
        if "/tensor/missions/" in path:
            parts = path.split("/")
            try:
                idx = parts.index("missions")
                if idx + 1 < len(parts):
                    return int(parts[idx + 1])
            except (ValueError, IndexError):
                pass
        return None

    def _is_mission_endpoint(self, path: str) -> bool:
        """Check if this is a mission-related endpoint to track."""
        return "/tensor/missions/" in path

    def get_mission_metrics(self, mission_id: int) -> dict | None:
        """Get accumulated metrics for a mission."""
        return self._mission_metrics.get(mission_id)

    def flush_mission_metrics(self, mission_id: int, db_session) -> dict | None:
        """
        Flush in-memory metrics to database (metered_usage table).
        Called on mission completion.
        """
        metrics = self._mission_metrics.pop(mission_id, None)
        if not metrics:
            return None

        from models.truth_gates import MeteredUsage

        meter = MeteredUsage(
            mission_id=mission_id,
            tool_calls=metrics["tool_calls"],
            tokens_used=metrics["tokens"],
            wall_seconds=metrics["wall_seconds"],
            api_calls_detail=metrics["api_calls_detail"],
            recorded_by="truth_gates_middleware",
        )
        db_session.add(meter)
        db_session.commit()
        db_session.refresh(meter)
        return meter.to_dict()
