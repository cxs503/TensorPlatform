"""
数据血缘 API
实现完整的数据血缘追踪系统
"""
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from api.deps import require_auth, require_permission
from api.tenant_access import is_admin, require_organization, scope_query
from services.lineage_sql_extractor import extract_sql_lineage as sqlglot_extract, extract_column_lineage as sqlglot_columns
from fastapi.responses import JSONResponse
from models.lineage import LineageChangeLog, LineageEdge, LineageGraph, LineageNode
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/lineage", tags=["数据血缘"],
    dependencies=[Depends(require_permission("datasets:read"))],
)


def lineage_organization(current_user: dict = Depends(require_auth)) -> int | None:
    return None if is_admin(current_user) else require_organization(current_user)


def _scope(query, column, organization_id: int | None):
    return query if organization_id is None else query.filter(column == organization_id)

# ==================== Pydantic Schemas ====================


class NodeCreate(BaseModel):
    """创建节点请求"""

    name: str = Field(..., min_length=1, max_length=255)
    display_name: Optional[str] = None
    node_type: str = Field(
        ..., pattern="^(source|table|column|report|model|pipeline|api)$"
    )
    system: Optional[str] = None
    database: Optional[str] = None
    schema_name: Optional[str] = None
    full_path: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = {}
    tags: Optional[List[str]] = []
    owner: Optional[str] = None
    quality_score: Optional[int] = 0
    created_by: Optional[str] = None


class NodeUpdate(BaseModel):
    """更新节点请求"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    display_name: Optional[str] = None
    node_type: Optional[str] = Field(
        None, pattern="^(source|table|column|report|model|pipeline|api)$"
    )
    system: Optional[str] = None
    database: Optional[str] = None
    schema_name: Optional[str] = None
    full_path: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    owner: Optional[str] = None
    quality_score: Optional[int] = None
    is_active: Optional[bool] = None


class EdgeCreate(BaseModel):
    """创建边请求"""

    source_id: str
    target_id: str
    edge_type: Optional[str] = "derived"
    transformation: Optional[str] = None
    transformation_type: Optional[str] = "sql"
    column_mapping: Optional[List[Dict[str, str]]] = []
    impact_weight: Optional[int] = 1
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = {}
    created_by: Optional[str] = None


class EdgeUpdate(BaseModel):
    """更新边请求"""

    edge_type: Optional[str] = None
    transformation: Optional[str] = None
    transformation_type: Optional[str] = None
    column_mapping: Optional[List[Dict[str, str]]] = None
    impact_weight: Optional[int] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class SQLLineageRequest(BaseModel):
    """SQL血缘提取请求"""

    sql: str
    database: Optional[str] = None
    schema_name: Optional[str] = None
    persist: bool = True


def _enum_value(value: Any) -> str:
    """Return a stable string for SQLAlchemy enums and historical strings."""
    return str(getattr(value, "value", value) or "unknown")


def _upsert_discovered_node(
    db: Session,
    *,
    node_id: str,
    name: str,
    display_name: str,
    node_type: str,
    system: str,
    description: str,
    metadata: Optional[Dict[str, Any]] = None,
    created_at: Optional[datetime] = None,
) -> tuple[LineageNode, bool]:
    node = db.query(LineageNode).filter(LineageNode.id == node_id).first()
    created = node is None
    if node is None:
        node = LineageNode(id=node_id, name=name, created_by="training_lineage_sync")
        db.add(node)
    node.name = name
    node.display_name = display_name or name
    node.node_type = node_type
    node.system = system
    node.description = description
    node.full_path = f"{system}.{node_id}"
    node.extra_metadata = metadata or {}
    node.tags = ["自动发现", "训练血缘"]
    node.owner = "system"
    node.is_active = 1
    if created_at and created:
        node.created_at = created_at
    node.updated_at = datetime.now()
    return node, created


def _upsert_discovered_edge(
    db: Session,
    *,
    edge_id: str,
    source_id: str,
    target_id: str,
    edge_type: str,
    transformation: str,
) -> tuple[LineageEdge, bool]:
    edge = (
        db.query(LineageEdge)
        .filter(
            LineageEdge.source_id == source_id,
            LineageEdge.target_id == target_id,
        )
        .first()
    )
    created = edge is None
    if edge is None:
        edge = LineageEdge(
            id=edge_id,
            source_id=source_id,
            target_id=target_id,
            created_by="training_lineage_sync",
        )
        db.add(edge)
    edge.edge_type = edge_type
    edge.transformation = transformation
    edge.transformation_type = "platform"
    edge.extra_metadata = {"source": "training_jobs"}
    edge.is_active = 1
    edge.updated_at = datetime.now()
    return edge, created


def _sync_training_lineage(db: Session, limit: int = 200) -> Dict[str, int]:
    """Persist dataset → training job → model lineage for historical jobs."""
    rows = db.execute(text("""
        SELECT tj.id, tj.training_job_id, tj.name, tj.dataset_id, tj.status,
               tj.created_at, d.name AS dataset_name,
               COALESCE(tj.registered_model_id, tj.model_id) AS result_model_id,
               m.name AS model_name
        FROM training_jobs tj
        LEFT JOIN datasets d ON d.id = tj.dataset_id
        LEFT JOIN models m ON m.id = COALESCE(tj.registered_model_id, tj.model_id)
        WHERE tj.dataset_id IS NOT NULL
        ORDER BY tj.id DESC
        LIMIT :limit
    """), {"limit": limit}).fetchall()

    created_nodes = 0
    updated_nodes = 0
    created_edges = 0
    updated_edges = 0
    for row in rows:
        job_db_id, job_ref, job_name, dataset_id, status, created_at, dataset_name, model_id, model_name = row
        dataset_node_id = f"ln-dataset-{dataset_id}"
        job_node_id = f"ln-job-{job_db_id}"
        _, created = _upsert_discovered_node(
            db,
            node_id=dataset_node_id,
            name=dataset_name or f"数据集#{dataset_id}",
            display_name=dataset_name or f"数据集#{dataset_id}",
            node_type="table",
            system="data_platform",
            description=f"训练任务引用的数据集 #{dataset_id}",
            metadata={"dataset_id": dataset_id},
        )
        created_nodes += int(created)
        updated_nodes += int(not created)

        _, created = _upsert_discovered_node(
            db,
            node_id=job_node_id,
            name=job_name or job_ref or f"训练任务#{job_db_id}",
            display_name=job_name or job_ref or f"训练任务#{job_db_id}",
            node_type="pipeline",
            system="training_engine",
            description="模型训练任务",
            metadata={
                "training_job_id": job_ref,
                "training_job_db_id": job_db_id,
                "status": _enum_value(status),
                "dataset_id": dataset_id,
            },
            created_at=created_at,
        )
        created_nodes += int(created)
        updated_nodes += int(not created)

        # LineageNode/LineageEdge are linked only by scalar FK ids (no ORM
        # relationship), so flush nodes explicitly before inserting edges.
        db.flush()
        _, created = _upsert_discovered_edge(
            db,
            edge_id=f"ln-edge-train-{job_db_id}",
            source_id=dataset_node_id,
            target_id=job_node_id,
            edge_type="train",
            transformation=f"训练任务 {job_ref or job_db_id} 读取数据集 #{dataset_id}",
        )
        created_edges += int(created)
        updated_edges += int(not created)

        if model_id:
            model_node_id = f"ln-model-{model_id}"
            _, created = _upsert_discovered_node(
                db,
                node_id=model_node_id,
                name=model_name or f"模型#{model_id}",
                display_name=model_name or f"模型#{model_id}",
                node_type="model",
                system="model_registry",
                description=f"训练任务 {job_ref or job_db_id} 生成的模型",
                metadata={"model_id": model_id},
            )
            created_nodes += int(created)
            updated_nodes += int(not created)
            db.flush()
            _, created = _upsert_discovered_edge(
                db,
                edge_id=f"ln-edge-model-{job_db_id}",
                source_id=job_node_id,
                target_id=model_node_id,
                edge_type="produces",
                transformation=f"训练任务 {job_ref or job_db_id} 注册模型 #{model_id}",
            )
            created_edges += int(created)
            updated_edges += int(not created)

    db.commit()
    return {
        "jobs": len(rows),
        "created_nodes": created_nodes,
        "updated_nodes": updated_nodes,
        "created_edges": created_edges,
        "updated_edges": updated_edges,
    }


# ==================== 节点 API ====================


@router.post("/sync/training", summary="同步训练任务血缘")
def sync_training_lineage(
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """将历史训练任务持久化为 数据集→训练任务→模型 血缘链。"""
    try:
        from services.lineage_sync_service import sync_training_lineage_batch
        result = sync_training_lineage_batch(db, limit, organization_id)
        return {"message": f"已同步 {result['jobs']} 条训练任务", **result}
    except Exception as exc:
        db.rollback()
        logger.error("同步训练血缘失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"同步训练血缘失败: {exc}")


@router.post("/sync/all", summary="同步平台血缘")
def sync_all_lineage(
    limit: int = Query(1000, ge=1, le=5000),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """幂等回填派生数据集及训练/模型链，不会重复生成节点和关系。"""
    try:
        from services.lineage_sync_service import (
            sync_dataset_lineage_batch, sync_training_lineage_batch,
        )
        datasets = sync_dataset_lineage_batch(db, limit, organization_id)
        training = sync_training_lineage_batch(db, min(limit, 1000), organization_id)
        return {
            "message": f"已同步 {datasets['synced']} 条数据处理关系和 {training['synced']} 条训练链",
            "datasets": datasets, "training": training,
        }
    except Exception as exc:
        db.rollback()
        logger.error("同步平台血缘失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"同步平台血缘失败: {exc}")


@router.get("/nodes", summary="获取节点列表")
def get_nodes(
    skip: int = Query(0, ge=0, description="跳过记录数"),
    limit: int = Query(100, ge=1, le=1000, description="返回记录数"),
    node_type: Optional[str] = Query(None, description="节点类型过滤"),
    system: Optional[str] = Query(None, description="系统过滤"),
    database: Optional[str] = Query(None, description="数据库过滤"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    is_active: Optional[bool] = Query(None, description="是否活跃"),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """获取血缘节点列表，支持过滤和搜索"""
    try:
        query = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id)

        if node_type:
            query = query.filter(LineageNode.node_type == node_type)
        if system:
            query = query.filter(LineageNode.system == system)
        if database:
            query = query.filter(LineageNode.database == database)
        if is_active is not None:
            query = query.filter(LineageNode.is_active == (1 if is_active else 0))
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    LineageNode.name.ilike(search_term),
                    LineageNode.display_name.ilike(search_term),
                    LineageNode.description.ilike(search_term),
                )
            )

        total = query.count()
        nodes = (
            query.order_by(LineageNode.updated_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "items": [node.to_dict() for node in nodes],
        }
    except Exception as e:
        logger.error(f"获取节点列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取节点列表失败: {str(e)}")


@router.get("/nodes/{node_id}", summary="获取节点详情")
def get_node(node_id: str, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """获取单个节点的详细信息"""
    try:
        node = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail="节点不存在")

        # 获取关联的边数量
        incoming_count = (
            db.query(LineageEdge)
            .filter(LineageEdge.target_id == node_id, LineageEdge.is_active == 1)
            .count()
        )
        outgoing_count = (
            db.query(LineageEdge)
            .filter(LineageEdge.source_id == node_id, LineageEdge.is_active == 1)
            .count()
        )

        result = node.to_dict()
        result["incoming_edges_count"] = incoming_count
        result["outgoing_edges_count"] = outgoing_count

        # 关联的真实实体名（数据集/模型/训练任务），供前端详情展示
        if node.resource_type and node.resource_id:
            _table = {"dataset": "datasets", "model": "models", "training_job": "training_jobs"}.get(node.resource_type)
            if _table:
                _r = db.execute(text(f"SELECT name FROM {_table} WHERE id = :id"), {"id": node.resource_id}).fetchone()
                result["resource_name"] = _r[0] if _r else None

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取节点详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取节点详情失败: {str(e)}")


@router.post("/nodes", summary="创建节点")
def create_node(node: NodeCreate, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """创建新的血缘节点"""
    try:
        # 检查名称是否重复
        existing = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.name == node.name).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"节点名称 '{node.name}' 已存在")

        # 生成完整路径
        full_path = node.full_path
        if not full_path:
            parts = [
                p
                for p in [node.system, node.database, node.schema_name, node.name]
                if p
            ]
            full_path = ".".join(parts) if parts else node.name

        db_node = LineageNode(
            id=str(uuid.uuid4()),
            name=node.name,
            display_name=node.display_name or node.name,
            node_type=node.node_type,
            system=node.system,
            database=node.database,
            schema_name=node.schema_name,
            full_path=full_path,
            description=node.description,
            extra_metadata=node.metadata or {},
            tags=node.tags or [],
            owner=node.owner,
            quality_score=node.quality_score or 0,
            created_by=node.created_by,
            organization_id=organization_id,
        )

        db.add(db_node)

        # 记录变更日志
        log = LineageChangeLog(
            node_id=db_node.id,
            change_type="create",
            change_detail={"action": "create_node"},
            new_value=db_node.to_dict(),
            created_by=node.created_by,
            organization_id=organization_id,
        )
        db.add(log)

        db.commit()
        db.refresh(db_node)

        return {"message": "节点创建成功", "data": db_node.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"创建节点失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建节点失败: {str(e)}")


@router.put("/nodes/{node_id}", summary="更新节点")
def update_node(node_id: str, node: NodeUpdate, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """更新血缘节点信息"""
    try:
        db_node = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == node_id).first()
        if not db_node:
            raise HTTPException(status_code=404, detail="节点不存在")

        old_value = db_node.to_dict()

        # 更新字段
        update_data = node.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if key == "is_active":
                setattr(db_node, key, 1 if value else 0)
            elif key == "metadata":
                db_node.extra_metadata = value or {}
            else:
                setattr(db_node, key, value)

        db_node.updated_at = datetime.now()

        # 记录变更日志
        log = LineageChangeLog(
            node_id=node_id,
            change_type="update",
            change_detail={"action": "update_node", "fields": list(update_data.keys())},
            old_value=old_value,
            new_value=db_node.to_dict(),
            created_by=node.metadata.get("updated_by") if node.metadata else None,
            organization_id=organization_id,
        )
        db.add(log)

        db.commit()
        db.refresh(db_node)

        return {"message": "节点更新成功", "data": db_node.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"更新节点失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新节点失败: {str(e)}")


@router.delete("/nodes/{node_id}", summary="删除节点")
def delete_node(node_id: str, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """删除血缘节点（级联删除相关边）"""
    try:
        db_node = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == node_id).first()
        if not db_node:
            raise HTTPException(status_code=404, detail="节点不存在")

        old_value = db_node.to_dict()

        # 删除相关边
        db.query(LineageEdge).filter(
            or_(LineageEdge.source_id == node_id, LineageEdge.target_id == node_id)
        ).delete()

        # 删除节点
        db.delete(db_node)

        # 记录变更日志
        log = LineageChangeLog(
            node_id=node_id,
            change_type="delete",
            change_detail={"action": "delete_node"},
            old_value=old_value,
            created_by="system",
            organization_id=organization_id,
        )
        db.add(log)

        db.commit()

        return {"message": "节点删除成功", "id": node_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"删除节点失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除节点失败: {str(e)}")


# ==================== 边 API ====================


@router.get("/edges", summary="获取边列表")
def get_edges(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    source_id: Optional[str] = Query(None, description="源节点ID"),
    target_id: Optional[str] = Query(None, description="目标节点ID"),
    edge_type: Optional[str] = Query(None, description="关系类型"),
    is_active: Optional[bool] = Query(None, description="是否活跃"),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """获取血缘关系边列表"""
    try:
        query = _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)

        if source_id:
            query = query.filter(LineageEdge.source_id == source_id)
        if target_id:
            query = query.filter(LineageEdge.target_id == target_id)
        if edge_type:
            query = query.filter(LineageEdge.edge_type == edge_type)
        if is_active is not None:
            query = query.filter(LineageEdge.is_active == (1 if is_active else 0))

        total = query.count()
        edges = (
            query.order_by(LineageEdge.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "items": [edge.to_dict() for edge in edges],
        }
    except Exception as e:
        logger.error(f"获取边列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取边列表失败: {str(e)}")


@router.get("/edges/{edge_id}", summary="获取边详情")
def get_edge(edge_id: str, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """获取单条边的详细信息"""
    try:
        edge = _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id).filter(LineageEdge.id == edge_id).first()
        if not edge:
            raise HTTPException(status_code=404, detail="边不存在")

        result = edge.to_dict()

        # 获取源节点和目标节点信息
        source = db.query(LineageNode).filter(LineageNode.id == edge.source_id).first()
        target = db.query(LineageNode).filter(LineageNode.id == edge.target_id).first()

        result["source_node"] = source.to_dict() if source else None
        result["target_node"] = target.to_dict() if target else None

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取边详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取边详情失败: {str(e)}")


@router.post("/edges", summary="创建边")
def create_edge(edge: EdgeCreate, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """创建新的血缘关系"""
    try:
        # 检查节点是否存在
        source = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == edge.source_id).first()
        if not source:
            raise HTTPException(status_code=400, detail="源节点不存在")

        target = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == edge.target_id).first()
        if not target:
            raise HTTPException(status_code=400, detail="目标节点不存在")

        # 检查是否已存在相同关系
        existing = (
            db.query(LineageEdge)
            .filter(
                LineageEdge.source_id == edge.source_id,
                LineageEdge.target_id == edge.target_id,
                LineageEdge.is_active == 1,
            )
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="该血缘关系已存在")

        db_edge = LineageEdge(
            id=str(uuid.uuid4()),
            source_id=edge.source_id,
            target_id=edge.target_id,
            edge_type=edge.edge_type,
            transformation=edge.transformation,
            transformation_type=edge.transformation_type,
            column_mapping=edge.column_mapping or [],
            impact_weight=edge.impact_weight or 1,
            description=edge.description,
            extra_metadata=edge.metadata or {},
            created_by=edge.created_by,
            organization_id=organization_id,
        )

        db.add(db_edge)

        # 记录变更日志
        log = LineageChangeLog(
            edge_id=db_edge.id,
            change_type="create",
            change_detail={"action": "create_edge"},
            new_value=db_edge.to_dict(),
            created_by=edge.created_by,
            organization_id=organization_id,
        )
        db.add(log)

        db.commit()
        db.refresh(db_edge)

        return {"message": "血缘关系创建成功", "data": db_edge.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"创建边失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建边失败: {str(e)}")


@router.put("/edges/{edge_id}", summary="更新边")
def update_edge(edge_id: str, edge: EdgeUpdate, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """更新血缘关系信息"""
    try:
        db_edge = _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id).filter(LineageEdge.id == edge_id).first()
        if not db_edge:
            raise HTTPException(status_code=404, detail="边不存在")

        old_value = db_edge.to_dict()

        update_data = edge.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if key == "is_active":
                setattr(db_edge, key, 1 if value else 0)
            elif key == "metadata":
                db_edge.extra_metadata = value or {}
            else:
                setattr(db_edge, key, value)

        db_edge.updated_at = datetime.now()

        # 记录变更日志
        log = LineageChangeLog(
            edge_id=edge_id,
            change_type="update",
            change_detail={"action": "update_edge", "fields": list(update_data.keys())},
            old_value=old_value,
            new_value=db_edge.to_dict(),
            created_by=edge.metadata.get("updated_by") if edge.metadata else None,
            organization_id=organization_id,
        )
        db.add(log)

        db.commit()
        db.refresh(db_edge)

        return {"message": "边更新成功", "data": db_edge.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"更新边失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新边失败: {str(e)}")


@router.delete("/edges/{edge_id}", summary="删除边")
def delete_edge(edge_id: str, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """删除血缘关系"""
    try:
        db_edge = _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id).filter(LineageEdge.id == edge_id).first()
        if not db_edge:
            raise HTTPException(status_code=404, detail="边不存在")

        old_value = db_edge.to_dict()
        db.delete(db_edge)

        # 记录变更日志
        log = LineageChangeLog(
            edge_id=edge_id,
            change_type="delete",
            change_detail={"action": "delete_edge"},
            old_value=old_value,
            created_by="system",
            organization_id=organization_id,
        )
        db.add(log)

        db.commit()

        return {"message": "边删除成功", "id": edge_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"删除边失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除边失败: {str(e)}")


# ==================== 图谱 API ====================


@router.get("/graph", summary="获取完整血缘图谱")
def get_graph(
    node_types: Optional[str] = Query(None, description="节点类型过滤(逗号分隔)"),
    system: Optional[str] = Query(None, description="系统过滤"),
    max_depth: int = Query(5, ge=1, le=10, description="最大深度"),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """获取完整的血缘图谱数据（含 DataHub 自动发现）"""
    try:
        # ── 从数据库查询已有节点和边 ──
        node_query = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.is_active == 1)
        if node_types:
            types = [t.strip() for t in node_types.split(",")]
            node_query = node_query.filter(LineageNode.node_type.in_(types))
        if system:
            node_query = node_query.filter(LineageNode.system == system)

        nodes = node_query.all()
        node_ids = [n.id for n in nodes]
        node_list = [node.to_dict() for node in nodes]

        edges = (
            _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
            .filter(LineageEdge.is_active == 1,
                    LineageEdge.source_id.in_(node_ids),
                    LineageEdge.target_id.in_(node_ids))
            .all()
        )
        edge_list = [edge.to_dict() for edge in edges]

        # 统计
        node_types_stat, systems_stat = {}, {}
        for n in node_list:
            nt = n.get("node_type", "unknown")
            node_types_stat[nt] = node_types_stat.get(nt, 0) + 1
            s = n.get("system", "unknown")
            if s: systems_stat[s] = systems_stat.get(s, 0) + 1

        return {
            "nodes": node_list,
            "edges": edge_list,
            "statistics": {
                "node_count": len(node_list), "edge_count": len(edge_list),
                "node_types": node_types_stat, "systems": systems_stat,
            },
        }
    except Exception as e:
        logger.error(f"获取图谱失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取图谱失败: {str(e)}")


@router.get("/graph/sub/{node_id}", summary="获取子图")
def get_subgraph(
    node_id: str,
    depth: int = Query(2, ge=1, le=5, description="遍历深度"),
    direction: str = Query(
        "both", pattern="^(upstream|downstream|both)$", description="遍历方向"
    ),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """获取以某个节点为中心的子图"""
    try:
        # 检查节点是否存在
        center_node = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == node_id).first()
        if not center_node:
            raise HTTPException(status_code=404, detail="节点不存在")

        # 收集节点和边
        node_set = {node_id}
        edge_list = []

        # BFS遍历
        current_level = {node_id}

        for _ in range(depth):
            next_level = set()

            if direction in ["upstream", "both"]:
                # 上游遍历
                edges = (
                    _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
                    .filter(
                        LineageEdge.target_id.in_(current_level),
                        LineageEdge.is_active == 1,
                    )
                    .all()
                )

                for edge in edges:
                    edge_list.append(edge)
                    node_set.add(edge.source_id)
                    next_level.add(edge.source_id)

            if direction in ["downstream", "both"]:
                # 下游遍历
                edges = (
                    _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
                    .filter(
                        LineageEdge.source_id.in_(current_level),
                        LineageEdge.is_active == 1,
                    )
                    .all()
                )

                for edge in edges:
                    edge_list.append(edge)
                    node_set.add(edge.target_id)
                    next_level.add(edge.target_id)

            current_level = next_level
            if not current_level:
                break

        # 获取所有节点
        nodes = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id.in_(node_set)).all()

        # 去重边
        edge_ids = set()
        unique_edges = []
        for edge in edge_list:
            if edge.id not in edge_ids:
                edge_ids.add(edge.id)
                unique_edges.append(edge)

        return {
            "center_node": node_id,
            "depth": depth,
            "direction": direction,
            "nodes": [node.to_dict() for node in nodes],
            "edges": [edge.to_dict() for edge in unique_edges],
            "statistics": {"node_count": len(nodes), "edge_count": len(unique_edges)},
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取子图失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取子图失败: {str(e)}")


# ==================== 影响分析 API ====================


def _impact_score(level: int, weight: Optional[int] = 1) -> int:
    """Calculate a transparent 0-100 impact score from graph distance/weight."""
    distance_score = max(20, 100 - max(0, level - 1) * 20)
    weight_factor = min(1.0, max(0.2, float(weight or 1)))
    return max(1, min(100, round(distance_score * weight_factor)))


@router.get("/impact/{node_id}", summary="影响分析")
def get_impact_analysis(
    node_id: str,
    depth: int = Query(3, ge=1, le=10, description="分析深度"),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """分析某个节点变更的影响范围"""
    try:
        # 检查节点是否存在
        node = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail="节点不存在")

        # 下游影响分析
        affected_nodes = []
        affected_edges = []
        visited = {node_id}
        current_level = [(node_id, 0)]

        while current_level:
            next_level = []
            for current_id, current_depth in current_level:
                if current_depth >= depth:
                    continue

                # 找到下游边
                edges = (
                    _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
                    .filter(
                        LineageEdge.source_id == current_id, LineageEdge.is_active == 1
                    )
                    .all()
                )

                for edge in edges:
                    affected_edges.append(
                        {**edge.to_dict(), "level": current_depth + 1}
                    )

                    if edge.target_id not in visited:
                        target_node = (
                            _scope(db.query(LineageNode), LineageNode.organization_id, organization_id)
                            .filter(LineageNode.id == edge.target_id)
                            .first()
                        )
                        if target_node:
                            affected_nodes.append(
                                {
                                    **target_node.to_dict(),
                                    "impact_level": current_depth + 1,
                                    "impact_type": "downstream",
                                    "impact_score": _impact_score(
                                        current_depth + 1, edge.impact_weight
                                    ),
                                }
                            )
                            visited.add(edge.target_id)
                            next_level.append((edge.target_id, current_depth + 1))

            current_level = next_level

        node_types: Dict[str, int] = {}
        for affected in affected_nodes:
            node_type = affected.get("node_type", "unknown")
            node_types[node_type] = node_types.get(node_type, 0) + 1

        return {
            "source_node": node.to_dict(),
            "impact_summary": {
                "total_affected_nodes": len(affected_nodes),
                "max_depth": depth,
                "node_types": node_types,
            },
            "affected_nodes": affected_nodes,
            "affected_edges": affected_edges,
            "recommendations": _generate_impact_recommendations(
                affected_nodes, "downstream"
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"影响分析失败: {e}")
        raise HTTPException(status_code=500, detail=f"影响分析失败: {str(e)}")


@router.get("/upstream/{node_id}", summary="上游追溯")
def get_upstream(
    node_id: str,
    depth: int = Query(3, ge=1, le=10, description="追溯深度"),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """追溯数据来源"""
    try:
        # 检查节点是否存在
        node = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail="节点不存在")

        # 上游追溯
        source_nodes = []
        source_edges = []
        visited = {node_id}
        current_level = [(node_id, 0)]

        while current_level:
            next_level = []
            for current_id, current_depth in current_level:
                if current_depth >= depth:
                    continue

                # 找到上游边
                edges = (
                    _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
                    .filter(
                        LineageEdge.target_id == current_id, LineageEdge.is_active == 1
                    )
                    .all()
                )

                for edge in edges:
                    source_edges.append({**edge.to_dict(), "level": current_depth + 1})

                    if edge.source_id not in visited:
                        source_node = (
                            _scope(db.query(LineageNode), LineageNode.organization_id, organization_id)
                            .filter(LineageNode.id == edge.source_id)
                            .first()
                        )
                        if source_node:
                            source_nodes.append(
                                {
                                    **source_node.to_dict(),
                                    "distance": current_depth + 1,
                                    "lineage_type": "upstream",
                                    "impact_score": _impact_score(
                                        current_depth + 1, edge.impact_weight
                                    ),
                                }
                            )
                            visited.add(edge.source_id)
                            next_level.append((edge.source_id, current_depth + 1))

            current_level = next_level

        return {
            "target_node": node.to_dict(),
            "upstream_summary": {
                "total_source_nodes": len(source_nodes),
                "max_depth": depth,
                "node_types": {},
            },
            "source_nodes": source_nodes,
            "source_edges": source_edges,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上游追溯失败: {e}")
        raise HTTPException(status_code=500, detail=f"上游追溯失败: {str(e)}")


@router.get("/downstream/{node_id}", summary="下游影响")
def get_downstream(
    node_id: str,
    depth: int = Query(3, ge=1, le=10, description="追溯深度"),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """查看数据去向"""
    try:
        # 检查节点是否存在
        node = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail="节点不存在")

        # 下游追溯
        downstream_nodes = []
        downstream_edges = []
        visited = {node_id}
        current_level = [(node_id, 0)]

        while current_level:
            next_level = []
            for current_id, current_depth in current_level:
                if current_depth >= depth:
                    continue

                # 找到下游边
                edges = (
                    _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
                    .filter(
                        LineageEdge.source_id == current_id, LineageEdge.is_active == 1
                    )
                    .all()
                )

                for edge in edges:
                    downstream_edges.append(
                        {**edge.to_dict(), "level": current_depth + 1}
                    )

                    if edge.target_id not in visited:
                        target_node = (
                            _scope(db.query(LineageNode), LineageNode.organization_id, organization_id)
                            .filter(LineageNode.id == edge.target_id)
                            .first()
                        )
                        if target_node:
                            downstream_nodes.append(
                                {
                                    **target_node.to_dict(),
                                    "distance": current_depth + 1,
                                    "lineage_type": "downstream",
                                    "impact_score": _impact_score(
                                        current_depth + 1, edge.impact_weight
                                    ),
                                }
                            )
                            visited.add(edge.target_id)
                            next_level.append((edge.target_id, current_depth + 1))

            current_level = next_level

        return {
            "source_node": node.to_dict(),
            "downstream_summary": {
                "total_downstream_nodes": len(downstream_nodes),
                "max_depth": depth,
                "node_types": {},
            },
            "downstream_nodes": downstream_nodes,
            "downstream_edges": downstream_edges,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下游追溯失败: {e}")
        raise HTTPException(status_code=500, detail=f"下游追溯失败: {str(e)}")


# ==================== 路径分析 API ====================


@router.get("/path", summary="路径分析")
def get_path(
    source_id: str = Query(..., description="起始节点ID"),
    target_id: str = Query(..., description="目标节点ID"),
    max_depth: int = Query(5, ge=1, le=10, description="最大搜索深度"),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """查找两个节点之间的血缘路径"""
    try:
        # 检查节点是否存在
        source = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == source_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="起始节点不存在")

        target = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="目标节点不存在")

        # BFS搜索路径
        paths = []
        queue = [(source_id, [source_id])]
        visited_at_depth = {source_id: 0}

        while queue:
            current_id, path = queue.pop(0)
            current_depth = len(path) - 1

            if current_depth >= max_depth:
                continue

            if current_id == target_id:
                paths.append(path)
                continue

            # 获取下游节点
            edges = (
                _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
                .filter(LineageEdge.source_id == current_id, LineageEdge.is_active == 1)
                .all()
            )

            for edge in edges:
                next_id = edge.target_id
                # 允许多条路径
                if next_id not in path:  # 避免环
                    new_path = path + [next_id]
                    queue.append((next_id, new_path))

        # 构建路径详情
        path_details = []
        for path in paths[:10]:  # 最多返回10条路径
            path_nodes = []
            path_edges = []

            for i, node_id in enumerate(path):
                node = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == node_id).first()
                if node:
                    path_nodes.append(node.to_dict())

                if i < len(path) - 1:
                    edge = (
                        _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
                        .filter(
                            LineageEdge.source_id == node_id,
                            LineageEdge.target_id == path[i + 1],
                            LineageEdge.is_active == 1,
                        )
                        .first()
                    )
                    if edge:
                        path_edges.append(edge.to_dict())

            path_details.append(
                {"nodes": path_nodes, "edges": path_edges, "length": len(path) - 1}
            )

        return {
            "source_node": source.to_dict(),
            "target_node": target.to_dict(),
            "total_paths": len(paths),
            "paths": path_details,
            "max_depth": max_depth,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"路径分析失败: {e}")
        raise HTTPException(status_code=500, detail=f"路径分析失败: {str(e)}")


# ==================== SQL血缘提取 API ====================


@router.post("/extract/sql", summary="SQL血缘提取")
def extract_sql_lineage(request: SQLLineageRequest, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """从SQL语句中自动提取血缘关系 — 使用 sqlglot 解析器"""
    try:
        sql = request.sql.strip()
        if not sql:
            raise HTTPException(status_code=400, detail="SQL语句不能为空")

        lineage = sqlglot_extract(sql)
        col_lineage = sqlglot_columns(sql)
        graph = lineage.get("graph") or {}
        graph_nodes = graph.get("nodes") or []
        graph_edges = graph.get("edges") or []
        persisted_nodes: List[Dict[str, Any]] = []
        persisted_edges: List[Dict[str, Any]] = []
        created_node_count = 0
        created_edge_count = 0

        if request.persist:
            node_names: List[str] = []
            for item in graph_nodes:
                name = str(item.get("label") or item.get("id") or "").strip()
                if name and name not in node_names:
                    node_names.append(name)
            for item in lineage.get("tables") or []:
                name = str(item.get("name") or "").strip()
                if name and name not in node_names:
                    node_names.append(name)

            node_map: Dict[str, LineageNode] = {}
            for table_name in node_names:
                query = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.name == table_name)
                query = query.filter(LineageNode.database == request.database)
                query = query.filter(LineageNode.schema_name == request.schema_name)
                node = query.first()
                if node is None:
                    path_parts = [request.database, request.schema_name, table_name]
                    node = LineageNode(
                        id=str(uuid.uuid4()),
                        name=table_name,
                        display_name=table_name,
                        node_type="table",
                        system="sql_import",
                        database=request.database,
                        schema_name=request.schema_name,
                        full_path=".".join(part for part in path_parts if part),
                        description="由SQL血缘解析自动登记",
                        extra_metadata={"provider": lineage.get("provider")},
                        tags=["SQL导入"],
                        created_by="sql_extractor",
                        organization_id=organization_id,
                    )
                    db.add(node)
                    db.add(LineageChangeLog(
                        node_id=node.id,
                        change_type="create",
                        change_detail={"action": "extract_sql"},
                        new_value=node.to_dict(),
                        created_by="sql_extractor",
                        organization_id=organization_id,
                    ))
                    created_node_count += 1
                else:
                    node.is_active = 1
                    node.updated_at = datetime.now()
                node_map[table_name] = node
                persisted_nodes.append(node.to_dict())

            db.flush()
            for relation in graph_edges:
                source_name = str(relation.get("source") or "").strip()
                target_name = str(relation.get("target") or "").strip()
                if not source_name or not target_name or source_name == target_name:
                    continue
                source = node_map.get(source_name)
                target = node_map.get(target_name)
                if not source or not target:
                    continue
                edge = (
                    db.query(LineageEdge)
                    .filter(
                        LineageEdge.source_id == source.id,
                        LineageEdge.target_id == target.id,
                    )
                    .first()
                )
                edge_type = {
                    "etl": "transform",
                    "join": "join",
                }.get(str(relation.get("type") or "").lower(), "derived")
                if edge is None:
                    edge = LineageEdge(
                        id=str(uuid.uuid4()),
                        source_id=source.id,
                        target_id=target.id,
                        created_by="sql_extractor",
                        organization_id=organization_id,
                    )
                    db.add(edge)
                    created_edge_count += 1
                edge.edge_type = edge_type
                edge.transformation = sql
                edge.transformation_type = "sql"
                edge.column_mapping = col_lineage or []
                edge.extra_metadata = {"provider": lineage.get("provider")}
                edge.is_active = 1
                edge.updated_at = datetime.now()
                persisted_edges.append(edge.to_dict())
            db.commit()

        return {
            **lineage,
            "column_lineage": col_lineage,
            "persisted": request.persist,
            "persisted_nodes": persisted_nodes,
            "persisted_edges": persisted_edges,
            "created_node_count": created_node_count,
            "created_edge_count": created_edge_count,
            "message": (
                f"解析到 {len(graph_nodes)} 个节点、{len(graph_edges)} 条关系；"
                f"新增 {created_node_count} 个节点、{created_edge_count} 条关系"
                if request.persist else
                f"解析到 {len(graph_nodes)} 个节点、{len(graph_edges)} 条关系"
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"SQL血缘提取失败: {e}")
        raise HTTPException(status_code=500, detail=f"SQL血缘提取失败: {str(e)}")


# ==================== 统计 API ====================


@router.get("/stats", summary="血缘统计(别名)")
def get_stats(db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """前端兼容别名 -> /statistics"""
    return get_statistics(db, organization_id)

@router.get("/statistics", summary="血缘统计")
def get_statistics(db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """获取血缘数据统计信息"""
    try:
        # 节点统计
        node_query = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id)
        edge_query = _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
        total_nodes = node_query.count()
        active_nodes = node_query.filter(LineageNode.is_active == 1).count()

        nodes_by_type = node_query.with_entities(LineageNode.node_type, func.count()).group_by(LineageNode.node_type).all()
        nodes_by_system = node_query.filter(LineageNode.system.isnot(None)).with_entities(LineageNode.system, func.count()).group_by(LineageNode.system).all()

        # 边统计
        total_edges = edge_query.count()
        active_edges = edge_query.filter(LineageEdge.is_active == 1).count()

        edges_by_type = edge_query.with_entities(LineageEdge.edge_type, func.count()).group_by(LineageEdge.edge_type).all()

        # 最近变更
        recent_changes = (
            _scope(db.query(LineageChangeLog), LineageChangeLog.organization_id, organization_id)
            .order_by(LineageChangeLog.created_at.desc())
            .limit(10)
            .all()
        )

        return {
            "nodes": {
                "total": total_nodes,
                "active": active_nodes,
                "by_type": {row[0]: row[1] for row in nodes_by_type},
                "by_system": {row[0]: row[1] for row in nodes_by_system},
            },
            "edges": {
                "total": total_edges,
                "active": active_edges,
                "by_type": {row[0]: row[1] for row in edges_by_type},
            },
            "recent_changes": [log.to_dict() for log in recent_changes],
            "last_updated": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"获取统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计失败: {str(e)}")


# ==================== 批量操作 API ====================


@router.post("/nodes/batch", summary="批量创建节点")
def batch_create_nodes(nodes: List[NodeCreate], db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """批量创建血缘节点"""
    try:
        created_nodes = []
        errors = []

        for i, node in enumerate(nodes):
            try:
                # 检查名称是否重复
                existing = (
                    _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.name == node.name).first()
                )
                if existing:
                    errors.append({"index": i, "name": node.name, "error": "名称已存在"})
                    continue

                full_path = node.full_path
                if not full_path:
                    parts = [
                        p
                        for p in [
                            node.system,
                            node.database,
                            node.schema_name,
                            node.name,
                        ]
                        if p
                    ]
                    full_path = ".".join(parts) if parts else node.name

                db_node = LineageNode(
                    id=str(uuid.uuid4()),
                    name=node.name,
                    display_name=node.display_name or node.name,
                    node_type=node.node_type,
                    system=node.system,
                    database=node.database,
                    schema_name=node.schema_name,
                    full_path=full_path,
                    description=node.description,
                    extra_metadata=node.metadata or {},
                    tags=node.tags or [],
                    owner=node.owner,
                    quality_score=node.quality_score or 0,
                    created_by=node.created_by,
                    organization_id=organization_id,
                )

                db.add(db_node)
                created_nodes.append(db_node)

            except Exception as e:
                errors.append({"index": i, "name": node.name, "error": str(e)})

        db.commit()

        return {
            "message": f"成功创建 {len(created_nodes)} 个节点",
            "created_count": len(created_nodes),
            "error_count": len(errors),
            "created": [n.to_dict() for n in created_nodes],
            "errors": errors,
        }
    except Exception as e:
        db.rollback()
        logger.error(f"批量创建节点失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量创建节点失败: {str(e)}")


@router.post("/edges/batch", summary="批量创建边")
def batch_create_edges(edges: List[EdgeCreate], db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """批量创建血缘关系"""
    try:
        created_edges = []
        errors = []

        for i, edge in enumerate(edges):
            try:
                # 检查节点是否存在
                source = (
                    _scope(db.query(LineageNode), LineageNode.organization_id, organization_id)
                    .filter(LineageNode.id == edge.source_id)
                    .first()
                )
                if not source:
                    errors.append({"index": i, "error": f"源节点 {edge.source_id} 不存在"})
                    continue

                target = (
                    _scope(db.query(LineageNode), LineageNode.organization_id, organization_id)
                    .filter(LineageNode.id == edge.target_id)
                    .first()
                )
                if not target:
                    errors.append({"index": i, "error": f"目标节点 {edge.target_id} 不存在"})
                    continue

                # 检查是否已存在
                existing = (
                    db.query(LineageEdge)
                    .filter(
                        LineageEdge.source_id == edge.source_id,
                        LineageEdge.target_id == edge.target_id,
                        LineageEdge.is_active == 1,
                    )
                    .first()
                )
                if existing:
                    errors.append({"index": i, "error": "该关系已存在"})
                    continue

                db_edge = LineageEdge(
                    id=str(uuid.uuid4()),
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    edge_type=edge.edge_type,
                    transformation=edge.transformation,
                    transformation_type=edge.transformation_type,
                    column_mapping=edge.column_mapping or [],
                    impact_weight=edge.impact_weight or 1,
                    description=edge.description,
                    extra_metadata=edge.metadata or {},
                    created_by=edge.created_by,
                    organization_id=organization_id,
                )

                db.add(db_edge)
                created_edges.append(db_edge)

            except Exception as e:
                errors.append({"index": i, "error": str(e)})

        db.commit()

        return {
            "message": f"成功创建 {len(created_edges)} 条关系",
            "created_count": len(created_edges),
            "error_count": len(errors),
            "created": [e.to_dict() for e in created_edges],
            "errors": errors,
        }
    except Exception as e:
        db.rollback()
        logger.error(f"批量创建边失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量创建边失败: {str(e)}")


# ==================== 变更日志 API ====================


@router.get("/changelog", summary="变更日志")
def get_changelog(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    node_id: Optional[str] = Query(None),
    edge_id: Optional[str] = Query(None),
    change_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    organization_id: int | None = Depends(lineage_organization),
):
    """获取血缘变更日志"""
    try:
        query = _scope(db.query(LineageChangeLog), LineageChangeLog.organization_id, organization_id)

        if node_id:
            query = query.filter(LineageChangeLog.node_id == node_id)
        if edge_id:
            query = query.filter(LineageChangeLog.edge_id == edge_id)
        if change_type:
            query = query.filter(LineageChangeLog.change_type == change_type)

        total = query.count()
        logs = (
            query.order_by(LineageChangeLog.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "items": [log.to_dict() for log in logs],
        }
    except Exception as e:
        logger.error(f"获取变更日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取变更日志失败: {str(e)}")


# ==================== 辅助函数 ====================


def _generate_impact_recommendations(affected_nodes: list, direction: str) -> list:
    """生成影响分析建议"""
    recommendations = []

    if not affected_nodes:
        recommendations.append("无下游影响，变更风险较低")
        return recommendations

    # 按节点类型统计
    type_counts = {}
    for node in affected_nodes:
        node_type = node.get("node_type", "unknown")
        type_counts[node_type] = type_counts.get(node_type, 0) + 1

    if "report" in type_counts:
        recommendations.append(f"影响 {type_counts['report']} 个报表，请通知相关用户")

    if "model" in type_counts:
        recommendations.append(f"影响 {type_counts['model']} 个模型，请考虑重新训练")

    if "pipeline" in type_counts:
        recommendations.append(f"影响 {type_counts['pipeline']} 个数据管道，请检查调度任务")

    if len(affected_nodes) > 10:
        recommendations.append("影响范围较大，建议分批次变更")

    return recommendations


def _extract_tables_from_sql(sql: str) -> list:
    """从SQL中提取表名"""
    tables = []

    # 常见表名匹配模式
    patterns = [
        r"FROM\s+([a-zA-Z_][a-zA-Z0-9_\.]*)",
        r"JOIN\s+([a-zA-Z_][a-zA-Z0-9_\.]*)",
        r"INTO\s+([a-zA-Z_][a-zA-Z0-9_\.]*)",
        r"UPDATE\s+([a-zA-Z_][a-zA-Z0-9_\.]*)",
        r"TABLE\s+([a-zA-Z_][a-zA-Z0-9_\.]*)",
    ]

    sql_upper = sql.upper()

    for pattern in patterns:
        matches = re.findall(pattern, sql_upper, re.IGNORECASE)
        for match in matches:
            # 清理表名
            table_name = match.strip().lower()
            # 移除可能的别名
            if " " in table_name:
                table_name = table_name.split()[0]
            if (
                table_name
                and table_name not in tables
                and table_name not in ["select", "where", "and", "or", "on"]
            ):
                tables.append(table_name)

    return tables


def _detect_sql_type(sql: str) -> str:
    """检测SQL类型"""
    sql_upper = sql.strip().upper()

    if sql_upper.startswith("INSERT"):
        return "INSERT"
    elif sql_upper.startswith("UPDATE"):
        return "UPDATE"
    elif sql_upper.startswith("DELETE"):
        return "DELETE"
    elif sql_upper.startswith("CREATE"):
        if "AS SELECT" in sql_upper or "AS(" in sql_upper:
            return "CREATE_TABLE_AS"
        return "CREATE"
    elif sql_upper.startswith("SELECT"):
        return "SELECT"
    elif sql_upper.startswith("WITH"):
        return "CTE"
    else:
        return "UNKNOWN"


# ==================== 兼容旧API ====================


@router.get("/tables", summary="获取表列表(兼容)")
def get_tables_legacy(db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """获取表节点列表（兼容旧API）"""
    try:
        nodes = (
            _scope(db.query(LineageNode), LineageNode.organization_id, organization_id)
            .filter(LineageNode.node_type == "table", LineageNode.is_active == 1)
            .all()
        )

        return {"items": [node.to_dict() for node in nodes], "total": len(nodes)}
    except Exception as e:
        # 如果表不存在，返回空列表
        return {"items": [], "total": 0}


@router.get("/lineage/{table_name}", summary="获取表血缘(兼容)")
def get_lineage_legacy(table_name: str, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """获取表的血缘关系（兼容旧API）"""
    try:
        node = (
            _scope(db.query(LineageNode), LineageNode.organization_id, organization_id)
            .filter(LineageNode.name == table_name, LineageNode.is_active == 1)
            .first()
        )

        if not node:
            return {"table": table_name, "upstream": [], "downstream": []}

        # 获取上游
        upstream_edges = (
            _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
            .filter(LineageEdge.target_id == node.id, LineageEdge.is_active == 1)
            .all()
        )

        upstream = []
        for edge in upstream_edges:
            source = (
                db.query(LineageNode).filter(LineageNode.id == edge.source_id).first()
            )
            if source:
                upstream.append({**source.to_dict(), "relation": edge.to_dict()})

        # 获取下游
        downstream_edges = (
            _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
            .filter(LineageEdge.source_id == node.id, LineageEdge.is_active == 1)
            .all()
        )

        downstream = []
        for edge in downstream_edges:
            target = (
                db.query(LineageNode).filter(LineageNode.id == edge.target_id).first()
            )
            if target:
                downstream.append({**target.to_dict(), "relation": edge.to_dict()})

        return {
            "table": table_name,
            "node": node.to_dict(),
            "upstream": upstream,
            "downstream": downstream,
        }
    except Exception as e:
        return {"table": table_name, "upstream": [], "downstream": []}


@router.get("/impact/{dataset_id:int}", summary="获取影响分析(兼容)")
def get_impact_legacy(dataset_id: int, db: Session = Depends(get_db), organization_id: int | None = Depends(lineage_organization)):
    """获取数据集影响分析（兼容旧API）"""
    try:
        node = _scope(db.query(LineageNode), LineageNode.organization_id, organization_id).filter(LineageNode.id == f"ln-dataset-{dataset_id}").first()

        if not node:
            return {
                "dataset_id": dataset_id,
                "affected_models": [],
                "affected_pipelines": [],
            }

        # 获取下游节点
        downstream = (
            _scope(db.query(LineageEdge), LineageEdge.organization_id, organization_id)
            .filter(LineageEdge.source_id == node.id, LineageEdge.is_active == 1)
            .all()
        )

        affected_models = []
        affected_pipelines = []

        for edge in downstream:
            target = (
                db.query(LineageNode).filter(LineageNode.id == edge.target_id).first()
            )
            if target:
                if target.node_type == "model":
                    affected_models.append(target.to_dict())
                elif target.node_type == "pipeline":
                    affected_pipelines.append(target.to_dict())

        return {
            "dataset_id": dataset_id,
            "node": node.to_dict(),
            "affected_models": affected_models,
            "affected_pipelines": affected_pipelines,
        }
    except Exception as e:
        return {
            "dataset_id": dataset_id,
            "affected_models": [],
            "affected_pipelines": [],
        }
