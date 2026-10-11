"""
代码生成API
支持代码生成、代码优化、测试生成、代码解释
"""
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.llm_service import get_llm_service
from services.local_code_generator import (
    detect_generated_code_metadata,
    explain_local_code,
    generate_local_code,
    optimize_local_code,
)

router = APIRouter(prefix="/code-generator", tags=["代码生成"])


def _generation_requirement(req: "CodeGenerateRequest") -> str:
    constraints = []
    if req.task:
        constraints.append(f"任务类型必须为：{req.task}")
    if req.framework:
        constraints.append(f"代码框架必须为：{req.framework}")
    if req.algorithm:
        constraints.append(f"算法实现必须为：{req.algorithm}")
    constraint_text = "\n".join(constraints) if constraints else "请根据用户需求自行判断任务类型、框架和算法，不要默认使用随机森林。"
    return (
        f"{constraint_text}\n"
        "生成完整、可运行的Python模型代码；数据路径和关键超参数应可从外部传入，"
        "不要把业务数据硬编码在源码中；训练完成后保存模型并输出指标。\n"
        f"用户需求：{req.requirement.strip()}"
    )


def _clean_generated_code(value: str, language: str) -> str:
    code = str(value or "").strip()
    fenced = re.search(r"```(?:[A-Za-z0-9_+.-]+)?\s*\n(.*?)```", code, re.DOTALL)
    if fenced:
        code = fenced.group(1).strip()
    if not code:
        raise ValueError("LLM没有返回代码")
    if language.lower() == "python":
        compile(code, "llm_generated.py", "exec")
    return code + ("\n" if not code.endswith("\n") else "")

@router.get("/health")
async def code_generator_health():
    llm = get_llm_service()
    provider = getattr(llm, "provider", None)
    if provider:
        return {
            "status": "ready",
            "mode": "llm",
            "provider": provider,
            "api_key_configured": True,
            "message": f"LLM 服务已就绪（{provider}）",
            "supported_tasks": ["regression", "classification", "time_series"],
            "features": ["generate", "optimize", "explain"],
        }
    return {
        "status": "ready",
        "mode": "local_template",
        "provider": "local",
        "api_key_configured": False,
        "message": "当前使用本地模板生成器：无需外部 LLM，可生成平台支持的可运行训练代码",
        "supported_tasks": ["regression", "classification", "time_series"],
        "features": ["generate", "syntax_optimize", "structure_explain"],
    }


# 请求模型
class CodeGenerateRequest(BaseModel):
    requirement: str
    language: str = "python"
    model: Optional[str] = None
    task: Optional[str] = None
    framework: Optional[str] = None
    algorithm: Optional[str] = None


class CodeOptimizeRequest(BaseModel):
    code: str
    language: str = "python"
    model: Optional[str] = None


class CodeExplainRequest(BaseModel):
    code: str
    model: Optional[str] = None


@router.post("/analyze")
async def analyze_generated_code(req: CodeExplainRequest):
    try:
        return {"success": True, "analysis": detect_generated_code_metadata(req.code)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/generate")
async def generate_code(req: CodeGenerateRequest):
    """根据需求生成代码"""
    try:
        llm = get_llm_service()
        provider = getattr(llm, "provider", None)
        warning = None
        if provider:
            try:
                code = _clean_generated_code(
                    llm.generate_code(
                        requirement=_generation_requirement(req),
                        language=req.language,
                        model=req.model,
                    ),
                    req.language,
                )
                return {
                    "success": True,
                    "code": code,
                    "language": req.language,
                    "mode": "llm",
                    "provider": provider,
                    "analysis": detect_generated_code_metadata(code, req.requirement),
                }
            except Exception as exc:
                warning = f"LLM 调用失败，已切换本地模板：{str(exc)[:200]}"
        code = generate_local_code(
            requirement=req.requirement,
            language=req.language,
            task=req.task,
            framework=req.framework,
            algorithm=req.algorithm,
        )
        return {
            "success": True,
            "code": code,
            "language": req.language,
            "mode": "local_template",
            "provider": "local",
            "warning": warning,
            "analysis": detect_generated_code_metadata(code, req.requirement),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/optimize")
async def optimize_code(req: CodeOptimizeRequest):
    """优化代码"""
    try:
        llm = get_llm_service()
        provider = getattr(llm, "provider", None)
        if provider:
            try:
                result = llm.optimize_code(
                    code=req.code, language=req.language, model=req.model
                )
                return {
                    "success": True,
                    "original_code": req.code,
                    "optimized_code": result.get("optimized_code", ""),
                    "explanation": result.get("explanation", ""),
                    "mode": "llm",
                    "provider": provider,
                }
            except Exception:
                pass
        result = optimize_local_code(req.code)
        return {
            "success": True,
            "original_code": req.code,
            "optimized_code": result.get("optimized_code", ""),
            "explanation": result.get("explanation", ""),
            "mode": "local_template",
            "provider": "local",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/explain")
async def explain_code(req: CodeExplainRequest):
    """解释代码"""
    try:
        llm = get_llm_service()
        provider = getattr(llm, "provider", None)
        if provider:
            try:
                explanation = llm.explain_code(code=req.code, model=req.model)
                return {"success": True, "explanation": explanation, "mode": "llm", "provider": provider}
            except Exception:
                pass
        return {"success": True, "explanation": explain_local_code(req.code), "mode": "local_template", "provider": "local"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/models")
async def list_models():
    """Return only providers that are actually configured."""
    llm = get_llm_service()
    provider = getattr(llm, "provider", None)
    if not provider:
        return {
            "mode": "local_template",
            "provider": "local",
            "models": [],
            "message": "未配置外部 LLM；本地模板模式不需要选择大模型",
        }
    return {
        "mode": "llm",
        "provider": provider,
        "models": [{"id": llm.default_model, "name": llm.default_model}],
    }


@router.post("/chat")
async def chat(prompt: str, model: Optional[str] = None):
    """通用对话"""
    try:
        llm = get_llm_service()
        if not getattr(llm, "provider", None):
            raise HTTPException(status_code=503, detail="通用对话需要配置外部 LLM")
        response = llm.chat(prompt=prompt, model=model)
        return {"success": True, "response": response}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
