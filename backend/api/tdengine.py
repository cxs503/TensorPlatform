"""
TDengine时序数据库API
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

# TDengine服务
from services.tdengine_service import get_tdengine_service

router = APIRouter(prefix="/tdengine", tags=["时序数据库"])


class CreateDatabaseReq(BaseModel):
    database: str
    keep_days: int = 365


class CreateTableReq(BaseModel):
    table_name: str
    super_table: Optional[str] = None
    tags: Dict[str, Any] = {}


class InsertDataReq(BaseModel):
    table_name: str
    timestamps: List[str]
    values: List[float]


class QueryReq(BaseModel):
    sql: str


@router.post("/databases")
def create_database(req: CreateDatabaseReq):
    """创建数据库"""
    td = get_tdengine_service()
    success = td.create_database(req.database, req.keep_days)
    if success:
        return {"message": f"Database {req.database} created"}
    raise HTTPException(status_code=500, detail="Failed to create database")


@router.post("/tables")
def create_table(req: CreateTableReq):
    """创建表"""
    td = get_tdengine_service()

    if req.super_table:
        # 创建子表
        success = td.create_sub_table(req.table_name, req.super_table, req.tags)
    else:
        # 创建超级表
        # 需要tags schema
        tags_schema = {
            k: "NCHAR(50)" if isinstance(v, str) else "INT" for k, v in req.tags.items()
        }
        success = td.create_super_table(req.table_name, tags_schema)

    if success:
        return {"message": f"Table {req.table_name} created"}
    raise HTTPException(status_code=500, detail="Failed to create table")


@router.post("/data")
def insert_data(req: InsertDataReq):
    """插入时序数据"""
    td = get_tdengine_service()

    # 转换时间戳
    timestamps = [datetime.fromisoformat(ts) for ts in req.timestamps]

    success = td.insert_data(req.table_name, timestamps, req.values)
    if success:
        return {"message": f"Inserted {len(req.values)} records"}
    raise HTTPException(status_code=500, detail="Failed to insert data")


@router.post("/query")
def execute_query(req: QueryReq):
    """执行SQL查询"""
    td = get_tdengine_service()
    try:
        results = td.query(req.sql)
        return {"total": len(results), "items": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables")
def list_tables(super_table: Optional[str] = None):
    """列出所有表"""
    td = get_tdengine_service()
    tables = td.list_tables(super_table)
    return {"total": len(tables), "items": tables}


@router.get("/tables/{table_name}/latest")
def get_latest(table_name: str):
    """获取最新数据"""
    td = get_tdengine_service()
    data = td.get_latest(table_name)
    if data is None:
        raise HTTPException(status_code=404, detail="No data found")
    return data


@router.get("/tables/{table_name}/aggregate")
def get_aggregate(
    table_name: str,
    start_time: str = Query(..., description="开始时间"),
    end_time: str = Query(..., description="结束时间"),
    interval: str = Query("1m", description="聚合间隔"),
    agg_func: str = Query("avg", description="聚合函数"),
):
    """获取聚合数据"""
    td = get_tdengine_service()
    results = td.get_aggregate(table_name, start_time, end_time, interval, agg_func)
    return {"total": len(results), "items": results}


@router.delete("/tables/{table_name}")
def delete_table(table_name: str):
    """删除表"""
    td = get_tdengine_service()
    success = td.delete_table(table_name)
    if success:
        return {"message": f"Table {table_name} deleted"}
    raise HTTPException(status_code=500, detail="Failed to delete table")


@router.get("/status")
def get_status():
    """获取TDengine连接状态"""
    td = get_tdengine_service()
    return {
        "connected": td._conn is not None,
        "host": td.host,
        "port": td.port,
        "database": td.database,
    }
