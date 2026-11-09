"""Publish the m8405 report payload to the TensorPlatform reports API.

Credentials come from the environment (TENSOR_AGENT_USER / TENSOR_AGENT_PASS);
nothing is written to disk or printed.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
SECRET = os.environ.get("TENSOR_AGENT_PASS", "")


def post(path: str, body: dict, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode()[:500]}


def main() -> int:
    status, out = post("/auth/login", {"username": USER, "password": SECRET})
    token = out.get("access_token") or out.get("token") if isinstance(out, dict) else None
    print("login:", status, "token_ok" if token else out)
    if not token:
        return 1
    payload = json.loads(open("/tmp/report.json", encoding="utf-8").read())
    status, out = post("/tensor/reports", payload, token)
    print("report:", status, json.dumps(out, ensure_ascii=False)[:400])
    return 0 if status < 300 else 2


if __name__ == "__main__":
    raise SystemExit(main())
