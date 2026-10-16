"""
数据目录模块 API
支持元数据发现、分类标签、血缘追踪
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from models.data_catalog import (
    AssetMetadata,
    DataAsset,
    DataClassification,
    DataLineage,
    DataTag,
)
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, text
from sqlalchemy.orm import Session

router = APIRouter(prefix="/catalog", tags=["数据目录"])


# ==================== Pydantic 模型 ====================


class AssetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., pattern="^(table|file|api|model|dataset|stream)$")
    description: Optional[str] = None
    owner: Optional[str] = None
    tags: Optional[List[str]] = []
    sensitivity_level: Optional[str] = "normal"
    quality_score: Optional[int] = Field(0, ge=0, le=100)
    schema_info: Optional[Dict[str, Any]] = None
    storage_info: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    status: Optional[str] = "active"
    user_id: Optional[int] = None


class AssetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    owner: Optional[str] = None
    tags: Optional[List[str]] = None
    sensitivity_level: Optional[str] = None
    quality_score: Optional[int] = Field(None, ge=0, le=100)
    schema_info: Optional[Dict[str, Any]] = None
    storage_info: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    status: Optional[str] = None


class MetadataCreate(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    value: Optional[str] = None
    source: Optional[str] = "manual"
    confidence: Optional[float] = Field(1.0, ge=0, le=1)


class ClassificationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = "#409EFF"
    icon: Optional[str] = None
    rules: Optional[List[Dict[str, Any]]] = []
    parent_id: Optional[int] = None
    sort_order: Optional[int] = 0


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: Optional[str] = "#909399"
    category: Optional[str] = "general"
    description: Optional[str] = None


class LineageCreate(BaseModel):
    source_id: int
    target_id: int
    relationship: Optional[str] = "derives"
    transform_logic: Optional[str] = None
    column_mapping: Optional[Dict[str, str]] = None
    created_by: Optional[int] = None


# ==================== 数据资产 API ====================


@router.get("/assets")
def list_assets(
    q: Optional[str] = Query(None, description="搜索关键词"),
    type: Optional[str] = Query(None, description="资产类型过滤"),
    status: Optional[str] = Query(None, description="状态过滤"),
    sensitivity: Optional[str] = Query(None, description="敏感级别过滤"),
    classification_id: Optional[int] = Query(None, description="分类ID过滤"),
    tag: Optional[str] = Query(None, description="标签过滤"),
    owner: Optional[str] = Query(None, description="所有者过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """获取数据资产列表，支持搜索和多条件过滤"""
    query = db.query(DataAsset)

    # 关键词搜索
    if q:
        search_filter = or_(
            DataAsset.name.ilike(f"%{q}%"),
            DataAsset.description.ilike(f"%{q}%"),
            DataAsset.owner.ilike(f"%{q}%"),
        )
        query = query.filter(search_filter)

    # 类型过滤
    if type:
        query = query.filter(DataAsset.type == type)

    # 状态过滤
    if status:
        query = query.filter(DataAsset.status == status)

    # 敏感级别过滤
    if sensitivity:
        query = query.filter(DataAsset.sensitivity_level == sensitivity)

    # 所有者过滤
    if owner:
        query = query.filter(DataAsset.owner == owner)

    # 分类过滤
    if classification_id:
        query = query.join(DataAsset.classification_objects).filter(
            DataClassification.id == classification_id
        )

    # 标签过滤
    if tag:
        query = query.join(DataAsset.tag_objects).filter(DataTag.name == tag)

    # 计算总数
    total = query.count()

    # 分页
    offset = (page - 1) * page_size
    assets = (
        query.order_by(DataAsset.updated_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    # 转换为响应格式
    items = []
    for asset in assets:
        asset_dict = {
            "id": asset.id,
            "name": asset.name,
            "type": asset.type,
            "description": asset.description,
            "owner": asset.owner,
            "tags": asset.tags or [],
            "sensitivity_level": asset.sensitivity_level,
            "quality_score": asset.quality_score,
            "source": asset.source,
            "status": asset.status,
            "version": asset.version,
            "created_at": asset.created_at.isoformat() if asset.created_at else None,
            "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
            "user_id": asset.user_id,
        }
        items.append(asset_dict)

    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/entries")
def list_entries(
    q: Optional[str] = Query(None, description="搜索关键词"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """数据目录条目列表 — 轻量视图,供前端 DataHubCatalog 合并展示。

    返回 {items:[{name,type,description,tags,owner,updated_at,...}]}。
    优先返回 data_assets 表;表为空时回退到 datasets 表,保证非空。
    """
    query = db.query(DataAsset).filter(DataAsset.status != "archived")
    if q:
        query = query.filter(
            or_(
                DataAsset.name.ilike(f"%{q}%"),
                DataAsset.description.ilike(f"%{q}%"),
            )
        )
    assets = query.order_by(DataAsset.updated_at.desc()).limit(limit).all()

    items = []
    for a in assets:
        items.append(
            {
                "id": a.id,
                "name": a.name,
                "type": a.type,
                "description": a.description,
                "owner": a.owner,
                "tags": a.tags or [],
                "status": a.status,
                "quality_score": a.quality_score,
                "source": a.source,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
        )

    # 回退:data_assets 表为空时,从 datasets 表生成条目,保证前端非空
    if not items:
        try:
            rows = db.execute(
                text(
                    "SELECT id, name, COALESCE(description,'') AS description, "
                    "       stage, row_count, created_at "
                    "FROM datasets ORDER BY created_at DESC LIMIT :lim"
                ),
                {"lim": limit},
            ).fetchall()
            for r in rows:
                created = r[5].isoformat() if r[5] else None
                items.append(
                    {
                        "id": r[0],
                        "name": r[1],
                        "type": "dataset",
                        "description": r[2],
                        "owner": None,
                        "tags": [],
                        "rows": r[4],
                        "updated_at": created,
                        "created_at": created,
                        "source": "datasets",
                    }
                )
        except Exception:
            pass

    return {"items": items, "total": len(items)}


@router.post("/assets")
def create_asset(asset: AssetCreate, db: Session = Depends(get_db)):
    """创建数据资产"""
    # 检查名称是否重复
    existing = db.query(DataAsset).filter(DataAsset.name == asset.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="资产名称已存在")

    # 创建资产
    db_asset = DataAsset(
        name=asset.name,
        type=asset.type,
        description=asset.description,
        owner=asset.owner,
        tags=asset.tags,
        sensitivity_level=asset.sensitivity_level,
        quality_score=asset.quality_score,
        schema_info=asset.schema_info,
        storage_info=asset.storage_info,
        source=asset.source,
        status=asset.status,
        user_id=asset.user_id,
    )

    db.add(db_asset)
    db.commit()
    db.refresh(db_asset)

    # 处理标签
    if asset.tags:
        for tag_name in asset.tags:
            tag = db.query(DataTag).filter(DataTag.name == tag_name).first()
            if not tag:
                tag = DataTag(name=tag_name)
                db.add(tag)
                db.commit()
            db_asset.tag_objects.append(tag)
        db.commit()

    return {"id": db_asset.id, "name": db_asset.name, "message": "资产创建成功"}


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    """获取数据资产详情"""
    asset = db.query(DataAsset).filter(DataAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    # 获取元数据
    metadata = db.query(AssetMetadata).filter(AssetMetadata.asset_id == asset_id).all()

    # 获取分类
    classifications = [
        {"id": c.id, "name": c.name, "color": c.color, "icon": c.icon}
        for c in asset.classification_objects
    ]

    # 获取标签
    tags = [
        {"id": t.id, "name": t.name, "color": t.color, "category": t.category}
        for t in asset.tag_objects
    ]

    # 获取上游血缘
    upstream = db.query(DataLineage).filter(DataLineage.target_id == asset_id).all()
    upstream_assets = []
    for lineage in upstream:
        source = db.query(DataAsset).filter(DataAsset.id == lineage.source_id).first()
        if source:
            upstream_assets.append(
                {
                    "id": source.id,
                    "name": source.name,
                    "type": source.type,
                    "relationship": lineage.relationship,
                    "transform_logic": lineage.transform_logic,
                }
            )

    # 获取下游血缘
    downstream = db.query(DataLineage).filter(DataLineage.source_id == asset_id).all()
    downstream_assets = []
    for lineage in downstream:
        target = db.query(DataAsset).filter(DataAsset.id == lineage.target_id).first()
        if target:
            downstream_assets.append(
                {
                    "id": target.id,
                    "name": target.name,
                    "type": target.type,
                    "relationship": lineage.relationship,
                    "transform_logic": lineage.transform_logic,
                }
            )

    return {
        "id": asset.id,
        "name": asset.name,
        "type": asset.type,
        "description": asset.description,
        "owner": asset.owner,
        "tags": asset.tags or [],
        "tag_objects": tags,
        "classifications": classifications,
        "sensitivity_level": asset.sensitivity_level,
        "quality_score": asset.quality_score,
        "schema_info": asset.schema_info,
        "storage_info": asset.storage_info,
        "source": asset.source,
        "status": asset.status,
        "version": asset.version,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
        "user_id": asset.user_id,
        "metadata": [
            {
                "id": m.id,
                "key": m.key,
                "value": m.value,
                "source": m.source,
                "confidence": m.confidence,
            }
            for m in metadata
        ],
        "lineage": {"upstream": upstream_assets, "downstream": downstream_assets},
    }


@router.put("/assets/{asset_id}")
def update_asset(asset_id: int, asset: AssetUpdate, db: Session = Depends(get_db)):
    """更新数据资产"""
    db_asset = db.query(DataAsset).filter(DataAsset.id == asset_id).first()
    if not db_asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    # 更新字段
    update_data = asset.dict(exclude_unset=True)
    for key, value in update_data.items():
        if key == "tags" and value is not None:
            # 处理标签更新
            db_asset.tags = value
            # 更新标签关联
            db_asset.tag_objects = []
            for tag_name in value:
                tag = db.query(DataTag).filter(DataTag.name == tag_name).first()
                if not tag:
                    tag = DataTag(name=tag_name)
                    db.add(tag)
                    db.commit()
                db_asset.tag_objects.append(tag)
        else:
            setattr(db_asset, key, value)

    db.commit()
    db.refresh(db_asset)

    return {"id": db_asset.id, "name": db_asset.name, "message": "资产更新成功"}


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db)):
    from sqlalchemy import text

    result = db.execute(
        text("SELECT id FROM data_assets WHERE id = :id"), {"id": asset_id}
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Asset not found")
    try:
        db.execute(
            text("DELETE FROM asset_tags WHERE asset_id = :id"), {"id": asset_id}
        )
    except:
        pass
    try:
        db.execute(
            text("DELETE FROM asset_classifications WHERE asset_id = :id"),
            {"id": asset_id},
        )
    except:
        pass
    try:
        db.execute(
            text("DELETE FROM asset_metadata WHERE asset_id = :id"), {"id": asset_id}
        )
    except:
        pass
    db.execute(text("DELETE FROM data_assets WHERE id = :id"), {"id": asset_id})
    db.commit()
    return {"message": "Asset deleted successfully"}


@router.post("/assets/{asset_id}/metadata")
def add_metadata(
    asset_id: int, metadata: MetadataCreate, db: Session = Depends(get_db)
):
    """添加元数据"""
    asset = db.query(DataAsset).filter(DataAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    # 检查是否已存在相同key
    existing = (
        db.query(AssetMetadata)
        .filter(AssetMetadata.asset_id == asset_id, AssetMetadata.key == metadata.key)
        .first()
    )

    if existing:
        # 更新现有记录
        existing.value = metadata.value
        existing.source = metadata.source
        existing.confidence = metadata.confidence
    else:
        # 创建新记录
        db_metadata = AssetMetadata(
            asset_id=asset_id,
            key=metadata.key,
            value=metadata.value,
            source=metadata.source,
            confidence=metadata.confidence,
        )
        db.add(db_metadata)

    db.commit()

    return {"message": "元数据添加成功"}


@router.get("/assets/{asset_id}/metadata")
def get_metadata(asset_id: int, db: Session = Depends(get_db)):
    """获取资产元数据"""
    metadata = db.query(AssetMetadata).filter(AssetMetadata.asset_id == asset_id).all()

    return {
        "asset_id": asset_id,
        "metadata": [
            {
                "id": m.id,
                "key": m.key,
                "value": m.value,
                "source": m.source,
                "confidence": m.confidence,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in metadata
        ],
    }


@router.delete("/assets/{asset_id}/metadata/{metadata_id}")
def delete_metadata(asset_id: int, metadata_id: int, db: Session = Depends(get_db)):
    """删除元数据"""
    metadata = (
        db.query(AssetMetadata)
        .filter(AssetMetadata.id == metadata_id, AssetMetadata.asset_id == asset_id)
        .first()
    )

    if not metadata:
        raise HTTPException(status_code=404, detail="元数据不存在")

    db.delete(metadata)
    db.commit()

    return {"message": "元数据删除成功"}


# ==================== 分类 API ====================


@router.get("/classifications")
def list_classifications(db: Session = Depends(get_db)):
    """获取分类列表（树形结构）"""
    # 获取所有分类
    classifications = (
        db.query(DataClassification).order_by(DataClassification.sort_order).all()
    )

    # 构建树形结构
    classification_map = {}
    root_classifications = []

    for c in classifications:
        classification_map[c.id] = {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "color": c.color,
            "icon": c.icon,
            "parent_id": c.parent_id,
            "sort_order": c.sort_order,
            "children": [],
        }

    for c in classifications:
        if c.parent_id is None:
            root_classifications.append(classification_map[c.id])
        elif c.parent_id in classification_map:
            classification_map[c.parent_id]["children"].append(classification_map[c.id])

    return {"total": len(classifications), "items": root_classifications}


@router.post("/classifications")
def create_classification(
    classification: ClassificationCreate, db: Session = Depends(get_db)
):
    """创建分类"""
    # 检查名称是否重复
    existing = (
        db.query(DataClassification)
        .filter(DataClassification.name == classification.name)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="分类名称已存在")

    # 检查父分类是否存在
    if classification.parent_id:
        parent = (
            db.query(DataClassification)
            .filter(DataClassification.id == classification.parent_id)
            .first()
        )
        if not parent:
            raise HTTPException(status_code=400, detail="父分类不存在")

    db_classification = DataClassification(
        name=classification.name,
        description=classification.description,
        color=classification.color,
        icon=classification.icon,
        rules=classification.rules,
        parent_id=classification.parent_id,
        sort_order=classification.sort_order,
    )

    db.add(db_classification)
    db.commit()
    db.refresh(db_classification)

    return {
        "id": db_classification.id,
        "name": db_classification.name,
        "message": "分类创建成功",
    }


@router.put("/classifications/{classification_id}")
def update_classification(
    classification_id: int,
    classification: ClassificationCreate,
    db: Session = Depends(get_db),
):
    """更新分类"""
    db_classification = (
        db.query(DataClassification)
        .filter(DataClassification.id == classification_id)
        .first()
    )
    if not db_classification:
        raise HTTPException(status_code=404, detail="分类不存在")

    update_data = classification.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_classification, key, value)

    db.commit()
    db.refresh(db_classification)

    return {
        "id": db_classification.id,
        "name": db_classification.name,
        "message": "分类更新成功",
    }


@router.delete("/classifications/{classification_id}")
def delete_classification(classification_id: int, db: Session = Depends(get_db)):
    """删除分类"""
    db_classification = (
        db.query(DataClassification)
        .filter(DataClassification.id == classification_id)
        .first()
    )
    if not db_classification:
        raise HTTPException(status_code=404, detail="分类不存在")

    # 检查是否有子分类
    children = (
        db.query(DataClassification)
        .filter(DataClassification.parent_id == classification_id)
        .count()
    )
    if children > 0:
        raise HTTPException(status_code=400, detail="该分类下有子分类，无法删除")

    db.delete(db_classification)
    db.commit()

    return {"message": "分类删除成功"}


@router.post("/assets/{asset_id}/classifications/{classification_id}")
def assign_classification(
    asset_id: int, classification_id: int, db: Session = Depends(get_db)
):
    """为资产分配分类"""
    asset = db.query(DataAsset).filter(DataAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    classification = (
        db.query(DataClassification)
        .filter(DataClassification.id == classification_id)
        .first()
    )
    if not classification:
        raise HTTPException(status_code=404, detail="分类不存在")

    if classification not in asset.classification_objects:
        asset.classification_objects.append(classification)
        db.commit()

    return {"message": "分类分配成功"}


# ==================== 标签 API ====================


@router.get("/tags")
def list_tags(
    category: Optional[str] = Query(None, description="标签类别过滤"),
    db: Session = Depends(get_db),
):
    """获取标签列表"""
    query = db.query(DataTag)

    if category:
        query = query.filter(DataTag.category == category)

    tags = query.order_by(DataTag.name).all()

    return {
        "total": len(tags),
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "color": t.color,
                "category": t.category,
                "description": t.description,
                "asset_count": len(t.assets),
            }
            for t in tags
        ],
    }


@router.post("/tags")
def create_tag(tag: TagCreate, db: Session = Depends(get_db)):
    """创建标签"""
    # 检查名称是否重复
    existing = db.query(DataTag).filter(DataTag.name == tag.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="标签名称已存在")

    db_tag = DataTag(
        name=tag.name,
        color=tag.color,
        category=tag.category,
        description=tag.description,
    )

    db.add(db_tag)
    db.commit()
    db.refresh(db_tag)

    return {"id": db_tag.id, "name": db_tag.name, "message": "标签创建成功"}


@router.put("/tags/{tag_id}")
def update_tag(tag_id: int, tag: TagCreate, db: Session = Depends(get_db)):
    """更新标签"""
    db_tag = db.query(DataTag).filter(DataTag.id == tag_id).first()
    if not db_tag:
        raise HTTPException(status_code=404, detail="标签不存在")

    update_data = tag.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_tag, key, value)

    db.commit()
    db.refresh(db_tag)

    return {"id": db_tag.id, "name": db_tag.name, "message": "标签更新成功"}


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    """删除标签"""
    db_tag = db.query(DataTag).filter(DataTag.id == tag_id).first()
    if not db_tag:
        raise HTTPException(status_code=404, detail="标签不存在")

    db.delete(db_tag)
    db.commit()

    return {"message": "标签删除成功"}


# ==================== 血缘关系 API ====================


@router.post("/assets/{asset_id}/lineage")
def add_lineage(asset_id: int, lineage: LineageCreate, db: Session = Depends(get_db)):
    """添加血缘关系"""
    # 验证资产存在
    source = db.query(DataAsset).filter(DataAsset.id == lineage.source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="源资产不存在")

    target = db.query(DataAsset).filter(DataAsset.id == lineage.target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标资产不存在")

    # 检查是否已存在相同血缘关系
    existing = (
        db.query(DataLineage)
        .filter(
            DataLineage.source_id == lineage.source_id,
            DataLineage.target_id == lineage.target_id,
        )
        .first()
    )

    if existing:
        raise HTTPException(status_code=400, detail="血缘关系已存在")

    db_lineage = DataLineage(
        source_id=lineage.source_id,
        target_id=lineage.target_id,
        relationship=lineage.relationship,
        transform_logic=lineage.transform_logic,
        column_mapping=lineage.column_mapping,
        created_by=lineage.created_by,
    )

    db.add(db_lineage)
    db.commit()
    db.refresh(db_lineage)

    return {"id": db_lineage.id, "message": "血缘关系添加成功"}


@router.get("/assets/{asset_id}/lineage")
def get_lineage(
    asset_id: int, depth: int = Query(3, ge=1, le=5), db: Session = Depends(get_db)
):
    """获取资产血缘图谱"""
    asset = db.query(DataAsset).filter(DataAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")

    def get_upstream(asset_id: int, current_depth: int, visited: set):
        if current_depth > depth or asset_id in visited:
            return []
        visited.add(asset_id)

        lineages = db.query(DataLineage).filter(DataLineage.target_id == asset_id).all()
        result = []

        for lineage in lineages:
            source = (
                db.query(DataAsset).filter(DataAsset.id == lineage.source_id).first()
            )
            if source:
                node = {
                    "id": source.id,
                    "name": source.name,
                    "type": source.type,
                    "relationship": lineage.relationship,
                    "transform_logic": lineage.transform_logic,
                    "upstream": get_upstream(
                        source.id, current_depth + 1, visited.copy()
                    ),
                }
                result.append(node)

        return result

    def get_downstream(asset_id: int, current_depth: int, visited: set):
        if current_depth > depth or asset_id in visited:
            return []
        visited.add(asset_id)

        lineages = db.query(DataLineage).filter(DataLineage.source_id == asset_id).all()
        result = []

        for lineage in lineages:
            target = (
                db.query(DataAsset).filter(DataAsset.id == lineage.target_id).first()
            )
            if target:
                node = {
                    "id": target.id,
                    "name": target.name,
                    "type": target.type,
                    "relationship": lineage.relationship,
                    "transform_logic": lineage.transform_logic,
                    "downstream": get_downstream(
                        target.id, current_depth + 1, visited.copy()
                    ),
                }
                result.append(node)

        return result

    return {
        "asset_id": asset_id,
        "asset_name": asset.name,
        "asset_type": asset.type,
        "upstream": get_upstream(asset_id, 1, set()),
        "downstream": get_downstream(asset_id, 1, set()),
    }


@router.delete("/lineage/{lineage_id}")
def delete_lineage(lineage_id: int, db: Session = Depends(get_db)):
    """删除血缘关系"""
    lineage = db.query(DataLineage).filter(DataLineage.id == lineage_id).first()
    if not lineage:
        raise HTTPException(status_code=404, detail="血缘关系不存在")

    db.delete(lineage)
    db.commit()

    return {"message": "血缘关系删除成功"}


# ==================== 搜索 API ====================


@router.get("/search")
def search_catalog(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    type: Optional[str] = Query(None, description="资产类型过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """搜索数据目录"""
    # 搜索资产
    query = db.query(DataAsset).filter(
        or_(
            DataAsset.name.ilike(f"%{q}%"),
            DataAsset.description.ilike(f"%{q}%"),
            DataAsset.owner.ilike(f"%{q}%"),
        )
    )

    if type:
        query = query.filter(DataAsset.type == type)

    total = query.count()
    offset = (page - 1) * page_size
    assets = (
        query.order_by(DataAsset.updated_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    # 搜索标签
    tags = db.query(DataTag).filter(DataTag.name.ilike(f"%{q}%")).all()

    # 搜索分类
    classifications = (
        db.query(DataClassification)
        .filter(DataClassification.name.ilike(f"%{q}%"))
        .all()
    )

    return {
        "query": q,
        "assets": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "id": a.id,
                    "name": a.name,
                    "type": a.type,
                    "description": a.description,
                    "owner": a.owner,
                    "sensitivity_level": a.sensitivity_level,
                    "quality_score": a.quality_score,
                }
                for a in assets
            ],
        },
        "tags": [{"id": t.id, "name": t.name, "category": t.category} for t in tags],
        "classifications": [
            {"id": c.id, "name": c.name, "description": c.description}
            for c in classifications
        ],
    }


# ==================== 统计 API ====================


@router.get("/stats")
def get_catalog_stats(db: Session = Depends(get_db)):
    """获取数据目录统计"""
    # 总资产数
    total_assets = db.query(DataAsset).count()

    # 按类型统计
    assets_by_type = db.execute(
        text(
            """
        SELECT type, COUNT(*) as count
        FROM data_assets
        GROUP BY type
    """
        )
    ).fetchall()

    # 按状态统计
    assets_by_status = db.execute(
        text(
            """
        SELECT status, COUNT(*) as count
        FROM data_assets
        GROUP BY status
    """
        )
    ).fetchall()

    # 按敏感级别统计
    assets_by_sensitivity = db.execute(
        text(
            """
        SELECT sensitivity_level, COUNT(*) as count
        FROM data_assets
        GROUP BY sensitivity_level
    """
        )
    ).fetchall()

    # 总标签数
    total_tags = db.query(DataTag).count()

    # 总分类数
    total_classifications = db.query(DataClassification).count()

    # 平均质量得分
    avg_quality = (
        db.execute(
            text(
                """
        SELECT AVG(quality_score) as avg_score
        FROM data_assets
    """
            )
        ).scalar()
        or 0
    )

    # 近期活跃资产
    recent_assets = (
        db.query(DataAsset).order_by(DataAsset.updated_at.desc()).limit(10).all()
    )

    return {
        "total_assets": total_assets,
        "assets_by_type": {r[0]: r[1] for r in assets_by_type},
        "assets_by_status": {r[0]: r[1] for r in assets_by_status},
        "assets_by_sensitivity": {r[0]: r[1] for r in assets_by_sensitivity},
        "total_tags": total_tags,
        "total_classifications": total_classifications,
        "avg_quality_score": round(avg_quality, 2),
        "recent_assets": [
            {
                "id": a.id,
                "name": a.name,
                "type": a.type,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }
            for a in recent_assets
        ],
    }


# ==================== 批量操作 API ====================


@router.post("/assets/batch/delete")
def batch_delete_assets(asset_ids: List[int], db: Session = Depends(get_db)):
    """批量删除资产"""
    deleted = (
        db.query(DataAsset)
        .filter(DataAsset.id.in_(asset_ids))
        .delete(synchronize_session=False)
    )
    db.commit()

    return {"deleted_count": deleted, "message": f"成功删除 {deleted} 个资产"}


@router.post("/assets/batch/tag")
def batch_tag_assets(
    asset_ids: List[int], tags: List[str], db: Session = Depends(get_db)
):
    """批量添加标签"""
    assets = db.query(DataAsset).filter(DataAsset.id.in_(asset_ids)).all()

    # 获取或创建标签
    tag_objects = []
    for tag_name in tags:
        tag = db.query(DataTag).filter(DataTag.name == tag_name).first()
        if not tag:
            tag = DataTag(name=tag_name)
            db.add(tag)
            db.commit()
        tag_objects.append(tag)

    # 为资产添加标签
    for asset in assets:
        for tag in tag_objects:
            if tag not in asset.tag_objects:
                asset.tag_objects.append(tag)

    db.commit()

    return {"updated_count": len(assets), "message": f"成功为 {len(assets)} 个资产添加标签"}


# ==================== 数据扫描 API ====================


@router.post("/scan")
def scan_data_sources(db: Session = Depends(get_db)):
    """
    扫描数据源，自动发现和注册数据资产
    """
    from sqlalchemy import inspect

    discovered = []

    try:
        # 扫描数据库表
        inspector = inspect(db.bind)
        tables = inspector.get_table_names()

        # 过滤掉系统表
        system_tables = [
            "alembic_version",
            "data_assets",
            "data_tags",
            "data_classifications",
            "asset_tags",
            "asset_classifications",
            "data_asset_lineage",
            "asset_metadata",
            "lineage_nodes",
            "lineage_edges",
            "lineage_graphs",
            "lineage_change_logs",
            "users",
            "projects",
            "models",
            "training_jobs",
            "deployments",
            "datasets",
            "model_training_jobs",
        ]

        for table in tables:
            if table in system_tables:
                continue

            # 检查是否已注册
            existing = db.query(DataAsset).filter(DataAsset.name == table).first()
            if existing:
                continue

            # 获取表行数
            try:
                count_result = db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                row_count = count_result.scalar() or 0
            except:
                row_count = 0

            if row_count > 0:
                # 自动注册
                asset = DataAsset(
                    name=table,
                    type="table",
                    description=f"Auto discovered table: {table}",
                    owner="system",
                    source=f"database://{table}",
                    quality_score=80,
                    sensitivity_level="internal",
                    status="active",
                )
                db.add(asset)
                discovered.append(
                    {"name": table, "type": "table", "row_count": row_count}
                )

        db.commit()

        return {
            "message": f"Scan completed, discovered {len(discovered)} new data assets",
            "discovered": discovered,
            "total_tables": len(tables),
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Scan failed: {str(e)}")


@router.post("/scan/database")
def scan_database_tables(db: Session = Depends(get_db)):
    """
    扫描数据库中的所有表
    """
    from sqlalchemy import inspect

    try:
        inspector = inspect(db.bind)
        tables = inspector.get_table_names()

        results = []
        for table in tables:
            try:
                count_result = db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                row_count = count_result.scalar() or 0
            except:
                row_count = 0

            # 检查是否已注册
            registered = db.query(DataAsset).filter(DataAsset.name == table).first()

            results.append(
                {
                    "table_name": table,
                    "row_count": row_count,
                    "registered": registered is not None,
                }
            )

        return {"total_tables": len(tables), "tables": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scan failed: {str(e)}")
