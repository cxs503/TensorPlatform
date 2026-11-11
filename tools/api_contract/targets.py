"""Execution targets: live HTTP, in-process ASGI, and degradation stubs.

Any target turns a case into a :class:`Response`.  Failures are *values*
(``error`` set, ``status`` ``None``) so that a contract run records ERROR rows
instead of raising (acceptance criterion f: unreachable endpoint -> ERROR, no
crash).

Why two live-ish targets
------------------------
``HttpTarget`` exercises the real deployment, so it catches deployment/spec
drift.  ``ASGITarget`` drives the application object in-process through the
ASGI contract, which removes network flakiness and lets the harness finish a
full-operation sweep when the deployed service is being restarted by its
supervisor.  A discrepancy between the two is itself a finding (deployed code
!= repository code).
"""
from __future__ import annotations

import importlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

ERROR_UNREACHABLE = "UNREACHABLE"
ERROR_TIMEOUT = "TIMEOUT"
ERROR_EXCEPTION = "EXCEPTION"


@dataclass
class Response:
    status: Optional[int]
    body: Any = None
    error: Optional[str] = None
    elapsed_ms: float = 0.0
    bytes: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "bytes": self.bytes,
            "detail": self.detail,
        }


def _encode_query(query: Optional[Dict[str, Any]]) -> str:
    if not query:
        return ""
    parts = []
    for key, value in query.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            for item in value:
                parts.append((key, str(item)))
            continue
        parts.append((key, str(value)))
    return urllib.parse.urlencode(parts)


class HttpTarget:
    """Live HTTP target with health-gated retry.

    ``health_path`` (e.g. ``"/health"``) is injected data: when a request fails
    with a connection error the target polls the health endpoint until the
    service answers again (bounded by ``recovery_wait``) and then retries, so a
    supervisor restart degrades throughput instead of destroying the run.
    """

    def __init__(self, base_url: str, timeout: float = 20.0, retries: int = 2,
                 health_path: str = "", recovery_wait: float = 60.0,
                 poll_interval: float = 1.0, headers: Optional[Dict[str, str]] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.health_path = health_path
        self.recovery_wait = recovery_wait
        self.poll_interval = poll_interval
        self.headers = {"Content-Type": "application/json", "Accept": "application/json",
                        **(headers or {})}
        self.recoveries = 0

    # -- health ---------------------------------------------------------
    def healthy(self, timeout: float = 3.0) -> bool:
        if not self.health_path:
            return True
        try:
            with urllib.request.urlopen(self.base_url + self.health_path, timeout=timeout) as fh:
                return 200 <= int(fh.status) < 500
        except Exception:  # noqa: BLE001
            return False

    def wait_until_healthy(self) -> bool:
        deadline = time.time() + self.recovery_wait
        while time.time() < deadline:
            if self.healthy():
                self.recoveries += 1
                return True
            time.sleep(self.poll_interval)
        return False

    # -- request --------------------------------------------------------
    def _once(self, method: str, path: str, query: Optional[Dict[str, Any]],
              body: Any, has_body: bool) -> Response:
        url = self.base_url + path
        qs = _encode_query(query)
        if qs:
            url += ("&" if "?" in url else "?") + qs
        data = None
        headers = dict(self.headers)
        if has_body:
            data = json.dumps(body if body is not None else {}).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as fh:
                raw = fh.read()
                elapsed = (time.time() - started) * 1000.0
                return Response(int(fh.status), _maybe_json(raw), None, elapsed, len(raw))
        except urllib.error.HTTPError as exc:  # a real HTTP answer, just not 2xx
            raw = exc.read() or b""
            elapsed = (time.time() - started) * 1000.0
            return Response(int(exc.code), _maybe_json(raw), None, elapsed, len(raw),
                            detail=str(exc.reason))
        except urllib.error.URLError as exc:
            elapsed = (time.time() - started) * 1000.0
            reason = str(getattr(exc, "reason", exc))
            kind = ERROR_TIMEOUT if "timed out" in reason.lower() else ERROR_UNREACHABLE
            return Response(None, None, kind, elapsed, 0, detail=reason)
        except TimeoutError as exc:
            elapsed = (time.time() - started) * 1000.0
            return Response(None, None, ERROR_TIMEOUT, elapsed, 0, detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.time() - started) * 1000.0
            return Response(None, None, ERROR_EXCEPTION, elapsed, 0,
                            detail="%s: %s" % (type(exc).__name__, exc))

    def request(self, method: str, path: str, query: Optional[Dict[str, Any]] = None,
                body: Any = None, has_body: bool = False) -> Response:
        response = self._once(method, path, query, body, has_body)
        attempts = 0
        while (not response.ok) and attempts < self.retries:
            attempts += 1
            if response.error in (ERROR_UNREACHABLE, ERROR_TIMEOUT) and self.health_path:
                if not self.wait_until_healthy():
                    break
            response = self._once(method, path, query, body, has_body)
        return response


class ASGITarget:
    """In-process ASGI target (no sockets)."""

    def __init__(self, app: Any, timeout: float = 60.0,
                 headers: Optional[Dict[str, str]] = None) -> None:
        # Starlette's TestClient is the synchronous ASGI driver (httpx's
        # ASGITransport is async-only).  ``raise_server_exceptions=False`` keeps
        # a 500 as a *response* instead of an exception: a contract run must
        # record server errors, not die on them.
        from starlette.testclient import TestClient

        self._client = TestClient(app, raise_server_exceptions=False,
                                  follow_redirects=False)
        self._timeout = timeout
        self.headers = {"Content-Type": "application/json", "Accept": "application/json",
                        **(headers or {})}
        self.recoveries = 0

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    def request(self, method: str, path: str, query: Optional[Dict[str, Any]] = None,
                body: Any = None, has_body: bool = False) -> Response:
        params = {k: v for k, v in (query or {}).items() if v is not None}
        started = time.time()
        try:
            kwargs: Dict[str, Any] = {"params": params, "headers": self.headers}
            if has_body:
                kwargs["content"] = json.dumps(body if body is not None else {}).encode()
            raw = self._client.request(method.upper(), path, **kwargs)
            elapsed = (time.time() - started) * 1000.0
            return Response(int(raw.status_code), _maybe_json(raw.content), None, elapsed,
                            len(raw.content))
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.time() - started) * 1000.0
            return Response(None, None, ERROR_EXCEPTION, elapsed, 0,
                            detail="%s: %s" % (type(exc).__name__, exc))


class UnreachableTarget:
    """Deterministic negative control: every request reports UNREACHABLE.

    Using a stub instead of a closed port keeps criterion (f) hermetic (no
    dependence on the host's firewall state) and lets the degradation path be
    tested in CI.
    """

    recoveries = 0

    def __init__(self, detail: str = "negative control: target deliberately unreachable") -> None:
        self.detail = detail

    def request(self, method: str, path: str, query: Optional[Dict[str, Any]] = None,
                body: Any = None, has_body: bool = False) -> Response:
        return Response(None, None, ERROR_UNREACHABLE, 0.0, 0, detail=self.detail)

    def close(self) -> None:
        return None


def load_asgi_app(dotted: str) -> Tuple[Any, str]:
    """Load an ASGI application from ``module:attribute`` (data injected by caller)."""
    if ":" not in dotted:
        raise ValueError("expected 'module:attribute', got %r" % dotted)
    module_name, attr = dotted.split(":", 1)
    module = importlib.import_module(module_name)
    app = getattr(module, attr)
    if callable(app) and not hasattr(app, "router"):
        app = app()
    return app, module_name


def make_target(kind: str, **kwargs: Any) -> Any:
    if kind == "http":
        return HttpTarget(**kwargs)
    if kind == "asgi":
        return ASGITarget(**kwargs)
    raise ValueError("unknown target kind: %r" % kind)


def _maybe_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - non-JSON bodies are legitimate
        return {"__non_json__": raw[:2048].decode("utf-8", "replace")}