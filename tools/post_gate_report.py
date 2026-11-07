#!/usr/bin/env python3
"""POST 报告到平台（凭据取自环境变量，不入库）。用法: python3 tools/post_gate_report.py /tmp/report.json"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")


def call(method: str, path: str, payload=None, token: str | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    status, body = call("POST", "/auth/login", {"username": USER, "password": PASS})
    if status != 200:
        print("login failed", status, body)
        return 1
    token = body["access_token"]
    payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
    status, body = call("POST", "/tensor/reports", payload, token)
    print("post", status, json.dumps(body, ensure_ascii=False)[:400])
    return 0 if status < 300 else 2


if __name__ == "__main__":
    raise SystemExit(main())