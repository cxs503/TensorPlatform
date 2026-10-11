"""Real LLMOps management: provider status, call traces, prompts and tenant RAG docs."""
from __future__ import annotations

import json
from typing import Any, Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from api.deps import require_auth
from api.tenant_access import is_admin, require_organization
from database.config import get_db
from services.llm_service import get_llm_service


router = APIRouter(prefix="/llmops", tags=["LLMOps"])


class PromptCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    template: str = Field(min_length=1, max_length=50000)
    variables: list[str] = Field(default_factory=list)


class PromptTestReq(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


class RagDocumentReq(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=200000)
    source: str = Field(default="manual", max_length=255)


class RagSearchReq(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


def _trace_scope(current_user: dict, alias: str = "") -> tuple[str, dict]:
    prefix = f"{alias}." if alias else ""
    if is_admin(current_user):
        return "", {}
    return f"WHERE {prefix}organization_id=:organization_id", {
        "organization_id": require_organization(current_user)
    }


def _prompt_row(row) -> dict:
    value = dict(row)
    if isinstance(value.get("variables"), str):
        value["variables"] = json.loads(value["variables"])
    return value


@router.get("/provider")
def provider_status(current_user: dict = Depends(require_auth)):
    del current_user
    service = get_llm_service()
    provider = getattr(service, "provider", None)
    return {
        "configured": bool(provider),
        "provider": provider,
        "model": getattr(service, "default_model", None),
        "message": "外部大模型连接已配置" if provider else "尚未配置外部大模型密钥",
    }


@router.get("/overview")
def overview(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    where, params = _trace_scope(current_user)
    condition = f"{where} {'AND' if where else 'WHERE'} created_at >= NOW() - make_interval(days => :days)"
    params["days"] = days
    totals = db.execute(text(f"""
        SELECT COUNT(*) AS total_calls,
               COUNT(*) FILTER (WHERE status='success') AS success_calls,
               COUNT(*) FILTER (WHERE status<>'success') AS failed_calls,
               COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM llm_call_traces {condition}
    """), params).mappings().first()
    sources = db.execute(text(f"""
        SELECT source, COUNT(*) AS calls,
               COUNT(*) FILTER (WHERE status='success') AS success_calls,
               COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM llm_call_traces {condition}
        GROUP BY source ORDER BY calls DESC
    """), params).mappings().all()
    daily = db.execute(text(f"""
        SELECT DATE(created_at) AS day, COUNT(*) AS calls,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM llm_call_traces {condition}
        GROUP BY DATE(created_at) ORDER BY day
    """), params).mappings().all()
    prompt_where, prompt_params = _trace_scope(current_user)
    rag_where, rag_params = _trace_scope(current_user)
    prompt_count = db.execute(text(f"SELECT COUNT(*) FROM llm_prompt_templates {prompt_where}"), prompt_params).scalar()
    rag_count = db.execute(text(f"SELECT COUNT(*) FROM llm_rag_documents {rag_where}"), rag_params).scalar()
    return {
        **dict(totals or {}), "days": days,
        "success_rate": round((int(totals["success_calls"] or 0) / max(int(totals["total_calls"] or 0), 1)) * 100, 2) if totals else 0,
        "sources": [dict(row) for row in sources],
        "daily": [dict(row) for row in daily],
        "prompt_versions": int(prompt_count or 0), "rag_documents": int(rag_count or 0),
    }


@router.get("/calls")
def list_calls(
    limit: int = Query(100, ge=1, le=500),
    source: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    clauses, params = [], {"limit": limit}
    if not is_admin(current_user):
        clauses.append("organization_id=:organization_id")
        params["organization_id"] = require_organization(current_user)
    if source:
        clauses.append("source=:source"); params["source"] = source
    if status:
        clauses.append("status=:status"); params["status"] = status
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = db.execute(text(f"""
        SELECT id, organization_id, username, provider, model, source, operation,
               status, prompt_tokens, completion_tokens, total_tokens,
               latency_ms, error_message, created_at
        FROM llm_call_traces {where}
        ORDER BY created_at DESC, id DESC LIMIT :limit
    """), params).mappings().all()
    return {"total": len(rows), "items": [dict(row) for row in rows]}


@router.get("/prompts")
def list_prompts(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    where, params = _trace_scope(current_user)
    rows = db.execute(text(f"""
        SELECT id, organization_id, name, description, template, variables,
               version, is_active, created_by, created_at
        FROM llm_prompt_templates {where}
        ORDER BY name, version DESC, id DESC
    """), params).mappings().all()
    return {"total": len(rows), "items": [_prompt_row(row) for row in rows]}


@router.post("/prompts")
def create_prompt(
    req: PromptCreateReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    organization_id = require_organization(current_user)
    variables = [str(item).strip() for item in req.variables if str(item).strip()]
    version = db.execute(text("""
        SELECT COALESCE(MAX(version), 0) + 1 FROM llm_prompt_templates
        WHERE organization_id=:organization_id AND name=:name
    """), {"organization_id": organization_id, "name": req.name.strip()}).scalar()
    row = db.execute(text("""
        INSERT INTO llm_prompt_templates
            (organization_id, name, description, template, variables, version, created_by, created_at)
        VALUES (:organization_id, :name, :description, :template,
                CAST(:variables AS JSONB), :version, :created_by, NOW())
        RETURNING id, organization_id, name, description, template, variables,
                  version, is_active, created_by, created_at
    """), {
        "organization_id": organization_id, "name": req.name.strip(),
        "description": req.description.strip(), "template": req.template,
        "variables": json.dumps(variables, ensure_ascii=False), "version": int(version),
        "created_by": current_user.get("id"),
    }).mappings().first()
    db.commit()
    return _prompt_row(row)


def _get_prompt(db: Session, prompt_id: int, current_user: dict):
    row = db.execute(text("SELECT * FROM llm_prompt_templates WHERE id=:id"), {"id": prompt_id}).mappings().first()
    if not row or (not is_admin(current_user) and row["organization_id"] != require_organization(current_user)):
        raise HTTPException(status_code=404, detail="Prompt不存在")
    return row


@router.post("/prompts/{prompt_id}/test")
async def test_prompt(
    prompt_id: int,
    req: PromptTestReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    row = _get_prompt(db, prompt_id, current_user)
    rendered = str(row["template"])
    missing = []
    variables = row["variables"] if isinstance(row["variables"], list) else json.loads(row["variables"] or "[]")
    for name in variables:
        marker = "{" + str(name) + "}"
        if str(name) not in req.variables:
            missing.append(str(name))
        else:
            rendered = rendered.replace(marker, str(req.variables[str(name)]))
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少Prompt变量：{', '.join(missing)}")
    service = get_llm_service()
    if not getattr(service, "provider", None):
        raise HTTPException(status_code=503, detail="外部大模型未配置")
    try:
        response = await run_in_threadpool(
            service.chat, rendered, None,
            trace_operation="prompt_test", max_tokens=1200, temperature=0.2,
        )
        return {"rendered_prompt": rendered, "response": response, "provider": service.provider, "model": service.default_model}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Prompt测试失败：{str(exc)[:300]}") from exc


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: int, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    _get_prompt(db, prompt_id, current_user)
    db.execute(text("DELETE FROM llm_prompt_templates WHERE id=:id"), {"id": prompt_id})
    db.commit()
    return {"message": "Prompt版本已删除"}


@router.get("/rag/documents")
def list_rag_documents(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    where, params = _trace_scope(current_user)
    rows = db.execute(text(f"""
        SELECT id, organization_id, name, source, LENGTH(content) AS characters,
               metadata, created_by, created_at
        FROM llm_rag_documents {where} ORDER BY created_at DESC, id DESC
    """), params).mappings().all()
    return {"total": len(rows), "items": [dict(row) for row in rows]}


@router.post("/rag/documents")
def add_rag_document(req: RagDocumentReq, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    organization_id = require_organization(current_user)
    row = db.execute(text("""
        INSERT INTO llm_rag_documents
            (organization_id, name, source, content, metadata, created_by, created_at)
        VALUES (:organization_id, :name, :source, :content, '{}'::jsonb, :created_by, NOW())
        RETURNING id, organization_id, name, source, LENGTH(content) AS characters,
                  metadata, created_by, created_at
    """), {
        "organization_id": organization_id, "name": req.name.strip(),
        "source": req.source.strip() or "manual", "content": req.content,
        "created_by": current_user.get("id"),
    }).mappings().first()
    db.commit()
    return dict(row)


@router.delete("/rag/documents/{document_id}")
def delete_rag_document(document_id: int, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    row = db.execute(text("SELECT organization_id FROM llm_rag_documents WHERE id=:id"), {"id": document_id}).first()
    if not row or (not is_admin(current_user) and row[0] != require_organization(current_user)):
        raise HTTPException(status_code=404, detail="知识库文档不存在")
    db.execute(text("DELETE FROM llm_rag_documents WHERE id=:id"), {"id": document_id})
    db.commit()
    return {"message": "知识库文档已删除"}


def _embed(value: str, dim: int = 384) -> np.ndarray:
    vector = np.zeros(dim)
    lowered = value.lower()
    for index, character in enumerate(lowered):
        vector[hash(character) % dim] += 1
        if index + 1 < len(lowered):
            vector[hash(lowered[index:index + 2]) % dim] += 0.5
    norm = np.linalg.norm(vector)
    return vector / (norm + 1e-8)


@router.post("/rag/search")
def search_rag(req: RagSearchReq, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    where, params = _trace_scope(current_user)
    rows = db.execute(text(f"SELECT id, name, source, content FROM llm_rag_documents {where} LIMIT 1000"), params).mappings().all()
    query_vector = _embed(req.query)
    ranked = []
    for row in rows:
        content = str(row["content"])
        score = float(np.dot(query_vector, _embed(content[:20000])))
        ranked.append({
            "id": row["id"], "name": row["name"], "source": row["source"],
            "score": round(score, 4), "preview": content[:500],
        })
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return {"query": req.query, "items": ranked[:req.top_k]}
