"""Mission gradient API — seeding mix, exploration budget, credit-matched dispatch.

Flag-gated: the router is mounted only when ``TP_MISSION_GRADIENT=1`` (default
off).  The mechanism lands additively; the live dispatch path is untouched until
the platform freeze is lifted (see ``docs/mission_gradient_mechanism.md``).

Schema (migration ``103_mission_gradient.sql``):
  mission_gradient_mix        — versioned seeding ratios (60/25/10/4/1 start)
  worker_credit_tiers         — worker credit levels L1..L5
  mission_negative_results    — append-only S-N track (honest FAIL learning)
  tensor_dev_missions         — + gradient_tier / exploration_budget / honest_fail
                                / fail_root_cause / retrospective
  worker_failure_rate_v1      — failure rate excluding quarantined honest FAILs

Red-team checks:
  - a non-exploration mission can never be booked as honest FAIL
  - an honest FAIL is append-only (overwrite refused)
  - an S5 mission is never dispatched to an L4 worker
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from services import mission_gradient as mg
from services.budget_accounting import record_budget_used
from database.config import get_db

router = APIRouter(prefix="/tensor/mission-gradient", tags=["任务梯度"])


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _active_mix(db: Session) -> tuple[dict[str, float], dict[str, Any]]:
    row = db.execute(
        text("""
            SELECT version, mix, signature, note, created_at
            FROM mission_gradient_mix WHERE active
            ORDER BY version DESC LIMIT 1
        """)
    ).mappings().first()
    if not row:
        return dict(mg.DEFAULT_MIX), {"version": 0, "signature": mg.mix_signature(mg.DEFAULT_MIX),
                                      "note": "built-in default (no registry row yet)",
                                      "created_at": None}
    mix = row["mix"] if isinstance(row["mix"], dict) else json.loads(row["mix"])
    return mg.validate_mix(mix), {
        "version": row["version"],
        "signature": row["signature"],
        "note": row["note"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


# ─────────────────────────────── mix ────────────────────────────────────

class MixBody(BaseModel):
    mix: dict[str, float] = Field(description="tier -> weight, must sum to 1.0")
    note: str = Field(default="", max_length=500)


@router.get("/mix", dependencies=[Depends(require_auth)])
def get_mix(db: Session = Depends(get_db)):
    mix, meta = _active_mix(db)
    return {"mix": mix, "meta": meta, "labels": mg.TIER_LABELS,
            "defaults": dict(mg.DEFAULT_MIX), "tolerance": mg.MIX_TOL}


@router.put("/mix", dependencies=[Depends(require_auth)])
def put_mix(body: MixBody, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    try:
        mix = mg.validate_mix(body.mix)
    except mg.MixError as exc:
        raise HTTPException(422, str(exc))
    version = db.execute(
        text("SELECT COALESCE(max(version), 0) + 1 FROM mission_gradient_mix")
    ).scalar()
    db.execute(text("UPDATE mission_gradient_mix SET active = FALSE WHERE active"))
    db.execute(
        text("""
            INSERT INTO mission_gradient_mix
                (version, mix, signature, active, note, created_by)
            VALUES (:v, CAST(:mix AS jsonb), :sig, TRUE, :note, :uid)
        """),
        {"v": version, "mix": _dumps(mix), "sig": mg.mix_signature(mix),
         "note": body.note, "uid": user.get("id")},
    )
    db.commit()
    return {"version": version, "mix": mix, "signature": mg.mix_signature(mix),
            "note": body.note, "active": True}


@router.get("/quota", dependencies=[Depends(require_auth)])
def get_quota(total: int = 100, db: Session = Depends(get_db)):
    mix, meta = _active_mix(db)
    try:
        quota = mg.allocate_quota(total, mix)
    except mg.GradientError as exc:
        raise HTTPException(422, str(exc))
    return {"total": total, "quota": quota, "mix": mix, "mix_meta": meta,
            "labels": mg.TIER_LABELS,
            "exploration_quota": sum(v for k, v in quota.items() if k in mg.EXPLORATION_TIERS)}


# ───────────────────────── mission gradient field ───────────────────────

class GradientBody(BaseModel):
    tier: str = Field(pattern="^[Ss][1-5]$")
    base_budget: dict[str, Any] = Field(default_factory=dict)
    self_declared: bool = False


@router.post("/missions/{mission_id}/gradient", dependencies=[Depends(require_auth)])
def set_mission_gradient(mission_id: int, body: GradientBody, db: Session = Depends(get_db)):
    tier = mg.normalise_tier(body.tier)
    mix, meta = _active_mix(db)
    marker = mg.exploration_budget(tier, body.base_budget, mix)
    exists = db.execute(text("SELECT 1 FROM tensor_dev_missions WHERE id = :m"),
                        {"m": mission_id}).first()
    if not exists:
        raise HTTPException(404, f"mission {mission_id} not found")
    db.execute(
        text("""
            UPDATE tensor_dev_missions
               SET gradient_tier = :tier,
                   exploration_budget = CAST(:expl AS jsonb),
                   gradient_self_declared = :selfdecl
             WHERE id = :m
        """),
        {"tier": tier,
         "expl": _dumps(marker) if marker["exploration"] else None,
         "selfdecl": body.self_declared, "m": mission_id},
    )
    db.commit()
    return {"mission_id": mission_id, "gradient": marker, "mix_meta": meta,
            "labels": mg.TIER_LABELS}


@router.get("/missions/{mission_id}/gradient", dependencies=[Depends(require_auth)])
def get_mission_gradient(mission_id: int, db: Session = Depends(get_db)):
    row = db.execute(
        text("""
            SELECT id, gradient_tier, exploration_budget, gradient_self_declared,
                   honest_fail, fail_root_cause, retrospective, negative_result_ref, status
              FROM tensor_dev_missions WHERE id = :m
        """),
        {"m": mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"mission {mission_id} not found")
    tier = mg.normalise_tier(row["gradient_tier"] or "S1")
    return {
        "mission_id": row["id"],
        "status": row["status"],
        "gradient": {
            "tier": tier,
            "label": mg.TIER_LABELS[tier],
            "exploration": tier in mg.EXPLORATION_TIERS,
            "min_credit_level": mg.min_credit_level(tier),
            "self_declared": bool(row["gradient_self_declared"]),
            "marker": (row["exploration_budget"]
                       if isinstance(row["exploration_budget"], dict)
                       else (json.loads(row["exploration_budget"]) if row["exploration_budget"] else None)),
        },
        "honest_fail": {
            "is_honest_fail": bool(row["honest_fail"]),
            "root_cause": row["fail_root_cause"],
            "retrospective": row["retrospective"],
            "negative_result_ref": row["negative_result_ref"],
        },
    }


# ───────────────────────── credit-tier dispatch ─────────────────────────

class CreditBody(BaseModel):
    credit_level: int = Field(ge=1, le=5)
    rationale: str = Field(default="", max_length=500)


@router.put("/workers/{worker_id}/credit", dependencies=[Depends(require_auth)])
def put_worker_credit(worker_id: str, body: CreditBody, db: Session = Depends(get_db)):
    db.execute(
        text("""
            INSERT INTO worker_credit_tiers (worker_id, credit_level, rationale, updated_at)
            VALUES (:w, :lvl, :why, now())
            ON CONFLICT ON CONSTRAINT uq_worker_credit_tiers_worker
            DO UPDATE SET credit_level = EXCLUDED.credit_level,
                          rationale = EXCLUDED.rationale,
                          updated_at = now()
        """),
        {"w": worker_id, "lvl": body.credit_level, "why": body.rationale},
    )
    db.commit()
    return {"worker_id": worker_id, "credit_level": body.credit_level}


class DispatchPreview(BaseModel):
    mission_id: int
    tier: str = Field(pattern="^[Ss][1-5]$")
    candidates: list[dict[str, Any]] | None = None


@router.post("/dispatch/preview", dependencies=[Depends(require_auth)])
def dispatch_preview(body: DispatchPreview, db: Session = Depends(get_db)):
    """Dry-run the credit gate for one mission against the registered worker tiers."""
    if body.candidates:
        candidates = body.candidates
    else:
        rows = db.execute(
            text("SELECT worker_id, credit_level FROM worker_credit_tiers ORDER BY worker_id")
        ).mappings().all()
        candidates = [{"worker_id": r["worker_id"], "credit_level": r["credit_level"],
                       "inflight": 0} for r in rows]
    decision = mg.dispatch_decide({"mission_id": body.mission_id, "tier": body.tier}, candidates)
    return {"decision": decision, "candidate_source": "database" if not body.candidates else "request"}


# ─────────────────── honest FAIL / negative-result track ────────────────

class HonestFailBody(BaseModel):
    mission_id: int
    worker_id: str = Field(min_length=1, max_length=120)
    root_cause: str = Field(min_length=8)
    retrospective: str = Field(min_length=20)
    evidence_ref: str = Field(default="", max_length=500)
    reuse_tags: list[str] = Field(default_factory=list)


@router.post("/negative-results", dependencies=[Depends(require_auth)])
def post_negative_result(body: HonestFailBody, db: Session = Depends(get_db)):
    row = db.execute(
        text("""
            SELECT id, gradient_tier, honest_fail, status
              FROM tensor_dev_missions WHERE id = :m FOR UPDATE
        """),
        {"m": body.mission_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"mission {body.mission_id} not found")

    guard = mg.guard_honest_fail(
        {"mission_id": row["id"], "status": "honest_fail"} if row["honest_fail"] else None,
        "honest_fail",
    )
    if not guard["allowed"]:
        raise HTTPException(409, guard["detail"])

    tier = mg.normalise_tier(row["gradient_tier"] or "S1")
    try:
        entry = mg.negative_result_entry(
            {"worker_id": body.worker_id, "mission_id": str(body.mission_id), "tier": tier,
             "status": "honest_fail", "root_cause": body.root_cause,
             "retrospective": body.retrospective, "evidence_ref": body.evidence_ref},
            reuse_tags=body.reuse_tags,
        )
    except mg.GradientError as exc:
        raise HTTPException(422, str(exc))

    nid = db.execute(
        text("""
            INSERT INTO mission_negative_results
                (mission_id, tier, root_cause, retrospective, evidence_ref, reuse_tags)
            VALUES (:m, :tier, :rc, :retro, :ev, :tags)
            RETURNING id
        """),
        {"m": body.mission_id, "tier": tier, "rc": entry["root_cause"],
         "retro": entry["retrospective"], "ev": entry["evidence_ref"],
         "tags": entry["reuse_tags"]},
    ).scalar()
    # FP15-BUDGET-ACCOUNTING：honest_fail 也是终态，先落预算记账再翻状态
    record_budget_used(db, body.mission_id)
    db.execute(
        text("""
            UPDATE tensor_dev_missions
               SET honest_fail = TRUE, fail_root_cause = :rc, retrospective = :retro,
                   negative_result_ref = :ref, status = 'failed', fail_stage = 'honest_fail'
             WHERE id = :m
        """),
        {"m": body.mission_id, "rc": entry["root_cause"], "retro": entry["retrospective"],
         "ref": f"S-N:{nid}"},
    )
    db.commit()
    return {"negative_result_id": nid, "entry": entry}


@router.get("/negative-results", dependencies=[Depends(require_auth)])
def list_negative_results(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id, mission_id, tier, root_cause, retrospective, evidence_ref,
                   reuse_tags, created_at
              FROM mission_negative_results ORDER BY id DESC LIMIT :lim
        """),
        {"lim": max(1, min(500, limit))},
    ).mappings().all()
    return {"track": "S-N", "count": len(rows),
            "items": [{**dict(r), "created_at": r["created_at"].isoformat() if r["created_at"] else None}
                      for r in rows]}


@router.get("/failure-rate", dependencies=[Depends(require_auth)])
def failure_rate(db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT * FROM worker_failure_rate_v1 ORDER BY worker_id")
    ).mappings().all()
    return {"view": "worker_failure_rate_v1",
            "rule": "quarantined honest FAILs leave both numerator and denominator",
            "workers": [dict(r) for r in rows]}


# ───────────────────────────── Q1 self-check ────────────────────────────

class Q1Body(BaseModel):
    criteria: list[dict[str, Any]]
    min_criteria: int = 1


@router.post("/q1/evaluate", dependencies=[Depends(require_auth)])
def q1_evaluate(body: Q1Body):
    try:
        return mg.evaluate_q1(body.criteria, min_criteria=body.min_criteria)
    except mg.GradientError as exc:
        raise HTTPException(422, str(exc))


@router.get("/self-declaration", dependencies=[Depends(require_auth)])
def self_declaration(mission_id: int = 0, db: Session = Depends(get_db)):
    mix, meta = _active_mix(db)
    payload = mg.self_declare(mission_id, mix=mix)
    return {**payload, "mix_meta": meta}