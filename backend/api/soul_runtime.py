"""Tensor Soul Runtime API（RFC #72 Phase 1 MVP，契约 v0.1）。

复用既有 factory 应用（`backend/factory_app.py`）的认证与组织作用域；
所有写入都落在 094_factory_soul.sql 新增的表上，不改动 upstream 任务引擎。

边界（每一条都在测试里断言）：
- 身份键是 agent_id；model_binding_id 只是会话上的可切换配置引用；
- observe 幂等：同一 idempotency_key 重复提交不产生第二次副作用；
- 共享知识必须 validated + 有 validator，且经由 LearningCandidate 晋级；
- recall/prepare 有字符预算，不回放全量 transcript。
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db
from services import soul_runtime as soul

router = APIRouter(prefix='/tensor/soul', tags=['soul'])

CUSTODIAN_ROLES = ('ADMIN', 'MANAGER', 'ENGINEER')


def _role(user: dict) -> str:
    return str(user.get('role') or '').split('.')[-1].upper()


def _own(db: Session, user: dict, agent_id: str) -> dict:
    """读取本人（或平台托管角色）可操作的 agent 身份。"""
    org = resolved_org(None, user, db)
    try:
        agent = soul.get_agent(db, agent_id=agent_id, organization_id=org)
    except soul.NotFoundError:
        raise HTTPException(404, 'agent identity not found') from None
    if agent['owner_user_id'] != user['id'] and _role(user) not in CUSTODIAN_ROLES:
        raise HTTPException(403, 'agent identity belongs to another principal')
    return agent


class NewAgent(BaseModel):
    worker_id: str = Field(min_length=2, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    role: str = Field(min_length=1, max_length=40)
    goal: str = Field(default='', max_length=4000)
    constraints: list[str] = Field(default_factory=list, max_length=32)
    policy_refs: list[str] = Field(default_factory=list, max_length=32)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    owner_user_id: int | None = Field(default=None, ge=1)


class GoalRevision(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    constraints: list[str] | None = Field(default=None, max_length=32)


class NewBinding(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    adapter: dict = Field(default_factory=dict)
    capability_limits: dict = Field(default_factory=dict)


class NewSession(BaseModel):
    agent_id: uuid.UUID
    task_id: int | None = Field(default=None, ge=1)
    model_binding_id: uuid.UUID | None = None
    active_plan: dict = Field(default_factory=dict)


class TaskLink(BaseModel):
    task_id: int = Field(ge=1)
    note: str = Field(default='', max_length=200)


class Plan(BaseModel):
    plan: dict = Field(default_factory=dict)


class BindingSwitch(BaseModel):
    binding_id: uuid.UUID


class CheckpointWrite(BaseModel):
    version: int = Field(ge=1)
    state: dict
    state_version: int = Field(default=1, ge=1)


class Observation(BaseModel):
    agent_id: uuid.UUID
    event_type: Literal['observation', 'decision', 'outcome', 'feedback', 'lesson', 'fact']
    source: str = Field(min_length=1, max_length=200)
    payload: dict
    session_id: uuid.UUID | None = None
    task_id: int | None = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    provenance: dict = Field(default_factory=dict)
    sensitivity: Literal['private', 'organization', 'shared'] = 'private'
    verification_status: Literal['observed', 'validated', 'rejected', 'superseded'] = 'observed'
    validator: dict | None = None


class VerificationChange(BaseModel):
    status: Literal['observed', 'validated', 'rejected', 'superseded']
    validator: dict | None = None
    superseded_by: int | None = Field(default=None, ge=1)


class CandidateProposal(BaseModel):
    agent_id: uuid.UUID
    source_event_ids: list[int] = Field(min_length=1, max_length=32)
    proposed_generalization: str = Field(min_length=1, max_length=4000)
    scope: Literal['agent', 'organization', 'shared'] = 'organization'
    evidence: dict = Field(default_factory=dict)
    evaluation: dict = Field(default_factory=dict)


class CandidateDecision(BaseModel):
    status: Literal['approved', 'rejected', 'superseded']
    reason: str = Field(default='', max_length=2000)
    evaluation: dict = Field(default_factory=dict)


class RecallQuery(BaseModel):
    agent_id: uuid.UUID
    task_context: str = Field(default='', max_length=8000)
    limit: int = Field(default=8, ge=1, le=24)
    max_chars: int = Field(default=soul.DEFAULT_RECALL_CHARS, ge=200, le=20000)


class PrepareQuery(BaseModel):
    session_id: uuid.UUID
    task_context: str = Field(default='', max_length=8000)
    max_chars: int = Field(default=soul.DEFAULT_PREPARE_CHARS, ge=400, le=40000)
    recall_limit: int = Field(default=6, ge=1, le=24)


def _guard(call):
    """把契约级异常映射成明确的 HTTP 状态，不吞异常。"""
    try:
        return call()
    except soul.ConflictError as exc:
        raise HTTPException(409, str(exc)) from None
    except soul.NotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except soul.CheckpointCorruptError as exc:
        raise HTTPException(409, str(exc)) from None
    except soul.SoulError as exc:
        raise HTTPException(422, str(exc)) from None


# ── 身份 ──────────────────────────────────────────────────────────────────
@router.post('/agents')
def create_agent(body: NewAgent, db: Session = Depends(get_db),
                 user=Depends(require_role('ADMIN'))):
    org = resolved_org(None, user, db)
    owner = body.owner_user_id or user['id']
    if body.owner_user_id:
        valid = db.execute(text(
            "SELECT 1 FROM users WHERE id=:uid AND organization_id=:org AND is_active=1"),
            {'uid': owner, 'org': org}).scalar()
        if not valid:
            raise HTTPException(422, 'owner_user_id must be an active user of this organization')
    agent = _guard(lambda: soul.create_agent(
        db, organization_id=org, owner_user_id=owner, worker_id=body.worker_id,
        role=body.role, goal=body.goal, constraints=body.constraints,
        policy_refs=body.policy_refs, capabilities=body.capabilities))
    return {'agent': agent, 'identity_key': 'agent_id', 'model_is_not_identity': True}


@router.post('/agents/link-resident/{resident_session_id}')
def link_resident_session(resident_session_id: uuid.UUID, db: Session = Depends(get_db),
                          user=Depends(require_role('ADMIN'))):
    """把 091 时代的 resident 会话挂到稳定 agent 身份（幂等，不搬移旧数据）。"""
    org = resolved_org(None, user, db)
    agent = _guard(lambda: soul.ensure_agent_for_resident_session(
        db, organization_id=org, resident_session_id=str(resident_session_id)))
    return {'agent': agent, 'identity_key': 'agent_id',
            'legacy_session_linked': True}


@router.get('/agents/{agent_id}')
def read_agent(agent_id: uuid.UUID, db: Session = Depends(get_db), user=Depends(require_auth)):
    return {'agent': _own(db, user, str(agent_id))}


@router.patch('/agents/{agent_id}/goal')
def revise_goal(agent_id: uuid.UUID, body: GoalRevision, db: Session = Depends(get_db),
                user=Depends(require_auth)):
    agent = _own(db, user, str(agent_id))
    org = resolved_org(None, user, db)
    updated = _guard(lambda: soul.revise_goal(
        db, agent_id=str(agent['agent_id']), organization_id=org, goal=body.goal,
        constraints=body.constraints))
    return {'agent': updated, 'identity_preserved': updated['agent_id'] == agent['agent_id']}


# ── 模型绑定（版本化配置）───────────────────────────────────────────────────
@router.post('/bindings')
def register_binding(body: NewBinding, db: Session = Depends(get_db),
                     user=Depends(require_role('ADMIN'))):
    org = resolved_org(None, user, db)
    binding = _guard(lambda: soul.register_binding(
        db, organization_id=org, name=body.name, provider=body.provider, model=body.model,
        adapter=body.adapter, capability_limits=body.capability_limits, created_by=user['id']))
    return {'binding': binding, 'is_identity_key': False, 'versioned': True}


@router.get('/bindings/{binding_id}')
def read_binding(binding_id: uuid.UUID, db: Session = Depends(get_db),
                 user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    return {'binding': _guard(lambda: soul.get_binding(
        db, binding_id=str(binding_id), organization_id=org))}


# ── 会话 / 任务引用 / 绑定切换 ─────────────────────────────────────────────
@router.post('/sessions')
def open_session(body: NewSession, db: Session = Depends(get_db), user=Depends(require_auth)):
    agent = _own(db, user, str(body.agent_id))
    org = resolved_org(None, user, db)
    session = _guard(lambda: soul.open_session(
        db, organization_id=org, agent_id=str(agent['agent_id']), task_id=body.task_id,
        model_binding_id=str(body.model_binding_id) if body.model_binding_id else None,
        active_plan=body.active_plan))
    return {'session': session, 'agent_id': str(agent['agent_id']),
            'session_is_episode': True}


@router.get('/sessions/{session_id}')
def read_session(session_id: uuid.UUID, db: Session = Depends(get_db),
                 user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    return {'session': _guard(lambda: soul.get_session(
        db, session_id=str(session_id), organization_id=org))}


@router.post('/sessions/{session_id}/task')
def attach_task(session_id: uuid.UUID, body: TaskLink, db: Session = Depends(get_db),
                user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    exists = db.execute(text(
        "SELECT 1 FROM tensor_dev_missions WHERE id=:mid AND organization_id=:org"),
        {'mid': body.task_id, 'org': org}).scalar()
    if not exists:
        raise HTTPException(404, 'task not found in this organization')
    return {'session': _guard(lambda: soul.attach_task(
        db, session_id=str(session_id), organization_id=org, task_id=body.task_id,
        note=body.note))}


@router.put('/sessions/{session_id}/plan')
def set_plan(session_id: uuid.UUID, body: Plan, db: Session = Depends(get_db),
             user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    return {'session': _guard(lambda: soul.set_active_plan(
        db, session_id=str(session_id), organization_id=org, plan=body.plan))}


@router.post('/sessions/{session_id}/binding')
def switch_binding(session_id: uuid.UUID, body: BindingSwitch, db: Session = Depends(get_db),
                   user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    result = _guard(lambda: soul.switch_binding(
        db, session_id=str(session_id), organization_id=org,
        binding_id=str(body.binding_id), switched_by=user['id']))
    return {**result, 'identity_changed': result['agent_id_before'] != result['agent_id_after']}


@router.post('/sessions/{session_id}/close')
def close_session(session_id: uuid.UUID, db: Session = Depends(get_db),
                  user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    return {'session': _guard(lambda: soul.close_session(
        db, session_id=str(session_id), organization_id=org))}


# ── checkpoint / restore ──────────────────────────────────────────────────
@router.post('/sessions/{session_id}/checkpoints')
def write_checkpoint(session_id: uuid.UUID, body: CheckpointWrite,
                     db: Session = Depends(get_db), user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    return _guard(lambda: soul.commit_checkpoint(
        db, session_id=str(session_id), organization_id=org, version=body.version,
        state=body.state, state_version=body.state_version))


@router.get('/sessions/{session_id}/checkpoints/restore')
def restore_checkpoint(session_id: uuid.UUID, version: int | None = None,
                       db: Session = Depends(get_db), user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    return _guard(lambda: soul.restore_checkpoint(
        db, session_id=str(session_id), organization_id=org, version=version))


# ── 记忆事件 ──────────────────────────────────────────────────────────────
@router.post('/observe')
def observe(body: Observation, db: Session = Depends(get_db), user=Depends(require_auth)):
    agent = _own(db, user, str(body.agent_id))
    org = resolved_org(None, user, db)
    result = _guard(lambda: soul.observe(
        db, organization_id=org, agent_id=str(agent['agent_id']),
        event_type=body.event_type, source=body.source, payload=body.payload,
        session_id=str(body.session_id) if body.session_id else None,
        task_id=body.task_id, idempotency_key=body.idempotency_key,
        provenance=body.provenance, sensitivity=body.sensitivity,
        verification_status=body.verification_status, validator=body.validator))
    return {**result, 'side_effect_applied': not result['duplicate']}


@router.patch('/events/{event_id}/verification')
def change_verification(event_id: int, body: VerificationChange,
                        db: Session = Depends(get_db), user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    if body.status == 'validated' and _role(user) not in CUSTODIAN_ROLES:
        raise HTTPException(403, 'validation requires an independent platform role')
    return {'event': _guard(lambda: soul.set_verification(
        db, organization_id=org, event_id=event_id, status=body.status,
        validator=body.validator, superseded_by=body.superseded_by))}


@router.post('/events/{event_id}/promote')
def promote(event_id: int, db: Session = Depends(get_db),
            user=Depends(require_role('ADMIN', 'MANAGER', 'ENGINEER'))):
    org = resolved_org(None, user, db)
    return _guard(lambda: soul.promote_to_shared(
        db, organization_id=org, event_id=event_id))


@router.get('/knowledge')
def knowledge(limit: int = 20, db: Session = Depends(get_db), user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    rows = soul.list_shared_knowledge(db, organization_id=org,
                                     limit=max(1, min(100, limit)))
    return {'items': rows, 'only_validated': True,
            'policy': 'shared knowledge requires validated verification status'}


# ── recall / prepare ──────────────────────────────────────────────────────
@router.post('/recall')
def recall(body: RecallQuery, db: Session = Depends(get_db), user=Depends(require_auth)):
    agent = _own(db, user, str(body.agent_id))
    org = resolved_org(None, user, db)
    return _guard(lambda: soul.recall(
        db, organization_id=org, agent_id=str(agent['agent_id']),
        task_context=body.task_context, limit=body.limit, max_chars=body.max_chars))


@router.post('/prepare')
def prepare(body: PrepareQuery, db: Session = Depends(get_db), user=Depends(require_auth)):
    org = resolved_org(None, user, db)
    return _guard(lambda: soul.prepare(
        db, organization_id=org, session_id=str(body.session_id),
        task_context=body.task_context, max_chars=body.max_chars,
        recall_limit=body.recall_limit))


# ── learn / consolidate ───────────────────────────────────────────────────
@router.post('/learning/candidates')
def propose_candidate(body: CandidateProposal, db: Session = Depends(get_db),
                      user=Depends(require_auth)):
    agent = _own(db, user, str(body.agent_id))
    org = resolved_org(None, user, db)
    return {'candidate': _guard(lambda: soul.propose_candidate(
        db, organization_id=org, agent_id=str(agent['agent_id']),
        source_event_ids=body.source_event_ids,
        proposed_generalization=body.proposed_generalization, scope=body.scope,
        evidence=body.evidence, evaluation=body.evaluation)),
        'auto_promoted': False}


@router.post('/learning/candidates/{candidate_id}/decision')
def decide_candidate(candidate_id: uuid.UUID, body: CandidateDecision,
                     db: Session = Depends(get_db),
                     user=Depends(require_role('ADMIN', 'MANAGER', 'ENGINEER'))):
    org = resolved_org(None, user, db)
    return {'candidate': _guard(lambda: soul.decide_candidate(
        db, organization_id=org, candidate_id=str(candidate_id), status=body.status,
        decided_by=user['id'], reason=body.reason, evaluation=body.evaluation))}


@router.post('/learning/candidates/{candidate_id}/consolidate')
def consolidate(candidate_id: uuid.UUID, db: Session = Depends(get_db),
                user=Depends(require_role('ADMIN', 'MANAGER', 'ENGINEER'))):
    org = resolved_org(None, user, db)
    return _guard(lambda: soul.consolidate(
        db, organization_id=org, candidate_id=str(candidate_id), decided_by=user['id']))
