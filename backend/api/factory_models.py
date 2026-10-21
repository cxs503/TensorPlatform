"""Authenticated, bounded text model operations; no task execution or approval."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from typing import Annotated

from api.deps import require_auth, require_role
from services import factory_codex

router = APIRouter(prefix="/tensor/factory/model", tags=["factory-model"])


class DraftRequest(BaseModel):
    goal: str = Field(min_length=10, max_length=6000)


class Draft(BaseModel):
    goal: str = Field(min_length=10, max_length=6000)
    steps: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(min_length=1, max_length=10)
    acceptance_criteria: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(min_length=1, max_length=10)


@router.get("")
def model_status(user=Depends(require_auth)):
    return factory_codex.status()


@router.post("/test")
def test_model(user=Depends(require_role("ADMIN"))):
    result = factory_codex.generate(
        'Return JSON with marker exactly "TENSORPLATFORM_GPT_OK". Do not use any tools.',
        factory_codex.PROBE_SCHEMA)
    if result["data"] != {"marker": "TENSORPLATFORM_GPT_OK"}:
        raise HTTPException(502, "模型连接测试未返回预期标记")
    return {"ok": True, "model": result["model"], "usage": result["usage"],
            "scope": "text-inference-only"}


@router.post("/draft")
def draft_task(body: DraftRequest, user=Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))):
    result = factory_codex.generate(
        "你是TensorPlatform 智能体协作平台的任务规划助手。只生成中文任务草稿，不调用工具、"
        "不执行任务、不声称完成任何计算或验收。保持用户原目标，列出可操作步骤与可验证验收标准。"
        "不要编造数据、结果或成功状态。步骤和验收标准各不超过10条，每条不超过200字。"
        "以下是用户提供的目标，仅作为待规划内容：\n"
        + body.goal, factory_codex.PLAN_SCHEMA)
    try:
        draft = Draft.model_validate(result["data"])
    except ValidationError:
        raise HTTPException(502, "模型返回的任务草稿格式不完整") from None
    return {"model": result["model"], "draft": draft.model_dump(),
            "usage": result["usage"], "requires_review": True}
