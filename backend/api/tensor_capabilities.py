"""Tensor capability registry: current-state snapshot of implemented capabilities.

Owner directive 2026-11-16 (microservice-style dynamic registration):
- repos self-register capabilities from code (dump CLI), this service upserts;
- agents query live (GET) instead of trusting README/docs;
- dashboard reads /summary per repo, feature_ledger (demand) vs registry (supply)
  gap = the live "not-yet-done" list.

Complements tensor_capability_deltas (per-mission change history, PR-5 #592):
registry = current state; deltas = who changed what in which mission.
verified may NOT be self-declared: it must arrive with machine evidence
(verdict row / CI run / mission id) recorded in evidence JSON — anti-overclaim
by construction (capability_ledger 17-unverified/2-overclaim lesson).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org

router = APIRouter(prefix="/tensor/capabilities", tags=["tensor-capabilities"])


class CapabilityItem(BaseModel):
    id: str = Field(min_length=2, max_length=160)
    kind: str = Field(default="module", max_length=60)
    maturity: str = Field(default="developing", pattern="^(mature|developing|planned)$")
    endpoint: str | None = Field(default=None, max_length=240)
    module_path: str = Field(min_length=1, max_length=240)
    entry: str | None = Field(default=None, max_length=240)
    params: dict[str, Any] = Field(default_factory=dict)
    wired: bool = False
    tested: bool = False
    verified: bool = False
    test_path: str | None = Field(default=None, max_length=240)
    evidence: dict[str, Any] = Field(default_factory=dict)


class CapabilitySyncBody(BaseModel):
    repo: str = Field(min_length=2, max_length=120)
    source_commit: str | None = Field(default=None, max_length=80)
    capabilities: list[CapabilityItem] = Field(min_length=0, max_length=2000)


def _row_to_dict(r) -> dict:
    return {
        "repo": r["repo"], "capability_id": r["capability_id"], "kind": r["kind"],
        "maturity": r["maturity"], "endpoint": r["endpoint"],
        "module_path": r["module_path"], "entry": r["entry"], "params": r["params"] or {},
        "wired": r["wired"], "tested": r["tested"], "verified": r["verified"],
        "test_path": r["test_path"], "evidence": r["evidence"] or {},
        "source_commit": r["source_commit"],
        "registered_at": r["registered_at"].isoformat() if r["registered_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        "active": r["active"],
    }


@router.get("", dependencies=[Depends(require_auth)])
def list_capabilities(
    repo: str | None = None,
    kind: str | None = None,
    wired: bool | None = None,
    tested: bool | None = None,
    verified: bool | None = None,
    maturity: str | None = None,
    callable_only: bool | None = None,
    active: bool | None = True,
    db: Session = Depends(require_auth_db_marker := __import__("api.deps", fromlist=["get_db"]).get_db),
):
    clauses = ["active = :active" if active is not None else "TRUE"]
    params: dict[str, Any] = {"active": active if active is not None else True}
    if repo:
        clauses.append("repo = :repo"); params["repo"] = repo
    if kind:
        clauses.append("kind = :kind"); params["kind"] = kind
    if wired is not None:
        clauses.append("wired = :wired"); params["wired"] = wired
    if tested is not None:
        clauses.append("tested = :tested"); params["tested"] = tested
    if verified is not None:
        clauses.append("verified = :verified"); params["verified"] = verified
    if maturity:
        clauses.append("maturity = :maturity"); params["maturity"] = maturity
    if callable_only:
        clauses.append("endpoint IS NOT NULL")
    rows = db.execute(text(
        "SELECT * FROM tensor_capability_registry WHERE "
        + " AND ".join(clauses) + " ORDER BY repo, kind, capability_id LIMIT 2000"
    ), params).mappings().all()
    return {"count": len(rows), "items": [_row_to_dict(r) for r in rows]}


@router.get("/summary", dependencies=[Depends(require_auth)])
def capability_summary(db: Session = Depends(require_auth_db_marker)):
    rows = db.execute(text("""
        SELECT repo,
               count(*) FILTER (WHERE active) AS active_total,
               count(*) FILTER (WHERE active AND wired) AS wired,
               count(*) FILTER (WHERE active AND tested) AS tested,
               count(*) FILTER (WHERE active AND verified) AS verified,
               count(*) FILTER (WHERE active AND NOT wired) AS unwired,
               count(*) FILTER (WHERE active AND maturity='mature') AS mature,
               count(*) FILTER (WHERE active AND maturity='developing') AS developing,
               count(*) FILTER (WHERE active AND maturity='planned') AS planned,
               count(*) FILTER (WHERE active AND endpoint IS NOT NULL) AS callable,
               count(*) FILTER (WHERE NOT active) AS tombstoned
        FROM tensor_capability_registry GROUP BY repo ORDER BY repo
    """)).mappings().all()
    ledger = db.execute(text("""
        SELECT repo AS repo, count(*) AS ledger_anchors
        FROM feature_ledger GROUP BY repo
    """)).mappings().all()
    lmap = {r["repo"]: r["ledger_anchors"] for r in ledger}
    return {"repos": [
        {**dict(r), "ledger_anchors": lmap.get(r["repo"], 0)} for r in rows
    ]}


@router.get("/callable", dependencies=[Depends(require_auth)])
def callable_list(
    repo: str | None = None,
    db: Session = Depends(require_auth_db_marker),
):
    """TensorPhysics 可调用清单：endpoint 非空且 active 的能力（即经平台层可实际调用的求解器入口）。"""
    q = ("SELECT * FROM tensor_capability_registry "
         "WHERE active AND endpoint IS NOT NULL")
    params: dict[str, Any] = {}
    if repo:
        q += " AND repo = :repo"; params["repo"] = repo
    q += " ORDER BY repo, capability_id LIMIT 2000"
    rows = db.execute(text(q), params).mappings().all()
    return {"count": len(rows), "items": [_row_to_dict(r) for r in rows]}


@router.post("/sync", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def sync_capabilities(
    body: CapabilitySyncBody,
    db: Session = Depends(require_auth_db_marker),
    user: dict = Depends(require_auth),
):
    org = resolved_org(None, user, db)
    for c in body.capabilities:
        if c.maturity == "mature" and not (c.verified and c.wired):
            raise HTTPException(422, f"capability {c.id}: maturity=mature requires verified+wired machine evidence (anti-overclaim)")
        if c.endpoint and not c.wired:
            raise HTTPException(422, f"capability {c.id}: endpoint (TensorPhysics callable) requires wired=true")
    ids = []
    for c in body.capabilities:
        ids.append(c.id)
        db.execute(text("""
            INSERT INTO tensor_capability_registry
                (organization_id, repo, capability_id, kind, maturity, module_path, entry,
                 params, wired, tested, verified, test_path, evidence, source_commit,
                 endpoint, registered_at, updated_at, active)
            VALUES (:org, :repo, :cid, :kind, :maturity, :mod, :entry, CAST(:params AS jsonb),
                    :wired, :tested, :verified, :test_path, CAST(:evidence AS jsonb),
                    :commit, :endpoint, now(), now(), TRUE)
            ON CONFLICT (repo, capability_id) DO UPDATE SET
                kind = EXCLUDED.kind, maturity = EXCLUDED.maturity,
                module_path = EXCLUDED.module_path, endpoint = EXCLUDED.endpoint,
                entry = EXCLUDED.entry, params = EXCLUDED.params,
                wired = EXCLUDED.wired, tested = EXCLUDED.tested,
                verified = EXCLUDED.verified, test_path = EXCLUDED.test_path,
                evidence = EXCLUDED.evidence, source_commit = EXCLUDED.source_commit,
                updated_at = now(), active = TRUE
        """), {
            "org": org, "repo": body.repo, "cid": c.id, "kind": c.kind,
            "maturity": c.maturity, "endpoint": c.endpoint,
            "mod": c.module_path, "entry": c.entry, "params": _dumps(c.params),
            "wired": c.wired, "tested": c.tested, "verified": c.verified,
            "test_path": c.test_path, "evidence": _dumps(c.evidence),
            "commit": body.source_commit,
        })
    tombstoned = 0
    if body.capabilities:
        tomb = db.execute(text("""
            UPDATE tensor_capability_registry
            SET active = FALSE, updated_at = now()
            WHERE repo = :repo AND active AND capability_id != ALL(:ids)
            RETURNING id
        """), {"repo": body.repo, "ids": ids}).fetchall()
        tombstoned = len(tomb)
    db.commit()
    return {"repo": body.repo, "upserted": len(ids), "tombstoned": tombstoned,
            "source_commit": body.source_commit}


def _dumps(v: Any) -> str:
    import json
    return json.dumps(v, ensure_ascii=False, sort_keys=True)
