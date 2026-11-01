#!/usr/bin/env python3
"""Lightweight API probe helper (read-only): login -> list reports / missions."""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8001/api/v1").rstrip("/")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")


def req(method, path, body=None, token=None):
    url = f"{BASE}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def login():
    for path in ("auth/login", "login", "token"):
        st, d = req("POST", path, {"username": USER, "password": PASS})
        if st == 200 and isinstance(d, dict):
            tok = d.get("access_token") or d.get("token") or d.get("data", {}).get("access_token")
            if tok:
                return tok
    return None


if __name__ == "__main__":
    tok = login()
    print("TOKEN_OK" if tok else "TOKEN_FAIL")
    open("/tmp/tok.txt", "w").write(tok or "")
    if len(sys.argv) > 1 and sys.argv[1] == "reports":
        st, d = req("GET", "tensor/reports?limit=20", token=tok)
        items = d.get("items") or d.get("data") or [] if isinstance(d, dict) else []
        if isinstance(items, dict):
            items = items.get("items", [])
        for r in items[:20]:
            print(r.get("id"), "|", (r.get("title") or "")[:70], "|", (r.get("created_at") or "")[:16])