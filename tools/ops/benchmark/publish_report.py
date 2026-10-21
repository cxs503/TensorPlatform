#!/usr/bin/env python3
"""代发平台进展报告：publish_report.py <report.md> [tag ...]

operator.env（OPERATOR_USER/PASS）登录，POST /tensor/reports。
payload 不含 stage（^S[0-4]$ 约束）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE = "http://localhost:3000/api/v1"


def main() -> int:
    md = Path(sys.argv[1]).read_text(encoding="utf-8")
    tags = sys.argv[2:] or ["benchmark-closure"]
    u, p = os.environ["OPERATOR_USER"], os.environ["OPERATOR_PASS"]
    req = urllib.request.Request(
        BASE + "/auth/login",
        data=json.dumps({"username": u, "password": p}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        tok = json.loads(r.read())["access_token"]
    lines = md.splitlines()
    title = next(l[2:] for l in lines if l.startswith("# "))
    body_start = next(i for i, l in enumerate(lines) if l.strip() and not l.startswith("#"))
    summary = next(l for l in lines[body_start:] if l.strip())
    payload = {"title": title[:200], "summary": summary[:300],
               "scenario_key": "benchmark-closure",
               "tags": tags, "markdown": md}
    req = urllib.request.Request(
        BASE + "/tensor/reports", data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read())
    print("report id:", out.get("id"), "| title:", str(out.get("title"))[:60])
    return 0


if __name__ == "__main__":
    sys.exit(main())
