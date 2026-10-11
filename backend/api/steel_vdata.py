"""钢铁工艺虚拟数据生成 API v2.0 - 独立模块"""
import os, numpy as np, pandas as pd
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/steel-vdata", tags=["钢铁虚拟数据"])
_STEEL_CSV = os.path.join(os.path.dirname(__file__), "..", "uploads", "feo_sintering_data.csv")
_store = {}

from services.steel_data_generator import get_generator, SCENARIOS

def _sanitize(obj):
    """递归将所有numpy类型转为Python原生类型"""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    elif isinstance(obj, (np.bool_,)):
        return bool(obj)
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    elif isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj

@router.get("/scenarios")
def scenarios():
    return {"scenarios": [
        {"key": s.key, "name": s.name, "icon": s.icon, "desc": s.desc, "features": s.n_features, "targets": s.target_names}
        for s in SCENARIOS.values()
    ]}

@router.post("/generate/{key}")
def generate(key: str, n: int = 500, anomalies: bool = True):
    gen = get_generator(_STEEL_CSV if os.path.exists(_STEEL_CSV) else None)
    try:
        df = gen.generate(key, n, anomalies)
        quality = gen.validate_quality(df, key)
        recs = df.to_dict(orient="records")
        for r in recs:
            for k, v in r.items():
                if isinstance(v, (np.integer,)): r[k] = int(v)
                elif isinstance(v, (np.floating,)): r[k] = round(float(v), 4)
                elif isinstance(v, pd.Timestamp): r[k] = v.isoformat()
        _store[f"steel_{key}"] = recs
        result = {"scenario": key, "records": len(recs), "columns": list(df.columns), "quality": _sanitize(quality), "preview": recs[:3]}
        return _sanitize(result)
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.post("/validate/{key}")
def validate(key: str, n: int = 500):
    gen = get_generator(_STEEL_CSV if os.path.exists(_STEEL_CSV) else None)
    df = gen.generate(key, n)
    return _sanitize({"scenario": key, "quality": gen.validate_quality(df, key)})

@router.get("/stored/{key}")
def stored(key: str):
    return _store.get(f"steel_{key}", [])
