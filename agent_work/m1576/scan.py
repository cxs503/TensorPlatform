"""Read-only helper: login + fetch recent tensor reports (limitations sections)."""
import json
import os
import urllib.request

base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ["TENSOR_AGENT_USER"]
pwd = os.environ["TENSOR_AGENT_PASS"]


def call(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req, timeout=30))


login = call("/auth/login", {"username": user, "password": pwd})
tok = login.get("access_token") or login.get("token") or ""
open("/tmp/tok", "w").write(tok)
print("token_len", len(tok))
reps = call("/tensor/reports?limit=8", token=tok)
items = reps.get("items", []) if isinstance(reps, dict) else reps
print("n_reports", len(items))
for x in items:
    print("-", x.get("id"), "|", (x.get("title") or "")[:90], "| m", x.get("mission_id"))