#!/usr/bin/env python3
"""Publish the IN27-H7 report payload to the platform (/tensor/reports)."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")


def post(path: str, body: dict, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:  # pragma: no cover
        return exc.code, {"error": exc.read().decode()[:400]}


def main() -> int:
    payload_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/report.json"
    status, body = post("/auth/login", {"username": USER, "password": PASS})
    if status != 200:
        print("login failed", status, body)
        return 1
    token = body.get("access_token") or body.get("token") or (body.get("data") or {}).get("access_token")
    if not token:
        print("no token in login response:", list(body))
        return 1
    with open(payload_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    status, body = post("/tensor/reports", payload, token)
    print("submit:", status, json.dumps(body, ensure_ascii=False)[:500])
    if status >= 300:
        return 1
    report_id = body.get("id") or (body.get("data") or {}).get("id")
    # read back (trap: silent failures / foreign echoes)
    url = BASE + "/tensor/reports?mission_id=5308&limit=5"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        back = json.loads(resp.read().decode() or "{}")
    items = back.get("items") or back.get("data") or back
    if isinstance(items, dict):
        items = items.get("items") or []
    print("readback count:", len(items) if isinstance(items, list) else "n/a")
    if isinstance(items, list) and items:
        first = items[0]
        print("readback first:", {k: first.get(k) for k in ("id", "title", "mission_id", "created_at")})
        print("readback md len:", len(first.get("markdown") or ""), "figures:", len(first.get("figures") or []))
    print("report_id:", report_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())