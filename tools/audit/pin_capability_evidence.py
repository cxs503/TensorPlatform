#!/usr/bin/env python3
"""Pin platform report bodies as offline, digest-verifiable evidence anchors.

Why: capability cards (`docs/capability_registry/cards/*.json`) claim a
`validation_evidence.report_id` + `report_md5`.  Before mission 1819 those two
fields were free text (`"suboff_sail"`) and a placeholder digest, so nothing in
the tree could tell a real anchor from a fabricated one, and nothing detected a
retro-edit of the platform report after the card was signed off.

This tool is the *only* writer of the evidence snapshot.  It fetches the
report bodies once, stores them verbatim plus their canonical digests, so that
`tools/audit/capability_card_evidence.py` can re-verify every anchor **offline**
and byte-exactly (CI, no :8010 needed).

Canonical form (documented contract, used by checker and cards alike):
    body   := report["markdown"] exactly as returned by GET /api/v1/tensor/reports/{id}
    bytes  := body.encode("utf-8")
    sha256 := hashlib.sha256(bytes).hexdigest()
    md5    := hashlib.md5(bytes).hexdigest()

Usage:
    TENSOR_API_BASE=... TENSOR_AGENT_USER=... TENSOR_AGENT_PASS=... \
        python3 tools/audit/pin_capability_evidence.py --ids 1375 1340 1296
    python3 tools/audit/pin_capability_evidence.py --ids 1375 --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "tests" / "fixtures" / "capability_evidence" / "reports_snapshot.json"


def digests(body: str) -> dict[str, str]:
    b = body.encode("utf-8")
    return {"body_sha256": hashlib.sha256(b).hexdigest(), "body_md5": hashlib.md5(b).hexdigest()}


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())


def login(base: str) -> str:
    r = _post(
        base + "/auth/login",
        {
            "username": os.environ["TENSOR_AGENT_USER"],
            "password": os.environ["TENSOR_AGENT_PASS"],
        },
    )
    tok = r.get("access_token") or r.get("token") or (r.get("data") or {}).get("access_token")
    if not tok:
        raise SystemExit("login failed: no access_token in response")
    return tok


def pin(base: str, ids: list[int], dry_run: bool = False) -> dict:
    tok = login(base)
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reports = {}
    for rid in ids:
        payload = _get(f"{base}/tensor/reports/{rid}", tok)
        rep = payload.get("data") or payload
        body = rep.get("markdown") or ""
        if not body:
            raise SystemExit(f"report {rid} has empty markdown; refusing to pin an empty anchor")
        rec = {
            "report_id": str(rid),
            "title": rep.get("title"),
            "mission_id": rep.get("mission_id"),
            "created_at": rep.get("created_at"),
            "fetched_at": fetched_at,
            "body": body,
        }
        rec.update(digests(body))
        reports[str(rid)] = rec
        print(f"pinned {rid}: {rec['body_sha256'][:16]}  {str(rec['title'])[:60]}")
    snap = {
        "schema": "capability-evidence-snapshot/v1",
        "generated_by": "tools/audit/pin_capability_evidence.py",
        "canonical_form": "sha256/md5 over report['markdown'].encode('utf-8')",
        "api_base": base,
        "fetched_at": fetched_at,
        "reports": reports,
    }
    if dry_run:
        print(json.dumps({k: v["body_sha256"] for k, v in reports.items()}, indent=2))
        return snap
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT.relative_to(ROOT)} ({len(reports)} anchors)")
    return snap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="+", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--api-base", default=os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010"))
    a = ap.parse_args(argv)
    pin(a.api_base.rstrip("/"), a.ids, a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())