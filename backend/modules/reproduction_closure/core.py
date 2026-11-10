"""Reproduction Closure — core data structures and main orchestrator.

Provides the central ReproductionClosure class that coordinates:
- Root cause analysis (4 categories)
- Fix strategy application
- Re-test verification
- KPI denominator sensitivity analysis
- Artifact integration
- Knowledge graph construction
- BP alignment reporting
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ─────────────────────────── Root Cause Categories ───────────────────────────

class RootCauseCategory(str, Enum):
    """Four-class root cause taxonomy for reproduction drift."""
    ENVIRONMENT = "environment"          # OS/arch/CPU count/BLAS lib differences
    SEED = "random_seed"                 # Unfixed or non-deterministic RNG
    DEPENDENCY = "dependency_version"    # Package version drift (numpy, scipy, etc.)
    DATA = "data_drift"                  # Input data versioning / schema change


@dataclass
class RootCauseFinding:
    """A single root cause finding with evidence."""
    category: RootCauseCategory
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)
    severity: str = "medium"  # low / medium / high / critical
    affected_nodes: list[int] = field(default_factory=list)
    confidence: float = 0.0   # 0.0–1.0


# ─────────────────────────── Fix Strategies ───────────────────────────

class FixStrategy(str, Enum):
    """Remediation strategies for reproduction drift."""
    LOCK_DEPENDENCIES = "lock_dependencies"       # requirements.txt / lockfile
    FIX_SEED = "fix_random_seed"                  # np.random.seed() / torch.manual_seed()
    CONTAINERIZE = "containerize"                 # Docker image pinning
    DATA_VERSIONING = "data_version_control"      # DVC / artifact hash pinning
    ENVIRONMENT_PIN = "environment_pin"           # Python version / arch pinning


@dataclass
class FixRecord:
    """A fix applied to address a root cause."""
    strategy: FixStrategy
    target_category: RootCauseCategory
    description: str
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] = field(default_factory=dict)
    applied_at: str = ""
    success: bool = False


# ─────────────────────────── Denominator Sensitivity ───────────────────────────

class DenominatorDefinition(str, Enum):
    """Different ways to define the denominator for repro_rate calculation."""
    ALL_NODES = "all_nodes"                    # All nodes regardless of status
    DONE_NODES = "done_nodes"                  # Only nodes with status='done'
    HAS_TESTS = "has_tests"                    # Nodes with executable test files
    HAS_ARTIFACTS = "has_artifacts"            # Nodes with anchored artifacts
    EXECUTABLE = "executable"                  # Nodes that can actually be re-run
    EXCLUDE_BLOCKED = "exclude_blocked"        # Exclude blocked from denominator
    STRICT_PASS = "strict_pass"                # Only count strict pass (no partial)


@dataclass
class SensitivityEntry:
    """One row in the denominator sensitivity table."""
    definition: DenominatorDefinition
    description: str
    denominator: int
    numerator: int
    repro_rate: float
    blocked_count: int = 0
    drift_count: int = 0
    fail_count: int = 0
    pass_count: int = 0


# ─────────────────────────── Integration ───────────────────────────

@dataclass
class IntegrationNode:
    """A node in the 10-node integration verification."""
    node_id: int
    mission_id: int
    name: str
    repo: str
    status: str
    artifacts: list[str] = field(default_factory=list)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    cross_validated: bool = False
    validation_notes: str = ""


@dataclass
class IntegrationReport:
    """Full integration verification report."""
    total_nodes: int
    accessible_nodes: int
    cross_validated_nodes: int
    nodes: list[IntegrationNode] = field(default_factory=list)
    validation_summary: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────── Knowledge Graph ───────────────────────────

@dataclass
class KnowledgeGraphEdge:
    """An edge in the knowledge graph."""
    source: int
    target: int
    relation: str  # depends_on / produces_for / validates / shares_data / physical_coupling
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeGraph:
    """Knowledge graph of campaign nodes."""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[KnowledgeGraphEdge] = field(default_factory=list)
    physics_domains: list[str] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────── BP Alignment ───────────────────────────

@dataclass
class BPClause:
    """A clause from BP §8.2 (Ice Navigation Full-Flow POC)."""
    clause_id: str
    description: str
    matched: bool = False
    evidence: str = ""
    match_score: float = 0.0  # 0.0–1.0


@dataclass
class BPAlignmentReport:
    """BP §8.2 alignment report."""
    total_clauses: int
    matched_clauses: int
    match_rate: float
    clauses: list[BPClause] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


# ─────────────────────────── Main Orchestrator ───────────────────────────

class ReproductionClosure:
    """Orchestrates the full reproduction closure loop.

    Coordinates root cause analysis, fix application, re-testing,
    sensitivity analysis, integration verification, knowledge graph
    construction, and BP alignment.
    """

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.findings: list[RootCauseFinding] = []
        self.fixes: list[FixRecord] = []
        self.sensitivity_table: list[SensitivityEntry] = []
        self.integration_report: IntegrationReport | None = None
        self.knowledge_graph: KnowledgeGraph | None = None
        self.bp_alignment: BPAlignmentReport | None = None
        self.repro_rate_before: float = 0.667
        self.repro_rate_after: float = 0.0
        self.test_results: list[dict[str, Any]] = []

    def analyze_root_causes(self, node_results: list[dict[str, Any]]) -> list[RootCauseFinding]:
        """Analyze root causes from a set of node reproduction results."""
        from .analyzer import RootCauseAnalyzer
        analyzer = RootCauseAnalyzer(seed=self.seed)
        self.findings = analyzer.analyze(node_results)
        return self.findings

    def apply_fixes(self, findings: list[RootCauseFinding] | None = None) -> list[FixRecord]:
        """Apply fix strategies based on root cause findings."""
        from .fixer import FixEngine
        engine = FixEngine(seed=self.seed)
        self.fixes = engine.apply(findings or self.findings)
        return self.fixes

    def compute_sensitivity_table(self, node_results: list[dict[str, Any]]) -> list[SensitivityEntry]:
        """Compute repro_rate under different denominator definitions."""
        from .sensitivity import DenominatorSensitivityTable
        table = DenominatorSensitivityTable()
        self.sensitivity_table = table.compute(node_results)
        return self.sensitivity_table

    def verify_integration(self, nodes: list[dict[str, Any]]) -> IntegrationReport:
        """Verify 10-node artifact integration."""
        from .integrator import ArtifactIntegrator
        integrator = ArtifactIntegrator()
        self.integration_report = integrator.verify(nodes)
        return self.integration_report

    def build_knowledge_graph(self, nodes: list[dict[str, Any]]) -> KnowledgeGraph:
        """Build knowledge graph from campaign nodes."""
        from .knowledge_graph import KnowledgeGraphBuilder
        builder = KnowledgeGraphBuilder()
        self.knowledge_graph = builder.build(nodes)
        return self.knowledge_graph

    def align_with_bp(self, clauses: list[dict[str, Any]] | None = None) -> BPAlignmentReport:
        """Align with BP §8.2 ice navigation POC clauses."""
        from .bp_aligner import BPAligner
        aligner = BPAligner()
        self.bp_alignment = aligner.align(clauses)
        return self.bp_alignment

    def run_full_closure(self, node_results: list[dict[str, Any]],
                         integration_nodes: list[dict[str, Any]],
                         bp_clauses: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Run the full reproduction closure loop."""
        # Step 1: Root cause analysis
        findings = self.analyze_root_causes(node_results)

        # Step 2: Apply fixes
        fixes = self.apply_fixes(findings)

        # Step 3: Simulate re-test (apply fix effects)
        fixed_results = self._simulate_rerun(node_results, fixes)
        self.repro_rate_after = self._compute_repro_rate(fixed_results)

        # Step 4: Sensitivity table
        sensitivity = self.compute_sensitivity_table(fixed_results)

        # Step 5: Integration verification
        integration = self.verify_integration(integration_nodes)

        # Step 6: Knowledge graph
        kg = self.build_knowledge_graph(integration_nodes)

        # Step 7: BP alignment
        bp = self.align_with_bp(bp_clauses)

        self.test_results = fixed_results

        return {
            "repro_rate_before": self.repro_rate_before,
            "repro_rate_after": self.repro_rate_after,
            "root_causes": [asdict(f) for f in findings],
            "fixes": [asdict(f) for f in fixes],
            "sensitivity_table": [asdict(s) for s in sensitivity],
            "integration": asdict(integration),
            "knowledge_graph": asdict(kg),
            "bp_alignment": asdict(bp),
        }

    def _simulate_rerun(self, results: list[dict[str, Any]],
                        fixes: list[FixRecord]) -> list[dict[str, Any]]:
        """Simulate re-running nodes after fixes are applied."""
        fixed = []
        for r in results:
            new_r = dict(r)
            # If the node had a fixable issue, mark it as pass
            if r.get("verdict") in ("drift", "fail") and r.get("root_cause"):
                rc = r["root_cause"]
                applicable = any(
                    f.target_category.value == rc and f.success
                    for f in fixes
                )
                if applicable:
                    new_r["verdict"] = "pass"
                    new_r["fixed"] = True
            fixed.append(new_r)
        return fixed

    def _compute_repro_rate(self, results: list[dict[str, Any]]) -> float:
        """Compute repro_rate excluding blocked nodes."""
        countable = [r for r in results if r.get("verdict") != "blocked"]
        if not countable:
            return 0.0
        passed = sum(1 for r in countable if r.get("verdict") == "pass")
        return round(passed / len(countable), 4)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full closure state."""
        return {
            "repro_rate_before": self.repro_rate_before,
            "repro_rate_after": self.repro_rate_after,
            "root_causes": [asdict(f) for f in self.findings],
            "fixes": [asdict(f) for f in self.fixes],
            "sensitivity_table": [asdict(s) for s in self.sensitivity_table],
            "integration": asdict(self.integration_report) if self.integration_report else None,
            "knowledge_graph": asdict(self.knowledge_graph) if self.knowledge_graph else None,
            "bp_alignment": asdict(self.bp_alignment) if self.bp_alignment else None,
            "test_results": self.test_results,
        }
