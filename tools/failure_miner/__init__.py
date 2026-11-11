"""Failure signature miner: extract TRAPS candidates from failed missions.

Six signature types (config-driven, zero domain semantics):
  infra_lost / timeout / criteria_infeasible / duplicate_seed /
  physics_honest_fail / report_owed

Usage:
    python -m tools.failure_miner mine --days 30 --limit 200
    python -m tools.failure_miner classify --input failed_missions.json
    python -m tools.failure_miner cluster --input classified.json
    python -m tools.failure_miner export --output artifacts/traps_candidates.json
"""
from __future__ import annotations

__version__ = "0.1.0"
