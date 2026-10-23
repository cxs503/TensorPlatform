"""Tensor Soul Runtime v0.1（RFC #72 Phase 1 MVP）。

在现有 resident community / mission / event 机制上补齐契约 MVP 六条：
稳定 agent 身份、会话/任务引用与 checkpoint、带来源的 append-only 记忆事件、
有界 recall/prepare、版本化 ModelBinding、验证状态与 LearningCandidate 晋级门。

设计纪律（契约不变量）：
- agent_id 与模型/供应商无关，永不由 session 或模型派生；
- 模型输出是观察（verification_status='observed'），不是事实；
- 共享知识（sensitivity='shared'）只能来自 validated 且有 validator 的记录，
  这条由数据库 CHECK 约束兜底，不依赖调用方自觉；
- recall/prepare 有硬预算，禁止把全量 transcript 倒进模型输入。

本模块不引模型、不发网络请求；推理执行仍由既有 Gateway/factory_codex 承担。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

VERIFICATION_STATES = ("observed", "validated", "rejected", "superseded")
SENSITIVITIES = ("private", "organization", "shared")
EVENT_TYPES = ("observation", "decision", "outcome", "feedback", "lesson", "fact")
PROMOTION_STATES = ("proposed", "approved", "rejected", "superseded")

DEFAULT_RECALL_CHARS = 2400
DEFAULT_PREPARE_CHARS = 6000
RECALL_SCAN_LIMIT = 400


class SoulError(RuntimeError):
    """契约级错误：调用方必须显式处理，不能当作可忽略告警。"""


class ConflictError(SoulError):
    """幂等键/版本已被不同内容占用。"""


class NotFoundError(SoulError):
    pass


class CheckpointCorruptError(SoulError):
    """校验和不符：拒绝恢复，绝不静默丢弃后从零开始。"""


def canonical(payload: Any) -> str:
    """稳定序列化：checksum 必须可复算，不受 dict 顺序影响。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checksum(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


# ── 身份 ──────────────────────────────────────────────────────────────────
def create_agent(db: Session, *, organization_id: int, owner_user_id: int, worker_id: str,
                 role: str, goal: str = "", constraints: Iterable[str] = (),
                 policy_refs: Iterable[str] = (), capabilities: Iterable[str] = (),
                 legacy_session_id: str | None = None) -> dict:
    """创建稳定 Agent 身份。同一组织内 worker_id 唯一；重复创建返回既有身份。"""
    existing = db.execute(text("""SELECT * FROM factory_agent_identities
        WHERE organization_id=:org AND worker_id=:wid"""),
        {"org": organization_id, "wid": worker_id}).mappings().first()
    if existing:
        return dict(existing)
    agent_id = str(uuid.uuid4())
    db.execute(text("""INSERT INTO factory_agent_identities
        (agent_id,organization_id,owner_user_id,worker_id,role,goal,constraints,policy_refs,
         capabilities,legacy_session_id)
        VALUES (:aid,:org,:uid,:wid,:role,:goal,CAST(:constraints AS jsonb),
                CAST(:policies AS jsonb),CAST(:caps AS jsonb),:legacy)"""),
        {"aid": agent_id, "org": organization_id, "uid": owner_user_id, "wid": worker_id,
         "role": role, "goal": goal, "constraints": json.dumps(list(constraints)),
         "policies": json.dumps(list(policy_refs)), "caps": json.dumps(list(capabilities)),
         "legacy": legacy_session_id})
    db.commit()
    return _agent(db, agent_id, organization_id)


def revise_goal(db: Session, *, agent_id: str, organization_id: int, goal: str,
                constraints: Iterable[str] | None = None) -> dict:
    """目标/约束改版：只涨 revision，不改身份。"""
    row = _agent(db, agent_id, organization_id)
    goal_revision = row["goal_revision"] + (1 if goal != row["goal"] else 0)
    constraint_revision = row["constraint_revision"]
    new_constraints = list(row["constraints"]) if constraints is None else list(constraints)
    if constraints is not None and new_constraints != list(row["constraints"]):
        constraint_revision += 1
    db.execute(text("""UPDATE factory_agent_identities SET goal=:goal,goal_revision=:gr,
        constraints=CAST(:constraints AS jsonb),constraint_revision=:cr,updated_at=now()
        WHERE agent_id=:aid AND organization_id=:org"""),
        {"aid": agent_id, "org": organization_id, "goal": goal, "gr": goal_revision,
         "constraints": json.dumps(new_constraints), "cr": constraint_revision})
    db.commit()
    return _agent(db, agent_id, organization_id)


def _agent(db: Session, agent_id: str, organization_id: int) -> dict:
    row = db.execute(text("""SELECT * FROM factory_agent_identities
        WHERE agent_id=:aid AND organization_id=:org"""),
        {"aid": agent_id, "org": organization_id}).mappings().first()
    if not row:
        raise NotFoundError("agent identity not found in this organization")
    return dict(row)


def get_agent(db: Session, *, agent_id: str, organization_id: int) -> dict:
    return _agent(db, agent_id, organization_id)


# ── 模型绑定（版本化；永不做身份键）────────────────────────────────────────
def register_binding(db: Session, *, organization_id: int, name: str, provider: str,
                     model: str, adapter: dict | None = None,
                     capability_limits: dict | None = None,
                     created_by: int | None = None) -> dict:
    """登记 ModelBinding 的新版本：同名绑定版本号单调递增，旧版本保留可审计。"""
    version = db.execute(text("""SELECT COALESCE(MAX(version),0)+1 FROM factory_soul_model_bindings
        WHERE organization_id=:org AND name=:name"""),
        {"org": organization_id, "name": name}).scalar()
    binding_id = str(uuid.uuid4())
    db.execute(text("""INSERT INTO factory_soul_model_bindings
        (binding_id,organization_id,name,provider,model,adapter,capability_limits,version,created_by)
        VALUES (:bid,:org,:name,:provider,:model,CAST(:adapter AS jsonb),
                CAST(:limits AS jsonb),:version,:by)"""),
        {"bid": binding_id, "org": organization_id, "name": name, "provider": provider,
         "model": model, "adapter": json.dumps(adapter or {}),
         "limits": json.dumps(capability_limits or {}), "version": version, "by": created_by})
    db.commit()
    return dict(db.execute(text("SELECT * FROM factory_soul_model_bindings WHERE binding_id=:bid"),
                           {"bid": binding_id}).mappings().one())


def get_binding(db: Session, *, binding_id: str, organization_id: int) -> dict:
    row = db.execute(text("""SELECT * FROM factory_soul_model_bindings
        WHERE binding_id=:bid AND organization_id=:org"""),
        {"bid": binding_id, "org": organization_id}).mappings().first()
    if not row:
        raise NotFoundError("model binding not found in this organization")
    return dict(row)


# ── 会话（一次交互 episode）────────────────────────────────────────────────
def open_session(db: Session, *, organization_id: int, agent_id: str,
                 task_id: int | None = None, model_binding_id: str | None = None,
                 active_plan: dict | None = None) -> dict:
    """为稳定身份开一次新 episode；agent_id 不变，只有 episode 递增。"""
    _agent(db, agent_id, organization_id)
    if model_binding_id:
        get_binding(db, binding_id=model_binding_id, organization_id=organization_id)
    episode = db.execute(text("""SELECT COALESCE(MAX(episode),0)+1 FROM factory_soul_sessions
        WHERE agent_id=:aid"""), {"aid": agent_id}).scalar()
    session_id = str(uuid.uuid4())
    task_refs = [{"task_id": task_id, "kind": "primary"}] if task_id else []
    db.execute(text("""INSERT INTO factory_soul_sessions
        (session_id,organization_id,agent_id,episode,current_task_id,task_refs,
         active_plan,model_binding_id)
        VALUES (:sid,:org,:aid,:episode,:task,CAST(:refs AS jsonb),
                CAST(:plan AS jsonb),:binding)"""),
        {"sid": session_id, "org": organization_id, "aid": agent_id, "episode": episode,
         "task": task_id, "refs": json.dumps(task_refs),
         "plan": json.dumps(active_plan or {}), "binding": model_binding_id})
    if model_binding_id:
        _record_binding(db, session_id, agent_id, model_binding_id)
    db.commit()
    return _session(db, session_id, organization_id)


def _record_binding(db: Session, session_id: str, agent_id: str, binding_id: str,
                    switched_by: int | None = None) -> None:
    db.execute(text("""INSERT INTO factory_soul_binding_history
        (session_id,agent_id,binding_id,switched_by) VALUES (:sid,:aid,:bid,:by)"""),
        {"sid": session_id, "aid": agent_id, "bid": binding_id, "by": switched_by})


def attach_task(db: Session, *, session_id: str, organization_id: int, task_id: int,
                note: str = "") -> dict:
    """持久化一次任务引用（<uuid> episode 与 task 的关联历史，不只保留最后一个）。"""
    row = _session(db, session_id, organization_id)
    refs = list(row["task_refs"])
    if not any(ref.get("task_id") == task_id for ref in refs):
        refs.append({"task_id": task_id, "kind": "linked", "note": note[:200]})
    db.execute(text("""UPDATE factory_soul_sessions SET task_refs=CAST(:refs AS jsonb),
        current_task_id=:task WHERE session_id=:sid AND organization_id=:org"""),
        {"refs": json.dumps(refs), "task": task_id, "sid": session_id,
         "org": organization_id})
    db.commit()
    return _session(db, session_id, organization_id)


def set_active_plan(db: Session, *, session_id: str, organization_id: int,
                    plan: dict) -> dict:
    _session(db, session_id, organization_id)
    db.execute(text("""UPDATE factory_soul_sessions SET active_plan=CAST(:plan AS jsonb)
        WHERE session_id=:sid AND organization_id=:org"""),
        {"plan": json.dumps(plan), "sid": session_id, "org": organization_id})
    db.commit()
    return _session(db, session_id, organization_id)


def close_session(db: Session, *, session_id: str, organization_id: int) -> dict:
    _session(db, session_id, organization_id)
    db.execute(text("""UPDATE factory_soul_sessions SET status='closed',closed_at=now()
        WHERE session_id=:sid AND organization_id=:org"""),
        {"sid": session_id, "org": organization_id})
    db.commit()
    return _session(db, session_id, organization_id)


def _session(db: Session, session_id: str, organization_id: int) -> dict:
    row = db.execute(text("""SELECT * FROM factory_soul_sessions
        WHERE session_id=:sid AND organization_id=:org"""),
        {"sid": session_id, "org": organization_id}).mappings().first()
    if not row:
        raise NotFoundError("session not found in this organization")
    return dict(row)


def get_session(db: Session, *, session_id: str, organization_id: int) -> dict:
    return _session(db, session_id, organization_id)


def switch_binding(db: Session, *, session_id: str, organization_id: int,
                   binding_id: str, switched_by: int | None = None) -> dict:
    """切换模型绑定：只改 binding 引用与历史，agent_id 与持久状态一律不动。"""
    before = _session(db, session_id, organization_id)
    get_binding(db, binding_id=binding_id, organization_id=organization_id)
    db.execute(text("""UPDATE factory_soul_sessions SET model_binding_id=:bid
        WHERE session_id=:sid AND organization_id=:org"""),
        {"bid": binding_id, "sid": session_id, "org": organization_id})
    _record_binding(db, session_id, str(before["agent_id"]), binding_id, switched_by)
    db.commit()
    after = _session(db, session_id, organization_id)
    return {"session": after, "agent_id_before": str(before["agent_id"]),
            "agent_id_after": str(after["agent_id"]),
            "checkpoint_version_preserved":
                after["checkpoint_version"] == before["checkpoint_version"]}


# ── checkpoint / restore ──────────────────────────────────────────────────
def commit_checkpoint(db: Session, *, session_id: str, organization_id: int,
                      version: int, state: dict, state_version: int = 1) -> dict:
    """写检查点。同一 (session, version) 同内容 = 幂等空操作；不同内容 = 冲突。"""
    session = _session(db, session_id, organization_id)
    digest = checksum(state)
    existing = db.execute(text("""SELECT * FROM factory_soul_checkpoints
        WHERE session_id=:sid AND version=:v"""),
        {"sid": session_id, "v": version}).mappings().first()
    if existing:
        if existing["checksum"] != digest:
            raise ConflictError(
                f"checkpoint version {version} already stored with a different checksum")
        return {"checkpoint": dict(existing), "idempotent": True, "applied": False}
    try:
        db.execute(text("""INSERT INTO factory_soul_checkpoints
            (session_id,agent_id,version,checksum,state,state_version)
            VALUES (:sid,:aid,:v,:cs,CAST(:state AS jsonb),:sv)"""),
            {"sid": session_id, "aid": str(session["agent_id"]), "v": version,
             "cs": digest, "state": canonical(state), "sv": state_version})
        db.execute(text("""UPDATE factory_soul_sessions
            SET checkpoint_version=GREATEST(checkpoint_version,:v) WHERE session_id=:sid"""),
            {"v": version, "sid": session_id})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError(f"checkpoint version {version} concurrently written") from None
    row = db.execute(text("""SELECT * FROM factory_soul_checkpoints
        WHERE session_id=:sid AND version=:v"""),
        {"sid": session_id, "v": version}).mappings().one()
    return {"checkpoint": dict(row), "idempotent": False, "applied": True}


def restore_checkpoint(db: Session, *, session_id: str, organization_id: int,
                       version: int | None = None) -> dict:
    """恢复检查点。校验和不符 → 抛错拒绝恢复（不静默丢状态）。"""
    session = _session(db, session_id, organization_id)
    if version is None:
        row = db.execute(text("""SELECT * FROM factory_soul_checkpoints
            WHERE session_id=:sid ORDER BY version DESC LIMIT 1"""),
            {"sid": session_id}).mappings().first()
    else:
        row = db.execute(text("""SELECT * FROM factory_soul_checkpoints
            WHERE session_id=:sid AND version=:v"""),
            {"sid": session_id, "v": version}).mappings().first()
    if not row:
        raise NotFoundError("checkpoint not found")
    if checksum(row["state"]) != row["checksum"]:
        raise CheckpointCorruptError(
            f"checkpoint {row['version']} checksum mismatch; refusing to restore")
    db.execute(text("""UPDATE factory_soul_sessions SET last_restored_version=:v
        WHERE session_id=:sid"""), {"v": row["version"], "sid": session_id})
    db.commit()
    return {"session": _session(db, session_id, organization_id),
            "checkpoint": dict(row), "agent_id": str(session["agent_id"]),
            "state_version": row["state_version"], "checksum_verified": True}


def latest_checkpoint_version(db: Session, *, session_id: str) -> int:
    return db.execute(text("""SELECT COALESCE(MAX(version),0) FROM factory_soul_checkpoints
        WHERE session_id=:sid"""), {"sid": session_id}).scalar() or 0


# ── 记忆事件（observe / 验证状态）──────────────────────────────────────────
def observe(db: Session, *, organization_id: int, agent_id: str, event_type: str,
            source: str, payload: dict, session_id: str | None = None,
            task_id: int | None = None, idempotency_key: str,
            provenance: dict | None = None, sensitivity: str = "private",
            verification_status: str = "observed", validator: dict | None = None) -> dict:
    """追加一条带来源的记忆事件；同一幂等键重复提交不产生第二次副作用。"""
    if event_type not in EVENT_TYPES:
        raise SoulError(f"unknown event type: {event_type}")
    if sensitivity not in SENSITIVITIES:
        raise SoulError(f"unknown sensitivity: {sensitivity}")
    if verification_status not in VERIFICATION_STATES:
        raise SoulError(f"unknown verification status: {verification_status}")
    _agent(db, agent_id, organization_id)
    if session_id:
        session = _session(db, session_id, organization_id)
        if str(session["agent_id"]) != str(agent_id):
            raise SoulError("session belongs to another agent")
    if sensitivity == "shared" and verification_status != "validated":
        # 数据库 CHECK 同样兜底，这里提前给出可读错误。
        raise SoulError("shared memory requires validated content with a named validator")
    if verification_status == "validated" and not validator:
        raise SoulError("validated content requires a named validator record")
    inserted = db.execute(text("""INSERT INTO factory_memory_events
        (organization_id,agent_id,session_id,task_id,event_type,source,provenance,payload,
         sensitivity,verification_status,validator,idempotency_key)
        VALUES (:org,:aid,:sid,:task,:etype,:source,CAST(:prov AS jsonb),CAST(:payload AS jsonb),
                :sensitivity,:status,CAST(:validator AS jsonb),:key)
        ON CONFLICT (organization_id,idempotency_key) DO NOTHING
        RETURNING *"""),
        {"org": organization_id, "aid": agent_id, "sid": session_id, "task": task_id,
         "etype": event_type, "source": source[:200],
         "prov": json.dumps(provenance or {}), "payload": canonical(payload),
         "sensitivity": sensitivity, "status": verification_status,
         "validator": json.dumps(validator) if validator else None,
         "key": idempotency_key}).mappings().first()
    if inserted:
        db.commit()
        return {"event": dict(inserted), "duplicate": False}
    row = db.execute(text("""SELECT * FROM factory_memory_events
        WHERE organization_id=:org AND idempotency_key=:key"""),
        {"org": organization_id, "key": idempotency_key}).mappings().one()
    if (row["payload"] != payload or row["event_type"] != event_type
            or row["source"] != source[:200] or str(row["agent_id"]) != str(agent_id)):
        db.rollback()
        raise ConflictError("idempotency key already used for different content")
    db.commit()
    return {"event": dict(row), "duplicate": True}


def set_verification(db: Session, *, organization_id: int, event_id: int,
                     status: str, validator: dict | None = None,
                     superseded_by: int | None = None) -> dict:
    """验证状态迁移。append-only 触发器只放行状态字段变更。"""
    if status not in VERIFICATION_STATES:
        raise SoulError(f"unknown verification status: {status}")
    if status == "validated" and not validator:
        raise SoulError("validated requires a named validator record")
    row = db.execute(text("""SELECT * FROM factory_memory_events
        WHERE event_id=:eid AND organization_id=:org"""),
        {"eid": event_id, "org": organization_id}).mappings().first()
    if not row:
        raise NotFoundError("memory event not found in this organization")
    if row["verification_status"] in ("rejected", "superseded"):
        raise SoulError(f"cannot transition terminal status {row['verification_status']}")
    if row["sensitivity"] == "shared" and status != "validated":
        raise SoulError("shared memory cannot be downgraded")
    db.execute(text("""UPDATE factory_memory_events
        SET verification_status=:status,validator=COALESCE(CAST(:validator AS jsonb),validator),
            superseded_by=:superseded
        WHERE event_id=:eid AND organization_id=:org"""),
        {"status": status, "validator": json.dumps(validator) if validator else None,
         "superseded": superseded_by, "eid": event_id, "org": organization_id})
    db.commit()
    return dict(db.execute(text("SELECT * FROM factory_memory_events WHERE event_id=:eid"),
                           {"eid": event_id}).mappings().one())


def promote_to_shared(db: Session, *, organization_id: int, event_id: int,
                      candidate_id: str | None = None) -> dict:
    """把一条已验证个体记忆升级为共享知识（只允许 validated 且带 validator）。"""
    row = db.execute(text("""SELECT * FROM factory_memory_events
        WHERE event_id=:eid AND organization_id=:org"""),
        {"eid": event_id, "org": organization_id}).mappings().first()
    if not row:
        raise NotFoundError("memory event not found in this organization")
    if row["verification_status"] != "validated" or not row["validator"]:
        raise SoulError("only validated content with a named validator may become shared knowledge")
    provenance = dict(row["provenance"])
    provenance["promoted_from_event"] = row["event_id"]
    if candidate_id:
        provenance["learning_candidate_id"] = candidate_id
    return observe(db, organization_id=organization_id, agent_id=str(row["agent_id"]),
                   event_type="fact", source=f"promotion:{row['source']}"[:200],
                   payload=row["payload"], session_id=str(row["session_id"]) if row["session_id"] else None,
                   task_id=row["task_id"], idempotency_key=f"promote:event:{row['event_id']}",
                   provenance=provenance, sensitivity="shared",
                   verification_status="validated", validator=row["validator"])


# ── recall / prepare（有界）────────────────────────────────────────────────
def _text_of(payload: Any) -> str:
    if isinstance(payload, dict):
        return " ".join(str(v) for v in payload.values() if isinstance(v, (str, int, float)))
    return str(payload)


def _terms(context: str) -> set[str]:
    raw = "".join(ch.lower() if ch.isalnum() else " " for ch in context)
    return {token for token in raw.split() if len(token) >= 2}


def recall(db: Session, *, organization_id: int, agent_id: str,
           task_context: str, limit: int = 8,
           max_chars: int = DEFAULT_RECALL_CHARS,
           include_shared: bool = True) -> dict:
    """按任务上下文检索记忆：自己的全部记录 + 本组织已验证的共享记录。

    返回项带 source / scope / verification_status，调用方可判断相关性与真实性。
    预算按字符硬截断，禁止整表倾倒。
    """
    _agent(db, agent_id, organization_id)
    limit = max(1, min(24, int(limit)))
    max_chars = max(200, min(20000, int(max_chars)))
    rows = db.execute(text("""SELECT * FROM factory_memory_events
        WHERE organization_id=:org
          AND (agent_id=:aid
               OR (:shared AND sensitivity IN ('organization','shared')
                   AND verification_status='validated'))
          AND verification_status <> 'rejected'
        ORDER BY event_id DESC LIMIT :scan"""),
        {"org": organization_id, "aid": agent_id, "shared": include_shared,
         "scan": RECALL_SCAN_LIMIT}).mappings().all()
    wanted = _terms(task_context or "")
    scored = []
    for row in rows:
        item = dict(row)
        haystack = (_text_of(item["payload"]) + " " + item["source"] + " "
                    + item["event_type"]).lower()
        overlap = len(wanted & _terms(haystack))
        own = 1 if str(item["agent_id"]) == str(agent_id) else 0
        trusted = 1 if item["verification_status"] == "validated" else 0
        # 排序键：上下文重合 > 已验证 > 本人 > 新近（数据库已倒序，索引即次序）
        scored.append((overlap, trusted, own, item))
    scored.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
    selected, used, truncated = [], 0, False
    for overlap, _trusted, own, item in scored:
        if overlap == 0 and selected:
            continue   # 已有匹配后不再塞零匹配记录，避免噪音倾倒
        cost = len(_text_of(item["payload"])) + len(item["source"])
        if used + cost > max_chars or len(selected) >= limit:
            truncated = True
            continue
        used += cost
        selected.append({
            "event_id": item["event_id"], "event_type": item["event_type"],
            "source": item["source"], "provenance": item["provenance"],
            "payload": item["payload"], "scope": item["sensitivity"],
            "verification_status": item["verification_status"],
            "own_agent": bool(own), "relevance": overlap,
            "agent_id": str(item["agent_id"]), "task_id": item["task_id"]})
    return {"matches": selected, "count": len(selected), "chars_used": used,
            "char_budget": max_chars, "truncated": truncated,
            "scanned": len(rows), "scan_limit": RECALL_SCAN_LIMIT,
            "scope": "own + organization-validated", "task_context_terms": sorted(wanted)}


def prepare(db: Session, *, organization_id: int, session_id: str,
            task_context: str, max_chars: int = DEFAULT_PREPARE_CHARS,
            recall_limit: int = 6) -> dict:
    """构造有界、任务相关的模型输入片段；不回放全量 transcript。"""
    session = _session(db, session_id, organization_id)
    agent = _agent(db, str(session["agent_id"]), organization_id)
    max_chars = max(400, min(40000, int(max_chars)))
    memo = recall(db, organization_id=organization_id, agent_id=str(agent["agent_id"]),
                  task_context=task_context, limit=recall_limit,
                  max_chars=max(200, max_chars // 2))
    checkpoint = db.execute(text("""SELECT version, state_version FROM factory_soul_checkpoints
        WHERE session_id=:sid ORDER BY version DESC LIMIT 1"""),
        {"sid": session_id}).mappings().first()
    binding = None
    if session["model_binding_id"]:
        binding = dict(db.execute(text("""SELECT name,provider,model,version FROM
            factory_soul_model_bindings WHERE binding_id=:bid"""),
            {"bid": session["model_binding_id"]}).mappings().one())
    sections = [{
        "kind": "identity",
        "agent_id": str(agent["agent_id"]),
        "role": agent["role"],
        "goal": agent["goal"],
        "goal_revision": agent["goal_revision"],
        "constraints": agent["constraints"],
        "constraint_revision": agent["constraint_revision"],
        "capabilities": agent["capabilities"],
        "model_binding": binding,   # 只做元数据，不是身份
    }, {
        "kind": "session",
        "session_id": session_id,
        "episode": session["episode"],
        "task_refs": session["task_refs"],
        "active_plan": session["active_plan"],
        "checkpoint": dict(checkpoint) if checkpoint else None,
    }, {
        "kind": "task_context",
        "text": (task_context or "")[:max_chars // 3],
    }, {
        "kind": "memories",
        "items": memo["matches"],
    }]
    encoded, used, dropped = [], 0, 0
    for section in sections:
        body = canonical(section)
        if used + len(body) > max_chars:
            dropped += 1
            if section["kind"] == "memories":
                kept = []
                for item in section["items"]:
                    piece = canonical(item)
                    if used + len(piece) <= max_chars:
                        kept.append(item)
                        used += len(piece)
                    else:
                        dropped += 1
                if kept:
                    encoded.append({"kind": "memories", "items": kept,
                                    "note": "bounded: remaining memories omitted"})
            continue
        used += len(body)
        encoded.append(section)
    return {"sections": encoded, "chars_used": used, "char_budget": max_chars,
            "truncated": dropped > 0, "sections_dropped": dropped,
            "recall": {"count": memo["count"], "truncated": memo["truncated"],
                       "scope": memo["scope"]},
            "transcript_dump": False,
            "identity_key": "agent_id", "model_binding_is_identity_key": False,
            "agent_id": str(agent["agent_id"])}


# ── learn / consolidate（LearningCandidate 晋级门）────────────────────────
def propose_candidate(db: Session, *, organization_id: int, agent_id: str,
                      source_event_ids: list[int], proposed_generalization: str,
                      scope: str = "organization", evidence: dict | None = None,
                      evaluation: dict | None = None,
                      candidate_id: str | None = None) -> dict:
    """从个体事件提出候选经验；不自动晋升。源事件必须属于同一 agent 且未被拒。"""
    _agent(db, agent_id, organization_id)
    if scope not in ("agent", "organization", "shared"):
        raise SoulError("unknown candidate scope")
    if not source_event_ids:
        raise SoulError("candidate requires at least one source event")
    rows = db.execute(text("""SELECT event_id,agent_id,verification_status FROM
        factory_memory_events WHERE organization_id=:org AND event_id = ANY(:ids)"""),
        {"org": organization_id, "ids": list(source_event_ids)}).mappings().all()
    if len(rows) != len(set(source_event_ids)):
        raise SoulError("every source event must exist in this organization")
    for row in rows:
        if str(row["agent_id"]) != str(agent_id):
            raise SoulError("source events must belong to the proposing agent")
        if row["verification_status"] in ("rejected", "superseded"):
            raise SoulError("rejected or superseded events cannot be learning sources")
    cid = candidate_id or str(uuid.uuid4())
    db.execute(text("""INSERT INTO factory_learning_candidates
        (candidate_id,organization_id,agent_id,source_event_ids,proposed_generalization,
         scope,evidence,evaluation)
        VALUES (:cid,:org,:aid,:sources,:text,:scope,CAST(:evidence AS jsonb),
                CAST(:evaluation AS jsonb))"""),
        {"cid": cid, "org": organization_id, "aid": agent_id,
         "sources": list(source_event_ids), "text": proposed_generalization[:4000],
         "scope": scope, "evidence": json.dumps(evidence or {}),
         "evaluation": json.dumps(evaluation or {})})
    db.commit()
    return _candidate(db, cid, organization_id)


def _candidate(db: Session, candidate_id: str, organization_id: int) -> dict:
    row = db.execute(text("""SELECT * FROM factory_learning_candidates
        WHERE candidate_id=:cid AND organization_id=:org"""),
        {"cid": candidate_id, "org": organization_id}).mappings().first()
    if not row:
        raise NotFoundError("learning candidate not found in this organization")
    return dict(row)


def get_candidate(db: Session, *, candidate_id: str, organization_id: int) -> dict:
    return _candidate(db, candidate_id, organization_id)


def decide_candidate(db: Session, *, organization_id: int, candidate_id: str,
                     status: str, decided_by: int | None = None,
                     reason: str = "", evaluation: dict | None = None) -> dict:
    """晋级裁决。approved 必须有 passed 的评测结果；shared scope 还要有 validator。"""
    if status not in PROMOTION_STATES:
        raise SoulError(f"unknown promotion status: {status}")
    candidate = _candidate(db, candidate_id, organization_id)
    if candidate["promotion_status"] in ("rejected", "superseded"):
        raise SoulError(f"candidate already terminal: {candidate['promotion_status']}")
    merged = {**candidate["evaluation"], **(evaluation or {})}
    evidence = dict(candidate["evidence"])
    if status == "approved":
        if not merged.get("passed"):
            raise SoulError("approved candidate requires a passed evaluation")
        if candidate["scope"] == "shared" and not evidence.get("validator"):
            raise SoulError("shared-scope candidate requires a named validator")
    db.execute(text("""UPDATE factory_learning_candidates
        SET promotion_status=:status,evaluation=CAST(:evaluation AS jsonb),
            decided_by=:by,decided_reason=:reason,decided_at=now()
        WHERE candidate_id=:cid AND organization_id=:org"""),
        {"status": status, "evaluation": json.dumps(merged), "by": decided_by,
         "reason": reason[:2000], "cid": candidate_id, "org": organization_id})
    db.commit()
    return _candidate(db, candidate_id, organization_id)


def consolidate(db: Session, *, organization_id: int, candidate_id: str,
                decided_by: int | None = None) -> dict:
    """把已批准的候选固化为共享知识；未批准的一律拒绝（不静默晋升）。"""
    candidate = _candidate(db, candidate_id, organization_id)
    if candidate["promotion_status"] != "approved":
        raise SoulError("only approved candidates may be consolidated into shared knowledge")
    promoted, skipped = [], []
    for event_id in candidate["source_event_ids"]:
        event = db.execute(text("""SELECT * FROM factory_memory_events
            WHERE event_id=:eid AND organization_id=:org"""),
            {"eid": event_id, "org": organization_id}).mappings().first()
        if not event:
            skipped.append({"event_id": event_id, "reason": "source event missing"})
            continue
        if event["verification_status"] != "validated":
            # 模型输出未被独立验证：只能作为候选保留，不得进共享知识。
            skipped.append({"event_id": event_id,
                            "reason": f"verification_status={event['verification_status']}"})
            continue
        promoted.append(promote_to_shared(db, organization_id=organization_id,
                                          event_id=event_id, candidate_id=candidate_id))
    if promoted and candidate["scope"] == "shared":
        db.execute(text("""UPDATE factory_learning_candidates SET promotion_status='superseded',
            decided_by=:by,decided_at=COALESCE(decided_at,now()),decided_reason=
            COALESCE(decided_reason,'') || ' | consolidated' WHERE candidate_id=:cid"""),
            {"by": decided_by, "cid": candidate_id})
        db.commit()
    return {"candidate_id": candidate_id, "promoted": promoted, "skipped": skipped,
            "promoted_count": len(promoted), "skipped_count": len(skipped),
            "shared_knowledge_written": len(promoted)}


def list_shared_knowledge(db: Session, *, organization_id: int, limit: int = 20) -> list[dict]:
    """共享知识只包含 validated 且 sensitivity in (organization, shared)。"""
    rows = db.execute(text("""SELECT event_id,agent_id,event_type,source,payload,provenance,
        sensitivity,verification_status,validator,created_at
        FROM factory_memory_events
        WHERE organization_id=:org AND sensitivity IN ('organization','shared')
          AND verification_status='validated'
        ORDER BY event_id DESC LIMIT :limit"""),
        {"org": organization_id, "limit": max(1, min(100, limit))}).mappings().all()
    return [dict(row) for row in rows]


# ── 既有 resident 会话 → 稳定身份的迁移入口 ────────────────────────────────
def ensure_agent_for_resident_session(db: Session, *, organization_id: int,
                                      resident_session_id: str) -> dict:
    """把一个 091 时代的 resident 会话挂到稳定身份上（幂等）。

    091 的 factory_resident_workers 把会话当身份；本函数按 worker_id 派生/复用
    稳定 agent_id，并记录 legacy_session_id 供审计追溯。
    同一 worker_id 多次注册的会话得到同一个 agent_id —— 这正是"跨 session 不变"。
    """
    resident = db.execute(text("""SELECT agent_session_id,user_id,organization_id,worker_id,role
        FROM factory_resident_workers WHERE agent_session_id=:sid"""),
        {"sid": resident_session_id}).mappings().first()
    if not resident:
        raise NotFoundError("resident session not found")
    if int(resident["organization_id"]) != int(organization_id):
        raise NotFoundError("resident session belongs to another organization")
    linked = db.execute(text("""SELECT i.* FROM factory_soul_legacy_sessions l
        JOIN factory_agent_identities i ON i.agent_id=l.agent_id
        WHERE l.agent_session_id=:sid"""),
        {"sid": resident_session_id}).mappings().first()
    if linked:
        return dict(linked)
    agent = create_agent(db, organization_id=organization_id,
                         owner_user_id=int(resident["user_id"]),
                         worker_id=resident["worker_id"], role=resident["role"])
    db.execute(text("""INSERT INTO factory_soul_legacy_sessions
        (agent_session_id,agent_id) VALUES (:sid,:aid)
        ON CONFLICT (agent_session_id) DO NOTHING"""),
        {"sid": resident_session_id, "aid": str(agent["agent_id"])})
    db.commit()
    return _agent(db, str(agent["agent_id"]), organization_id)
