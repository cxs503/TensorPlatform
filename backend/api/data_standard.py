"""
数据标准 API
提供数据标准管理和验证功能
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from models.data_standard import DataStandard, DataValidationResult, StandardField
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/standards", tags=["数据标准"])


# ========== Pydantic 模型 ==========


class StandardFieldCreate(BaseModel):
    """字段定义创建模型"""

    name: str
    display_name: Optional[str] = None
    data_type: str = Field(
        ..., description="数据类型: string/integer/float/date/datetime/boolean/json"
    )
    length: Optional[int] = None
    precision: Optional[int] = None
    nullable: int = 1
    default_value: Optional[str] = None
    description: Optional[str] = None
    validation_rules: Optional[Dict[str, Any]] = None
    is_primary_key: int = 0
    is_unique: int = 0
    is_indexed: int = 0
    sort_order: int = 0


class StandardFieldResponse(BaseModel):
    """字段定义响应模型"""

    id: int
    name: str
    display_name: Optional[str]
    data_type: str
    length: Optional[int]
    precision: Optional[int]
    nullable: int
    default_value: Optional[str]
    description: Optional[str]
    validation_rules: Optional[Dict[str, Any]]
    is_primary_key: int
    is_unique: int
    is_indexed: int
    sort_order: int

    class Config:
        from_attributes = True


class DataStandardCreate(BaseModel):
    """数据标准创建模型"""

    name: str
    version: str = "1.0.0"
    description: Optional[str] = None
    fields: Optional[List[StandardFieldCreate]] = None
    status: str = "draft"


class DataStandardUpdate(BaseModel):
    """数据标准更新模型"""

    name: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[List[StandardFieldCreate]] = None
    status: Optional[str] = None
    is_active: Optional[int] = None


class DataStandardResponse(BaseModel):
    """数据标准响应模型"""

    id: int
    name: str
    version: str
    description: Optional[str]
    fields: Optional[List[Dict[str, Any]]]
    status: str
    is_active: int
    created_at: datetime
    updated_at: datetime
    created_by: Optional[int]
    field_count: int

    class Config:
        from_attributes = True


class ValidateDataRequest(BaseModel):
    """数据验证请求"""

    data: List[Dict[str, Any]]
    validation_type: str = "all"


class ValidateDataResponse(BaseModel):
    """数据验证响应"""

    standard_id: int
    standard_name: str
    total_records: int
    valid_records: int
    invalid_records: int
    validation_score: float
    errors: List[Dict[str, Any]]
    warnings: List[Dict[str, Any]]


# ========== API 端点 ==========


@router.get("", response_model=Dict[str, Any])
def list_standards(
    status: Optional[str] = None,
    is_active: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """获取数据标准列表"""
    query = db.query(DataStandard)

    if status:
        query = query.filter(DataStandard.status == status)
    if is_active is not None:
        query = query.filter(DataStandard.is_active == is_active)

    total = query.count()
    standards = (
        query.order_by(DataStandard.created_at.desc()).offset(skip).limit(limit).all()
    )

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": [
            {
                "id": s.id,
                "name": s.name,
                "version": s.version,
                "description": s.description,
                "status": s.status,
                "is_active": s.is_active,
                "field_count": len(s.field_definitions) if s.field_definitions else 0,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in standards
        ],
    }


@router.post("", response_model=Dict[str, Any])
def create_standard(
    standard: DataStandardCreate,
    user_id: int = Query(1, description="用户ID"),
    db: Session = Depends(get_db),
):
    """创建数据标准"""
    # 检查名称是否已存在
    existing = db.query(DataStandard).filter(DataStandard.name == standard.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="标准名称已存在")

    # 创建标准
    db_standard = DataStandard(
        name=standard.name,
        version=standard.version,
        description=standard.description,
        status=standard.status,
        created_by=user_id,
    )
    db.add(db_standard)
    db.flush()

    # 创建字段定义
    if standard.fields:
        for idx, field in enumerate(standard.fields):
            db_field = StandardField(
                standard_id=db_standard.id,
                name=field.name,
                display_name=field.display_name,
                data_type=field.data_type,
                length=field.length,
                precision=field.precision,
                nullable=field.nullable,
                default_value=field.default_value,
                description=field.description,
                validation_rules=field.validation_rules or {},
                is_primary_key=field.is_primary_key,
                is_unique=field.is_unique,
                is_indexed=field.is_indexed,
                sort_order=idx,
            )
            db.add(db_field)

    db.commit()
    db.refresh(db_standard)

    return {
        "id": db_standard.id,
        "name": db_standard.name,
        "version": db_standard.version,
        "description": db_standard.description,
        "status": db_standard.status,
        "field_count": len(db_standard.field_definitions)
        if db_standard.field_definitions
        else 0,
        "message": "数据标准创建成功",
    }


@router.get("/{standard_id}", response_model=Dict[str, Any])
def get_standard(standard_id: int, db: Session = Depends(get_db)):
    """获取数据标准详情"""
    standard = db.query(DataStandard).filter(DataStandard.id == standard_id).first()
    if not standard:
        raise HTTPException(status_code=404, detail="数据标准不存在")

    return {
        "id": standard.id,
        "name": standard.name,
        "version": standard.version,
        "description": standard.description,
        "status": standard.status,
        "is_active": standard.is_active,
        "fields": [
            {
                "id": f.id,
                "name": f.name,
                "display_name": f.display_name,
                "data_type": f.data_type,
                "length": f.length,
                "precision": f.precision,
                "nullable": f.nullable,
                "default_value": f.default_value,
                "description": f.description,
                "validation_rules": f.validation_rules,
                "is_primary_key": f.is_primary_key,
                "is_unique": f.is_unique,
                "is_indexed": f.is_indexed,
                "sort_order": f.sort_order,
            }
            for f in sorted(standard.field_definitions, key=lambda x: x.sort_order)
        ],
        "created_at": standard.created_at.isoformat() if standard.created_at else None,
        "updated_at": standard.updated_at.isoformat() if standard.updated_at else None,
        "created_by": standard.created_by,
    }


@router.put("/{standard_id}", response_model=Dict[str, Any])
def update_standard(
    standard_id: int, update: DataStandardUpdate, db: Session = Depends(get_db)
):
    """更新数据标准"""
    standard = db.query(DataStandard).filter(DataStandard.id == standard_id).first()
    if not standard:
        raise HTTPException(status_code=404, detail="数据标准不存在")

    # 更新基本信息
    if update.name is not None:
        existing = (
            db.query(DataStandard)
            .filter(DataStandard.name == update.name, DataStandard.id != standard_id)
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="标准名称已存在")
        standard.name = update.name

    if update.version is not None:
        standard.version = update.version
    if update.description is not None:
        standard.description = update.description
    if update.status is not None:
        standard.status = update.status
    if update.is_active is not None:
        standard.is_active = update.is_active

    # 更新字段定义
    if update.fields is not None:
        # 删除旧字段
        db.query(StandardField).filter(
            StandardField.standard_id == standard_id
        ).delete()

        # 添加新字段
        for idx, field in enumerate(update.fields):
            db_field = StandardField(
                standard_id=standard_id,
                name=field.name,
                display_name=field.display_name,
                data_type=field.data_type,
                length=field.length,
                precision=field.precision,
                nullable=field.nullable,
                default_value=field.default_value,
                description=field.description,
                validation_rules=field.validation_rules or {},
                is_primary_key=field.is_primary_key,
                is_unique=field.is_unique,
                is_indexed=field.is_indexed,
                sort_order=idx,
            )
            db.add(db_field)

    db.commit()
    db.refresh(standard)

    return {
        "id": standard.id,
        "name": standard.name,
        "version": standard.version,
        "message": "数据标准更新成功",
    }


@router.delete("/{standard_id}")
def delete_standard(standard_id: int, db: Session = Depends(get_db)):
    """删除数据标准"""
    standard = db.query(DataStandard).filter(DataStandard.id == standard_id).first()
    if not standard:
        raise HTTPException(status_code=404, detail="数据标准不存在")

    db.delete(standard)
    db.commit()

    return {"message": "数据标准删除成功"}


@router.post("/{standard_id}/validate", response_model=ValidateDataResponse)
def validate_data(
    standard_id: int,
    request: ValidateDataRequest,
    user_id: int = Query(1, description="用户ID"),
    db: Session = Depends(get_db),
):
    """根据标准验证数据"""
    standard = db.query(DataStandard).filter(DataStandard.id == standard_id).first()
    if not standard:
        raise HTTPException(status_code=404, detail="数据标准不存在")

    if not standard.field_definitions:
        raise HTTPException(status_code=400, detail="标准未定义字段")

    errors = []
    warnings = []
    valid_records = 0
    invalid_records = 0

    # 构建字段映射
    field_map = {f.name: f for f in standard.field_definitions}

    for idx, record in enumerate(request.data):
        record_errors = []
        record_warnings = []

        for field_name, field_def in field_map.items():
            value = record.get(field_name)

            # 检查必填
            if field_def.nullable == 0 and value is None:
                if field_def.default_value:
                    continue  # 有默认值
                record_errors.append(
                    {"field": field_name, "error": "字段不能为空", "error_type": "required"}
                )
                continue

            if value is None:
                continue

            # 类型验证
            type_valid, type_error = validate_type(
                value, field_def.data_type, field_def.length, field_def.precision
            )
            if not type_valid:
                record_errors.append(
                    {
                        "field": field_name,
                        "value": str(value)[:50],
                        "error": type_error,
                        "error_type": "type_mismatch",
                    }
                )

            # 唯一性检查(在记录间)
            if field_def.is_unique == 1:
                same_value_count = sum(
                    1 for r in request.data[:idx] if r.get(field_name) == value
                )
                if same_value_count > 0:
                    record_errors.append(
                        {
                            "field": field_name,
                            "error": f"值 '{value}' 不唯一",
                            "error_type": "unique_violation",
                        }
                    )

            # 自定义验证规则
            if field_def.validation_rules:
                custom_errors = validate_rules(
                    value, field_def.validation_rules, field_name
                )
                record_errors.extend(custom_errors)

        if record_errors:
            invalid_records += 1
            errors.append({"record_index": idx, "errors": record_errors})
        else:
            valid_records += 1

        if record_warnings:
            warnings.append({"record_index": idx, "warnings": record_warnings})

    total_records = len(request.data)
    validation_score = round(
        (valid_records / total_records * 100) if total_records > 0 else 0, 2
    )

    # 保存验证结果
    validation_result = DataValidationResult(
        standard_id=standard_id,
        validation_type=request.validation_type,
        total_records=total_records,
        valid_records=valid_records,
        invalid_records=invalid_records,
        validation_score=int(validation_score),
        errors=errors[:100],  # 限制保存的错误数量
        warnings=warnings[:100],
        validated_by=user_id,
    )
    db.add(validation_result)
    db.commit()

    return ValidateDataResponse(
        standard_id=standard_id,
        standard_name=standard.name,
        total_records=total_records,
        valid_records=valid_records,
        invalid_records=invalid_records,
        validation_score=validation_score,
        errors=errors[:50],  # 返回前50条错误
        warnings=warnings[:50],
    )


@router.get("/{standard_id}/fields", response_model=List[Dict[str, Any]])
def list_standard_fields(standard_id: int, db: Session = Depends(get_db)):
    """获取标准的字段列表"""
    standard = db.query(DataStandard).filter(DataStandard.id == standard_id).first()
    if not standard:
        raise HTTPException(status_code=404, detail="数据标准不存在")

    return [
        {
            "id": f.id,
            "name": f.name,
            "display_name": f.display_name,
            "data_type": f.data_type,
            "length": f.length,
            "precision": f.precision,
            "nullable": f.nullable,
            "default_value": f.default_value,
            "description": f.description,
            "validation_rules": f.validation_rules,
            "is_primary_key": f.is_primary_key,
            "is_unique": f.is_unique,
            "is_indexed": f.is_indexed,
            "sort_order": f.sort_order,
        }
        for f in sorted(standard.field_definitions, key=lambda x: x.sort_order)
    ]


@router.post("/{standard_id}/fields", response_model=Dict[str, Any])
def add_field_to_standard(
    standard_id: int, field: StandardFieldCreate, db: Session = Depends(get_db)
):
    """向标准添加字段"""
    standard = db.query(DataStandard).filter(DataStandard.id == standard_id).first()
    if not standard:
        raise HTTPException(status_code=404, detail="数据标准不存在")

    # 检查字段名是否已存在
    existing = (
        db.query(StandardField)
        .filter(
            StandardField.standard_id == standard_id, StandardField.name == field.name
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="字段名已存在")

    # 获取最大排序号
    max_order = (
        db.query(StandardField).filter(StandardField.standard_id == standard_id).count()
    )

    db_field = StandardField(
        standard_id=standard_id,
        name=field.name,
        display_name=field.display_name,
        data_type=field.data_type,
        length=field.length,
        precision=field.precision,
        nullable=field.nullable,
        default_value=field.default_value,
        description=field.description,
        validation_rules=field.validation_rules or {},
        is_primary_key=field.is_primary_key,
        is_unique=field.is_unique,
        is_indexed=field.is_indexed,
        sort_order=max_order,
    )
    db.add(db_field)
    db.commit()
    db.refresh(db_field)

    return {
        "id": db_field.id,
        "name": db_field.name,
        "data_type": db_field.data_type,
        "message": "字段添加成功",
    }


@router.delete("/{standard_id}/fields/{field_id}")
def remove_field_from_standard(
    standard_id: int, field_id: int, db: Session = Depends(get_db)
):
    """从标准中删除字段"""
    field = (
        db.query(StandardField)
        .filter(StandardField.id == field_id, StandardField.standard_id == standard_id)
        .first()
    )

    if not field:
        raise HTTPException(status_code=404, detail="字段不存在")

    db.delete(field)
    db.commit()

    return {"message": "字段删除成功"}


# ========== 辅助函数 ==========


def validate_type(
    value: Any, data_type: str, length: int = None, precision: int = None
) -> tuple:
    """验证数据类型"""
    try:
        if data_type == "string":
            if not isinstance(value, str):
                return False, f"期望字符串类型，实际为 {type(value).__name__}"
            if length and len(value) > length:
                return False, f"长度超过限制 {length}"
        elif data_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                return False, f"期望整数类型，实际为 {type(value).__name__}"
        elif data_type == "float":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False, f"期望浮点类型，实际为 {type(value).__name__}"
            if precision is not None:
                # 检查小数位数
                str_val = str(value)
                if "." in str_val and len(str_val.split(".")[1]) > precision:
                    return False, f"小数位数超过限制 {precision}"
        elif data_type == "boolean":
            if not isinstance(value, bool):
                return False, f"期望布尔类型，实际为 {type(value).__name__}"
        elif data_type == "date":
            if isinstance(value, str):
                datetime.strptime(value, "%Y-%m-%d")
            elif not hasattr(value, "strftime"):
                return False, f"期望日期类型"
        elif data_type == "datetime":
            if isinstance(value, str):
                datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            elif not hasattr(value, "strftime"):
                return False, f"期望日期时间类型"
        elif data_type == "json":
            if not isinstance(value, (dict, list)):
                return False, f"期望JSON类型，实际为 {type(value).__name__}"

        return True, None
    except Exception as e:
        return False, str(e)


def validate_rules(
    value: Any, rules: Dict[str, Any], field_name: str
) -> List[Dict[str, Any]]:
    """验证自定义规则"""
    errors = []

    # 正则验证
    if "pattern" in rules:
        pattern = rules["pattern"]
        if not re.match(pattern, str(value)):
            errors.append(
                {
                    "field": field_name,
                    "error": f"值 '{value}' 不匹配模式 {pattern}",
                    "error_type": "pattern_mismatch",
                }
            )

    # 范围验证
    if "min" in rules and value < rules["min"]:
        errors.append(
            {
                "field": field_name,
                "error": f"值 {value} 小于最小值 {rules['min']}",
                "error_type": "range_violation",
            }
        )
    if "max" in rules and value > rules["max"]:
        errors.append(
            {
                "field": field_name,
                "error": f"值 {value} 大于最大值 {rules['max']}",
                "error_type": "range_violation",
            }
        )

    # 枚举验证
    if "enum" in rules:
        if value not in rules["enum"]:
            errors.append(
                {
                    "field": field_name,
                    "error": f"值 '{value}' 不在允许的枚举值中",
                    "error_type": "enum_violation",
                }
            )

    return errors
