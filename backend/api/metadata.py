"""
元数据管理 API
"""
from fastapi import APIRouter

router = APIRouter(prefix="/metadata", tags=["元数据管理"])


@router.get("/tables")
def get_tables():
    return {"items": []}


@router.get("/tables/{table_id}")
def get_table(table_id: int):
    return {"id": table_id, "name": "table"}


@router.get("/columns/{table_id}")
def get_columns(table_id: int):
    return {"items": []}


@router.get("/search")
def search():
    return {"items": []}
