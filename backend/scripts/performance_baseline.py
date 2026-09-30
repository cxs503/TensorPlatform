#!/usr/bin/env python3
"""Low-impact HTTP performance gate for Open Digital Foundation.

Only Python's standard library is used.  Defaults are deliberately conservative:
three workers and twenty measured requests per endpoint.

Credentials are read from PERF_USERNAME/PERF_PASSWORD and are never written to
the report.  PERF_TOKEN/--token may supply the token used for protected reads,
but credentials remain required because login itself is one of the measurements.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    p95_limit_ms: float
    authenticated: bool = True
    body: dict[str, Any] | None = None


@dataclass
class Sample:
    elapsed_ms: float
    status: int
    ok: bool
    error: str | None = None


ENDPOINTS = (
    Endpoint("login", "POST", "/api/v1/auth/login", 1500, False),
    Endpoint("health", "GET", "/api/v1/health", 500, False),
    Endpoint("dashboard", "GET", "/api/v1/dashboard/statistics", 1200),
    Endpoint("models", "GET", "/api/v1/models?limit=20", 1200),
    Endpoint("deployments", "GET", "/api/v1/deployments", 1200),
)


def valid_payload(endpoint: Endpoint, payload: Any) -> bool:
    """Reject HTTP 200 degradation/error envelopes as contract failures."""
    if not isinstance(payload, dict) or payload.get("error"):
        return False
    if endpoint.name == "login":
        return isinstance(payload.get("access_token"), str) and bool(payload["access_token"])
    if endpoint.name == "health":
        return payload.get("status") == "healthy"
    if endpoint.name == "dashboard":
        return all(key in payload for key in ("projects", "models", "deployments"))
    if endpoint.name in {"models", "deployments"}:
        return isinstance(payload.get("items"), list) and isinstance(payload.get("total"), int)
    return True


def request_once(
    base_url: str,
    endpoint: Endpoint,
    token: str | None,
    username: str | None,
    password: str | None,
    timeout: float,
) -> Sample:
    url = base_url.rstrip("/") + endpoint.path
    body = endpoint.body
    if endpoint.name == "login":
        body = {"username": username, "password": password}
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if endpoint.authenticated and token:
        headers["Authorization"] = f"Bearer {token}"

    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, data=data, headers=headers, method=endpoint.method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            status = response.status
        elapsed = (time.perf_counter() - started) * 1000
        # A successful empty/non-JSON API response is a contract failure here.
        try:
            parsed = json.loads(payload)
            valid_json = valid_payload(endpoint, parsed)
        except (json.JSONDecodeError, UnicodeDecodeError):
            valid_json = False
        return Sample(elapsed, status, 200 <= status < 300 and valid_json)
    except urllib.error.HTTPError as exc:
        exc.read()
        elapsed = (time.perf_counter() - started) * 1000
        return Sample(elapsed, exc.code, False, f"HTTP {exc.code}")
    except Exception as exc:  # timeout/DNS/reset are all availability failures
        elapsed = (time.perf_counter() - started) * 1000
        return Sample(elapsed, 0, False, type(exc).__name__)


def percentile_nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def acquire_token(base_url: str, username: str, password: str, timeout: float) -> str:
    endpoint = Endpoint("login", "POST", "/api/v1/auth/login", 1500, False)
    url = base_url.rstrip("/") + endpoint.path
    data = json.dumps({"username": username, "password": password}).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("login response did not contain access_token")
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://127.0.0.1:18080"))
    parser.add_argument("--requests", type=int, default=20, help="measured requests per endpoint")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=2, help="sequential warm-up requests per endpoint")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--token", default=os.getenv("PERF_TOKEN"))
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    if not 1 <= args.concurrency <= 5:
        parser.error("--concurrency must be between 1 and 5")
    if not 5 <= args.requests <= 100:
        parser.error("--requests must be between 5 and 100")
    if not 0 <= args.warmup <= 5:
        parser.error("--warmup must be between 0 and 5")
    if not 0 <= args.max_error_rate <= 1:
        parser.error("--max-error-rate must be between 0 and 1")

    username = os.getenv("PERF_USERNAME")
    password = os.getenv("PERF_PASSWORD")
    if not username or not password:
        parser.error("set PERF_USERNAME and PERF_PASSWORD")
    token = args.token
    if not token:
        try:
            token = acquire_token(args.base_url, username, password, args.timeout)
        except Exception as exc:
            print(f"authentication setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
    results: list[dict[str, Any]] = []
    overall_pass = True
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for endpoint in ENDPOINTS:
        for _ in range(args.warmup):
            request_once(args.base_url, endpoint, token, username, password, args.timeout)

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [
                executor.submit(
                    request_once,
                    args.base_url,
                    endpoint,
                    token,
                    username,
                    password,
                    args.timeout,
                )
                for _ in range(args.requests)
            ]
            samples = [future.result() for future in futures]

        latencies = [sample.elapsed_ms for sample in samples]
        errors = sum(not sample.ok for sample in samples)
        error_rate = errors / len(samples)
        p95 = percentile_nearest_rank(latencies, 0.95)
        passed = p95 <= endpoint.p95_limit_ms and error_rate <= args.max_error_rate
        overall_pass = overall_pass and passed
        result = {
            "endpoint": endpoint.name,
            "method": endpoint.method,
            "path": endpoint.path,
            "requests": len(samples),
            "concurrency": args.concurrency,
            "min_ms": round(min(latencies), 2),
            "mean_ms": round(statistics.fmean(latencies), 2),
            "p50_ms": round(statistics.median(latencies), 2),
            "p95_ms": round(p95, 2),
            "max_ms": round(max(latencies), 2),
            "error_rate": round(error_rate, 4),
            "p95_limit_ms": endpoint.p95_limit_ms,
            "max_error_rate": args.max_error_rate,
            "passed": passed,
            "statuses": {
                str(status): sum(sample.status == status for sample in samples)
                for status in sorted({sample.status for sample in samples})
            },
        }
        results.append(result)
        verdict = "PASS" if passed else "FAIL"
        print(
            f"{verdict:4} {endpoint.name:12} p50={result['p50_ms']:8.2f} ms "
            f"p95={result['p95_ms']:8.2f}/{endpoint.p95_limit_ms:.0f} ms "
            f"errors={error_rate:.2%}"
        )

    report = {
        "started_at": started_at,
        "base_url": args.base_url,
        "requests_per_endpoint": args.requests,
        "concurrency": args.concurrency,
        "max_error_rate": args.max_error_rate,
        "passed": overall_pass,
        "results": results,
    }
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"report: {args.json_output}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
