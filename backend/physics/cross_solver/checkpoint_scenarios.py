"""SYNTHETIC coupling scenarios for the checkpoint acceptance harness.

The fixtures here are deliberately artificial: arrays of coordinates rotated by
orthogonal maps, coupled through the exchange bus with a one-segment lag.  They
are **not** physics models -- they exist because an orthogonal map conserves the
2-norm to floating-point rounding, which makes "energy conservation" and
"bit-level reproducibility" independently measurable without importing any
domain semantics into the platform layer.

Naming is synthetic on purpose (``wave``/``phase``/``anon`` solvers, ``q``
fields).  Real solver families register their own fields and ledger terms
through the public ``CoupledRunner`` / ``EnergyLedger`` API.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .checkpoint import CoupledRunner, CoupledSolver, EnergyLedger, ExchangeBus

SCENARIOS: dict[str, dict[str, Any]] = {
    # two solvers, deterministic, one exchange per 10 steps
    "s1_two_solver": {
        "names": ("wave", "phase"),
        "sync_interval": 10,
        "dt": 0.01,
        "n_cells": 64,
        "kappa": 0.25,
        "rng_seed": None,
        "description": "2 solvers / deterministic / sync_interval=10",
    },
    # three solvers in a coupling ring, tighter exchange
    "s2_three_solver": {
        "names": ("wave", "phase", "anon"),
        "sync_interval": 5,
        "dt": 0.005,
        "n_cells": 96,
        "kappa": 0.5,
        "rng_seed": None,
        "description": "3 solvers / deterministic / sync_interval=5",
    },
    # three solvers + stochastic rotation angle (exercises RNG-state capture)
    "s3_stochastic": {
        "names": ("wave", "phase", "anon"),
        "sync_interval": 20,
        "dt": 0.01,
        "n_cells": 64,
        "kappa": 0.35,
        "rng_seed": 20261110,
        "description": "3 solvers / stochastic RNG angle / sync_interval=20",
    },
}


def _rotation(angle: float) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s], [s, c]], dtype=np.float64)


def _initial_coordinates(n_cells: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, 1.0, size=(n_cells, 2)).astype(np.float64)


class Scenario:
    """Bundle of factories describing one coupled run (re-usable, stateless)."""

    def __init__(self, key: str):
        if key not in SCENARIOS:
            raise KeyError(f"unknown scenario {key!r}; have {sorted(SCENARIOS)}")
        self.key = key
        spec = SCENARIOS[key]
        self.names: Sequence[str] = tuple(spec["names"])
        self.sync_interval: int = int(spec["sync_interval"])
        self.dt: float = float(spec["dt"])
        self.n_cells: int = int(spec["n_cells"])
        self.kappa: float = float(spec["kappa"])
        self.rng_seed: int | None = spec["rng_seed"]
        self.description: str = spec["description"]

    # -- factories ---------------------------------------------------------- #
    def rng_factory(self) -> Callable[[], np.random.Generator] | None:
        if self.rng_seed is None:
            return None
        seed = int(self.rng_seed)
        return lambda: np.random.default_rng(seed)

    def solvers(self, rng: np.random.Generator | None = None) -> list[CoupledSolver]:
        """Build the solver list; ``rng`` must be the RUNNER's generator.

        Sharing one generator between the step kernels and the runner is what
        makes the stream position part of the checkpoint -- with a private
        generator the restart would silently reuse random numbers.
        """
        solvers: list[CoupledSolver] = []
        for index, name in enumerate(self.names):
            upstream = self.names[index - 1]           # ring coupling
            initial = _initial_coordinates(self.n_cells, seed=101 + index)
            step_fn = _make_step_fn(name, upstream, self.kappa, rng)
            solvers.append(CoupledSolver(name=name, step_fn=step_fn,
                                         fields={"q": initial}))
        return solvers

    def solvers_factory(self, rng: np.random.Generator | None = None
                        ) -> Callable[[], list[CoupledSolver]]:
        return lambda: self.solvers(rng)

    def runner(self) -> CoupledRunner:
        """Fresh runner (also usable as a ``runner_factory``)."""
        rng = (np.random.default_rng(int(self.rng_seed))
               if self.rng_seed is not None else None)
        return CoupledRunner(
            self.solvers(rng), dt=self.dt, sync_interval=self.sync_interval,
            bus=ExchangeBus(), ledger=self.ledger_factory()(),
            rng=rng, meta={"scenario": self.key},
        )

    def ledger_factory(self) -> Callable[[], EnergyLedger]:
        names = list(self.names)

        def build() -> EnergyLedger:
            ledger = EnergyLedger(tolerance=1e-10)
            for name in names:
                ledger.register(f"kinetic_{name}", f"{name}/q", coefficient=0.5,
                                reduce="sumsq")
            return ledger

        return build

def _make_step_fn(name: str, upstream: str, kappa: float,
                  rng: np.random.Generator | None):
    """Orthogonal rotation with a bus-dependent, optionally stochastic angle."""
    # NB: ``hash(str)`` is salted per process (PYTHONHASHSEED), which would make a
    # restart in a fresh process use a different frequency.  Derive the frequency
    # from a digest instead: cross-process bit-reproducibility depends on it.
    omega = 0.5 + 0.25 * (int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) % 4)
    bus_key = f"{upstream}/angle"

    def step_fn(fields: Mapping[str, np.ndarray], dt: float, bus: ExchangeBus) -> None:
        angle = omega * dt
        if kappa:
            angle = angle * (1.0 + kappa * float(bus.read(bus_key, 0.0)))
        if rng is not None:
            angle = angle + float(rng.normal(0.0, 1e-3))
        coords = fields["q"]
        coords[:] = coords @ _rotation(angle).T
        if kappa:
            bus.publish(f"{name}/angle", angle)

    return step_fn


__all__ = ["SCENARIOS", "Scenario"]