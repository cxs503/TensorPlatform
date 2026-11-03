"""Single-source status contracts shared with TensorPhysics (migration-disposition G-A/G-C).

The JSON files under backend/contracts/ are the single contract source ordered by
docs/architecture/migration_disposition_2026-11.md §3. Both repos derive their
runtime vocabularies from these files; cross-repo drift is caught by contract
tests (TensorPhysics fetches the copies published on this repo's main branch).

This loader is deliberately dependency-free (json + pathlib only): it must stay
importable from any layer without cycle risk.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"


def _load(filename: str, expected_contract: str) -> dict[str, Any]:
    path = _CONTRACTS_DIR / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("contract") != expected_contract:
        raise ValueError(f"{filename}: contract field {data.get('contract')!r} != {expected_contract!r}")
    if not isinstance(data.get("version"), int):
        raise ValueError(f"{filename}: version must be an integer")
    return data


def mission_status_contract() -> dict[str, Any]:
    """tensor_dev_missions.status vocabulary (G-A side 1)."""
    return _load("mission_status_vocab.v1.json", "mission_status_vocab")


def research_run_contract() -> dict[str, Any]:
    """Run-registration vocabulary for /tensorphysics/runs (G-C)."""
    return _load("research_run_status_vocab.v1.json", "research_run_status_vocab")


def mission_task_status_map_contract() -> dict[str, Any]:
    """TP mission status <-> TensorPhysics TaskStatus mapping (G-A)."""
    return _load("mission_task_status_map.v1.json", "mission_task_status_map")
