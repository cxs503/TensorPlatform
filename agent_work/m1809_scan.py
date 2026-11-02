#!/usr/bin/env python3
"""m1809 只读扫描：报告池局限/待办 + 提案池 + 任务池。"""
import json
import os
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")


def post(path, body, token=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get(path, token=None):
    req = urllib.request.Request(
        BASE + path,
        headers={**({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


tok = post("/auth/login", {"username": USER, "password": PASS})
tok = tok.get("access_token") or tok.get("token")
print("TOKEN_OK", bool(tok))

reps = get("/tensor/reports?limit=20", tok)
items = reps.get("items", reps) if isinstance(reps, dict) else reps
print("REPORTS", len(items))
for r in items:
    print("-", r.get("id"), "m%s" % r.get("mission_id"), (r.get("title") or "")[:70])

# 任务池：近 24h 终态 + 在飞
try:
    ms = get("/tensor/missions?limit=40", tok)
    mi = ms.get("items", ms) if isinstance(ms, dict) else ms
    print("\nMISSIONS", len(mi))
    for m in mi[:40]:
        print("-", m.get("id"), m.get("status"), (m.get("title") or m.get("goal") or "")[:60].replace("\n", " "))
except Exception as e:  # noqa
    print("missions err", e)