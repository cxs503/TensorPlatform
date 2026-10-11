"""
数据集 API — 含文件上传和真实数据存储
"""
import csv
import io
import math
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
import pandas as pd

from database.config import get_db
from models.core import Dataset
from sqlalchemy import text
from api.deps import require_auth, require_permission
from api.tenant_access import (
    create_organization_id, require_dataset_access, scope_query,
)

router = APIRouter(prefix="/datasets", tags=["数据集管理"])

UPLOAD_DIR = Path("/app/uploads/datasets")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class DatasetSplitRequest(BaseModel):
    strategy: Literal["random", "chronological"] = "random"


class FieldRolesRequest(BaseModel):
    # Dataset-level metadata must remain task-independent.
    time_column: str | None = None
    id_columns: list[str] = []


def _ensure_field_roles(db: Session):
    db.execute(text("CREATE TABLE IF NOT EXISTS dataset_field_roles (dataset_id INTEGER PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE, organization_id INTEGER, settings JSONB NOT NULL, updated_at TIMESTAMP DEFAULT NOW())"))
    db.commit()


def _field_recommendation(dataset: Dataset):
    import pandas as pd
    frame = _load_split_frame(dataset).head(1000)
    columns = [str(c) for c in frame.columns]
    times, ids, numeric = [], [], []
    for col in columns:
        values, name = frame[col].dropna(), col.lower()
        number_ratio = float(pd.to_numeric(values, errors="coerce").notna().mean()) if len(values) else 0
        date_ratio = float(pd.to_datetime(values, errors="coerce").notna().mean()) if len(values) else 0
        if date_ratio >= .8 and (any(k in name for k in ("time", "date", "日期", "时间")) or number_ratio < .8): times.append(col)
        unique_ratio = float(values.nunique() / max(len(values), 1))
        if any(k in name for k in ("_id", "id", "编号", "设备", "炉号", "批次")) or (unique_ratio >= .95 and len(values) >= 10): ids.append(col)
        if number_ratio >= .8: numeric.append(col)
    targets = [c for c in numeric if c not in ids and any(k in c.lower() for k in ("target", "label", "output", "result", "temp", "temperature", "预测", "标签", "温度", "能耗", "产量"))]
    target = targets[0] if targets else (numeric[-1] if numeric else None)
    return {"time_column": times[0] if times else None, "id_columns": ids, "feature_columns": [c for c in numeric if c not in ids and c != target], "target_column": target, "candidates": {"time_columns": times, "target_columns": targets or numeric}, "data_kind": "timeseries" if times else "tabular"}


@router.get("/{dataset_id:int}/field-roles", dependencies=[Depends(require_permission("datasets:read")), Depends(require_dataset_access)])
def get_field_roles(dataset_id: int, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset: raise HTTPException(404, "数据集不存在")
    if not dataset.file_path and not dataset.data_blob: raise HTTPException(400, "数据集没有可分析的数据文件")
    _ensure_field_roles(db)
    saved = db.execute(text("SELECT settings FROM dataset_field_roles WHERE dataset_id=:id"), {"id": dataset_id}).scalar()
    # Ignore legacy target/feature values: those are selected in each algorithm task.
    if isinstance(saved, dict):
        saved = {"time_column": saved.get("time_column"), "id_columns": saved.get("id_columns") or []}
    else:
        saved = None
    return {"recommendation": _field_recommendation(dataset), "saved": saved}


@router.put("/{dataset_id:int}/field-roles", dependencies=[Depends(require_permission("datasets:edit")), Depends(require_dataset_access)])
def save_field_roles(dataset_id: int, request: FieldRolesRequest, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset: raise HTTPException(404, "数据集不存在")
    columns = {str(c) for c in _load_split_frame(dataset).head(1).columns}
    settings = request.model_dump()
    chosen = set(settings["id_columns"] + [x for x in (settings["time_column"],) if x])
    missing = chosen - columns
    if missing: raise HTTPException(400, "所选字段不存在：" + "、".join(sorted(missing)))
    _ensure_field_roles(db)
    db.execute(text("INSERT INTO dataset_field_roles(dataset_id, organization_id, settings, updated_at) VALUES (:id, :org, CAST(:settings AS JSONB), NOW()) ON CONFLICT(dataset_id) DO UPDATE SET settings=EXCLUDED.settings, updated_at=NOW()"), {"id": dataset_id, "org": dataset.organization_id, "settings": __import__("json").dumps(settings, ensure_ascii=False)})
    db.commit()
    return {"message": "字段用途已保存", "settings": settings}



def _load_split_frame(dataset: Dataset):
    import pandas as pd

    if dataset.file_path and Path(dataset.file_path).is_file():
        path = Path(dataset.file_path)
        suffix = path.suffix.lower()
        if suffix in {".csv", ".txt", ".tsv"}:
            return pd.read_csv(path, sep="\t" if suffix == ".tsv" else ",")
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(path)
        if suffix == ".parquet":
            return pd.read_parquet(path)
        if suffix in {".json", ".jsonl"}:
            return pd.read_json(path, lines=suffix == ".jsonl")
        raise HTTPException(400, f"暂不支持分割该文件类型：{suffix or '未知'}")
    if dataset.data_blob:
        return pd.read_csv(io.BytesIO(dataset.data_blob))
    raise HTTPException(400, "数据集没有可分割的数据文件")


def _split_frame(frame, strategy: str):
    if len(frame) < 2:
        raise HTTPException(400, "数据集至少需要2行才能分割为训练数据和测试数据")
    test_count = max(1, math.ceil(len(frame) * 0.1))
    train_count = len(frame) - test_count
    if strategy == "chronological":
        ordered = frame.reset_index(drop=True)
    elif strategy == "random":
        ordered = frame.sample(frac=1, random_state=42).reset_index(drop=True)
    else:
        raise HTTPException(400, "不支持的数据分割方式")
    return ordered.iloc[:train_count].copy(), ordered.iloc[train_count:].copy()


def _safe_split_filename(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "_", str(name)).strip("._")
    return (cleaned or "dataset")[:120]


def _split_payload(dataset: Dataset, strategy: str):
    train, test = _split_frame(_load_split_frame(dataset), strategy)
    total = len(train) + len(test)
    return train, test, {
        "source_dataset_id": dataset.id,
        "source_name": dataset.name,
        "strategy": strategy,
        "total_rows": int(total),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "train_ratio": round(len(train) / total, 4),
        "test_ratio": round(len(test) / total, 4),
        "train_name": f"{dataset.name}训练",
        "test_name": f"{dataset.name}测试",
    }


@router.get("", dependencies=[Depends(require_permission("datasets:read"))])
def list_datasets(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    q = scope_query(db.query(Dataset), Dataset.organization_id, current_user)
    datasets = q.order_by(Dataset.id.desc()).all()
    return {"total": len(datasets), "items": [d.to_dict() for d in datasets]}


@router.post("", dependencies=[Depends(require_permission("datasets:upload"))])
def create_dataset(req: dict = Body(...), db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    dataset = Dataset(
        name=req.get("name", "未命名数据集"),
        description=req.get("description"),
        file_path=req.get("file_path"),
        size_bytes=req.get("size_bytes", 0),
        row_count=req.get("row_count", 0),
        project_id=req.get("project_id"),
        organization_id=create_organization_id(req.get("organization_id"), current_user),
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    try:
        from services.lineage_sync_service import upsert_dataset_node
        upsert_dataset_node(db, dataset.id)
    except Exception as exc:
        print(f"[lineage] 数据集 {dataset.id} 同步血缘失败: {exc}")
    return dataset.to_dict()


@router.post("/{dataset_id:int}/upload", dependencies=[Depends(require_permission("datasets:upload")), Depends(require_dataset_access)])
async def upload_dataset_file(
    dataset_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传CSV文件到数据集"""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    
    # 平台的后续清洗、训练、评估与脱敏统一读取 CSV。Excel 仅作为
    # 上传格式：解析成功后马上转换为 UTF-8 CSV，避免不同模块对格式
    # 支持不一致，造成“上传成功、后续处理失败”。
    original_name = file.filename or "upload.csv"
    suffix = Path(original_name).suffix.lower() or ".csv"
    uploaded_path = UPLOAD_DIR / f"{dataset_id}_upload{suffix}"
    file_path = UPLOAD_DIR / f"{dataset_id}_data.csv" if suffix in {".xlsx", ".xls"} else UPLOAD_DIR / f"{dataset_id}_data{suffix}"
    content = await file.read()
    uploaded_path.write_bytes(content)

    try:
        if suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(uploaded_path)
            temporary_csv = file_path.with_suffix(".csv.tmp")
            df.to_csv(temporary_csv, index=False, encoding="utf-8")
            temporary_csv.replace(file_path)
            uploaded_path.unlink(missing_ok=True)
        else:
            # CSV 保留为原始格式；将上传临时文件原子替换为平台文件名。
            uploaded_path.replace(file_path)
            df = pd.read_csv(file_path)
        row_count = len(df)
        col_count = len(df.columns)
    except Exception as exc:
        for path in (uploaded_path, file_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise HTTPException(
            status_code=400,
            detail=f"无法解析数据文件，未保存上传结果：{exc}",
        ) from exc

    # 更新数据库：保存的是平台内部统一后的 CSV 文件，而非原 Excel。
    dataset.file_path = str(file_path)
    dataset.size_bytes = file_path.stat().st_size
    dataset.row_count = row_count
    
    db.commit()
    db.refresh(dataset)

    try:
        from services.lineage_sync_service import upsert_dataset_node
        upsert_dataset_node(db, dataset_id)
    except Exception as exc:
        print(f"[lineage] 数据集 {dataset_id} 同步血缘失败: {exc}")

    return {
        "id": dataset_id,
        "file_path": dataset.file_path,
        "size_bytes": dataset.size_bytes,
        "row_count": dataset.row_count,
        "columns": col_count,
        "message": f"上传成功: {row_count}行 x {col_count}列"
    }


@router.get("/{dataset_id:int}", dependencies=[Depends(require_permission("datasets:read")), Depends(require_dataset_access)])
def get_dataset(dataset_id: int, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    return dataset.to_dict()


@router.delete("/{dataset_id:int}", dependencies=[Depends(require_permission("datasets:delete")), Depends(require_dataset_access)])
def delete_dataset(dataset_id: int, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    file_path = Path(dataset.file_path) if dataset.file_path else None
    # 先完成数据库事务；清洗/治理历史表使用 ON DELETE SET NULL 保留审计记录。
    db.execute(text("UPDATE datasets SET governed_from = NULL WHERE governed_from = :id"), {"id": dataset_id})
    db.execute(text("UPDATE training_jobs SET dataset_id = NULL WHERE dataset_id = :id"), {"id": dataset_id})
    db.flush()
    db.delete(dataset)
    db.commit()
    artifact_deleted = False
    artifact_error = None
    if file_path and file_path.exists():
        try:
            file_path.unlink()
            artifact_deleted = True
        except OSError as exc:
            artifact_error = str(exc)
    return {
        "status": "deleted",
        "dataset_id": dataset_id,
        "artifact_deleted": artifact_deleted,
        "artifact_error": artifact_error,
    }


@router.get("/{dataset_id:int}/rows", dependencies=[Depends(require_permission("datasets:read")), Depends(require_dataset_access)])
def get_dataset_rows(
    dataset_id: int,
    offset: int = 0,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """数据集行预览 — 读取 CSV 返回 {columns, rows, total, offset, limit}。

    无文件或解析失败时返回空结构(前端按空态展示)。
    """
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")

    empty = {"columns": [], "rows": [], "total": 0, "offset": offset, "limit": limit}
    if not dataset.file_path or not os.path.exists(dataset.file_path):
        return empty

    try:
        # 详情预览必须和上传、分割共用同一套格式读取逻辑。此前这里
        # 只用 csv.reader，导致已成功上传的 Excel 数据集返回空预览。
        frame = _load_split_frame(dataset)
    except Exception:
        return empty

    columns = [str(column) for column in frame.columns]
    total = len(frame)
    if limit <= 0:
        limit = 10
    start = max(0, offset)
    page = frame.iloc[start:start + limit]

    def json_value(value):
        if pd.isna(value):
            return None
        # NumPy 标量、Pandas 时间戳等需要转成 FastAPI 可安全序列化的值。
        if hasattr(value, "item") and not isinstance(value, (str, bytes)):
            try:
                value = value.item()
            except (TypeError, ValueError):
                pass
        if hasattr(value, "isoformat") and not isinstance(value, str):
            try:
                return value.isoformat()
            except (TypeError, ValueError):
                pass
        return value

    rows = [[json_value(value) for value in row] for row in page.itertuples(index=False, name=None)]
    return {
        "columns": columns,
        "rows": rows,
        "total": total,
        "offset": start,
        "limit": limit,
    }


@router.post("/{dataset_id:int}/govern", dependencies=[Depends(require_permission("datasets:process")), Depends(require_dataset_access)])
def govern_dataset(dataset_id: int, db: Session = Depends(get_db)):
    """数据治理：分析 + 清洗 + 提升阶段"""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    
    import pandas as pd, numpy as np
    
    # Load data
    if dataset.file_path and os.path.exists(dataset.file_path):
        df = pd.read_csv(dataset.file_path)
    else:
        raise HTTPException(status_code=400, detail="数据集文件不存在")
    
    # Analyze columns
    cols_info = []
    total = len(df)
    for col in df.columns:
        na_pct = round(df[col].isna().sum() / total * 100, 1) if total > 0 else 0
        unique = int(df[col].nunique()) if df[col].dtype in ['object','int64','float64'] else 0
        cols_info.append({
            "name": str(col),
            "dtype": str(df[col].dtype),
            "missing_pct": na_pct,
            "unique_values": unique,
        })
    
    # Quality score: 100 - penalty for missing values
    avg_missing = np.mean([c["missing_pct"] for c in cols_info]) if cols_info else 0
    quality = round(max(0, 100 - avg_missing * 2), 1)
    
    # Clean: drop fully empty cols, drop rows with too many NaN
    df = df.dropna(axis=1, how='all')
    df = df.dropna(thresh=max(1, len(df.columns) // 2))
    
    # Create a distinct derived record first, so repeated governance runs never
    # overwrite one another or mutate the source dataset's lifecycle stage.
    governed = Dataset(
        name=f"{dataset.name}(已治理)",
        description=f"从 [{dataset.name}] 清洗得到。质量评分: {quality}",
        file_path=None,
        row_count=len(df),
        size_bytes=0,
        stage="governed",
        columns_info=cols_info,
        quality_score=quality,
        governed_from=dataset_id,
        organization_id=dataset.organization_id,
    )
    db.add(governed)
    clean_path = None
    temp_path = None
    try:
        db.flush()
        clean_path = UPLOAD_DIR / f"{governed.id}_governed.csv"
        temp_path = UPLOAD_DIR / f".{governed.id}_governed.tmp"
        df.to_csv(temp_path, index=False, encoding="utf-8")
        temp_path.replace(clean_path)
        governed.file_path = str(clean_path)
        governed.size_bytes = clean_path.stat().st_size

        # Analysis metadata may be refreshed on the source, but its file and
        # stage stay untouched.
        dataset.columns_info = cols_info
        dataset.quality_score = quality
        db.commit()
        db.refresh(governed)
        from services.lineage_sync_service import safe_sync_derived_dataset
        safe_sync_derived_dataset(db, governed.id)
    except Exception:
        db.rollback()
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        if clean_path and clean_path.exists():
            clean_path.unlink(missing_ok=True)
        raise
    
    return {
        "original": dataset.to_dict(),
        "governed": governed.to_dict(),
        "quality_score": quality,
        "removed_rows": total - len(df),
        "columns_analyzed": len(cols_info),
    }



import os as _os

@router.get("/{dataset_id:int}/download", dependencies=[Depends(require_permission("datasets:download")), Depends(require_dataset_access)])
def download_dataset(dataset_id: int, db: Session = Depends(get_db)):
    """下载数据集文件"""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")

    # Check file_path first
    if dataset.file_path and _os.path.exists(dataset.file_path):
        filename = dataset.name or f"dataset_{dataset_id}"
        if not filename.endswith('.csv'):
            filename += '.csv'
        return FileResponse(
            path=dataset.file_path,
            filename=filename,
            media_type='application/octet-stream',
        )

    # Try alternative path
    alt_path = UPLOAD_DIR / f"{dataset_id}_data.csv"
    if alt_path.exists():
        return FileResponse(
            path=str(alt_path),
            filename=f"{dataset.name or 'dataset'}.csv",
            media_type='application/octet-stream',
        )

    # Check data_blob
    if dataset.data_blob:
        from fastapi.responses import Response
        return Response(
            content=dataset.data_blob,
            media_type='application/octet-stream',
            headers={"Content-Disposition": f"attachment; filename={dataset.name or 'data'}.csv"}
        )

    raise HTTPException(status_code=404, detail="数据集文件不存在")


@router.post(
    "/{dataset_id:int}/split/save",
    dependencies=[Depends(require_permission("datasets:process")), Depends(require_dataset_access)],
)
def save_dataset_split(
    dataset_id: int,
    request: DatasetSplitRequest,
    db: Session = Depends(get_db),
):
    """保存训练（含验证）和独立测试两个派生数据集。"""
    source = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not source:
        raise HTTPException(404, "数据集不存在")
    train_frame, test_frame, summary = _split_payload(source, request.strategy)
    train_dataset = Dataset(
        name=summary["train_name"],
        description=f"由数据集 #{source.id} {source.name} 按90%训练/验证、10%测试分割得到的训练数据",
        row_count=len(train_frame), size_bytes=0, stage="split_train",
        columns_info=source.columns_info, quality_score=source.quality_score,
        governed_from=source.id, organization_id=source.organization_id,
    )
    test_dataset = Dataset(
        name=summary["test_name"],
        description=f"由数据集 #{source.id} {source.name} 按90%训练/验证、10%测试分割得到的独立测试数据",
        row_count=len(test_frame), size_bytes=0, stage="split_test",
        columns_info=source.columns_info, quality_score=source.quality_score,
        governed_from=source.id, organization_id=source.organization_id,
    )
    db.add_all([train_dataset, test_dataset])
    final_paths: list[Path] = []
    temp_paths: list[Path] = []
    try:
        db.flush()
        for record, frame, suffix in (
            (train_dataset, train_frame, "train"),
            (test_dataset, test_frame, "test"),
        ):
            final_path = UPLOAD_DIR / f"{record.id}_split_{suffix}.csv"
            temp_path = UPLOAD_DIR / f".{record.id}_split_{suffix}.tmp"
            frame.to_csv(temp_path, index=False, encoding="utf-8")
            temp_path.replace(final_path)
            record.file_path = str(final_path)
            record.size_bytes = final_path.stat().st_size
            final_paths.append(final_path)
            temp_paths.append(temp_path)
        db.commit()
        db.refresh(train_dataset)
        db.refresh(test_dataset)
        from services.lineage_sync_service import safe_sync_derived_dataset
        safe_sync_derived_dataset(db, train_dataset.id)
        safe_sync_derived_dataset(db, test_dataset.id)
    except Exception:
        db.rollback()
        for path in [*temp_paths, *final_paths]:
            path.unlink(missing_ok=True)
        raise
    return {
        **summary,
        "message": "训练数据和测试数据已保存至数据集管理",
        "train_dataset": train_dataset.to_dict(),
        "test_dataset": test_dataset.to_dict(),
    }


@router.post(
    "/{dataset_id:int}/split/download",
    dependencies=[Depends(require_permission("datasets:download")), Depends(require_dataset_access)],
)
def download_dataset_split(
    dataset_id: int,
    request: DatasetSplitRequest,
    db: Session = Depends(get_db),
):
    """不入库，直接下载包含训练和测试CSV的ZIP。"""
    source = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not source:
        raise HTTPException(404, "数据集不存在")
    train_frame, test_frame, summary = _split_payload(source, request.strategy)
    temporary = Path(tempfile.mkdtemp(prefix=f"foundation-dataset-split-{dataset_id}-"))
    try:
        train_name = _safe_split_filename(summary["train_name"]) + ".csv"
        test_name = _safe_split_filename(summary["test_name"]) + ".csv"
        train_path = temporary / train_name
        test_path = temporary / test_name
        archive_path = temporary / (_safe_split_filename(source.name) + "_分割.zip")
        train_frame.to_csv(train_path, index=False, encoding="utf-8")
        test_frame.to_csv(test_path, index=False, encoding="utf-8")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(train_path, arcname=train_name)
            archive.write(test_path, arcname=test_name)
        return FileResponse(
            path=archive_path,
            filename=archive_path.name,
            media_type="application/zip",
            background=BackgroundTask(shutil.rmtree, temporary, ignore_errors=True),
        )
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise



class ProcessedDataRequest(BaseModel):
    name: str
    description: str = ""
    data: list = []

@router.post("/from-processed", dependencies=[Depends(require_permission("datasets:edit"))])
def create_from_processed(req: ProcessedDataRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    name = req.name
    description = req.description
    data = req.data
    """将处理后的数据保存为新数据集"""
    import pandas as pd
    import io
    
    df = pd.DataFrame(data)
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    csv_content = csv_buffer.getvalue()
    
    # Create dataset record
    from models.core import Dataset
    dataset = Dataset(
        name=name,
        description=description,
        row_count=len(df),
        size_bytes=len(csv_content.encode('utf-8')),
        columns_info=str(df.dtypes.to_dict()),
        organization_id=create_organization_id(None, current_user),
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    
    # Store file
    import os
    os.makedirs('/tmp/foundation_datasets', exist_ok=True)
    filepath = f'/tmp/foundation_datasets/{dataset.id}.csv'
    with open(filepath, 'w') as f:
        f.write(csv_content)
    
    return {"id": dataset.id, "name": dataset.name, "row_count": dataset.row_count, "status": "created"}

@router.get("/stats/summary", dependencies=[Depends(require_permission("datasets:read"))])
def dataset_summary(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    """数据集统计摘要"""
    datasets = scope_query(db.query(Dataset), Dataset.organization_id, current_user).all()
    raw = sum(1 for d in datasets if d.stage == "raw" and d.row_count and d.row_count > 0)
    cleaned = sum(1 for d in datasets if d.stage == "cleaned")
    governed = sum(1 for d in datasets if d.stage == "governed")
    total_rows = sum(d.row_count or 0 for d in datasets)
    return {
        "total": len(datasets),
        "raw": raw, "cleaned": cleaned, "governed": governed,
        "total_rows": total_rows,
        "avg_quality": round(sum(d.quality_score or 0 for d in datasets) / max(1, len([d for d in datasets if d.quality_score])), 1),
    }
