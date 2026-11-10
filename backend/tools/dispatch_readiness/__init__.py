"""Dispatch-time interface readiness gate (P233W2-N08).

Zero-domain-semantics mechanism: every campaign node may declare an
``input_contract`` (which upstream artefacts it consumes, with required
fields / units / version) and an ``output_contract`` (what it produces).
Before a node is dispatched the gate scans the artefacts its upstream nodes
have already delivered and answers one of three states:

    READY      — every required upstream artefact is present and matches the
                 declared field / unit / version contract
    NOT_READY  — a required upstream artefact is missing (upstream node has not
                 been dispatched, or has not finished)
    CONFLICT   — the artefact exists but its declared fields / units / version
                 do not match the consumer contract

The resolver never inspects node names, goals or physics wording: everything it
knows about a node comes from the contract record plus the delivery registry.
When no contract configuration is supplied the module degrades to an identity
gate (everything READY) instead of crashing.

Public entry points
-------------------
``contracts.load_contracts`` / ``contracts.infer_contract``
``resolver.evaluate_readiness``
``gate.DispatchGate`` / ``gate.DeliveryLedger``
``backtest.run_backtest``
"""

from .models import (  # noqa: F401
    ArtifactRecord,
    ArtifactRequirement,
    Attribution,
    CONFLICT,
    InputContract,
    NOT_READY,
    OutputContract,
    READY,
    ReadinessResult,
    UpstreamState,
)
from .contracts import infer_contract, load_contracts  # noqa: F401
from .gate import DeliveryLedger, DispatchGate  # noqa: F401
from .resolver import evaluate_readiness  # noqa: F401

__all__ = [
    "ArtifactRecord",
    "ArtifactRequirement",
    "Attribution",
    "CONFLICT",
    "InputContract",
    "NOT_READY",
    "OutputContract",
    "READY",
    "ReadinessResult",
    "UpstreamState",
    "infer_contract",
    "load_contracts",
    "DispatchGate",
    "DeliveryLedger",
    "evaluate_readiness",
]

CONTRACT_VERSION = "dispatch-readiness-contract/1.0"