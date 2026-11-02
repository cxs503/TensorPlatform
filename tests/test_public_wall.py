"""公共实况面回归：只读端点数据正确 + 全 GET 无写面。

运行：python -m pytest tests/test_public_wall.py（sqlite 自带夹具，
直调路由函数——不依赖 TestClient/httpx 版本组合）
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from api import public
from api.public import (marked_js, missions, overview, report_detail,
                        reports, wall)

# 私有 engine：backend.database.config.engine 是进程内共享的 :memory: 实例，
# 其它测试模块会在其中建同名但不同列数的表（例如 8 列的 organizations），
# 复用共享 engine 会让本模块在整包收集时报 schema 冲突 → collection ERROR。
engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
Session = sessionmaker(bind=engine, future=True)

from datetime import datetime, timedelta, timezone


def _iso(hours_ago: float) -> str:
    """相对 now 的时间戳：避免硬编码绝对日期在 24h 窗口断言上过期（时效炸弹）。"""
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS organizations (id INTEGER PRIMARY KEY, name TEXT)",
    "CREATE TABLE IF NOT EXISTS tensor_dev_missions (id INTEGER PRIMARY KEY,"
    " organization_id INTEGER, goal TEXT, repo TEXT, status TEXT, model TEXT,"
    " claimed_by TEXT, created_at TEXT, finished_at TEXT, heartbeat_at TEXT)",
    "CREATE TABLE IF NOT EXISTS tensor_reports (id INTEGER PRIMARY KEY,"
    " organization_id INTEGER, title TEXT, summary TEXT, tags TEXT,"
    " mission_id INTEGER, markdown TEXT, figures TEXT, status TEXT, created_at TEXT)",
)

# 备注：pytest 进程内 sqlite :memory: 由同一 engine 承载，其它测试模块可能已建过同名表，
# 故建表语句必须幂等（IF NOT EXISTS）并在装载夹具前清空，否则本模块在整包收集时会
# 以 "table organizations already exists" 直接 collection ERROR（历史缺陷）。
with engine.begin() as db:
    for ddl in _SCHEMA:
        db.execute(text(ddl))
    for table in ("tensor_reports", "tensor_dev_missions", "organizations"):
        db.execute(text(f"DELETE FROM {table}"))
    db.execute(text("INSERT INTO organizations VALUES (1,'single')"))
    db.execute(text(
        "INSERT INTO tensor_dev_missions VALUES "
        f"(160,1,'SI-2 目标多行','TensorPhysics','succeeded','qwen3.7-plus','w1',"
        f"'{_iso(4.0)}','{_iso(2.0)}',NULL),"
        f"(161,1,'running 目标','TensorLBM','running','glm-5.1','w2',"
        f"'{_iso(5.0)}',NULL,'{_iso(0.05)}'),"
        "(999,2,'他租户','X','failed',NULL,NULL,NULL,NULL,NULL)"))
    db.execute(text(
        "INSERT INTO tensor_reports VALUES "
        f"(5,1,'报告甲','摘要','[\"si\"]',160,'# 正文',"
        "'[{\"name\":\"f1\",\"caption\":\"图一\",\"png_base64\":\"aGk=\"}]',"
        f"'published','{_iso(1.9)}'),"
        f"(6,1,'草稿','s',NULL,NULL,'m','[]','draft','{_iso(1.8)}'),"
        f"(7,2,'他租户报告','s',NULL,NULL,'m','[]','published','{_iso(1.7)}')"))


def test_overview_counts_and_tenant_scope():
    with Session() as db:
        ov = overview(db=db)
    assert ov["status_counts"]["succeeded"] == 1      # m160
    assert ov["status_counts"].get("failed", 0) in (0, None)  # m999 他租户不计入
    assert len(ov["running"]) == 1 and ov["running"][0]["id"] == 161
    assert ov["latest_reports"][0]["id"] == 5         # draft/他租户不可见
    assert ov["reports_total"] == 1
    assert ov["succeeded_24h"] == 1


def test_missions_list_shape():
    with Session() as db:
        ms = missions(limit=50, db=db)
    ids = [m["id"] for m in ms]
    assert 999 not in ids and 161 in ids
    assert all("png_base64" not in m and "markdown" not in m for m in ms)
    assert len(ms[0]["goal"]) <= 300


def test_report_detail_and_404():
    with Session() as db:
        r = report_detail(report_id=5, db=db)
        assert r["title"] == "报告甲" and r["figures"][0]["caption"] == "图一"
        with pytest.raises(HTTPException) as ei:
            report_detail(report_id=6, db=db)   # draft
        assert ei.value.status_code == 404
        with pytest.raises(HTTPException):
            report_detail(report_id=7, db=db)   # 他租户


def test_wall_assets_served():
    w = wall()
    assert isinstance(w, FileResponse) and str(w.path).endswith("wall.html")
    j = marked_js()
    assert isinstance(j, FileResponse) and str(j.path).endswith("marked.min.js")


def test_public_surface_is_read_only():
    flat = set().union(*[route.methods or set() for route in public.router.routes])
    assert flat == {"GET"}  # 无写面
