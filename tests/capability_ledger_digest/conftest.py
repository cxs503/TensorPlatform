"""Shared fixtures for the capability-ledger digest tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def spec_known_path() -> Path:
    return FIXTURES / "spec_known.json"


@pytest.fixture()
def gate_matrix() -> dict:
    return load_fixture("gate_matrix.json")


@pytest.fixture()
def known_spec():
    from capability_ledger_digest.spec import AuditSpec

    return AuditSpec.load(FIXTURES / "spec_known.json")