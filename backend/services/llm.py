"""OpenAI-compatible LLM client for the research console.

Backends onto the self-deployed DeepSeek service (free, internal network):
backend container -> http://192.0.2.20:13001/v1 (controller relay)
-> ssh -L -> jump host -> new-api. All configured via env, never hardcoded.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_ATTEMPTS = 3


def llm_base_url() -> str:
    return os.environ.get("LLM_BASE_URL", "").rstrip("/")


def llm_api_key() -> str:
    return os.environ.get("LLM_API_KEY", "")


def llm_model() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def llm_configured() -> bool:
    return bool(llm_base_url() and llm_api_key())


def llm_chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """Chat completion with bounded retries.

    The upstream reasoning model occasionally spirals into very long thinking
    (0 bytes until cutoff) — per-attempt timeout + retry lands on a fast path.
    Empty content with finish_reason=length (reasoning ate the budget) is retried
    once with a doubled budget.
    """
    if not llm_configured():
        raise RuntimeError("LLM is not configured (LLM_BASE_URL / LLM_API_KEY env missing)")
    timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    attempts = int(os.environ.get("LLM_ATTEMPTS", DEFAULT_ATTEMPTS))

    errors: list[str] = []
    budget = max_tokens
    for attempt in range(attempts + 1):  # +1 room for the budget-doubling retry
        body = {
            "model": model or llm_model(),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": budget,
        }
        t0 = time.perf_counter()
        try:
            resp = httpx.post(
                f"{llm_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {llm_api_key()}"},
                json=body,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            errors.append(f"attempt {attempt + 1}: {type(exc).__name__}: {exc}"[:200])
            continue
        if resp.status_code != 200:
            errors.append(f"attempt {attempt + 1}: HTTP {resp.status_code}: {resp.text[:150]}"[:200])
            continue
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = (message.get("content") or "").strip()
        if not content:
            if choice.get("finish_reason") == "length" and budget == max_tokens:
                # reasoning consumed the whole budget — one retry with 2x tokens
                budget *= 2
                errors.append(f"attempt {attempt + 1}: empty content (reasoning hit max_tokens)")
                continue
            errors.append(f"attempt {attempt + 1}: empty content, finish={choice.get('finish_reason')}")
            continue
        return {
            "model": data.get("model", body["model"]),
            "content": content,
            "finish_reason": choice.get("finish_reason"),
            "usage": data.get("usage") or {},
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "attempts": attempt + 1,
        }
    raise RuntimeError("LLM call failed after retries: " + " | ".join(errors[-3:]))


def llm_probe() -> dict[str, Any]:
    """Cheap live probe for the health/status endpoint (lists models)."""
    if not llm_configured():
        return {"configured": False}
    t0 = time.perf_counter()
    try:
        resp = httpx.get(
            f"{llm_base_url()}/models",
            headers={"Authorization": f"Bearer {llm_api_key()}"},
            timeout=10.0,
        )
        models = (
            [m.get("id") for m in resp.json().get("data", [])]
            if resp.status_code == 200
            else []
        )
        return {
            "configured": True,
            "ok": resp.status_code == 200,
            "status_code": resp.status_code,
            "models": models,
            "model": llm_model(),
            "model_available": llm_model() in models,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }
    except httpx.HTTPError as exc:
        return {
            "configured": True,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }
