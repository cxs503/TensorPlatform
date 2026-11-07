"""Publish the IN23-N08 report payload to the platform report API (no secrets in the repo).

Reads ``artifacts/in23_n08/report_payload.json`` (built by ``tools.worker_truth.report``) and
POSTs it to ``$TENSOR_API_BASE/tensor/reports`` after logging in with the fleet credentials
that live in the (never committed) agent env file.

    python3 -m tools.worker_truth.publish                     # publish
    python3 -m tools.worker_truth.publish --dry-run           # size check only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

AGENT_ENV = "/root/tp-agent/agent.env"
DEFAULT_PAYLOAD = "artifacts/in23_n08/report_payload.json"
MAX_BYTES = 250_000  # the report API silently drops larger payloads (KNOWN_TRAPS)


def _env(path: str = AGENT_ENV) -> dict:
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _post(url: str, body: dict, token: str | None = None) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default=DEFAULT_PAYLOAD)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--base-url", default=None)
    args = ap.parse_args(argv)

    env = _env()
    base = (args.base_url or os.environ.get("TENSOR_API_BASE") or env.get("TENSOR_API_BASE", "")).rstrip("/")
    if not base:
        print("no TENSOR_API_BASE", file=sys.stderr)
        return 2
    size = os.path.getsize(args.payload)
    if size > MAX_BYTES:
        print(f"payload {size} B exceeds the {MAX_BYTES} B report-API ceiling; shrink figures",
              file=sys.stderr)
        return 3
    with open(args.payload, encoding="utf-8") as fh:
        payload = json.load(fh)
    print(json.dumps({"payload_bytes": size,
                      "figures": [(f["name"], len(f["png_base64"])) for f in payload["figures"]]}))
    if args.dry_run:
        return 0

    user = os.environ.get("TENSOR_AGENT_USER") or env.get("TENSOR_AGENT_USER")
    password = os.environ.get("TENSOR_AGENT_PASS") or env.get("TENSOR_AGENT_PASS")
    token = None
    try:
        tok = _post(f"{base}/auth/login", {"username": user, "password": password})
        token = (tok.get("access_token") or tok.get("token")
                 or (tok.get("data") or {}).get("access_token"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        print(f"login failed: {exc.code} {exc.read()[:200]!r}", file=sys.stderr)
        return 4
    try:
        resp = _post(f"{base}/tensor/reports", payload, token)
    except urllib.error.HTTPError as exc:  # pragma: no cover
        print(f"report POST failed: {exc.code} {exc.read()[:400]!r}", file=sys.stderr)
        return 5
    print(json.dumps({"published": resp})[:400])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())