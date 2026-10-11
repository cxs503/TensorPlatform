
"""
DataHub Metadata API — 内嵌轻量元数据服务
对标 DataHub 核心能力: 数据目录 / 自动 Profile / 全局搜索 / 业务术语表
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from database.config import get_db
from typing import Optional
import json

router = APIRouter(prefix="/datahub", tags=["DataHub元数据"])

# ══════════════════════════════════════════════
# 1. 数据目录 — 浏览所有数据集/模型/表
# ══════════════════════════════════════════════
@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    """浏览所有数据资产"""
    datasets = db.execute(text("""
        SELECT id, name, description, row_count, size_bytes, stage, created_at
        FROM datasets ORDER BY id DESC
    """)).fetchall()
    models = db.execute(text("""
        SELECT id, name, description, model_type, framework, status, created_at
        FROM models ORDER BY id DESC
    """)).fetchall()

    items = []
    for d in datasets:
        items.append({"type":"dataset","id":d[0],"name":d[1],"desc":d[2],
                      "rows":d[3],"size":d[4],"stage":d[5],"created":str(d[6]) if d[6] else None})
    for m in models:
        items.append({"type":"model","id":m[0],"name":m[1],"desc":m[2],
                      "model_type":m[3],"framework":m[4],"status":m[5],"created":str(m[6]) if m[6] else None})
    return {"total":len(items),"items":items}

# ══════════════════════════════════════════════
# 2. 全局搜索
# ══════════════════════════════════════════════
@router.get("/search")
def search(q: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """跨数据集+模型+训练任务全文搜索"""
    like = f"%{q}%"
    results = []
    for ds in db.execute(text(
        "SELECT id,name,description,'dataset' as type FROM datasets WHERE name ILIKE :q OR description ILIKE :q LIMIT 10"
    ),{"q":like}).fetchall():
        results.append({"id":ds[0],"name":ds[1],"desc":ds[2],"type":ds[3]})
    for md in db.execute(text(
        "SELECT id,name,description,'model' as type FROM models WHERE name ILIKE :q OR description ILIKE :q LIMIT 10"
    ),{"q":like}).fetchall():
        results.append({"id":md[0],"name":md[1],"desc":md[2],"type":md[3]})
    for tj in db.execute(text(
        "SELECT id,name,'training_job' as type FROM training_jobs WHERE name ILIKE :q LIMIT 10"
    ),{"q":like}).fetchall():
        results.append({"id":tj[0],"name":tj[1],"type":tj[2]})
    return {"query":q,"total":len(results),"results":results}

# ══════════════════════════════════════════════
# 3. 数据 Profile — 列级统计
# ══════════════════════════════════════════════
@router.get("/profile/{dataset_id}")
def data_profile(dataset_id: int, db: Session = Depends(get_db)):
    """数据集列级统计"""
    ds = db.execute(text("SELECT id,name,row_count,columns_info FROM datasets WHERE id=:id"),
                    {"id":dataset_id}).fetchone()
    if not ds:
        return {"error":"not found"}
    cols = ds[3] if isinstance(ds[3],list) else (json.loads(ds[3]) if ds[3] else [])
    return {"id":ds[0],"name":ds[1],"rows":ds[2],"columns":len(cols),
            "column_info":cols[:20]}

# ══════════════════════════════════════════════
# 4. 业务术语表
# ══════════════════════════════════════════════
_glossary = [
    {"term":"生产过程终点温度","en":"BTP","desc":"生产过程机最后一个风箱的废气温度","domain":"钢铁/生产过程"},
    {"term":"FeO工艺参数优化比","en":"FeO/C Ratio","desc":"氧化亚铁与工艺参数优化量的比值","domain":"钢铁/生产过程"},
    {"term":"光伏发电功率","en":"PV Power","desc":"光伏组件单位时间发电量(kW)","domain":"新能源/光伏"},
    {"term":"R²","en":"R-squared","desc":"决定系数，模型拟合优度指标","domain":"ML/评估"},
    {"term":"均方根误差","en":"RMSE","desc":"预测值与真实值偏差的度量","domain":"ML/评估"},
]

@router.get("/glossary")
def glossary(search: Optional[str] = None):
    """业务术语表"""
    items = _glossary
    if search:
        q = search.lower()
        items = [g for g in _glossary if q in g["term"].lower() or q in g["en"].lower()]
    return {"total":len(items),"items":items}

# ══════════════════════════════════════════════
# 5. 综合统计
# ══════════════════════════════════════════════
@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    ds = db.execute(text("SELECT count(*) FROM datasets")).scalar()
    md = db.execute(text("SELECT count(*) FROM models")).scalar()
    tj = db.execute(text("SELECT count(*) FROM training_jobs")).scalar()
    return {"datasets":ds,"models":md,"training_jobs":tj,"glossary_terms":len(_glossary)}
