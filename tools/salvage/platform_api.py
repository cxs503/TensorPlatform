"""Minimal read-only client for the platform campaign API (stdlib urllib).

Only used to *observe* campaign state (plan + node status snapshots).  The
salvage flow never posts verdicts: a status snapshot exists solely to prove the
status invariance invariant (acceptance A3).
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
TIMEOUT = 60


class PlatformAPIError(RuntimeError):
    pass


def _request(path: str, token: str | None = None, method: str = "GET", payload: dict | None = None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        raise PlatformAPIError(f"{method} {path} -> HTTP {exc.code}: {exc.read()[:300]!r}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network dependent
        raise PlatformAPIError(f"{method} {path} -> {exc}") from exc


def login() -> str:
    user = os.environ.get("TENSOR_AGENT_USER")
    password = os.environ.get("TENSOR_AGENT_PASS")
    if not user or not password:
        raise PlatformAPIError("TENSOR_AGENT_USER/TENSOR_AGENT_PASS not set")
    data = _request("/auth/login", method="POST", payload={"username": user, "password": password})
    data = data.get("data", data)
    token = data.get("access_token") or data.get("token")
    if not token:
        raise PlatformAPIError("login returned no token")
    return token


def _unwrap(payload):
    if isinstance(payload, dict) and "items" in payload:
        return payload["items"]
    if isinstance(payload, dict) and "data" in payload:
        return _unwrap(payload["data"])
    return payload


def get_plan(token: str, plan_id: int) -> dict:
    return _unwrap(_request(f"/tensor/campaigns/plans/{plan_id}", token))


def get_nodes(token: str, plan_id: int) -> list[dict]:
    return _unwrap(_request(f"/tensor/campaigns/plans/{plan_id}/nodes", token)) or []


def get_mission(token: str, mission_id: int) -> dict:
    return _unwrap(_request(f"/tensor/missions/{mission_id}", token)) or {}


def canonical_sha256(payload: object) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def status_snapshot(plan_id: int, token: str | None = None) -> dict:
    """Node status snapshot for one plan (sorted, hash-chained)."""
    token = token or login()
    nodes = get_nodes(token, plan_id)
    rows = sorted(
        (
            {
                "node_id": n.get("id"),
                "mission_id": n.get("mission_id"),
                "status": n.get("status"),
                "node_status_updated_at": n.get("updated_at"),
            }
            for n in nodes
        ),
        key=lambda r: (str(r["node_id"])),
    )
    snap = {
        "plan_id": plan_id,
        "node_count": len(rows),
        "nodes": rows,
        "source": "api:/tensor/campaigns/plans/{plan_id}/nodes",
    }
    snap["status_sha256"] = canonical_sha256([(r["node_id"], r["status"]) for r in rows])
    return snap


def status_map(snapshot: dict) -> dict[str, str]:
    return {str(r["mission_id"]): r["status"] for r in snapshot.get("nodes", []) if r.get("mission_id")}


def diff_snapshots(before: dict, after: dict) -> dict:
    """Structural diff of two status snapshots (empty ``changes`` == invariant holds)."""
    b = {r["node_id"]: r for r in before.get("nodes", [])}
    a = {r["node_id"]: r for r in after.get("nodes", [])}
    changes = []
    for node_id in sorted(set(b) | set(a), key=str):
        bs = b.get(node_id, {}).get("status")
        as_ = a.get(node_id, {}).get("status")
        if bs != as_:
            changes.append({"node_id": node_id, "before": bs, "after": as_})
    return {
        "node_count_before": len(b),
        "node_count_after": len(a),
        "status_sha256_before": before.get("status_sha256"),
        "status_sha256_after": after.get("status_sha256"),
        "changed_rows": len(changes),
        "changes": changes,
        "diff_is_zero": not changes,
    }