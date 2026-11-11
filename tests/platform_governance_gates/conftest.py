"""Fixtures for the platform governance gates suite (domain-neutral)."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.platform_governance_gates.evidence import (  # noqa: E402
    Contradiction,
    EvidenceItem,
)


def item(kind: str, ref: str, **kwargs) -> EvidenceItem:
    """Digest-addressed item whose sha256 is the hash of its ref (synthetic)."""
    sha = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    return EvidenceItem(kind=kind, ref=ref, sha256=sha, **kwargs)


@pytest.fixture()
def complete_bundle():
    from tools.platform_governance_gates.evidence import CapabilityEvidence

    return CapabilityEvidence(
        capability_id="demo.cap_complete",
        current_level="MOCK",
        items=[
            item("verification", "reports/v.json", independent=True),
            item("cross_check", "artifacts/x.json"),
            item("benchmark", "benchmarks/b.json"),
        ],
    )


@pytest.fixture()
def complete_bundle_cls():
    from tools.platform_governance_gates.evidence import CapabilityEvidence

    return CapabilityEvidence


@pytest.fixture()
def contradiction_cls():
    return Contradiction