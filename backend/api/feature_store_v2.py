"""
特征存储 API — 持久化版
特征组注册 · 在线/离线特征 · 特征检索
"""
import json, uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from database.config import get_db

router = APIRouter(prefix="/feature-store", tags=["特征存储"])

class FeatureDef(BaseModel):
    name: str
    dtype: str = "float32"
    description: str = ""

class FeatureGroupReq(BaseModel):
    name: str
    description: str = ""
    entity_name: str = "default"
    features: List[FeatureDef] = []

class OnlineFeatureReq(BaseModel):
    group_id: Optional[str] = None
    entity_keys: List[str]


@router.get("/status")
def get_status(db=Depends(get_db)):
    total = db.execute(text("SELECT COUNT(*) FROM feature_groups")).fetchone()[0]
    online = db.execute(text("SELECT COUNT(*) FROM feature_groups WHERE online_enabled=true")).fetchone()[0]
    return {"status": "active", "total_groups": total, "online_enabled": online, "engine": "PostgreSQL"}

@router.post("/feature-groups")
def create_feature_group(req: FeatureGroupReq, db=Depends(get_db)):
    gid = f"fg_{uuid.uuid4().hex[:8]}"
    features_json = json.dumps([f.dict() for f in req.features])
    db.execute(text("""INSERT INTO feature_groups (group_id, name, description, entity_name, features, created_at)
        VALUES (:gid, :nm, :desc, :ent, :feat, NOW())"""),
        {"gid": gid, "nm": req.name, "desc": req.description, "ent": req.entity_name, "feat": features_json})
    db.commit()
    return {"group_id": gid, "name": req.name, "feature_count": len(req.features)}

@router.get("/feature-groups")
def list_feature_groups(db=Depends(get_db)):
    rows = db.execute(text("SELECT group_id, name, description, entity_name, features, online_enabled, created_at FROM feature_groups ORDER BY id DESC")).fetchall()
    return [{"group_id": r[0], "name": r[1], "description": r[2], "entity_name": r[3],
             "features": json.loads(r[4]) if isinstance(r[4], str) else (r[4] or []),
             "online_enabled": r[5], "created_at": r[6].isoformat() if r[6] else None} for r in rows]

@router.get("/feature-groups/{group_id}")
def get_feature_group(group_id: str, db=Depends(get_db)):
    r = db.execute(text("SELECT * FROM feature_groups WHERE group_id=:gid"), {"gid": group_id}).fetchone()
    if not r: raise HTTPException(404, "特征组不存在")
    return {"group_id": r[1], "name": r[2], "description": r[3], "entity_name": r[4],
            "features": json.loads(r[5]) if isinstance(r[5], str) else r[5],
            "online_enabled": r[6], "created_at": r[7].isoformat() if r[7] else None}

@router.delete("/feature-groups/{group_id}")
def delete_feature_group(group_id: str, db=Depends(get_db)):
    db.execute(text("DELETE FROM feature_groups WHERE group_id=:gid"), {"gid": group_id})
    db.execute(text("DELETE FROM feature_values WHERE group_id=:gid"), {"gid": group_id})
    db.commit()
    return {"status": "deleted", "group_id": group_id}

@router.post("/online")
def set_online_features(req: List[dict], db=Depends(get_db)):
    """批量写入在线特征值"""
    count = 0
    for item in req:
        gid = item.get("group_id", "")
        key = item.get("entity_key", "")
        vals = item.get("values", {})
        if gid and key and vals:
            db.execute(text("""INSERT INTO feature_values (group_id, entity_key, values_json, event_ts)
                VALUES (:gid, :key, :vals, NOW())
                ON CONFLICT DO NOTHING"""), {"gid": gid, "key": key, "vals": json.dumps(vals)})
            count += 1
    db.commit()
    return {"status": "stored", "count": count}

@router.post("/online/query")
def get_online_features(req: OnlineFeatureReq, db=Depends(get_db)):
    if not req.entity_keys:
        return {"features": []}
    q = "SELECT entity_key, values_json FROM feature_values WHERE entity_key = ANY(:keys)"
    p = {"keys": req.entity_keys}
    if req.group_id:
        q += " AND group_id = :gid"; p["gid"] = req.group_id
    q += " ORDER BY event_ts DESC"
    rows = db.execute(text(q), p).fetchall()
    return {"features": [{"entity_key": r[0], "values": json.loads(r[1]) if isinstance(r[1], str) else r[1]} for r in rows]}

@router.get("/features")
def list_all_features(db=Depends(get_db)):
    rows = db.execute(text("SELECT group_id, name, features FROM feature_groups ORDER BY id DESC")).fetchall()
    all_features = []
    for r in rows:
        feats = json.loads(r[2]) if isinstance(r[2], str) else (r[2] or [])
        for f in feats:
            all_features.append({"group_id": r[0], "group_name": r[1], "feature_name": f.get("name"),
                                 "dtype": f.get("dtype"), "description": f.get("description", "")})
    return {"total": len(all_features), "items": all_features}
