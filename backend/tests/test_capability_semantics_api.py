"""能力台账语义分层 API 单测（独立小应用挂载 router，不依赖主应用/数据库）。

覆盖：工件缺失 503、正常读取、过滤参数、format=dot/mermaid、非法参数 422、
refresh 端点（monkeypatch 掉真实扫描，避免 45 秒开销）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

try:
    from api import capability_semantics as cs  # noqa: E402
    from api.deps import require_auth  # noqa: E402
except Exception as exc:  # pragma: no cover - 环境缺依赖时跳过而非误报
    pytest.skip(f"backend api 依赖不可用: {exc}", allow_module_level=True)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(cs.router, prefix="/api/v1")
    app.dependency_overrides[require_auth] = lambda: {"user_id": "test", "role": "admin"}
    return TestClient(app)


def _write_artifacts(tmp_path: Path, monkeypatch) -> Path:
    art = tmp_path / "artifacts/capability_semantics"
    art.mkdir(parents=True)
    (art / "semantic_layers.json").write_text(json.dumps({
        "stats": {"tier_node_counts": {"capability": 2, "delivery": 3, "evidence": 1},
                  "tier_coverage_ratio": {"capability": 1.0, "delivery": 0.5,
                                          "evidence": 0.5, "both": 0.5},
                  "class_distribution": {"capability+delivery": 1, "capability_only": 1},
                  "entry_loss_ratio": 0.0, "orphan_delivery_count": 1,
                  "warnings": ["w1"]},
        "triples": [
            {"capability_id": "synth.a", "domain": "d1",
             "layer_complete": {"capability": True, "delivery": True, "evidence": False}},
            {"capability_id": "synth.b", "domain": "d2",
             "layer_complete": {"capability": True, "delivery": False, "evidence": True}},
        ],
    }), encoding="utf-8")
    (art / "evidence_audit_after_patch.json").write_text(json.dumps({
        "graded_entries": 2, "withheld_ratio": 0.5, "withheld_ratio_max": 0.3,
        "level_distribution": {"WITHHELD": 1, "REAL": 1},
        "reason_histogram": {"CODE_ONLY": 1},
        "withheld_breakdown": {"NO_CODE_DELIVERY_WITH_TEST_REFERENCE":
                               {"count": 1, "share_of_withheld": 1.0, "text": "t"}},
        "accuracy_histogram": {"within_pass": 1, "no_numeric": 1},
        "overclaim_ids": ["synth.a"],
        "entries": [
            {"capability_id": "synth.a", "domain": "d1", "derived_level": "WITHHELD"},
            {"capability_id": "synth.b", "domain": "d2", "derived_level": "REAL"},
        ],
    }), encoding="utf-8")
    (art / "misuse_report.json").write_text(json.dumps({
        "total_findings": 1, "not_supported_misuse_count": 1, "review_needed_count": 0,
        "by_kind": {"UNDERCLAIM_NOT_SUPPORTED": 1},
        "findings": [{"kind": "UNDERCLAIM_NOT_SUPPORTED", "capability_id": "synth.a",
                      "declared_level": "NOT_SUPPORTED", "suggested_level": "STUB",
                      "actions": ["SET_DECLARED_LEVEL"]}],
    }), encoding="utf-8")
    (art / "registry_patch.json").write_text(json.dumps({
        "patch_count": 1, "patch_sha256": "abc", "applies_to_ledger_sha256": "def",
        "patches": [{"capability_id": "synth.a", "to_level": "STUB"}],
    }), encoding="utf-8")
    (art / "dependency_graph.json").write_text(json.dumps({
        "nodes": ["synth.a", "synth.b"],
        "edges": [{"dep": "synth.a", "dependent": "synth.b"}],
        "roles": {"synth.a": "domain", "synth.b": "domain"},
        "summary": {"cycle_count": 0, "orphan_ratio_domain": 0.0},
    }), encoding="utf-8")
    (art / "dependency_graph.dot").write_text("digraph x {}\n", encoding="utf-8")
    (art / "acceptance.json").write_text(json.dumps({
        "all_ok": True,
        "criteria": {"a_semantic_layers": {"ok": True, "note": "n"}},
    }), encoding="utf-8")
    (art / "audit_log.json").write_text(json.dumps([
        {"artifact": "semantic_layers.json", "sha256": "s1", "prev": "",
         "record_sha256": "r1"},
        {"artifact": "acceptance.json", "sha256": "s2", "prev": "r1",
         "record_sha256": "r2"},
    ]), encoding="utf-8")
    monkeypatch.setattr(cs, "ARTIFACT_DIR", art)
    cs._CACHE.clear()
    return art


def test_overview_and_hashes(tmp_path, monkeypatch):
    _write_artifacts(tmp_path, monkeypatch)
    resp = _client().get("/api/v1/tensor/capabilities/semantics")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tiers"] == {"capability": 2, "delivery": 3, "evidence": 1}
    assert data["criteria"]["a_semantic_layers"]["ok"] is True
    assert data["artifact_sha256"]["semantic_layers.json"] == "s1"
    assert data["all_ok"] is True


def test_layers_filters(tmp_path, monkeypatch):
    _write_artifacts(tmp_path, monkeypatch)
    c = _client()
    assert c.get("/api/v1/tensor/capabilities/semantics/layers").json()["data"]["total"] == 2
    missing_delivery = c.get("/api/v1/tensor/capabilities/semantics/layers",
                             params={"missing_tier": "delivery"}).json()["data"]
    assert [t["capability_id"] for t in missing_delivery["triples"]] == ["synth.b"]
    by_domain = c.get("/api/v1/tensor/capabilities/semantics/layers",
                      params={"domain": "d1"}).json()["data"]
    assert by_domain["total"] == 1
    assert c.get("/api/v1/tensor/capabilities/semantics/layers",
                 params={"missing_tier": "bogus"}).status_code == 422


def test_evidence_filters(tmp_path, monkeypatch):
    _write_artifacts(tmp_path, monkeypatch)
    c = _client()
    base = c.get("/api/v1/tensor/capabilities/semantics/evidence").json()["data"]
    assert base["graded_entries"] == 2 and base["withheld_ratio"] == 0.5
    only_real = c.get("/api/v1/tensor/capabilities/semantics/evidence",
                      params={"level": "real"}).json()["data"]
    assert [e["capability_id"] for e in only_real["entries"]] == ["synth.b"]
    by_domain = c.get("/api/v1/tensor/capabilities/semantics/evidence",
                      params={"domain": "d1"}).json()["data"]
    assert [e["capability_id"] for e in by_domain["entries"]] == ["synth.a"]


def test_misuse_endpoint(tmp_path, monkeypatch):
    _write_artifacts(tmp_path, monkeypatch)
    data = _client().get("/api/v1/tensor/capabilities/semantics/misuse").json()["data"]
    assert data["not_supported_misuse_count"] == 1
    assert data["patch"]["patch_count"] == 1
    assert data["findings"][0]["suggested_level"] == "STUB"


def test_graph_formats(tmp_path, monkeypatch):
    _write_artifacts(tmp_path, monkeypatch)
    c = _client()
    g = c.get("/api/v1/tensor/capabilities/semantics/graph").json()["data"]
    assert g["summary"]["cycle_count"] == 0 and g["nodes"] == ["synth.a", "synth.b"]
    dot = c.get("/api/v1/tensor/capabilities/semantics/graph",
                params={"format": "dot"}).json()["data"]
    assert "digraph" in dot["dot"]
    mm = c.get("/api/v1/tensor/capabilities/semantics/graph",
               params={"format": "mermaid"}).json()["data"]
    assert "flowchart" in mm["mermaid"]


def test_acceptance_endpoint(tmp_path, monkeypatch):
    _write_artifacts(tmp_path, monkeypatch)
    data = _client().get("/api/v1/tensor/capabilities/semantics/acceptance").json()["data"]
    assert data["all_ok"] is True
    assert data["artifact_sha256"]


def test_missing_artifacts_returns_503(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "ARTIFACT_DIR", tmp_path / "nope")
    cs._CACHE.clear()
    resp = _client().get("/api/v1/tensor/capabilities/semantics")
    assert resp.status_code == 503
    assert "tools.capability_semantics" in resp.json()["detail"]


def test_corrupt_artifact_returns_503(tmp_path, monkeypatch):
    art = _write_artifacts(tmp_path, monkeypatch)
    (art / "semantic_layers.json").write_text("{broken", encoding="utf-8")
    cs._CACHE.clear()
    assert _client().get("/api/v1/tensor/capabilities/semantics").status_code == 503


def test_refresh_endpoint_monkeypatched(tmp_path, monkeypatch):
    _write_artifacts(tmp_path, monkeypatch)

    class _FakeResult:
        acceptance = {"all_ok": True, "criteria": {"a_semantic_layers": {"ok": True}}}
        artifacts = {"acceptance.json": "sha"}
        warnings: list[str] = []

    def _fake_run(**kwargs):
        return _FakeResult()

    engine = cs.load_engine()  # 私有包名加载（避开 backend/tools 影子包）
    monkeypatch.setattr(engine, "run_pipeline", _fake_run)
    data = _client().post("/api/v1/tensor/capabilities/semantics/refresh").json()["data"]
    assert data["all_ok"] is True
    assert data["criteria"]["a_semantic_layers"] is True

def test_routers_wired_in_main_and_route_shapes():
    """接线回归：两个能力路由器都必须在 main.py 里 include；路由形状可枚举。"""
    main_src = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert "api.capability_semantics" in main_src
    assert "api.capability_audit" in main_src  # P266W1-N10 修复：此前从未挂载

    from api.capability_audit import router as audit_router

    assert any(getattr(r, "path", "").endswith("/audit") for r in audit_router.routes)

    paths = {getattr(r, "path", "") for r in cs.router.routes}
    base = "/tensor/capabilities/semantics"
    assert {base, f"{base}/layers", f"{base}/evidence", f"{base}/misuse",
            f"{base}/graph", f"{base}/acceptance", f"{base}/refresh"} <= paths
