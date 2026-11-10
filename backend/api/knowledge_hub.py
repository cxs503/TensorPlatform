"""Tensor console: knowledge column (知识专栏) — read-only view over the
TensorKnowledge Engine (KE) HTTP API.

Single contract source
----------------------
Every knowledge number rendered in this column is fetched from the KE API
(``KE_API_BASE``, default ``http://127.0.0.1:8021``). This module never opens the
TensorKnowledge store (SQLite/Postgres) and never parses the TK knowledge package
to obtain object data — the HTTP API is the only contract for knowledge objects.

The single local-file surface is the *evidence source reader*
(``GET /source/{path}``): it serves a read-only, line-addressed window of a file
inside the TensorKnowledge repository, so a claim → evidence → source:line chain
can be opened in the browser. It reuses the report-figure direct-read pattern
(``/tensor/reports/{id}/figures/{name}``): the auth exemption lives in
``middleware/auth_guard.py`` (middleware layer — a route-level exemption alone
would still be rejected by the guard), while this module keeps the path fence
(resolved path must stay under the TK repo root) and the extension allowlist.

Degradation
-----------
When the engine is unreachable every endpoint answers ``available: false`` with a
``reason`` instead of raising, so the console renders an 「引擎离线」placeholder —
never a blank page and never a fabricated number.

Draft redaction
---------------
Objects that are not published (``publication_status != "published"``) carry
status metadata only: statement / quote / snippet / rationale / doc bodies are
stripped server-side and flagged with ``content_hidden: true``. Hiding only in
the view would leak un-reviewed text through the API, so the gate lives here and
is covered by tests.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import require_auth

router = APIRouter(prefix="/tensor/knowledge", tags=["科研运行台·知识专栏"])

ENGINE_DEFAULT = "http://127.0.0.1:8021"
TK_DIR_DEFAULT = "/root/tensor-ecosystem/TensorKnowledge"
#: 导出三件套 manifest 目录（KE CLI 的产物），可用 KE_ASSET_DIR 覆盖。
ASSET_DIR_DEFAULT = "/root/tensor-ecosystem/TensorKnowledge/artifacts/ke_export"

#: 未评估/未判定态不算「有证据」——漏斗第二档的口径写死在常量里，便于审计。
_ASSESSED_EXCLUDE = {"unassessed", "not-assessed", ""}
#: 计入「被引」的边类型（refutes 单独成榜）。
_CITING_RELATIONS = ("supports", "qualifies", "contextualizes", "mentions", "derived-from")

_SOURCE_SUFFIXES = {
    ".json", ".jsonl", ".md", ".py", ".txt", ".yaml", ".yml",
    ".toml", ".csv", ".cfg", ".ini", ".rst", ".tsv",
}
_SOURCE_MAX_BYTES = 2 * 1024 * 1024
_CONTENT_FIELDS = (
    "statement", "snippet", "quote", "notes", "doc",
    "rationale", "findings", "description", "summary", "content",
)


# ---------------------------------------------------------------------------
# engine plumbing
# ---------------------------------------------------------------------------
class EngineOffline(RuntimeError):
    """KE API not reachable (connection refused / timeout / DNS)."""


class EngineError(RuntimeError):
    """KE API answered with a non-2xx status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def _engine_base() -> str:
    return os.environ.get("KE_API_BASE", ENGINE_DEFAULT).rstrip("/")


def _engine_timeout() -> float:
    try:
        return max(0.2, float(os.environ.get("KE_API_TIMEOUT", "4")))
    except ValueError:
        return 4.0


def _ke_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET one KE API endpoint. Raises EngineOffline / EngineError."""
    url = _engine_base() + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean, doseq=True)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_engine_timeout()) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:  # engine up, request rejected
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # pragma: no cover - body already consumed
            pass
        raise EngineError(exc.code, detail or exc.reason or "error") from exc
    except (urllib.error.URLError, OSError) as exc:  # refused / timeout / DNS
        raise EngineOffline(str(getattr(exc, "reason", exc))) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise EngineError(502, f"non-JSON response from {path}") from exc


def _offline_payload(exc: Exception) -> dict[str, Any]:
    """Uniform degradation envelope: no numbers, a reason, a timestamp."""
    return {
        "available": False,
        "reason": "engine_offline" if isinstance(exc, EngineOffline) else "engine_error",
        "detail": str(exc)[:300],
        "engine": {"base": _engine_base(), "reachable": False},
        "checked_at": _now(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# short-lived cache (three tabs poll; identical upstream reads are cheap to share)
# ---------------------------------------------------------------------------
_CACHE: dict[str, tuple[float, Any]] = {}


def _cache_ttl() -> float:
    try:
        return max(0.0, float(os.environ.get("KE_CACHE_TTL", "15")))
    except ValueError:
        return 15.0


def _cached(key: str, producer) -> Any:
    ttl = _cache_ttl()
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    _CACHE[key] = (now, value)
    return value


def reset_cache() -> None:
    """Tests / manual refresh hook."""
    _CACHE.clear()


# ---------------------------------------------------------------------------
# redaction (draft must not leak its body through the API)
# ---------------------------------------------------------------------------
def is_published(obj: dict[str, Any]) -> bool:
    return obj.get("publication_status") == "published"


def redact(obj: dict[str, Any]) -> dict[str, Any]:
    """Strip content-bearing fields from an unpublished object."""
    if not isinstance(obj, dict) or is_published(obj):
        return obj
    out = {k: v for k, v in obj.items() if k not in _CONTENT_FIELDS}
    if any(k in obj for k in _CONTENT_FIELDS):
        out["content_hidden"] = True
    out.setdefault("publication_status", obj.get("publication_status"))
    return out


def _redact_link(link: dict[str, Any]) -> dict[str, Any]:
    """Evidence link: keep source/locator metadata, drop the verbatim quote."""
    out = dict(link)
    locator = out.get("locator")
    if isinstance(locator, dict) and not is_published(out):
        keep = {k: v for k, v in locator.items() if k != "quote"}
        if "quote" in locator:
            keep["quote_hidden"] = True
        out["locator"] = keep
    for field in ("statement", "snippet", "quote"):
        if field in out and not is_published(out):
            out.pop(field, None)
            out["content_hidden"] = True
    # Evidence records carry their own status axis; a published claim does not
    # publish the evidence quoting it.
    if out.get("evidence_status") is None and link.get("assessment_status"):
        out["evidence_status"] = link.get("assessment_status")
    return out


def _statement_of(obj: dict[str, Any]) -> str | None:
    """Statement from a /ko list item (``doc`` present only with include_doc)."""
    doc = obj.get("doc") if isinstance(obj.get("doc"), dict) else {}
    return doc.get("statement") or obj.get("statement")


# ---------------------------------------------------------------------------
# KE reads (all through the HTTP API)
# ---------------------------------------------------------------------------
def _ko_objects(kind: str | None = None, include_doc: bool = False,
                page: int = 2000) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        res = _ke_get("/ko", {"kind": kind, "limit": page, "offset": offset,
                              "include_doc": "true" if include_doc else "false"})
        batch = res.get("items") or []
        items.extend(batch)
        total = int(res.get("total") or 0)
        offset += len(batch)
        if not batch or offset >= total:
            break
    return items


def _edges(relation: str) -> list[dict[str, Any]]:
    return (_ke_get("/query/by-evidence-relation", {"relation": relation}) or {}).get("items") or []


def _stats() -> dict[str, Any]:
    return _ke_get("/query/stats") or {}


def _engine_health() -> dict[str, Any]:
    return _ke_get("/healthz") or {}


# ---------------------------------------------------------------------------
# aggregation helpers
# ---------------------------------------------------------------------------
def _funnel(stats: dict[str, Any]) -> list[dict[str, Any]]:
    produced = int(stats.get("objects") or 0)
    evidence_dist = stats.get("evidence_status") or {}
    review_dist = stats.get("review_status") or {}
    pub_dist = stats.get("publication_status") or {}
    evidenced = sum(int(v or 0) for k, v in evidence_dist.items() if k not in _ASSESSED_EXCLUDE)
    reviewed = sum(int(v or 0) for k, v in review_dist.items() if k != "unreviewed")
    published = int(pub_dist.get("published") or 0)
    return [
        {"key": "produced", "label": "生产", "count": produced,
         "hint": "已入库知识对象（四契约记录总数）"},
        {"key": "evidenced", "label": "证据", "count": evidenced,
         "hint": "证据态已判定（扣除 unassessed / not-assessed）"},
        {"key": "reviewed", "label": "评审", "count": reviewed,
         "hint": "已进入评审态（review_status ≠ unreviewed）"},
        {"key": "published", "label": "发布", "count": published,
         "hint": "publication_status = published"},
    ]


def _domain_rows(objects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    agg: dict[str, dict[str, int]] = {}
    for obj in objects:
        domain = obj.get("domain") or "(未标注域)"
        slot = agg.setdefault(domain, {"count": 0, "claim": 0, "evidence": 0, "provenance": 0, "review": 0})
        slot["count"] += 1
        kind = obj.get("kind")
        if kind in slot:
            slot[kind] += 1
    rows = [{"domain": d, **v} for d, v in agg.items()]
    rows.sort(key=lambda r: (-r["count"], r["domain"]))
    return rows


def _growth_rows(objects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for obj in objects:
        stamp = str(obj.get("created_at") or "")[:10]
        if len(stamp) == 10:
            counts[stamp] = counts.get(stamp, 0) + 1
    return [{"date": d, "count": counts[d]} for d in sorted(counts)]


def _citation_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for relation in _CITING_RELATIONS:
        for edge in _edges(relation):
            cid = edge.get("claim_id")
            if cid:
                counts[cid] = counts.get(cid, 0) + 1
    return counts


def _evidence_pub_index() -> dict[str, str | None]:
    """evidence id -> publication_status (quote visibility follows the *evidence*
    record, not the claim quoting it). Best effort: engine trouble -> empty map."""
    try:
        return _cached("evidence_pub", lambda: {
            o["id"]: o.get("publication_status")
            for o in _ko_objects(kind="evidence") if o.get("id")
        })
    except (EngineOffline, EngineError):
        return {}


def _claims_index(include_doc: bool = True) -> dict[str, dict[str, Any]]:
    return {o["id"]: o for o in _ko_objects(kind="claim", include_doc=include_doc) if o.get("id")}


def _cited_board(limit: int) -> list[dict[str, Any]]:
    counts = _citation_counts()
    claims = _claims_index()
    board: list[dict[str, Any]] = []
    for cid, n in counts.items():
        claim = claims.get(cid, {"id": cid})
        row = {
            "claim_id": cid,
            "citations": n,
            "domain": claim.get("domain"),
            "evidence_status": claim.get("evidence_status"),
            "review_status": claim.get("review_status"),
            "publication_status": claim.get("publication_status"),
            "content_hidden": not is_published(claim),
        }
        if is_published(claim):
            row["statement"] = _statement_of(claim)
        board.append(row)
    board.sort(key=lambda r: (-r["citations"], r["claim_id"]))
    return board[:limit]


def _refuted_board(limit: int) -> list[dict[str, Any]]:
    claims = _claims_index()
    rows: list[dict[str, Any]] = []
    for edge in _edges("refutes"):
        cid = edge.get("claim_id")
        claim = claims.get(cid, {"id": cid})
        source = edge.get("source") or {}
        locator = edge.get("locator") or {}
        row = {
            "claim_id": cid,
            "evidence_id": edge.get("evidence_id"),
            "citation": source.get("citation"),
            "locator": locator.get("value"),
            "content_hash": locator.get("content_hash"),
            "accessed_at": source.get("accessed_at"),
            "domain": claim.get("domain"),
            "evidence_status": claim.get("evidence_status"),
            "review_status": claim.get("review_status"),
            "publication_status": claim.get("publication_status"),
            "content_hidden": not is_published(claim),
        }
        if is_published(claim):
            row["statement"] = _statement_of(claim)
        if is_published(claim) and locator.get("quote"):
            row["quote"] = locator.get("quote")
        rows.append(row)
    rows.sort(key=lambda r: (str(r.get("accessed_at") or ""), r["claim_id"]), reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
# TK local repository (evidence source reader + record locator)
# ---------------------------------------------------------------------------
def _tk_root() -> Path:
    return Path(os.environ.get("TENSOR_KNOWLEDGE_DIR", TK_DIR_DEFAULT))


def _tk_commit() -> str:
    def produce() -> str:
        try:
            out = subprocess.run(["git", "-C", str(_tk_root()), "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, timeout=10)
            return out.stdout.strip() if out.returncode == 0 else ""
        except Exception:
            return ""

    return _cached("tk_commit", produce)


def _resolve_source(rel: str) -> Path:
    """Path fence: only regular files under the TK repo, allowlisted suffixes."""
    root = _tk_root()
    if not rel or "\x00" in rel:
        raise HTTPException(400, "empty source path")
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(403, "source path escapes the knowledge repository") from None
    if not candidate.is_file():
        raise HTTPException(404, f"source file not found: {rel}")
    if candidate.suffix.lower() not in _SOURCE_SUFFIXES:
        raise HTTPException(403, f"suffix not allowlisted for direct read: {candidate.suffix}")
    if candidate.stat().st_size > _SOURCE_MAX_BYTES:
        raise HTTPException(413, "source file larger than direct-read budget")
    return candidate


_LOCATE_CACHE: dict[str, dict[str, Any] | None] = {}


_KIND_BY_PREFIX = {"tkc": "claim", "tke": "evidence", "tkp": "provenance", "tkr": "review"}


def _locate_record(ko_id: str) -> dict[str, Any] | None:
    """Find the knowledge-package JSON file + line holding one object id.

    A claim's ``evidence_refs`` also contain evidence ids, so a naive first hit
    points at the *claim* file instead of the evidence record. Files under the
    record's own kind directory (``claims/``, ``evidence/`` …) are searched first.
    """
    if ko_id in _LOCATE_CACHE:
        return _LOCATE_CACHE[ko_id]
    root = _tk_root()
    needle = f'"{ko_id}"'
    found: dict[str, Any] | None = None
    base = root / "knowledge"
    kind = _KIND_BY_PREFIX.get(ko_id.split(":", 1)[0])

    def _rank(path: Path) -> tuple[int, str]:
        if kind is None:
            return (0, str(path))
        own = {kind, kind + "s"}
        hit = any(part in own for part in path.parts)
        return (0 if hit else 1, str(path))

    if ko_id and base.is_dir():
        for path in sorted(base.rglob("*.json"), key=_rank):
            try:
                if path.stat().st_size > _SOURCE_MAX_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            idx = text.find(needle)
            if idx < 0:
                continue
            found = {
                "path": str(path.relative_to(root)),
                "line": text.count("\n", 0, idx) + 1,
                "commit": _tk_commit(),
            }
            break
    _LOCATE_CACHE[ko_id] = found
    return found


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
@router.get("/status")
def knowledge_status(_u: dict = Depends(require_auth)):
    """Cheap liveness probe used by the tab banner (never 5xx)."""
    try:
        health = _engine_health()
    except (EngineOffline, EngineError) as exc:
        return _offline_payload(exc)
    return {
        "available": True,
        "engine": {"base": _engine_base(), "reachable": True, **health},
        "checked_at": _now(),
    }


@router.get("/overview")
def knowledge_overview(_u: dict = Depends(require_auth)):
    """tab1 总览：总量 / 四态漏斗 / 领域分布 / 按日增长 / 活跃命题榜。"""
    try:
        health = _engine_health()
        stats = _cached("stats", _stats)
        objects = _cached("objects_all", lambda: _ko_objects(include_doc=False))
        cited = _cached("cited", lambda: _cited_board(10))
        refuted = _cached("refuted", lambda: _refuted_board(10))
    except (EngineOffline, EngineError) as exc:
        return _offline_payload(exc)
    return {
        "available": True,
        "engine": {"base": _engine_base(), "reachable": True, **health},
        "generated_at": _now(),
        "stats": {
            "objects": stats.get("objects"),
            "by_kind": stats.get("by_kind") or {},
            "edges": stats.get("edges"),
            "review_events": stats.get("review_events"),
            "production_status": stats.get("production_status") or {},
            "evidence_status": stats.get("evidence_status") or {},
            "review_status": stats.get("review_status") or {},
            "publication_status": stats.get("publication_status") or {},
        },
        "funnel": _funnel(stats),
        "domains": _domain_rows(objects),
        "growth": _growth_rows(objects),
        "cited": cited,
        "refuted": refuted,
    }


@router.get("/map")
def knowledge_map(_u: dict = Depends(require_auth)):
    """tab2 知识地图：领域 → 对象类型 → claim 可下钻结构。"""
    try:
        objects = _cached("objects_all", lambda: _ko_objects(include_doc=False))
        claims_doc = _cached("claims_doc", lambda: _ko_objects(kind="claim", include_doc=True))
        counts = _cached("citations", _citation_counts)
    except (EngineOffline, EngineError) as exc:
        return _offline_payload(exc)

    doc_by_id = {c["id"]: c for c in claims_doc if c.get("id")}
    tree: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for obj in objects:
        domain = obj.get("domain") or "(未标注域)"
        kind = obj.get("kind") or "unknown"
        slot = tree.setdefault(domain, {}).setdefault(kind, [])
        row = {
            "id": obj.get("id"),
            "citations": counts.get(obj.get("id"), 0),
            "evidence_status": obj.get("evidence_status"),
            "review_status": obj.get("review_status"),
            "publication_status": obj.get("publication_status"),
            "content_hidden": not is_published(obj),
        }
        if kind == "claim" and is_published(obj):
            row["statement"] = _statement_of(doc_by_id.get(obj.get("id"), {}))
        slot.append(row)
    for domain in tree:
        for kind in tree[domain]:
            tree[domain][kind].sort(key=lambda r: (-r["citations"], str(r["id"])))
    domains = [
        {"domain": d, "total": sum(len(v) for v in kinds.values()),
         "kinds": [{"kind": k, "count": len(v), "claims": v} for k, v in sorted(kinds.items())]}
        for d, kinds in tree.items()
    ]
    domains.sort(key=lambda d: (-d["total"], d["domain"]))
    return {"available": True, "generated_at": _now(), "domains": domains}


@router.get("/chain")
def knowledge_chain(claim_id: str = Query(min_length=3), _u: dict = Depends(require_auth)):
    """tab2 证据链：claim → evidence → 源文件:行号 + commit（含本地记录定位）。"""
    try:
        chain = _ke_get("/query/chain", {"claim_id": claim_id})
    except EngineError as exc:
        if exc.status == 404:
            raise HTTPException(404, f"unknown claim: {claim_id}") from exc
        return _offline_payload(exc)
    except EngineOffline as exc:
        return _offline_payload(exc)

    claim = chain.get("claim") or {}
    links = chain.get("links") or []
    claim_record = _locate_record(claim_id)
    out_claim = redact({"id": claim_id, **claim})
    if claim_record:
        out_claim["record_source"] = claim_record

    pub_index = _evidence_pub_index()
    out_links = []
    for link in links:
        item = dict(link)
        item.setdefault("publication_status", pub_index.get(link.get("evidence_id") or ""))
        item = _redact_link(item)
        item["record_source"] = _locate_record(link.get("evidence_id") or "")
        out_links.append(item)

    return {
        "available": True,
        "generated_at": _now(),
        "claim_id": claim_id,
        "claim": out_claim,
        "links": out_links,
        "provenance": chain.get("provenance") or [],
        "package_provenance": chain.get("package_provenance") or [],
        "complete": chain.get("complete"),
        "dangling_evidence_refs": chain.get("dangling_evidence_refs") or [],
        "tk_commit": _tk_commit(),
    }


@router.get("/search")
def knowledge_search(
    q: str = Query(min_length=1, max_length=200),
    kind: str | None = None,
    domain: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    _u: dict = Depends(require_auth),
):
    """tab3 检索：KE /search 原样转发，未发布对象只留状态标记。"""
    try:
        res = _ke_get("/search", {"q": q, "kind": kind, "domain": domain, "limit": limit})
    except (EngineOffline, EngineError) as exc:
        payload = _offline_payload(exc)
        payload.update({"q": q, "count": None, "items": []})
        return payload
    items = [redact(it) for it in (res.get("items") or [])]
    return {"available": True, "generated_at": _now(), "q": res.get("q", q),
            "count": res.get("count", len(items)), "items": items}


@router.get("/assets")
def knowledge_assets(_u: dict = Depends(require_auth)):
    """tab3 训练资产：导出三件套各版本 manifest 摘要（条数/状态分布/sha256 前 8 位）。

    Source of record: the export artifacts produced by the KE export pipeline
    (``python -m ke.cli export``), i.e. derived files — not the TK store. If either
    the manifest directory or its files are missing the answer is ``available:
    false``; nothing is synthesised.
    """
    import hashlib

    directory = Path(os.environ.get("KE_ASSET_DIR", ASSET_DIR_DEFAULT))
    if not directory.is_dir():
        return {"available": False, "reason": "asset_dir_missing", "detail": str(directory),
                "manifests": [], "checked_at": _now()}
    rows: list[dict[str, Any]] = []
    for manifest_path in sorted(directory.glob("*manifest*.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rows.append({"file": manifest_path.name, "error": str(exc)[:160]})
            continue
        stem = manifest_path.name.replace("_manifest.json", "").replace("manifest.json", "")
        artifact = None
        for cand in (directory / f"{stem}.jsonl", directory / f"{stem}.json"):
            if cand.is_file() and cand != manifest_path:
                artifact = cand
                break
        digest, size = None, None
        if artifact is not None:
            try:
                digest = hashlib.sha256(artifact.read_bytes()).hexdigest()[:8]
                size = artifact.stat().st_size
            except OSError:  # pragma: no cover - unreadable artifact
                digest = None
        rows.append({
            "file": manifest_path.name,
            "kind": manifest.get("export_type") or stem,
            "version": manifest.get("version") or stem,
            "generated_at": manifest.get("generated_at"),
            "upstream_commit": manifest.get("upstream_commit"),
            "total_claims": manifest.get("total_claims"),
            "included": manifest.get("included"),
            "excluded": manifest.get("excluded"),
            "status_distribution": manifest.get("status_distribution") or {},
            "domain_distribution": manifest.get("domain_distribution") or {},
            "tolerance_distribution": manifest.get("tolerance_distribution") or {},
            "artifact": artifact.name if artifact is not None else None,
            "artifact_bytes": size,
            "sha256_8": digest,
        })
    rows.sort(key=lambda r: (str(r.get("kind") or ""), str(r.get("generated_at") or "")))
    return {"available": bool(rows), "reason": None if rows else "no_manifest_found",
            "directory": str(directory), "manifests": rows, "checked_at": _now()}


@router.get("/source/{rel_path:path}")
def knowledge_source(
    rel_path: str,
    line: int | None = Query(default=None, ge=1),
    context: int = Query(default=8, ge=0, le=60),
):
    """证据直读通道：TK 仓库内单文件只读窗口（免鉴权 GET，见 auth_guard）。

    Route-level depth: suffix allowlist + resolved-path fence + size budget.
    No engine dependency — it keeps working while the KE API is offline.
    """
    path = _resolve_source(rel_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    all_lines = text.splitlines()
    total = len(all_lines)
    focus = line if line else 1
    start = max(1, focus - context)
    end = min(total, focus + context)
    window = [{"n": n, "text": all_lines[n - 1]} for n in range(start, end + 1)]
    return {
        "path": str(path.relative_to(_tk_root().resolve())),
        "commit": _tk_commit(),
        "total_lines": total,
        "focus": focus,
        "start": start,
        "end": end,
        "lines": window,
    }