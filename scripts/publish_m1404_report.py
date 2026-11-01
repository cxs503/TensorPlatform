#!/usr/bin/env python3
"""Publish the mission report to the Tensor platform (credentials from env only).

Kept inside the worktree (scripts/) because the workspace tool may only write
inside the repository.  Reads the report payload from /tmp/report.json.
"""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
PASS = os.environ["TENSOR_AGENT_PASS"]


def post(url, payload, token=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, r.read().decode()[:800]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:800]
    except Exception as e:  # noqa: BLE001
        return None, repr(e)


def main():
    token = None
    for path in ("/auth/login", "/tensor/auth/login"):
        st, body = post(BASE + path, {"username": USER, "password": PASS})
        try:
            doc = json.loads(body)
            token = doc.get("access_token") or doc.get("token") or (doc.get("data") or {}).get("access_token")
        except Exception:  # noqa: BLE001
            token = None
        print("login", path, st, "token" if token else "no-token")
        if token:
            break
    payload = json.load(open("/tmp/report.json"))
    st, body = post(BASE + "/tensor/reports", payload, token)
    print("report", st)
    print(body[:600])


if __name__ == "__main__":
    main()