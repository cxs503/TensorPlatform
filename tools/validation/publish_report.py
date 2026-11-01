#!/usr/bin/env python3
"""Publish the mission report to the platform reports API (reads creds from env)."""
import json
import os
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")


def post(url, body, token=None):
    hdr = {"Content-Type": "application/json"}
    if token:
        hdr["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


tok = post(BASE + "/auth/login",
           {"username": os.environ["TENSOR_AGENT_USER"], "password": os.environ["TENSOR_AGENT_PASS"]})
token = tok.get("access_token") or tok.get("token")
print("token_len", len(token or ""))
res = post(BASE + "/tensor/reports", json.load(open("/tmp/report.json")), token)
print(json.dumps({k: v for k, v in res.items() if k != "markdown"}, ensure_ascii=False)[:900])