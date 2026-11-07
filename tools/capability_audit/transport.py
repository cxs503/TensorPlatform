"""Transport layer: HTTP to the ship-performance API (:8001) plus a scripted
transport used by the test-suite so the audit can be exercised hermetically.

The audit is *read-only* by construction: only POSTs to surveyed compute
endpoints are issued, no writes, no auth, no state mutation.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Tuple

import requests


class TransportError(RuntimeError):
    """Raised when the capability endpoint cannot be reached or 4xx/5xx fails."""


class HttpTransport:
    """Minimal JSON POST client against the ship-performance API."""

    def __init__(self, base_url: str, timeout: float = 120.0, retries: int = 1):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.calls = 0

    def post(self, path: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last: Optional[Exception] = None
        for _attempt in range(self.retries + 1):
            try:
                self.calls += 1
                resp = requests.post(url, json=payload, timeout=self.timeout)
                if resp.status_code >= 400:
                    raise TransportError(
                        f"{path} -> HTTP {resp.status_code}: {resp.text[:200]}")
                return resp.json()
            except TransportError as exc:
                last = exc
            except requests.RequestException as exc:  # pragma: no cover - network
                last = TransportError(f"{path} -> {exc}")
        raise last  # type: ignore[misc]


class ScriptedTransport:
    """Deterministic transport for tests: maps (path, payload-canonical) to a
    callable producing the response, with an optional default handler."""

    def __init__(self, handler: Callable[[str, Dict[str, Any]], Any]):
        self.handler = handler
        self.calls = 0
        self.log: list = []

    def post(self, path: str, payload: Dict[str, Any]) -> Any:
        self.calls += 1
        self.log.append((path, json.dumps(payload, sort_keys=True)))
        return self.handler(path, payload)


def probe_transport(transport, base_url: str) -> Tuple[bool, str]:
    """Cheap liveness probe so an unreachable API yields WITHHELD, not a crash."""
    if isinstance(transport, ScriptedTransport):
        return True, "scripted transport"
    try:
        requests.get(base_url.rstrip("/") + "/openapi.json", timeout=5)
        return True, "openapi reachable"
    except requests.RequestException as exc:  # pragma: no cover - network
        return False, str(exc)