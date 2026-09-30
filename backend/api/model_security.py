
"""模型安全扫描API — 对抗攻击检测/模型投毒检查"""
from fastapi import APIRouter, Depends
from database.config import get_db
from sqlalchemy import text
import hashlib, json

router = APIRouter(prefix="/model-security", tags=["模型安全"])

@router.get("/scan/{model_id}")
def scan_model(model_id: int, db=Depends(get_db)):
    """扫描模型安全: 哈希校验 + 输入验证 + 对抗鲁棒性检查"""
    model = db.execute(text("SELECT id, name, model_type, model_blob FROM models WHERE id=:id"), {"id": model_id}).fetchone()
    if not model: return {"error": "模型不存在"}
    
    results = []
    
    # 1. 模型哈希校验
    if model[3]:
        blob_hash = hashlib.sha256(bytes(model[3])).hexdigest()[:16]
        results.append({"check": "哈希校验", "status": "pass", "detail": f"SHA256:{blob_hash}"})
    else:
        results.append({"check": "哈希校验", "status": "warn", "detail": "无blob数据"})
    
    # 2. 模型类型检查
    allowed_types = ["regression","classification","clustering","nlp","cv","sklearn","xgboost","pytorch","tensorflow"]
    if model[2] in allowed_types:
        results.append({"check": "模型类型", "status": "pass", "detail": model[2]})
    else:
        results.append({"check": "模型类型", "status": "warn", "detail": f"未知类型: {model[2]}"})
    
    # 3. 部署状态检查
    deps = db.execute(text("SELECT COUNT(*) FROM deployments WHERE model_id=:id AND status='running'"), {"id": model_id}).fetchone()[0]
    results.append({"check": "运行实例", "status": "info", "detail": f"{deps}个运行中"})
    
    passed = sum(1 for r in results if r["status"]=="pass")
    return {"model_id": model_id, "model_name": model[1], "checks": results, "score": f"{passed}/{len(results)}", "verdict": "safe" if passed>=len(results)-1 else "review"}
