#!/usr/bin/env python3
"""Post the node report to the platform (credentials from env, never on the CLI)."""

from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ["TENSOR_AGENT_USER"]
PASS = os.environ["TENSOR_AGENT_PASS"]
PAYLOAD = sys.argv[1] if len(sys.argv) > 1 else "/tmp/report_p470w2_n10.json"


def _post(path: str, body: dict, token: str | None = None) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    login = _post("/auth/login", {"username": USER, "password": PASS})
    token = login.get("access_token") or login.get("token")
    if not token:
        print("login failed:", list(login)[:5])
        return 1
    payload = json.loads(open(PAYLOAD, encoding="utf-8").read())
    result = _post("/tensor/reports", payload, token)
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("markdown", "figures")}, ensure_ascii=False)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
