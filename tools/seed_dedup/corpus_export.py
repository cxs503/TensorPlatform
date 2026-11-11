"""Freeze a *node corpus* snapshot out of the campaign API (dedup ledger input).

The seeding dedup gate is only as good as the ledger it searches: a candidate
seed must be compared against **historical + in-flight nodes**, not against a
synthetic toy list.  This module pulls those nodes from the platform API and
writes an immutable snapshot with provenance so that every similarity number a
later report quotes can be traced back to one file hash.

Zero subject-matter semantics: the module knows HTTP shapes (``plans``,
``nodes``, ``title``/``goal``), never what the text means.

Usage::

    # token from env (TENSOR_TOKEN) or a login round-trip
    export TENSOR_API_BASE=http://127.0.0.1:8010/api/v1
    python -m tools.seed_dedup.corpus_export --out data/seed_dedup/corpus/nodes.json

The snapshot window is deterministic: plans are walked newest-first, nodes
inside a plan ordered by id, and the first ``--limit`` nodes are kept.  The same
platform state therefore always yields byte-identical output.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence

SCHEMA = "seed-corpus/v1"
DEFAULT_LIMIT = 1888
DONE_STATES = frozenset({"done", "completed", "verified", "passed", "success", "succeeded"})


# ---------------------------------------------------------------- transport
def _request(url: str, token: str, *, timeout: float = 20.0, attempts: int = 4) -> Any:
    """GET with bounded retries: a transient timeout must not silently drop a plan."""
    last: Optional[BaseException] = None
    for i in range(attempts):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                return json.load(fh)
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                body = ""
                try:
                    body = exc.read().decode("utf-8", "replace")[:200]
                except Exception:  # pragma: no cover - body is best effort
                    pass
                raise RuntimeError(f"GET {url} -> HTTP {exc.code}: {body}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            last = exc
        time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"GET {url} failed after {attempts} attempts: {last}")


def login(base: str, user: str, password: str) -> str:
    """Exchange credentials for an access token (no token in argv, ever)."""
    body = json.dumps({"username": user, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/auth/login", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20.0) as fh:
        payload = json.load(fh)
    for key in ("access_token", "token"):
        if payload.get(key):
            return str(payload[key])
    raise RuntimeError("login response carried no access token")


def list_plans(base: str, token: str, *, page: int = 100) -> List[Dict[str, Any]]:
    url = f"{base.rstrip('/')}/tensor/campaigns/plans?{urllib.parse.urlencode({'limit': page})}"
    return list((_request(url, token) or {}).get("items") or [])


def list_nodes(base: str, token: str, plan_id: Any) -> List[Dict[str, Any]]:
    url = f"{base.rstrip('/')}/tensor/campaigns/plans/{plan_id}/nodes"
    try:
        return list((_request(url, token) or {}).get("items") or [])
    except urllib.error.HTTPError:
        return []


def get_plan(base: str, token: str, plan_id: Any) -> Optional[Dict[str, Any]]:
    url = f"{base.rstrip('/')}/tensor/campaigns/plans/{plan_id}"
    try:
        return _request(url, token) or {}
    except urllib.error.HTTPError:
        return None


def walk_plan_ids(
    base: str,
    token: str,
    start_id: int,
    *,
    budget: int,
    threads: int = 16,
    batch: int = 64,
) -> List[int]:
    """Probe plan ids ``start_id-1 .. start_id-budget`` and return the existing ones.

    The list endpoint is paged/capped, so older plans are only reachable by id.
    Probing is deterministic (descending ids, fixed batch size) and misses are
    silent, which keeps the snapshot window reproducible.
    """
    found: List[int] = []
    low = max(1, start_id - budget)
    ids = list(range(start_id - 1, low - 1, -1))
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        for chunk_start in range(0, len(ids), batch):
            chunk = ids[chunk_start:chunk_start + batch]
            hits = list(pool.map(lambda pid: (pid, get_plan(base, token, pid)), chunk))
            found.extend(pid for pid, plan in hits if plan is not None)
    return sorted(found, reverse=True)


# ---------------------------------------------------------------- shaping
def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _status(raw: Any) -> str:
    return "done" if str(raw or "").strip().lower() in DONE_STATES else "inflight"


def shape_node(node: Dict[str, Any], plan_id: Any, *, goal_chars: int = 800) -> Dict[str, Any]:
    """Project one API node onto the fields the gate actually reads."""
    meta = node.get("metadata") or {}
    goal = str(meta.get("goal") or "")
    out: Dict[str, Any] = {
        "source": "node",
        "node_id": f"p{plan_id}:{node.get('id')}",
        "title": str(node.get("name") or ""),
        "goal": goal[:goal_chars],
        "status": _status(node.get("status")),
        "plan_id": plan_id,
        "ext_id": str(meta.get("ext_id") or ""),
        "repo": str(meta.get("repo") or ""),
        "proposal_src": _as_list(meta.get("proposal_src")),
    }
    if len(goal) > goal_chars:
        out["goal_truncated_at"] = goal_chars
    return out


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def corpus_digest(nodes: Sequence[Dict[str, Any]]) -> str:
    """sha256 over the canonical node list (order-sensitive)."""
    return hashlib.sha256(canonical_json(list(nodes)).encode("utf-8")).hexdigest()


def list_missions(base: str, token: str, *, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    url = (
        f"{base.rstrip('/')}/tensor/missions?"
        f"{urllib.parse.urlencode({'limit': limit, 'offset': offset})}"
    )
    return _request(url, token) or {}


def shape_mission(mission: Dict[str, Any], *, goal_chars: int = 800, title_chars: int = 120) -> Dict[str, Any]:
    """Project one mission (a *seed text*) onto the ledger schema.

    A mission carries no title, so the first line of its goal — where these
    task books put their heading — becomes the title.  The projection is
    lossless for the gate: ``title + goal`` is exactly the text the gate scores.
    """
    goal = str(mission.get("goal") or "")
    head = goal.strip().splitlines()[0] if goal.strip() else ""
    out: Dict[str, Any] = {
        "source": "mission",
        "node_id": f"m{mission.get('id')}",
        "title": head[:title_chars],
        "goal": goal[:goal_chars],
        "status": _status(mission.get("status")),
        "plan_id": None,
        "ext_id": "",
        "repo": str(mission.get("repo") or ""),
        "proposal_src": [],
        "created_at": str(mission.get("created_at") or ""),
    }
    if len(goal) > goal_chars:
        out["goal_truncated_at"] = goal_chars
    return out


# ---------------------------------------------------------------- build
def build_corpus(
    base: str,
    token: str,
    *,
    limit: int = DEFAULT_LIMIT,
    page: int = 100,
    threads: int = 8,
    goal_chars: int = 800,
    max_plans: Optional[int] = None,
    probe_budget: int = 2000,
    include_missions: bool = True,
) -> Dict[str, Any]:
    """Walk plans newest-first until ``limit`` nodes are collected."""
    plans = sorted(
        [p for p in list_plans(base, token, page=page) if p.get("id") is not None],
        key=lambda p: int(p["id"]),
        reverse=True,
    )
    if max_plans is not None:
        plans = plans[:max_plans]
    plan_ids = [int(p["id"]) for p in plans]
    if probe_budget > 0 and plan_ids:
        # the list endpoint is capped, so reach older plans by id
        plan_ids = plan_ids + walk_plan_ids(
            base, token, min(plan_ids),
            budget=probe_budget, threads=max(threads, 6),
        )
        plan_ids = sorted(dict.fromkeys(plan_ids), reverse=True)

    nodes: List[Dict[str, Any]] = []
    visited = 0
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        for plan_id, items in zip(plan_ids, pool.map(lambda pid: list_nodes(base, token, pid), plan_ids)):
            visited += 1
            for node in sorted(items, key=lambda n: int(n.get("id") or 0)):
                nodes.append(shape_node(node, plan_id, goal_chars=goal_chars))
                if len(nodes) >= limit:
                    break
            if len(nodes) >= limit:
                break

    n_from_nodes = len(nodes)
    # the plans endpoint exposes fewer nodes than the window, so fill the rest of
    # the window with the newest *mission seeds* (a mission goal is the text a
    # seed is adjudicated on, which makes it a legitimate ledger entry)
    mission_total = 0
    if include_missions and len(nodes) < limit:
        offset = 0
        while len(nodes) < limit:
            # NB: do not reuse the ``page`` size parameter as the response name:
            # it would be passed back as ``limit`` on the next iteration
            chunk_resp = list_missions(base, token, limit=page, offset=offset)
            items = list(chunk_resp.get("items") or [])
            mission_total = int(chunk_resp.get("total") or mission_total or 0)
            if not items:
                break
            for mission in sorted(items, key=lambda m: int(m.get("id") or 0), reverse=True):
                nodes.append(shape_mission(mission, goal_chars=goal_chars))
                if len(nodes) >= limit:
                    break
            offset += len(items)
            if mission_total and offset >= mission_total:
                break

    truncated = len(nodes) > limit
    nodes = nodes[:limit]
    ids = [n["node_id"] for n in nodes]
    if len(set(ids)) != len(ids):
        raise RuntimeError("duplicate node_id in snapshot: the window rule is not a set")
    return {
        "schema": SCHEMA,
        "window_rule": (
            "plans walked newest-first (plan id desc); nodes inside a plan ordered by node id asc; "
            f"first {limit} nodes kept"
        ),
        "limit": limit,
        "plans_available": len(plan_ids),
        "probe_budget": probe_budget,
        "sources": {
            "node": n_from_nodes,
            "mission": len(nodes) - n_from_nodes,
        },
        "mission_total": mission_total,
        "plans_visited": visited,
        "n_nodes": len(nodes),
        "limit_reached": truncated or len(nodes) == limit,
        "goal_chars": goal_chars,
        "digest": {"alg": "sha256", "scope": "canonical_json_of_nodes", "sha256": corpus_digest(nodes)},
        "nodes": nodes,
    }


def provenance(corpus: Dict[str, Any], *, base: str, source: str = "campaign-api") -> Dict[str, Any]:
    """Provenance record written next to the snapshot (never inside it)."""
    return {
        "schema": "seed-corpus-provenance/v1",
        "source": source,
        "endpoint": base.rstrip("/"),
        "snapshot": corpus["digest"],
        "n_nodes": corpus["n_nodes"],
        "window_rule": corpus["window_rule"],
        "plans_available": corpus["plans_available"],
        "plans_visited": corpus["plans_visited"],
        "limit_reached": corpus["limit_reached"],
        "goal_chars": corpus["goal_chars"],
        "snapshot_file_sha256": hashlib.sha256(canonical_json(corpus).encode("utf-8")).hexdigest(),
    }


def write_snapshot(corpus: Dict[str, Any], out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    text = json.dumps(corpus, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1"))
    ap.add_argument("--out", default="data/seed_dedup/corpus/nodes.json")
    ap.add_argument("--provenance-out", default=None)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--page", type=int, default=100)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--max-plans", type=int, default=None)
    ap.add_argument("--probe-budget", type=int, default=2000,
                    help="how many plan ids below the newest to probe (0 = paged list only)")
    ap.add_argument("--no-missions", action="store_true",
                    help="do not fill the window with mission seeds")
    args = ap.parse_args(argv)

    token = os.environ.get("TENSOR_TOKEN", "").strip()
    if not token:
        user = os.environ.get("TENSOR_AGENT_USER", "")
        password = os.environ.get("TENSOR_AGENT_PASS", "")
        if not (user and password):
            print("[corpus] no TENSOR_TOKEN and no credentials in env", file=sys.stderr)
            return 2
        token = login(args.base, user, password)

    corpus = build_corpus(
        args.base,
        token,
        limit=args.limit,
        page=args.page,
        threads=args.threads,
        max_plans=args.max_plans,
        probe_budget=args.probe_budget,
        include_missions=not args.no_missions,
    )
    file_sha = write_snapshot(corpus, args.out)
    prov = provenance(corpus, base=args.base)
    prov["file_sha256"] = file_sha
    prov_out = args.provenance_out or (os.path.splitext(args.out)[0] + ".provenance.json")
    with open(prov_out, "w", encoding="utf-8") as fh:
        json.dump(prov, fh, ensure_ascii=False, sort_keys=True, indent=1)
        fh.write("\n")
    print(
        f"[corpus] {corpus['n_nodes']} nodes / {corpus['plans_visited']} plans -> {args.out} "
        f"digest={corpus['digest']['sha256'][:16]} file_sha={file_sha[:16]}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())