"""Test fixtures for the multi-solver checkpoint layer (m9723).

The scenarios used here are the domain-neutral archetypes from
``tools.multisolver_ckpt.models``: a discrete-velocity distribution array, a
nodal field and a particle table.  No application-domain vocabulary is used
anywhere in this tree -- the checkpoint layer only sees arrays + scalars + time.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(ROOT))

from tools.multisolver_ckpt import (  # noqa: E402
    CheckpointStore,
    CouplingTimeGrid,
    EnergyLedger,
    SyncPolicy,
    build_harness,
)


@pytest.fixture()
def grid() -> CouplingTimeGrid:
    """Master grid: 1.0 s master step, 4 sub-steps (exact binary arithmetic)."""

    return CouplingTimeGrid(dt_master=1.0, n_sub=4)


@pytest.fixture()
def harness_level3():
    harness, ledger = build_harness(3)
    return harness, ledger


def make_store(root, ledger: EnergyLedger, grid: CouplingTimeGrid, *, codec: str = "zstd",
               policy: SyncPolicy | str = SyncPolicy.STRICT, **kwargs) -> CheckpointStore:
    return CheckpointStore(root, codec=codec, level=3, ledger=ledger, grid=grid,
                           sync_policy=policy, max_workers=2, **kwargs)