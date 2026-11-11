"""跨仓能力依赖图谱 + 级联传播 + 预警 的机器判据测试（p285w2b-n03）。

跑法::

    python -m pytest tests/test_capability_graph.py -q

每个测试对应战役判据的一项或一项失败模式；夹具全部读仓内真实工件
（config/capability_graph.json + 台账 + 基准边 + 人工链），不手抄数字。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.capability_graph import (  # noqa: E402
    AlertEngine,
    CascadeEvent,
    GraphConfig,
    build_snapshot,
    compare_cascade_with_manual,
    compare_edge_sets,
    jaccard,
    mutation_sensitivity,
    scan_forbidden_tokens,
    simulate,
)
from tools.capability_graph.builder import load_declared_edges, load_levels  # noqa: E402
from tools.capability_graph.model import LevelError, normalise_level  # noqa: E402


# ── 夹具 ────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def cfg() -> GraphConfig:
    return GraphConfig.from_file(ROOT / "config" / "capability_graph.json", ROOT)


@pytest.fixture(scope="module")
def snapshot(cfg):
    return build_snapshot(cfg)


@pytest.fixture(scope="module")
def baseline():
    return json.loads((ROOT / "artifacts" / "capability_graph" / "baseline_edges.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def manual():
    return json.loads((ROOT / "artifacts" / "capability_graph" / "manual_chains.json").read_text("utf-8"))


def _sim(snapshot, cfg, capability_id, to_level):
    return simulate(snapshot, CascadeEvent(capability_id, to_level), cfg).to_dict()


# ── 1. 图构建 ───────────────────────────────────────────────
def test_graph_nodes_match_ledger_entries(snapshot, cfg):
    levels, unparsable = load_levels(cfg.sources["ledger"], cfg)
    assert snapshot.nodes.keys() == levels.keys()
    assert not unparsable
    assert len(snapshot.nodes) >= 20
    for node_id, level in levels.items():
        assert snapshot.nodes[node_id].own_level == level


def test_graph_edges_match_declared_config(snapshot, cfg):
    declared = {(e.src, e.dst) for e in load_declared_edges(cfg.sources["semantics_config"], cfg)}
    built = {(e.src, e.dst) for e in snapshot.edges}
    assert declared <= built
    assert len(declared) >= 10
    for e in snapshot.edges:
        assert 0.0 < e.weight <= 1.0
        assert e.src != e.dst


def test_unknown_level_is_reported_not_guessed(cfg, tmp_path):
    ledger = json.loads(cfg.sources["ledger"].read_text("utf-8"))
    ledger["capabilities"].append({"id": "synthetic.node", "claimed_level": "WAT"})
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger), "utf-8")
    levels, unparsable = load_levels(path, cfg)
    assert "synthetic.node" not in levels
    assert unparsable and unparsable[0]["id"] == "synthetic.node"
    with pytest.raises(LevelError):
        normalise_level("WAT", cfg.level_rank)


def test_inferred_edges_from_synthetic_evidence(cfg, tmp_path, snapshot):
    """证据文本推断边：合成台账里某节点证据两次提到另一节点 → 生成弱推断边。"""
    ledger = {
        "capabilities": [
            {"id": "synthetic.up", "claimed_level": "REAL"},
            {
                "id": "synthetic.down",
                "claimed_level": "MOCK",
                "evidence": [
                    {"note": "synthetic.up 的解析判据不通过"},
                    {"note": "复算显示 synthetic.up 端点恒等"},
                ],
            },
            {
                "id": "synthetic.weak",
                "claimed_level": "STUB",
                "evidence": [{"note": "synthetic.up 只被提到一次"}],
            },
        ]
    }
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger), "utf-8")
    cfg2 = GraphConfig(raw=json.loads(json.dumps(cfg.raw)), repo_root=cfg.repo_root, sources=dict(cfg.sources))
    cfg2.sources["ledger"] = path
    snap = build_snapshot(cfg2)
    edges = {(e.src, e.dst): e for e in snap.edges}
    assert ("synthetic.up", "synthetic.down") in edges
    inferred = edges[("synthetic.up", "synthetic.down")]
    assert "ledger_evidence_text" in inferred.sources
    assert inferred.weight == pytest.approx(cfg.inferred_weight)
    assert inferred.corroborations == 2
    assert ("synthetic.up", "synthetic.weak") not in edges  # 只提一次 → 保守不放行


def test_dangling_dependency_target_registered_as_node(cfg, tmp_path):
    sem = {
        "capabilities": [
            {"id": "synthetic.down", "label": "d", "graph_role": "domain",
             "depends_on": ["synthetic.absent"]},
        ]
    }
    sem_path = tmp_path / "semantics.json"
    sem_path.write_text(json.dumps(sem), "utf-8")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"capabilities": [{"id": "synthetic.down", "claimed_level": "REAL"}]}), "utf-8")
    cfg2 = GraphConfig(raw=json.loads(json.dumps(cfg.raw)), repo_root=cfg.repo_root, sources=dict(cfg.sources))
    cfg2.sources["semantics_config"] = sem_path
    cfg2.sources["ledger"] = ledger
    snap = build_snapshot(cfg2)
    assert "synthetic.absent" in snap.nodes
    assert snap.nodes["synthetic.absent"].level_source == "dangling"
    assert snap.nodes["synthetic.absent"].own_confidence == 0.0


# ── 2. Jaccard 判据（含敏感性负对照）────────────────────────
def test_jaccard_vs_baseline_meets_threshold(snapshot, baseline, cfg):
    cmp_ = compare_edge_sets(snapshot.edges, baseline["edges"])
    assert cmp_["baseline_count"] >= int(cfg.acceptance["baseline_edges_min"])
    assert cmp_["jaccard"] >= float(cfg.acceptance["jaccard_min"])
    assert cmp_["recall"] == pytest.approx(1.0)
    assert jaccard(snapshot.edges, baseline["edges"]) == pytest.approx(cmp_["jaccard"])


def test_jaccard_criterion_is_discriminative(snapshot, baseline):
    sens = mutation_sensitivity(snapshot.edges, baseline["edges"])
    assert sens["criterion_is_discriminative"] is True
    by_name = {m["mutation"]: m["jaccard"] for m in sens["mutations"]}
    assert by_name["duplicate_all_edges"] == pytest.approx(1.0)
    assert by_name["reverse_3_edges"] < 0.8
    assert by_name["delete_1_edges"] < 1.0


# ── 3. 级联传播 vs 人工分析 ─────────────────────────────────
@pytest.mark.parametrize(
    "chain_id",
    ["C1-generate-to-mock", "C2-listmodels-to-mock", "C3-calcres-to-mock"],
)
def test_cascade_matches_manual_analysis(snapshot, cfg, manual, chain_id):
    chain = next(c for c in manual["chains"] if c["chain_id"] == chain_id)
    result = _sim(snapshot, cfg, chain["capability_id"], chain["to_level"])
    cmp_ = compare_cascade_with_manual(result, chain)
    assert cmp_["max_rel_error_pct"] <= float(cfg.acceptance["cascade_manual_error_max_pct"])
    assert cmp_["level_mismatches"] == []
    assert set(result["degradation"]) == set(snapshot.nodes)
    assert sorted(result["event"].items())  # 事件显式回显
    assert result["converged"] is True


def test_cascade_affected_set_matches_manual(snapshot, cfg, manual):
    for chain in manual["chains"]:
        result = _sim(snapshot, cfg, chain["capability_id"], chain["to_level"])
        got = {a["id"] for a in result["affected"]}
        assert got == set(chain["expected_affected_ids"]), chain["chain_id"]
        hops = {a["id"]: a["hop"] for a in result["affected"]}
        assert hops[chain["capability_id"]] == 0


def test_cascade_does_not_mutate_snapshot(snapshot, cfg):
    before = snapshot.levels()
    _sim(snapshot, cfg, "geometry.generate", "MOCK")
    assert snapshot.levels() == before


def test_cascade_marks_unmeasurable_decay_when_own_confidence_zero(snapshot, cfg):
    result = _sim(snapshot, cfg, "geometry.generate", "MOCK")
    rows = {a["id"]: a for a in result["affected"]}
    zero_basis = rows["ai.predict"]
    assert zero_basis["confidence_zero_basis"] is True
    assert zero_basis["confidence_decay"] is None
    assert zero_basis["degradation"] > 0  # 结构性受影响，但不是相对衰减可量化


def test_upgrade_event_produces_no_degradation(snapshot, cfg):
    result = _sim(snapshot, cfg, "misc.stubs", "REAL")
    assert result["own_drop"] == 0.0
    assert result["affected_count"] == 0
    assert result["severity"] == "info"
    assert result["meta"]["direction"] == "upgrade"
    assert result["meta"]["changed_ids"] == ["misc.stubs"]


def test_cascade_never_upgrades_dependents(snapshot, cfg):
    """级联只能降级：把上游抬到 REAL 不得让下游等级高于其自身证据等级。"""
    result = _sim(snapshot, cfg, "methods.computation", "REAL")
    rank = cfg.level_rank
    for node_id, level in result["levels_after"].items():
        if node_id == "methods.computation":  # 事件节点自身等级就是被抬升的对象
            continue
        assert rank[level] <= rank[snapshot.nodes[node_id].own_level], node_id


def test_cyclic_synthetic_graph_converges(cfg, tmp_path):
    """有环图必须收敛并有显式迭代计数（不静默截断）。"""
    sem = {
        "capabilities": [
            {"id": "synthetic.a", "graph_role": "domain", "depends_on": ["synthetic.b"]},
            {"id": "synthetic.b", "graph_role": "domain", "depends_on": ["synthetic.a"]},
        ]
    }
    sem_path = tmp_path / "semantics.json"
    sem_path.write_text(json.dumps(sem), "utf-8")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"capabilities": [
            {"id": "synthetic.a", "claimed_level": "REAL"},
            {"id": "synthetic.b", "claimed_level": "REAL"},
        ]}),
        "utf-8",
    )
    cfg2 = GraphConfig(raw=json.loads(json.dumps(cfg.raw)), repo_root=cfg.repo_root, sources=dict(cfg.sources))
    cfg2.sources["semantics_config"] = sem_path
    cfg2.sources["ledger"] = ledger
    snap = build_snapshot(cfg2)
    result = simulate(snap, CascadeEvent("synthetic.a", "STUB"), cfg2)
    assert result.converged is True
    assert 1 <= result.iterations <= int(cfg.raw["cascade"]["max_iterations"])
    assert result.degradation["synthetic.b"] > 0.0


# ── 4. 预警 ─────────────────────────────────────────────────
def test_alert_emitted_within_latency_budget(snapshot, cfg, tmp_path):
    history = tmp_path / "alerts.jsonl"
    engine = AlertEngine(snapshot, cfg, history_path=history)
    alert = engine.apply_level_change("geometry.generate", "MOCK", persist=True)
    assert alert.latency_ms <= float(cfg.acceptance["alert_latency_max_ms"])
    assert alert.within_budget is True
    assert alert.severity in ("critical", "high", "medium", "info")
    assert alert.affected_count >= len(snapshot.dependencies("geometry.generate"))
    assert any(a["id"] == "calc.resistance" for a in alert.affected)
    lines = history.read_text("utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["alert_id"].startswith("alr-")
    assert payload["latency_ms"] <= float(cfg.acceptance["alert_latency_max_ms"])


def test_alert_detects_changes_from_level_table(snapshot, cfg):
    engine = AlertEngine(snapshot, cfg)
    new_levels = dict(snapshot.levels())
    new_levels["geometry.generate"] = "MOCK"
    new_levels["geometry.list_models"] = "MOCK"
    alerts = engine.detect_changes(new_levels)
    assert [a.capability_id for a in alerts] == ["geometry.generate", "geometry.list_models"]
    assert all(a.latency_ms <= float(cfg.acceptance["alert_latency_max_ms"]) for a in alerts)
    assert engine.summary()["alert_count"] == 2


# ── 5. API ─────────────────────────────────────────────────
def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    if str(ROOT / "backend") not in sys.path:
        sys.path.insert(0, str(ROOT / "backend"))
    from api.capability_graph import router as cap_router
    from api.deps import require_auth

    app = FastAPI()
    app.include_router(cap_router, prefix="/api/v1")
    app.dependency_overrides[require_auth] = lambda: {"user_id": 1, "username": "pytest"}
    return TestClient(app)


def test_api_returns_graph_within_budget(cfg):
    client = _client()
    t0 = time.perf_counter()
    resp = client.get("/api/v1/capability-graph")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert resp.status_code == 200
    assert elapsed_ms <= float(cfg.acceptance["api_response_max_ms"])
    body = resp.json()
    assert body["summary"]["node_count"] >= 20
    assert body["summary"]["edge_count"] >= 10
    assert len(body["edges"]) == body["summary"]["edge_count"]
    assert set(body["levels"]) == {n["id"] for n in body["nodes"]}
    assert body["config"]["default_edge_weight"] == pytest.approx(cfg.default_weight)
    # 二次请求走缓存
    resp2 = client.get("/api/v1/capability-graph")
    assert resp2.status_code == 200


def test_api_simulate_and_alerts(cfg):
    client = _client()
    resp = client.post(
        "/api/v1/capability-graph/simulate",
        json={"capability_id": "geometry.generate", "to_level": "MOCK"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["severity"] in ("critical", "high", "medium", "info")
    assert body["affected_count"] >= 5
    assert body["elapsed_ms"] <= float(cfg.acceptance["alert_latency_max_ms"])

    alerts = client.get("/api/v1/capability-graph/alerts").json()
    assert alerts["count"] >= 1
    assert alerts["alerts"][0]["capability_id"] == "geometry.generate"
    assert alerts["alerts"][0]["within_budget"] is True

    bad = client.post(
        "/api/v1/capability-graph/simulate",
        json={"capability_id": "geometry.generate", "to_level": "NOPE"},
    )
    assert bad.status_code == 422
    unknown = client.post(
        "/api/v1/capability-graph/simulate",
        json={"capability_id": "no.such.node", "to_level": "MOCK"},
    )
    assert unknown.status_code == 404


# ── 6. 零领域语义 ───────────────────────────────────────────
def test_zero_domain_semantics_scan_clean(cfg):
    spec = cfg.raw["forbidden_tokens_scan"]
    result = scan_forbidden_tokens(
        [ROOT / p for p in spec["paths"]], spec["forbidden_tokens"]
    )
    assert result["scanned_files"] >= 3
    assert result["clean"] is True, result["hits"]