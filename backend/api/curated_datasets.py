"""Shared, read-only verification datasets.

Curated assets use data_assets for descriptive metadata and refer to a backing
Dataset through storage_info.dataset_id. Normal workspace datasets remain
tenant-scoped, while a small, explicitly published library can be browsed and
copied into a user's workspace.
"""
from __future__ import annotations

import csv
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from api.deps import require_auth, require_permission
from api.tenant_access import create_organization_id
from database.config import get_db
from models.core import Dataset
from models.data_catalog import DataAsset

router = APIRouter(prefix="/curated-datasets", tags=["验证数据资产库"])
UPLOAD_DIR = Path("/app/uploads/datasets")


def _published_assets(db: Session):
    return (
        db.query(DataAsset)
        .filter(
            DataAsset.type == "dataset",
            DataAsset.status == "active",
            DataAsset.sensitivity_level == "public",
        )
        .order_by(DataAsset.updated_at.desc(), DataAsset.id.desc())
    )


def _asset_payload(asset: DataAsset) -> dict[str, Any]:
    storage = asset.storage_info or {}
    schema = asset.schema_info or {}
    return {
        "id": asset.id,
        "name": asset.name,
        "description": asset.description,
        "tags": asset.tags or [],
        "domain": schema.get("domain", "未分类"),
        "scenario": schema.get("scenario", ""),
        "granularity": schema.get("granularity", ""),
        "recommended_tasks": schema.get("recommended_tasks", []),
        "source": asset.source,
        "license": schema.get("license", "待核验"),
        "source_url": schema.get("source_url", ""),
        "quality_score": asset.quality_score,
        "row_count": storage.get("row_count", 0),
        "size_bytes": storage.get("size_bytes", 0),
        "format": storage.get("format", "CSV"),
        "version": asset.version,
        "dataset_id": storage.get("dataset_id"),
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


def _published_asset(asset_id: int, db: Session) -> DataAsset:
    asset = _published_assets(db).filter(DataAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="验证数据资产不存在或未发布")
    return asset


def _backing_dataset(asset: DataAsset, db: Session) -> Dataset:
    dataset_id = (asset.storage_info or {}).get("dataset_id")
    if not dataset_id:
        raise HTTPException(status_code=409, detail="验证数据资产尚未关联数据文件")
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset or not dataset.file_path or not os.path.exists(dataset.file_path):
        raise HTTPException(status_code=409, detail="验证数据资产文件不可用")
    return dataset


@router.get("", dependencies=[Depends(require_permission("datasets:read"))])
def list_curated_datasets(db: Session = Depends(get_db), _user: dict = Depends(require_auth)):
    """List explicitly published assets; this is not a tenant-wide dataset list."""
    items = [_asset_payload(asset) for asset in _published_assets(db).all()]
    return {"total": len(items), "items": items}


@router.get("/{asset_id}", dependencies=[Depends(require_permission("datasets:read"))])
def get_curated_dataset(asset_id: int, db: Session = Depends(get_db), _user: dict = Depends(require_auth)):
    return _asset_payload(_published_asset(asset_id, db))


@router.get("/{asset_id}/rows", dependencies=[Depends(require_permission("datasets:read"))])
def preview_curated_dataset(asset_id: int, limit: int = 10, db: Session = Depends(get_db), _user: dict = Depends(require_auth)):
    dataset = _backing_dataset(_published_asset(asset_id, db), db)
    limit = max(1, min(limit, 100))
    try:
        with open(dataset.file_path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            columns = next(reader, [])
            rows = []
            for row in reader:
                rows.append((row + [None] * len(columns))[: len(columns)])
                if len(rows) >= limit:
                    break
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(status_code=422, detail="验证数据文件无法预览") from exc
    return {"columns": columns, "rows": rows, "total": dataset.row_count or 0}


@router.get("/{asset_id}/download", dependencies=[Depends(require_permission("datasets:read"))])
def download_curated_dataset(asset_id: int, db: Session = Depends(get_db), _user: dict = Depends(require_auth)):
    asset = _published_asset(asset_id, db)
    dataset = _backing_dataset(asset, db)
    filename = f"{asset.name}.csv".replace("/", "_")
    return FileResponse(dataset.file_path, filename=filename, media_type="text/csv")


@router.post("/{asset_id}/copy-to-workspace", dependencies=[Depends(require_permission("datasets:upload"))])
def copy_curated_dataset(asset_id: int, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    """Create an independently tenant-owned working copy of a published asset."""
    asset = _published_asset(asset_id, db)
    source = _backing_dataset(asset, db)
    target = Dataset(
        name=f"{asset.name}（工作副本）",
        description=f"从验证数据资产库复制。来源：{asset.source or '未说明'}。",
        row_count=source.row_count,
        size_bytes=source.size_bytes,
        stage="raw",
        organization_id=create_organization_id(None, current_user),
    )
    db.add(target)
    db.flush()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target_path = UPLOAD_DIR / f"{target.id}_data.csv"
    shutil.copyfile(source.file_path, target_path)
    target.file_path = str(target_path)
    db.commit()
    db.refresh(target)
    return target.to_dict()
