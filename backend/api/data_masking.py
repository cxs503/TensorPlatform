"""
数据脱敏 API (增强版)
提供脱敏规则管理、数据脱敏、访问策略和审计日志功能
"""
from datetime import datetime
import csv
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from database.config import get_db, SessionLocal
from sqlalchemy import text
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from services.data_security_service import DataSecurityService
from sqlalchemy.orm import Session
from models.core import Dataset
from models.data_security import MaskingRule
from api.deps import require_auth
from api.tenant_access import scope_query, create_organization_id

router = APIRouter(prefix="/masking", tags=["数据脱敏"])
MASKING_JOBS: Dict[str, Dict[str, Any]] = {}


class DatasetMaskingRequest(BaseModel):
    dataset_id: int
    rule_id: Optional[int] = None
    output_name: Optional[str] = None


def _run_dataset_masking(job_id: str, dataset_id: int, rule_id: Optional[int], output_name: str, organization_id: Optional[int]):
    """Stream a CSV into a new derived dataset; no original file is modified."""
    db = SessionLocal()
    job = MASKING_JOBS[job_id]
    try:
        source = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if not source or not source.file_path or not os.path.exists(source.file_path):
            raise ValueError("源数据集文件不存在或不可读取")
        query = db.query(MaskingRule).filter(MaskingRule.is_active == 1).order_by(MaskingRule.priority)
        if rule_id:
            query = query.filter(MaskingRule.id == rule_id)
        rules = query.all()
        if rule_id and not rules:
            raise ValueError("指定脱敏规则不存在或未启用")
        derived = Dataset(name=output_name, description=f"由数据集 {dataset_id} 全量脱敏生成；任务 {job_id}", file_path=None, row_count=0, size_bytes=0, stage="masked", governed_from=dataset_id, organization_id=organization_id)
        db.add(derived); db.commit(); db.refresh(derived)
        output_dir = Path("/app/uploads/datasets"); output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{derived.id}_masked.csv"
        processed = 0; masked_cells = 0
        with open(source.file_path, "r", encoding="utf-8-sig", newline="") as src, open(output_path, "w", encoding="utf-8", newline="") as dst:
            reader = csv.DictReader(src)
            if not reader.fieldnames:
                raise ValueError("源数据集缺少 CSV 表头")
            writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                for field, value in row.items():
                    for rule in rules:
                        if re.match(rule.field_pattern, field, re.IGNORECASE):
                            if value not in (None, ""):
                                row[field] = DataSecurityService(db)._mask_value(str(value), rule.masking_type, rule.masking_strategy, rule.parameters)
                                masked_cells += 1
                            break
                writer.writerow(row); processed += 1
                if processed % 500 == 0:
                    job.update({"processed_rows": processed, "masked_cells": masked_cells})
        derived.file_path, derived.row_count, derived.size_bytes = str(output_path), processed, output_path.stat().st_size
        db.commit()
        job.update({"status": "success", "processed_rows": processed, "masked_cells": masked_cells, "dataset_id": derived.id, "finished_at": datetime.now().isoformat()})
    except Exception as exc:
        db.rollback()
        job.update({"status": "error", "error": str(exc), "finished_at": datetime.now().isoformat()})
    finally:
        db.close()


@router.post("/dataset-jobs")
def create_dataset_masking_job(req: DatasetMaskingRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    """Start a non-destructive, full-dataset masking task in the backend."""
    source = scope_query(db.query(Dataset), Dataset.organization_id, current_user).filter(Dataset.id == req.dataset_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据集不存在或无权访问")
    if not source.file_path or not os.path.exists(source.file_path):
        raise HTTPException(status_code=400, detail="数据集没有可处理的 CSV 文件")
    job_id = str(uuid.uuid4())
    output_name = (req.output_name or f"{source.name}_脱敏版")[:200]
    MASKING_JOBS[job_id] = {"id": job_id, "status": "running", "source_dataset_id": req.dataset_id, "processed_rows": 0, "masked_cells": 0, "started_at": datetime.now().isoformat()}
    organization_id = create_organization_id(None, current_user)
    threading.Thread(target=_run_dataset_masking, args=(job_id, req.dataset_id, req.rule_id, output_name, organization_id), daemon=True).start()
    return MASKING_JOBS[job_id]


@router.get("/dataset-jobs/{job_id}")
def get_dataset_masking_job(job_id: str, current_user: dict = Depends(require_auth)):
    job = MASKING_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="脱敏任务不存在或服务已重启")
    return job


# ========== Pydantic 模型 ==========


class MaskingRuleCreate(BaseModel):
    """脱敏规则创建模型"""

    name: str
    description: Optional[str] = None
    field_pattern: str = Field(..., description="字段匹配正则表达式")
    masking_type: str = Field(
        ..., description="脱敏类型: phone/id_card/bank_card/email/name/address/custom"
    )
    masking_strategy: str = Field(
        default="mask",
        description="脱敏策略: mask/hash/encrypt/replace/shuffle/truncate/fpe",
    )
    parameters: Optional[Dict[str, Any]] = None
    is_active: int = Field(default=1)
    priority: int = Field(default=100)


class MaskingRuleUpdate(BaseModel):
    """脱敏规则更新模型"""

    name: Optional[str] = None
    description: Optional[str] = None
    field_pattern: Optional[str] = None
    masking_type: Optional[str] = None
    masking_strategy: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    is_active: Optional[int] = None
    priority: Optional[int] = None


class AccessPolicyCreate(BaseModel):
    """访问策略创建模型"""

    name: str
    description: Optional[str] = None
    resource_type: str = Field(..., description="资源类型: table/column/api/file")
    resource_pattern: str = Field(..., description="资源匹配正则表达式")
    action: str = Field(..., description="操作: read/write/delete/admin/*")
    effect: str = Field(default="deny", description="效果: allow/deny")
    conditions: Optional[Dict[str, Any]] = None
    priority: int = Field(default=100)
    is_active: int = Field(default=1)


class AccessPolicyUpdate(BaseModel):
    """访问策略更新模型"""

    name: Optional[str] = None
    description: Optional[str] = None
    resource_type: Optional[str] = None
    resource_pattern: Optional[str] = None
    action: Optional[str] = None
    effect: Optional[str] = None
    conditions: Optional[Dict[str, Any]] = None
    priority: Optional[int] = None
    is_active: Optional[int] = None


class ApplyMaskingRequest(BaseModel):
    """应用脱敏请求"""

    data: List[Dict[str, Any]] = Field(..., description="要脱敏的数据列表")
    field_mapping: Optional[Dict[str, str]] = Field(None, description="字段映射")
    rule_id: Optional[int] = Field(None, description="指定单条启用的脱敏规则；为空则使用全部启用规则")


class AccessCheckRequest(BaseModel):
    """访问检查请求"""

    user_id: int
    resource_type: str
    resource: str
    action: str


class AuditLogRequest(BaseModel):
    """审计日志请求"""

    user_id: Optional[int] = None
    username: Optional[str] = None
    resource_type: Optional[str] = None
    resource: Optional[str] = None
    action: Optional[str] = None
    result: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_method: Optional[str] = None
    request_path: Optional[str] = None
    request_params: Optional[Dict[str, Any]] = None
    response_code: Optional[int] = None
    duration_ms: Optional[int] = None
    policy_matched: Optional[str] = None
    masking_applied: Optional[int] = 0
    extra_info: Optional[Dict[str, Any]] = None


# ========== 脱敏规则 API ==========


@router.get("/rules", response_model=Dict[str, Any])
def list_masking_rules(
    is_active: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """获取脱敏规则列表"""
    service = DataSecurityService(db)
    rules = service.list_masking_rules(is_active)

    return {
        "total": len(rules),
        "skip": skip,
        "limit": limit,
        "items": rules[skip : skip + limit],
    }


@router.post("/rules", response_model=Dict[str, Any])
def create_masking_rule(rule: MaskingRuleCreate, db: Session = Depends(get_db)):
    """创建脱敏规则"""
    service = DataSecurityService(db)
    try:
        result = service.create_masking_rule(rule.model_dump())
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/rules/{rule_id}", response_model=Dict[str, Any])
def get_masking_rule(rule_id: int, db: Session = Depends(get_db)):
    """获取脱敏规则详情"""
    service = DataSecurityService(db)
    rule = service.get_masking_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    return rule


@router.put("/rules/{rule_id}", response_model=Dict[str, Any])
def update_masking_rule(
    rule_id: int, rule: MaskingRuleUpdate, db: Session = Depends(get_db)
):
    """更新脱敏规则"""
    service = DataSecurityService(db)
    try:
        result = service.update_masking_rule(
            rule_id, rule.model_dump(exclude_unset=True)
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/rules/{rule_id}")
def delete_masking_rule(rule_id: int, db: Session = Depends(get_db)):
    """删除脱敏规则"""
    service = DataSecurityService(db)
    if not service.delete_masking_rule(rule_id):
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"message": "脱敏规则删除成功"}


# ========== 数据脱敏 API ==========


@router.post("/apply", response_model=Dict[str, Any])
def apply_masking(request: ApplyMaskingRequest, db: Session = Depends(get_db)):
    """应用脱敏规则到数据"""
    service = DataSecurityService(db)

    if not isinstance(request.data, list):
        raise HTTPException(status_code=400, detail="数据必须是列表")

    selected_rules = None
    if request.rule_id is not None:
        selected_rules = (
            db.query(MaskingRule)
            .filter(MaskingRule.id == request.rule_id, MaskingRule.is_active == 1)
            .all()
        )
        if not selected_rules:
            raise HTTPException(status_code=404, detail="指定脱敏规则不存在或未启用")
    masked_data = service.apply_masking(request.data, request.field_mapping, rules=selected_rules)

    return {
        "original_count": len(request.data),
        "masked_count": len(masked_data),
        "data": masked_data,
    }


class DatasetMaskingPreviewReq(BaseModel):
    dataset_id: int
    rule_ids: Optional[List[int]] = None
    limit: int = 10


@router.post("/preview", response_model=Dict[str, Any])
def preview_masking(request: DatasetMaskingPreviewReq, db: Session = Depends(get_db)):
    """预览脱敏效果(前N行)"""
    import pandas as pd
    from models.core import Dataset
    ds = db.query(Dataset).filter(Dataset.id == request.dataset_id).first()
    if not ds or not ds.file_path:
        raise HTTPException(status_code=404, detail="数据集不存在或无文件")
    df = pd.read_csv(ds.file_path, nrows=request.limit)
    records = df.to_dict(orient="records")
    service = DataSecurityService(db)
    # 从 masking_rules 表加载规则(用 field_pattern 匹配列名)
    all_rules = db.execute(text("SELECT field_pattern, masking_type FROM masking_rules WHERE is_active = 1")).fetchall()
    field_mapping = {}
    import re as _re
    for pattern, mtype in all_rules:
        for col in df.columns:
            try:
                # field_pattern 是 | 分隔的多模式,拆分后逐个尝试
                for sub in str(pattern).split('|'):
                    sub = sub.strip()
                    if sub and _re.search(sub, col, _re.IGNORECASE):
                        field_mapping[col] = mtype
                        break
            except _re.error:
                continue
    masked = service.apply_masking(records, field_mapping if field_mapping else None)
    return {"original": records[:3], "masked": masked[:3], "columns": list(df.columns), "total": len(records)}


class DatasetMaskingApplyReq(BaseModel):
    dataset_id: int
    rule_ids: Optional[List[int]] = None


@router.post("/apply-dataset", response_model=Dict[str, Any])
def apply_masking_to_dataset(request: DatasetMaskingApplyReq, db: Session = Depends(get_db)):
    """对整个数据集执行脱敏,生成脱敏后的CSV并下载"""
    import pandas as pd
    import io as _io
    from fastapi.responses import StreamingResponse
    from models.core import Dataset
    ds = db.query(Dataset).filter(Dataset.id == request.dataset_id).first()
    if not ds or not ds.file_path:
        raise HTTPException(status_code=404, detail="数据集不存在或无文件")
    df = pd.read_csv(ds.file_path)
    records = df.to_dict(orient="records")
    service = DataSecurityService(db)
    all_rules = db.execute(text("SELECT field_pattern, masking_type FROM masking_rules WHERE is_active = 1")).fetchall()
    field_mapping = {}
    import re as _re2
    for pattern, mtype in all_rules:
        for col in df.columns:
            try:
                for sub in str(pattern).split('|'):
                    sub = sub.strip()
                    if sub and _re2.search(sub, col, _re2.IGNORECASE):
                        field_mapping[col] = mtype
                        break
            except _re2.error:
                continue
    masked = service.apply_masking(records, field_mapping if field_mapping else None)
    out_df = pd.DataFrame(masked)
    buf = _io.BytesIO()
    out_df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)
    return StreamingResponse(buf, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="masked_{ds.name}.csv"'})


@router.post("/mask-single", response_model=Dict[str, Any])
def mask_single_record(record: Dict[str, Any], db: Session = Depends(get_db)):
    """脱敏单条记录"""
    service = DataSecurityService(db)
    result = service.apply_masking([record])
    return {"data": result[0] if result else {}}


@router.get("/types", response_model=Dict[str, Any])
def list_masking_types():
    """列出支持的脱敏类型"""
    return {
        "masking_types": [
            {"value": "phone", "name": "手机号", "example": "138****5678"},
            {"value": "id_card", "name": "身份证号", "example": "110***********1234"},
            {"value": "bank_card", "name": "银行卡号", "example": "6222****1234"},
            {"value": "email", "name": "电子邮箱", "example": "a***@example.com"},
            {"value": "name", "name": "姓名", "example": "张**"},
            {"value": "address", "name": "地址", "example": "北京市朝阳区***"},
            {"value": "custom", "name": "自定义", "example": "根据参数配置"},
        ],
        "masking_strategies": [
            {"value": "mask", "name": "掩码显示", "description": "保留部分字符，其余用*替代"},
            {"value": "hash", "name": "哈希加密", "description": "不可逆加密"},
            {"value": "encrypt", "name": "加密", "description": "可逆加密"},
            {"value": "replace", "name": "替换", "description": "统一替换为指定值"},
            {"value": "shuffle", "name": "打乱", "description": "打乱字符顺序"},
            {"value": "truncate", "name": "截断", "description": "保留前N位"},
            {"value": "fpe", "name": "格式保留加密", "description": "保持格式的加密"},
        ],
    }


# ========== 访问策略 API ==========


@router.get("/policies", response_model=Dict[str, Any])
def list_access_policies(
    is_active: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """获取访问策略列表"""
    service = DataSecurityService(db)
    policies = service.list_access_policies(is_active)

    return {
        "total": len(policies),
        "skip": skip,
        "limit": limit,
        "items": policies[skip : skip + limit],
    }


@router.post("/policies", response_model=Dict[str, Any])
def create_access_policy(policy: AccessPolicyCreate, db: Session = Depends(get_db)):
    """创建访问策略"""
    service = DataSecurityService(db)
    try:
        result = service.create_access_policy(policy.model_dump())
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/policies/{policy_id}", response_model=Dict[str, Any])
def update_access_policy(
    policy_id: int, policy: AccessPolicyUpdate, db: Session = Depends(get_db)
):
    """更新访问策略"""
    service = DataSecurityService(db)
    try:
        result = service.update_access_policy(
            policy_id, policy.model_dump(exclude_unset=True)
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/policies/{policy_id}")
def delete_access_policy(policy_id: int, db: Session = Depends(get_db)):
    """删除访问策略"""
    service = DataSecurityService(db)
    if not service.delete_access_policy(policy_id):
        raise HTTPException(status_code=404, detail="策略不存在")
    return {"message": "访问策略删除成功"}


@router.post("/check-access", response_model=Dict[str, Any])
def check_access(request: AccessCheckRequest, db: Session = Depends(get_db)):
    """检查访问权限"""
    service = DataSecurityService(db)
    result = service.check_access(
        request.user_id, request.resource_type, request.resource, request.action
    )
    return result


# ========== 审计日志 API ==========


@router.get("/audit", response_model=Dict[str, Any])
def list_audit_logs(
    user_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    result: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """获取审计日志列表"""
    service = DataSecurityService(db)
    return service.list_audit_logs(
        user_id=user_id,
        resource_type=resource_type,
        result=result,
        start_time=start_time,
        end_time=end_time,
        skip=skip,
        limit=limit,
    )


@router.post("/audit", response_model=Dict[str, Any])
def log_audit(request: AuditLogRequest, db: Session = Depends(get_db)):
    """记录审计日志"""
    service = DataSecurityService(db)
    return service.log_access(request.model_dump())


@router.get("/audit/statistics", response_model=Dict[str, Any])
def get_audit_statistics(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_db),
):
    """获取审计统计"""
    service = DataSecurityService(db)
    return service.get_audit_statistics(start_time, end_time)


# ========== 兼容旧API ==========


@router.post("/analyze", response_model=Dict[str, Any])
def analyze_sensitivity(request: ApplyMaskingRequest, db: Session = Depends(get_db)):
    """分析数据敏感度"""
    service = DataSecurityService(db)

    # 应用脱敏获取字段信息
    sample = request.data[:100] if len(request.data) > 100 else request.data

    # 获取脱敏规则
    rules = service.list_masking_rules(is_active=1)

    field_stats = {}
    all_fields = set()
    for record in sample:
        all_fields.update(record.keys())

    import re

    for field in all_fields:
        matched_rule = None
        for rule in rules:
            if re.match(rule["field_pattern"], field, re.IGNORECASE):
                matched_rule = rule
                break

        field_stats[field] = {
            "sensitive": matched_rule is not None,
            "recommended_rule": matched_rule["name"] if matched_rule else None,
            "masking_type": matched_rule["masking_type"] if matched_rule else None,
        }

    total_fields = len(field_stats)
    sensitive_fields = sum(1 for v in field_stats.values() if v["sensitive"])

    return {
        "total_fields": total_fields,
        "sensitive_fields": sensitive_fields,
        "field_details": field_stats,
        "recommendations": [
            {"field": field, "action": "建议脱敏", "rule": details["recommended_rule"]}
            for field, details in field_stats.items()
            if details["sensitive"]
        ],
    }
