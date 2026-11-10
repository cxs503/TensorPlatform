"""Multi-solver coupling checkpoint layer (domain-neutral platform mechanism).

Public surface
--------------
``protocol``     the unified state contract (arrays + scalars + phase + time)
                 and bit-level digest helpers
``codec``        lossless zstd (python binding or CLI) with recorded identity
``store``        atomic, chained, chunk-digested checkpoint store
``energy``       invariant ledger + boundary/drift acceptance gates
``sync``         coupling-instant bookkeeping (sync-point policy)
``rollback``     newest-first verified restart with quarantine + audit trail
``models``       three domain-neutral solver archetypes + deterministic harness
``determinism``  bit-level reproducibility probes (in-process and cross-process)
``bench_io``     save/load throughput measurement and 100 GiB extrapolation

Usage
-----
>>> from tools.multisolver_ckpt import build_harness, CheckpointStore, RestartSupervisor
>>> harness, ledger = build_harness(3)
>>> store = CheckpointStore("/tmp/run", ledger=ledger, grid=harness.grid)
>>> harness.run(100, ledger=ledger)
>>> store.save(harness.master_index, harness.snapshot())
>>> fresh, _ = build_harness(3)
>>> RestartSupervisor(store).resume_into(fresh)
"""

from __future__ import annotations

from .codec import Codec, RawCodec, ZstdCodec, available_codecs, get_codec, zstd_available
from .energy import (
    ACCEPT_REL_DRIFT,
    ACCEPT_REL_RESIDUAL,
    EnergyLedger,
    EnergyReading,
    GateReport,
    absolute_functional,
    quadratic_functional,
)
from .errors import (
    CheckpointCorruption,
    CheckpointError,
    CheckpointLayoutError,
    CheckpointUnreadable,
    CodecError,
    CodecUnavailable,
    NonSyncPointError,
    SchemaMismatch,
)
from .models import (
    CoupledHarness,
    CouplingExchange,
    KineticGrid,
    NodalField,
    ParticleSet,
    RunResult,
    SCENARIOS,
    build_harness,
    scenario_name,
    scenario_table,
)
from .protocol import (
    SCHEMA,
    ArraySolverBase,
    SolverSnapshot,
    StateBundle,
    StatefulSolver,
    array_digest,
    bundle_from_solvers,
    canonical_json,
    sha256_bytes,
    state_digest,
)
from .rollback import RestartFailed, RestartOutcome, RestartSupervisor, RollbackEvent
from .store import CheckpointStore, LoadedCheckpoint, SaveResult
from .sync import CouplingTimeGrid, SyncGuard, SyncPolicy, SyncReport, assess_sync

__all__ = [
    "ACCEPT_REL_DRIFT", "ACCEPT_REL_RESIDUAL", "ArraySolverBase", "CheckpointCorruption",
    "CheckpointError", "CheckpointLayoutError", "CheckpointStore", "CheckpointUnreadable",
    "Codec", "CodecError", "CodecUnavailable", "CoupledHarness", "CouplingExchange",
    "CouplingTimeGrid", "EnergyLedger", "EnergyReading", "GateReport", "KineticGrid",
    "LoadedCheckpoint", "NodalField", "NonSyncPointError", "ParticleSet", "RawCodec",
    "RestartFailed", "RestartOutcome", "RestartSupervisor", "RollbackEvent", "RunResult",
    "SCENARIOS", "SCHEMA", "SaveResult", "SchemaMismatch", "SolverSnapshot", "StateBundle",
    "StatefulSolver", "SyncGuard", "SyncPolicy", "SyncReport", "ZstdCodec",
    "absolute_functional", "array_digest", "assess_sync", "available_codecs",
    "build_harness", "bundle_from_solvers", "canonical_json", "get_codec", "quadratic_functional",
    "scenario_name", "scenario_table", "sha256_bytes", "state_digest", "zstd_available",
]

__version__ = "1.0.0"