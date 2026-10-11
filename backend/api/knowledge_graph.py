"""
工业知识图谱 API — 将工艺经验转化为可查询的结构化知识
"""
from fastapi import APIRouter, HTTPException
from services.knowledge_graph_service import search_knowledge, add_knowledge, get_domains, match_knowledge

router = APIRouter(prefix="/knowledge-graph", tags=["知识图谱"])


@router.get("/domains")
def list_domains():
    """获取知识领域列表"""
    return {"total": len(get_domains()), "domains": get_domains()}


@router.get("/search")
def search(domain: str = None, keyword: str = None):
    """搜索工业知识规则"""
    results = search_knowledge(domain=domain, keyword=keyword)
    return {"total": len(results), "results": results}


@router.post("/rules")
def add_rule(req: dict):
    """添加工业知识规则"""
    required = ["domain", "rule", "cause", "action"]
    for k in required:
        if k not in req:
            raise HTTPException(status_code=400, detail=f"缺少参数: {k}")
    result = add_knowledge(
        domain=req["domain"],
        rule=req["rule"],
        cause=req["cause"],
        action=req["action"],
        confidence=req.get("confidence", 0.8),
    )
    return result


@router.post("/match")
def match(req: dict):
    """根据当前工况匹配知识规则"""
    values = req.get("values", {})
    results = match_knowledge(values)
    return {"total": len(results), "matches": results}
