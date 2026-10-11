# 数据标准 API (前端兼容层)
from typing import Optional

from database.config import get_db
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter(prefix="/data-standard", tags=["数据标准-前端"])


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """获取数据标准统计"""
    result = db.execute(text("SELECT COUNT(*) FROM data_standards"))
    total_standards = result.fetchone()[0] or 0

    result = db.execute(text("SELECT COUNT(*) FROM data_standards WHERE is_active = 1"))
    active_standards = result.fetchone()[0] or 0

    result = db.execute(text("SELECT COUNT(*) FROM standard_fields"))
    total_fields = result.fetchone()[0] or 0

    result = db.execute(
        text("SELECT status, COUNT(*) FROM data_standards GROUP BY status")
    )
    by_status = {row[0] or "draft": row[1] for row in result.fetchall()}

    return {
        "total_standards": total_standards,
        "active_standards": active_standards,
        "total_fields": total_fields,
        "by_status": by_status,
        "compliance_rate": round(active_standards / total_standards * 100, 1)
        if total_standards > 0
        else 0,
    }


@router.get("/standards")
def list_standards(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取数据标准列表"""
    query = "SELECT * FROM data_standards WHERE 1=1"
    params = {"skip": skip, "limit": limit}

    if status:
        query += " AND status = :status"
        params["status"] = status

    query += " ORDER BY created_at DESC LIMIT :limit OFFSET :skip"

    result = db.execute(text(query), params)
    items = []
    for row in result.fetchall():
        items.append(
            {
                "id": row[0],
                "name": row[1],
                "version": row[2],
                "description": row[3],
                "status": row[5],
                "is_active": row[6],
                "created_at": str(row[7]) if row[7] else None,
                "updated_at": str(row[8]) if row[8] else None,
            }
        )

    count_query = "SELECT COUNT(*) FROM data_standards"
    if status:
        count_query += " WHERE status = :status"
    total_result = db.execute(text(count_query), {"status": status} if status else {})
    total = total_result.fetchone()[0]

    return {"total": total, "skip": skip, "limit": limit, "items": items}


@router.get("/standards/{standard_id}")
def get_standard(standard_id: int, db: Session = Depends(get_db)):
    """获取数据标准详情"""
    result = db.execute(
        text("SELECT * FROM data_standards WHERE id = :id"), {"id": standard_id}
    )
    row = result.fetchone()

    if not row:
        return {"error": "标准不存在"}

    fields_result = db.execute(
        text(
            "SELECT * FROM standard_fields WHERE standard_id = :id ORDER BY sort_order"
        ),
        {"id": standard_id},
    )
    fields = []
    for f in fields_result.fetchall():
        fields.append(
            {
                "id": f[0],
                "name": f[3],
                "display_name": f[4],
                "data_type": f[5],
                "nullable": f[8],
                "description": f[10],
            }
        )

    return {
        "id": row[0],
        "name": row[1],
        "version": row[2],
        "description": row[3],
        "status": row[5],
        "is_active": row[6],
        "fields": fields,
        "created_at": str(row[7]) if row[7] else None,
    }


@router.post("/standards")
def create_standard(data: dict, db: Session = Depends(get_db)):
    """创建数据标准"""
    result = db.execute(
        text(
            "INSERT INTO data_standards (name, version, description, status) VALUES (:name, :version, :description, 'draft') RETURNING id"
        ),
        {
            "name": data.get("name"),
            "version": data.get("version", "1.0"),
            "description": data.get("description"),
        },
    )
    db.commit()
    new_id = result.fetchone()[0]
    return {"id": new_id, "message": "创建成功"}


@router.put("/standards/{standard_id}")
def update_standard(standard_id: int, data: dict, db: Session = Depends(get_db)):
    """更新数据标准"""
    db.execute(
        text(
            "UPDATE data_standards SET name = :name, description = :description, updated_at = NOW() WHERE id = :id"
        ),
        {
            "id": standard_id,
            "name": data.get("name"),
            "description": data.get("description"),
        },
    )
    db.commit()
    return {"message": "更新成功"}


@router.delete("/standards/{standard_id}")
def delete_standard(standard_id: int, db: Session = Depends(get_db)):
    """删除数据标准"""
    db.execute(
        text("DELETE FROM standard_fields WHERE standard_id = :id"), {"id": standard_id}
    )
    db.execute(text("DELETE FROM data_standards WHERE id = :id"), {"id": standard_id})
    db.commit()
    return {"message": "删除成功"}


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    """获取标准分类"""
    return {
        "total": 3,
        "items": [
            {"id": 1, "name": "用户数据", "code": "user", "standard_count": 1},
            {"id": 2, "name": "设备数据", "code": "device", "standard_count": 1},
            {"id": 3, "name": "能源数据", "code": "energy", "standard_count": 1},
        ],
    }


@router.get("/mappings")
def list_mappings(standard_id: Optional[int] = None, db: Session = Depends(get_db)):
    """获取标准映射"""
    return {
        "total": 2,
        "items": [
            {
                "id": 1,
                "standard_id": 1,
                "table_name": "users",
                "field_count": 5,
                "status": "mapped",
            },
            {
                "id": 2,
                "standard_id": 2,
                "table_name": "devices",
                "field_count": 8,
                "status": "partial",
            },
        ],
    }


@router.get("/rules")
def list_rules(db: Session = Depends(get_db)):
    """获取验证规则"""
    return {
        "total": 4,
        "items": [
            {
                "id": 1,
                "name": "邮箱格式",
                "type": "pattern",
                "field": "email",
                "active": True,
            },
            {
                "id": 2,
                "name": "手机号格式",
                "type": "pattern",
                "field": "phone",
                "active": True,
            },
            {"id": 3, "name": "年龄范围", "type": "range", "field": "age", "active": True},
            {"id": 4, "name": "必填检查", "type": "required", "field": "*", "active": True},
        ],
    }
