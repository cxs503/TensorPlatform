
"""
模型血缘追踪API — 追踪 数据→特征→模型→部署 全链路
"""
from fastapi import APIRouter, Depends
from database.config import get_db
from sqlalchemy import text
router = APIRouter(prefix="/lineage/model", tags=["模型血缘"])

@router.get("/{model_id}")
def model_lineage(model_id: int, db=Depends(get_db)):
    """获取模型的完整血缘链路"""
    lineage = {"model_id": model_id, "nodes": [], "edges": []}
    
    # 1. Model info
    model = db.execute(text("SELECT id, name, model_type FROM models WHERE id=:id"), {"id": model_id}).fetchone()
    if not model: return {"error": "模型不存在"}
    lineage["nodes"].append({"id": f"model_{model[0]}", "type": "model", "label": model[1]})
    
    # 2. Training jobs
    jobs = db.execute(text("SELECT id, name, dataset_id FROM training_jobs WHERE model_id=:id"), {"id": model_id}).fetchall()
    for j in jobs:
        lineage["nodes"].append({"id": f"train_{j[0]}", "type": "training", "label": j[1]})
        lineage["edges"].append({"from": f"train_{j[0]}", "to": f"model_{model[0]}", "label": "produces"})
        if j[2]:
            ds = db.execute(text("SELECT id, name FROM datasets WHERE id=:did"), {"did": j[2]}).fetchone()
            if ds:
                lineage["nodes"].append({"id": f"data_{ds[0]}", "type": "dataset", "label": ds[1]})
                lineage["edges"].append({"from": f"data_{ds[0]}", "to": f"train_{j[0]}", "label": "feeds"})
    
    # 3. Deployments
    deps = db.execute(text("SELECT id, version FROM deployments WHERE model_id=:id"), {"id": model_id}).fetchall()
    for d in deps:
        lineage["nodes"].append({"id": f"deploy_{d[0]}", "type": "deployment", "label": f"v{d[1]}"})
        lineage["edges"].append({"from": f"model_{model[0]}", "to": f"deploy_{d[0]}", "label": "deploys"})
    
    return lineage

@router.get("/all")
def all_lineage(db=Depends(get_db), limit: int = 20):
    """所有模型的血缘概览"""
    models = db.execute(text("SELECT id, name FROM models ORDER BY id DESC LIMIT :lim"), {"lim": limit}).fetchall()
    result = {"lineages": []}
    for m in models:
        result["lineages"].append(model_lineage(m[0], db))
    return result
