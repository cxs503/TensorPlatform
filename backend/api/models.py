"""
模型管理 API — 数据库持久化版本
"""
import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm import defer
from sqlalchemy import text

from database.config import get_db
from models.core import Model, ModelFolder, ModelStatus, Organization
from api.deps import require_auth, require_permission
from api.tenant_access import assert_organization_access, create_organization_id, require_model_access, scope_query, is_admin

router = APIRouter(prefix="/models", tags=["模型管理"])
MODEL_ARTIFACT_ROOT = Path(os.getenv("MODEL_ARTIFACT_ROOT", "/app/uploads/models")).resolve()
ACTIVE_DEPLOYMENT_STATUSES = {
    "pending", "starting", "deploying", "running", "healthy", "active", "deployed", "serving"
}


def _safe_artifact_candidates(file_paths, root: Path = MODEL_ARTIFACT_ROOT) -> list[Path]:
    root = root.resolve()
    candidates: set[Path] = set()
    for raw in file_paths:
        if not raw:
            continue
        path = Path(str(raw)).resolve()
        if path == root or root not in path.parents:
            continue
        candidates.add(path)
        if path.name.endswith("_model.pkl"):
            prefix = path.name[:-10]
            candidates.add(path.with_name(f"{prefix}_model.pt"))
            candidates.add(path.with_name(f"{prefix}_config.json"))
    return sorted(candidates, key=str)


def _remove_model_artifacts(file_paths, shared_paths=(), root: Path = MODEL_ARTIFACT_ROOT) -> dict:
    shared = {Path(str(path)).resolve() for path in shared_paths if path}
    deleted, skipped, errors = [], [], []
    for path in _safe_artifact_candidates(file_paths, root=root):
        if path in shared:
            skipped.append(str(path))
            continue
        try:
            if path.is_file():
                path.unlink()
                deleted.append(str(path))
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return {"deleted": deleted, "skipped_shared": skipped, "errors": errors}


def _delete_mlflow_registration(name: str | None) -> dict:
    if not name:
        return {"name": None, "deleted": False, "errors": []}
    try:
        from services.mlflow_tracker import get_mlflow_tracker

        client = get_mlflow_tracker().client
        if not client:
            return {"name": name, "deleted": False, "errors": ["MLflow client unavailable"]}
        try:
            client.get_registered_model(name)
        except Exception as exc:
            if "RESOURCE_DOES_NOT_EXIST" in str(exc) or "not found" in str(exc).lower():
                return {"name": name, "deleted": True, "errors": []}
            return {"name": name, "deleted": False, "errors": [str(exc)[:300]]}
        client.delete_registered_model(name)
        return {"name": name, "deleted": True, "errors": []}
    except Exception as exc:
        return {"name": name, "deleted": False, "errors": [str(exc)[:300]]}


@router.get("", dependencies=[Depends(require_permission("models:read"))])
def list_models(
    organization_id: int = None,
    workspace: str = None,
    offset: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """List models and enrich them from the deployments fact table."""
    # 模型列表只需要元数据。Model.model_blob 总计可能达到数百 MB；ORM 若不显式
    # defer，即使 to_dict 不返回该字段也会先把全部权重读入 Python，多个并发列表
    # 请求会迅速耗尽后端内存。
    q = scope_query(
        db.query(Model).options(defer(Model.model_blob)),
        Model.organization_id,
        current_user,
    )
    if organization_id is not None and not is_admin(current_user) and organization_id != current_user.get("organization_id"):
        raise HTTPException(status_code=403, detail="不能查询其他组织的模型")
    if organization_id is not None:
        q = q.filter(Model.organization_id == organization_id)
    if workspace:
        q = q.join(Organization, Model.organization_id == Organization.id).filter(Organization.workspace == workspace)
    total = q.count()
    safe_offset = max(offset, 0)
    safe_limit = min(max(limit, 1), 500)
    models = q.order_by(Model.id.desc()).offset(safe_offset).limit(safe_limit).all()

    deployment_counts = {}
    deployment_record_counts = {}
    training_counts = {}
    latest_versions = {}
    try:
        rows = db.execute(text("""
            SELECT model_id,
                   SUM(active_count) AS active_count,
                   SUM(record_count) AS record_count
            FROM (
                SELECT model_id,
                       COUNT(*) FILTER (WHERE LOWER(COALESCE(status, '')) IN
                           ('pending','starting','deploying','running','healthy','active','deployed','serving')) AS active_count,
                       COUNT(*) AS record_count
                FROM deployments GROUP BY model_id
                UNION ALL
                SELECT model_id,
                       COUNT(*) FILTER (WHERE LOWER(COALESCE(status, '')) IN
                           ('pending','starting','deploying','running','healthy','active','deployed','serving')) AS active_count,
                       COUNT(*) AS record_count
                FROM model_deployments GROUP BY model_id
            ) deployment_facts
            GROUP BY model_id
        """)).fetchall()
        deployment_counts = {int(row[0]): int(row[1]) for row in rows if row[0] is not None}
        deployment_record_counts = {int(row[0]): int(row[2]) for row in rows if row[0] is not None}
    except Exception:
        db.rollback()

    try:
        training_rows = db.execute(text("""
            SELECT referenced_model_id, COUNT(DISTINCT job_id)
            FROM (
                SELECT id AS job_id, model_id AS referenced_model_id FROM training_jobs WHERE model_id IS NOT NULL
                UNION ALL
                SELECT id, registered_model_id FROM training_jobs WHERE registered_model_id IS NOT NULL
            ) training_refs
            GROUP BY referenced_model_id
        """)).fetchall()
        training_counts = {int(row[0]): int(row[1]) for row in training_rows if row[0] is not None}
    except Exception:
        db.rollback()

    try:
        version_rows = db.execute(text("""
            SELECT DISTINCT ON (model_id)
                model_id, id, version_number, mlflow_run_id, mlflow_model_version
            FROM model_versions
            ORDER BY model_id, id DESC
        """)).fetchall()
        latest_versions = {
            int(row[0]): {
                "id": row[1],
                "version_number": row[2],
                "mlflow_run_id": row[3],
                "mlflow_model_version": row[4],
            }
            for row in version_rows
        }
    except Exception:
        db.rollback()

    items = []
    for model in models:
        item = model.to_dict()
        item["deployment_count"] = deployment_counts.get(model.id, 0)
        item["deployment_record_count"] = deployment_record_counts.get(model.id, 0)
        item["training_job_count"] = training_counts.get(model.id, 0)
        item["is_deployed"] = item["deployment_count"] > 0
        item["latest_version"] = latest_versions.get(model.id)
        item["latest_mlflow_run_id"] = (
            item["latest_version"].get("mlflow_run_id") if item["latest_version"] else None
        )
        items.append(item)
    return {
        "total": total,
        "offset": safe_offset,
        "limit": safe_limit,
        "items": items,
    }


@router.get("/folders", dependencies=[Depends(require_permission("models:read"))])
def list_model_folders(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    folders = scope_query(db.query(ModelFolder), ModelFolder.organization_id, current_user).order_by(ModelFolder.name.asc(), ModelFolder.id.asc()).all()
    return [{"id": folder.id, "name": folder.name, "description": folder.description, "created_at": folder.created_at.isoformat() if folder.created_at else None} for folder in folders]


@router.post("/folders", dependencies=[Depends(require_permission("models:write"))])
def create_model_folder(req: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    name = str(req.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "请输入模型分类名称")
    if len(name) > 128:
        raise HTTPException(400, "模型分类名称不能超过128个字符")
    organization_id = current_user.get("organization_id")
    if organization_id is None:
        raise HTTPException(400, "当前用户未绑定组织，不能创建模型分类")
    existing = db.query(ModelFolder).filter(ModelFolder.organization_id == organization_id, ModelFolder.name == name).first()
    if existing:
        raise HTTPException(409, "同名模型分类已存在")
    folder = ModelFolder(organization_id=organization_id, name=name, description=str(req.get("description") or "").strip() or None)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"id": folder.id, "name": folder.name, "description": folder.description, "created_at": folder.created_at.isoformat() if folder.created_at else None}


@router.delete("/folders/{folder_id}", dependencies=[Depends(require_permission("models:write"))])
def delete_model_folder(
    folder_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Delete a model classification folder while retaining its models as ungrouped."""
    folder = db.query(ModelFolder).filter(ModelFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="模型分类不存在或已删除")
    assert_organization_access(folder.organization_id, current_user)
    moved_models = (
        db.query(Model)
        .filter(Model.folder_id == folder_id)
        .update({Model.folder_id: None}, synchronize_session=False)
    )
    db.delete(folder)
    db.commit()
    return {
        "success": True,
        "id": folder_id,
        "moved_models": int(moved_models or 0),
        "message": "模型分类已删除，关联模型已移至未分类",
    }


@router.get("/grouped-by-folder", dependencies=[Depends(require_permission("models:read"))])
def list_models_grouped_by_folder(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    """按用户选择的模型分类文件夹分组；历史模型保留在未分类。"""
    folders = scope_query(db.query(ModelFolder), ModelFolder.organization_id, current_user).order_by(ModelFolder.name.asc(), ModelFolder.id.asc()).all()
    groups = {folder.id: {"folder_id": folder.id, "folder_name": folder.name, "folder_description": folder.description, "model_count": 0, "models": []} for folder in folders}
    ungrouped = {"folder_id": None, "folder_name": "未分类", "folder_description": "尚未选择模型分类的历史或新建模型", "model_count": 0, "models": []}
    # 文件夹内的模型按首次创建时间从早到晚排序；由此生成稳定、易理解的
    # 分类版本号 v1、v2…（与单个模型的制品版本 model_versions 区分）。
    models = scope_query(db.query(Model).options(defer(Model.model_blob)), Model.organization_id, current_user).order_by(Model.created_at.asc(), Model.id.asc()).all()
    latest_rows = db.execute(text("""
        SELECT DISTINCT ON (model_id) model_id, metrics, mlflow_run_id
        FROM model_versions ORDER BY model_id, id DESC
    """)).fetchall()
    latest = {row[0]: {"metrics": row[1], "mlflow_run_id": row[2]} for row in latest_rows}
    for model in models:
        group = groups.get(model.folder_id, ungrouped)
        folder_version = group["model_count"] + 1
        group["models"].append({"id": model.id, "name": model.name, "folder_version": folder_version, "model_type": model.model_type, "framework": model.framework, "stage": model.stage, "created_at": model.created_at.isoformat() if model.created_at else None, "metrics": (latest.get(model.id) or {}).get("metrics"), "mlflow_run_id": (latest.get(model.id) or {}).get("mlflow_run_id")})
        group["model_count"] = folder_version
    result = list(groups.values())
    if ungrouped["model_count"]:
        result.append(ungrouped)
    return result


@router.get("/grouped-by-algorithm", dependencies=[Depends(require_permission("models:read"))])
def list_models_grouped_by_algorithm(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """按训练源码(算法)分组的模型列表 —— 同一算法训练出的多个模型归为一组。

    分组锚点: training_jobs.hyperparameters->>'algorithm_id' → algorithm_models
    (复用 model_comparison._eligible_sql 的 LATERAL JOIN 写法)。无 algorithm_id
    的模型归入"未归类"桶。仅读现有数据, 不改训练写库。
    """
    params = {}
    org_filter = ""
    if not is_admin(current_user):
        org_filter = "AND m.organization_id = :organization_id"
        params["organization_id"] = current_user.get("organization_id")

    rows = db.execute(text(f"""
        SELECT m.id, m.name, m.model_type, m.framework, m.stage, m.created_at,
               mv.metrics AS metrics, mv.mlflow_run_id AS mlflow_run_id,
               (tj.hyperparameters->>'algorithm_id')::INTEGER AS algorithm_id,
               a.name AS algorithm_name, a.algorithm AS algorithm_type
        FROM models m
        LEFT JOIN LATERAL (
            SELECT metrics, mlflow_run_id FROM model_versions v
            WHERE v.model_id = m.id ORDER BY v.id DESC LIMIT 1
        ) mv ON TRUE
        LEFT JOIN LATERAL (
            SELECT hyperparameters FROM training_jobs job
            WHERE (job.registered_model_id = m.id OR job.model_id = m.id)
              AND COALESCE(job.hyperparameters->>'algorithm_id', '') ~ '^[0-9]+$'
            ORDER BY job.id DESC LIMIT 1
        ) tj ON TRUE
        LEFT JOIN algorithm_models a ON a.id = (tj.hyperparameters->>'algorithm_id')::INTEGER
        WHERE 1=1 {org_filter}
        ORDER BY algorithm_id NULLS LAST, m.id DESC
    """), params).fetchall()

    groups: dict = {}
    for r in rows:
        aid = r[8]
        if aid not in groups:
            groups[aid] = {
                "algorithm_id": aid,
                "algorithm_name": r[9] or ("未归类" if aid is None else f"算法 #{aid}"),
                "algorithm_type": r[10],
                "models": [],
            }
        groups[aid]["models"].append({
            "id": r[0], "name": r[1], "model_type": r[2], "framework": r[3],
            "stage": r[4], "created_at": r[5].isoformat() if r[5] else None,
            "metrics": r[6], "mlflow_run_id": r[7],
        })

    grouped = []
    for aid, g in groups.items():
        g["model_count"] = len(g["models"])
        if aid is not None:
            grouped.append(g)
    grouped.sort(key=lambda g: g["model_count"], reverse=True)
    result = grouped
    if None in groups:
        result.append(groups[None])
    return result


@router.post("", dependencies=[Depends(require_permission("models:write"))])
def create_model(req: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    model = Model(
        name=req.get("name", "新模型"),
        description=req.get("description"),
        model_type=req.get("model_type"),
        framework=req.get("framework"),
        project_id=req.get("project_id"),
        folder_id=req.get("folder_id"),
        organization_id=create_organization_id(req.get("organization_id"), current_user),
        status=ModelStatus.DRAFT,
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return model.to_dict()


@router.get("/{model_id}", dependencies=[Depends(require_permission("models:read")), Depends(require_model_access)])
def get_model(model_id: int, db: Session = Depends(get_db)):
    model = db.query(Model).filter(Model.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    item = model.to_dict()
    version = db.execute(text("""
        SELECT id, version_number, mlflow_run_id, mlflow_model_version,
               is_deployed, created_at
        FROM model_versions
        WHERE model_id = :model_id
        ORDER BY id DESC LIMIT 1
    """), {"model_id": model_id}).fetchone()
    item["latest_version"] = {
        "id": version[0],
        "version_number": version[1],
        "mlflow_run_id": version[2],
        "mlflow_model_version": version[3],
        "is_deployed": bool(version[4]),
        "created_at": version[5].isoformat() if version[5] else None,
    } if version else None
    item["latest_mlflow_run_id"] = version[2] if version else None
    return item



@router.delete("/{model_id}", dependencies=[Depends(require_permission("models:write")), Depends(require_model_access)])
def delete_model(model_id: int, db: Session = Depends(get_db)):
    model = db.query(Model).filter(Model.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")

    active_deployments = db.execute(
        text("""
            SELECT
                (SELECT COUNT(*) FROM deployments
                 WHERE model_id=:model_id AND LOWER(COALESCE(status, '')) IN
                    ('pending','starting','deploying','running','healthy','active','deployed','serving'))
              + (SELECT COUNT(*) FROM model_deployments
                 WHERE model_id=:model_id AND LOWER(COALESCE(status, '')) IN
                    ('pending','starting','deploying','running','healthy','active','deployed','serving'))
        """),
        {"model_id": model_id},
    ).scalar() or 0
    if active_deployments:
        raise HTTPException(
            status_code=409,
            detail=f"模型仍有 {active_deployments} 个运行中或待启动的部署，请先停止部署",
        )

    deployment_count = db.execute(
        text("""
            SELECT
                (SELECT COUNT(*) FROM deployments WHERE model_id=:model_id)
              + (SELECT COUNT(*) FROM model_deployments WHERE model_id=:model_id)
        """),
        {"model_id": model_id},
    ).scalar() or 0
    training_count = db.execute(
        text("SELECT COUNT(*) FROM training_jobs "
             "WHERE model_id = :model_id OR registered_model_id = :model_id"),
        {"model_id": model_id},
    ).scalar() or 0
    version_rows = db.execute(
        text("SELECT id, file_path FROM model_versions WHERE model_id=:model_id ORDER BY id"),
        {"model_id": model_id},
    ).fetchall()
    file_paths = [row[1] for row in version_rows if row[1]]
    shared_rows = db.execute(
        text("SELECT file_path FROM model_versions WHERE model_id<>:model_id AND file_path IS NOT NULL"),
        {"model_id": model_id},
    ).fetchall()
    shared_paths = [row[0] for row in shared_rows]
    mlflow_name = model.mlflow_name

    # Preserve training and evaluation history while detaching the deleted registry model.
    db.execute(text("""
        UPDATE training_jobs
        SET hyperparameters = COALESCE(hyperparameters::jsonb, '{}'::jsonb)
                || jsonb_build_object('deleted_model_id', :model_id, 'deleted_model_name', :model_name),
            model_id = CASE WHEN model_id=:model_id THEN NULL ELSE model_id END,
            registered_model_id = CASE WHEN registered_model_id=:model_id THEN NULL ELSE registered_model_id END
        WHERE model_id=:model_id OR registered_model_id=:model_id
    """), {"model_id": model_id, "model_name": model.name})
    for table_name in ("governance_audit", "inference_logs", "model_evaluation_results"):
        db.execute(text(f"UPDATE {table_name} SET model_id=NULL WHERE model_id=:model_id"), {"model_id": model_id})
    db.execute(text("DELETE FROM deployments WHERE model_id=:model_id"), {"model_id": model_id})
    db.execute(text("DELETE FROM model_deployments WHERE model_id=:model_id"), {"model_id": model_id})
    db.delete(model)
    db.commit()

    artifact_cleanup = _remove_model_artifacts(file_paths, shared_paths=shared_paths)
    mlflow_cleanup = _delete_mlflow_registration(mlflow_name)
    return {
        "status": "deleted",
        "id": model_id,
        "deleted_versions": len(version_rows),
        "deleted_deployment_records": int(deployment_count),
        "preserved_training_jobs": int(training_count),
        "artifact_cleanup": artifact_cleanup,
        "mlflow_cleanup": mlflow_cleanup,
    }

@router.put("/{model_id}", dependencies=[Depends(require_permission("models:write")), Depends(require_model_access)])
def update_model(model_id: int, req: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    model = db.query(Model).filter(Model.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    if "folder_id" in req and req.get("folder_id") is not None:
        folder = db.query(ModelFolder).filter(ModelFolder.id == req.get("folder_id")).first()
        if not folder:
            raise HTTPException(status_code=404, detail="模型分类不存在")
        assert_organization_access(folder.organization_id, current_user)
    for k, v in req.items():
        if hasattr(model, k) and k not in {"id", "organization_id"}:
            setattr(model, k, v)
    db.commit()
    return model.to_dict()


@router.post("/{model_id}/rollback", dependencies=[Depends(require_permission("models:write")), Depends(require_model_access)])
def rollback_model_version(model_id: int, req: dict, db: Session = Depends(get_db)):
    """模型版本回退 — 把指定 version_id 设为 is_deployed=true,其余版本置 false。

    入参: {version_id: int}
    返回: 回退后的版本信息
    """
    version_id = req.get("version_id")
    if version_id is None:
        raise HTTPException(status_code=400, detail="缺少 version_id")

    # 校验 model 存在
    model = db.query(Model).filter(Model.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")

    # 校验目标版本存在且属于该模型
    target = db.execute(text(
        "SELECT id, model_id, version_number, file_path, metrics, parameters, is_deployed, created_at "
        "FROM model_versions WHERE id = :vid"
    ), {"vid": version_id}).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="版本不存在")
    if target[1] != model_id:
        raise HTTPException(status_code=400, detail="版本不属于该模型")

    # 模型级回退同步更新该模型的部署事实，避免 deployment.version 与
    # model_version_id/MLflow run 指向不同版本。
    db.execute(text(
        "UPDATE deployments SET model_version_id = :vid, version = :version, updated_at = NOW() "
        "WHERE model_id = :mid"
    ), {"vid": version_id, "version": target[2], "mid": model_id})
    db.execute(text(
        "UPDATE model_versions SET is_deployed = CASE WHEN id = :vid THEN "
        "EXISTS (SELECT 1 FROM deployments d WHERE d.model_version_id = :vid "
        "AND LOWER(d.status) IN ('running','healthy','active','deployed','serving','rolling_back')) "
        "ELSE false END WHERE model_id = :mid"
    ), {"vid": version_id, "mid": model_id})
    db.commit()

    # 回读返回
    row = db.execute(text(
        "SELECT id, model_id, version_number, file_path, metrics, parameters, is_deployed, created_at "
        "FROM model_versions WHERE id = :vid"
    ), {"vid": version_id}).fetchone()

    def _as_json(v):
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        try:
            return json.loads(v)
        except Exception:
            return v

    return {
        "id": row[0],
        "model_id": row[1],
        "version_number": row[2],
        "file_path": row[3],
        "metrics": _as_json(row[4]),
        "parameters": _as_json(row[5]),
        "is_deployed": bool(row[6]),
        "created_at": row[7].isoformat() if row[7] else None,
        "message": f"已回退到版本 {row[2]}",
    }


@router.get("/{model_id}/lineage", dependencies=[Depends(require_permission("models:read")), Depends(require_model_access)])
def model_lineage(model_id: int, db: Session = Depends(get_db)):
    """模型完整血缘:model_id → models 表 → MLflow Registry → 训练任务 → 数据集。

    返回 {model_id, mlflow_name, mlflow_run_id, training_job_id, dataset_id, stage, ...}
    打通 训练→models表→MLflow Registry→部署→推理 的 model_id 全链路一致性。
    """
    model = db.query(Model).filter(Model.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")

    # 最新版本(含 metrics + parameters,后者存有 job_id / dataset_id)
    version = db.execute(text(
        "SELECT id, version_number, file_path, metrics, parameters, is_deployed, created_at, "
        "mlflow_run_id, mlflow_model_version "
        "FROM model_versions WHERE model_id = :mid ORDER BY id DESC LIMIT 1"
    ), {"mid": model_id}).fetchone()

    params = {}
    metrics = {}
    training_job_id = None
    dataset_id = None
    if version:
        try:
            params = version[4] if isinstance(version[4], dict) else (json.loads(version[4]) if version[4] else {})
        except Exception:
            params = {}
        try:
            metrics = version[3] if isinstance(version[3], dict) else (json.loads(version[3]) if version[3] else {})
        except Exception:
            metrics = {}
        training_job_id = params.get("job_id") or params.get("training_job_id")
        dataset_id = params.get("dataset_id")

    # 数据集信息
    dataset = None
    if dataset_id:
        ds_row = db.execute(text("SELECT id, name FROM datasets WHERE id = :did"), {"did": dataset_id}).fetchone()
        if ds_row:
            dataset = {"id": ds_row[0], "name": ds_row[1]}

    # 训练任务信息:优先持久化FK，旧记录回退到 parameters.job_id。
    training_job = None
    tj_row = db.execute(text(
        "SELECT id, name, training_job_id, status, metrics, started_at, completed_at, "
        "mlflow_run_id, registered_model_id, dataset_id "
        "FROM training_jobs "
        "WHERE registered_model_id = :mid OR model_id = :mid "
        "OR (:jid IS NOT NULL AND training_job_id = :jid) "
        "ORDER BY id DESC LIMIT 1"
    ), {"mid": model_id, "jid": training_job_id}).fetchone()
    if tj_row:
        try:
            tj_metrics = tj_row[4] if isinstance(tj_row[4], dict) else (json.loads(tj_row[4]) if tj_row[4] else {})
        except Exception:
            tj_metrics = {}
        training_job_id = tj_row[2] or training_job_id
        dataset_id = tj_row[9] or dataset_id
        training_job = {
            "id": tj_row[0], "name": tj_row[1], "training_job_id": tj_row[2],
            "status": str(tj_row[3]).lower() if tj_row[3] else None,
            "metrics": tj_metrics,
            "mlflow_run_id": tj_row[7],
            "registered_model_id": tj_row[8],
            "started_at": tj_row[5].isoformat() if tj_row[5] else None,
            "completed_at": tj_row[6].isoformat() if tj_row[6] else None,
        }
        if dataset is None and dataset_id:
            ds_row = db.execute(
                text("SELECT id, name FROM datasets WHERE id = :did"),
                {"did": dataset_id},
            ).fetchone()
            if ds_row:
                dataset = {"id": ds_row[0], "name": ds_row[1]}

    # MLflow Registry 反查(用 mlflow_name → version/stage/run_id)
    mlflow_info = None
    mlflow_run_id = (version[7] if version else None) or (tj_row[7] if tj_row else None)
    if model.mlflow_name:
        try:
            from services.mlflow_tracker import get_mlflow_tracker
            mlflow_info = get_mlflow_tracker().get_registry_model(model.mlflow_name)
            if mlflow_info:
                mlflow_run_id = mlflow_run_id or mlflow_info.get("latest_run_id")
        except Exception as e:
            mlflow_info = {"error": f"MLflow 查询失败: {e}"}

    # 部署信息(deployments 表为裸 SQL 表,无 ORM;name/version/status 列确定存在)
    deployments = []
    try:
        dep_rows = db.execute(text(
            "SELECT id, name, version, status FROM deployments WHERE model_id = :mid ORDER BY id DESC"
        ), {"mid": model_id}).fetchall()
        deployments = [
            {"id": r[0], "name": r[1], "version": r[2],
             "status": str(r[3]).lower() if r[3] else None}
            for r in dep_rows
        ]
    except Exception:
        # deployments 表结构若有差异,忽略部署部分,不影响血缘主体
        pass

    return {
        "model_id": model_id,
        "name": model.name,
        "model_type": model.model_type,
        "framework": model.framework,
        "stage": model.stage or "development",
        # ── models.id ↔ MLflow 双向映射 ──
        "mlflow_name": model.mlflow_name,
        "mlflow_run_id": mlflow_run_id,
        "mlflow_registry": mlflow_info,
        # ── 上游:训练 ──
        "training_job_id": training_job_id,
        "training_job": training_job,
        "model_version": {
            "id": version[0] if version else None,
            "version_number": version[1] if version else None,
            "file_path": version[2] if version else None,
            "mlflow_run_id": version[7] if version else None,
            "mlflow_model_version": version[8] if version else None,
            "is_deployed": bool(version[5]) if version else False,
            "created_at": version[6].isoformat() if version and version[6] else None,
            "metrics": metrics,
            "parameters": params,
        } if version else None,
        # ── 上游:数据集 ──
        "dataset_id": dataset_id,
        "dataset": dataset,
        # ── 下游:部署 ──
        "deployments": deployments,
    }
