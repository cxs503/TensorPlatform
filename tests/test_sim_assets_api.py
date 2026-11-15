"""仿真成果资产 API 的机器判据（FEAT-SIM-DATAHOUSE 子件1 读取面）。

守住的命题（每条断言对应一条，用内联假 fetch，不依赖数据库/网络）：

- P1 过滤口径：repo / case(→case_key) / state / mission_id 都必须落到 SQL 的
  WHERE 子句并以**绑定参数**传递（外部字符串不得进 SQL 拼接）。
- P2 参数校验：非法 state、非法 mission_id、page<1、page_size 越界、
  未知排序字段 → 422 而不是静默返回全表。
- P3 分页算得对：total/pages/offset 一致，page_size 上限 200。
- P4 别名口径：``case`` 与 ``case_key`` 是同一个过滤位，不能出现两个不同列。
- P5 summary 聚合成一行总账：by_repo 与 totals 同口径（events/bytes/files +
  captured/partial/missed 分档），且未登记的 repo 显示为 <unknown>。
- P6 路由可达性：router 上确实挂了 GET ''、GET '/summary'、GET '/{event_id}'，
  且列表端点前缀是 /assets/sim（=线上 /api/v1/assets/sim）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"


def _load(name: str, rel: str):
    path = BACKEND / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def reg():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    return _load("sim_asset_registry_t", "services/sim_asset_registry.py")


@pytest.fixture(scope="module")
def api():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    return _load("sim_assets_api_t", "api/sim_assets.py")


# ── P1/P4 过滤与绑定参数 ─────────────────────────────────────────────
def test_filters_bind_parameters_not_concat(reg):
    where, params = reg.build_filters(repo="TensorLBM", case="case-a",
                                      state="captured", mission_id="21577")
    assert "repo = :repo" in where and "case_key = :case_key" in where
    assert "state = :state" in where and "mission_id = :mission_id" in where
    assert params["repo"] == "TensorLBM"
    assert params["case_key"] == "case-a"          # case → case_key 单一口径
    assert params["mission_id"] == 21577
    assert "case-a" not in where                    # 值不进 SQL 文本


def test_injection_attempt_stays_in_params(reg):
    evil = "x'; DROP TABLE simulation_assets; --"
    where, params = reg.build_filters(repo=evil)
    assert where == " WHERE repo = :repo"
    assert params["repo"] == evil


def test_empty_filters_are_ignored(reg):
    where, params = reg.build_filters(repo=None, case="", state=None, mission_id=None)
    assert where == "" and params == {}


# ── P2 参数校验 ────────────────────────────────────────────────────
@pytest.mark.parametrize("kwargs", [
    {"state": "everything"},
    {"mission_id": "abc"},
])
def test_invalid_filters_raise_query_error(reg, kwargs):
    with pytest.raises(reg.QueryError):
        reg.build_filters(**kwargs)


@pytest.mark.parametrize("page,page_size,msg", [(0, 20, "page"), (1, 0, "page_size"),
                                                (1, 201, "page_size")])
def test_invalid_pagination_raises(reg, page, page_size, msg):
    with pytest.raises(reg.QueryError) as ei:
        reg.build_list_sql(filters={}, page=page, page_size=page_size)
    assert msg in str(ei.value)


def test_unknown_sort_column_raises(reg):
    with pytest.raises(reg.QueryError):
        reg.build_list_sql(filters={}, page=1, page_size=10, sort="params; DROP TABLE x")


# ── P3 分页口径 ────────────────────────────────────────────────────
def test_pagination_limits_and_offset(reg):
    sql, params = reg.build_list_sql(filters={"repo": "TensorLBM"}, page=3, page_size=25)
    assert sql.endswith("LIMIT 25 OFFSET 50")
    assert params == {"repo": "TensorLBM"}
    page = reg.paginate([{"id": 1}], total=51, page=3, page_size=25)
    assert page["pages"] == 3 and page["total"] == 51 and page["page"] == 3


def test_sort_direction_whitelisted(reg):
    desc, _ = reg.build_list_sql(filters={}, page=1, page_size=5, sort="bytes", order="desc")
    asc, _ = reg.build_list_sql(filters={}, page=1, page_size=5, sort="bytes", order="asc")
    assert "ORDER BY bytes_total DESC" in desc
    assert "ORDER BY bytes_total ASC" in asc
    weird, _ = reg.build_list_sql(filters={}, page=1, page_size=5, sort="id", order="; DROP")
    assert "ORDER BY id DESC" in weird           # 不认识的方向退回默认，不进 SQL


# ── P5 summary 聚合 ────────────────────────────────────────────────
def test_summary_aggregation(api, monkeypatch):
    rows = [("TensorLBM", "captured", 2, 4096, 20),
            ("TensorLBM", "partial", 1, 1024, 5),
            ("TensorFEM", "missed", 3, 0, 0),
            ("<unknown>", "captured", 1, 10, 1)]
    monkeypatch.setattr(api, "_fetch", lambda sql, params=None: rows)
    out = api.simulation_asset_summary()
    assert out["totals"]["events"] == 7
    assert out["totals"]["bytes"] == 5130
    assert out["totals"]["captured"] == 3 and out["totals"]["missed"] == 3
    by_repo = {r["repo"]: r for r in out["by_repo"]}
    assert by_repo["TensorLBM"]["events"] == 3
    assert out["by_repo"][0]["bytes"] >= out["by_repo"][-1]["bytes"]   # 按字节降序


# ── P6 端点存在性与路由前缀 ─────────────────────────────────────────
def test_router_paths(api):
    paths = {r.path for r in api.router.routes}
    assert paths == {"/assets/sim", "/assets/sim/summary", "/assets/sim/{event_id}"}
    assert api.router.prefix == "/assets/sim"


def test_list_endpoint_shapes_rows_and_maps_errors(api, monkeypatch):
    captured = {}

    def fake_fetch(sql, params=None):
        captured.setdefault("sql", sql)
        if sql.startswith("SELECT count(*)"):
            return [(1,)]
        return [(1, "m21577-abc", 21577, "TensorLBM", "case", "http://r/1",
                 "/root/sim-assets/m21577-abc", 2048, 3, "captured", None,
                 "/srv/tensor/agent/work/m21577", "2026-11-15T08:00:00+00",
                 2, 1, 0, 0)]

    monkeypatch.setattr(api, "_fetch", fake_fetch)
    out = api.list_simulation_assets(page=1, page_size=20, repo="TensorLBM",
                                     case=None, state=None, mission_id=None,
                                     sort="captured_at", order="desc")
    assert out["total"] == 1 and out["pages"] == 1
    item = out["items"][0]
    assert item["event_id"] == "m21577-abc" and item["field_count"] == 2
    assert item["captured_at"] == "2026-11-15T08:00:00+00"
    assert "FROM simulation_assets WHERE repo = :repo" in captured["sql"]


def test_list_endpoint_422_on_bad_state(api):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        api.list_simulation_assets(page=1, page_size=20, repo=None, case=None,
                                   state="bogus", mission_id=None,
                                   sort="captured_at", order="desc")
    assert ei.value.status_code == 422


def test_detail_endpoint_404(api, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(api, "_fetch", lambda sql, params=None: [])
    with pytest.raises(HTTPException) as ei:
        api.get_simulation_asset("m0-none")
    assert ei.value.status_code == 404