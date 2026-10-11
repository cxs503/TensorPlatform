"""
模型监控 API — 持久化版
自动记录预测、真实漂移检测(KS/PSI)、趋势告警
"""
import json
from datetime import datetime
from typing import Optional, List

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from database.config import get_db

router = APIRouter(prefix="/monitoring", tags=["模型监控"])


class PredictionLog(BaseModel):
    model_id: int
    dataset_id: Optional[int] = None
    predictions: List[float]
    actuals: Optional[List[float]] = None
    metrics: Optional[dict] = None


class DriftCheck(BaseModel):
    model_id: int
    window_size: int = 200


# ═══════════════ 端点 ═══════════════

@router.get("/status")
def get_status(db=Depends(get_db)):
    total = db.execute(text("SELECT COUNT(*) FROM monitoring_logs")).fetchone()[0]
    alerts = db.execute(text("SELECT COUNT(*) FROM monitoring_logs WHERE drift_detected = true")).fetchone()[0]
    row = db.execute(text("SELECT model_id, COUNT(*), MAX(created_at) FROM monitoring_logs GROUP BY model_id ORDER BY 2 DESC LIMIT 1")).fetchone()
    return {
        "status": "active",
        "total_logs": total,
        "active_alerts": alerts,
        "most_monitored_model": row[0] if row else None,
        "last_check": row[2].isoformat() if row and row[2] else None,
    }


@router.post("/predictions/log")
def log_prediction(req: PredictionLog, db=Depends(get_db)):
    preds_json = json.dumps(req.predictions)
    actuals_json = json.dumps(req.actuals) if req.actuals else None
    metrics_json = json.dumps(req.metrics) if req.metrics else None

    # 计算漂移（如果之前有记录）
    drift_score = None
    drift_detected = False
    if req.actuals and len(req.actuals) > 10:
        prev = db.execute(text(
            "SELECT predictions, actuals FROM monitoring_logs WHERE model_id = :mid ORDER BY id DESC LIMIT 1"
        ), {"mid": req.model_id}).fetchone()
        if prev and prev[1]:
            prev_actuals = json.loads(prev[1]) if isinstance(prev[1], str) else prev[1]
            drift_score = _compute_ks_drift(prev_actuals, req.actuals)
            drift_detected = drift_score > 0.1 if drift_score else False

    db.execute(text("""
        INSERT INTO monitoring_logs (model_id, dataset_id, predictions, actuals, metrics, drift_score, drift_detected, sample_count, created_at)
        VALUES (:mid, :did, :pred, :act, :met, :ds, :dd, :sc, NOW())
    """), {
        "mid": req.model_id, "did": req.dataset_id,
        "pred": preds_json, "act": actuals_json, "met": metrics_json,
        "ds": drift_score, "dd": drift_detected,
        "sc": len(req.predictions),
    })
    db.commit()

    return {"status": "logged", "drift_score": drift_score, "drift_detected": drift_detected,
            "sample_count": len(req.predictions)}


@router.get("/predictions/{model_id}")
def get_predictions(model_id: int, limit: int = 50, db=Depends(get_db)):
    rows = db.execute(text(
        "SELECT id, predictions, actuals, metrics, drift_score, drift_detected, sample_count, created_at "
        "FROM monitoring_logs WHERE model_id = :mid ORDER BY id DESC LIMIT :lim"
    ), {"mid": model_id, "lim": limit}).fetchall()

    items = []
    for r in rows:
        items.append({
            "id": r[0],
            "predictions": json.loads(r[1]) if isinstance(r[1], str) else r[1],
            "actuals": json.loads(r[2]) if isinstance(r[2], str) and r[2] else (r[2] or []),
            "metrics": json.loads(r[3]) if isinstance(r[3], str) and r[3] else (r[3] or {}),
            "drift_score": r[4], "drift_detected": r[5],
            "sample_count": r[6], "created_at": r[7].isoformat() if r[7] else None,
        })
    total = db.execute(text("SELECT COUNT(*) FROM monitoring_logs WHERE model_id = :mid"), {"mid": model_id}).fetchone()[0]
    return {"total": total, "items": items}


@router.post("/drift/check")
def check_drift(req: DriftCheck, db=Depends(get_db)):
    rows = db.execute(text(
        "SELECT predictions, actuals FROM monitoring_logs WHERE model_id = :mid ORDER BY id DESC LIMIT 2"
    ), {"mid": req.model_id}).fetchall()

    if len(rows) < 2:
        return {"error": "数据不足，需要至少2条记录", "has_drift": False}

    current = json.loads(rows[0][1]) if isinstance(rows[0][1], str) and rows[0][1] else (rows[0][1] or [])
    previous = json.loads(rows[1][1]) if isinstance(rows[1][1], str) and rows[1][1] else (rows[1][1] or [])

    if not current or not previous:
        current = json.loads(rows[0][0]) if isinstance(rows[0][0], str) else rows[0][0]
        previous = json.loads(rows[1][0]) if isinstance(rows[1][0], str) else rows[1][0]

    if not current or not previous:
        return {"error": "无有效数据", "has_drift": False}

    ks = _compute_ks_drift(list(previous), list(current))
    psi = _compute_psi(list(previous), list(current))
    has_drift = ks > 0.1 or psi > 0.2

    # 更新最新记录
    db.execute(text(
        "UPDATE monitoring_logs SET drift_score = :ds, drift_detected = :dd WHERE id = (SELECT id FROM monitoring_logs WHERE model_id = :mid ORDER BY id DESC LIMIT 1)"
    ), {"ds": max(ks, psi), "dd": has_drift, "mid": req.model_id})
    db.commit()

    return {
        "has_drift": has_drift,
        "ks_statistic": round(ks, 4),
        "psi": round(psi, 4),
        "ks_threshold": 0.1,
        "psi_threshold": 0.2,
        "details": {
            "ks": round(ks, 4), "psi": round(psi, 4),
            "current_samples": len(current), "previous_samples": len(previous),
        }
    }


@router.get("/trend/{model_id}")
def get_trend(model_id: int, db=Depends(get_db)):
    rows = db.execute(text(
        "SELECT metrics, drift_score, sample_count, created_at FROM monitoring_logs WHERE model_id = :mid ORDER BY id ASC LIMIT 50"
    ), {"mid": model_id}).fetchall()

    timestamps, r2_scores, drift_scores = [], [], []
    for r in rows:
        timestamps.append(r[3].isoformat() if r[3] else None)
        drift_scores.append(r[1] or 0)
        try:
            m = json.loads(r[2]) if isinstance(r[2], str) else (r[2] or {})
            if isinstance(m, dict):
                r2_scores.append(m.get("R2") or m.get("r2"))
            else:
                r2_scores.append(None)
        except Exception:
            r2_scores.append(None)

    return {"timestamps": timestamps, "r2_scores": r2_scores, "drift_scores": drift_scores}


@router.get("/alerts")
def get_alerts(limit: int = 20, db=Depends(get_db)):
    rows = db.execute(text(
        "SELECT id, model_id, drift_score, sample_count, created_at FROM monitoring_logs WHERE drift_detected = true ORDER BY id DESC LIMIT :lim"
    ), {"lim": limit}).fetchall()
    return {"total": len(rows), "items": [
        {"id": r[0], "model_id": r[1], "drift_score": r[2], "sample_count": r[3], "created_at": r[4].isoformat() if r[4] else None}
        for r in rows
    ]}


# ═══════════════ 漂移检测算法 ═══════════════

def _compute_ks_drift(ref: List[float], cur: List[float]) -> float:
    """KS 检验：两个分布的最大差异"""
    try:
        from scipy.stats import ks_2samp
        stat, _ = ks_2samp(ref, cur)
        return float(stat)
    except ImportError:
        ref_sorted = np.sort(ref)
        cur_sorted = np.sort(cur)
        all_vals = np.sort(np.concatenate([ref_sorted, cur_sorted]))
        cdf1 = np.searchsorted(ref_sorted, all_vals, side='right') / len(ref_sorted)
        cdf2 = np.searchsorted(cur_sorted, all_vals, side='right') / len(cur_sorted)
        return float(np.max(np.abs(cdf1 - cdf2)))


def _compute_psi(ref: List[float], cur: List[float], bins: int = 10) -> float:
    """PSI (Population Stability Index)：分布偏移度量"""
    ref_arr = np.array(ref)
    cur_arr = np.array(cur)
    min_val = min(ref_arr.min(), cur_arr.min())
    max_val = max(ref_arr.max(), cur_arr.max())
    bin_edges = np.linspace(min_val, max_val, bins + 1)

    ref_hist, _ = np.histogram(ref_arr, bins=bin_edges)
    cur_hist, _ = np.histogram(cur_arr, bins=bin_edges)

    ref_dist = ref_hist / len(ref_arr) + 1e-10
    cur_dist = cur_hist / len(cur_arr) + 1e-10

    psi = np.sum((cur_dist - ref_dist) * np.log(cur_dist / ref_dist))
    return float(max(0, psi))
