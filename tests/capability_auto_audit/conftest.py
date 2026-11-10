"""Pytest fixtures for the p262w1-N07 capability auto-audit tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from chain_fixtures import write_case  # noqa: E402


@pytest.fixture
def make_case(tmp_path):
    verified = tmp_path / "verified"
    verified.mkdir(parents=True, exist_ok=True)

    def _make(capability_id: str, **kwargs) -> Path:
        return write_case(verified, capability_id, **kwargs)

    _make.root = verified  # type: ignore[attr-defined]
    return _make


@pytest.fixture
def engine(tmp_path, make_case):
    from tools.capability_auto_audit.auto_audit_engine import AutoAuditEngine
    from tools.capability_auto_audit.evidence_chain import ChainSpec

    return AutoAuditEngine(
        verified_dir=make_case.root,
        state_path=tmp_path / "state" / "ledger_state.json",
        log_path=tmp_path / "state" / "audit_log.jsonl",
        spec=ChainSpec(),
    )