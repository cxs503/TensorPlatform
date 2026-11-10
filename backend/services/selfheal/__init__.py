"""Campaign DAG self-healing & elastic node scheduling (TensorPlatform).

Domain-neutral building blocks used by the campaign executor:

* ``policy``  — retry/backoff, resource-saturation and degradation policies
* ``dag``     — node graph with cycle validation and dependency semantics
* ``audit``   — tamper-evident, hash-chained self-healing audit trail
* ``engine``  — the healing state machine (retry / bypass / degrade / schedule)
* ``store``   — persistence adapter for ``campaign_nodes`` / ``campaign_events``

Nothing in this package knows about a scientific domain: node payloads are
opaque, so any campaign (or external caller) can plug in its own executors.
"""
from __future__ import annotations

from .audit import AuditChain, AuditEvent, HEAL_ACTIONS, verify_event_dicts
from .dag import (
    CycleError,
    DagNode,
    NodeStatus,
    SelfHealingDag,
    UnknownDependency,
)
from .engine import SelfHealEngine
from .policy import (
    DegradationPolicy,
    LoadSample,
    ResourcePolicy,
    RetryPolicy,
    SelfHealPolicy,
)

__all__ = [
    "AuditChain",
    "AuditEvent",
    "HEAL_ACTIONS",
    "verify_event_dicts",
    "CycleError",
    "DagNode",
    "NodeStatus",
    "SelfHealingDag",
    "UnknownDependency",
    "SelfHealEngine",
    "DegradationPolicy",
    "LoadSample",
    "ResourcePolicy",
    "RetryPolicy",
    "SelfHealPolicy",
]