#!/usr/bin/env python3
"""Fetch recent tensor reports (titles + 局限/待办 sections) from the tensor API.

Usage: python3 tools/tp_reports_fetch.py [limit]
Credentials come from TENSOR_AGENT_USER / TENSOR_AGENT_PASS env vars.
"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")


def _post(path, payload, token=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _get(path, token):
    req = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def login():
    d = _post(
        "/auth/login",
        {"username": os.environ["TENSOR_AGENT_USER"], "password": os.environ["TENSOR_AGENT_PASS"]},
    )
    return d.get("access_token") or d.get("token")


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    tok = login()
    d = _get(f"/tensor/reports?limit={limit}", tok)
    items = d if isinstance(d, list) else (d.get("items") or d.get("reports") or [])
    out = []
    for r in items:
        out.append(
            {
                "id": r.get("id"),
                "mission_id": r.get("mission_id"),
                "title": r.get("title"),
                "scenario_key": r.get("scenario_key"),
                "created_at": str(r.get("created_at")),
                "summary": (r.get("summary") or "")[:600],
                "tags": r.get("tags"),
            }
        )
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()