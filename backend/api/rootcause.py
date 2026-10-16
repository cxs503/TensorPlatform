
"""根因分析API"""
from fastapi import APIRouter, Depends
from database.config import get_db
from sqlalchemy import text

router = APIRouter(prefix="/rootcause", tags=["根因分析"])

@router.get("/analyze/{model_id}")
def analyze_model_issue(model_id: int, db=Depends(get_db)):
    """分析模型性能下降的根因"""
    causes = []

    # 1. Check evaluation trend
    evals = db.execute(text("SELECT metrics FROM model_evaluation_results WHERE model_id=:id ORDER BY created_at DESC LIMIT 5"), {"id": model_id}).fetchall()
    if len(evals) >= 2:
        if isinstance(evals[0][0], str):
            import json as _json
            m1 = _json.loads(evals[0][0]).get("R2", _json.loads(evals[0][0]).get("accuracy", 0))
            m2 = _json.loads(evals[1][0]).get("R2", _json.loads(evals[1][0]).get("accuracy", 0))
        else:
            m1 = (evals[0][0] or {}).get("R2", (evals[0][0] or {}).get("accuracy", 0))
            m2 = (evals[1][0] or {}).get("R2", (evals[1][0] or {}).get("accuracy", 0))
        if float(m2) - float(m1) > 0.02:
            causes.append({"cause": "模型性能下降", "severity": "high", "evidence": f"R²从{m2:.3f}降至{m1:.3f}", "suggestion": "触发自动重训练或回滚到上一版本"})
    
    # 2. Check deployment issues
    deps = db.execute(text("SELECT COUNT(*) FROM deployments WHERE model_id=:id AND status!='running'"), {"id": model_id}).fetchone()[0]
    if deps > 0:
        causes.append({"cause": "部署异常", "severity": "medium", "evidence": f"{deps}个部署异常", "suggestion": "检查部署日志, 考虑回滚"})
    
    if not causes:
        causes.append({"cause": "未检测到异常", "severity": "info", "evidence": "所有指标正常", "suggestion": "持续监控"})
    
    return {"model_id": model_id, "causes": causes, "timestamp": str(db.execute(text("SELECT NOW()")).fetchone()[0])}
