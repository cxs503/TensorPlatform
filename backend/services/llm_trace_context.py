"""Request context and privacy-preserving persistence for LLM call traces."""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy import text

from database.config import SessionLocal


_context: ContextVar[dict[str, Any]] = ContextVar("llm_request_context", default={})


def set_llm_request_context(value: dict[str, Any]) -> Token:
    return _context.set(dict(value or {}))


def reset_llm_request_context(token: Token) -> None:
    _context.reset(token)


def get_llm_request_context() -> dict[str, Any]:
    return dict(_context.get() or {})


def _source_label(path: str) -> str:
    mappings = (
        ("/workspace/ai-assistant", "沙箱AI助手"),
        ("/code-generator", "代码生成器"),
        ("/intelligent-analysis", "智能数据分析"),
        ("/llmops/prompts", "Prompt测试"),
        ("/agent/chat", "AI智能体"),
    )
    for prefix, label in mappings:
        if prefix in path:
            return label
    return "平台后台调用" if not path else path[:128]


def record_llm_trace(
    *,
    provider: str,
    model: str,
    operation: str,
    status: str,
    latency_ms: float,
    usage: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    """Best-effort trace. Prompt and response content are deliberately never stored."""
    context = get_llm_request_context()
    db = SessionLocal()
    try:
        user_id = context.get("user_id")
        organization_id = None
        if user_id is not None:
            organization_id = db.execute(
                text("SELECT organization_id FROM users WHERE id=:id"),
                {"id": int(user_id)},
            ).scalar()
        usage = usage or {}
        db.execute(text("""
            INSERT INTO llm_call_traces
                (organization_id, user_id, username, provider, model, source,
                 operation, status, prompt_tokens, completion_tokens, total_tokens,
                 latency_ms, error_message, request_path, created_at)
            VALUES
                (:organization_id, :user_id, :username, :provider, :model, :source,
                 :operation, :status, :prompt_tokens, :completion_tokens, :total_tokens,
                 :latency_ms, :error_message, :request_path, NOW())
        """), {
            "organization_id": organization_id,
            "user_id": int(user_id) if user_id is not None else None,
            "username": str(context.get("username") or "system")[:128],
            "provider": str(provider or "unknown")[:64],
            "model": str(model or "unknown")[:255],
            "source": _source_label(str(context.get("path") or "")),
            "operation": str(operation or "chat")[:64],
            "status": str(status or "unknown")[:32],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "latency_ms": round(float(latency_ms or 0), 3),
            "error_message": str(error_message or "")[:500] or None,
            "request_path": str(context.get("path") or "")[:500] or None,
        })
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
