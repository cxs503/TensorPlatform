"""
数据质量API（增强版）
支持规则引擎、异常检测、质量监控
"""
import logging
import re
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from database.config import Base, get_db
from api.deps import require_permission
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from services.gx_integration import validate_dataset, list_rule_types, validate_connection as gx_validate

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, func, text
from sqlalchemy.orm import Session


# 定义数据质量模型（如果不存在）
class QualityRule(Base):
    """数据质量规则"""

    __tablename__ = "quality_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    rule_type = Column(String(50), nullable=False)
    field = Column(String(100))
    condition = Column(Text)
    threshold = Column(JSON)
    severity = Column(String(20), default="warning")
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    created_by = Column(Integer)


class QualityCheckResult(Base):
    """质量检查结果"""

    __tablename__ = "quality_check_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, nullable=False)
    dataset_id = Column(Integer, nullable=False)
    passed_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    total_count = Column(Integer, default=0)
    score = Column(Integer)
    details = Column(JSON)
    checked_at = Column(DateTime, default=datetime.now)
    checked_by = Column(Integer)


class QualityAnomaly(Base):
    """质量异常记录"""

    __tablename__ = "quality_anomalies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, nullable=False)
    rule_id = Column(Integer)
    field = Column(String(100))
    anomaly_type = Column(String(50))
    expected_value = Column(Text)
    actual_value = Column(Text)
    record_ids = Column(JSON)
    record_count = Column(Integer, default=1)
    severity = Column(String(20), default="warning")
    status = Column(String(20), default="open")
    detected_at = Column(DateTime, default=datetime.now)
    resolved_at = Column(DateTime)
    resolved_by = Column(Integer)
    resolution_note = Column(Text)


from services.data_quality_service import get_data_quality_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quality", tags=["数据质量"])


# ==================== Pydantic Schemas ====================


class QualityRuleCreate(BaseModel):
    """创建质量规则"""

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    rule_type: str = Field(
        ..., description="规则类型: completeness, uniqueness, range, pattern, custom"
    )
    field: str = Field(..., description="目标字段")
    condition: Optional[str] = Field(None, description="条件表达式")
    threshold: Optional[Dict[str, Any]] = Field(None, description="阈值配置")
    severity: str = Field(
        default="warning", description="严重程度: critical, warning, info"
    )


class QualityRuleUpdate(BaseModel):
    """更新质量规则"""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    field: Optional[str] = None
    condition: Optional[str] = None
    threshold: Optional[Dict[str, Any]] = None
    severity: Optional[str] = None
    is_active: Optional[bool] = None




# ==================== Great Expectations 集成端点 ====================

class GXCheckRequest(BaseModel):
    """GX 数据质量检查请求"""
    data: List[Dict] = Field(..., description="待检查的数据行")
    rules: List[Dict] = Field(..., description="GX规则列表 [{rule_type, column, params}]")


@router.post("/gx-check", summary="Great Expectations 数据质量检查", dependencies=[Depends(require_permission("datasets:process"))])
def gx_quality_check(request: GXCheckRequest):
    """使用 Great Expectations 进行数据质量检查"""
    from fastapi.responses import JSONResponse
    result = validate_dataset(request.data, request.rules)
    return JSONResponse(content=result)


class QualityCheckRequest(BaseModel):
    """质量检查请求"""

    dataset_id: Optional[int] = None
    rule_ids: Optional[List[int]] = None  # 指定规则ID列表，为空则使用所有活跃规则
    data: Optional[List[Dict]] = None  # 可选的数据，如果不传则从数据库读取
    rules: Optional[List[Dict]] = None  # GX规则列表 [{rule_type, column, params}]


class AnomalyResolve(BaseModel):
    """解决异常"""

    resolution_note: str = Field(..., min_length=1, description="解决说明")


class RuleRevisionCreate(BaseModel):
    payload: Dict[str, Any]
    note: str = Field(default="", max_length=1000)


# ==================== 规则管理API ====================


@router.get("/rules")
def list_quality_rules(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    rule_type: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """获取质量规则列表"""
    try:
        query = "SELECT * FROM quality_rules WHERE 1=1"
        params = {}

        if rule_type:
            query += " AND rule_type = :rule_type"
            params["rule_type"] = rule_type

        if is_active is not None:
            query += " AND is_active = :is_active"
            params["is_active"] = 1 if is_active else 0

        query += " ORDER BY created_at DESC LIMIT :limit OFFSET :skip"
        params["limit"] = limit
        params["skip"] = skip

        result = db.execute(text(query), params).fetchall()

        # 获取总数
        count_query = "SELECT COUNT(*) FROM quality_rules WHERE 1=1"
        count_params = {}
        if rule_type:
            count_query += " AND rule_type = :rule_type"
            count_params["rule_type"] = rule_type
        if is_active is not None:
            count_query += " AND is_active = :is_active"
            count_params["is_active"] = 1 if is_active else 0

        total = db.execute(text(count_query), count_params).fetchone()[0]

        rules = []
        for row in result:
            rules.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "rule_type": row[3],
                    "field": row[4],
                    "condition": row[5],
                    "threshold": row[6],
                    "severity": row[7],
                    "is_active": bool(row[8]),
                    "created_at": str(row[9]) if row[9] else None,
                    "updated_at": str(row[10]) if row[10] else None,
                    "created_by": row[11],
                }
            )

        return {"total": total, "items": rules}
    except Exception as e:
        logger.error(f"获取规则列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取规则列表失败: {str(e)}")


@router.post("/rules", dependencies=[Depends(require_permission("datasets:process"))])
def create_quality_rule(
    rule: QualityRuleCreate,
    user_id: int = 1,  # TODO: 从认证获取
    db: Session = Depends(get_db),
):
    """创建质量规则"""
    try:
        # 验证规则类型
        valid_types = ["completeness", "uniqueness", "range", "pattern", "custom"]
        if rule.rule_type not in valid_types:
            raise HTTPException(status_code=400, detail=f"无效的规则类型，支持: {valid_types}")

        # 验证严重程度
        valid_severities = ["critical", "warning", "info"]
        if rule.severity not in valid_severities:
            raise HTTPException(
                status_code=400, detail=f"无效的严重程度，支持: {valid_severities}"
            )

        insert_query = text(
            """
            INSERT INTO quality_rules (name, description, rule_type, field, condition, threshold, severity, is_active, created_by)
            VALUES (:name, :description, :rule_type, :field, :condition, :threshold, :severity, 1, :created_by)
            RETURNING id
        """
        )

        result = db.execute(
            insert_query,
            {
                "name": rule.name,
                "description": rule.description,
                "rule_type": rule.rule_type,
                "field": rule.field,
                "condition": rule.condition,
                "threshold": json.dumps(rule.threshold) if rule.threshold else None,
                "severity": rule.severity,
                "created_by": user_id,
            },
        )
        db.commit()

        rule_id = result.fetchone()[0]

        return {"message": "规则创建成功", "rule_id": rule_id, "rule": rule.dict()}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"创建规则失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建规则失败: {str(e)}")


@router.put("/rules/{rule_id}", dependencies=[Depends(require_permission("datasets:process"))])
def update_quality_rule(
    rule_id: int, rule: QualityRuleUpdate, db: Session = Depends(get_db)
):
    """更新质量规则"""
    try:
        # 检查规则是否存在
        check_query = text("SELECT id FROM quality_rules WHERE id = :id")
        if not db.execute(check_query, {"id": rule_id}).fetchone():
            raise HTTPException(status_code=404, detail="规则不存在")

        # 构建更新语句
        updates = []
        params = {"id": rule_id}

        if rule.name is not None:
            updates.append("name = :name")
            params["name"] = rule.name
        if rule.description is not None:
            updates.append("description = :description")
            params["description"] = rule.description
        if rule.field is not None:
            updates.append("field = :field")
            params["field"] = rule.field
        if rule.condition is not None:
            updates.append("condition = :condition")
            params["condition"] = rule.condition
        if rule.threshold is not None:
            updates.append("threshold = :threshold")
            params["threshold"] = json.dumps(rule.threshold)
        if rule.severity is not None:
            updates.append("severity = :severity")
            params["severity"] = rule.severity
        if rule.is_active is not None:
            updates.append("is_active = :is_active")
            params["is_active"] = 1 if rule.is_active else 0

        if not updates:
            return {"message": "无更新内容"}

        updates.append("updated_at = NOW()")

        update_query = text(
            f"UPDATE quality_rules SET {', '.join(updates)} WHERE id = :id"
        )
        db.execute(update_query, params)
        db.commit()

        return {"message": "规则更新成功", "rule_id": rule_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"更新规则失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新规则失败: {str(e)}")


@router.delete("/rules/{rule_id}", dependencies=[Depends(require_permission("datasets:process"))])
def delete_quality_rule(rule_id: int, db: Session = Depends(get_db)):
    """删除质量规则"""
    try:
        # 检查规则是否存在
        check_query = text("SELECT id FROM quality_rules WHERE id = :id")
        if not db.execute(check_query, {"id": rule_id}).fetchone():
            raise HTTPException(status_code=404, detail="规则不存在")

        # 删除规则
        delete_query = text("DELETE FROM quality_rules WHERE id = :id")
        db.execute(delete_query, {"id": rule_id})
        db.commit()

        return {"message": "规则删除成功", "rule_id": rule_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"删除规则失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除规则失败: {str(e)}")


@router.get("/rules/{rule_id}")
def get_quality_rule(rule_id: int, db: Session = Depends(get_db)):
    """获取单个规则详情"""
    try:
        query = text("SELECT * FROM quality_rules WHERE id = :id")
        result = db.execute(query, {"id": rule_id}).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="规则不存在")

        return {
            "id": result[0],
            "name": result[1],
            "description": result[2],
            "rule_type": result[3],
            "field": result[4],
            "condition": result[5],
            "threshold": result[6],
            "severity": result[7],
            "is_active": bool(result[8]),
            "created_at": str(result[9]) if result[9] else None,
            "updated_at": str(result[10]) if result[10] else None,
            "created_by": result[11],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取规则详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取规则详情失败: {str(e)}")


@router.get("/rules/{rule_id}/revisions")
def list_rule_revisions(rule_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT id, version, payload, status, note, created_at, published_at FROM quality_rule_revisions WHERE rule_id=:rule_id ORDER BY version DESC"), {"rule_id": rule_id}).fetchall()
    return {"items": [{"id": row[0], "version": row[1], "payload": row[2], "status": row[3], "note": row[4], "created_at": str(row[5]), "published_at": str(row[6]) if row[6] else None} for row in rows]}


@router.post("/rules/{rule_id}/revisions", dependencies=[Depends(require_permission("datasets:process"))])
def create_rule_revision(rule_id: int, revision: RuleRevisionCreate, db: Session = Depends(get_db)):
    rule = db.execute(text("SELECT id FROM quality_rules WHERE id=:id"), {"id": rule_id}).fetchone()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    allowed = {"name", "description", "rule_type", "field", "condition", "threshold", "severity", "is_active"}
    payload = {key: value for key, value in revision.payload.items() if key in allowed}
    if not {"rule_type", "field"}.issubset(payload):
        raise HTTPException(status_code=400, detail="草稿至少需要 rule_type 和 field")
    version = db.execute(text("SELECT COALESCE(MAX(version), 0) + 1 FROM quality_rule_revisions WHERE rule_id=:rule_id"), {"rule_id": rule_id}).scalar()
    row = db.execute(text("INSERT INTO quality_rule_revisions(rule_id, version, payload, note) VALUES(:rule_id,:version,:payload,:note) RETURNING id"), {"rule_id": rule_id, "version": version, "payload": json.dumps(payload, ensure_ascii=False), "note": revision.note}).fetchone()
    db.commit()
    return {"id": row[0], "rule_id": rule_id, "version": version, "status": "draft"}


@router.post("/rules/{rule_id}/revisions/{revision_id}/publish", dependencies=[Depends(require_permission("datasets:process"))])
def publish_rule_revision(rule_id: int, revision_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT payload, status FROM quality_rule_revisions WHERE id=:id AND rule_id=:rule_id"), {"id": revision_id, "rule_id": rule_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="规则草稿不存在")
    if row[1] == "published":
        return {"message": "该版本已发布", "revision_id": revision_id}
    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    db.execute(text("""UPDATE quality_rules SET name=:name, description=:description, rule_type=:rule_type, field=:field, condition=:condition, threshold=:threshold, severity=:severity, is_active=:is_active, updated_at=NOW() WHERE id=:id"""), {"id": rule_id, "name": payload.get("name", "规则"), "description": payload.get("description"), "rule_type": payload["rule_type"], "field": payload["field"], "condition": payload.get("condition"), "threshold": json.dumps(payload.get("threshold")) if payload.get("threshold") is not None else None, "severity": payload.get("severity", "warning"), "is_active": 1 if payload.get("is_active", True) else 0})
    db.execute(text("UPDATE quality_rule_revisions SET status='superseded' WHERE rule_id=:rule_id AND status='published'"), {"rule_id": rule_id})
    db.execute(text("UPDATE quality_rule_revisions SET status='published', published_at=NOW() WHERE id=:id"), {"id": revision_id})
    db.commit()
    return {"message": "规则草稿已发布", "revision_id": revision_id}


@router.get("/rules/{rule_id}/revisions/{revision_id}/diff")
def diff_rule_revision(rule_id: int, revision_id: int, db: Session = Depends(get_db)):
    """Compare a revision with the currently effective rule for a reviewable change set."""
    row = db.execute(text("SELECT payload FROM quality_rule_revisions WHERE id=:id AND rule_id=:rule_id"), {"id": revision_id, "rule_id": rule_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="规则版本不存在")
    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    current = db.execute(text("SELECT name, description, rule_type, field, condition, threshold, severity, is_active FROM quality_rules WHERE id=:id"), {"id": rule_id}).fetchone()
    if not current:
        raise HTTPException(status_code=404, detail="规则不存在")
    fields = ["name", "description", "rule_type", "field", "condition", "threshold", "severity", "is_active"]
    baseline = dict(zip(fields, current))
    changes = []
    for key in fields:
        before, after = baseline.get(key), payload.get(key)
        if isinstance(before, (dict, list)):
            before = json.dumps(before, ensure_ascii=False, sort_keys=True)
        if isinstance(after, (dict, list)):
            after = json.dumps(after, ensure_ascii=False, sort_keys=True)
        if str(before) != str(after):
            changes.append({"field": key, "current": before, "revision": after})
    return {"rule_id": rule_id, "revision_id": revision_id, "changes": changes}


@router.post("/rules/{rule_id}/revisions/{revision_id}/rollback", dependencies=[Depends(require_permission("datasets:process"))])
def rollback_rule_revision(rule_id: int, revision_id: int, db: Session = Depends(get_db)):
    """Restore a historical published/superseded revision through the normal publish path."""
    row = db.execute(text("SELECT status FROM quality_rule_revisions WHERE id=:id AND rule_id=:rule_id"), {"id": revision_id, "rule_id": rule_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="规则版本不存在")
    if row[0] == "draft":
        raise HTTPException(status_code=400, detail="草稿请使用发布，不可作为回滚目标")
    db.execute(text("UPDATE quality_rule_revisions SET status='draft' WHERE id=:id"), {"id": revision_id})
    db.commit()
    result = publish_rule_revision(rule_id, revision_id, db)
    return {**result, "message": "已回滚到所选历史版本"}


# ==================== 质量检查API ====================


@router.post("/check", dependencies=[Depends(require_permission("datasets:process"))])
def execute_quality_check(
    request: QualityCheckRequest,
    user_id: int = 1,  # TODO: 从认证获取
    db: Session = Depends(get_db),
):
    """执行质量检查"""
    try:
        # 获取要执行的规则
        if request.rule_ids:
            rules_query = text(
                """
                SELECT * FROM quality_rules
                WHERE id IN :rule_ids AND is_active = 1
            """
            )
            rules = db.execute(
                rules_query, {"rule_ids": tuple(request.rule_ids)}
            ).fetchall()
        else:
            rules_query = text("SELECT * FROM quality_rules WHERE is_active = 1")
            rules = db.execute(rules_query).fetchall()

        if not rules:
            return {"message": "没有可执行的规则", "results": []}

        # 获取数据
        if request.data:
            df = pd.DataFrame(request.data)
        else:
            # 从数据库读取数据集
            data_query = text("SELECT * FROM datasets WHERE id = :dataset_id")
            dataset = db.execute(
                data_query, {"dataset_id": request.dataset_id}
            ).fetchone()
            if not dataset:
                raise HTTPException(status_code=404, detail="数据集不存在")

            # 根据数据集 file_path 加载真实 CSV(原为空 DataFrame 占位,导致检查结果全假)
            import os as _os
            _fp = getattr(dataset, "file_path", None) or (dataset._mapping.get("file_path") if hasattr(dataset, "_mapping") else None)
            if not _fp or not _os.path.exists(_fp):
                raise HTTPException(status_code=400, detail=f"数据集无文件或文件不存在: {_fp}")
            try:
                df = pd.read_csv(_fp)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"数据集加载失败: {e}")

        results = []
        anomalies = []

        # 执行每个规则
        for rule in rules:
            rule_id = rule[0]
            rule_type = rule[3]
            field = rule[4]
            condition = rule[5]
            threshold = rule[6] or {}
            severity = rule[7]

            result = execute_rule(df, rule_id, rule_type, field, condition, threshold)

            # numpy 值 → Python 原生(避免 psycopg2 'can't adapt type numpy.int64/float64')
            try:
                result = json.loads(json.dumps(result, default=lambda o: int(o) if hasattr(o, 'item') and not isinstance(o, bool) else str(o)))
            except Exception:
                pass

            # 保存检查结果
            insert_result = text(
                """
                INSERT INTO quality_check_results
                (rule_id, dataset_id, passed_count, failed_count, total_count, score, details, checked_by)
                VALUES (:rule_id, :dataset_id, :passed_count, :failed_count, :total_count, :score, :details, :checked_by)
                RETURNING id
            """
            )

            result_row = db.execute(
                insert_result,
                {
                    "rule_id": rule_id,
                    "dataset_id": request.dataset_id,
                    "passed_count": int(result["passed_count"]),
                    "failed_count": int(result["failed_count"]),
                    "total_count": int(result["total_count"]),
                    "score": float(result["score"]),
                    "details": json.dumps(result["details"], default=str) if result.get("details") else None,
                    "checked_by": user_id,
                },
            ).fetchone()

            result["result_id"] = result_row[0]
            results.append(result)

            # 记录异常
            if result["failed_count"] > 0:
                for anomaly in result.get("anomalies", []):
                    insert_anomaly = text(
                        """
                        INSERT INTO quality_anomalies
                        (dataset_id, rule_id, field, anomaly_type, expected_value, actual_value,
                         record_count, severity, status)
                        VALUES (:dataset_id, :rule_id, :field, :anomaly_type, :expected_value,
                                :actual_value, :record_count, :severity, 'open')
                    """
                    )
                    db.execute(
                        insert_anomaly,
                        {
                            "dataset_id": request.dataset_id,
                            "rule_id": rule_id,
                            "field": anomaly.get("field", field),
                            "anomaly_type": anomaly.get("type"),
                            "expected_value": str(anomaly.get("expected", "")),
                            "actual_value": str(anomaly.get("actual", "")),
                            "record_count": anomaly.get("count", 1),
                            "severity": severity,
                        },
                    )

        db.commit()

        return {
            "message": "质量检查完成",
            "dataset_id": request.dataset_id,
            "rules_executed": len(rules),
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"质量检查失败: {e}")
        raise HTTPException(status_code=500, detail=f"质量检查失败: {str(e)}")


def execute_rule(
    df: pd.DataFrame,
    rule_id: int,
    rule_type: str,
    field: str,
    condition: str,
    threshold: Dict,
) -> Dict:
    """执行单个规则检查"""
    result = {
        "rule_id": rule_id,
        "rule_type": rule_type,
        "field": field,
        "passed_count": 0,
        "failed_count": 0,
        "total_count": len(df),
        "score": 100,
        "details": {},
        "anomalies": [],
    }

    if field not in df.columns:
        result["details"]["error"] = f"字段 '{field}' 不存在"
        result["score"] = 0
        return result

    series = df[field]
    total = len(series)

    if rule_type == "completeness":
        # 完整性检查 - NULL检查
        null_count = series.isnull().sum()
        min_threshold = threshold.get("min", 0.9)  # 默认90%完整度

        result["passed_count"] = total - null_count
        result["failed_count"] = null_count
        result["score"] = int((total - null_count) / total * 100) if total > 0 else 100
        result["details"] = {
            "null_count": int(null_count),
            "completeness_rate": float((total - null_count) / total)
            if total > 0
            else 1.0,
        }

        if null_count > 0:
            result["anomalies"].append(
                {
                    "type": "null",
                    "field": field,
                    "count": int(null_count),
                    "expected": "非空值",
                    "actual": f"{null_count}个空值",
                }
            )

    elif rule_type == "uniqueness":
        # 唯一性检查 - 重复检测
        duplicate_count = series.duplicated().sum()
        max_duplicate_rate = threshold.get("max_rate", 0.05)  # 默认最多5%重复

        result["passed_count"] = total - duplicate_count
        result["failed_count"] = duplicate_count
        result["score"] = (
            int((total - duplicate_count) / total * 100) if total > 0 else 100
        )
        result["details"] = {
            "duplicate_count": int(duplicate_count),
            "unique_count": int(series.nunique()),
            "duplicate_rate": float(duplicate_count / total) if total > 0 else 0,
        }

        if duplicate_count > total * max_duplicate_rate:
            result["anomalies"].append(
                {
                    "type": "duplicate",
                    "field": field,
                    "count": int(duplicate_count),
                    "expected": f"重复率低于{max_duplicate_rate*100}%",
                    "actual": f"重复率{duplicate_count/total*100:.2f}%",
                }
            )

    elif rule_type == "range":
        # 范围检查 - 数值范围
        min_val = threshold.get("min")
        max_val = threshold.get("max")

        if min_val is not None and max_val is not None:
            # 数值范围检查
            numeric_series = pd.to_numeric(series, errors="coerce")
            in_range = (numeric_series >= min_val) & (numeric_series <= max_val)
            failed_count = (~in_range).sum()

            result["passed_count"] = total - failed_count
            result["failed_count"] = int(failed_count)
            result["score"] = (
                int((total - failed_count) / total * 100) if total > 0 else 100
            )
            result["details"] = {
                "min": min_val,
                "max": max_val,
                "out_of_range_count": int(failed_count),
                "actual_min": float(numeric_series.min())
                if not numeric_series.isna().all()
                else None,
                "actual_max": float(numeric_series.max())
                if not numeric_series.isna().all()
                else None,
            }

            if failed_count > 0:
                result["anomalies"].append(
                    {
                        "type": "out_of_range",
                        "field": field,
                        "count": int(failed_count),
                        "expected": f"范围[{min_val}, {max_val}]",
                        "actual": f"{failed_count}条记录超出范围",
                    }
                )

    elif rule_type == "pattern":
        # 模式检查 - 正则匹配
        pattern = threshold.get("pattern", condition)
        if pattern:
            try:
                regex = re.compile(pattern)
                valid = series.astype(str).apply(
                    lambda x: bool(regex.match(x)) if pd.notna(x) else False
                )
                failed_count = (~valid).sum()

                result["passed_count"] = int(total - failed_count)
                result["failed_count"] = int(failed_count)
                result["score"] = (
                    int((total - failed_count) / total * 100) if total > 0 else 100
                )
                result["details"] = {
                    "pattern": pattern,
                    "invalid_count": int(failed_count),
                    "valid_rate": float((total - failed_count) / total)
                    if total > 0
                    else 1.0,
                }

                if failed_count > 0:
                    result["anomalies"].append(
                        {
                            "type": "pattern_mismatch",
                            "field": field,
                            "count": int(failed_count),
                            "expected": f"匹配模式: {pattern}",
                            "actual": f"{failed_count}条记录不匹配",
                        }
                    )
            except re.error as e:
                result["details"]["error"] = f"无效的正则表达式: {str(e)}"
                result["score"] = 0

    elif rule_type == "custom":
        # 自定义检查 - SQL表达式或Python表达式
        # TODO: 实现安全的表达式执行
        result["details"]["message"] = "自定义规则执行需要额外配置"

    return result


# ==================== 检查结果API ====================


@router.get("/results")
def list_check_results(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    dataset_id: Optional[int] = None,
    rule_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """获取检查历史"""
    try:
        query = """
            SELECT r.*, qr.name as rule_name, qr.rule_type
            FROM quality_check_results r
            LEFT JOIN quality_rules qr ON r.rule_id = qr.id
            WHERE 1=1
        """
        params = {}

        if dataset_id:
            query += " AND r.dataset_id = :dataset_id"
            params["dataset_id"] = dataset_id
        if rule_id:
            query += " AND r.rule_id = :rule_id"
            params["rule_id"] = rule_id

        query += " ORDER BY r.checked_at DESC LIMIT :limit OFFSET :skip"
        params["limit"] = limit
        params["skip"] = skip

        result = db.execute(text(query), params).fetchall()

        # 获取总数
        count_query = """
            SELECT COUNT(*) FROM quality_check_results r WHERE 1=1
        """
        count_params = {}
        if dataset_id:
            count_query += " AND r.dataset_id = :dataset_id"
            count_params["dataset_id"] = dataset_id
        if rule_id:
            count_query += " AND r.rule_id = :rule_id"
            count_params["rule_id"] = rule_id

        total = db.execute(text(count_query), count_params).fetchone()[0]

        results = []
        for row in result:
            results.append(
                {
                    "id": row[0],
                    "rule_id": row[1],
                    "dataset_id": row[2],
                    "passed_count": row[3],
                    "failed_count": row[4],
                    "total_count": row[5],
                    "score": row[6],
                    "details": row[7],
                    "checked_at": str(row[8]) if row[8] else None,
                    "checked_by": row[9],
                    "rule_name": row[10] if len(row) > 10 else None,
                    "rule_type": row[11] if len(row) > 11 else None,
                }
            )

        return {"total": total, "items": results}
    except Exception as e:
        logger.error(f"获取检查历史失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取检查历史失败: {str(e)}")


# ==================== 异常管理API ====================


@router.get("/anomalies")
def list_anomalies(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    dataset_id: Optional[int] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取异常列表"""
    try:
        query = """
            SELECT a.*, qr.name as rule_name
            FROM quality_anomalies a
            LEFT JOIN quality_rules qr ON a.rule_id = qr.id
            WHERE 1=1
        """
        params = {}

        if dataset_id:
            query += " AND a.dataset_id = :dataset_id"
            params["dataset_id"] = dataset_id
        if status:
            query += " AND a.status = :status"
            params["status"] = status
        if severity:
            query += " AND a.severity = :severity"
            params["severity"] = severity

        query += " ORDER BY a.detected_at DESC LIMIT :limit OFFSET :skip"
        params["limit"] = limit
        params["skip"] = skip

        result = db.execute(text(query), params).fetchall()

        # 获取总数
        count_query = "SELECT COUNT(*) FROM quality_anomalies WHERE 1=1"
        count_params = {}
        if dataset_id:
            count_query += " AND dataset_id = :dataset_id"
            count_params["dataset_id"] = dataset_id
        if status:
            count_query += " AND status = :status"
            count_params["status"] = status
        if severity:
            count_query += " AND severity = :severity"
            count_params["severity"] = severity

        total = db.execute(text(count_query), count_params).fetchone()[0]

        anomalies = []
        for row in result:
            anomalies.append(
                {
                    "id": row[0],
                    "dataset_id": row[1],
                    "rule_id": row[2],
                    "field": row[3],
                    "anomaly_type": row[4],
                    "expected_value": row[5],
                    "actual_value": row[6],
                    "record_ids": row[7],
                    "record_count": row[8],
                    "severity": row[9],
                    "status": row[10],
                    "detected_at": str(row[11]) if row[11] else None,
                    "resolved_at": str(row[12]) if row[12] else None,
                    "resolved_by": row[13],
                    "resolution_note": row[14],
                    "rule_name": row[15] if len(row) > 15 else None,
                }
            )

        return {"total": total, "items": anomalies}
    except Exception as e:
        logger.error(f"获取异常列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取异常列表失败: {str(e)}")


@router.put("/anomalies/{anomaly_id}/resolve", dependencies=[Depends(require_permission("datasets:process"))])
def resolve_anomaly(
    anomaly_id: int,
    data: AnomalyResolve,
    user_id: int = 1,  # TODO: 从认证获取
    db: Session = Depends(get_db),
):
    """解决异常"""
    try:
        # 检查异常是否存在
        check_query = text("SELECT id, status FROM quality_anomalies WHERE id = :id")
        result = db.execute(check_query, {"id": anomaly_id}).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="异常不存在")

        if result[1] == "resolved":
            return {"message": "异常已解决", "anomaly_id": anomaly_id}

        # 更新状态
        update_query = text(
            """
            UPDATE quality_anomalies
            SET status = 'resolved', resolved_at = NOW(), resolved_by = :resolved_by, resolution_note = :note
            WHERE id = :id
        """
        )
        db.execute(
            update_query,
            {"id": anomaly_id, "resolved_by": user_id, "note": data.resolution_note},
        )
        db.commit()

        return {"message": "异常已解决", "anomaly_id": anomaly_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"解决异常失败: {e}")
        raise HTTPException(status_code=500, detail=f"解决异常失败: {str(e)}")


@router.post("/anomalies/{anomaly_id}/ignore", dependencies=[Depends(require_permission("datasets:process"))])
def ignore_anomaly(anomaly_id: int, db: Session = Depends(get_db)):
    """忽略异常"""
    try:
        # 检查异常是否存在
        check_query = text("SELECT id FROM quality_anomalies WHERE id = :id")
        if not db.execute(check_query, {"id": anomaly_id}).fetchone():
            raise HTTPException(status_code=404, detail="异常不存在")

        # 更新状态
        update_query = text(
            "UPDATE quality_anomalies SET status = 'ignored' WHERE id = :id"
        )
        db.execute(update_query, {"id": anomaly_id})
        db.commit()

        return {"message": "异常已忽略", "anomaly_id": anomaly_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"忽略异常失败: {e}")
        raise HTTPException(status_code=500, detail=f"忽略异常失败: {str(e)}")


# ==================== 仪表盘API ====================


@router.get("/dashboard")
def get_quality_dashboard(
    dataset_id: Optional[int] = None, db: Session = Depends(get_db)
):
    """获取质量仪表盘数据"""
    try:
        # 规则统计
        rules_stats = db.execute(
            text(
                """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active,
                rule_type
            FROM quality_rules
            GROUP BY rule_type
        """
            )
        ).fetchall()

        # 检查结果统计
        results_filter = ""
        params = {}
        if dataset_id:
            results_filter = " WHERE dataset_id = :dataset_id"
            params["dataset_id"] = dataset_id

        results_stats = db.execute(
            text(
                f"""
            SELECT
                COUNT(*) as total_checks,
                AVG(score) as avg_score,
                SUM(passed_count) as total_passed,
                SUM(failed_count) as total_failed
            FROM quality_check_results
            {results_filter}
        """
            ),
            params,
        ).fetchone()

        # 异常统计
        anomalies_stats = db.execute(
            text(
                f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_count,
                SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) as resolved_count,
                SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) as critical_count,
                SUM(CASE WHEN severity = 'warning' THEN 1 ELSE 0 END) as warning_count
            FROM quality_anomalies
            {results_filter}
        """
            ),
            params,
        ).fetchone()

        # 最近检查趋势（最近7天）
        trend = db.execute(
            text(
                f"""
            SELECT
                DATE(checked_at) as date,
                COUNT(*) as check_count,
                AVG(score) as avg_score
            FROM quality_check_results
            WHERE checked_at >= NOW() - INTERVAL '7 days'
            {results_filter.replace('WHERE', 'AND') if results_filter else ''}
            GROUP BY DATE(checked_at)
            ORDER BY date DESC
        """
            ),
            params,
        ).fetchall()

        # 按规则类型统计异常
        anomalies_by_type = db.execute(
            text(
                f"""
            SELECT
                anomaly_type,
                COUNT(*) as count,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_count
            FROM quality_anomalies
            {results_filter}
            GROUP BY anomaly_type
            ORDER BY count DESC
        """
            ),
            params,
        ).fetchall()

        return {
            "rules": {
                "by_type": [
                    {"rule_type": r[2], "total": r[0], "active": r[1]}
                    for r in rules_stats
                ]
            },
            "results": {
                "total_checks": results_stats[0] or 0,
                "avg_score": float(results_stats[1]) if results_stats[1] else 0,
                "total_passed": results_stats[2] or 0,
                "total_failed": results_stats[3] or 0,
            },
            "anomalies": {
                "total": anomalies_stats[0] or 0,
                "open": anomalies_stats[1] or 0,
                "resolved": anomalies_stats[2] or 0,
                "critical": anomalies_stats[3] or 0,
                "warning": anomalies_stats[4] or 0,
            },
            "trend": [
                {
                    "date": str(t[0]),
                    "check_count": t[1],
                    "avg_score": float(t[2]) if t[2] else 0,
                }
                for t in trend
            ],
            "anomalies_by_type": [
                {"type": a[0], "count": a[1], "open_count": a[2]}
                for a in anomalies_by_type
            ],
        }
    except Exception as e:
        logger.error(f"获取仪表盘数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取仪表盘数据失败: {str(e)}")


# ==================== 规则类型说明API ====================


@router.get("/rule-types")
def get_rule_types():
    return {"rule_types": list_rule_types(), "provider": "Great Expectations 1.18"}
    """获取支持的规则类型"""
    return {
        "rule_types": [
            {
                "type": "completeness",
                "name": "完整性检查",
                "description": "检查字段是否为空或NULL",
                "threshold_config": {"min": "最小完整度比例 (0-1)，默认0.9表示90%记录必须有值"},
            },
            {
                "type": "uniqueness",
                "name": "唯一性检查",
                "description": "检查字段值是否唯一，检测重复数据",
                "threshold_config": {"max_rate": "最大重复率 (0-1)，默认0.05表示最多5%可以重复"},
            },
            {
                "type": "range",
                "name": "范围检查",
                "description": "检查数值是否在指定范围内",
                "threshold_config": {"min": "最小值", "max": "最大值"},
            },
            {
                "type": "pattern",
                "name": "模式检查",
                "description": "使用正则表达式检查字段格式",
                "threshold_config": {"pattern": "正则表达式模式"},
            },
            {
                "type": "custom",
                "name": "自定义检查",
                "description": "使用自定义表达式进行检查",
                "threshold_config": {"expression": "自定义表达式"},
            },
        ]
    }
