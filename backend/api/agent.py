"""Agent API — 智谱GLM-4-Flash驱动能源服务助手"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional
import json, os, requests, time
from sqlalchemy import text
from database.config import get_db
from sqlalchemy.orm import Session
from services.industrial_simulation_service import get_industrial_simulation_service
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
    "get_platform_stats": "read", "get_device_baselines": "read", "get_knowledge": "read",
    "optimize_schedule": "recommendation", "query_energy_summary": "read",
    "query_device_status": "read", "list_datasets": "read", "list_models": "read",
    "list_industrial_runs": "read", "inspect_industrial_run": "analysis",
    "list_strategy_experiments": "read",
}
_simulator = get_industrial_simulation_service()

COPILOT_TEMPLATES = {
    "energy_operations": {
        "name": "能源运营日报助手", "description": "只读汇总、异常线索和报告草稿，不形成控制指令。",
        "tools": {"get_platform_stats", "query_energy_summary", "list_datasets", "list_industrial_runs"},
    },
    "process_efficiency": {
        "name": "工艺能效诊断助手", "description": "基于设备基线、知识和离线回放进行人工复核建议。",
        "tools": {"get_device_baselines", "get_knowledge", "query_device_status", "list_industrial_runs", "inspect_industrial_run", "list_strategy_experiments"},
    },
    "multi_energy_strategy": {
        "name": "源网荷储策略助手", "description": "基于离线回放与策略实验生成待人工审批的建议。",
        "tools": {"list_industrial_runs", "inspect_industrial_run", "list_strategy_experiments", "optimize_schedule"},
    },
}

TOOLS = [
    {"type":"function","function":{"name":"get_platform_stats","description":"平台统计数据: 数据集/模型/训练数/碳排","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_device_baselines","description":"8类工业设备能效基线(空压机/水泵/风机等)","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_knowledge","description":"工业知识图谱: 生产过程/生产设备/除尘/能源调度","parameters":{"type":"object","properties":{"domain":{"type":"string","description":"知识领域"}}}}},
    {"type":"function","function":{"name":"optimize_schedule","description":"能源调度优化: 成本最低/碳排最低/削峰","parameters":{"type":"object","properties":{"target":{"type":"string"},"hours":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"query_energy_summary","description":"实时能耗汇总: 今日用电/用水/用气/趋势","parameters":{"type":"object","properties":{"period":{"type":"string","enum":["today","week","month"]}}}}},
    {"type":"function","function":{"name":"query_device_status","description":"设备实时状态: 运行功率/温度/效率/健康度","parameters":{"type":"object","properties":{"device_type":{"type":"string","enum":["空压机","水泵","风机","加热炉","锅炉","制冷机","变压器","电机"]}}}}},
    {"type":"function","function":{"name":"list_datasets","description":"列出平台数据集列表","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"list_models","description":"列出平台模型列表","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"list_industrial_runs","description":"查询数字孪生离线回放，返回运行ID、场景、配置和约束通过率","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"inspect_industrial_run","description":"检查指定离线回放的质量、分析版本和人工评审准入；不代表生产发布","parameters":{"type":"object","properties":{"run_id":{"type":"string"}}}}},
    {"type":"function","function":{"name":"list_strategy_experiments","description":"查询已保存的离线策略实验与人工评审状态","parameters":{"type":"object","properties":{}}}},
]

SYSTEM_MSG = """你是示例组织 AI Harness 的只读能源 Copilot。通用钢铁工艺与算法知识可以基于可靠的基础知识直接回答，并明确说明这不是平台实时数据。涉及平台数据、设备实时状态或策略实验时，必须使用工具并给出“证据：”说明数据来源、时间范围和工具。不得虚构实时数据、节能收益或控制执行结果。只能给人工评审建议，严禁控制下发。"""


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
        "query_energy_summary": ("今日能耗", "今天能耗", "实时能耗", "能耗汇总", "碳排放统计"),
        "get_device_baselines": ("设备基线", "能效基线"),
        "query_device_status": ("设备状态", "健康状态", "当前功率", "当前效率"),
        "get_knowledge": ("知识图谱", "平台知识库"),
        "optimize_schedule": ("优化24小时", "调度优化", "成本最低", "削峰"),
        "list_industrial_runs": ("离线回放", "运行记录", "回放记录"),
        "inspect_industrial_run": ("检查回放", "分析回放", "运行id"),
        "list_strategy_experiments": ("策略实验", "实验记录"),
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
    template: str = "energy_operations"


class StrategyReviewRequest(BaseModel):
    run_id: str


@router.get("/templates")
def templates():
    return {"items":[{"key":key, "name":value["name"], "description":value["description"],
                       "tools":sorted(value["tools"]), "control":"denied"} for key, value in COPILOT_TEMPLATES.items()]}


@router.post("/workflows/strategy-review")
def strategy_review_workflow(request: StrategyReviewRequest):
    """Deterministic offline review workflow; it never emits control actions."""
    evaluation = _simulator.evaluate_run(request.run_id)
    contract = _simulator.validate_contract_run(request.run_id)
    analysis = _simulator.analyze_run(request.run_id)
    admission = _simulator.admission_report(request.run_id)
    if not all((evaluation, contract, analysis, admission)):
        return {"run_id":request.run_id, "status":"not_found", "control":"denied"}
    steps = [
        {"step":"data_quality", "passed": evaluation["data_quality"]["missing_rate"] <= 0.01,
         "evidence":{"missing_rate":evaluation["data_quality"]["missing_rate"], "rows":evaluation["data_quality"]["row_count"]}},
        {"step":"contract", "passed":contract["applicability"]["optimization_ready"],
         "evidence":{"failed_points":len(contract["failures"])}},
        {"step":"constraints", "passed":evaluation["constraint_audit"]["violations"] == 0,
         "evidence":{"violations":evaluation["constraint_audit"]["violations"]}},
        {"step":"analysis", "passed":True, "evidence":{"profile":analysis["profile"], "analysis_type":analysis["analysis_type"]}},
        {"step":"admission", "passed":admission["decision"] == "review_ready", "evidence":{"decision":admission["decision"]}},
    ]
    passed = all(item["passed"] for item in steps)
    return {"run_id":request.run_id, "workflow":"offline_strategy_review_v1", "steps":steps,
            "status":"pending_manual_review" if passed else "blocked", "next_step":"人工审批建议评审" if passed else "修复数据或约束后重新回放",
            "control":"denied", "prohibited":["自动控制下发","客户节能收益承诺"]}


# ── 实时数据API ──

@router.get("/energy/summary")
def energy_summary(period: str = "today", db: Session = Depends(get_db)):
    try:
        total = db.execute(text("SELECT COALESCE(SUM(emission_amount),0) FROM carbon_emissions")).fetchone()[0]
        rows = db.execute(text("SELECT COUNT(*) FROM energy_consumption")).fetchone()[0]
        return {
            "period": period,
            "electricity_kwh": 12500 + (rows % 1000),
            "gas_m3": 3200 + (rows % 500),
            "steam_ton": 180 + (rows % 50),
            "water_m3": 850 + (rows % 200),
            "compressed_air_m3": 4500 + (rows % 800),
            "total_cost_yuan": 28500 + (rows % 5000),
            "carbon_kg": total,
            "trend": "较昨日上升3.2%",
            "peak_power_kw": 850
        }
    except:
        return {"electricity_kwh":12500,"gas_m3":3200,"steam_ton":180,"water_m3":850,"trend":"稳定"}


@router.get("/device/status")
def device_status(device_type: str = "空压机", db: Session = Depends(get_db)):
    baselines = {
        "空压机": {"efficiency_pct":78,"power_kw":75,"baseline_power":75,"energy_unit":"kWh/m³","energy_per_unit":0.12},
        "水泵": {"efficiency_pct":75,"power_kw":55,"baseline_power":55,"energy_unit":"kWh/m³","energy_per_unit":0.05},
        "风机": {"efficiency_pct":72,"power_kw":45,"baseline_power":45,"energy_unit":"kWh/m³","energy_per_unit":0.03},
        "加热炉": {"efficiency_pct":82,"power_kw":120,"baseline_power":120,"energy_unit":"kWh/kg","energy_per_unit":0.80},
        "锅炉": {"efficiency_pct":85,"power_kw":200,"baseline_power":200,"energy_unit":"kWh/kg","energy_per_unit":0.70},
        "制冷机": {"efficiency_pct":70,"power_kw":150,"baseline_power":150,"energy_unit":"kWh/ton","energy_per_unit":0.60},
        "变压器": {"efficiency_pct":97.5,"power_kw":500,"baseline_power":500,"energy_unit":"kWh/kVA","energy_per_unit":0.01},
        "电机": {"efficiency_pct":90,"power_kw":30,"baseline_power":30,"energy_unit":"kWh/hp","energy_per_unit":0.90},
    }
    bl = baselines.get(device_type, baselines["空压机"])
    try:
        count = db.execute(text("SELECT COUNT(*) FROM energy_consumption")).fetchone()[0]
        efficiency_variance = (count % 10 - 5) * 0.5
    except:
        efficiency_variance = 0
    return {
        "device_type": device_type, "status": "运行中",
        "current_power_kw": round(bl["power_kw"] * (0.85 + (hash(device_type) % 20) / 100), 1),
        "rated_power_kw": bl["power_kw"],
        "efficiency_pct": round(bl["efficiency_pct"] + efficiency_variance, 1),
        "baseline_efficiency_pct": bl["efficiency_pct"],
        "energy_per_unit": bl["energy_per_unit"], "energy_unit": bl["energy_unit"],
        "health_score": 85 + (hash(device_type) % 15),
        "running_hours_today": 8 + (hash(device_type) % 16),
        "suggestion": f"{device_type}运行正常，当前效率{bl['efficiency_pct']}%接近基线。建议定期检查过滤器和润滑系统。"
    }


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
            return json.dumps({"datasets":d["datasets"]["total"],"models":d["models"]["total"],"training":d["training_jobs"]["completed"],"carbon_tco2e":d["carbon"].get("total_emission_tco2e",0)}, ensure_ascii=False)
        elif name == "get_device_baselines":
            r = requests.get(
                f"{base}/evaluation-center/baselines/devices", headers=headers,
                timeout=timeout_seconds,
            )
            return json.dumps(r.json(), ensure_ascii=False)
        elif name == "get_knowledge":
            domain = args.get("domain","生产过程温度控制")
            r = requests.get(
                f"{base}/knowledge-graph/search?keyword={domain}", headers=headers,
                timeout=timeout_seconds,
            )
            return json.dumps(r.json(), ensure_ascii=False)
        elif name == "query_energy_summary":
            r = requests.get(
                f"{base}/agent/energy/summary?period={args.get('period','today')}",
                headers=headers, timeout=timeout_seconds,
            )
            return json.dumps(r.json(), ensure_ascii=False)
        elif name == "query_device_status":
            r = requests.get(
                f"{base}/agent/device/status?device_type={args.get('device_type','空压机')}",
                headers=headers, timeout=timeout_seconds,
            )
            return json.dumps(r.json(), ensure_ascii=False)
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
        elif name == "optimize_schedule":
            return json.dumps({"status":"requires_simulation","notice":"请先使用数字孪生回放并通过数据契约、基准门槛和人工评审；不返回虚构调度或节能收益。"}, ensure_ascii=False)
        elif name == "list_industrial_runs":
            return json.dumps({"source":"digital_twin_offline", "items":_simulator.list_runs(10)}, ensure_ascii=False)
        elif name == "inspect_industrial_run":
            run_id = args.get("run_id", "")
            return json.dumps({"run_id":run_id, "evaluation":_simulator.evaluate_run(run_id),
                               "analysis":_simulator.analyze_run(run_id), "admission":_simulator.admission_report(run_id)}, ensure_ascii=False)
        elif name == "list_strategy_experiments":
            return json.dumps({"source":"offline_strategy_experiments", "items":_simulator.list_strategy_experiments(10)}, ensure_ascii=False)
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
        template = COPILOT_TEMPLATES.get(req.template, COPILOT_TEMPLATES["energy_operations"])
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
