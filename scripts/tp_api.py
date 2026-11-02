#!/usr/bin/env python3
"""Minimal TensorPlatform API client for agent reporting.

Reads credentials from environment (never from source):
  TENSOR_API_BASE, TENSOR_AGENT_USER, TENSOR_AGENT_PASS

Usage:
  python3 scripts/tp_api.py reports [limit]
  python3 scripts/tp_api.py post_report /tmp/report.json
"""
import json
import os
import sys
import urllib.request

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER = os.environ["TENSOR_AGENT_USER"]
PASS = os.environ["TENSOR_AGENT_PASS"]


def _req(path, payload=None, token=None, method=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def login():
    d = _req("/auth/login", {"username": USER, "password": PASS})
    return d.get("access_token") or d.get("token")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "reports"
    tok = login()
    if cmd == "reports":
        lim = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        d = _req("/tensor/reports?limit=%d" % lim, token=tok)
        rs = d if isinstance(d, list) else (d.get("items") or d.get("reports") or [])
        print("n=", len(rs))
        out = []
        for r in rs:
            md = r.get("markdown") or ""
            tails = []
            for kw in ("待办", "局限", "未完成", "TODO"):
                i = md.find(kw)
                if i >= 0:
                    tails.append(md[i:i + 160].replace("\n", " "))
            print("---", r.get("id"), (r.get("title") or "")[:90])
            for t in tails[:2]:
                print("   *", t)
            out.append({"id": r.get("id"), "title": r.get("title"), "tails": tails})
        with open("/tmp/reports_scan.json", "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    elif cmd == "post_report":
        with open(sys.argv[2]) as f:
            payload = json.load(f)
        d = _req("/tensor/reports", payload, token=tok)
        print(json.dumps(d, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()