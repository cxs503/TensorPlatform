"""Fetch terminal mission records (read-only) for the delivery-semantics experiment.

Standalone CLI; no domain semantics. Writes JSONL + sha256 to the given out dir.

Usage:
    python tools/p92w3_n10/fetch_missions.py --status succeeded --limit 800 \
        --out runs/p92w3-n10/corpus
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import urllib.error
import urllib.request


def _base() -> str:
    return os.environ["TENSOR_API_BASE"].rstrip("/")


def login() -> str:
    body = json.dumps({
        "username": os.environ["TENSOR_AGENT_USER"],
        "password": os.environ["TENSOR_AGENT_PASS"],
    }).encode()
    req = urllib.request.Request(_base() + "/auth/login", data=body,
                                headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    for key in ("access_token", "token", "accessToken"):
        if isinstance(data, dict) and data.get(key):
            return data[key]
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        for key in ("access_token", "token"):
            if data["data"].get(key):
                return data["data"][key]
    raise SystemExit(f"login payload without token: {list(data)[:8]}")


def get(token: str, path: str) -> dict:
    req = urllib.request.Request(_base() + path, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code} on {path}: {exc.read().decode()[:300]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="succeeded")
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--page", type=int, default=200)
    ap.add_argument("--out", default="runs/p92w3-n10/corpus")
    args = ap.parse_args()

    token = login()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    offset = 0
    while len(records) < args.limit:
        page = min(args.page, args.limit - len(records))
        payload = get(token, f"/tensor/missions?status={args.status}&limit={page}&offset={offset}")
        items = payload.get("items") or payload.get("missions") or payload.get("data") or []
        if isinstance(items, dict):
            items = items.get("items") or items.get("missions") or []
        if not items:
            break
        records.extend(items)
        offset += len(items)
        print(f"fetched {len(records)} (offset={offset})", flush=True)

    blob = json.dumps(records, ensure_ascii=False, sort_keys=True).encode()
    (out / f"missions_{args.status}.json").write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    (out / f"missions_{args.status}.sha256").write_text(digest + "\n", encoding="utf-8")
    print(json.dumps({"n": len(records), "sha256": digest, "path": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()