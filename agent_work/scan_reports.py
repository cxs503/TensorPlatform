#!/usr/bin/env python3
"""Read-only scan of platform reports to find recent limitations/todos (self-start scan)."""
import json
import os
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")


def post(path, body, token=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get(path, token):
    req = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    d = post("/auth/login", {"username": USER, "password": PASS})
    tok = d.get("access_token") or d.get("data", {}).get("access_token")
    with open("/tmp/tok.txt", "w") as f:
        f.write(tok or "")
    rep = get("/tensor/reports?limit=25", tok)
    items = rep.get("data") or rep.get("items") or rep
    if isinstance(items, dict):
        items = items.get("items", [])
    out = []
    for r in items[:25]:
        lim = ""
        md = r.get("markdown") or ""
        for key in ("局限", "待办", "注意点"):
            i = md.find(key)
            if i >= 0:
                lim = md[i:i + 400].replace("\n", " ")
                break
        out.append({"id": r.get("id"), "mission_id": r.get("mission_id"),
                    "title": r.get("title"), "tags": r.get("tags"),
                    "scenario_key": r.get("scenario_key"), "lim": lim})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()