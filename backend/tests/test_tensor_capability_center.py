"""PR-10（AOG P1）：能力中心聚合端点契约。

/tensor/capability/deltas + /summary 是四中心之能力中心的后端面——
组织视角聚合 PR-5（#592）落的能力增量流水。门能红：
- 坏 category → 422；
- org 域隔离：他 org 的 delta 不可见；
- 覆盖率语义：succeeded 未入账不算 covered（闭环 C 真相不粉饰）；
- headline 解析可移植（sqlite CI 无 `->>'，Python 侧解析）。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, BACKEND / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def capability():
    """直接函数层调用（不起 HTTP），与 test_capability_delta.py 同风格。"""
    return _load("tp_tensor_capability", pathlib.Path("api") / "tensor_capability.py")


ENGINE = create_engine("sqlite:///:memory:")

SCHEMA = [
    "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT)",
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, organization_id INTEGER)",
    """CREATE TABLE tensor_dev_missions (
        id INTEGER PRIMARY KEY, organization_id INTEGER, repo TEXT, goal TEXT,
        status TEXT, result TEXT)""",
    """CREATE TABLE tensor_capability_deltas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER,
        mission_id INTEGER UNIQUE, category TEXT NOT NULL, summary TEXT NOT NULL,
        reason TEXT, artifacts TEXT DEFAULT '[]', evidence TEXT DEFAULT '{}',
        card_id INTEGER, reviewer TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
]

ADMIN = {"id": 9, "username": "cap-admin", "role": "ADMIN", "organization_id": 1}


@pytest.fixture()
def db():
    with ENGINE.begin() as conn:
        for ddl in SCHEMA:
            conn.execute(text(ddl))
        conn.execute(text("INSERT INTO organizations (id, name) VALUES (1, 'OrgA'), (2, 'OrgB')"))
        conn.execute(text(
            "INSERT INTO users (id, username, role, organization_id) VALUES (9, 'cap-admin', 'ADMIN', 1)"))
        # missions：m1/m2 succeeded（m2 未入账）、m3 failed（不该进覆盖分母）、
        # m4 他 org（不可见）
        for mid, org, repo, status, result in (
            (1, 1, "TensorLBM", "succeeded", json.dumps({"headline": "自由面回归绿"})),
            (2, 1, "TensorSPH", "succeeded", None),
            (3, 1, "TensorFEM", "failed", None),
            (4, 2, "TensorDG", "succeeded", None),
        ):
            conn.execute(text(
                "INSERT INTO tensor_dev_missions (id, organization_id, repo, goal, status, result) "
                "VALUES (:i, :o, :r, :g, :s, :x)"),
                {"i": mid, "o": org, "r": repo, "g": f"goal-m{mid}", "s": status, "x": result})
        # deltas：m1 新能力、m1' 不可能（UNIQUE）、m4 他 org
        conn.execute(text(
            "INSERT INTO tensor_capability_deltas (organization_id, mission_id, category, summary, reviewer) "
            "VALUES (1, 1, 'new_capability', 'LBM 自由面封闭回归', 'verifier'), "
            "       (2, 4, 'defect_fix', '他 org 项', 'x')"))
    session = sessionmaker(bind=ENGINE, future=True)()
    yield session
    session.close()
    with ENGINE.begin() as conn:
        for t in ("tensor_capability_deltas", "tensor_dev_missions", "users", "organizations"):
            conn.execute(text(f"DROP TABLE {t}"))


class TestDeltas:
    def test_list_joins_mission_and_parses_headline(self, db, capability):
        out = capability.list_capability_deltas(limit=10, db=db, user=ADMIN)
        assert out["total"] == 1, "org=1 只见自家 delta（m4 他 org 不可见）"
        item = out["items"][0]
        assert item["mission_repo"] == "TensorLBM"
        assert item["mission_title"] == "自由面回归绿", "headline 缺省时才回 goal"

    def test_bad_category_422(self, db, capability):
        with pytest.raises(Exception) as ei:
            capability.list_capability_deltas(limit=5, category="bogus", db=db, user=ADMIN)
        assert "422" in str(ei.value) or "category" in str(ei.value)

    def test_category_filter(self, db, capability):
        out = capability.list_capability_deltas(limit=5, category="new_capability", db=db, user=ADMIN)
        assert out["total"] == 1
        out2 = capability.list_capability_deltas(limit=5, category="defect_fix", db=db, user=ADMIN)
        assert out2["total"] == 0, "他 org 的 defect_fix 不得漏进本 org 视图"


class TestSummary:
    def test_coverage_counts_only_succeeded_booked(self, db, capability):
        s = capability.capability_summary(db=db, user=ADMIN)
        assert s["succeeded_missions"] == 2, "分母=本 org succeeded（m3 failed/m4 他 org 不算）"
        assert s["covered_succeeded"] == 1
        assert s["coverage_ratio"] == 0.5, "m2 succeeded 未入账——覆盖率必须显半开"
        assert s["by_category"]["new_capability"] == 1
        assert s["substantive_deltas"] == 1
        assert s["no_substantive_deltas"] == 0

    def test_summary_shape_pinned(self, db, capability):
        s = capability.capability_summary(db=db, user=ADMIN)
        assert set(s) == {"by_category", "total_deltas", "substantive_deltas",
                          "no_substantive_deltas", "succeeded_missions",
                          "covered_succeeded", "coverage_ratio"}
        assert set(s["by_category"]) == set(capability.CATEGORIES)
