#!/usr/bin/env python3
"""Read-only probe for tp-ver-6 scan: reports digest + mission pool + prior selfstart work."""
import json
import os
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")


def call(path, method="GET", body=None, token=None):
    url = f"{BASE}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    tok = call("auth/login", "POST", {"username": USER, "password": PASS})
    token = tok.get("access_token") or (tok.get("data") or {}).get("access_token")
    out = {}
    try:
        reps = call("tensor/reports?limit=20", token=token)
        rows = reps.get("data") if isinstance(reps.get("data"), list) else reps.get("reports") or reps.get("items") or []
        out["reports"] = [
            {"id": r.get("id"), "mission_id": r.get("mission_id"), "title": (r.get("title") or "")[:90],
             "created": str(r.get("created_at"))[:19],
             "summary": (r.get("summary") or "")[:200]}
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001
        out["reports_err"] = repr(e)
    try:
        ms = call("tensor/missions?limit=40", token=token)
        rows = ms.get("data") if isinstance(ms.get("data"), list) else ms.get("missions") or ms.get("items") or []
        out["missions"] = [
            {"id": m.get("id"), "status": m.get("status"), "runner": m.get("runner"),
             "title": (m.get("title") or "")[:80]}
            for r_ in [0] for m in rows
        ]
    except Exception as e:  # noqa: BLE001
        out["missions_err"] = repr(e)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:12000])


if __name__ == "__main__":
    main()