"""Persisted A/B experiments connected to real deployment inference."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal, Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.deps import require_permission
from api.tenant_access import assert_organization_access, is_admin
from database.config import get_db
from services.abtest_analysis import (
    SUPPORTED_METRICS,
    analyze_manual_values,
    analyze_observations,
    calculate_sample_size,
    observation_metric,
    stable_variant,
)


router = APIRouter(prefix="/abtest", tags=["A/B测试"])
ACTIVE_DEPLOYMENT_STATUSES = {
    "running", "healthy", "active", "deployed", "serving", "rolling_back"
}


class ExperimentConfig(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    deployment_a_id: Optional[int] = Field(default=None, ge=1)
    deployment_b_id: Optional[int] = Field(default=None, ge=1)
    # Kept for compatible clients; a running deployment is resolved and frozen at creation.
    model_a_id: Optional[int] = Field(default=None, ge=1)
    model_b_id: Optional[int] = Field(default=None, ge=1)
    metric: Literal["accuracy", "r2", "f1", "mae"] = "accuracy"
    traffic_split_a: float = Field(default=50.0, ge=1, le=99)


class RecordRequest(BaseModel):
    variant: Literal["A", "B"]
    metric_value: float
    user_id: Optional[str] = None


class PredictRequest(BaseModel):
    subject_id: str = Field(min_length=1, max_length=256)
    features: list[float] = Field(min_length=1)


class FeedbackRequest(BaseModel):
    actual_value: Any


def _json_value(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _scalar_prediction(value: Any) -> Any:
    value = _json_value(value)
    while isinstance(value, list) and len(value) == 1:
        value = value[0]
    if isinstance(value, (str, int, float, bool)) and not isinstance(value, dict):
        return value
    raise HTTPException(status_code=400, detail="A/B实验目前只支持单一预测结果")


def _json_list(value: Any) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []


def _experiment(db, experiment_id: str) -> dict:
    row = db.execute(text("""
        SELECT experiment_id, name, model_a_id, model_b_id, metric, traffic_split_a,
               status, winner, results_a, results_b, analysis, created_at,
               started_at, ended_at, deployment_a_id, deployment_b_id, organization_id
        FROM ab_experiments
        WHERE experiment_id = :experiment_id
    """), {"experiment_id": experiment_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="实验不存在")
    return dict(row)


def _assert_experiment_access(experiment: dict, user: dict) -> None:
    assert_organization_access(experiment.get("organization_id"), user)


def _deployment_info(
    db,
    *,
    deployment_id: int | None = None,
    model_id: int | None = None,
    require_running: bool = True,
) -> dict:
    if deployment_id is None and model_id is None:
        raise HTTPException(status_code=400, detail="必须选择一个已部署模型")
    where = "d.id = :value" if deployment_id is not None else "d.model_id = :value"
    value = deployment_id if deployment_id is not None else model_id
    row = db.execute(text(f"""
        SELECT d.id AS deployment_id, d.model_id, d.status, d.config,
               d.model_version_id, d.version, m.name AS model_name,
               m.model_type, m.framework, m.organization_id
        FROM deployments d
        JOIN models m ON m.id = d.model_id
        WHERE {where}
        ORDER BY d.id DESC
        LIMIT 1
    """), {"value": value}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到对应的模型部署")
    result = dict(row)
    result["status"] = str(result.get("status") or "").lower()
    if require_running and result["status"] not in ACTIVE_DEPLOYMENT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"部署 #{result['deployment_id']} 当前状态为 {result['status'] or '未知'}，不能参加实验",
        )
    config = result.get("config") or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            config = {}
    raw_count = config.get("n_features_in") if isinstance(config, dict) else None
    result["n_features_in"] = int(raw_count) if raw_count not in (None, "") else None
    return result


def _validate_pair(a: dict, b: dict, user: dict) -> None:
    assert_organization_access(a.get("organization_id"), user)
    assert_organization_access(b.get("organization_id"), user)
    if a["organization_id"] != b["organization_id"]:
        raise HTTPException(status_code=400, detail="两个模型必须属于同一个组织")
    if a["deployment_id"] == b["deployment_id"] or a["model_id"] == b["model_id"]:
        raise HTTPException(status_code=400, detail="模型A和模型B必须是两个不同的模型部署")
    count_a, count_b = a.get("n_features_in"), b.get("n_features_in")
    if count_a is not None and count_b is not None and count_a != count_b:
        raise HTTPException(
            status_code=400,
            detail=f"两个模型输入不兼容：A需要{count_a}个特征，B需要{count_b}个特征",
        )


def _run_deployment(deployment_id: int, features: list[float], db) -> dict:
    info = _deployment_info(db, deployment_id=deployment_id, require_running=True)
    try:
        from api.deployment import predict as deployment_predict

        result = deployment_predict(deployment_id, {"features": features}, db)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"部署推理失败: {exc}") from exc
    return {"info": info, "result": result}


def _request_stats(db, experiment_id: str) -> dict:
    row = db.execute(text("""
        SELECT COUNT(*) AS request_count,
               COUNT(*) FILTER (WHERE feedback_at IS NOT NULL) AS feedback_count,
               COUNT(*) FILTER (WHERE fallback_used) AS fallback_count,
               COUNT(*) FILTER (WHERE assigned_variant = 'A') AS assigned_a,
               COUNT(*) FILTER (WHERE assigned_variant = 'B') AS assigned_b,
               COUNT(*) FILTER (WHERE served_variant = 'A') AS served_a,
               COUNT(*) FILTER (WHERE served_variant = 'B') AS served_b,
               COUNT(*) FILTER (WHERE status = 'failed') AS failed_count
        FROM ab_experiment_requests
        WHERE experiment_id = :experiment_id
    """), {"experiment_id": experiment_id}).mappings().first()
    stats = dict(row or {})
    request_count = int(stats.get("request_count") or 0)
    fallback_count = int(stats.get("fallback_count") or 0)
    stats["fallback_rate"] = round(fallback_count / request_count, 4) if request_count else 0
    return stats


def _analyze_experiment(db, experiment: dict) -> dict:
    rows = db.execute(text("""
        SELECT served_variant, prediction, actual_value
        FROM ab_experiment_requests
        WHERE experiment_id = :experiment_id
          AND status = 'success'
          AND feedback_at IS NOT NULL
        ORDER BY id
    """), {"experiment_id": experiment["experiment_id"]}).mappings().all()
    observations = [
        {"variant": row["served_variant"], "prediction": row["prediction"], "actual": row["actual_value"]}
        for row in rows
    ]
    if observations:
        result = analyze_observations(experiment["metric"], observations)
    else:
        values_a = [item.get("metric_value") for item in _json_list(experiment.get("results_a"))]
        values_b = [item.get("metric_value") for item in _json_list(experiment.get("results_b"))]
        result = analyze_manual_values(experiment["metric"], values_a, values_b)
    result.update(_request_stats(db, experiment["experiment_id"]))
    return result


@router.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "abtest",
        "features": [
            "deployment_validation", "stable_traffic_routing", "baseline_fallback",
            "feedback_collection", "statistical_analysis", "sample_size_calculation",
        ],
    }


@router.get("/eligible-models")
def eligible_models(
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:read")),
):
    sql = """
        SELECT d.id AS deployment_id, d.model_id, d.status, d.config,
               d.model_version_id, d.version, m.name AS model_name,
               m.model_type, m.framework, m.organization_id
        FROM deployments d
        JOIN models m ON m.id = d.model_id
        WHERE LOWER(d.status) IN ('running', 'healthy', 'active', 'deployed', 'serving', 'rolling_back')
    """
    params = {}
    if not is_admin(current_user):
        sql += " AND m.organization_id = :organization_id"
        params["organization_id"] = current_user.get("organization_id")
    sql += " ORDER BY d.id DESC"
    rows = db.execute(text(sql), params).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        config = item.pop("config", {}) or {}
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except json.JSONDecodeError:
                config = {}
        item["n_features_in"] = config.get("n_features_in") if isinstance(config, dict) else None
        item["status"] = str(item.get("status") or "").lower()
        items.append(item)
    return {"items": items, "total": len(items)}


@router.post("/experiments")
def create_experiment(
    config: ExperimentConfig,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:write")),
):
    a = _deployment_info(
        db, deployment_id=config.deployment_a_id, model_id=config.model_a_id, require_running=True
    )
    b = _deployment_info(
        db, deployment_id=config.deployment_b_id, model_id=config.model_b_id, require_running=True
    )
    _validate_pair(a, b, current_user)
    experiment_id = f"exp_{uuid.uuid4().hex[:8]}"
    db.execute(text("""
        INSERT INTO ab_experiments
            (experiment_id, name, model_a_id, model_b_id, deployment_a_id, deployment_b_id,
             organization_id, metric, traffic_split_a, status, created_at, updated_at)
        VALUES
            (:experiment_id, :name, :model_a_id, :model_b_id, :deployment_a_id, :deployment_b_id,
             :organization_id, :metric, :traffic_split_a, 'created', NOW(), NOW())
    """), {
        "experiment_id": experiment_id,
        "name": config.name.strip(),
        "model_a_id": a["model_id"],
        "model_b_id": b["model_id"],
        "deployment_a_id": a["deployment_id"],
        "deployment_b_id": b["deployment_id"],
        "organization_id": a["organization_id"],
        "metric": config.metric,
        "traffic_split_a": config.traffic_split_a,
    })
    db.commit()
    return {
        "experiment_id": experiment_id,
        "status": "created",
        "model_a_id": a["model_id"],
        "model_b_id": b["model_id"],
        "deployment_a_id": a["deployment_id"],
        "deployment_b_id": b["deployment_id"],
    }


@router.post("/experiments/{experiment_id}/start")
def start_experiment(
    experiment_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:write")),
):
    experiment = _experiment(db, experiment_id)
    _assert_experiment_access(experiment, current_user)
    if experiment["status"] != "created":
        raise HTTPException(status_code=409, detail="只有待启动实验可以启动")
    a = _deployment_info(
        db, deployment_id=experiment.get("deployment_a_id"), model_id=experiment.get("model_a_id")
    )
    b = _deployment_info(
        db, deployment_id=experiment.get("deployment_b_id"), model_id=experiment.get("model_b_id")
    )
    _validate_pair(a, b, current_user)
    db.execute(text("""
        UPDATE ab_experiments
        SET status = 'running', started_at = NOW(), updated_at = NOW(),
            deployment_a_id = :deployment_a_id, deployment_b_id = :deployment_b_id
        WHERE experiment_id = :experiment_id AND status = 'created'
    """), {
        "experiment_id": experiment_id,
        "deployment_a_id": a["deployment_id"],
        "deployment_b_id": b["deployment_id"],
    })
    db.commit()
    return {"experiment_id": experiment_id, "status": "running"}


@router.post("/experiments/{experiment_id}/end")
def end_experiment(
    experiment_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:write")),
):
    experiment = _experiment(db, experiment_id)
    _assert_experiment_access(experiment, current_user)
    if experiment["status"] != "running":
        raise HTTPException(status_code=409, detail="只有运行中的实验可以结束")
    analysis = _analyze_experiment(db, experiment)
    winner = analysis.get("best_variant") if analysis.get("significant") else None
    db.execute(text("""
        UPDATE ab_experiments
        SET status = 'completed', winner = :winner, analysis = CAST(:analysis AS JSONB),
            ended_at = NOW(), updated_at = NOW()
        WHERE experiment_id = :experiment_id AND status = 'running'
    """), {
        "winner": winner,
        "analysis": json.dumps(analysis, ensure_ascii=False),
        "experiment_id": experiment_id,
    })
    db.commit()
    return {"experiment_id": experiment_id, "status": "completed", "winner": winner, "analysis": analysis}


@router.get("/experiments")
def list_experiments(
    status: Optional[str] = None,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:read")),
):
    sql = """
        SELECT e.experiment_id, e.name, e.model_a_id, e.model_b_id,
               e.deployment_a_id, e.deployment_b_id, e.metric, e.traffic_split_a,
               e.status, e.winner, e.created_at, ma.name AS model_a_name, mb.name AS model_b_name,
               COALESCE(stats.request_count, 0) AS request_count,
               COALESCE(stats.feedback_count, 0) AS feedback_count,
               COALESCE(stats.fallback_count, 0) AS fallback_count
        FROM ab_experiments e
        LEFT JOIN models ma ON ma.id = e.model_a_id
        LEFT JOIN models mb ON mb.id = e.model_b_id
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS request_count,
                   COUNT(*) FILTER (WHERE feedback_at IS NOT NULL) AS feedback_count,
                   COUNT(*) FILTER (WHERE fallback_used) AS fallback_count
            FROM ab_experiment_requests request
            WHERE request.experiment_id = e.experiment_id
        ) stats ON TRUE
        WHERE 1 = 1
    """
    params = {}
    if status:
        sql += " AND e.status = :status"
        params["status"] = status
    if not is_admin(current_user):
        sql += " AND e.organization_id = :organization_id"
        params["organization_id"] = current_user.get("organization_id")
    sql += " ORDER BY e.id DESC"
    rows = db.execute(text(sql), params).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        item["created_at"] = item["created_at"].isoformat() if item.get("created_at") else None
        items.append(item)
    return items


@router.get("/experiments/{experiment_id}")
def get_experiment(
    experiment_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:read")),
):
    experiment = _experiment(db, experiment_id)
    _assert_experiment_access(experiment, current_user)
    for key in ("created_at", "started_at", "ended_at"):
        if experiment.get(key):
            experiment[key] = experiment[key].isoformat()
    experiment.update(_request_stats(db, experiment_id))
    return experiment


@router.post("/experiments/{experiment_id}/predict")
def predict_experiment(
    experiment_id: str,
    request: PredictRequest,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:write")),
):
    experiment = _experiment(db, experiment_id)
    _assert_experiment_access(experiment, current_user)
    if experiment["status"] != "running":
        raise HTTPException(status_code=409, detail="实验尚未启动或已经结束")
    if not experiment.get("deployment_a_id") or not experiment.get("deployment_b_id"):
        raise HTTPException(status_code=409, detail="实验没有绑定有效部署，请重新创建或启动实验")

    assigned_variant = stable_variant(experiment_id, request.subject_id, experiment["traffic_split_a"])
    assigned_deployment_id = experiment[f"deployment_{assigned_variant.lower()}_id"]
    assigned_model_id = experiment[f"model_{assigned_variant.lower()}_id"]
    request_id = f"abr_{uuid.uuid4().hex[:16]}"
    started = time.perf_counter()
    fallback_used = False
    served_variant = None
    served_model_id = None
    served_deployment_id = None
    prediction = None
    errors = []

    try:
        response = _run_deployment(assigned_deployment_id, request.features, db)
        prediction = _scalar_prediction(response["result"].get("prediction"))
        served_variant = assigned_variant
        served_model_id = response["info"]["model_id"]
        served_deployment_id = response["info"]["deployment_id"]
    except HTTPException as exc:
        errors.append(f"{assigned_variant}: {exc.detail}")
        if assigned_variant == "B":
            fallback_used = True
            try:
                response = _run_deployment(experiment["deployment_a_id"], request.features, db)
                prediction = _scalar_prediction(response["result"].get("prediction"))
                served_variant = "A"
                served_model_id = response["info"]["model_id"]
                served_deployment_id = response["info"]["deployment_id"]
            except HTTPException as fallback_exc:
                errors.append(f"A兜底: {fallback_exc.detail}")

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    status = "success" if served_variant else "failed"
    db.execute(text("""
        INSERT INTO ab_experiment_requests
            (request_id, experiment_id, subject_id, assigned_variant, served_variant,
             assigned_model_id, served_model_id, assigned_deployment_id, served_deployment_id,
             features, prediction, fallback_used, latency_ms, status, error)
        VALUES
            (:request_id, :experiment_id, :subject_id, :assigned_variant, :served_variant,
             :assigned_model_id, :served_model_id, :assigned_deployment_id, :served_deployment_id,
             CAST(:features AS JSONB), CAST(:prediction AS JSONB), :fallback_used,
             :latency_ms, :status, :error)
    """), {
        "request_id": request_id,
        "experiment_id": experiment_id,
        "subject_id": request.subject_id,
        "assigned_variant": assigned_variant,
        "served_variant": served_variant,
        "assigned_model_id": assigned_model_id,
        "served_model_id": served_model_id,
        "assigned_deployment_id": assigned_deployment_id,
        "served_deployment_id": served_deployment_id,
        "features": json.dumps(request.features, ensure_ascii=False),
        "prediction": json.dumps(prediction, ensure_ascii=False),
        "fallback_used": fallback_used,
        "latency_ms": latency_ms,
        "status": status,
        "error": "; ".join(errors) or None,
    })
    db.commit()
    if status == "failed":
        raise HTTPException(
            status_code=502,
            detail={"message": "A、B模型均推理失败", "request_id": request_id, "errors": errors},
        )
    return {
        "request_id": request_id,
        "experiment_id": experiment_id,
        "subject_id": request.subject_id,
        "assigned_variant": assigned_variant,
        "served_variant": served_variant,
        "assigned_model_id": assigned_model_id,
        "served_model_id": served_model_id,
        "fallback_used": fallback_used,
        "prediction": prediction,
        "latency_ms": latency_ms,
        "warning": "; ".join(errors) if errors else None,
    }


@router.post("/experiments/{experiment_id}/requests/{request_id}/feedback")
def record_feedback(
    experiment_id: str,
    request_id: str,
    request: FeedbackRequest,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:write")),
):
    experiment = _experiment(db, experiment_id)
    _assert_experiment_access(experiment, current_user)
    if experiment["status"] != "running":
        raise HTTPException(status_code=409, detail="只有运行中的实验可以回填真实结果")
    row = db.execute(text("""
        SELECT prediction, status, feedback_at
        FROM ab_experiment_requests
        WHERE experiment_id = :experiment_id AND request_id = :request_id
    """), {"experiment_id": experiment_id, "request_id": request_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="实验请求不存在")
    if row["status"] != "success":
        raise HTTPException(status_code=409, detail="失败请求不能回填指标")
    if row["feedback_at"] is not None:
        raise HTTPException(status_code=409, detail="该请求已经回填过真实结果")
    actual = _json_value(request.actual_value)
    try:
        metric_value = observation_metric(experiment["metric"], row["prediction"], actual)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.execute(text("""
        UPDATE ab_experiment_requests
        SET actual_value = CAST(:actual AS JSONB), metric_value = :metric_value, feedback_at = NOW()
        WHERE experiment_id = :experiment_id AND request_id = :request_id AND feedback_at IS NULL
    """), {
        "actual": json.dumps(actual, ensure_ascii=False),
        "metric_value": metric_value,
        "experiment_id": experiment_id,
        "request_id": request_id,
    })
    db.commit()
    experiment = _experiment(db, experiment_id)
    return {
        "status": "recorded",
        "request_id": request_id,
        "actual_value": actual,
        "metric_value": metric_value,
        "analysis": _analyze_experiment(db, experiment),
    }


@router.get("/experiments/{experiment_id}/requests")
def list_requests(
    experiment_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:read")),
):
    experiment = _experiment(db, experiment_id)
    _assert_experiment_access(experiment, current_user)
    rows = db.execute(text("""
        SELECT request_id, subject_id, assigned_variant, served_variant,
               assigned_model_id, served_model_id, assigned_deployment_id, served_deployment_id,
               prediction, actual_value, metric_value, fallback_used, latency_ms,
               status, error, created_at, feedback_at
        FROM ab_experiment_requests
        WHERE experiment_id = :experiment_id
        ORDER BY id DESC
        LIMIT :limit
    """), {"experiment_id": experiment_id, "limit": limit}).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        for key in ("created_at", "feedback_at"):
            item[key] = item[key].isoformat() if item.get(key) else None
        items.append(item)
    return {"items": items, "total": len(items)}


@router.post("/experiments/{experiment_id}/results")
def record_result(
    experiment_id: str,
    request: RecordRequest,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:write")),
):
    """Compatibility endpoint for legacy/manual observations; new UI uses request feedback."""
    experiment = _experiment(db, experiment_id)
    _assert_experiment_access(experiment, current_user)
    if experiment["status"] != "running":
        raise HTTPException(status_code=409, detail="只有运行中的实验可以录入结果")
    column = "results_a" if request.variant == "A" else "results_b"
    row = db.execute(text(
        f"SELECT {column} FROM ab_experiments WHERE experiment_id=:experiment_id FOR UPDATE"
    ), {"experiment_id": experiment_id}).fetchone()
    data = _json_list(row[0] if row else None)
    data.append({
        "metric_value": request.metric_value,
        "user_id": request.user_id,
        "timestamp": time.time(),
    })
    db.execute(text(f"""
        UPDATE ab_experiments
        SET {column} = CAST(:data AS JSONB), updated_at = NOW()
        WHERE experiment_id = :experiment_id
    """), {"data": json.dumps(data), "experiment_id": experiment_id})
    db.commit()
    refreshed = _experiment(db, experiment_id)
    return {
        "status": "recorded",
        "variant": request.variant,
        "total_a": len(_json_list(refreshed.get("results_a"))),
        "total_b": len(_json_list(refreshed.get("results_b"))),
    }


@router.get("/experiments/{experiment_id}/analyze")
def analyze_experiment(
    experiment_id: str,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("experiments:read")),
):
    experiment = _experiment(db, experiment_id)
    _assert_experiment_access(experiment, current_user)
    return _analyze_experiment(db, experiment)


@router.get("/sample-size")
def calc_sample_size(
    metric: str = "accuracy",
    baseline: float = Query(default=0.5, gt=0, lt=1),
    min_effect: float = Query(default=0.05, gt=0),
    power: float = Query(default=0.8, gt=0, lt=1),
    alpha: float = Query(default=0.05, gt=0, lt=1),
    _current_user: dict = Depends(require_permission("experiments:read")),
):
    if metric not in SUPPORTED_METRICS:
        raise HTTPException(status_code=400, detail="不支持的指标")
    try:
        return calculate_sample_size(metric, baseline, min_effect, power, alpha)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
