"""Minimal JSON-over-HTTP client with explicit failure taxonomy.

The prober must be able to tell four different kinds of "no answer" apart,
because they map to different evidence levels:

``TRANSPORT``  connection refused / DNS / timeout          → WITHHELD
``HTTP_4XX``   400/422 = payload rejected, 404/405 = absent → INVALID_INPUT / NOT_SUPPORTED
``HTTP_5XX``   server error                                → WITHHELD
``OK``         parseable JSON body                         → decided by probes

The client never raises for HTTP status; it raises only for programming errors.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from .canonical import payload_sha256


@dataclass
class Response:
    status: int = 0
    payload: Any = None
    body_sha256: Optional[str] = None
    error: Optional[str] = None
    error_kind: Optional[str] = None   # TRANSPORT | TIMEOUT | PARSE | HTTP
    latency_s: float = 0.0
    raw_bytes: int = 0
    retried: int = 0
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300 and self.payload is not None

    @property
    def client_rejected(self) -> bool:
        return self.status in (400, 401, 403, 409, 422)

    @property
    def absent(self) -> bool:
        return self.status in (404, 405, 410, 501)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "body_sha256": self.body_sha256,
            "error": self.error,
            "error_kind": self.error_kind,
            "latency_s": round(self.latency_s, 6),
            "raw_bytes": self.raw_bytes,
        }


class JsonClient:
    """Synchronous JSON client. Thread-safe (no shared mutable state)."""

    def __init__(self, base_url: str, default_timeout: float = 20.0,
                 api_prefix: str = "/api/v1", user_agent: str = "mock-detector/1.0",
                 transport_retries: int = 1):
        self.base_url = base_url.rstrip("/")
        self.default_timeout = default_timeout
        self.api_prefix = api_prefix
        self.user_agent = user_agent
        self.transport_retries = max(0, int(transport_retries))
        self.calls = 0
        self.retries = 0

    def url_for(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if path.startswith(self.api_prefix) or path.endswith(".json") \
                or path.startswith("/health"):
            return f"{self.base_url}{path}"
        return f"{self.base_url}{self.api_prefix}{path}"

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None,
                timeout: Optional[float] = None) -> Response:
        """One JSON request, with a bounded transport-level retry.

        A transient 5xx/timeout says nothing about the endpoint's *behaviour*, so
        retrying it is not verdict shopping: it removes noise that would otherwise
        be mislabelled ``UNREACHABLE`` (and thus WITHHELD) for a healthy endpoint.
        """
        resp = self._request_once(method, path, payload, timeout)
        attempts = 0
        while (attempts < self.transport_retries
               and (resp.error_kind in ("TIMEOUT", "TRANSPORT")
                    or (resp.status and 500 <= resp.status < 600))):
            attempts += 1
            self.retries += 1
            resp = self._request_once(method, path, payload, timeout)
            resp.retried = attempts
        return resp

    def _request_once(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None,
                      timeout: Optional[float] = None) -> Response:
        url = self.url_for(path)
        data = None
        headers = {"Accept": "application/json", "User-Agent": self.user_agent}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method.upper())
        t0 = time.perf_counter()
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.default_timeout) as resp:
                raw = resp.read()
                latency = time.perf_counter() - t0
                return self._decode(resp.status, raw, latency,
                                    dict(resp.headers.items()))
        except urllib.error.HTTPError as exc:                     # 4xx / 5xx
            raw = exc.read() if hasattr(exc, "read") else b""
            latency = time.perf_counter() - t0
            resp = self._decode(exc.code, raw, latency, dict(exc.headers or {}))
            if resp.payload is None:
                resp.error = f"HTTP {exc.code}"
                resp.error_kind = "HTTP"
            return resp
        except socket.timeout:
            return Response(error="timeout", error_kind="TIMEOUT",
                            latency_s=time.perf_counter() - t0)
        except urllib.error.URLError as exc:
            kind = "TIMEOUT" if "timed out" in str(exc).lower() else "TRANSPORT"
            return Response(error=f"{type(exc).__name__}: {exc.reason}",
                            error_kind=kind, latency_s=time.perf_counter() - t0)
        except (TimeoutError, socket.error) as exc:
            return Response(error=f"{type(exc).__name__}: {exc}", error_kind="TIMEOUT",
                            latency_s=time.perf_counter() - t0)

    @staticmethod
    def _decode(status: int, raw: bytes, latency: float,
                headers: Dict[str, str]) -> Response:
        body_hash = payload_sha256(raw.decode("utf-8", "replace")) if raw else None
        payload: Any = None
        err = None
        kind = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                payload = None
                err = "non-JSON body"
                kind = "PARSE"
        return Response(status=status, payload=payload, body_sha256=body_hash,
                        error=err, error_kind=kind, latency_s=latency,
                        raw_bytes=len(raw), headers=headers)

    def get_json(self, path: str, timeout: Optional[float] = None) -> Tuple[int, Any]:
        resp = self.request("GET", path, None, timeout)
        return resp.status, resp.payload