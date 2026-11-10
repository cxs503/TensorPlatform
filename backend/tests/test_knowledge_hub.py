"""知识专栏（FEAT-TK-ASSET-OPS）后端契约围栏。

判据（机器可裁）
----------------
1) **单一契约源**：所有知识数字经 KE API（``KE_API_BASE``）取；引擎离线时每个
   端点返回 ``available: false`` + ``reason``，不抛 500、不返回任何编造数字。
2) **draft 不泄正文**：未发布对象的 statement/quote/snippet/doc 由服务端剥离并打
   ``content_hidden``；已发布对象保留正文。渲染层隐藏不算数（API 仍会泄）。
3) **证据直读通道**：路径围栏（不能逃出 TK 仓库）+ 扩展名白名单 + 体积预算；
   命中窗口行号正确；免鉴权判定落在 **middleware/auth_guard** 层且只放行安全方法。
4) **真数据**：KE 引擎在 8021 活着时，/overview 的对象数、领域分布、漏斗口径与
   /query/stats 逐项对齐（不是自引用：一边是被测端点，一边是引擎原始统计）。

运行：
    cd backend && python -m pytest tests/test_knowledge_hub.py -q
（引擎不在时真数据用例自动 skip，离线降级用例仍然执行。）
"""
from __future__ import annotations

import os
import sys
import urllib.error

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from api import knowledge_hub as kh  # noqa: E402
from api.deps import require_auth  # noqa: E402


def _client() -> TestClient:
    """Router under test, with the JWT dependency stubbed (auth itself is
    covered by tests/test_auth_guard*.py; this file tests the hub contract)."""
    app = FastAPI()
    app.include_router(kh.router, prefix="/api/v1")
    app.dependency_overrides[require_auth] = lambda: {"username": "pytest"}
    return TestClient(app)


def _engine_up() -> bool:
    try:
        kh._ke_get("/healthz")
        return True
    except Exception:
        return False


ENGINE_UP = _engine_up()


@pytest.fixture(autouse=True)
def _clean_cache():
    kh.reset_cache()
    kh._LOCATE_CACHE.clear()
    yield


# ---------------------------------------------------------------------------
# 1) 离线降级
# ---------------------------------------------------------------------------
def _boom(*_a, **_kw):
    raise kh.EngineOffline("connection refused")


def test_offline_overview_degrades_without_numbers(monkeypatch):
    monkeypatch.setattr(kh, "_ke_get", _boom)
    body = _client().get("/api/v1/tensor/knowledge/overview").json()
    assert body["available"] is False
    assert body["reason"] == "engine_offline"
    assert "stats" not in body and "funnel" not in body
    assert body["checked_at"].endswith("Z")


def test_offline_status_and_search_do_not_raise(monkeypatch):
    monkeypatch.setattr(kh, "_ke_get", _boom)
    c = _client()
    assert c.get("/api/v1/tensor/knowledge/status").json()["available"] is False
    search = c.get("/api/v1/tensor/knowledge/search", params={"q": "lbm"}).json()
    assert search["available"] is False and search["items"] == [] and search["count"] is None
    assert c.get("/api/v1/tensor/knowledge/map").json()["available"] is False


def test_engine_http_error_is_not_reported_as_available(monkeypatch):
    def _404(*_a, **_kw):
        raise kh.EngineError(404, "not found")

    monkeypatch.setattr(kh, "_ke_get", _404)
    body = _client().get("/api/v1/tensor/knowledge/overview").json()
    assert body["available"] is False and body["reason"] == "engine_error"


# ---------------------------------------------------------------------------
# 2) 聚合口径（用合成上游载荷，不依赖引擎；域名合成，符合「零领域语义」）
# ---------------------------------------------------------------------------
_STATS = {
    "objects": 7,
    "by_kind": {"claim": 3, "evidence": 3, "provenance": 1},
    "edges": 4,
    "review_events": 0,
    "production_status": {"draft": 6, "submitted": 1},
    "evidence_status": {"established": 2, "unassessed": 4, "contested": 1},
    "review_status": {"unreviewed": 6, "approved": 1},
    "publication_status": {"private": 6, "published": 1},
}
_OBJECTS = [
    {"id": "tkc:a:1", "kind": "claim", "domain": "domain-a",
     "publication_status": "published", "created_at": "2026-11-08T01:00:00Z"},
    {"id": "tkc:a:2", "kind": "claim", "domain": "domain-a",
     "publication_status": "private", "created_at": "2026-11-08T02:00:00Z"},
    {"id": "tkc:b:1", "kind": "claim", "domain": "domain-b",
     "publication_status": "private", "created_at": "2026-11-09T02:00:00Z"},
    {"id": "tke:a:1", "kind": "evidence", "domain": "domain-a",
     "publication_status": "private", "created_at": "2026-11-09T03:00:00Z"},
]


def test_funnel_uses_declared_denominators():
    funnel = {row["key"]: row["count"] for row in kh._funnel(_STATS)}
    # produced = 对象总数
    assert funnel["produced"] == 7
    # evidenced = 证据态已判定（established 2 + contested 1），unassessed 不计
    assert funnel["evidenced"] == 3
    # reviewed = 非 unreviewed
    assert funnel["reviewed"] == 1
    assert funnel["published"] == 1


def test_domain_and_growth_aggregation():
    domains = kh._domain_rows(_OBJECTS)
    assert domains[0] == {"domain": "domain-a", "count": 3, "claim": 2, "evidence": 1,
                          "provenance": 0, "review": 0}
    growth = kh._growth_rows(_OBJECTS)
    assert growth == [{"date": "2026-11-08", "count": 2}, {"date": "2026-11-09", "count": 2}]


def test_overview_assembles_funnel_and_boards(monkeypatch):
    def fake(path, params=None):
        if path == "/healthz":
            return {"status": "ok", "driver": "sqlite"}
        if path == "/query/stats":
            return _STATS
        if path == "/ko":
            if (params or {}).get("kind") == "claim":
                return {"total": 1, "items": [{"id": "tkc:a:1", "kind": "claim",
                                               "domain": "domain-a",
                                               "publication_status": "published",
                                               "doc": {"statement": "published text"}}]}
            return {"total": len(_OBJECTS), "items": _OBJECTS}
        if path == "/query/by-evidence-relation":
            rel = (params or {}).get("relation")
            if rel == "supports":
                return {"items": [{"claim_id": "tkc:a:1"}, {"claim_id": "tkc:a:2"}]}
            if rel == "refutes":
                return {"items": [{"claim_id": "tkc:a:2", "evidence_id": "tke:a:1",
                                   "source": {"citation": "src", "accessed_at": "2026-11-09"},
                                   "locator": {"value": "repo@abc f.py:1-2", "quote": "SECRET"}}]}
            return {"items": []}
        raise AssertionError(f"unexpected upstream call {path}")

    monkeypatch.setattr(kh, "_ke_get", fake)
    body = _client().get("/api/v1/tensor/knowledge/overview").json()
    assert body["available"] is True
    assert body["stats"]["objects"] == 7
    assert [d["domain"] for d in body["domains"]] == ["domain-a", "domain-b"]
    cited = {r["claim_id"]: r for r in body["cited"]}
    assert cited["tkc:a:1"]["citations"] == 1
    assert cited["tkc:a:1"]["statement"] == "published text"      # 已发布 → 正文可见
    assert cited["tkc:a:2"]["content_hidden"] is True
    assert "statement" not in cited["tkc:a:2"]                    # 未发布 → 正文不出 API
    refuted = body["refuted"][0]
    assert refuted["locator"] == "repo@abc f.py:1-2"              # 溯源定位是元数据，保留
    assert "quote" not in refuted and refuted["content_hidden"] is True


# ---------------------------------------------------------------------------
# 3) 证据链：draft 剥离 quote，但保留 source/locator/record_source
# ---------------------------------------------------------------------------
def test_chain_redacts_quote_but_keeps_locator(monkeypatch, tmp_path):
    pkg = tmp_path / "knowledge" / "synth-pkg" / "evidence"
    pkg.mkdir(parents=True)
    rec = pkg / "ev.json"
    rec.write_text('[\n {\n  "id": "tke:synth:1",\n  "x": 1\n }\n]\n', encoding="utf-8")
    monkeypatch.setenv("TENSOR_KNOWLEDGE_DIR", str(tmp_path))

    chain_payload = {
        "claim_id": "tkc:synth:1",
        "claim": {"statement": "draft claim text", "publication_status": "private",
                  "evidence_status": "contested"},
        "links": [{
            "evidence_id": "tke:synth:1",
            "relation": "supports",
            "assessment_status": "unassessed",
            "source": {"citation": "synth source", "url": "https://example.invalid"},
            "locator": {"value": "synth-repo@deadbee file.py:10-11",
                        "content_hash": "abc123", "quote": "LEAKY QUOTE"},
        }],
        "provenance": [],
        "package_provenance": [{"provenance_id": "tkp:synth:1"}],
        "complete": True,
    }
    monkeypatch.setattr(kh, "_ke_get", lambda path, params=None: chain_payload)
    body = _client().get("/api/v1/tensor/knowledge/chain",
                         params={"claim_id": "tkc:synth:1"}).json()
    assert body["available"] is True
    assert "statement" not in body["claim"] and body["claim"]["content_hidden"] is True
    link = body["links"][0]
    assert link["locator"]["value"] == "synth-repo@deadbee file.py:10-11"
    assert link["locator"]["content_hash"] == "abc123"
    assert "quote" not in link["locator"] and link["locator"]["quote_hidden"] is True
    # 本地记录定位：行号指向知识包 JSON 里该 id 出现的那一行
    assert link["record_source"]["path"] == "knowledge/synth-pkg/evidence/ev.json"
    assert link["record_source"]["line"] == 3


def test_quote_visibility_follows_the_evidence_record(monkeypatch, tmp_path):
    """已发布 claim 不等于其证据已发布：引文可见性跟证据记录自身的发布态。"""
    monkeypatch.setenv("TENSOR_KNOWLEDGE_DIR", str(tmp_path))
    payload = {"claim_id": "tkc:synth:2",
               "claim": {"statement": "public claim", "publication_status": "published"},
               "links": [{"evidence_id": "tke:synth:2", "locator": {"quote": "Q"}}]}

    def fake(path, params=None):
        if path == "/ko":
            return {"total": 1, "items": [{"id": "tke:synth:2",
                                           "publication_status": "published"}]}
        return payload

    monkeypatch.setattr(kh, "_ke_get", fake)
    body = _client().get("/api/v1/tensor/knowledge/chain",
                         params={"claim_id": "tkc:synth:2"}).json()
    assert body["claim"]["statement"] == "public claim"
    assert body["links"][0]["locator"]["quote"] == "Q"
    assert body["links"][0]["publication_status"] == "published"


def test_unpublished_evidence_quote_stays_hidden(monkeypatch, tmp_path):
    monkeypatch.setenv("TENSOR_KNOWLEDGE_DIR", str(tmp_path))
    payload = {"claim_id": "tkc:synth:3",
               "claim": {"statement": "public claim", "publication_status": "published"},
               "links": [{"evidence_id": "tke:synth:3", "locator": {"quote": "Q"}}]}

    def fake(path, params=None):
        if path == "/ko":
            return {"total": 1, "items": [{"id": "tke:synth:3",
                                           "publication_status": "private"}]}
        return payload

    monkeypatch.setattr(kh, "_ke_get", fake)
    link = _client().get("/api/v1/tensor/knowledge/chain",
                         params={"claim_id": "tkc:synth:3"}).json()["links"][0]
    assert "quote" not in link["locator"] and link["locator"]["quote_hidden"] is True


def test_map_counts_every_kind_and_redacts_claims(monkeypatch, tmp_path):
    monkeypatch.setenv("TENSOR_KNOWLEDGE_DIR", str(tmp_path))
    objects = [
        {"id": "tkc:s:1", "kind": "claim", "domain": "d1", "publication_status": "private",
         "evidence_status": "established"},
        {"id": "tke:s:1", "kind": "evidence", "domain": "d1", "publication_status": "private",
         "evidence_status": "unassessed"},
        {"id": "tkp:s:1", "kind": "provenance", "domain": "d1", "publication_status": "private"},
    ]

    def fake(path, params=None):
        if path == "/ko":
            kind = (params or {}).get("kind")
            items = [o for o in objects if kind is None or o["kind"] == kind]
            return {"total": len(items), "items": items}
        if path == "/query/by-evidence-relation":
            rel = (params or {}).get("relation")
            return {"items": [{"claim_id": "tkc:s:1"}] if rel == "supports" else []}
        raise AssertionError(path)

    monkeypatch.setattr(kh, "_ke_get", fake)
    body = _client().get("/api/v1/tensor/knowledge/map").json()
    dom = body["domains"][0]
    assert dom["total"] == 3                                  # 非 claim 类型也要计数
    kinds = {k["kind"]: k for k in dom["kinds"]}
    assert kinds["evidence"]["count"] == 1 and kinds["provenance"]["count"] == 1
    claim = kinds["claim"]["claims"][0]
    assert claim["citations"] == 1 and claim["content_hidden"] is True
    assert "statement" not in claim


def test_locate_record_prefers_own_kind_directory(monkeypatch, tmp_path):
    """claim 的 evidence_refs 里也有 evidence id：定位必须落在 evidence/ 而不是 claims/。"""
    pkg = tmp_path / "knowledge" / "pkg"
    (pkg / "claims").mkdir(parents=True)
    (pkg / "evidence").mkdir(parents=True)
    (pkg / "claims" / "c.json").write_text(
        '[\n {"id": "tkc:s:9", "evidence_refs": ["tke:s:9"]}\n]\n', encoding="utf-8")
    (pkg / "evidence" / "e.json").write_text(
        '[\n\n {"id": "tke:s:9", "source": "x"}\n]\n', encoding="utf-8")
    monkeypatch.setenv("TENSOR_KNOWLEDGE_DIR", str(tmp_path))
    kh._LOCATE_CACHE.clear()
    loc = kh._locate_record("tke:s:9")
    assert loc["path"] == "knowledge/pkg/evidence/e.json"
    assert loc["line"] == 3
    claim_loc = kh._locate_record("tkc:s:9")
    assert claim_loc["path"] == "knowledge/pkg/claims/c.json" and claim_loc["line"] == 2


# ---------------------------------------------------------------------------
# 4) 证据直读通道（路径围栏 / 白名单 / 窗口）
# ---------------------------------------------------------------------------
def test_source_reader_serves_window(monkeypatch, tmp_path):
    (tmp_path / "knowledge").mkdir()
    f = tmp_path / "knowledge" / "note.md"
    f.write_text("\n".join(f"line{i}" for i in range(1, 21)) + "\n", encoding="utf-8")
    monkeypatch.setenv("TENSOR_KNOWLEDGE_DIR", str(tmp_path))
    body = _client().get("/api/v1/tensor/knowledge/source/knowledge/note.md",
                         params={"line": 10, "context": 2}).json()
    assert body["total_lines"] == 20
    assert [r["n"] for r in body["lines"]] == [8, 9, 10, 11, 12]
    assert body["lines"][2]["text"] == "line10"
    assert body["path"] == "knowledge/note.md"


def test_source_reader_fences(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "ok.json").write_text("{}", encoding="utf-8")
    (root / "secret.txt").write_text("nope", encoding="utf-8")
    (root / "knowledge" / "blob.bin").write_bytes(b"\x00\x01")
    monkeypatch.setenv("TENSOR_KNOWLEDGE_DIR", str(root))
    c = _client()
    assert c.get("/api/v1/tensor/knowledge/source/knowledge/ok.json").status_code == 200
    # 逃出仓库
    assert c.get("/api/v1/tensor/knowledge/source/../secret.txt").status_code in (403, 404)
    # 未在白名单的扩展名
    assert c.get("/api/v1/tensor/knowledge/source/knowledge/blob.bin").status_code == 403
    # 不存在
    assert c.get("/api/v1/tensor/knowledge/source/knowledge/none.json").status_code == 404


# ---------------------------------------------------------------------------
# 5) 免鉴权名单落在中间件层（历史坑：写在路由层会被守卫先拒）
# ---------------------------------------------------------------------------
def test_auth_guard_exempts_only_single_source_file_reads():
    from middleware.auth_guard import _should_skip_auth

    ok = "/api/v1/tensor/knowledge/source/knowledge/oss-lbm/fingering/claims/fingering.json"
    assert _should_skip_auth(ok, "GET") is True
    assert _should_skip_auth(ok, "HEAD") is True
    assert _should_skip_auth(ok, "POST") is False          # 写方法不放行
    assert _should_skip_auth("/api/v1/tensor/knowledge/source/../../etc/passwd", "GET") is False
    assert _should_skip_auth("/api/v1/tensor/knowledge/overview", "GET") is False
    assert _should_skip_auth("/api/v1/tensor/knowledge/source/", "GET") is False


# ---------------------------------------------------------------------------
# 6) 真数据：引擎在 8021 时与 /query/stats 对齐
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not ENGINE_UP, reason="KE API not running on KE_API_BASE (8021)")
def test_live_overview_matches_engine_stats():
    c = _client()
    stats = kh._ke_get("/query/stats")
    body = c.get("/api/v1/tensor/knowledge/overview").json()
    assert body["available"] is True
    assert body["stats"]["objects"] == stats["objects"]
    assert body["stats"]["by_kind"] == stats["by_kind"]
    assert sum(d["count"] for d in body["domains"]) == stats["objects"]
    assert sum(g["count"] for g in body["growth"]) == stats["objects"]
    funnel = {r["key"]: r["count"] for r in body["funnel"]}
    assert funnel["produced"] == stats["objects"]
    assert funnel["published"] == (stats["publication_status"] or {}).get("published", 0)


@pytest.mark.skipif(not ENGINE_UP, reason="KE API not running on KE_API_BASE (8021)")
def test_live_chain_and_search_are_traceable():
    c = _client()
    claims = kh._ko_objects(kind="claim")[:1]
    if not claims:
        pytest.skip("engine has no claims ingested")
    cid = claims[0]["id"]
    chain = c.get("/api/v1/tensor/knowledge/chain", params={"claim_id": cid}).json()
    assert chain["available"] is True and chain["claim_id"] == cid
    for link in chain["links"]:
        assert link["source"]["citation"]
        assert link["locator"]["value"]                      # 源文件:行号 @ commit
        assert link["record_source"] is None or link["record_source"]["line"] >= 1
    kw = "lattice"
    res = c.get("/api/v1/tensor/knowledge/search", params={"q": kw}).json()
    assert res["available"] is True and res["count"] >= 1
    assert all("statement" not in it for it in res["items"] if not kh.is_published(it))