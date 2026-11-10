"""Publish the m9723 report to the platform report API.

All numbers and figure captions are read from the committed artifacts
(``artifacts/m9723/results.json`` + ``acceptance.json``) and the markdown report
is read from ``docs/``; nothing is retyped, so the published report cannot drift
from the audited artifacts.

Usage::

    TENSOR_AGENT_USER=... TENSOR_AGENT_PASS=... \\
    python -m tools.multisolver_ckpt.post_report [--dry-run]
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "m9723"
DOCS = ROOT / "docs" / "MULTISOLVER_COUPLING_CHECKPOINT_REPORT.md"
MISSION_ID = 9723


def _login(base: str) -> str:
    user = os.environ.get("TENSOR_AGENT_USER")
    password = os.environ.get("TENSOR_AGENT_PASS")
    if not user or not password:
        raise SystemExit("TENSOR_AGENT_USER / TENSOR_AGENT_PASS must be set")
    req = urllib.request.Request(
        f"{base}/auth/login",
        data=json.dumps({"username": user, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    token = payload.get("access_token") or payload.get("token")
    if not token:
        raise SystemExit(f"login returned no token: {sorted(payload)}")
    return token


def _figures() -> List[Dict[str, str]]:
    data = json.loads((ART / "results.json").read_text(encoding="utf-8"))
    out = []
    for fig in data.get("figures", []):
        path = ROOT / fig["path"]
        if not path.is_file():
            continue
        out.append({
            "name": path.stem,
            "caption": fig["caption"],
            "png_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        })
    return out


def build_payload() -> Dict[str, Any]:
    acceptance = json.loads((ART / "acceptance.json").read_text(encoding="utf-8"))
    results = json.loads((ART / "results.json").read_text(encoding="utf-8"))
    markdown = DOCS.read_text(encoding="utf-8")
    verdict_line = ", ".join(f"{k}={v['verdict']}" for k, v in acceptance["verdicts"].items())
    summary = (
        "Domain-neutral checkpoint / bit-level-resume layer for coupled multi-solver runs: "
        "unified state protocol, atomic zstd store with chunked sha256, energy ledger gates, "
        "coupling-instant sync policy, corruption detection + rollback. "
        f"Verdicts: {verdict_line}."
    )
    return {
        "title": "Multi-solver coupling checkpoint & bit-level reproducibility surface (m9723)",
        "summary": summary,
        "scenario_key": "platform-multisolver-checkpoint",
        "mission_id": MISSION_ID,
        "tags": ["platform", "checkpoint", "bit-level", "coupling", "energy-ledger",
                 "rollback", "m9723"],
        "markdown": markdown,
        "figures": _figures(),
        "metadata": {
            "acceptance": {k: v["verdict"] for k, v in acceptance["verdicts"].items()},
            "artifacts": {
                "results": "artifacts/m9723/results.json",
                "acceptance": "artifacts/m9723/acceptance.json",
                "pytest": "artifacts/m9723/pytest_summary.json",
                "drill_audit": "artifacts/m9723/drill/restart_audit.jsonl",
                "report_md": "docs/MULTISOLVER_COUPLING_CHECKPOINT_REPORT.md",
            },
            "host": results.get("host"),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    payload = build_payload()
    if "--dry-run" in argv:
        payload = {**payload, "figures": [{k: (v[:32] + "...") if k == "png_base64" else v
                                           for k, v in f.items()} for f in payload["figures"]]}
        print(json.dumps({k: (v if k != "markdown" else f"{len(v)} chars")
                          for k, v in payload.items()}, indent=2)[:2000])
        return 0
    token = _login(base)
    req = urllib.request.Request(
        f"{base}/tensor/reports",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode()
        print(json.dumps({"status": resp.status, "response": body[:800]}))
    except urllib.error.HTTPError as exc:
        print(json.dumps({"status": exc.code, "body": exc.read().decode()[:800]}))
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())