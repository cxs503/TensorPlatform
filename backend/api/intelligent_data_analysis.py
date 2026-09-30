"""LLM-assisted analysis backed by locally computed dataset statistics."""
from __future__ import annotations

import io
import json
from pathlib import Path
import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from api.deps import require_auth, require_permission
from api.tenant_access import assert_organization_access, is_admin
from database.config import get_db
from models.core import Dataset
from services.intelligent_data_analysis_service import (
    build_llm_prompt,
    llm_summary,
    local_narrative,
    profile_frame,
)
from services.llm_service import get_llm_service


router = APIRouter(prefix="/intelligent-analysis", tags=["智能数据分析"])
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _ensure_table(db: Session) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS intelligent_data_analysis_reports (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NULL REFERENCES organizations(id) ON DELETE SET NULL,
            dataset_id INTEGER NULL REFERENCES datasets(id) ON DELETE SET NULL,
            source_name VARCHAR(255) NOT NULL,
            profile JSONB NOT NULL,
            narrative TEXT NOT NULL,
            llm_provider VARCHAR(64),
            sample_shared BOOLEAN NOT NULL DEFAULT FALSE,
            created_by VARCHAR(128),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    db.commit()


def _load_dataset_frame(dataset: Dataset) -> pd.DataFrame:
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
        raise HTTPException(status_code=400, detail=f"暂不支持分析该文件类型：{suffix or '未知'}")
    if dataset.data_blob:
        return pd.read_csv(io.BytesIO(dataset.data_blob))
    raise HTTPException(status_code=404, detail="数据集没有可读取的数据文件")


def _read_uploaded_csv(filename: str, content: bytes) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".txt", ".tsv"}:
        raise HTTPException(status_code=400, detail="第一版上传分析支持 CSV、TXT 和 TSV 文件")
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="分析文件不能超过50MB")
    try:
        return pd.read_csv(io.BytesIO(content), sep="\t" if suffix == ".tsv" else ",")
    except UnicodeDecodeError:
        try:
            return pd.read_csv(io.BytesIO(content), sep="\t" if suffix == ".tsv" else ",", encoding="gb18030")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"文件编码或表格格式无法识别：{exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"CSV解析失败：{exc}") from exc


def _narrative(profile: dict) -> tuple[str, str | None, str | None]:
    service = get_llm_service()
    provider = getattr(service, "provider", None)
    if not provider:
        return local_narrative(profile), None, "外部LLM未配置，已生成本地统计摘要"
    summary = llm_summary(profile)
    try:
        return service.chat(
            build_llm_prompt(summary), max_tokens=1800, temperature=0.2,
            trace_operation="data_analysis",
        ), provider, None
    except Exception as exc:
        return local_narrative(profile), provider, f"LLM解释失败，已保留本地统计：{str(exc)[:200]}"


def _save_report(
    db: Session,
    *,
    organization_id: int | None,
    dataset_id: int | None,
    source_name: str,
    profile: dict,
    narrative: str,
    provider: str | None,
    current_user: dict,
) -> int:
    _ensure_table(db)
    row = db.execute(text("""
        INSERT INTO intelligent_data_analysis_reports
            (organization_id, dataset_id, source_name, profile, narrative,
             llm_provider, sample_shared, created_by, created_at)
        VALUES (:org, :dataset, :source, CAST(:profile AS JSONB), :narrative,
                :provider, :sample, :actor, NOW())
        RETURNING id
    """), {
        "org": organization_id, "dataset": dataset_id, "source": source_name,
        "profile": json.dumps(profile, ensure_ascii=False), "narrative": narrative,
        "provider": provider, "sample": False,
        "actor": str(current_user.get("username") or current_user.get("sub") or "unknown"),
    }).scalar()
    db.commit()
    return int(row)


def _result(report_id: int, dataset_id: int | None, profile: dict, narrative: str, provider: str | None, warning: str | None) -> dict:
    return {
        "report_id": report_id, "dataset_id": dataset_id, "profile": profile,
        "narrative": narrative, "llm_provider": provider,
        "warning": warning,
    }


@router.post(
    "/datasets/{dataset_id}/analyze",
    dependencies=[Depends(require_permission("datasets:analyze"))],
)
def analyze_existing_dataset(
    dataset_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    assert_organization_access(dataset.organization_id, current_user)
    frame = _load_dataset_frame(dataset)
    try:
        profile = profile_frame(
            frame, source_name=dataset.name, total_rows=dataset.row_count or len(frame),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    narrative, provider, warning = _narrative(profile)
    report_id = _save_report(
        db, organization_id=dataset.organization_id, dataset_id=dataset.id,
        source_name=dataset.name, profile=profile, narrative=narrative,
        provider=provider, current_user=current_user,
    )
    return _result(report_id, dataset.id, profile, narrative, provider, warning)


@router.post(
    "/upload",
    dependencies=[Depends(require_permission("datasets:analyze"))],
)
async def analyze_uploaded_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    filename = Path(file.filename or "uploaded.csv").name
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    frame = await run_in_threadpool(_read_uploaded_csv, filename, content)
    try:
        profile = await run_in_threadpool(
            profile_frame,
            frame,
            source_name=filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    narrative, provider, warning = await run_in_threadpool(_narrative, profile)
    report_id = _save_report(
        db, organization_id=current_user.get("organization_id"), dataset_id=None,
        source_name=filename, profile=profile, narrative=narrative,
        provider=provider, current_user=current_user,
    )
    return _result(report_id, None, profile, narrative, provider, warning)


@router.get("/reports", dependencies=[Depends(require_permission("datasets:read"))])
def list_reports(db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    _ensure_table(db)
    params = {}
    where = ""
    if not is_admin(current_user):
        where = "WHERE organization_id = :org"
        params["org"] = current_user.get("organization_id")
    rows = db.execute(text(f"""
        SELECT id, dataset_id, source_name, llm_provider, created_by, created_at
        FROM intelligent_data_analysis_reports {where}
        ORDER BY created_at DESC, id DESC LIMIT 200
    """), params).mappings().all()
    return {"total": len(rows), "items": [dict(row) for row in rows]}


@router.get("/reports/{report_id}", dependencies=[Depends(require_permission("datasets:read"))])
def get_report(report_id: int, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    _ensure_table(db)
    row = db.execute(text("SELECT * FROM intelligent_data_analysis_reports WHERE id=:id"), {"id": report_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="分析报告不存在")
    if not is_admin(current_user) and row["organization_id"] != current_user.get("organization_id"):
        raise HTTPException(status_code=403, detail="无权访问该分析报告")
    result = dict(row)
    result.pop("sample_shared", None)
    return result


@router.delete("/reports/{report_id}", dependencies=[Depends(require_permission("datasets:analyze"))])
def delete_report(report_id: int, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    _ensure_table(db)
    row = db.execute(text("SELECT organization_id FROM intelligent_data_analysis_reports WHERE id=:id"), {"id": report_id}).first()
    if not row:
        raise HTTPException(status_code=404, detail="分析报告不存在")
    if not is_admin(current_user) and row[0] != current_user.get("organization_id"):
        raise HTTPException(status_code=403, detail="无权删除该分析报告")
    db.execute(text("DELETE FROM intelligent_data_analysis_reports WHERE id=:id"), {"id": report_id})
    db.commit()
    return {"message": "分析报告已删除"}
