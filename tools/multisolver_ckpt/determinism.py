"""Bit-level reproducibility probe: checkpoint/restart vs uninterrupted run.

The acceptance question this module answers is binary and unforgiving:

    does a run that was interrupted, serialised, deserialised and continued
    produce *the same sha256* as the uninterrupted run?

Nothing here is tolerance-based.  The comparison is between two digests of the
raw state bytes, so a single changed ULP anywhere in the state fails the probe.

Two restart paths are exercised, because they fail differently:

* **in-process fresh objects** -- a brand-new harness is constructed from the
  scenario declaration (no shared references with the writer), then restored
  from the checkpoint.  Catches state that lives outside the serialised
  surface (module-level caches, closure captures).
* **cross-process** -- the continuation runs in a separate interpreter via
  ``python -m tools.multisolver_ckpt``, so process-local state (hash seeds,
  allocator behaviour, thread scheduling) cannot leak into the comparison.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .energy import EnergyLedger, GateReport
from .models import build_harness, scenario_name
from .rollback import RestartOutcome, RestartSupervisor
from .store import CheckpointStore

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ReproResult:
    scenario: str
    master_steps: int
    checkpoint_at: int
    continuous_digest: str
    restarted_digest: str
    checkpoint_digest: str
    bit_identical: bool
    continuous_seconds: float = 0.0
    restart_seconds: float = 0.0
    boundary_gate: Optional[Dict[str, Any]] = None
    drift_gate: Optional[Dict[str, Any]] = None
    rollbacks: int = 0
    mode: str = "in-process"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario": self.scenario,
            "mode": self.mode,
            "master_steps": int(self.master_steps),
            "checkpoint_at": int(self.checkpoint_at),
            "continuous_digest": self.continuous_digest,
            "restarted_digest": self.restarted_digest,
            "checkpoint_digest": self.checkpoint_digest,
            "bit_identical": bool(self.bit_identical),
            "continuous_seconds": float(self.continuous_seconds),
            "restart_seconds": float(self.restart_seconds),
            "boundary_gate": self.boundary_gate,
            "drift_gate": self.drift_gate,
            "rollbacks": int(self.rollbacks),
        }


def make_store(root: str | os.PathLike[str], ledger: EnergyLedger, grid=None, **kwargs) -> CheckpointStore:
    from .sync import CouplingTimeGrid, SyncPolicy

    return CheckpointStore(
        root, ledger=ledger, grid=grid or CouplingTimeGrid(), sync_policy=SyncPolicy.STRICT,
        **kwargs,
    )


def run_continuous(level: int | str = 3, *, steps: int = 1000, damped: bool = False) -> Tuple[str, Any]:
    harness, ledger = build_harness(level, damped=damped)
    result = harness.run(steps, ledger=ledger)
    return result.final_digest, result


def run_checkpointed(level: int | str = 3, *, steps: int = 1000, at: int = 500,
                     root: str | os.PathLike[str], damped: bool = False,
                     fresh_process: bool = False) -> Tuple[str, CheckpointStore, RestartOutcome]:
    """Run ``steps`` master steps, checkpoint at ``at``, restart, continue."""

    harness, ledger = build_harness(level, damped=damped)
    store = make_store(root, ledger, grid=harness.grid)
    if at <= steps:
        harness.run(at, ledger=ledger)
        store.save(at, harness.snapshot(), label=f"{scenario_name(level)}@{at}")
    if fresh_process:
        digest = _continue_in_subprocess(level, steps, at, root, damped)
        supervisor = RestartSupervisor(store)
        outcome = _read_outcome(store)
        return digest, store, outcome
    # in-process restart: brand-new solver objects, same declaration
    fresh, fresh_ledger = build_harness(level, damped=damped)
    supervisor = RestartSupervisor(store)
    outcome = supervisor.resume_into(fresh)
    fresh.run(steps - at, ledger=fresh_ledger)
    return fresh.snapshot().digest, store, outcome


def check_reproducibility(level: int | str = 3, *, steps: int = 1000, at: int = 500,
                          root: str | os.PathLike[str] | None = None,
                          damped: bool = False, fresh_process: bool = False) -> ReproResult:
    name = scenario_name(level)
    tmp = root is None
    root = Path(root or tempfile.mkdtemp(prefix="ckpt-repro-"))
    try:
        t0 = time.perf_counter()
        continuous_digest, cont_run = run_continuous(level, steps=steps, damped=damped)
        continuous_seconds = time.perf_counter() - t0

        t0 = time.perf_counter()
        restarted_digest, store, outcome = run_checkpointed(
            level, steps=steps, at=at, root=root, damped=damped, fresh_process=fresh_process
        )
        restart_seconds = time.perf_counter() - t0

        ckpt_manifest = store.manifest_of(at)
        boundary = _boundary_gate(level, store, at, damped)
        drift = _drift_gate(cont_run)
        return ReproResult(
            scenario=name, master_steps=int(steps), checkpoint_at=int(at),
            continuous_digest=continuous_digest, restarted_digest=restarted_digest,
            checkpoint_digest=ckpt_manifest["state_digest"],
            bit_identical=continuous_digest == restarted_digest,
            continuous_seconds=continuous_seconds, restart_seconds=restart_seconds,
            boundary_gate=boundary, drift_gate=drift,
            rollbacks=len(outcome.rollbacks),
            mode="cross-process" if fresh_process else "in-process",
        )
    finally:
        if tmp:
            shutil.rmtree(root, ignore_errors=True)


def _boundary_gate(level: int | str, store: CheckpointStore, at: int, damped: bool) -> Dict[str, Any]:
    """Energy residual across the save/load boundary at step ``at``."""

    from .energy import EnergyReading

    manifest = store.manifest_of(at)
    recorded = EnergyReading.from_manifest(manifest["energy"])
    loaded = store.load(at, verify_energy=False)
    harness, ledger = build_harness(level, damped=damped)
    current = ledger.measure(loaded.bundle)
    gate: GateReport = ledger.check_boundary(recorded, current)
    return gate.to_dict()


def _drift_gate(run: Any) -> Dict[str, Any]:
    if not run.energy_readings or getattr(run, "ledger", None) is None:
        return {}
    ledger = run.ledger
    first, last = run.energy_readings[0], run.energy_readings[-1]
    return ledger.check_drift(first, last).to_dict()


def _continue_in_subprocess(level: int | str, steps: int, at: int, root: Any, damped: bool) -> str:
    """Restore + continue in a fresh interpreter, return the final digest."""

    cmd = [
        sys.executable, "-m", "tools.multisolver_ckpt", "restart-digest",
        "--scenario", str(level), "--steps", str(int(steps)), "--root", str(root),
    ] + (["--damped"] if damped else [])
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if proc.returncode != 0:
        raise RuntimeError(f"subprocess restart failed: {proc.stderr[-2000:]}")
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return payload["final_digest"]


def _read_outcome(store: CheckpointStore) -> RestartOutcome:  # pragma: no cover - helper
    loaded = store.load()
    return RestartOutcome(ok=True, step=loaded.step, digest=loaded.digest, loaded=loaded)


def repro_matrix(levels: Sequence[int | str] = (3, 2, 1), *, steps: int = 1000, at: int = 500,
                 root: str | os.PathLike[str] | None = None) -> List[Dict[str, Any]]:
    return [
        check_reproducibility(level, steps=steps, at=at, root=root).to_dict()
        for level in levels
    ]