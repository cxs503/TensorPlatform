#!/usr/bin/env python3
"""Mission 2043 helper: platform API access (login + reports list + report post)."""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")


def req(path, method="GET", body=None, token=None):
    url = f"{BASE}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()[:500]}


def login():
    d = req("auth/login", "POST", {"username": USER, "password": PASS})
    tok = d.get("access_token") or d.get("token") or (d.get("data") or {}).get("access_token")
    if not tok:
        sys.exit(f"login failed: {str(d)[:300]}")
    return tok


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "reports":
        tok = login()
        d = req(f"tensor/reports?limit={sys.argv[2] if len(sys.argv) > 2 else 20}", token=tok)
        rows = d.get("data") if isinstance(d.get("data"), list) else d.get("items", d)
        if isinstance(rows, dict):
            rows = rows.get("items", [])
        for x in rows if isinstance(rows, list) else []:
            print(x.get("id"), "|", str(x.get("title"))[:100], "|", x.get("created_at", ""))
    elif cmd == "get":
        tok = login()
        print(json.dumps(req(sys.argv[2], token=tok), ensure_ascii=False)[:4000])
    elif cmd == "post_report":
        tok = login()
        with open(sys.argv[2]) as f:
            payload = json.load(f)
        print(json.dumps(req("tensor/reports", "POST", payload, token=tok), ensure_ascii=False)[:1500])