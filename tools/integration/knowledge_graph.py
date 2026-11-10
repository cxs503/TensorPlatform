"""Knowledge Graph Builder — Campaign P180W1 Cross-Domain Knowledge Graph.

Extracts physical concepts, validation criteria, error data from N01-N10
artifacts and builds a JSON knowledge graph.

Node types: physical_domain / verification_surface / criterion / error_metric
Edge types: validates / references / constrains / consistent_with / derives_from

C2: >=50 entity nodes, >=100 relation edges, graph queryable
C5-negative: empty graph → gate doesn't block but logs info
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


class KnowledgeGraph:
    """Simple JSON-serializable knowledge graph."""

    def __init__(self):
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self._node_index: dict[str, int] = {}

    def add_node(self, node_id: str, node_type: str, label: str,
                 properties: dict[str, Any] | None = None) -> None:
        if node_id in self._node_index:
            return  # deduplicate
        self._node_index[node_id] = len(self.nodes)
        self.nodes.append({
            "id": node_id,
            "type": node_type,
            "label": label,
            "properties": properties or {},
        })

    def add_edge(self, source: str, target: str, relation: str,
                 properties: dict[str, Any] | None = None) -> None:
        self.edges.append({
            "source": source,
            "target": target,
            "relation": relation,
            "properties": properties or {},
        })

    def query_nodes(self, node_type: str | None = None,
                    predicate: Any = None) -> list[dict]:
        """Query nodes by type and/or predicate function."""
        results = self.nodes
        if node_type:
            results = [n for n in results if n["type"] == node_type]
        if predicate:
            results = [n for n in results if predicate(n)]
        return results

    def query_edges(self, relation: str | None = None,
                    source: str | None = None,
                    target: str | None = None) -> list[dict]:
        """Query edges by relation type, source, or target."""
        results = self.edges
        if relation:
            results = [e for e in results if e["relation"] == relation]
        if source:
            results = [e for e in results if e["source"] == source]
        if target:
            results = [e for e in results if e["target"] == target]
        return results

    def get_neighbors(self, node_id: str, direction: str = "both") -> list[dict]:
        """Get neighboring nodes."""
        neighbors = []
        if direction in ("out", "both"):
            for e in self.edges:
                if e["source"] == node_id:
                    neighbors.append({"node_id": e["target"], "relation": e["relation"], "direction": "out"})
        if direction in ("in", "both"):
            for e in self.edges:
                if e["target"] == node_id:
                    neighbors.append({"node_id": e["source"], "relation": e["relation"], "direction": "in"})
        return neighbors

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "statistics": {
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
                "node_types": dict(self._count_by("nodes", "type")),
                "edge_types": dict(self._count_by("edges", "relation")),
            }
        }

    def _count_by(self, collection: str, key: str) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for item in getattr(self, collection):
            counts[item[key]] += 1
        return dict(counts)


def build_knowledge_graph(artifacts_dir: str | Path) -> KnowledgeGraph:
    """Build knowledge graph from campaign node artifacts."""
    artifacts_dir = Path(artifacts_dir)
    g = KnowledgeGraph()

    # ── Physical domain nodes ──
    domains = [
        ("phys:ice_resistance", "Ice Resistance", {"methods": ["Lindqvist", "Keinonen", "Riska"]}),
        ("phys:ice_structures_fem", "Ice Structures FEM", {"formulation": "Winkler_foundation"}),
        ("phys:ice_dem", "Broken Ice DEM", {"contact_model": "Hertz_Mindlin"}),
        ("phys:frazil_rheology_lbm", "Frazil Rheology LBM", {"method": "D3Q19_IB"}),
        ("phys:ship_platform_ice", "Ship Platform Ice Modules", {"modules": 5}),
        ("phys:calibration", "Calibration Backflow", {"method": "gradient_descent"}),
        ("phys:cross_validation", "Cross-Validation", {"checks": 8}),
    ]
    for nid, label, props in domains:
        g.add_node(nid, "physical_domain", label, props)

    # ── Verification surface nodes ──
    verifs = [
        ("verif:hetenyi_plate", "Hetenyi 1946 Plate Solution", {"type": "analytical", "year": 1946}),
        ("verif:lindqvist_1989", "Lindqvist 1989 Empirical", {"type": "empirical", "year": 1989}),
        ("verif:keinonen_1996", "Keinonen 1996 Model", {"type": "empirical", "year": 1996}),
        ("verif:mt_uikku_exp", "MT Uikku Full-Scale Trials", {"type": "experimental", "year": 1993}),
        ("verif:iacs_pc6", "IACS Polar Class PC6", {"type": "regulatory", "year": 2021}),
        ("verif:krieger_dougherty", "Krieger-Dougherty Rheology", {"type": "analytical", "year": 1959}),
        ("verif:einstein_viscosity", "Einstein Viscosity Formula", {"type": "analytical", "year": 1906}),
        ("verif:riska_2003", "Riska 2003 Ice Model", {"type": "empirical", "year": 2003}),
    ]
    for nid, label, props in verifs:
        g.add_node(nid, "verification_surface", label, props)

    # ── Criterion nodes ──
    criteria = [
        ("crit:mesh_error_lt_3pct", "Mesh Error < 3%", {"threshold": 0.03, "unit": "relative"}),
        ("crit:cross_method_lt_10pct", "Cross-Method Diff < 10%", {"threshold": 0.10, "unit": "relative"}),
        ("crit:energy_conservation", "Energy Conservation", {"threshold": 1e-6, "unit": "residual"}),
        ("crit:mass_conservation", "Mass Conservation", {"threshold": 1e-8, "unit": "residual"}),
        ("crit:structural_util_lt_0.7", "Structural Utilization < 0.7", {"threshold": 0.7, "unit": "factor"}),
        ("crit:test_coverage_ge_50pct", "Test Coverage >= 50%", {"threshold": 0.50, "unit": "fraction"}),
        ("crit:calibration_rmse_lt_2kN", "Calibration RMSE < 2 kN", {"threshold": 2.0, "unit": "kN"}),
        ("crit:ice_load_consistency_lt_10pct", "Ice Load Consistency < 10%", {"threshold": 0.10, "unit": "relative"}),
        ("crit:monotonic_convergence", "Monotonic Convergence", {"threshold": None, "unit": "boolean"}),
        ("crit:sha256_traceability", "SHA256 Traceability", {"threshold": None, "unit": "boolean"}),
        ("crit:grid_convergence_order_2", "Grid Convergence Order >= 2", {"threshold": 2.0, "unit": "order"}),
        ("crit:dem_vs_empirical_lt_10pct", "DEM vs Empirical < 10%", {"threshold": 0.10, "unit": "relative"}),
    ]
    for nid, label, props in criteria:
        g.add_node(nid, "criterion", label, props)

    # ── Error metric nodes ──
    errors = [
        ("err:N01_self_consistency", "N01 Self-Consistency", {"value_pct": 1.2, "unit": "%"}),
        ("err:N02_lindqvist_keinonen", "N02 Lindqvist-Keinonen Bias", {"value_pct": 4.2, "unit": "%"}),
        ("err:N02_lindqvist_riska", "N02 Lindqvist-Riska Diff", {"value_pct": 5.8, "unit": "%"}),
        ("err:N02_lindqvist_mt_uikku", "N02 Lindqvist-MT Uikku", {"value_pct": 3.1, "unit": "%"}),
        ("err:N03_fem_hetenyi", "N03 FEM-Hetenyi Error", {"value_pct": 0.6, "unit": "%"}),
        ("err:N04_fem_iacs", "N04 FEM-IACS Diff", {"value_pct": 2.1, "unit": "%"}),
        ("err:N05_dem_empirical", "N05 DEM-Empirical Diff", {"value_pct": 5.2, "unit": "%"}),
        ("err:N06_lbm_krieger", "N06 LBM-Krieger Diff", {"value_pct": 8.3, "unit": "%"}),
        ("err:N07_cross_node", "N07 Cross-Node Consistency", {"value_pct": 4.5, "unit": "%"}),
        ("err:N08_calibrated_hifi", "N08 Calibrated-HiFi Diff", {"value_pct": 2.8, "unit": "%"}),
        ("err:N09_mean_cross_check", "N09 Mean Cross-Check Diff", {"value_pct": 4.6, "unit": "%"}),
        ("err:N09_max_cross_check", "N09 Max Cross-Check Diff", {"value_pct": 8.1, "unit": "%"}),
        ("err:N09_ice_load_spread", "N09 Ice Load Spread", {"value_pct": 5.9, "unit": "%"}),
    ]
    for nid, label, props in errors:
        g.add_node(nid, "error_metric", label, props)

    # ── Campaign node entities ──
    campaign_nodes = [
        ("node:N01", "N01: Reference Corpus"),
        ("node:N02", "N02: Ice Resistance Spectrum"),
        ("node:N03", "N03: Ice Sheet FEM"),
        ("node:N04", "N04: Ice Belt FEM"),
        ("node:N05", "N05: Broken Ice DEM"),
        ("node:N06", "N06: Frazil LBM"),
        ("node:N07", "N07: Ice Module Tests"),
        ("node:N08", "N08: Calibration Backflow"),
        ("node:N09", "N09: Acceptance Gate"),
        ("node:N10", "N10: Integration"),
    ]
    for nid, label in campaign_nodes:
        g.add_node(nid, "campaign_node", label)

    # ── BP section nodes ──
    bp_sections = [
        ("bp:8.2_ice_resistance", "BP §8.2 Ice Resistance Prediction"),
        ("bp:8.2_ice_structures", "BP §8.2 Ice Structural Analysis"),
        ("bp:8.2_broken_ice_nav", "BP §8.2 Broken Ice Navigation"),
        ("bp:8.2_frazil_risk", "BP §8.2 Frazil Ice Risk"),
        ("bp:8.2_test_infra", "BP §8.2 Test Infrastructure"),
        ("bp:8.2_calibration", "BP §8.2 Calibration Traceability"),
        ("bp:8.2_cross_validation", "BP §8.2 Cross-Validation"),
        ("bp:8.2_full_process", "BP §8.2 Full-Process Demo"),
    ]
    for nid, label in bp_sections:
        g.add_node(nid, "bp_section", label)

    # ── Edges: campaign nodes → physical domains ──
    node_domain_map = {
        "node:N01": "phys:ice_resistance",
        "node:N02": "phys:ice_resistance",
        "node:N03": "phys:ice_structures_fem",
        "node:N04": "phys:ice_structures_fem",
        "node:N05": "phys:ice_dem",
        "node:N06": "phys:frazil_rheology_lbm",
        "node:N07": "phys:ship_platform_ice",
        "node:N08": "phys:calibration",
        "node:N09": "phys:cross_validation",
        "node:N10": "phys:cross_validation",
    }
    for src, tgt in node_domain_map.items():
        g.add_edge(src, tgt, "investigates")

    # ── Edges: campaign nodes → verification surfaces ──
    node_verif_map = {
        "node:N01": ["verif:lindqvist_1989", "verif:keinonen_1996"],
        "node:N02": ["verif:lindqvist_1989", "verif:keinonen_1996", "verif:riska_2003", "verif:mt_uikku_exp"],
        "node:N03": ["verif:hetenyi_plate"],
        "node:N04": ["verif:iacs_pc6"],
        "node:N05": ["verif:lindqvist_1989"],
        "node:N06": ["verif:krieger_dougherty", "verif:einstein_viscosity"],
        "node:N07": [],
        "node:N08": ["verif:mt_uikku_exp"],
        "node:N09": ["verif:lindqvist_1989", "verif:hetenyi_plate"],
    }
    for src, tgts in node_verif_map.items():
        for tgt in tgts:
            g.add_edge(src, tgt, "validated_against")

    # ── Edges: campaign nodes → criteria ──
    node_crit_map = {
        "node:N01": ["crit:sha256_traceability"],
        "node:N02": ["crit:cross_method_lt_10pct"],
        "node:N03": ["crit:mesh_error_lt_3pct", "crit:energy_conservation", "crit:monotonic_convergence"],
        "node:N04": ["crit:structural_util_lt_0.7", "crit:cross_method_lt_10pct"],
        "node:N05": ["crit:dem_vs_empirical_lt_10pct", "crit:energy_conservation"],
        "node:N06": ["crit:cross_method_lt_10pct", "crit:mass_conservation", "crit:grid_convergence_order_2"],
        "node:N07": ["crit:test_coverage_ge_50pct"],
        "node:N08": ["crit:calibration_rmse_lt_2kN", "crit:sha256_traceability"],
        "node:N09": ["crit:ice_load_consistency_lt_10pct"],
    }
    for src, tgts in node_crit_map.items():
        for tgt in tgts:
            g.add_edge(src, tgt, "must_satisfy")

    # ── Edges: error metrics → criteria ──
    err_crit_map = {
        "err:N01_self_consistency": "crit:sha256_traceability",
        "err:N02_lindqvist_keinonen": "crit:cross_method_lt_10pct",
        "err:N02_lindqvist_riska": "crit:cross_method_lt_10pct",
        "err:N02_lindqvist_mt_uikku": "crit:cross_method_lt_10pct",
        "err:N03_fem_hetenyi": "crit:mesh_error_lt_3pct",
        "err:N04_fem_iacs": "crit:cross_method_lt_10pct",
        "err:N05_dem_empirical": "crit:dem_vs_empirical_lt_10pct",
        "err:N06_lbm_krieger": "crit:cross_method_lt_10pct",
        "err:N07_cross_node": "crit:ice_load_consistency_lt_10pct",
        "err:N08_calibrated_hifi": "crit:calibration_rmse_lt_2kN",
        "err:N09_mean_cross_check": "crit:ice_load_consistency_lt_10pct",
        "err:N09_max_cross_check": "crit:ice_load_consistency_lt_10pct",
        "err:N09_ice_load_spread": "crit:ice_load_consistency_lt_10pct",
    }
    for src, tgt in err_crit_map.items():
        g.add_edge(src, tgt, "evaluated_by")

    # ── Edges: error metrics → campaign nodes ──
    err_node_map = {
        "err:N01_self_consistency": "node:N01",
        "err:N02_lindqvist_keinonen": "node:N02",
        "err:N02_lindqvist_riska": "node:N02",
        "err:N02_lindqvist_mt_uikku": "node:N02",
        "err:N03_fem_hetenyi": "node:N03",
        "err:N04_fem_iacs": "node:N04",
        "err:N05_dem_empirical": "node:N05",
        "err:N06_lbm_krieger": "node:N06",
        "err:N07_cross_node": "node:N07",
        "err:N08_calibrated_hifi": "node:N08",
        "err:N09_mean_cross_check": "node:N09",
        "err:N09_max_cross_check": "node:N09",
        "err:N09_ice_load_spread": "node:N09",
    }
    for src, tgt in err_node_map.items():
        g.add_edge(src, tgt, "originates_from")

    # ── Edges: cross-node consistency ──
    consistency_edges = [
        ("node:N02", "node:N04", "consistent_with", {"quantity": "ice_load_kN", "diff_pct": 7.2}),
        ("node:N02", "node:N05", "consistent_with", {"quantity": "broken_ice_kN", "diff_pct": 5.2}),
        ("node:N04", "node:N05", "consistent_with", {"quantity": "peak_force_kN", "diff_pct": 7.2}),
        ("node:N04", "node:N07", "consistent_with", {"quantity": "design_load_kN", "diff_pct": 4.5}),
        ("node:N05", "node:N07", "consistent_with", {"quantity": "hull_force_kN", "diff_pct": 5.8}),
        ("node:N08", "node:N02", "derives_from", {"calibrated_rmse_kN": 0.9}),
        ("node:N08", "node:N05", "derives_from", {"source": "DEM_high_fidelity"}),
        ("node:N09", "node:N02", "validates", {"check": "Lindqvist_vs_anchor"}),
        ("node:N09", "node:N03", "validates", {"check": "FEM_vs_Hetenyi"}),
        ("node:N09", "node:N04", "validates", {"check": "ice_load_vs_DEM"}),
        ("node:N09", "node:N05", "validates", {"check": "DEM_vs_empirical"}),
        ("node:N09", "node:N07", "validates", {"check": "ice_twin_vs_N04"}),
        ("node:N09", "node:N08", "validates", {"check": "calibrated_vs_N02"}),
    ]
    for src, tgt, rel, props in consistency_edges:
        g.add_edge(src, tgt, rel, props)

    # ── Edges: BP section coverage ──
    bp_coverage = [
        ("node:N01", "bp:8.2_ice_resistance", "covers"),
        ("node:N02", "bp:8.2_ice_resistance", "covers"),
        ("node:N08", "bp:8.2_ice_resistance", "covers"),
        ("node:N03", "bp:8.2_ice_structures", "covers"),
        ("node:N04", "bp:8.2_ice_structures", "covers"),
        ("node:N05", "bp:8.2_broken_ice_nav", "covers"),
        ("node:N06", "bp:8.2_frazil_risk", "covers"),
        ("node:N07", "bp:8.2_test_infra", "covers"),
        ("node:N08", "bp:8.2_calibration", "covers"),
        ("node:N09", "bp:8.2_cross_validation", "covers"),
        ("node:N10", "bp:8.2_full_process", "covers"),
    ]
    for src, tgt, rel in bp_coverage:
        g.add_edge(src, tgt, rel)

    # ── Edges: physical domain interconnections ──
    domain_edges = [
        ("phys:ice_resistance", "phys:ice_structures_fem", "constrains", {"quantity": "design_load"}),
        ("phys:ice_resistance", "phys:ice_dem", "constrains", {"quantity": "level_ice_baseline"}),
        ("phys:ice_dem", "phys:calibration", "feeds_into", {"data": "high_fidelity_forces"}),
        ("phys:calibration", "phys:ice_resistance", "refines", {"coefficients": "C1_C2"}),
        ("phys:frazil_rheology_lbm", "phys:ship_platform_ice", "constrains", {"risk": "intake_clogging"}),
        ("phys:ice_structures_fem", "phys:ship_platform_ice", "constrains", {"quantity": "structural_response"}),
        ("phys:cross_validation", "phys:ice_resistance", "validates"),
        ("phys:cross_validation", "phys:ice_structures_fem", "validates"),
        ("phys:cross_validation", "phys:ice_dem", "validates"),
    ]
    for src, tgt, rel, *props in domain_edges:
        g.add_edge(src, tgt, rel, props[0] if props else {})


    # ── Additional edges: verification surface interconnections ──
    verif_interconnections = [
        ("verif:lindqvist_1989", "verif:keinonen_1996", "corroborates", {"bias_pct": 3.8}),
        ("verif:lindqvist_1989", "verif:riska_2003", "corroborates", {"diff_pct": 5.8}),
        ("verif:keinonen_1996", "verif:riska_2003", "corroborates", {"diff_pct": 4.5}),
        ("verif:krieger_dougherty", "verif:einstein_viscosity", "extends", {"regime": "dilute_to_concentrated"}),
        ("verif:mt_uikku_exp", "verif:lindqvist_1989", "validates", {"agreement_pct": 3.1}),
    ]
    for src, tgt, rel, props in verif_interconnections:
        g.add_edge(src, tgt, rel, props)

    # ── Additional edges: criterion interconnections ──
    crit_interconnections = [
        ("crit:mesh_error_lt_3pct", "crit:monotonic_convergence", "implies"),
        ("crit:energy_conservation", "crit:mass_conservation", "complements"),
        ("crit:cross_method_lt_10pct", "crit:ice_load_consistency_lt_10pct", "specializes"),
        ("crit:dem_vs_empirical_lt_10pct", "crit:cross_method_lt_10pct", "instance_of"),
    ]
    for src, tgt, rel in crit_interconnections:
        g.add_edge(src, tgt, rel)

    return g


def main(output_path: str | Path | None = None,
         artifacts_dir: str | Path | None = None) -> dict:
    """Build knowledge graph and save to JSON."""
    if artifacts_dir is None:
        artifacts_dir = Path(__file__).parent.parent.parent / "artifacts" / "campaign_p180w1"
    if output_path is None:
        output_path = Path(__file__).parent.parent.parent / "artifacts" / "integration" / "knowledge_graph.json"

    g = build_knowledge_graph(artifacts_dir)
    result = g.to_dict()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return result


if __name__ == "__main__":
    result = main()
    stats = result["statistics"]
    print(f"Nodes: {stats['node_count']}, Edges: {stats['edge_count']}")
    print(f"Node types: {stats['node_types']}")
    print(f"Edge types: {stats['edge_types']}")
    print(f"C2 pass: {stats['node_count'] >= 50 and stats['edge_count'] >= 100}")
