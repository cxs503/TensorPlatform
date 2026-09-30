"""
备份恢复 API
"""
import io
import json
import tarfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.config import get_db
from services.backup_service import BackupType, get_backup_service

router = APIRouter(prefix="/backup", tags=["备份恢复"])

BACKUP_DIR = Path("/app/uploads/backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_ROOT = Path("/app/uploads")


def _jsonable(v):
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    return v


@router.post("/run")
def run_full_backup(db: Session = Depends(get_db)):
    """真实备份:导出全部用户表数据(db.json) + 打包 uploads(数据集/模型/runs)。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        tables = [r[0] for r in db.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        ).fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取表清单失败: {e}")

    dump, meta_tables = {}, {}
    for t in tables:
        try:
            rows = db.execute(text(f'SELECT * FROM "{t}"')).fetchall()
            cols = list(rows[0]._mapping.keys()) if rows else []
            data = [{c: _jsonable(r._mapping[c]) for c in cols} for r in rows]
            dump[t] = data
            meta_tables[t] = len(data)
        except Exception as e:
            meta_tables[t] = f"error: {e}"

    out = BACKUP_DIR / f"backup_{ts}.tar.gz"
    try:
        with tarfile.open(out, "w:gz") as tar:
            payload = json.dumps({"meta": {"created_at": datetime.now().isoformat(), "tables": meta_tables},
                                  "tables": dump}, ensure_ascii=False, default=str).encode("utf-8")
            info = tarfile.TarInfo(name="db.json")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
            # 打包 uploads,排除 backups 目录(避免递归)
            for p in UPLOADS_ROOT.rglob("*"):
                if "backups" in p.relative_to(UPLOADS_ROOT).parts:
                    continue
                if p.is_file():
                    tar.add(p, arcname=str(p.relative_to(UPLOADS_ROOT.parent)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打包失败: {e}")

    size = out.stat().st_size
    return {
        "backup_file": out.name, "size_mb": round(size / 1024 / 1024, 2),
        "tables": meta_tables, "created_at": datetime.now().isoformat(),
    }


@router.get("/files")
def list_backup_files():
    """列出真实备份文件。"""
    items = []
    for f in sorted(BACKUP_DIR.glob("backup_*.tar.gz"), reverse=True):
        st = f.stat()
        items.append({"name": f.name, "size_mb": round(st.st_size / 1024 / 1024, 2),
                      "created_at": datetime.fromtimestamp(st.st_mtime).isoformat()})
    return {"total": len(items), "items": items}


@router.get("/download/{filename}")
def download_backup_file(filename: str):
    """下载备份文件。"""
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    f = BACKUP_DIR / filename
    if not f.exists():
        raise HTTPException(status_code=404, detail="备份文件不存在")
    return StreamingResponse(open(f, "rb"), media_type="application/gzip",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/restore")
def restore_backup(req: dict, db: Session = Depends(get_db)):
    """
    从备份恢复 DB 数据(安全模式)。

    - 仅做 INSERT ... ON CONFLICT (id) DO NOTHING,绝不 DROP/TRUNCATE。
    - 不会改动 schema(由 app 启动时 create_all 管理)。
    - 不会自动覆盖 uploads 文件(避免破坏在线数据集/模型)。
    - 每张表独立 try/except,单表出错不中断整体恢复。
    """
    filename = (req or {}).get("filename")
    confirm = (req or {}).get("confirm")

    # 1. 必须显式确认(恢复是破坏性操作)
    if not confirm:
        raise HTTPException(status_code=400, detail="恢复是破坏性操作,需显式传 confirm=true")
    if not filename or "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")

    # 2. 定位备份文件
    archive = BACKUP_DIR / filename
    if not archive.exists():
        raise HTTPException(status_code=404, detail="备份文件不存在")

    # 3. 仅从 tar 中读取 db.json(不落盘解压 uploads)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            member = tar.getmember("db.json")
            raw = tar.extractfile(member).read()
        data = json.loads(raw.decode("utf-8"))
    except KeyError:
        raise HTTPException(status_code=400, detail="备份文件内缺少 db.json")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取备份失败: {e}")

    tables_payload = data.get("tables", {}) or {}

    # 4. 逐表安全恢复
    restored = {}
    errors = []

    for table_name, rows in tables_payload.items():
        if not isinstance(rows, list):
            continue
        try:
            # 取该表现在真实的列(只恢复 DB 里存在的列)
            col_rows = db.execute(
                text("SELECT column_name FROM information_schema.columns "
                     "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"),
                {"t": table_name},
            ).fetchall()
            if not col_rows:
                restored[table_name] = "skipped: table not in schema"
                continue
            db_cols = [r[0] for r in col_rows]

            # 是否有 id 主键(决定能否用 ON CONFLICT DO NOTHING)
            has_id = "id" in db_cols

            inserted = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                cols = [c for c in db_cols if c in row]
                if not cols:
                    continue
                placeholders = ", ".join(f":{c}" for c in cols)
                col_list = ", ".join(f'"{c}"' for c in cols)
                conflict = " ON CONFLICT (id) DO NOTHING" if has_id else ""
                sql = f'INSERT INTO "{table_name}" ({col_list}) VALUES ({placeholders}){conflict}'
                params = {c: row[c] for c in cols}
                result = db.execute(text(sql), params)
                if has_id:
                    inserted += result.rowcount if result.rowcount and result.rowcount > 0 else 0
                else:
                    inserted += 1

            restored[table_name] = f"inserted:{inserted}" if has_id else f"inserted_no_conflict:{inserted}"
        except Exception as e:
            db.rollback()
            errors.append({"table": table_name, "error": str(e)})
            restored[table_name] = f"error: {e}"

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        errors.append({"table": "__commit__", "error": str(e)})

    return {
        "restored_tables": restored,
        "errors": errors,
        "filename": filename,
        "confirm_required_was": True,
        "note": (
            "仅恢复数据库表数据(INSERT-OR-IGNORE,不改 schema)。"
            "备份内 uploads/* 文件未自动覆盖(防止破坏在线数据集/模型);"
            "如需取回文件,请通过 GET /backup/download/{filename} 下载后手动解压。"
        ),
    }


# ========== 备份管理 ==========


@router.get("/backups")
def list_backups(target_type: str = None, target_id: str = None, status: str = None):
    """列出备份"""
    service = get_backup_service()
    return service.list_backups(target_type, target_id, status)


@router.post("/backups")
def create_backup(req: dict):
    """创建备份"""
    service = get_backup_service()
    backup = service.create_backup(
        name=req.get("name"),
        target_type=req.get("target_type"),
        target_id=req.get("target_id"),
        backup_type=req.get("backup_type", BackupType.FULL),
        retention_days=req.get("retention_days", 30),
    )
    return {
        "id": backup.id,
        "name": backup.name,
        "target_type": backup.target_type,
        "target_id": backup.target_id,
        "backup_type": backup.backup_type,
        "status": backup.status,
        "size_mb": backup.size_mb,
        "created_at": backup.created_at,
    }


@router.get("/backups/{backup_id}")
def get_backup(backup_id: str):
    """获取备份"""
    service = get_backup_service()
    backup = service.get_backup(backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="备份不存在")
    return {
        "id": backup.id,
        "name": backup.name,
        "target_type": backup.target_type,
        "target_id": backup.target_id,
        "backup_type": backup.backup_type,
        "size_mb": backup.size_mb,
        "status": backup.status,
        "created_at": backup.created_at,
        "completed_at": backup.completed_at,
        "checksum": backup.checksum,
    }


@router.delete("/backups/{backup_id}")
def delete_backup(backup_id: str):
    """删除备份"""
    service = get_backup_service()
    if not service.delete_backup(backup_id):
        raise HTTPException(status_code=404, detail="备份不存在")
    return {"message": "删除成功"}


# ========== 恢复管理 ==========


@router.get("/restores")
def list_restores(backup_id: str = None, status: str = None):
    """列出恢复任务"""
    service = get_backup_service()
    return service.list_restores(backup_id, status)


@router.post("/restores")
def create_restore(req: dict):
    """创建恢复任务"""
    service = get_backup_service()
    restore = service.create_restore(
        backup_id=req.get("backup_id"),
        target_type=req.get("target_type"),
        target_id=req.get("target_id"),
    )
    if not restore:
        raise HTTPException(status_code=400, detail="备份不存在或不可用")
    return {
        "id": restore.id,
        "backup_id": restore.backup_id,
        "target_type": restore.target_type,
        "target_id": restore.target_id,
        "status": restore.status,
        "progress": restore.progress,
    }


@router.get("/restores/{restore_id}")
def get_restore(restore_id: str):
    """获取恢复任务"""
    service = get_backup_service()
    restore = service.get_restore(restore_id)
    if not restore:
        raise HTTPException(status_code=404, detail="恢复任务不存在")
    return {
        "id": restore.id,
        "backup_id": restore.backup_id,
        "target_type": restore.target_type,
        "target_id": restore.target_id,
        "status": restore.status,
        "progress": restore.progress,
        "created_at": restore.created_at,
        "completed_at": restore.completed_at,
        "error_message": restore.error_message,
    }


# ========== 备份计划 ==========


@router.get("/schedules")
def list_schedules(enabled: bool = None):
    """列出备份计划"""
    service = get_backup_service()
    return service.list_schedules(enabled)


@router.post("/schedules")
def create_schedule(req: dict):
    """创建备份计划"""
    service = get_backup_service()
    schedule = service.create_schedule(
        name=req.get("name"),
        target_type=req.get("target_type"),
        target_id=req.get("target_id"),
        backup_type=req.get("backup_type", BackupType.FULL),
        schedule=req.get("schedule"),
        retention_days=req.get("retention_days", 30),
    )
    return {
        "id": schedule.id,
        "name": schedule.name,
        "target_type": schedule.target_type,
        "target_id": schedule.target_id,
        "schedule": schedule.schedule,
        "enabled": schedule.enabled,
    }


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: str, req: dict):
    """更新备份计划"""
    service = get_backup_service()
    schedule = service.update_schedule(
        schedule_id, enabled=req.get("enabled"), schedule=req.get("schedule")
    )
    if not schedule:
        raise HTTPException(status_code=404, detail="备份计划不存在")
    return {"message": "更新成功"}


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    """删除备份计划"""
    service = get_backup_service()
    if not service.delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="备份计划不存在")
    return {"message": "删除成功"}


# ========== 备份策略 ==========


@router.get("/policies")
def list_policies():
    """列出备份策略"""
    service = get_backup_service()
    return service.list_policies()


@router.post("/policies")
def create_policy(req: dict):
    """创建备份策略"""
    service = get_backup_service()
    policy = service.create_policy(
        name=req.get("name"),
        default_backup_type=req.get("default_backup_type", BackupType.FULL),
        default_retention_days=req.get("default_retention_days", 30),
        compression_enabled=req.get("compression_enabled", True),
        encryption_enabled=req.get("encryption_enabled", False),
    )
    return {
        "id": policy.id,
        "name": policy.name,
        "default_backup_type": policy.default_backup_type,
        "compression_enabled": policy.compression_enabled,
        "encryption_enabled": policy.encryption_enabled,
    }


# ========== 统计 ==========


@router.get("/stats")
def get_backup_stats():
    """获取备份统计"""
    service = get_backup_service()
    return service.get_backup_stats()
