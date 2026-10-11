"""Standalone comparison entry for models trained from algorithm-store assets."""

from __future__ import annotations

import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.deps import require_permission
from api.tenant_access import assert_organization_access, is_admin
from database.config import get_db


router = APIRouter(prefix="/model-comparison", tags=["模型对比测试"])
METRIC_KEYS = {
    "accuracy": "accuracy",
    "f1": "f1",
    "precision": "precision",
    "recall": "recall",
    "r2": "R2",
    "rmse": "RMSE",
    "mae": "MAE",
}
LOWER_IS_BETTER = {"rmse", "mae"}


class ComparisonRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    model_ids: list[int] = Field(min_length=2, max_length=10)
    dataset_id: int = Field(ge=1)
    task_type: Literal["regression", "classification"] = "regression"
    target_column: str = Field(min_length=1, max_length=256)
    feature_columns: list[str] | None = None  # legacy clients; comparison now uses each model's saved features
    primary_metric: Literal["accuracy", "f1", "precision", "recall", "r2", "rmse", "mae"]


def _eligible_sql(extra_where: str = "") -> str:
    return f"""
        SELECT m.id AS model_id, m.name AS model_name, m.model_type, m.framework,
               m.organization_id, tj.training_job_id, tj.dataset_id AS training_dataset_id,
               training_dataset.name AS training_dataset_name,
               (tj.hyperparameters->>'algorithm_id')::INTEGER AS algorithm_id,
               algorithm.name AS algorithm_name, algorithm.algorithm AS algorithm_type,
               algorithm.framework AS algorithm_framework,
               version.id AS model_version_id, version.version_number, version.parameters,
               CASE WHEN m.model_blob IS NOT NULL THEN 'database_blob' ELSE version.file_path END AS artifact
        FROM models m
        JOIN LATERAL (
            SELECT training_job_id, dataset_id, hyperparameters
            FROM training_jobs job
            WHERE (job.registered_model_id = m.id OR job.model_id = m.id)
              AND UPPER(COALESCE(job.status, '')) = 'COMPLETED'
              AND COALESCE(job.hyperparameters->>'algorithm_id', '') ~ '^[0-9]+$'
            ORDER BY job.id DESC
            LIMIT 1
        ) tj ON TRUE
        JOIN algorithm_models algorithm
          ON algorithm.id = (tj.hyperparameters->>'algorithm_id')::INTEGER
        LEFT JOIN datasets training_dataset ON training_dataset.id = tj.dataset_id
        LEFT JOIN LATERAL (
            SELECT id, version_number, file_path, parameters
            FROM model_versions candidate
            WHERE candidate.model_id = m.id
            ORDER BY candidate.id DESC
            LIMIT 1
        ) version ON TRUE
        WHERE (m.model_blob IS NOT NULL OR version.file_path IS NOT NULL)
        {extra_where}
    """


def _eligible_models(db, user: dict, model_ids: list[int] | None = None) -> list[dict]:
    params = {}
    extra = ""
    if model_ids is not None:
        extra += " AND m.id = ANY(:model_ids)"
        params["model_ids"] = model_ids
    if not is_admin(user):
        extra += " AND m.organization_id = :organization_id"
        params["organization_id"] = user.get("organization_id")
    rows = db.execute(text(_eligible_sql(extra) + " ORDER BY m.id DESC"), params).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        saved = item.pop("parameters", {}) or {}
        if isinstance(saved, str):
            try:
                saved = json.loads(saved)
            except (TypeError, ValueError):
                saved = {}
        features = saved.get("feature_columns") or saved.get("feature_cols") or []
        if isinstance(features, str):
            try:
                features = json.loads(features)
            except (TypeError, ValueError):
                features = [part.strip() for part in features.split(",") if part.strip()]
        item["feature_columns"] = [str(column) for column in features] if isinstance(features, list) else []
        item["target_column"] = saved.get("target_column") or saved.get("target_col")
        item["comparison_ready"] = True
        item["comparison_key"] = f"model-{item['model_id']}"
        items.append(item)

    # 模型对比页既是测试入口，也是算法商店的训练产物目录。此前只返回能
    # 关联到完整训练任务的模型，未训练算法、ONNX/Docker 等封装条目会被
    # 静默漏掉。保留它们的可见性，但没有可验证的训练契约时不允许勾选。
    if model_ids is None:
        from models.core import AlgorithmModel

        represented_algorithm_ids = {
            int(item["algorithm_id"])
            for item in items
            if item.get("algorithm_id") is not None
        }
        algorithms = db.query(AlgorithmModel).order_by(AlgorithmModel.id.desc()).all()
        for algorithm in algorithms:
            if algorithm.id in represented_algorithm_ids:
                continue
            metrics = algorithm.metrics if isinstance(algorithm.metrics, dict) else {}
            linked_model_id = metrics.get("registered_model_id")
            artifact_format = metrics.get("artifact_format")
            if linked_model_id:
                reason = "已有关联封装模型，但未保存训练特征和预测目标，不能公平对比"
            elif artifact_format in {"onnx", "docker_bundle", "pkl", "joblib"}:
                reason = "封装产物尚未登记为可对比模型"
            else:
                reason = "尚未完成训练，暂无可对比模型产物"
            items.append({
                "comparison_key": f"algorithm-{algorithm.id}",
                "comparison_ready": False,
                "comparison_reason": reason,
                "model_id": None,
                "model_name": "尚无可对比训练产物",
                "model_type": None,
                "framework": algorithm.framework,
                "organization_id": None,
                "training_job_id": None,
                "training_dataset_id": None,
                "training_dataset_name": None,
                "algorithm_id": algorithm.id,
                "algorithm_name": algorithm.name,
                "algorithm_type": algorithm.algorithm,
                "algorithm_framework": algorithm.framework,
                "model_version_id": None,
                "version_number": None,
                "artifact": None,
                "feature_columns": [],
                "target_column": None,
                "linked_model_id": linked_model_id,
            })
    return sorted(
        items,
        key=lambda item: (
            not bool(item.get("comparison_ready")),
            -int(item.get("algorithm_id") or 0),
            -int(item.get("model_id") or 0),
        ),
    )


def _winner(results: list[dict], primary_metric: str) -> int | None:
    key = METRIC_KEYS[primary_metric]
    candidates = []
    for result in results:
        if result.get("status") != "completed":
            continue
        value = (result.get("metrics") or {}).get(key)
        try:
            candidates.append((int(result["model_id"]), float(value)))
        except (TypeError, ValueError, KeyError):
            continue
    if not candidates:
        return None
    chooser = min if primary_metric in LOWER_IS_BETTER else max
    return chooser(candidates, key=lambda item: item[1])[0]


@router.get("/eligible-models")
def list_eligible_models(
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("models:read")),
):
    items = _eligible_models(db, current_user)
    return {"items": items, "total": len(items)}


@router.post("/compare")
def compare_trained_models(
    request: ComparisonRequest,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("models:read")),
):
    model_ids = list(dict.fromkeys(request.model_ids))
    if len(model_ids) < 2:
        raise HTTPException(status_code=400, detail="请至少选择两个不同的已训练模型")
    models = _eligible_models(db, current_user, model_ids)
    by_id = {int(model["model_id"]): model for model in models}
    missing = [model_id for model_id in model_ids if model_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"以下模型不是由上传算法训练完成，或缺少模型产物：{missing}",
        )
    organizations = {model.get("organization_id") for model in models}
    if len(organizations) != 1:
        raise HTTPException(status_code=400, detail="参与对比的模型必须属于同一组织")
    unknown_targets = [f"#{model['model_id']}" for model in models if not model.get("target_column")]
    if unknown_targets:
        raise HTTPException(
            status_code=400,
            detail="以下模型没有保存训练目标，无法确认预测含义一致：" + "、".join(unknown_targets),
        )
    target_mismatches = [
        f"#{model['model_id']}({model.get('target_column')})"
        for model in models
        if model.get("target_column") and str(model["target_column"]) != request.target_column
    ]
    if target_mismatches:
        raise HTTPException(
            status_code=400,
            detail="以下模型训练时预测的目标与当前目标列不一致：" + "、".join(target_mismatches),
        )
    unknown_features = [f"#{model['model_id']}" for model in models if not model.get("feature_columns")]
    if unknown_features:
        raise HTTPException(
            status_code=400,
            detail="以下模型没有保存训练特征，无法安全自动取数：" + "、".join(unknown_features),
        )

    dataset = db.execute(text(
        "SELECT id, name, organization_id FROM datasets WHERE id = :dataset_id"
    ), {"dataset_id": request.dataset_id}).mappings().first()
    if not dataset:
        raise HTTPException(status_code=404, detail="测试数据集不存在")
    assert_organization_access(dataset["organization_id"], current_user)
    model_org = next(iter(organizations))
    if model_org is not None and dataset["organization_id"] != model_org:
        raise HTTPException(status_code=400, detail="测试数据集与模型不属于同一组织")

    from api.model_evaluation import _load_dataset
    frame, _columns_info = _load_dataset(db, request.dataset_id)
    dataset_columns = set(str(column) for column in frame.columns)
    if request.target_column not in dataset_columns:
        raise HTTPException(status_code=400, detail=f"测试数据集缺少目标列：{request.target_column}")
    missing_features = []
    for model in models:
        missing = [column for column in model["feature_columns"] if column not in dataset_columns]
        if missing:
            missing_features.append(f"模型#{model['model_id']}缺少：{', '.join(missing)}")
    if missing_features:
        raise HTTPException(status_code=400, detail="；".join(missing_features))

    from api.mlflow import CompareModelsReq, compare_models

    started = time.perf_counter()
    comparison = compare_models(
        CompareModelsReq(
            model_ids=model_ids,
            dataset_id=request.dataset_id,
            task_type=request.task_type,
            target_column=request.target_column,
            feature_columns=None,
            test_size=1.0,
        ),
        db=db,
    )
    results = comparison.get("results") or []
    for result in results:
        source = by_id.get(int(result.get("model_id") or 0), {})
        result.update({
            "algorithm_id": source.get("algorithm_id"),
            "algorithm_name": source.get("algorithm_name"),
            "training_job_id": source.get("training_job_id"),
        })
    winner_model_id = _winner(results, request.primary_metric)
    status = "completed" if comparison.get("failed", 0) == 0 else (
        "partial" if comparison.get("completed", 0) else "failed"
    )
    duration = round(time.perf_counter() - started, 3)
    feature_columns_by_model = {
        str(result.get("model_id")): result.get("feature_columns") or []
        for result in results
    }
    stored_results = {
        "items": results,
        "completed": comparison.get("completed", 0),
        "failed": comparison.get("failed", 0),
        "duration_seconds": duration,
    }
    run_id = db.execute(text("""
        INSERT INTO model_comparison_runs
            (name, model_ids, dataset_id, task_type, target_column, feature_columns,
             primary_metric, results, winner_model_id, status, organization_id, created_by)
        VALUES
            (:name, CAST(:model_ids AS JSONB), :dataset_id, :task_type, :target_column,
             CAST(:feature_columns AS JSONB), :primary_metric, CAST(:results AS JSONB),
             :winner_model_id, :status, :organization_id, :created_by)
        RETURNING id
    """), {
        "name": request.name.strip(),
        "model_ids": json.dumps(model_ids),
        "dataset_id": request.dataset_id,
        "task_type": request.task_type,
        "target_column": request.target_column,
        "feature_columns": json.dumps(feature_columns_by_model, ensure_ascii=False),
        "primary_metric": request.primary_metric,
        "results": json.dumps(stored_results, ensure_ascii=False),
        "winner_model_id": winner_model_id,
        "status": status,
        "organization_id": model_org,
        "created_by": current_user.get("id"),
    }).scalar()
    db.commit()
    return {
        "id": run_id,
        "name": request.name.strip(),
        "dataset_id": request.dataset_id,
        "dataset_name": dataset["name"],
        "task_type": request.task_type,
        "target_column": request.target_column,
        "feature_columns_by_model": feature_columns_by_model,
        "primary_metric": request.primary_metric,
        "winner_model_id": winner_model_id,
        "status": status,
        "duration_seconds": duration,
        "completed": comparison.get("completed", 0),
        "failed": comparison.get("failed", 0),
        "results": results,
    }


@router.get("/runs")
def list_runs(
    limit: int = 50,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("models:read")),
):
    safe_limit = min(max(int(limit), 1), 200)
    sql = """
        SELECT run.id, run.name, run.model_ids, run.dataset_id, dataset.name AS dataset_name,
               run.task_type, run.target_column, run.feature_columns, run.primary_metric,
               run.results, run.winner_model_id, winner.name AS winner_model_name,
               run.status, run.created_at
        FROM model_comparison_runs run
        LEFT JOIN datasets dataset ON dataset.id = run.dataset_id
        LEFT JOIN models winner ON winner.id = run.winner_model_id
    """
    params = {"limit": safe_limit}
    if not is_admin(current_user):
        sql += " WHERE run.organization_id = :organization_id"
        params["organization_id"] = current_user.get("organization_id")
    sql += " ORDER BY run.id DESC LIMIT :limit"
    rows = db.execute(text(sql), params).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        payload = item.pop("results") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        item["results"] = payload.get("items", []) if isinstance(payload, dict) else []
        item["completed"] = payload.get("completed", 0) if isinstance(payload, dict) else 0
        item["failed"] = payload.get("failed", 0) if isinstance(payload, dict) else 0
        item["duration_seconds"] = payload.get("duration_seconds") if isinstance(payload, dict) else None
        item["created_at"] = item["created_at"].isoformat() if item.get("created_at") else None
        items.append(item)
    return {"items": items, "total": len(items)}


@router.delete("/runs/{run_id}")
def delete_run(
    run_id: int,
    db=Depends(get_db),
    current_user: dict = Depends(require_permission("models:write")),
):
    """Delete one saved comparison record without touching its models or data."""
    row = db.execute(text(
        "SELECT id, organization_id FROM model_comparison_runs WHERE id = :id"
    ), {"id": run_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="对比记录不存在或已删除")
    assert_organization_access(row["organization_id"], current_user)
    db.execute(text("DELETE FROM model_comparison_runs WHERE id = :id"), {"id": run_id})
    db.commit()
    return {"success": True, "id": run_id, "message": "对比记录已删除"}
