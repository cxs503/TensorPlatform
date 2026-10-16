"""Agent API — 智谱GLM-4-Flash 驱动的平台只读 Copilot

2026-10-16 架构收敛：移除能源/工业设备等行业工具与模板，平台回归
domain-neutral 共性底座；行业能力按 README 约定作为独立 adapter 交付。
当前智能体只允许读取、分析平台数据并提出人工评审建议。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import List
import json, os, requests, time
from services.llm_trace_context import record_llm_trace

router = APIRouter(prefix="/agent", tags=["Agent"])

ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
# 优先从密钥文件读取(便于轮换 key 而不重建容器/改 env)
try:
    if os.path.exists("/app/.keys/zhipu_api_key"):
        _fk = open("/app/.keys/zhipu_api_key").read().strip()
        if _fk:
            ZHIPU_API_KEY = _fk
except Exception:
    pass
ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
MODEL = os.environ.get("AGENT_MODEL", "glm-4-flash")
AGENT_TOTAL_TIMEOUT_SECONDS = max(
    10, min(int(os.environ.get("AGENT_TOTAL_TIMEOUT_SECONDS", "45")), 90)
)
MAX_CONTEXT_MESSAGES = 12
MAX_CONTEXT_CHARS = 12000
MAX_TOOL_CALLS = 3

# Harness 安全策略：当前智能体只允许读取、分析和提出人工评审建议。
TOOL_POLICIES = {
    "get_platform_stats": "read",
    "list_datasets": "read",
    "list_models": "read",
}

COPILOT_TEMPLATES = {
    "platform_overview": {
        "name": "平台运营日报助手", "description": "只读汇总平台数据集、模型与训练任务概况，生成运营日报草稿。",
        "tools": {"get_platform_stats", "list_datasets", "list_models"},
    },
    "data_analysis": {
        "name": "数据分析助手", "description": "基于平台数据集清单与统计信息回答数据资产问题。",
        "tools": {"get_platform_stats", "list_datasets"},
    },
    "mlops_review": {
        "name": "模型运维助手", "description": "基于平台模型清单与统计信息回答 MLOps 资产问题。",
        "tools": {"get_platform_stats", "list_models"},
    },
}

TOOLS = [
    {"type":"function","function":{"name":"get_platform_stats","description":"平台统计数据: 数据集/模型/训练任务/部署","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"list_datasets","description":"列出平台数据集列表","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"list_models","description":"列出平台模型列表","parameters":{"type":"object","properties":{}}}},
]

SYSTEM_MSG = """你是开放数字化基座的只读平台 Copilot。通用数据科学与算法知识可以基于可靠的基础知识直接回答，并明确说明这不是平台实时数据。涉及平台数据、模型或训练任务时，必须使用工具并给出“证据：”说明数据来源、时间范围和工具。不得虚构实时数据、指标或控制执行结果。只能给人工评审建议，严禁控制下发。"""


def _trim_conversation(messages: List[dict]) -> List[dict]:
    """Keep recent turns under a hard character budget to bound LLM latency."""
    selected = []
    used_chars = 0
    for item in reversed(messages or []):
        role = str(item.get("role") or "user")
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        remaining = MAX_CONTEXT_CHARS - used_chars
        if remaining <= 0:
            break
        content = content[-remaining:]
        selected.append({"role": role, "content": content})
        used_chars += len(content)
        if len(selected) >= MAX_CONTEXT_MESSAGES:
            break
    return list(reversed(selected))


def _select_tools(user_message: str, template: dict) -> list:
    """Only expose tools for requests that need live platform evidence."""
    value = str(user_message or "").lower()
    tool_keywords = {
        "get_platform_stats": ("平台有多少", "平台统计", "系统统计", "当前平台"),
        "list_datasets": ("数据集", "数据目录", "列出数据"),
        "list_models": ("模型列表", "列出模型", "训练任务", "平台模型"),
    }
    requested = {
        name for name, keywords in tool_keywords.items()
        if any(keyword in value for keyword in keywords)
    }
    if not requested:
        return []
    allowed = template["tools"]
    return [
        tool for tool in TOOLS
        if tool["function"]["name"] in requested
        and tool["function"]["name"] in allowed
    ]


def _remaining_seconds(deadline: float, cap: float = 30.0) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 1:
        raise TimeoutError("AI智能体处理超过总时间限制")
    return max(1.0, min(cap, remaining))


class ChatReq(BaseModel):
    messages: List[dict]
    template: str = "platform_overview"


@router.get("/templates")
def templates():
    return {"items":[{"key":key, "name":value["name"], "description":value["description"],
                       "tools":sorted(value["tools"]), "control":"denied"} for key, value in COPILOT_TEMPLATES.items()]}


@router.get("/tools")
def tools():
    return {"total":len(TOOLS),"tools":[{"name":t["function"]["name"],"access":TOOL_POLICIES[t["function"]["name"]]} for t in TOOLS],"model":MODEL,"control":"denied"}


def execute_tool(
    name: str,
    args: dict,
    auth_header: str = "",
    timeout_seconds: float = 5.0,
) -> str:
    base = "http://127.0.0.1:8001/api/v1"
    headers = {"Authorization": auth_header} if auth_header else {}
    try:
        if name == "get_platform_stats":
            r = requests.get(
                f"{base}/dashboard/statistics", headers=headers,
                timeout=timeout_seconds,
            )
            d = r.json()
            return json.dumps({
                "datasets": d["datasets"]["total"],
                "models": d["models"]["total"],
                "training": d["training_jobs"]["completed"],
                "deployments": d.get("deployments", {}).get("total", 0),
            }, ensure_ascii=False)
        elif name == "list_datasets":
            r = requests.get(
                f"{base}/datasets", headers=headers, timeout=timeout_seconds
            )
            d = r.json()
            items = d.get("items", [])[:5]
            return json.dumps({"total": d.get("total", 0), "datasets": [{"id": i["id"], "name": i["name"], "rows": i.get("row_count", "?")} for i in items]}, ensure_ascii=False)
        elif name == "list_models":
            r = requests.get(
                f"{base}/models", headers=headers, timeout=timeout_seconds
            )
            d = r.json()
            items = d.get("items", [])[:5]
            return json.dumps({"total": d.get("total", 0), "models": [{"id": i["id"], "name": i["name"]} for i in items]}, ensure_ascii=False)
        return json.dumps({"result":"ok"})
    except Exception as e:
        return json.dumps({"error":str(e)})


def _call_zhipu(
    messages: list,
    tools: list = None,
    temperature: float = 0.3,
    timeout_seconds: float = 30.0,
    operation: str = "agent_chat",
) -> dict:
    """调用智谱GLM-4-Flash"""
    if not ZHIPU_API_KEY:
        raise ValueError("未配置ZHIPU_API_KEY")

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    started_at = time.perf_counter()
    try:
        resp = requests.post(
            f"{ZHIPU_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout_seconds,
        )
        resp.raise_for_status()
        result = resp.json()
        record_llm_trace(
            provider="zhipu",
            model=MODEL,
            operation=operation,
            status="success",
            latency_ms=(time.perf_counter() - started_at) * 1000,
            usage=result.get("usage") if isinstance(result, dict) else None,
        )
        return result
    except Exception as exc:
        record_llm_trace(
            provider="zhipu",
            model=MODEL,
            operation=operation,
            status="error",
            latency_ms=(time.perf_counter() - started_at) * 1000,
            error_message=str(exc),
        )
        raise


@router.post("/chat")
def chat(req: ChatReq, request: Request):
    started_at = time.monotonic()
    deadline = started_at + AGENT_TOTAL_TIMEOUT_SECONDS
    user_msg = req.messages[-1].get("content","") if req.messages else ""

    if not ZHIPU_API_KEY:
        raise HTTPException(status_code=503, detail="AI智能体未配置智谱API密钥")

    try:
        template = COPILOT_TEMPLATES.get(req.template, COPILOT_TEMPLATES["platform_overview"])
        messages = [{"role":"system","content":SYSTEM_MSG + "\n当前角色：" + template["name"] + "。职责：" + template["description"]}]
        trimmed_messages = _trim_conversation(req.messages)
        messages.extend(trimmed_messages)

        # 通用知识问题不暴露工具，避免无意义的规划+总结两次模型调用。
        allowed_tools = _select_tools(user_msg, template)
        mode = "tool" if allowed_tools else "general"
        d = _call_zhipu(
            messages,
            tools=allowed_tools or None,
            timeout_seconds=_remaining_seconds(deadline),
            operation="agent_tool_planning" if allowed_tools else "agent_general_chat",
        )
        msg = d["choices"][0]["message"]

        # 处理工具调用
        if msg.get("tool_calls"):
            trace = []
            messages.append({"role":"assistant","content":msg.get("content") or "", "tool_calls": msg["tool_calls"]})
            auth_header = request.headers.get("authorization", "")
            for index, tc in enumerate(msg["tool_calls"]):
                fn = tc["function"]
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (TypeError, ValueError):
                    args = {}
                allowed = fn["name"] in template["tools"]
                if index >= MAX_TOOL_CALLS:
                    result = json.dumps({"error":"tool_call_limit_exceeded"})
                    allowed = False
                elif allowed:
                    result = execute_tool(
                        fn["name"], args,
                        auth_header=auth_header,
                        timeout_seconds=_remaining_seconds(deadline, cap=5.0),
                    )
                else:
                    result = json.dumps({"error":"tool_not_allowed_for_template"})
                trace.append({
                    "tool":fn["name"],
                    "access":TOOL_POLICIES.get(fn["name"],"denied") if allowed else "denied",
                    "arguments":args,
                })
                messages.append({"role":"tool","tool_call_id":tc["id"],"content":result})

            # 带工具结果第二次调用
            d2 = _call_zhipu(
                messages,
                temperature=0.3,
                timeout_seconds=_remaining_seconds(deadline),
                operation="agent_tool_answer",
            )
            return {
                "reply": d2["choices"][0]["message"]["content"],
                "model": MODEL,
                "template": req.template,
                "tool_trace": trace,
                "control": "denied",
                "mode": mode,
                "context_messages": len(trimmed_messages),
                "elapsed_ms": round((time.monotonic() - started_at) * 1000, 2),
            }

        return {
            "reply": msg.get("content", ""),
            "model": MODEL,
            "template": req.template,
            "tool_trace": [],
            "control": "denied",
            "mode": mode,
            "context_messages": len(trimmed_messages),
            "elapsed_ms": round((time.monotonic() - started_at) * 1000, 2),
        }
    except HTTPException:
        raise
    except (requests.Timeout, TimeoutError) as exc:
        raise HTTPException(
            status_code=504,
            detail="AI智能体响应超时，请缩短问题或稍后重试",
        ) from exc
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail="智谱服务暂时不可用，请稍后重试",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"AI智能体处理失败：{str(exc)[:200]}",
        ) from exc
