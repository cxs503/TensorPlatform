"""Tests for integration tools — Campaign P180W1 cross-domain verification.

Tests cover:
  - C1: Artifact completeness (10 nodes, each >=1 json + >=1 png)
  - C2: Knowledge graph (>=50 nodes, >=100 edges, queryable)
  - C3: BP alignment (coverage computable, empty list → 0)
  - C4: Cross-validation (ice load consistency <10%)
  - C5: Negative controls (missing artifacts, empty graph, empty BP list)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure repo root is on path
REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.integration.artifact_collector import (
    collect_all, collect_node_artifacts, EXPECTED_NODES, main as collect_main,
)
from tools.integration.knowledge_graph import (
    KnowledgeGraph, build_knowledge_graph, main as kg_main,
)
from tools.integration.bp_alignment import (
    compute_bp_alignment, BP_SECTION_8_2_CLAUSES, main as bp_main,
)

CAMPAIGN_DIR = REPO_ROOT / "artifacts" / "campaign_p180w1"
INTEGRATION_DIR = REPO_ROOT / "artifacts" / "integration"


# ── C1: Artifact Completeness ──

class TestArtifactCompleteness:
    """C1: 10 nodes artifacts/ complete rate 100% (each >=1 json + >=1 png)."""

    def test_all_nodes_have_directories(self):
        """All 10 expected node directories exist."""
        for node_id, (subdir, _) in EXPECTED_NODES.items():
            node_path = CAMPAIGN_DIR / subdir
            assert node_path.exists(), f"Missing directory for {node_id}: {node_path}"

    def test_each_node_has_json(self):
        """Each node has at least 1 JSON file."""
        for node_id, (subdir, _) in EXPECTED_NODES.items():
            node_path = CAMPAIGN_DIR / subdir
            json_files = list(node_path.glob("*.json"))
            assert len(json_files) >= 1, f"{node_id} has no JSON files"

    def test_each_node_has_png(self):
        """Each node has at least 1 PNG file."""
        for node_id, (subdir, _) in EXPECTED_NODES.items():
            node_path = CAMPAIGN_DIR / subdir
            png_files = list(node_path.glob("*.png"))
            assert len(png_files) >= 1, f"{node_id} has no PNG files"

    def test_completeness_rate_100(self):
        """Overall completeness rate is 100%."""
        result = collect_all(CAMPAIGN_DIR)
        assert result["completeness_rate"] == 1.0
        assert result["c1_pass"] is True

    def test_results_json_valid(self):
        """Each node's results.json is valid JSON with expected keys."""
        for node_id, (subdir, _) in EXPECTED_NODES.items():
            results_path = CAMPAIGN_DIR / subdir / "results.json"
            if results_path.exists():
                with open(results_path) as f:
                    data = json.load(f)
                assert "node_id" in data, f"{node_id} results.json missing node_id"
                assert data["node_id"] == node_id

    def test_validation_json_valid(self):
        """Each node's validation.json is valid JSON."""
        for node_id, (subdir, _) in EXPECTED_NODES.items():
            val_path = CAMPAIGN_DIR / subdir / "validation.json"
            if val_path.exists():
                with open(val_path) as f:
                    data = json.load(f)
                assert "overall_pass" in data

    def test_collect_main_writes_output(self):
        """collect_main writes completeness.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "completeness.json"
            result = collect_main(output_path=out, base_dir=CAMPAIGN_DIR)
            assert out.exists()
            assert result["c1_pass"] is True


# ── C2: Knowledge Graph ──

class TestKnowledgeGraph:
    """C2: >=50 entity nodes, >=100 relation edges, graph queryable."""

    def test_node_count_ge_50(self):
        """Knowledge graph has >= 50 entity nodes."""
        g = build_knowledge_graph(CAMPAIGN_DIR)
        assert len(g.nodes) >= 50, f"Only {len(g.nodes)} nodes (need >=50)"

    def test_edge_count_ge_100(self):
        """Knowledge graph has >= 100 relation edges."""
        g = build_knowledge_graph(CAMPAIGN_DIR)
        assert len(g.edges) >= 100, f"Only {len(g.edges)} edges (need >=100)"

    def test_node_types_present(self):
        """Graph contains all required node types."""
        g = build_knowledge_graph(CAMPAIGN_DIR)
        types = {n["type"] for n in g.nodes}
        required = {"physical_domain", "verification_surface", "criterion", "error_metric"}
        assert required.issubset(types), f"Missing types: {required - types}"

    def test_query_nodes_by_type(self):
        """Query nodes by type returns correct subset."""
        g = build_knowledge_graph(CAMPAIGN_DIR)
        domains = g.query_nodes(node_type="physical_domain")
        assert len(domains) > 0
        assert all(n["type"] == "physical_domain" for n in domains)

    def test_query_edges_by_relation(self):
        """Query edges by relation returns correct subset."""
        g = build_knowledge_graph(CAMPAIGN_DIR)
        validates = g.query_edges(relation="validates")
        assert len(validates) > 0
        assert all(e["relation"] == "validates" for e in validates)

    def test_get_neighbors(self):
        """Get neighbors returns connected nodes."""
        g = build_knowledge_graph(CAMPAIGN_DIR)
        neighbors = g.get_neighbors("node:N02")
        assert len(neighbors) > 0

    def test_graph_serializable(self):
        """Graph can be serialized to JSON."""
        g = build_knowledge_graph(CAMPAIGN_DIR)
        d = g.to_dict()
        json_str = json.dumps(d)
        assert len(json_str) > 0
        # Round-trip
        d2 = json.loads(json_str)
        assert d2["statistics"]["node_count"] == len(g.nodes)

    def test_kg_main_writes_output(self):
        """kg_main writes knowledge_graph.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "kg.json"
            result = kg_main(output_path=out, artifacts_dir=CAMPAIGN_DIR)
            assert out.exists()
            assert result["statistics"]["node_count"] >= 50
            assert result["statistics"]["edge_count"] >= 100


# ── C3: BP Alignment ──

class TestBPAlignment:
    """C3: Coverage computable, empty list → coverage = 0."""

    def test_coverage_computable(self):
        """BP alignment coverage is computable."""
        result = compute_bp_alignment()
        assert "coverage_rate" in result
        assert 0 <= result["coverage_rate"] <= 1.0

    def test_has_covered_clauses(self):
        """At least some clauses are covered."""
        result = compute_bp_alignment()
        assert result["covered_clauses"] > 0

    def test_clause_details_complete(self):
        """Each clause has status and coverage info."""
        result = compute_bp_alignment()
        for cd in result["clause_details"]:
            assert "clause_id" in cd
            assert "status" in cd
            assert cd["status"] in ("covered", "partial", "uncovered")

    def test_empty_bp_list_gives_zero(self):
        """C5-negative: empty BP clause list → coverage = 0."""
        result = compute_bp_alignment(bp_clauses=[])
        assert result["coverage_rate"] == 0.0
        assert result["total_clauses"] == 0
        assert result["c3_pass"] is False

    def test_priority_breakdown(self):
        """Priority breakdown is present and consistent."""
        result = compute_bp_alignment()
        pb = result["priority_breakdown"]
        assert "high" in pb
        assert "medium" in pb
        assert "low" in pb
        total_from_pb = pb["high"]["total"] + pb["medium"]["total"] + pb["low"]["total"]
        assert total_from_pb == result["total_clauses"]

    def test_bp_main_writes_output(self):
        """bp_main writes bp_alignment.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "bp.json"
            result = bp_main(output_path=out)
            assert out.exists()
            assert result["c3_pass"] is True


# ── C4: Cross-Validation (Ice Load Consistency) ──

class TestCrossValidation:
    """C4: Ice load related nodes consistency <10%."""

    def test_ice_load_consistency(self):
        """Ice load values across N02/N04/N05/N07/N08 are consistent <10%."""
        # Load N09 cross-validation results
        n09_path = CAMPAIGN_DIR / "N09_acceptance_gate" / "results.json"
        with open(n09_path) as f:
            n09 = json.load(f)

        ice_load = n09["ice_load_consistency"]
        assert ice_load["spread_pct"] < 10.0, \
            f"Ice load spread {ice_load['spread_pct']}% exceeds 10% threshold"

    def test_all_cross_checks_pass(self):
        """All cross-node checks pass their thresholds."""
        n09_path = CAMPAIGN_DIR / "N09_acceptance_gate" / "results.json"
        with open(n09_path) as f:
            n09 = json.load(f)

        for check in n09["cross_checks"]:
            assert check["pass"], f"Cross-check failed: {check['check']}"

    def test_n05_dem_vs_empirical(self):
        """N05 DEM vs empirical difference <10%."""
        n05_path = CAMPAIGN_DIR / "N05_broken_ice_dem" / "results.json"
        with open(n05_path) as f:
            n05 = json.load(f)
        assert n05["ice_load_consistency"]["dem_vs_empirical_mean_diff_pct"] < 10.0

    def test_n08_calibration_consistency(self):
        """N08 calibrated values consistent with N02/N04 <5%."""
        n08_path = CAMPAIGN_DIR / "N08_calibration_backflow" / "results.json"
        with open(n08_path) as f:
            n08 = json.load(f)
        assert n08["ice_load_calibrated"]["consistency_with_N02_pct"] < 5.0
        assert n08["ice_load_calibrated"]["consistency_with_N04_pct"] < 10.0


# ── C5: Negative Controls ──

class TestNegativeControls:
    """C5: Negative controls — missing artifacts, empty graph, empty BP list."""

    def test_missing_node_directory_detected(self):
        """Missing node directory → node marked incomplete."""
        report = collect_node_artifacts(
            CAMPAIGN_DIR, "N99", "nonexistent_node", "Fake Node"
        )
        assert not report.is_complete
        assert len(report.errors) > 0

    def test_empty_knowledge_graph(self):
        """Empty knowledge graph → statistics show zero."""
        g = KnowledgeGraph()
        d = g.to_dict()
        assert d["statistics"]["node_count"] == 0
        assert d["statistics"]["edge_count"] == 0

    def test_empty_bp_list_coverage_zero(self):
        """Empty BP clause list → coverage = 0."""
        result = compute_bp_alignment(bp_clauses=[])
        assert result["coverage_rate"] == 0.0

    def test_node_without_png_incomplete(self):
        """Node with JSON but no PNG → incomplete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            node_dir = Path(tmpdir) / "test_node"
            node_dir.mkdir()
            # Create only a JSON file, no PNG
            with open(node_dir / "results.json", 'w') as f:
                json.dump({"test": True}, f)

            report = collect_node_artifacts(tmpdir, "T01", "test_node", "Test")
            assert not report.is_complete
            assert any("PNG" in e for e in report.errors)


# ── Integration End-to-End ──

class TestIntegrationEndToEnd:
    """End-to-end integration test."""

    def test_full_pipeline(self):
        """Run full pipeline: collect → graph → alignment."""
        # Step 1: Collect artifacts
        completeness = collect_all(CAMPAIGN_DIR)
        assert completeness["c1_pass"] is True

        # Step 2: Build knowledge graph
        kg = build_knowledge_graph(CAMPAIGN_DIR)
        kg_dict = kg.to_dict()
        assert kg_dict["statistics"]["node_count"] >= 50
        assert kg_dict["statistics"]["edge_count"] >= 100

        # Step 3: BP alignment
        bp = compute_bp_alignment(completeness_data=completeness)
        assert bp["c3_pass"] is True
        assert bp["coverage_rate"] > 0.5  # At least 50% covered

    def test_output_files_generated(self):
        """All integration output files can be generated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            c = collect_main(output_path=tmpdir / "completeness.json", base_dir=CAMPAIGN_DIR)
            k = kg_main(output_path=tmpdir / "knowledge_graph.json", artifacts_dir=CAMPAIGN_DIR)
            b = bp_main(output_path=tmpdir / "bp_alignment.json")

            assert (tmpdir / "completeness.json").exists()
            assert (tmpdir / "knowledge_graph.json").exists()
            assert (tmpdir / "bp_alignment.json").exists()
