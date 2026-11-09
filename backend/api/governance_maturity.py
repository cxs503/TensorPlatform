"""
Platform Governance Maturity Module (M3 Self-Sustaining Level)
================================================================

This module implements four governance maturity capabilities:
(a) Report Gate Enhancement: 6-element completeness check
(b) Evidence Chain Auto-Audit: sha256 provenance tracking
(c) Capability Ledger Auto-Update: automatic capability registration
(d) Self-Healing Mechanism: auto-degrade after consecutive failures

Design principles:
- Zero domain semantics (per TensorPlatform iron rule)
- All domain data injected via API/config/artifacts
- Honest FAIL preserved, never overwritten
- Every artifact gets sha256 for provenance chain
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ============================================================================
# (a) Report Gate Enhancement: 6-Element Completeness Check
# ============================================================================

class ReportElement(str, Enum):
    """Six required elements for paper-quality reports."""
    CASE_INTRO = "case_introduction"       # 案例介绍: physical background, geometry, parameters
    METHOD = "method"                       # 方法: computational method, key settings, toolchain
    RESULTS = "results"                     # 结果: numerical results with traceable data
    FIGURES = "figures"                     # 图: field contours, comparison curves
    NOTES = "notes"                         # 注意点: pitfalls, limitations, scope
    ERROR_JUDGMENT = "error_judgment"       # 误差判定: error vs baseline, explicit <3% answer


# Regex patterns for detecting each element in markdown
ELEMENT_PATTERNS = {
    ReportElement.CASE_INTRO: [
        r"(?i)(案例介绍|物理背景|problem\s+description|background|几何|geometry|参数表|parameter\s+table)",
        r"(?i)(物理问题|physical\s+problem|计算对象|computation\s+target)",
    ],
    ReportElement.METHOD: [
        r"(?i)(方法|method|流程|procedure|工具链|toolchain|计算设置|computational\s+settings)",
        r"(?i)(算法|algorithm|求解器|solver|离散|discretization)",
    ],
    ReportElement.RESULTS: [
        r"(?i)(结果|result|计算结果|computed|数值|numerical|输出|output)",
        r"(?i)(表\s*\d|table\s*\d|数据|data|收敛|convergence)",
    ],
    ReportElement.FIGURES: [
        r"(?i)(图|figure|云图|contour|曲线|curve|比较|comparison|可视化|visualization)",
    ],
    ReportElement.NOTES: [
        r"(?i)(注意|note|局限|limitation|适用|scope|范围|踩坑|pitfall|经验|experience)",
        r"(?i)(展望|outlook|future|改进|improvement|后续|next\s+step)",
    ],
    ReportElement.ERROR_JUDGMENT: [
        r"(?i)(误差|error|精度|accuracy|偏差|deviation|vs|基准|baseline|参考值|reference)",
        r"(?i)(<\s*3\s*%|小于\s*3\s*%|within\s*3\s*%|是否.*3\s*%|<\s*\d+\s*%)",
    ],
}


@dataclass
class ReportGateResult:
    """Result of report gate completeness check."""
    passed: bool
    elements_present: list[str]
    elements_missing: list[str]
    degradation_level: int  # 0=pass, 1=minor missing, 2=major missing, 3=critical missing
    details: dict[str, bool]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "elements_present": self.elements_present,
            "elements_missing": self.elements_missing,
            "degradation_level": self.degradation_level,
            "details": self.details,
            "timestamp": self.timestamp,
        }


def check_report_completeness(markdown: str, figures: list[dict] | None = None) -> ReportGateResult:
    """
    Check if a report contains all 6 required elements.
    
    Args:
        markdown: Report markdown content
        figures: List of figure metadata dicts (optional)
    
    Returns:
        ReportGateResult with pass/fail and details
    
    Degradation levels:
        0: All 6 elements present → PASS
        1: 5/6 elements present → Minor degradation (warning)
        2: 3-4/6 elements present → Major degradation (requires revision)
        3: <3/6 elements present → Critical degradation (auto-reject)
    """
    if not markdown or len(markdown.strip()) < 50:
        return ReportGateResult(
            passed=False,
            elements_present=[],
            elements_missing=[e.value for e in ReportElement],
            degradation_level=3,
            details={e.value: False for e in ReportElement},
        )
    
    figures = figures or []
    details: dict[str, bool] = {}
    
    for element in ReportElement:
        patterns = ELEMENT_PATTERNS[element]
        found = any(re.search(p, markdown) for p in patterns)
        
        # Special handling for figures: also check if figures list is non-empty
        if element == ReportElement.FIGURES and not found:
            found = len(figures) > 0
        
        details[element.value] = found
    
    present = [k for k, v in details.items() if v]
    missing = [k for k, v in details.items() if not v]
    count = len(present)
    
    # Determine degradation level
    if count == 6:
        degradation = 0
        passed = True
    elif count == 5:
        degradation = 1
        passed = False  # Still requires all 6
    elif count >= 3:
        degradation = 2
        passed = False
    else:
        degradation = 3
        passed = False
    
    return ReportGateResult(
        passed=passed,
        elements_present=present,
        elements_missing=missing,
        degradation_level=degradation,
        details=details,
    )


# ============================================================================
# (b) Evidence Chain Auto-Audit: SHA256 Provenance Tracking
# ============================================================================

@dataclass
class ProvenanceRecord:
    """A single provenance record linking artifact to its hash and dependencies."""
    artifact_id: str
    artifact_path: str
    sha256: str
    node_id: str
    mission_id: int
    upstream_deps: list[str]  # artifact_ids this depends on
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_path": self.artifact_path,
            "sha256": self.sha256,
            "node_id": self.node_id,
            "mission_id": self.mission_id,
            "upstream_deps": self.upstream_deps,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


def compute_sha256(data: bytes | str) -> str:
    """Compute SHA256 hash of data."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(filepath: str | Path) -> str:
    """Compute SHA256 hash of a file."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {filepath}")
    return compute_sha256(path.read_bytes())


class ProvenanceChain:
    """
    Manages the provenance chain for evidence tracking.
    
    Each node's output gets a sha256 hash, and dependencies on upstream
    node outputs are recorded. This creates an auditable chain from
    raw inputs to final results.
    """
    
    def __init__(self):
        self._records: dict[str, ProvenanceRecord] = {}
    
    def register_artifact(
        self,
        artifact_id: str,
        artifact_path: str,
        data: bytes | str,
        node_id: str,
        mission_id: int,
        upstream_deps: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProvenanceRecord:
        """Register a new artifact in the provenance chain."""
        sha256 = compute_sha256(data)
        record = ProvenanceRecord(
            artifact_id=artifact_id,
            artifact_path=artifact_path,
            sha256=sha256,
            node_id=node_id,
            mission_id=mission_id,
            upstream_deps=upstream_deps or [],
            metadata=metadata or {},
        )
        self._records[artifact_id] = record
        return record
    
    def verify_artifact(self, artifact_id: str, data: bytes | str) -> bool:
        """Verify that data matches the registered sha256."""
        if artifact_id not in self._records:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        expected = self._records[artifact_id].sha256
        actual = compute_sha256(data)
        return expected == actual
    
    def get_chain(self, artifact_id: str) -> list[ProvenanceRecord]:
        """Get the full dependency chain for an artifact (upstream first)."""
        if artifact_id not in self._records:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        
        chain: list[ProvenanceRecord] = []
        visited: set[str] = set()
        
        def _walk(aid: str):
            if aid in visited or aid not in self._records:
                return
            visited.add(aid)
            record = self._records[aid]
            for dep in record.upstream_deps:
                _walk(dep)
            chain.append(record)
        
        _walk(artifact_id)
        return chain
    
    def check_chain_integrity(self, artifact_id: str) -> dict[str, Any]:
        """
        Check that the entire dependency chain is intact.
        Returns audit result with any broken links.
        """
        if artifact_id not in self._records:
            return {
                "valid": False,
                "error": f"Root artifact not found: {artifact_id}",
                "broken_links": [artifact_id],
            }
        
        chain = self.get_chain(artifact_id)
        broken_links = []
        
        for record in chain:
            for dep in record.upstream_deps:
                if dep not in self._records:
                    broken_links.append({
                        "artifact": record.artifact_id,
                        "missing_dep": dep,
                    })
        
        return {
            "valid": len(broken_links) == 0,
            "chain_length": len(chain),
            "broken_links": broken_links,
            "root_sha256": self._records[artifact_id].sha256,
        }
    
    def get_all_records(self) -> list[dict[str, Any]]:
        """Get all provenance records."""
        return [r.to_dict() for r in self._records.values()]


# ============================================================================
# (c) Capability Ledger Auto-Update
# ============================================================================

class CapabilityLevel(str, Enum):
    """Capability evidence levels."""
    REAL = "REAL"           # Verified real algorithm
    MOCK = "MOCK"           # Hardcoded/random values
    STUB = "STUB"           # Empty shell
    WITHHELD = "WITHHELD"   # Insufficient evidence
    NOT_SUPPORTED = "NOT_SUPPORTED"  # Not implemented


@dataclass
class CapabilityEntry:
    """A single entry in the capability ledger."""
    capability_id: str
    name: str
    level: CapabilityLevel
    domain: str
    coverage: float  # 0.0 to 1.0
    evidence_path: str  # Path to evidence artifact
    mission_id: int
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "level": self.level.value,
            "domain": self.domain,
            "coverage": self.coverage,
            "evidence_path": self.evidence_path,
            "mission_id": self.mission_id,
            "updated_at": self.updated_at,
            "notes": self.notes,
        }


class CapabilityLedger:
    """
    Auto-updating capability ledger.
    
    When a node passes verification, its capabilities are automatically
    registered/updated in the ledger with proper evidence level marking.
    """
    
    def __init__(self):
        self._entries: dict[str, CapabilityEntry] = {}
        self._update_log: list[dict[str, Any]] = []
    
    def register_capability(
        self,
        capability_id: str,
        name: str,
        level: CapabilityLevel,
        domain: str,
        coverage: float,
        evidence_path: str,
        mission_id: int,
        notes: str = "",
    ) -> CapabilityEntry:
        """Register or update a capability entry."""
        if not 0.0 <= coverage <= 1.0:
            raise ValueError(f"Coverage must be in [0, 1], got {coverage}")
        
        entry = CapabilityEntry(
            capability_id=capability_id,
            name=name,
            level=level,
            domain=domain,
            coverage=coverage,
            evidence_path=evidence_path,
            mission_id=mission_id,
            notes=notes,
        )
        
        is_update = capability_id in self._entries
        self._entries[capability_id] = entry
        
        self._update_log.append({
            "action": "update" if is_update else "create",
            "capability_id": capability_id,
            "level": level.value,
            "mission_id": mission_id,
            "timestamp": entry.updated_at,
        })
        
        return entry
    
    def get_capability(self, capability_id: str) -> Optional[CapabilityEntry]:
        """Get a capability entry by ID."""
        return self._entries.get(capability_id)
    
    def get_all_capabilities(self) -> list[dict[str, Any]]:
        """Get all capability entries."""
        return [e.to_dict() for e in self._entries.values()]
    
    def get_capabilities_by_level(self, level: CapabilityLevel) -> list[dict[str, Any]]:
        """Get all capabilities at a specific evidence level."""
        return [e.to_dict() for e in self._entries.values() if e.level == level]
    
    def get_coverage_summary(self) -> dict[str, Any]:
        """Get coverage summary by domain and level."""
        by_domain: dict[str, dict[str, int]] = {}
        by_level: dict[str, int] = {lvl.value: 0 for lvl in CapabilityLevel}
        
        for entry in self._entries.values():
            domain = entry.domain
            if domain not in by_domain:
                by_domain[domain] = {lvl.value: 0 for lvl in CapabilityLevel}
            by_domain[domain][entry.level.value] += 1
            by_level[entry.level.value] += 1
        
        total = len(self._entries)
        real_count = by_level.get(CapabilityLevel.REAL.value, 0)
        
        return {
            "total_capabilities": total,
            "by_level": by_level,
            "by_domain": by_domain,
            "real_coverage_ratio": real_count / total if total > 0 else 0.0,
        }
    
    def get_update_log(self) -> list[dict[str, Any]]:
        """Get the audit log of all ledger updates."""
        return self._update_log.copy()


# ============================================================================
# (d) Self-Healing Mechanism: Auto-Degrade on Consecutive Failures
# ============================================================================

class DegradationStrategy(str, Enum):
    """Available degradation strategies."""
    REDUCE_PRECISION = "reduce_precision"       # Lower accuracy requirements
    SWITCH_SOLVER = "switch_solver"             # Try alternative solver
    REDUCE_SCOPE = "reduce_scope"               # Narrow parameter range
    SKIP_VALIDATION = "skip_validation"         # Skip expensive validation steps


@dataclass
class FailureRecord:
    """Record of a node failure."""
    node_id: str
    mission_id: int
    error_type: str
    error_message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    attempt: int = 1


@dataclass
class DegradationAction:
    """Record of a degradation action taken."""
    node_id: str
    mission_id: int
    strategy: DegradationStrategy
    reason: str
    parameters: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    success: bool = False


class SelfHealingManager:
    """
    Manages self-healing for nodes that fail consecutively.
    
    After N consecutive failures (default N=3), automatically applies
    degradation strategies to try to recover. Tracks whether degradation
    leads to success.
    """
    
    DEFAULT_THRESHOLD = 3
    
    def __init__(self, threshold: int = DEFAULT_THRESHOLD):
        self.threshold = threshold
        self._failure_counts: dict[str, int] = {}  # node_id -> consecutive failure count
        self._failure_history: dict[str, list[FailureRecord]] = {}
        self._degradation_history: list[DegradationAction] = []
        self._degraded_nodes: dict[str, DegradationStrategy] = {}  # node_id -> current strategy
        self._recovery_success: dict[str, bool] = {}  # node_id -> recovered after degradation
    
    def record_failure(
        self,
        node_id: str,
        mission_id: int,
        error_type: str,
        error_message: str,
    ) -> FailureRecord:
        """Record a node failure and increment consecutive count."""
        if node_id not in self._failure_history:
            self._failure_history[node_id] = []
            self._failure_counts[node_id] = 0
        
        self._failure_counts[node_id] += 1
        attempt = self._failure_counts[node_id]
        
        record = FailureRecord(
            node_id=node_id,
            mission_id=mission_id,
            error_type=error_type,
            error_message=error_message,
            attempt=attempt,
        )
        self._failure_history[node_id].append(record)
        
        return record
    
    def record_success(self, node_id: str) -> None:
        """Record a node success, resetting consecutive failure count."""
        was_degraded = node_id in self._degraded_nodes
        self._failure_counts[node_id] = 0
        
        if was_degraded:
            self._recovery_success[node_id] = True
    
    def should_degrade(self, node_id: str) -> bool:
        """Check if a node should be degraded based on failure count."""
        count = self._failure_counts.get(node_id, 0)
        return count >= self.threshold
    
    def get_degradation_strategy(
        self,
        node_id: str,
        available_strategies: list[DegradationStrategy] | None = None,
    ) -> Optional[DegradationStrategy]:
        """
        Determine the next degradation strategy to apply.
        
        Strategies are applied in order of least to most aggressive:
        1. reduce_precision
        2. switch_solver
        3. reduce_scope
        4. skip_validation
        """
        if not self.should_degrade(node_id):
            return None
        
        if available_strategies is None:
            available_strategies = list(DegradationStrategy)
        
        # Get strategies not yet tried for this node
        current = self._degraded_nodes.get(node_id)
        if current is None:
            # First degradation: use least aggressive available
            for strategy in [DegradationStrategy.REDUCE_PRECISION,
                           DegradationStrategy.SWITCH_SOLVER,
                           DegradationStrategy.REDUCE_SCOPE,
                           DegradationStrategy.SKIP_VALIDATION]:
                if strategy in available_strategies:
                    return strategy
        else:
            # Escalate to next strategy
            strategy_order = list(DegradationStrategy)
            current_idx = strategy_order.index(current)
            for strategy in strategy_order[current_idx + 1:]:
                if strategy in available_strategies:
                    return strategy
        
        return None
    
    def apply_degradation(
        self,
        node_id: str,
        mission_id: int,
        strategy: DegradationStrategy,
        parameters: dict[str, Any] | None = None,
    ) -> DegradationAction:
        """Apply a degradation strategy to a node."""
        action = DegradationAction(
            node_id=node_id,
            mission_id=mission_id,
            strategy=strategy,
            reason=f"Consecutive failures >= {self.threshold}",
            parameters=parameters or {},
        )
        
        self._degraded_nodes[node_id] = strategy
        self._degradation_history.append(action)
        
        return action
    
    def get_node_status(self, node_id: str) -> dict[str, Any]:
        """Get the current status of a node."""
        return {
            "node_id": node_id,
            "consecutive_failures": self._failure_counts.get(node_id, 0),
            "is_degraded": node_id in self._degraded_nodes,
            "current_strategy": self._degraded_nodes.get(node_id),
            "recovered": self._recovery_success.get(node_id, False),
            "total_failures": len(self._failure_history.get(node_id, [])),
        }
    
    def get_all_degraded_nodes(self) -> list[dict[str, Any]]:
        """Get status of all degraded nodes."""
        return [
            self.get_node_status(nid)
            for nid in self._degraded_nodes
        ]
    
    def get_degradation_history(self) -> list[dict[str, Any]]:
        """Get full degradation action history."""
        return [
            {
                "node_id": a.node_id,
                "mission_id": a.mission_id,
                "strategy": a.strategy.value,
                "reason": a.reason,
                "parameters": a.parameters,
                "timestamp": a.timestamp,
                "success": a.success,
            }
            for a in self._degradation_history
        ]


# ============================================================================
# Integrated Governance Manager
# ============================================================================

class GovernanceManager:
    """
    Integrated governance manager combining all four capabilities.
    
    This is the main entry point for platform governance operations.
    """
    
    def __init__(self):
        self.provenance = ProvenanceChain()
        self.ledger = CapabilityLedger()
        self.healing = SelfHealingManager()
    
    def process_node_output(
        self,
        node_id: str,
        mission_id: int,
        artifact_id: str,
        artifact_path: str,
        data: bytes | str,
        upstream_deps: list[str] | None = None,
        capabilities: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Process a node's output: register provenance and update capabilities.
        
        Args:
            node_id: DAG node identifier
            mission_id: Mission ID
            artifact_id: Unique artifact identifier
            artifact_path: Path to artifact file
            data: Artifact content (for sha256 computation)
            upstream_deps: List of upstream artifact IDs
            capabilities: List of capability dicts to register
        
        Returns:
            Processing result with provenance and capability updates
        """
        # Register in provenance chain
        prov_record = self.provenance.register_artifact(
            artifact_id=artifact_id,
            artifact_path=artifact_path,
            data=data,
            node_id=node_id,
            mission_id=mission_id,
            upstream_deps=upstream_deps,
        )
        
        # Update capabilities if provided
        capability_updates = []
        if capabilities:
            for cap in capabilities:
                entry = self.ledger.register_capability(
                    capability_id=cap["capability_id"],
                    name=cap["name"],
                    level=CapabilityLevel(cap.get("level", "REAL")),
                    domain=cap.get("domain", "unknown"),
                    coverage=cap.get("coverage", 0.0),
                    evidence_path=artifact_path,
                    mission_id=mission_id,
                    notes=cap.get("notes", ""),
                )
                capability_updates.append(entry.to_dict())
        
        # Record success (reset failure counter)
        self.healing.record_success(node_id)
        
        return {
            "provenance": prov_record.to_dict(),
            "capability_updates": capability_updates,
            "chain_integrity": self.provenance.check_chain_integrity(artifact_id),
        }
    
    def handle_node_failure(
        self,
        node_id: str,
        mission_id: int,
        error_type: str,
        error_message: str,
    ) -> dict[str, Any]:
        """
        Handle a node failure with potential self-healing.
        
        Returns:
            Failure handling result with degradation recommendation
        """
        # Record the failure
        failure = self.healing.record_failure(
            node_id=node_id,
            mission_id=mission_id,
            error_type=error_type,
            error_message=error_message,
        )
        
        # Check if degradation is needed
        should_degrade = self.healing.should_degrade(node_id)
        recommended_strategy = None
        degradation_action = None
        
        if should_degrade:
            recommended_strategy = self.healing.get_degradation_strategy(node_id)
            if recommended_strategy:
                degradation_action = self.healing.apply_degradation(
                    node_id=node_id,
                    mission_id=mission_id,
                    strategy=recommended_strategy,
                )
        
        return {
            "failure": {
                "node_id": failure.node_id,
                "mission_id": failure.mission_id,
                "error_type": failure.error_type,
                "attempt": failure.attempt,
                "timestamp": failure.timestamp,
            },
            "should_degrade": should_degrade,
            "recommended_strategy": recommended_strategy.value if recommended_strategy else None,
            "degradation_applied": degradation_action is not None,
            "node_status": self.healing.get_node_status(node_id),
        }
    
    def audit_report(self, markdown: str, figures: list[dict] | None = None) -> dict[str, Any]:
        """Audit a report for completeness."""
        result = check_report_completeness(markdown, figures)
        return result.to_dict()
    
    def full_audit(self) -> dict[str, Any]:
        """Run a full governance audit."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provenance": {
                "total_artifacts": len(self.provenance._records),
                "records": self.provenance.get_all_records(),
            },
            "capabilities": self.ledger.get_coverage_summary(),
            "self_healing": {
                "degraded_nodes": self.healing.get_all_degraded_nodes(),
                "degradation_history": self.healing.get_degradation_history(),
            },
        }


# ============================================================================
# FastAPI Router (optional, for integration with platform API)
# ============================================================================

try:
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, Field
    
    router = APIRouter(prefix="/governance", tags=["Platform Governance"])
    
    # Singleton instance
    _governance = GovernanceManager()
    
    class ReportAuditRequest(BaseModel):
        markdown: str = Field(min_length=10)
        figures: list[dict] = Field(default_factory=list)
    
    class NodeOutputRequest(BaseModel):
        node_id: str
        mission_id: int
        artifact_id: str
        artifact_path: str
        data: str
        upstream_deps: list[str] = Field(default_factory=list)
        capabilities: list[dict] = Field(default_factory=list)
    
    class NodeFailureRequest(BaseModel):
        node_id: str
        mission_id: int
        error_type: str
        error_message: str
    
    @router.post("/audit/report")
    def audit_report(body: ReportAuditRequest):
        """Audit a report for 6-element completeness."""
        return _governance.audit_report(body.markdown, body.figures)
    
    @router.post("/provenance/register")
    def register_artifact(body: NodeOutputRequest):
        """Register an artifact in the provenance chain."""
        return _governance.process_node_output(
            node_id=body.node_id,
            mission_id=body.mission_id,
            artifact_id=body.artifact_id,
            artifact_path=body.artifact_path,
            data=body.data,
            upstream_deps=body.upstream_deps,
            capabilities=body.capabilities,
        )
    
    @router.post("/healing/failure")
    def report_failure(body: NodeFailureRequest):
        """Report a node failure for self-healing."""
        return _governance.handle_node_failure(
            node_id=body.node_id,
            mission_id=body.mission_id,
            error_type=body.error_type,
            error_message=body.error_message,
        )
    
    @router.get("/ledger/capabilities")
    def get_capabilities():
        """Get all capabilities from the ledger."""
        return _governance.ledger.get_all_capabilities()
    
    @router.get("/ledger/summary")
    def get_capability_summary():
        """Get capability coverage summary."""
        return _governance.ledger.get_coverage_summary()
    
    @router.get("/audit/full")
    def full_audit():
        """Run full governance audit."""
        return _governance.full_audit()

except ImportError:
    # FastAPI not available; module still usable as library
    router = None
