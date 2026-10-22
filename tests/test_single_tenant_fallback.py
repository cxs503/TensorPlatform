"""单租户回退：无绑定用户在仅存一个组织时自动落位，不再 403。

背景：2.3.0 升级引入组织绑定后，历史单租户舰队（全部 agent 共用
一个无绑定服务账号）claim/read 全线 403（2026-10-22 事故，284 次）。
回退语义：无绑定+未指名 org+全库唯一组织 → 隐式落位（无第二租户
可泄漏）；多租户/空平台保持原语义。
运行：python -m pytest tests/test_single_tenant_fallback.py（sqlite 自带夹具）
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from api.domain.tensor_common import resolved_org

ENGINE = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    future=True,
)
Session = sessionmaker(bind=ENGINE, future=True)
with ENGINE.begin() as db:
    db.execute(text("CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT)"))
    db.execute(text(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT,"
        " role TEXT, organization_id INTEGER)"))

AGENT = {"id": 4, "username": "tensor_agent", "role": "VIEWER",
         "organization_id": None}


def seed(orgs):
    with ENGINE.begin() as db:
        db.execute(text("DELETE FROM organizations"))
        for oid, name in orgs:
            db.execute(text("INSERT INTO organizations (id, name) "
                            "VALUES (:i, :n)"), {"i": oid, "n": name})


def test_orgless_single_tenant_falls_back():
    seed([(1, "single")])  # 唯一组织 + 无绑定用户
    with Session() as db:
        assert resolved_org(None, dict(AGENT), db) == 1


def test_orgless_multi_tenant_still_strict():
    seed([(1, "single"), (2, "other")])  # 存在第二租户 → 保持严格
    with Session() as db:
        with pytest.raises(HTTPException) as ei:
            resolved_org(None, dict(AGENT), db)
        assert ei.value.status_code == 403


def test_bound_user_and_empty_platform_unaffected():
    with Session() as db:  # 已绑定用户直落自身组织
        assert resolved_org(None, {"role": "VIEWER", "organization_id": 1},
                            db) == 1
    seed([])  # 空平台：无组织可落 → 原语义
    with Session() as db:
        with pytest.raises(HTTPException) as ei:
            resolved_org(None, dict(AGENT), db)
        assert ei.value.status_code in (400, 403)


def test_admin_fallback_path_preserved():
    seed([(1, "single")])  # 原有 admin 回退不受影响
    with Session() as db:
        assert resolved_org(None, {"role": "ADMIN", "organization_id": None},
                            db) == 1
