#!/usr/bin/env python3
"""Extract 局限/待办 (limitations / next steps) lines from recent reports."""
import json
import os
import re
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")


def call(path, method="GET", body=None, token=None):
    req = urllib.request.Request(f"{BASE}/{path.lstrip('/')}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode())


def main():
    tok = call("auth/login", "POST", {"username": os.environ["TENSOR_AGENT_USER"],
                                      "password": os.environ["TENSOR_AGENT_PASS"]})
    token = tok.get("access_token") or (tok.get("data") or {}).get("access_token")
    reps = call("tensor/reports?limit=12", token=token)
    rows = reps.get("data") if isinstance(reps.get("data"), list) else reps.get("reports") or []
    for r in rows:
        md = r.get("markdown") or ""
        rid, mid = r.get("id"), r.get("mission_id")
        print(f"\n########## report {rid} (mission {mid}) {str(r.get('title'))[:80]}")
        # find limitation / todo sections
        chunks = re.split(r"\n(?=#+)", md)
        hit = [c for c in chunks if re.search(r"局限|待办|未完成|下一步|TODO|遗留", c)]
        for c in hit[:3]:
            print(c[:900])
        if not hit:
            print("[no limitation section]", (r.get("summary") or "")[:300])


if __name__ == "__main__":
    main()