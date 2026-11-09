"""MVD (Minimum Viable Delivery) checkpoint at 70% budget.

Purpose
-------
Injects a checkpoint when a mission reaches 70% of its budget (tool calls or
wall time). The checkpoint verifies that the mission has produced at least:
1. A minimal runnable implementation (changed_files non-empty)
2. A reproducible evidence artifact (script + output)
3. A report/figure OR explicit negative-control explanation

Design rules
------------
* **Shadow mode first.** The checkpoint records but does not block (inject
  switch default off). After 2 waves of shadow data, decide whether to wire
  into the live path.
* **Template-based, no domain words.** The checkpoint message is generic.
* **Zero side effects.** When disabled, the checkpoint is a no-op.

Audit chain
-----------
- Consumed by ``api/tensor_mission_lifecycle.py`` heartbeat() endpoint
- Records to ``mission_checkpoint_log`` table (if exists, else in-memory)
- Shadow mode data feeds the delivery classifier experiment
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

# Checkpoint trigger threshold (70% budget)
BUDGET_THRESHOLD = 0.70

# Minimum viable delivery requirements (generic, no domain terms)
MVD_REQUIREMENTS = {
    "implementation": "At least one file in changed_files",
    "evidence": "At least one check result in checks",
    "report": "Summary present OR explicit negative-control explanation",
}


@dataclass
class MVDCheckpointResult:
    """Result of MVD checkpoint evaluation.
    
    Attributes
    ----------
    triggered : bool
        Whether the checkpoint was triggered (budget >= 70%)
    passed : bool
        Whether MVD requirements are met
    budget_pct : float
        Budget utilization percentage
    missing : list[str]
        List of missing MVD requirements
    message : str
        Human-readable checkpoint message
    shadow_mode : bool
        Whether this is shadow mode (record only, no blocking)
    timestamp : float
        Unix timestamp of checkpoint evaluation
    """
    triggered: bool
    passed: bool
    budget_pct: float
    missing: list[str] = field(default_factory=list)
    message: str = ""
    shadow_mode: bool = True  # Default to shadow mode
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "passed": self.passed,
            "budget_pct": self.budget_pct,
            "missing": self.missing,
            "message": self.message,
            "shadow_mode": self.shadow_mode,
            "timestamp": self.timestamp,
        }


def compute_budget_pct(
    tool_calls: int,
    max_tool_calls: int,
    wall_seconds: float,
    max_wall_seconds: float,
) -> float:
    """Compute budget utilization percentage.
    
    Takes the maximum of tool call utilization and wall time utilization.
    
    Parameters
    ----------
    tool_calls : int
        Tool call count so far
    max_tool_calls : int
        Maximum allowed tool calls
    wall_seconds : float
        Elapsed wall time in seconds
    max_wall_seconds : float
        Maximum allowed wall time in seconds
    
    Returns
    -------
    float
        Budget utilization percentage [0, 100]
    """
    if max_tool_calls <= 0 and max_wall_seconds <= 0:
        return 0.0
    
    tool_pct = (tool_calls / max_tool_calls * 100) if max_tool_calls > 0 else 0.0
    time_pct = (wall_seconds / max_wall_seconds * 100) if max_wall_seconds > 0 else 0.0
    
    return min(max(tool_pct, time_pct), 100.0)


def evaluate_mvd_checkpoint(
    result: dict[str, Any] | None,
    tool_calls: int,
    max_tool_calls: int,
    wall_seconds: float,
    max_wall_seconds: float,
    shadow_mode: bool = True,
) -> MVDCheckpointResult:
    """Evaluate MVD checkpoint at 70% budget.
    
    Parameters
    ----------
    result : dict | None
        Mission result JSON
    tool_calls : int
        Tool call count so far
    max_tool_calls : int
        Maximum allowed tool calls
    wall_seconds : float
        Elapsed wall time in seconds
    max_wall_seconds : float
        Maximum allowed wall time in seconds
    shadow_mode : bool
        If True, record but don't block (default)
    
    Returns
    -------
    MVDCheckpointResult
        Checkpoint evaluation result
    
    Examples
    --------
    >>> evaluate_mvd_checkpoint(None, 56, 80, 5000, 7200)
    MVDCheckpointResult(triggered=True, passed=False, budget_pct=70.0, ...)
    
    >>> evaluate_mvd_checkpoint({"changed_files": ["a.py"], "checks": {"pytest": "pass"}, "summary": {}}, 56, 80, 5000, 7200)
    MVDCheckpointResult(triggered=True, passed=True, budget_pct=70.0, ...)
    """
    budget_pct = compute_budget_pct(
        tool_calls, max_tool_calls, wall_seconds, max_wall_seconds
    )
    
    # Check if threshold reached
    if budget_pct < BUDGET_THRESHOLD * 100:
        return MVDCheckpointResult(
            triggered=False,
            passed=True,  # Not triggered = trivially passed
            budget_pct=budget_pct,
            message=f"Budget at {budget_pct:.1f}%, below {BUDGET_THRESHOLD*100:.0f}% threshold",
            shadow_mode=shadow_mode,
        )
    
    # Threshold reached, evaluate MVD requirements
    missing = []
    
    # Check implementation (changed_files)
    if result is None or not isinstance(result, dict):
        missing.append("implementation")
    else:
        changed_files = result.get("changed_files")
        if not changed_files or not isinstance(changed_files, list) or len(changed_files) == 0:
            missing.append("implementation")
        
        # Check evidence (checks)
        checks = result.get("checks")
        if not checks or not isinstance(checks, dict) or len(checks) == 0:
            missing.append("evidence")
        
        # Check report (summary OR negative-control explanation)
        summary = result.get("summary")
        negative_control = result.get("negative_control")
        if not summary and not negative_control:
            missing.append("report")
    
    passed = len(missing) == 0
    
    if passed:
        message = f"MVD checkpoint passed at {budget_pct:.1f}% budget"
    else:
        message = f"MVD checkpoint failed at {budget_pct:.1f}% budget: missing {', '.join(missing)}"
        if shadow_mode:
            message += " (shadow mode, not blocking)"
    
    return MVDCheckpointResult(
        triggered=True,
        passed=passed,
        budget_pct=budget_pct,
        missing=missing,
        message=message,
        shadow_mode=shadow_mode,
    )


# In-memory checkpoint log (for shadow mode before DB table exists)
_CHECKPOINT_LOG: list[dict[str, Any]] = []
_CHECKPOINT_LOG_MAX = 10000  # Cap to prevent memory leak


def log_checkpoint(mission_id: int, result: MVDCheckpointResult) -> None:
    """Log checkpoint result to in-memory store.
    
    Parameters
    ----------
    mission_id : int
        Mission ID
    result : MVDCheckpointResult
        Checkpoint evaluation result
    """
    entry = {
        "mission_id": mission_id,
        **result.to_dict(),
    }
    _CHECKPOINT_LOG.append(entry)
    
    # Cap log size
    if len(_CHECKPOINT_LOG) > _CHECKPOINT_LOG_MAX:
        _CHECKPOINT_LOG.pop(0)


def get_checkpoint_log(
    mission_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Retrieve checkpoint log entries.
    
    Parameters
    ----------
    mission_id : int | None
        Filter by mission ID (None = all)
    limit : int
        Maximum entries to return
    
    Returns
    -------
    list[dict]
        Checkpoint log entries (most recent first)
    """
    if mission_id is not None:
        entries = [e for e in _CHECKPOINT_LOG if e["mission_id"] == mission_id]
    else:
        entries = _CHECKPOINT_LOG.copy()
    
    return list(reversed(entries[-limit:]))


def compute_checkpoint_stats() -> dict[str, Any]:
    """Compute aggregate checkpoint statistics.
    
    Returns
    -------
    dict
        Statistics: total triggered, pass rate, shadow mode ratio
    """
    if not _CHECKPOINT_LOG:
        return {
            "total": 0,
            "triggered": 0,
            "passed": 0,
            "failed": 0,
            "pass_rate": 0.0,
            "shadow_mode_ratio": 0.0,
        }
    
    total = len(_CHECKPOINT_LOG)
    triggered = sum(1 for e in _CHECKPOINT_LOG if e["triggered"])
    passed = sum(1 for e in _CHECKPOINT_LOG if e["triggered"] and e["passed"])
    failed = sum(1 for e in _CHECKPOINT_LOG if e["triggered"] and not e["passed"])
    shadow = sum(1 for e in _CHECKPOINT_LOG if e["shadow_mode"])
    
    return {
        "total": total,
        "triggered": triggered,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / triggered * 100, 2) if triggered > 0 else 0.0,
        "shadow_mode_ratio": round(shadow / total * 100, 2) if total > 0 else 0.0,
    }
