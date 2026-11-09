"""API 层测试：三条门端点接线（sqlite 夹具 + 依赖覆盖，不依赖 PG 驱动）。

覆盖：``/proposals/gate/stats``（四态+KPI）、``/gate/ledger``（可过滤）、
``/gate/evaluate``（accepted/duplicate/quarantined 三态）、``/gate/adopt``、
``/gate/coverage``（真库 campaign_nodes 名 ↔ 采纳提案）、``/gate/config``。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 仓库根须在 backend 之前

from proposal_gate_support import v2_payload  # noqa: E402

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from api import proposal_gate as pg
    from api.deps import require_auth
    from database.config import get_db

    # 合成配置（白名单换 foo/bar/widget，隔离区/事件落 tmp）→ 经环境变量注入，
    # 被验的仍是同一套 config 装载与判定代码路径。
    base_cfg = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "proposal_gate.json").read_text(
            encoding="utf-8"
        )
    )
    base_cfg["repo_whitelist"] = ["foo", "bar", "widget"]
    base_cfg["compat"]["v1_defaults"]["repo"] = "bar"
    base_cfg["ledger"]["quarantine_dir"] = str(tmp_path / "quarantine")
    base_cfg["ledger"]["events_file"] = str(tmp_path / "events.jsonl")
    cfg_path = tmp_path / "proposal_gate.json"
    cfg_path.write_text(json.dumps(base_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("PROPOSAL_GATE_CONFIG", str(cfg_path))
    pg._STORE_CACHE.clear()

    engine = create_engine(f"sqlite:///{tmp_path / 'api.sqlite'}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE campaign_nodes (id INTEGER PRIMARY KEY, name TEXT NOT NULL, metadata TEXT)")
        )
        conn.execute(
            text("INSERT INTO campaign_nodes (id, name, metadata) VALUES (:i, :n, :m)"),
            {
                "i": 1,
                "n": v2_payload()["title"],
                "m": json.dumps({"goal": v2_payload()["goal"]["objective"]}, ensure_ascii=False),
            },
        )
        conn.execute(
            text("INSERT INTO campaign_nodes (id, name, metadata) VALUES (:i, :n, :m)"),
            {"i": 2, "n": "widget 报表延迟归因", "m": json.dumps({"goal": "把 widget 报表延迟拆到各阶段"})},
        )
    Session = sessionmaker(bind=engine, future=True)

    def _db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(pg.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_auth] = lambda: {"username": "tester", "role": "admin"}
    client = TestClient(app)
    yield client
    pg._STORE_CACHE.clear()
    engine.dispose()


def test_config_endpoint_exposes_thresholds_from_file(api):
    body = api.get("/api/v1/proposals/gate/config").json()
    assert body["thresholds"]["dup_threshold"] > body["thresholds"]["review_threshold"] > 0
    assert body["repo_whitelist_size"] >= 1
    assert body["kpi_keys"]["dup_rejected"]
    assert body["states"]["duplicate"] == "dedup_rejected"


def test_stats_endpoint_starts_empty_with_four_states(api):
    body = api.get("/api/v1/proposals/gate/stats").json()
    counts = body["stats"]["counts"]
    assert set(counts) == {"proposal", "adopted", "dedup_rejected", "quarantined"}
    assert body["stats"]["total"] == 0
    keys = [k["metric_key"] for k in body["stats"]["kpi"]]
    assert "guide.proposal_dup_rejected" in keys
    assert "SELECT" in body["sql_trace"].upper()


def test_evaluate_adopt_coverage_roundtrip(api):
    payload = v2_payload(mission_id=9001)
    res = api.post(
        "/api/v1/proposals/gate/evaluate",
        json={"mission_id": 9001, "plan_id": 5, "proposal": payload},
    )
    assert res.status_code == 200, res.text
    result = res.json()["result"]
    # 与真库节点名撞车（节点 1 的 name/goal 就是同一份提案）→ 去重门拦截
    assert result["verdict"] in ("duplicate", "near_duplicate"), result
    assert result["dup_of"] == "node:1"

    # 第二种：完全不同主题 → accepted
    other = v2_payload(
        title="bar 报表延迟归因",
        repo="bar",
        objective="把 bar 报表的端到端延迟拆到各阶段并定位阻滞点",
        criteria=("P95 延迟 ≤ 2.5 秒", "归因覆盖率 ≥ 0.90", "误归因率 ≤ 0.10"),
        deps=("链-a",),
        mission_id=9002,
    )
    res2 = api.post(
        "/api/v1/proposals/gate/evaluate",
        json={"mission_id": 9002, "plan_id": 5, "proposal": other},
    )
    result2 = res2.json()["result"]
    assert result2["verdict"] == "accepted", result2

    adopt = api.post(
        "/api/v1/proposals/gate/adopt",
        json={"mission_id": 9002, "sha256": result2["sha256"], "plan_id": 5},
    )
    assert adopt.status_code == 200, adopt.text
    assert adopt.json()["row"]["state"] == "adopted"

    cov = api.get("/api/v1/proposals/gate/coverage").json()["coverage"]
    assert cov["adopted"] == 1
    assert cov["nodes_indexed"] == 2
    assert cov["coverage_rate"] in (0.0, 1.0)

    stats = api.get("/api/v1/proposals/gate/stats").json()["stats"]
    assert stats["counts"]["adopted"] == 1
    assert stats["total"] >= 2


def test_quarantine_endpoint_path(api):
    bad = v2_payload(mission_id=9101, criteria=("效果好", "更稳", "更省"))
    res = api.post(
        "/api/v1/proposals/gate/evaluate",
        json={"mission_id": 9101, "proposal": bad, "with_nodes": False},
    )
    result = res.json()["result"]
    assert result["verdict"] == "quarantined"
    assert result["contract_ok"] is False
    assert result["quarantine_path"]
    ledger = api.get("/api/v1/proposals/gate/ledger?state=quarantined").json()
    assert ledger["count"] == 1
    assert "quarantined" in ledger["sql_trace"]


def test_adopt_unknown_row_is_404(api):
    res = api.post(
        "/api/v1/proposals/gate/adopt",
        json={"mission_id": 999999, "sha256": "0" * 64},
    )
    assert res.status_code == 404