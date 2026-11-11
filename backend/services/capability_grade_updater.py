"""Capability grade updater service (p488w1-n01).

Automatically updates evidence levels based on error calculations
against configurable rules. Integrates with the CapabilityAnnotator
to process recorded API calls and determine grade transitions.

C2 acceptance: auto-updates evidence levels with configurable rules.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Evidence level ladder (consistent with capability_auto_upgrader)
LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
LEVEL_INDEX = {name: i for i, name in enumerate(LEVELS)}


@dataclass
class GradeRule:
    """Configurable rule for grade transitions.

    Attributes:
        from_level: Current evidence level
        to_level: Target evidence level
        max_error_pct: Maximum error percentage to allow transition
        min_samples: Minimum number of annotation samples required
        description: Human-readable rule description
    """
    from_level: str
    to_level: str
    max_error_pct: float
    min_samples: int = 1
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_level": self.from_level,
            "to_level": self.to_level,
            "max_error_pct": self.max_error_pct,
            "min_samples": self.min_samples,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GradeRule":
        return cls(**d)


# Default promotion rules
DEFAULT_PROMOTION_RULES: List[GradeRule] = [
    GradeRule(
        from_level="STUB",
        to_level="MOCK",
        max_error_pct=100.0,  # Any response = better than empty
        min_samples=1,
        description="Stub produces a response → promote to MOCK",
    ),
    GradeRule(
        from_level="MOCK",
        to_level="WITHHELD",
        max_error_pct=50.0,  # Some accuracy but not validated
        min_samples=3,
        description="MOCK with moderate accuracy → WITHHELD (needs validation)",
    ),
    GradeRule(
        from_level="WITHHELD",
        to_level="REAL",
        max_error_pct=15.0,  # Industry standard: <15% error acceptable
        min_samples=5,
        description="WITHHELD with <15% error across multiple samples → REAL",
    ),
]

# Default demotion rules
DEFAULT_DEMOTION_RULES: List[GradeRule] = [
    GradeRule(
        from_level="REAL",
        to_level="WITHHELD",
        max_error_pct=25.0,  # Exceeds 15% threshold
        min_samples=2,
        description="REAL with >25% error → demote to WITHHELD",
    ),
    GradeRule(
        from_level="WITHHELD",
        to_level="MOCK",
        max_error_pct=50.0,
        min_samples=2,
        description="WITHHELD with >50% error → demote to MOCK",
    ),
]


@dataclass
class GradeDecision:
    """Result of a grade evaluation."""
    capability_id: str
    current_level: str
    proposed_level: str
    action: str  # "PROMOTE", "DEMOTE", "HOLD"
    reason: str
    error_stats: Dict[str, Any]
    timestamp: str
    rule_applied: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "current_level": self.current_level,
            "proposed_level": self.proposed_level,
            "action": self.action,
            "reason": self.reason,
            "error_stats": self.error_stats,
            "timestamp": self.timestamp,
            "rule_applied": self.rule_applied,
        }


class CapabilityGradeUpdater:
    """Evaluates and updates capability evidence grades.

    Usage::

        updater = CapabilityGradeUpdater()
        updater.register_capability("resistance", current_level="WITHHELD")
        updater.record_error("resistance", error_pct=8.5)
        decision = updater.evaluate("resistance")
        if decision.action == "PROMOTE":
            updater.apply(decision)
    """

    def __init__(self, rules_path: Optional[str] = None):
        self._capabilities: Dict[str, Dict[str, Any]] = {}
        self._error_history: Dict[str, List[float]] = {}
        self._decisions: List[GradeDecision] = []
        self._promotion_rules = list(DEFAULT_PROMOTION_RULES)
        self._demotion_rules = list(DEFAULT_DEMOTION_RULES)

        if rules_path:
            self.load_rules(rules_path)

    @property
    def capabilities(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._capabilities)

    @property
    def decisions(self) -> List[GradeDecision]:
        return list(self._decisions)

    def configure_rules(
        self,
        promotion_rules: List[GradeRule],
        demotion_rules: Optional[List[GradeRule]] = None,
    ) -> None:
        """Replace default rules with custom configuration."""
        self._promotion_rules = list(promotion_rules)
        if demotion_rules is not None:
            self._demotion_rules = list(demotion_rules)

    def load_rules(self, path: str) -> None:
        """Load rules from a JSON file."""
        p = Path(path)
        if not p.exists():
            return
        data = json.loads(p.read_text())
        if "promotion_rules" in data:
            self._promotion_rules = [GradeRule.from_dict(r) for r in data["promotion_rules"]]
        if "demotion_rules" in data:
            self._demotion_rules = [GradeRule.from_dict(r) for r in data["demotion_rules"]]

    def save_rules(self, path: str) -> None:
        """Save current rules to a JSON file."""
        data = {
            "promotion_rules": [r.to_dict() for r in self._promotion_rules],
            "demotion_rules": [r.to_dict() for r in self._demotion_rules],
        }
        Path(path).write_text(json.dumps(data, indent=2))

    def register_capability(
        self,
        capability_id: str,
        current_level: str = "NOT_SUPPORTED",
        api_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a capability for grade tracking."""
        if current_level not in LEVEL_INDEX:
            raise ValueError(f"Invalid level: {current_level}")
        self._capabilities[capability_id] = {
            "current_level": current_level,
            "api_path": api_path,
            "metadata": metadata or {},
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "last_verified": None,
        }
        self._error_history[capability_id] = []

    def record_error(self, capability_id: str, error_pct: float) -> None:
        """Record an error observation for a capability."""
        if capability_id not in self._capabilities:
            raise KeyError(f"Unknown capability: {capability_id}")
        if error_pct < 0:
            raise ValueError("Error percentage cannot be negative")
        self._error_history[capability_id].append(error_pct)
        self._capabilities[capability_id]["last_verified"] = datetime.now(timezone.utc).isoformat()

    def get_error_stats(self, capability_id: str) -> Dict[str, Any]:
        """Compute error statistics for a capability."""
        errors = self._error_history.get(capability_id, [])
        if not errors:
            return {"count": 0, "mean": None, "max": None, "min": None, "std": None}

        n = len(errors)
        mean = sum(errors) / n
        max_err = max(errors)
        min_err = min(errors)
        variance = sum((e - mean) ** 2 for e in errors) / n if n > 1 else 0.0
        std = variance ** 0.5

        return {
            "count": n,
            "mean": round(mean, 4),
            "max": round(max_err, 4),
            "min": round(min_err, 4),
            "std": round(std, 4),
        }

    def evaluate(self, capability_id: str) -> GradeDecision:
        """Evaluate a capability and propose a grade decision.

        Returns a GradeDecision with action PROMOTE, DEMOTE, or HOLD.
        """
        if capability_id not in self._capabilities:
            raise KeyError(f"Unknown capability: {capability_id}")

        cap = self._capabilities[capability_id]
        current_level = cap["current_level"]
        stats = self.get_error_stats(capability_id)

        # Try promotion rules first
        for rule in self._promotion_rules:
            if rule.from_level != current_level:
                continue
            if stats["count"] < rule.min_samples:
                continue
            if stats["mean"] is not None and stats["mean"] <= rule.max_error_pct:
                return GradeDecision(
                    capability_id=capability_id,
                    current_level=current_level,
                    proposed_level=rule.to_level,
                    action="PROMOTE",
                    reason=rule.description,
                    error_stats=stats,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    rule_applied=f"promotion:{rule.from_level}->{rule.to_level}",
                )

        # Try demotion rules
        for rule in self._demotion_rules:
            if rule.from_level != current_level:
                continue
            if stats["count"] < rule.min_samples:
                continue
            if stats["mean"] is not None and stats["mean"] > rule.max_error_pct:
                return GradeDecision(
                    capability_id=capability_id,
                    current_level=current_level,
                    proposed_level=rule.to_level,
                    action="DEMOTE",
                    reason=rule.description,
                    error_stats=stats,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    rule_applied=f"demotion:{rule.from_level}->{rule.to_level}",
                )

        # No rule applies: hold
        return GradeDecision(
            capability_id=capability_id,
            current_level=current_level,
            proposed_level=current_level,
            action="HOLD",
            reason="No promotion or demotion criteria met",
            error_stats=stats,
            timestamp=datetime.now(timezone.utc).isoformat(),
            rule_applied=None,
        )

    def apply(self, decision: GradeDecision) -> None:
        """Apply a grade decision (updates current level)."""
        if decision.capability_id not in self._capabilities:
            raise KeyError(f"Unknown capability: {decision.capability_id}")

        if decision.action in ("PROMOTE", "DEMOTE"):
            self._capabilities[decision.capability_id]["current_level"] = decision.proposed_level
            self._capabilities[decision.capability_id]["last_verified"] = decision.timestamp

        self._decisions.append(decision)

    def evaluate_all(self) -> List[GradeDecision]:
        """Evaluate all registered capabilities."""
        return [self.evaluate(cid) for cid in self._capabilities]

    def status(self) -> Dict[str, Any]:
        """Return full status of all capabilities."""
        result = {}
        for cid, cap in self._capabilities.items():
            result[cid] = {
                "grade": cap["current_level"],
                "last_verified": cap["last_verified"],
                "error_history": self.get_error_stats(cid),
                "api_path": cap.get("api_path"),
            }
        return result

    def to_json(self, path: str) -> None:
        """Persist state to JSON file."""
        data = {
            "capabilities": self._capabilities,
            "error_history": self._error_history,
            "decisions": [d.to_dict() for d in self._decisions],
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str))

    @classmethod
    def from_json(cls, path: str) -> "CapabilityGradeUpdater":
        """Load state from JSON file."""
        updater = cls()
        data = json.loads(Path(path).read_text())
        updater._capabilities = data.get("capabilities", {})
        updater._error_history = data.get("error_history", {})
        return updater
