"""
标签管理 API
"""
import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from api.deps import require_auth, require_permission
from services.labeling_service import AnnotationType, get_labeling_service
from api.tenant_access import assert_organization_access, scope_query
from database.config import get_db
from models.core import Dataset
from sqlalchemy.orm import Session
from pathlib import Path
import re
import uuid

router = APIRouter(prefix="/labeling", tags=["标签管理"])


# ========== Label Studio 协同标注对接(2026-07-15)==========
import urllib.request, json as _json
from urllib.parse import quote

# Label Studio:内部用容器名,对外暴露 :18081。LS 1.23 用 Web Session 登录(DRF token 被 phaseout),故对接=可达性+Web 入口+best-effort API。
LABEL_STUDIO_INTERNAL = os.getenv("LABEL_STUDIO_URL", "http://kp-label-studio:8080")
LABEL_STUDIO_PUBLIC = os.getenv("LABEL_STUDIO_PUBLIC_URL", "http://localhost:18081")
LABEL_STUDIO_TOKEN = os.getenv("LABEL_STUDIO_TOKEN", "foundationls2026token")


@router.get("/labelstudio")
def labelstudio_status():
    """Label Studio 可达性 + Web 入口 + best-effort 项目列表"""
    # 探活
    up, version, projects = False, None, []
    try:
        req = urllib.request.Request(LABEL_STUDIO_INTERNAL + "/", headers={"User-Agent": "foundation/1.0"})
        resp = urllib.request.urlopen(req, timeout=6)
        up = (resp.status == 200)
    except Exception:
        up = False
    # best-effort 拉项目(DRF token 可能被 phaseout→401,正常降级为空)
    if up:
        try:
            req = urllib.request.Request(
                LABEL_STUDIO_INTERNAL + "/api/projects/",
                headers={"Authorization": "Token " + LABEL_STUDIO_TOKEN, "User-Agent": "foundation/1.0"},
            )
            resp = urllib.request.urlopen(req, timeout=6)
            projects = _json.loads(resp.read()) if resp.status == 200 else []
        except Exception:
            projects = []
    return {
        "up": up,
        "web_url": LABEL_STUDIO_PUBLIC,
        "api_token_configured": bool(LABEL_STUDIO_TOKEN),
        "projects": projects if isinstance(projects, list) else [],
        "note": "Label Studio 1.23 使用 Web Session 登录；服务就绪后请使用由管理员分配的独立账号登录。",
    }


def _labelstudio_export_name(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_\-一-鿿]+", "_", value or "dataset").strip("_")
    return (safe or "dataset")[:80]


def _labelstudio_result_labels(task: dict) -> list[str]:
    """Extract table/classification labels from Label Studio exported task JSON."""
    annotations = task.get("annotations") or task.get("completions") or []
    labels: list[str] = []
    for annotation in annotations:
        if not isinstance(annotation, dict) or annotation.get("was_cancelled"):
            continue
        for result in annotation.get("result") or []:
            value = result.get("value") if isinstance(result, dict) else None
            if not isinstance(value, dict):
                continue
            for key in ("choices", "labels", "textarea"):
                selected = value.get(key)
                if isinstance(selected, list):
                    labels.extend(str(item).strip() for item in selected if str(item).strip())
        # A task with multiple reviewers uses the first completed annotation as its final export.
        if labels:
            break
    return list(dict.fromkeys(labels))


@router.get("/labelstudio/export/{dataset_id}", dependencies=[Depends(require_permission("datasets:read"))])
def export_labelstudio_tasks(dataset_id: int, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    """Download platform table rows as Label Studio import tasks with a stable row identifier."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    assert_organization_access(dataset.organization_id, current_user)
    from api.datasets import _load_split_frame
    frame = _load_split_frame(dataset).reset_index(drop=True)
    if frame.empty:
        raise HTTPException(status_code=400, detail="数据集没有可供标注的数据行")
    if len(frame) > 100000:
        raise HTTPException(status_code=400, detail="单次导出最多支持 100000 行；请先在数据集管理中分割数据")
    rows = _json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))

    # Label Studio configuration variables are safest with ASCII field names. Keep the
    # source columns intact, and add one readable summary field for table-row labeling.
    tasks = []
    for index, row in enumerate(rows):
        # Label Studio Text tags reject JSON null. Normalize only the exported task
        # representation; the source dataset remains unchanged.
        normalized_row = {
            column: "（空）" if value is None else value
            for column, value in row.items()
        }
        record_text = "\n".join(
            f"{column}：{value}"
            for column, value in normalized_row.items()
        )
        tasks.append({
            "data": {
                **normalized_row,
                "__foundation_row_id": str(index),
                "record_text": record_text,
            }
        })
    filename = f"{_labelstudio_export_name(dataset.name)}_labelstudio_tasks.json"
    # HTTP headers must remain latin-1. RFC 5987 percent-encodes Chinese filenames safely.
    encoded_filename = quote(filename, safe="")
    return Response(
        content=_json.dumps(tasks, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"labelstudio_tasks.json\"; filename*=UTF-8''{encoded_filename}", "Cache-Control": "no-store, max-age=0"},
    )


@router.post("/labelstudio/import", dependencies=[Depends(require_permission("datasets:process"))])
async def import_labelstudio_annotations(
    source_dataset_id: int = Form(...),
    name: str = Form(...),
    output_column: str = Form("label"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Import Label Studio JSON export and persist it as a platform labeled dataset."""
    source = db.query(Dataset).filter(Dataset.id == int(source_dataset_id)).first()
    if not source:
        raise HTTPException(status_code=404, detail="源数据集不存在")
    assert_organization_access(source.organization_id, current_user)
    if not (file.filename or "").lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="请上传 Label Studio 导出的 JSON 文件")
    raw = await file.read()
    if len(raw) > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="导入文件不能超过 100MB")
    try:
        exported = _json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法解析 Label Studio JSON：{exc}") from exc
    if isinstance(exported, dict):
        exported = exported.get("tasks") or exported.get("data") or []
    if not isinstance(exported, list):
        raise HTTPException(status_code=400, detail="Label Studio 导出内容必须是任务数组")
    from api.datasets import _load_split_frame
    frame = _load_split_frame(source).reset_index(drop=True)
    raw_column = str(output_column or "label").strip()
    column = re.sub(r"[^0-9A-Za-z_一-鿿]+", "_", raw_column).strip("_")[:64] or "label"
    base_column, suffix = column, 2
    while column in frame.columns:
        column = f"{base_column}_{suffix}"
        suffix += 1
    labels_by_row: dict[int, str] = {}
    skipped = 0
    for task in exported:
        if not isinstance(task, dict):
            skipped += 1
            continue
        data = task.get("data") or {}
        row_id = str(data.get("__foundation_row_id") or "")
        if not row_id.isdigit() or not 0 <= int(row_id) < len(frame):
            skipped += 1
            continue
        labels = _labelstudio_result_labels(task)
        if labels:
            labels_by_row[int(row_id)] = ",".join(labels)
    frame[column] = [labels_by_row.get(index, "") for index in range(len(frame))]
    target_dir = Path("/app/uploads/datasets")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"labelstudio_{uuid.uuid4().hex}.csv"
    frame.to_csv(target_path, index=False, encoding="utf-8-sig")
    result = Dataset(
        name=str(name or f"{source.name}(Label Studio标注)").strip()[:128] or f"{source.name}(Label Studio标注)",
        description=f"从 Label Studio 导入标注结果；源数据集 #{source.id}，共写入 {len(labels_by_row)} 条带标签记录。",
        file_path=str(target_path), size_bytes=target_path.stat().st_size, row_count=int(len(frame)),
        stage="labeled", columns_info=[{"name": str(column_name), "dtype": str(dtype)} for column_name, dtype in frame.dtypes.items()],
        governed_from=source.id, project_id=source.project_id, organization_id=source.organization_id,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    try:
        from services.lineage_sync_service import upsert_dataset_node
        upsert_dataset_node(db, result.id)
    except Exception:
        pass
    return {"message": "已导入 Label Studio 标注结果", "dataset": result.to_dict(), "output_column": column, "labeled_rows": len(labels_by_row), "skipped_tasks": skipped}


# ========== 标签管理 ==========


@router.get("/labels")
def list_labels(category: str = None, status: str = None):
    """列出标签"""
    service = get_labeling_service()
    return service.list_labels(category, status)


@router.post("/labels", dependencies=[Depends(require_permission("datasets:process"))])
def create_label(req: dict):
    """创建标签；指定标签组时同时建立标签组关联。"""
    service = get_labeling_service()
    name = str(req.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请输入标签名称")
    group_id = str(req.get("group_id") or "").strip()
    groups = service.list_label_groups()
    group = next((item for item in groups if item.get("id") == group_id), None) if group_id else None
    if group_id and not group:
        raise HTTPException(status_code=400, detail="标签组不存在")
    category = str(req.get("category") or (group or {}).get("name") or "默认分类").strip() or "默认分类"
    label = service.create_label(
        name=name,
        category=category,
        color=req.get("color", "#000000"),
        description=req.get("description"),
    )
    if group_id and not service.add_label_to_group(group_id, label.id):
        # 标签已创建但关联异常时明确报错，避免前端误以为标签可用于该任务。
        raise HTTPException(status_code=500, detail="标签已创建，但未能加入标签组")
    return {
        "id": label.id,
        "name": label.name,
        "category": label.category,
        "color": label.color,
        "status": label.status.value if hasattr(label.status, "value") else label.status,
        "group_id": group_id or None,
    }


@router.get("/labels/{label_id}")
def get_label(label_id: str):
    """获取标签"""
    service = get_labeling_service()
    label = service.get_label(label_id)
    if not label:
        raise HTTPException(status_code=404, detail="标签不存在")
    return {
        "id": label.id,
        "name": label.name,
        "category": label.category,
        "color": label.color,
        "description": label.description,
        "status": label.status,
        "usage_count": label.usage_count,
    }


@router.put("/labels/{label_id}", dependencies=[Depends(require_permission("datasets:process"))])
def update_label(label_id: str, req: dict):
    """更新标签"""
    service = get_labeling_service()
    label = service.update_label(label_id, **req)
    if not label:
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"message": "更新成功"}


@router.delete("/labels/{label_id}", dependencies=[Depends(require_permission("datasets:process"))])
def delete_label(label_id: str):
    """删除标签"""
    service = get_labeling_service()
    if not service.delete_label(label_id):
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"message": "删除成功"}


# ========== 标签组管理 ==========


@router.get("/groups")
def list_label_groups():
    """列出标签组"""
    service = get_labeling_service()
    return service.list_label_groups()


@router.post("/groups", dependencies=[Depends(require_permission("datasets:process"))])
def create_label_group(req: dict):
    """创建标签组"""
    service = get_labeling_service()
    group = service.create_label_group(
        name=req.get("name"), description=req.get("description")
    )
    return {"id": group.id, "name": group.name, "description": group.description}


@router.post("/groups/{group_id}/labels", dependencies=[Depends(require_permission("datasets:process"))])
def add_label_to_group(group_id: str, label_id: str):
    """添加标签到组"""
    service = get_labeling_service()
    if not service.add_label_to_group(group_id, label_id):
        raise HTTPException(status_code=400, detail="标签组或标签不存在")
    return {"message": "添加成功"}


@router.delete("/groups/{group_id}/labels/{label_id}", dependencies=[Depends(require_permission("datasets:process"))])
def remove_label_from_group(group_id: str, label_id: str):
    """从组移除标签"""
    service = get_labeling_service()
    if not service.remove_label_from_group(group_id, label_id):
        raise HTTPException(status_code=400, detail="标签组不存在")
    return {"message": "移除成功"}


# ========== 标注任务管理 ==========


@router.get("/tasks", dependencies=[Depends(require_permission("datasets:read"))])
def list_annotation_tasks(
    assignee_id: str = None, status: str = None, dataset_id: str = None,
    db: Session = Depends(get_db), current_user: dict = Depends(require_auth),
):
    """仅返回当前组织可访问数据集关联的标注任务。"""
    service = get_labeling_service()
    tasks = service.list_annotation_tasks(assignee_id, status, dataset_id)
    dataset_ids = {int(item["dataset_id"]) for item in tasks if str(item.get("dataset_id") or "").isdigit()}
    if not dataset_ids:
        return []
    visible_ids = {
        row.id for row in scope_query(db.query(Dataset), Dataset.organization_id, current_user)
        .filter(Dataset.id.in_(dataset_ids)).all()
    }
    return [item for item in tasks if str(item.get("dataset_id") or "").isdigit() and int(item["dataset_id"]) in visible_ids]


@router.get("/datasets", dependencies=[Depends(require_permission("datasets:read"))])
def list_labelable_datasets(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    rows = scope_query(db.query(Dataset), Dataset.organization_id, current_user).order_by(Dataset.id.desc()).all()
    return {"items": [
        {"id": row.id, "name": row.name, "row_count": row.row_count or 0, "stage": row.stage, "has_file": bool(row.file_path or row.data_blob)}
        for row in rows if (row.row_count or 0) > 0 and (row.file_path or row.data_blob)
    ]}


@router.post("/tasks", dependencies=[Depends(require_permission("datasets:process"))])
def create_annotation_task(req: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    name = str(req.get("name") or "").strip()
    group_id = str(req.get("label_group_id") or "").strip()
    try:
        dataset_id = int(req.get("dataset_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="请选择要标注的数据集")
    if not name:
        raise HTTPException(status_code=400, detail="请输入标注任务名称")
    if not group_id:
        raise HTTPException(status_code=400, detail="请选择标签组")
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    assert_organization_access(dataset.organization_id, current_user)
    if not dataset.file_path and not dataset.data_blob:
        raise HTTPException(status_code=400, detail="所选数据集没有可读取的数据文件")
    service = get_labeling_service()
    if not any(group.get("id") == group_id for group in service.list_label_groups()):
        raise HTTPException(status_code=400, detail="标签组不存在")
    task = service.create_annotation_task(
        name=name,
        dataset_id=str(dataset.id),
        annotation_type=str(req.get("annotation_type") or AnnotationType.CLASSIFICATION.value),
        label_group_id=group_id,
        assignee_id=str(current_user.get("id") or current_user.get("username") or ""),
        deadline=req.get("deadline"),
        total_items=int(dataset.row_count or 0),
    )
    return {
        "id": task.id, "name": task.name, "dataset_id": task.dataset_id,
        "annotation_type": task.annotation_type, "label_group_id": task.label_group_id,
        "status": task.status.value if hasattr(task.status, "value") else task.status,
        "total_items": task.total_items, "completed_items": task.completed_items,
    }


@router.get("/tasks/{task_id}")
def get_annotation_task(task_id: str):
    """获取标注任务"""
    service = get_labeling_service()
    task = service.get_annotation_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "id": task.id,
        "name": task.name,
        "dataset_id": task.dataset_id,
        "annotation_type": task.annotation_type,
        "label_group_id": task.label_group_id,
        "assignee_id": task.assignee_id,
        "status": task.status,
        "total_items": task.total_items,
        "completed_items": task.completed_items,
        "progress": round(task.completed_items / task.total_items * 100, 2)
        if task.total_items > 0
        else 0,
        "deadline": task.deadline,
    }


@router.post("/tasks/{task_id}/assign", dependencies=[Depends(require_permission("datasets:process"))])
def assign_task(task_id: str, assignee_id: str):
    """分配任务"""
    service = get_labeling_service()
    if not service.assign_task(task_id, assignee_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "分配成功"}


@router.put("/tasks/{task_id}/status", dependencies=[Depends(require_permission("datasets:process"))])
def update_task_status(task_id: str, status: str):
    """更新任务状态"""
    service = get_labeling_service()
    task = service.update_task_status(task_id, status)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "更新成功", "status": task.status}


def _task_source_dataset(task_id: str, db: Session, current_user: dict):
    task = get_labeling_service().get_annotation_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="标注任务不存在")
    try:
        dataset_id = int(task.dataset_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="标注任务的数据集配置无效")
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="标注任务关联的数据集不存在")
    assert_organization_access(dataset.organization_id, current_user)
    return task, dataset


@router.get("/tasks/{task_id}/items", dependencies=[Depends(require_permission("datasets:read"))])
def list_task_items(
    task_id: str,
    offset: int = 0,
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    task, dataset = _task_source_dataset(task_id, db, current_user)
    if offset < 0 or limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="分页参数无效")
    from api.datasets import _load_split_frame
    import json
    frame = _load_split_frame(dataset).reset_index(drop=True)
    page = frame.iloc[offset:offset + limit]
    values = json.loads(page.to_json(orient="records", date_format="iso", force_ascii=False))
    annotations = {str(item.get("data_id")): item for item in get_labeling_service().list_annotations(task_id=task_id)}
    items = []
    for index, row in enumerate(values, start=offset):
        items.append({
            "data_id": str(index),
            "row_number": index + 1,
            "values": row,
            "annotation": annotations.get(str(index)),
        })
    return {
        "task_id": task_id,
        "dataset_id": dataset.id,
        "columns": [str(column) for column in frame.columns],
        "items": items,
        "total": int(len(frame)),
        "offset": offset,
        "limit": limit,
    }


@router.post("/tasks/{task_id}/export", dependencies=[Depends(require_permission("datasets:process"))])
def export_approved_annotations(task_id: str, req: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    task, source = _task_source_dataset(task_id, db, current_user)
    from api.datasets import _load_split_frame
    from models.labeling import Label
    frame = _load_split_frame(source).reset_index(drop=True)
    raw_column = str(req.get("output_column") or "label").strip()
    output_column = re.sub(r"[^0-9A-Za-z_一-鿿]+", "_", raw_column).strip("_")[:64] or "label"
    base_column = output_column
    suffix = 2
    while output_column in frame.columns:
        output_column = f"{base_column}_{suffix}"
        suffix += 1
    label_names = {row.id: row.name for row in db.query(Label).all()}
    labels_by_row = {}
    for annotation in get_labeling_service().list_annotations(task_id=task_id, status="approved"):
        values = []
        for item in annotation.get("labels") or []:
            if not isinstance(item, dict):
                values.append(str(item))
                continue
            values.append(str(item.get("label_name") or label_names.get(item.get("label_id")) or item.get("label_id") or ""))
        labels_by_row[str(annotation.get("data_id"))] = ",".join(dict.fromkeys(value for value in values if value))
    frame[output_column] = [labels_by_row.get(str(index), "") for index in range(len(frame))]
    target_dir = Path("/app/uploads/datasets")
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"labeled_{uuid.uuid4().hex}.csv"
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    result = Dataset(
        name=(str(req.get("name") or f"{source.name}(已标注)").strip()[:128]),
        description=f"由标注任务「{task.name}」导出；仅写入审核通过的标注。",
        file_path=str(path),
        size_bytes=path.stat().st_size,
        row_count=int(len(frame)),
        stage="labeled",
        columns_info=[{"name": str(column), "dtype": str(dtype)} for column, dtype in frame.dtypes.items()],
        governed_from=source.id,
        project_id=source.project_id,
        organization_id=source.organization_id,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    try:
        from services.lineage_sync_service import upsert_dataset_node
        upsert_dataset_node(db, result.id)
    except Exception:
        pass
    return {"message": "已导出带标签数据集", "dataset": result.to_dict(), "output_column": output_column, "approved_rows": len(labels_by_row)}


# ========== 标注结果管理 ==========


@router.post("/annotations", dependencies=[Depends(require_permission("datasets:process"))])
def submit_annotation(req: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    task_id = str(req.get("task_id") or "")
    task, _ = _task_source_dataset(task_id, db, current_user)
    data_id = str(req.get("data_id") or "")
    if not data_id.isdigit() or int(data_id) < 0 or int(data_id) >= int(task.total_items or 0):
        raise HTTPException(status_code=400, detail="数据行编号无效")
    labels = req.get("labels") or []
    if not isinstance(labels, list) or not labels:
        raise HTTPException(status_code=400, detail="请至少选择一个标签")
    annotation = get_labeling_service().submit_annotation(
        task_id=task_id,
        data_id=data_id,
        annotator_id=str(current_user.get("id") or current_user.get("username") or ""),
        labels=labels,
        metadata=req.get("metadata") if isinstance(req.get("metadata"), dict) else {},
    )
    return {"id": annotation.id, "task_id": annotation.task_id, "data_id": annotation.data_id, "status": annotation.status.value if hasattr(annotation.status, "value") else annotation.status}


@router.get("/annotations")
def list_annotations(task_id: str = None, annotator_id: str = None, status: str = None):
    """列出标注"""
    service = get_labeling_service()
    return service.list_annotations(task_id, annotator_id, status)


@router.get("/annotations/{annotation_id}")
def get_annotation(annotation_id: str):
    """获取标注"""
    service = get_labeling_service()
    annotation = service.get_annotation(annotation_id)
    if not annotation:
        raise HTTPException(status_code=404, detail="标注不存在")
    return {
        "id": annotation.id,
        "task_id": annotation.task_id,
        "data_id": annotation.data_id,
        "annotator_id": annotation.annotator_id,
        "labels": annotation.labels,
        "status": annotation.status,
        "created_at": annotation.created_at,
        "reviewed_at": annotation.reviewed_at,
        "review_comment": annotation.review_comment,
    }


@router.post("/annotations/{annotation_id}/review", dependencies=[Depends(require_permission("datasets:process"))])
def review_annotation(annotation_id: str, req: dict, current_user: dict = Depends(require_auth)):
    approved = bool(req.get("approved", str(req.get("status") or "").lower() == "approved"))
    annotation = get_labeling_service().review_annotation(
        annotation_id=annotation_id,
        reviewer_id=str(current_user.get("id") or current_user.get("username") or ""),
        approved=approved,
        comment=str(req.get("comment") or ""),
    )
    if not annotation:
        raise HTTPException(status_code=404, detail="标注不存在")
    return {"message": "审核完成", "status": annotation.status.value if hasattr(annotation.status, "value") else annotation.status}


# ========== 统计 ==========


@router.get("/stats")
def get_labeling_stats(task_id: str = None):
    """获取标注统计"""
    service = get_labeling_service()
    return service.get_labeling_stats(task_id)
