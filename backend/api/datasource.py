"""Authenticated data-source connector API."""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime
from typing import Optional

from api.deps import require_auth, require_permission
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from services.datasource_service import get_datasource_manager

router = APIRouter(prefix="/datasources", tags=["数据源连接器"])


def _get_db_for_import():
    from database.config import get_db
    yield from get_db()


def _identity(current_user: dict) -> tuple[int, int]:
    return int(current_user.get("organization_id") or 0), int(current_user.get("id") or 0)


class AddMySQLReq(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=3306, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: str


class AddInfluxDBReq(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    host: str
    port: int = Field(default=8086, ge=1, le=65535)
    database: str
    username: Optional[str] = None
    password: Optional[str] = None


class AddOPCUAReq(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    endpoint: str
    username: Optional[str] = None
    password: Optional[str] = None


class AddTDengineReq(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=6041, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=255)
    username: str = Field(default="root", min_length=1, max_length=255)
    password: str = "taosdata"


class QueryReq(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)


class ImportReq(BaseModel):
    table: Optional[str] = None
    dataset_name: Optional[str] = Field(default=None, max_length=128)
    limit: int = Field(default=10000, ge=1, le=10000)


@router.post("/mysql", dependencies=[Depends(require_permission("datasets:connect"))])
def add_mysql_source(req: AddMySQLReq, current_user: dict = Depends(require_auth)):
    manager = get_datasource_manager()
    organization_id, user_id = _identity(current_user)
    source_id = manager.add_mysql_source(
        req.name.strip(), req.host.strip(), req.port, req.database.strip(),
        req.username.strip(), req.password, organization_id, user_id,
    )
    return {"source_id": source_id, "name": req.name, "type": "mysql", "status": "disconnected"}


@router.post("/influxdb", dependencies=[Depends(require_permission("datasets:connect"))])
def add_influxdb_source(req: AddInfluxDBReq, current_user: dict = Depends(require_auth)):
    manager = get_datasource_manager()
    organization_id, user_id = _identity(current_user)
    source_id = manager.add_influxdb_source(
        req.name.strip(), req.host.strip(), req.port, req.database.strip(),
        req.username, req.password, organization_id, user_id,
    )
    return {"source_id": source_id, "name": req.name, "type": "influxdb", "status": "disconnected"}


@router.post("/opcua", dependencies=[Depends(require_permission("datasets:connect"))])
def add_opcua_source(req: AddOPCUAReq, current_user: dict = Depends(require_auth)):
    manager = get_datasource_manager()
    organization_id, user_id = _identity(current_user)
    source_id = manager.add_opcua_source(
        req.name.strip(), req.endpoint.strip(), req.username, req.password,
        organization_id, user_id,
    )
    return {"source_id": source_id, "name": req.name, "type": "opcua", "status": "disconnected"}


@router.post("/tdengine", dependencies=[Depends(require_permission("datasets:connect"))])
def add_tdengine_source(req: AddTDengineReq, current_user: dict = Depends(require_auth)):
    manager = get_datasource_manager()
    organization_id, user_id = _identity(current_user)
    source_id = manager.add_tdengine_source(
        req.name.strip(), req.host.strip(), req.port, req.database.strip(),
        req.username.strip(), req.password, organization_id, user_id,
    )
    return {"source_id": source_id, "name": req.name, "type": "tdengine", "status": "disconnected"}


@router.get("")
def list_datasources(source_type: Optional[str] = None, current_user: dict = Depends(require_auth)):
    manager = get_datasource_manager()
    organization_id, _ = _identity(current_user)
    sources = manager.list_sources(organization_id, source_type)
    return {"total": len(sources), "items": sources}


@router.get("/types")
def get_datasource_types(current_user: dict = Depends(require_auth)):
    available = get_datasource_manager().driver_availability()
    return {"types": [
        {"id": "mysql", "name": "MySQL", "description": "关系型数据库",
         "available": available["mysql"], "fields": ["name", "host", "port", "database", "username", "password"]},
        {"id": "influxdb", "name": "InfluxDB", "description": "时序数据库",
         "available": available["influxdb"], "fields": ["name", "host", "port", "database", "username", "password"]},
        {"id": "opcua", "name": "OPC UA", "description": "工业设备协议",
         "available": available["opcua"], "fields": ["name", "endpoint", "username", "password"]},
        {"id": "tdengine", "name": "TDengine", "description": "工业时序数据库（REST）",
         "available": available["tdengine"], "fields": ["name", "host", "port", "database", "username", "password"]},
    ]}


@router.get("/{source_id}")
def get_datasource(source_id: str, current_user: dict = Depends(require_auth)):
    organization_id, _ = _identity(current_user)
    source = get_datasource_manager().get_config(source_id, organization_id)
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return source


@router.post("/{source_id}/connect", dependencies=[Depends(require_permission("datasets:connect"))])
def connect_datasource(source_id: str, current_user: dict = Depends(require_auth)):
    manager = get_datasource_manager()
    organization_id, _ = _identity(current_user)
    try:
        success = manager.connect(source_id, organization_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if success:
        return {"ok": True, "status": "connected", "message": "连接成功"}
    diagnosis = manager.diagnose_connection(source_id, organization_id)
    raise HTTPException(status_code=400, detail=diagnosis.get("message") or "连接失败")


@router.post("/{source_id}/disconnect", dependencies=[Depends(require_permission("datasets:connect"))])
def disconnect_datasource(source_id: str, current_user: dict = Depends(require_auth)):
    organization_id, _ = _identity(current_user)
    try:
        get_datasource_manager().disconnect(source_id, organization_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return {"ok": True, "status": "disconnected", "message": "已断开连接"}


@router.post("/{source_id}/test", dependencies=[Depends(require_permission("datasets:connect"))])
def test_datasource(source_id: str, current_user: dict = Depends(require_auth)):
    organization_id, _ = _identity(current_user)
    return get_datasource_manager().diagnose_connection(source_id, organization_id)


@router.post("/{source_id}/query", dependencies=[Depends(require_permission("datasets:connect"))])
def query_datasource(source_id: str, req: QueryReq, current_user: dict = Depends(require_auth)):
    organization_id, _ = _identity(current_user)
    try:
        results = get_datasource_manager().query(source_id, req.sql, organization_id)
        return {"total": len(results), "items": results}
    except KeyError:
        raise HTTPException(status_code=404, detail="数据源不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{source_id}/tables")
def get_tables(source_id: str, current_user: dict = Depends(require_auth)):
    organization_id, _ = _identity(current_user)
    try:
        return {"items": get_datasource_manager().get_tables(source_id, organization_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="数据源不存在")
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{source_id}", dependencies=[Depends(require_permission("datasets:connect"))])
def delete_datasource(source_id: str, current_user: dict = Depends(require_auth)):
    organization_id, _ = _identity(current_user)
    if get_datasource_manager().delete_source(source_id, organization_id):
        return {"message": "删除成功"}
    raise HTTPException(status_code=404, detail="数据源不存在")


def _infer_dtype(values) -> str:
    present = [value for value in values if value is not None]
    if not present:
        return "object"
    if all(isinstance(value, bool) for value in present):
        return "bool"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in present):
        return "int64"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return "float64"
    if all(isinstance(value, (datetime,)) for value in present):
        return "datetime64[ns]"
    return "object"


@router.post("/{source_id}/import", dependencies=[Depends(require_permission("datasets:connect")), Depends(require_permission("datasets:upload"))])
def import_datasource(
    source_id: str,
    req: ImportReq,
    db_session=Depends(_get_db_for_import),
    current_user: dict = Depends(require_auth),
):
    """Import a selected MySQL table into an organization-scoped platform dataset."""
    manager = get_datasource_manager()
    organization_id, _ = _identity(current_user)
    source = manager.get_config(source_id, organization_id)
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if source["type"] not in {"mysql", "tdengine"}:
        raise HTTPException(status_code=400, detail="当前版本支持从 MySQL 或 TDengine 选择表导入")

    try:
        tables = manager.get_tables(source_id, organization_id)
        table_names = [item.get("name") for item in tables]
        table_name = req.table or (table_names[0] if table_names else None)
        if not table_name:
            raise HTTPException(status_code=400, detail="数据源中没有可导入的表或超级表")
        if table_name not in table_names:
            raise HTTPException(status_code=400, detail="选择的表不存在或当前账号无权访问")
        quoted_table = table_name.replace("`", "``")
        rows = manager.query(source_id, f"SELECT * FROM `{quoted_table}` LIMIT {req.limit}", organization_id)
    except HTTPException:
        raise
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not rows:
        raise HTTPException(status_code=400, detail="所选表没有可导入的数据")

    columns = list(rows[0].keys())
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(column) for column in columns])
    csv_bytes = buffer.getvalue().encode("utf-8-sig")

    from models.core import Dataset
    dataset_name = (req.dataset_name or f"{source['name']}-{table_name}").strip()[:128]
    columns_info = [
        {"name": column, "dtype": _infer_dtype([row.get(column) for row in rows]), "derived": False}
        for column in columns
    ]
    dataset = Dataset(
        name=dataset_name,
        description=f"从 {source['type']} 数据源 {source['name']} 的表 {table_name} 导入",
        row_count=len(rows), size_bytes=len(csv_bytes), stage="raw",
        columns_info=columns_info, organization_id=organization_id,
    )
    db_session.add(dataset)
    db_session.commit()
    db_session.refresh(dataset)

    upload_dir = "/app/uploads/datasets"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = f"{upload_dir}/{dataset.id}_datasource.csv"
    try:
        with open(file_path, "wb") as file_handle:
            file_handle.write(csv_bytes)
        dataset.file_path = file_path
        db_session.commit()
        db_session.refresh(dataset)
    except Exception:
        db_session.rollback()
        try:
            db_session.delete(dataset)
            db_session.commit()
        finally:
            if os.path.exists(file_path):
                os.unlink(file_path)
        raise HTTPException(status_code=500, detail="写入平台数据集文件失败")

    return {
        "dataset_id": dataset.id, "dataset_name": dataset.name,
        "source_table": table_name, "rows": len(rows), "columns": len(columns),
        "column_names": columns, "message": f"导入成功：{len(rows)} 行 × {len(columns)} 列",
    }
