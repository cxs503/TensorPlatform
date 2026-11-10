"""三层装配 + 误用治理 + 依赖图的单测（合成域名 synth.*）。"""
from __future__ import annotations

from pathlib import Path

from tools.capability_semantics.graph import build_graph
from tools.capability_semantics.layering import (CLASS_BOTH, CLASS_CAPABILITY_ONLY,
                                                 CLASS_DELIVERY_ONLY, build_layers,
                                                 classify_tiers)
from tools.capability_semantics.misuse import (KIND_CLAIM_WITHOUT_DELIVERY,
                                               KIND_UNDERCLAIM, KIND_UNREGISTERED,
                                               build_registry_patch, scan_misuse,
                                               verify_patch)
from tools.capability_semantics.model import (DeliveryArtifact, Declaration, EvidenceRecord,
                                              SemanticTriple)
from tools.capability_semantics.scan import orphan_endpoint_modules
from tools.capability_semantics.sources import (EndpointModule, SemanticsConfig,
                                                load_semantics_config,
                                                load_declarations, load_capability_meta,
                                                load_ledger_evidence)
from tools.capability_semantics.tests.conftest import build_workspace


def _cfg(tmp_path: Path) -> SemanticsConfig:
    ws = build_workspace(tmp_path)
    return load_semantics_config(ws.config_path)


def _deliveries():
    return {
        "synth.base": [DeliveryArtifact(capability_id="synth.base", kind="code",
                                        path="backend/api/domain/synth_bound.py",
                                        endpoint_count=2)],
        "synth.derived": [DeliveryArtifact(capability_id="synth.derived", kind="code",
                                           path="backend/services/synth_derived.py")],
        "synth.tool": [],
    }


def test_classify_tiers_four_classes():
    both = SemanticTriple(capability_id="a",
                          deliveries=[DeliveryArtifact(capability_id="a", kind="code",
                                                      path="x.py")],
                          evidences=[EvidenceRecord(source="s", ref="1")])
    only_d = SemanticTriple(capability_id="b",
                            deliveries=[DeliveryArtifact(capability_id="b", kind="code",
                                                        path="x.py")])
    only_e = SemanticTriple(capability_id="c", evidences=[EvidenceRecord(source="s", ref="1")])
    none = SemanticTriple(capability_id="d")
    assert classify_tiers(both) == CLASS_BOTH
    assert classify_tiers(only_d) == CLASS_DELIVERY_ONLY
    assert classify_tiers(only_e) == "capability+evidence"
    assert classify_tiers(none) == CLASS_CAPABILITY_ONLY


def test_build_layers_merges_sources_and_detects_loss(tmp_path: Path):
    ws = build_workspace(tmp_path)
    cfg = load_semantics_config(ws.config_path)
    decls, _ = load_declarations(ws.ledger_path, "ledger")
    decls.append(Declaration(source="snapshot", capability_id="synth.base",
                             declared_level="WITHHELD"))
    decls.append(Declaration(source="snapshot", capability_id="synth.extra",
                             declared_level="STUB"))
    ledger = build_layers(
        cfg=cfg, declarations=decls, deliveries=_deliveries(),
        evidences={"synth.base": [EvidenceRecord(source="s", ref="1")]},
        endpoint_modules=[EndpointModule(path="backend/api/domain/synth_orphan.py",
                                         endpoint_count=1)],
        capability_meta=load_capability_meta(ws.ledger_path),
        ledger_ids=["synth.base", "synth.derived", "synth.missing"])
    assert ledger.by_id("synth.base").declared_level == "WITHHELD"  # 取最强声明
    assert ledger.by_id("synth.base").declared_levels == {"ledger": "NOT_SUPPORTED",
                                                          "snapshot": "WITHHELD"}
    assert ledger.lost_ids == ["synth.missing"]
    stats = ledger.stats()
    assert stats["entry_loss_ratio"] == round(1 / 3, 4)
    assert stats["class_assignment_ratio"] == 1.0
    assert stats["tier_node_counts"]["capability"] == 4
    assert stats["orphan_delivery_count"] == 1
    # 分母 = 孤立端点模块 + 已绑定端点模块路径（2 条）
    assert stats["orphan_delivery_ratio"] == round(1 / 3, 4)
    assert any("lost_entries" in w for w in stats["warnings"])
    assert ledger.by_id("synth.extra").origin == "declared"


def test_build_layers_patch_entries_mark_origin(tmp_path: Path):
    ws = build_workspace(tmp_path)
    cfg = load_semantics_config(ws.config_path)
    ledger = build_layers(
        cfg=cfg, declarations=[], deliveries=_deliveries(), evidences={},
        capability_meta={}, patch_entries=[{"capability_id": "unregistered.synth_orphan",
                                            "to_level": "STUB"}],
        ledger_ids=[])
    t = ledger.by_id("unregistered.synth_orphan")
    assert t is not None and t.origin == "patched" and t.declared_level == "STUB"
    assert ledger.stats()["capability_entries"] == 4  # 配置 3 + 补丁 1


def test_orphan_endpoint_modules_ignores_bound_path():
    bound = [DeliveryArtifact(capability_id="a", kind="code", path="p1.py")]
    mods = [EndpointModule(path="p1.py", endpoint_count=1),
            EndpointModule(path="p2.py", endpoint_count=3)]
    assert [m.path for m in orphan_endpoint_modules({"a": bound}, mods)] == ["p2.py"]


def test_scan_misuse_and_patch_and_verify(tmp_path: Path):
    ws = build_workspace(tmp_path)
    cfg = load_semantics_config(ws.config_path)
    decls, _ = load_declarations(ws.ledger_path, "ledger")
    deliveries = _deliveries()
    deliveries["synth.base"] = deliveries["synth.base"] + [
        DeliveryArtifact(capability_id="synth.base", kind="test", path="t.py",
                         numeric_asserts=2)]
    ledger = build_layers(
        cfg=cfg, declarations=decls, deliveries=deliveries, evidences={},
        endpoint_modules=[EndpointModule(path="backend/api/domain/synth_orphan.py",
                                         endpoint_count=2, sha256="deadbeef")],
        capability_meta=load_capability_meta(ws.ledger_path), ledger_ids=[])
    report = scan_misuse(ledger, cfg)
    kinds = {f["kind"] for f in report["findings"]}
    assert KIND_UNDERCLAIM in kinds and KIND_UNREGISTERED in kinds
    assert KIND_CLAIM_WITHOUT_DELIVERY in kinds  # synth.tool 声明 WITHHELD 但零交付
    assert report["not_supported_misuse_count"] == 2
    underclaim = next(f for f in report["findings"] if f["kind"] == KIND_UNDERCLAIM)
    assert underclaim["suggested_level"] == "MOCK"  # 有数值断言测试
    assert underclaim["severity"] == "high"

    patch = build_registry_patch(report, cfg, applies_to="sha")
    assert patch["patch_count"] == 2
    assert patch["applies_to_ledger_sha256"] == "sha"
    assert patch["patch_sha256"]

    ver = verify_patch(cfg=cfg, declarations=decls, deliveries=deliveries, evidences={},
                       endpoint_modules=[EndpointModule(path="backend/api/domain/synth_orphan.py",
                                                        endpoint_count=2, sha256="deadbeef")],
                       capability_meta=load_capability_meta(ws.ledger_path), patch=patch,
                       ledger_ids=[])
    assert ver["after"]["not_supported_misuse_count"] == 0
    assert ver["after"]["orphan_delivery_count"] == 0
    assert ver["fixed_underclaim_ids"] == ["synth.base"]
    assert ver["fixed_unregistered_ids"] == ["unregistered.synth_orphan"]
    assert ver["unfixed_ids"] == []
    patched = {t.capability_id: t for t in ver["ledger_after"].triples}
    assert patched["synth.base"].declared_level == "MOCK"
    assert patched["unregistered.synth_orphan"].origin == "patched"


def test_misuse_ignores_doc_only_delivery(tmp_path: Path):
    """只有文档/测试交付的 NOT_SUPPORTED 不算误用（避免假阳性）。"""
    ws = build_workspace(tmp_path)
    cfg = load_semantics_config(ws.config_path)
    ledger = build_layers(
        cfg=cfg, declarations=[Declaration(source="s", capability_id="synth.doc",
                                           declared_level="NOT_SUPPORTED")],
        deliveries={"synth.doc": [DeliveryArtifact(capability_id="synth.doc", kind="doc",
                                                   path="docs/x.md")]},
        evidences={}, capability_meta={}, ledger_ids=[])
    report = scan_misuse(ledger, cfg)
    assert report["not_supported_misuse_count"] == 0
    flagged = {f["capability_id"] for f in report["findings"]}
    assert "synth.doc" not in flagged  # 只有文档不构成实现 → 不报误用
    assert report["by_kind"].get(KIND_CLAIM_WITHOUT_DELIVERY, 0) >= 1


def test_graph_cycles_self_loops_missing_targets(tmp_path: Path):
    from tools.capability_semantics.tests.conftest import write_json

    p = write_json(tmp_path / "cfg.json", {"capabilities": [
        {"id": "synth.a", "depends_on": ["synth.b"]},
        {"id": "synth.b", "depends_on": ["synth.a"]},
        {"id": "synth.c", "depends_on": ["synth.c"]},
        {"id": "synth.d", "depends_on": ["synth.ghost"]},
        {"id": "synth.e", "depends_on": [], "graph_role": "tooling"},
    ]})
    cfg = load_semantics_config(p)
    g = build_graph(cfg)
    cycles = g.find_cycles()
    assert ["synth.a", "synth.b"] in [sorted_cycle for sorted_cycle in cycles]
    assert g.self_loops == ["synth.c"]
    assert g.missing_targets == [{"node": "synth.d", "missing_dependency": "synth.ghost"}]
    summary = g.summary()
    assert summary["criterion_no_cycle_ok"] is False
    assert "synth.e" in summary["orphan_domain"][0] or "synth.e" in summary["orphan_all"]
    assert set(summary["topological_order"]) == set(g.nodes)
    assert "digraph" in g.to_dot() and "flowchart" in g.to_mermaid()


def test_graph_acyclic_ok(tmp_path: Path):
    ws = build_workspace(tmp_path)
    cfg = load_semantics_config(ws.config_path)
    g = build_graph(cfg)
    summary = g.summary()
    assert summary["cycle_count"] == 0
    assert summary["criterion_no_cycle_ok"] is True
    assert summary["orphan_ratio_domain"] == 0.0  # synth.base -> synth.derived 连通
    assert summary["edge_count"] == 1
    assert summary["orphan_ratio_all_disclosed"] > 0  # tooling 节点孤立，单列披露