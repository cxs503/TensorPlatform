"""
DataHub Integration API — 自动发现 + 血缘追踪
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database.config import get_db
from sqlalchemy import text

router = APIRouter(prefix="/datahub", tags=["DataHub集成"])

@router.get("/stats")
def datahub_stats(db: Session = Depends(get_db)):
    """轻量统计"""
    ds = db.execute(text("SELECT count(*) FROM datasets")).scalar()
    md = db.execute(text("SELECT count(*) FROM models")).scalar()
    tj = db.execute(text("SELECT count(*) FROM training_jobs")).scalar()
    lg = db.execute(text("SELECT count(*) FROM lineage_edges")).scalar()
    return {"datasets": ds, "models": md, "training_jobs": tj,
            "lineage_edges": lg, "auto_discovered": True}

@router.get("/discover")
def discover_assets(db: Session = Depends(get_db)):
    """自动发现数据集+模型+血缘(仅返回统计+前20条血缘)"""
    ds = db.execute(text("SELECT count(*) FROM datasets")).scalar()
    md = db.execute(text("SELECT count(*) FROM models")).scalar()
    
    edges = db.execute(text("""
        SELECT tj.id, tj.name, tj.dataset_id, tj.hyperparameters, tj.status
        FROM training_jobs tj WHERE tj.dataset_id IS NOT NULL
        ORDER BY tj.id DESC LIMIT 50
    """)).fetchall()
    
    lineage = []
    for j in edges:
        hp = j[3] if isinstance(j[3], dict) else {}
        mt = hp.get("model_type", "?") if hp else "?"
        lineage.append({
            "source_type": "dataset", "source_id": j[2],
            "target_name": j[1], "label": f"训练({mt})",
            "job_id": j[0], "status": str(j[4]) if j[4] else "?"
        })
    
    return {"assets": ds + md, "datasets": ds, "models": md,
            "edges_shown": len(lineage), "total_edges": len(edges),
            "lineage": lineage}

@router.get("/lineage/full")
def full_lineage(db: Session = Depends(get_db)):
    """血缘图(训练任务 → 数据集 → 模型)"""
    rows = db.execute(text("""
        SELECT tj.id, tj.name, tj.dataset_id, tj.hyperparameters, tj.status
        FROM training_jobs tj WHERE tj.dataset_id IS NOT NULL
        ORDER BY tj.id DESC LIMIT 100
    """)).fetchall()
    
    nodes, edges = [], []
    node_ids = set()
    for r in rows:
        ds_id = f"dataset_{r[2]}"
        if ds_id not in node_ids:
            node_ids.add(ds_id)
            ds_name = db.execute(text("SELECT name FROM datasets WHERE id=:id"), {"id": r[2]}).scalar() or f"DS#{r[2]}"
            nodes.append({"id": ds_id, "label": ds_name, "type": "dataset"})
        job_id = f"job_{r[0]}"
        if job_id not in node_ids:
            node_ids.add(job_id)
            hp = r[3] if isinstance(r[3], dict) else {}
            nodes.append({"id": job_id, "label": r[1], "type": "training_job",
                          "model_type": hp.get("model_type","?") if hp else "?"})
        edges.append({"id": f"e_{r[0]}", "source": ds_id, "target": job_id,
                       "label": "训练数据"})
    
    return {"nodes": nodes, "edges": edges}

@router.get("/sync")
def sync_to_lineage(db: Session = Depends(get_db)):
    """将训练任务血缘同步到 lineage_edges 表"""
    rows = db.execute(text("""
        SELECT tj.id, tj.name, tj.dataset_id FROM training_jobs tj
        WHERE tj.dataset_id IS NOT NULL LIMIT 100
    """)).fetchall()
    count = 0
    for r in rows:
        try:
            db.execute(text("""
                INSERT INTO lineage_edges (id, source_id, target_id, relationship, description, created_at)
                VALUES (gen_random_uuid(), :src, :tgt, 'training', :desc, NOW())
                ON CONFLICT DO NOTHING
            """), {"src": str(r[2]), "tgt": r[1], "desc": f"训练任务#{r[0]}"})
            count += 1
        except: pass
    db.commit()
    return {"synced": count}
