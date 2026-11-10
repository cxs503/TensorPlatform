#!/usr/bin/env python3
"""把 N02 报告（含 3 张内嵌图）发到平台 /tensor/reports。

内容全部来自 ``artifacts/capability_ledger_auto/report_payload.json``
（由 ``tools/audit/gen_ledger_auto_n02_report.py`` 程序化生成，零手抄）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PAYLOAD = REPO_ROOT / "artifacts" / "capability_ledger_auto" / "report_payload.json"


def main() -> int:
    api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    user = os.environ.get("TENSOR_AGENT_USER", "")
    passwd = os.environ.get("TENSOR_AGENT_PASS", "")
    if not user or not passwd:
        print("missing TENSOR_AGENT_USER/PASS", file=sys.stderr)
        return 2

    login = urllib.request.Request(
        f"{api_base}/auth/login",
        data=json.dumps({"username": user, "password": passwd}).encode(),
        headers={"Content-Type": "application/json"},
    )
    token = json.loads(urllib.request.urlopen(login, timeout=15).read())["access_token"]

    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    req = urllib.request.Request(
        f"{api_base}/tensor/reports",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        body = json.loads(resp.read())
        print(json.dumps({"status": resp.status, "id": body.get("id")}, ensure_ascii=False))
        return 0
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read()[:500]!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())