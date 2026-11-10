"""Domain-neutral solver archetypes and a deterministic coupled harness.

Why archetypes at all
---------------------
A checkpoint layer is only as honest as the states it is tested on.  Three
shapes cover essentially every coupled-code state layout in this ecosystem:

``KineticGrid``    a *discrete-velocity distribution array* ``f[nx, ny, q]``
                   advanced by exact streaming plus an optional BGK relaxation.
                   It stands for any solver whose state is a per-cell vector of
                   directional populations.
``NodalField``     a *nodal field pair* ``(u, v)`` advanced by an exact
                   orthogonal (rotation) update; nodal/FE-style state.
``ParticleSet``    a *particle table* ``x[m, 3]``, ``v[m, 3]``; DEM-style state.

All three are pure ``float64`` numpy, no randomness after construction, so a
restart can be required to reproduce the bit pattern exactly.

They are also *deliberately constructed invariants*: streaming is a permutation
(no arithmetic), the nodal update is orthogonal, the particle velocity update is
a pairwise rotation and the coupling exchanges are pairwise rotations between
equal-size patches of two solvers.  Therefore

    E_total = Σ_terms ‖array‖²

is invariant up to floating-point rounding, which is what makes an energy
residual of exactly ``0`` across a save/restore boundary a meaningful result
rather than a tautology: the ledger *can* see a change -- :func:`build_harness`
with ``damped=True`` relaxes the distribution towards equilibrium and the same
ledger reports a multi-percent drift (negative control, see the report).

Coupling discipline
-------------------
Exchanges happen only at master boundaries, in a fixed (sorted) order, and are
pairwise orthogonal rotations between two equal-length patches.  The rotation
mixes energy *between* solvers (so the per-solver sub-ledgers move) while
leaving the total unchanged -- the precise situation a coupled-run ledger has to
be able to audit.

This module contains no physics of any particular application field: only
arrays, a step update and a conservation statement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .energy import EnergyLedger, EnergyReading, quadratic_functional
from .protocol import ArraySolverBase, StateBundle, bundle_from_solvers
from .store import CheckpointStore, SaveResult
from .sync import CouplingTimeGrid, assess_sync, SyncReport

# D2Q9 velocity set (cx, cy) and lattice weights -- standard discrete-velocity
# bookkeeping, identical for every application of the archetype.
D2Q9 = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))
D2Q9_W = (4.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0,
          1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0)


class KineticGrid(ArraySolverBase):
    """Discrete-velocity distribution array: exact streaming + optional BGK.

    ``omega == 0`` keeps streaming only, i.e. a pure permutation of the state
    (exact conservation of every symmetric functional).  ``omega > 0`` adds a
    BGK relaxation ``f += omega (f_eq - f)``, which dissipates -- the negative
    control that proves the ledger detects drift.
    """

    def __init__(self, name: str = "kinetic", *, nx: int = 16, ny: int = 16,
                 seed: int = 11, omega: float = 0.0, perturb: float = 0.02,
                 steps_per_master: int = 4, dt: float = 0.25, time: float = 0.0) -> None:
        if nx < 2 or ny < 2:
            raise ValueError("KineticGrid needs nx, ny >= 2 (periodic streaming)")
        if not 0.0 <= float(omega) < 2.0:
            raise ValueError(f"BGK omega must be in [0, 2), got {omega!r}")
        rng = np.random.default_rng(int(seed))
        base = np.asarray(D2Q9_W, dtype=np.float64)
        f = np.empty((int(nx), int(ny), len(D2Q9)), dtype=np.float64)
        f[...] = base[None, None, :]
        noise = rng.standard_normal(f.shape)
        f = f * (1.0 + float(perturb) * noise)
        super().__init__(
            name, {"f": f, "cx": np.asarray([c[0] for c in D2Q9], dtype=np.int64),
                   "cy": np.asarray([c[1] for c in D2Q9], dtype=np.int64),
                   "w": base.copy()},
            scalars={"omega": float(omega), "nx": int(nx), "ny": int(ny),
                     "collisions": 0},
            steps_per_master=steps_per_master, dt=dt, time=time,
        )

    # -- dynamics --------------------------------------------------------- #
    def advance(self, dt: float) -> None:
        f = self.arrays["f"]
        for i, (cx, cy) in enumerate(D2Q9):
            if cx or cy:
                f[:, :, i] = np.roll(np.roll(f[:, :, i], cy, axis=1), cx, axis=0)
        omega = float(self.scalars["omega"])
        if omega > 0.0:
            f += omega * (self._equilibrium() - f)
            self.scalars["collisions"] = int(self.scalars["collisions"]) + 1

    def _equilibrium(self) -> np.ndarray:
        f = self.arrays["f"]
        w = self.arrays["w"]
        rho = f.sum(axis=2)
        ux = (f * self.arrays["cx"]).sum(axis=2) / rho
        uy = (f * self.arrays["cy"]).sum(axis=2) / rho
        cu = ux[:, :, None] * self.arrays["cx"] + uy[:, :, None] * self.arrays["cy"]
        usq = ux ** 2 + uy ** 2
        return w[None, None, :] * rho[:, :, None] * (
            1.0 + 3.0 * cu + 4.5 * cu ** 2 - 1.5 * usq[:, :, None]
        )


class NodalField(ArraySolverBase):
    """Nodal field pair ``(u, v)`` with an exact orthogonal (modal) update."""

    def __init__(self, name: str = "nodal", *, nodes: int = 64, seed: int = 23,
                 steps_per_master: int = 2, dt: float = 0.5, time: float = 0.0,
                 omega_max: float = 1.0) -> None:
        if nodes < 4:
            raise ValueError("NodalField needs at least 4 nodes")
        rng = np.random.default_rng(int(seed))
        n = int(nodes)
        u = rng.standard_normal(n) * 0.1
        v = rng.standard_normal(n) * 0.1
        w = np.linspace(0.5 * omega_max, omega_max, n)
        super().__init__(
            name, {"u": u, "v": v, "w": w},
            scalars={"nodes": n, "updates": 0},
            steps_per_master=steps_per_master, dt=dt, time=time,
        )

    def advance(self, dt: float) -> None:
        u = self.arrays["u"]
        v = self.arrays["v"]
        theta = self.arrays["w"] * float(dt)
        c = np.cos(theta)
        s = np.sin(theta)
        un = c * u - s * v
        vn = s * u + c * v
        u[...] = un
        v[...] = vn
        self.scalars["updates"] = int(self.scalars["updates"]) + 1


class ParticleSet(ArraySolverBase):
    """Particle table ``x[m,3]``, ``v[m,3]`` with a conservative velocity update."""

    def __init__(self, name: str = "particles", *, count: int = 16, seed: int = 37,
                 steps_per_master: int = 1, dt: float = 1.0, time: float = 0.0,
                 angle: float = 0.05) -> None:
        if count < 2:
            raise ValueError("ParticleSet needs at least 2 particles")
        rng = np.random.default_rng(int(seed))
        m = int(count)
        x = rng.standard_normal((m, 3)) * 0.05
        v = rng.standard_normal((m, 3)) * 0.1
        super().__init__(
            name, {"x": x, "v": v}, scalars={"count": m, "angle": float(angle),
                                             "moves": 0},
            steps_per_master=steps_per_master, dt=dt, time=time,
        )

    def advance(self, dt: float) -> None:
        x = self.arrays["x"]
        v = self.arrays["v"]
        x += float(dt) * v
        # pairwise rotation on (v[i], v[m-1-i]) : preserves Σ|v|² exactly (up to
        # rounding) and keeps the table deterministic.
        angle = float(self.scalars["angle"])
        c = math.cos(angle)
        s = math.sin(angle)
        m = v.shape[0]
        for i in range(m // 2):
            j = m - 1 - i
            vi = v[i].copy()
            vj = v[j].copy()
            v[i] = c * vi - s * vj
            v[j] = s * vi + c * vj
        self.scalars["moves"] = int(self.scalars["moves"]) + 1


# ───────────────────────────────────────────────────────────────────────────
# Coupling exchange
# ───────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CouplingExchange:
    """Pairwise orthogonal rotation between two equal-length state patches.

    Energy moves between the two solvers; the sum of the two squared norms is
    unchanged (up to rounding).  That is the minimal, auditable stand-in for a
    real interface operator: it is the *bookkeeping* the checkpoint layer has to
    be able to freeze and thaw, not the physics.
    """

    src: str
    src_array: str
    dst: str
    dst_array: str
    n: int
    angle: float
    src_offset: int = 0
    dst_offset: int = 0

    def describe(self) -> Dict[str, Any]:
        return {
            "src": f"{self.src}/{self.src_array}", "dst": f"{self.dst}/{self.dst_array}",
            "n": self.n, "angle": self.angle,
            "src_offset": self.src_offset, "dst_offset": self.dst_offset,
        }

    def apply(self, solvers: Mapping[str, ArraySolverBase]) -> None:
        a = solvers[self.src].arrays[self.src_array]
        b = solvers[self.dst].arrays[self.dst_array]
        if not (a.flags["C_CONTIGUOUS"] and b.flags["C_CONTIGUOUS"]):
            raise ValueError("coupling patches require C-contiguous solver arrays")
        if self.src == self.dst and self.src_array == self.dst_array:
            raise ValueError("an exchange needs two distinct patches")
        for arr, off in ((a, self.src_offset), (b, self.dst_offset)):
            if off < 0 or off + self.n > arr.size:
                raise ValueError(
                    f"patch [{off}:{off + self.n}] outside array of size {arr.size} "
                    f"({self.src}/{self.src_array})"
                )
        av = a.reshape(-1)[self.src_offset:self.src_offset + self.n]
        bv = b.reshape(-1)[self.dst_offset:self.dst_offset + self.n]
        c = math.cos(self.angle)
        s = math.sin(self.angle)
        an = c * av - s * bv
        bn = s * av + c * bv
        av[...] = an
        bv[...] = bn


# ───────────────────────────────────────────────────────────────────────────
# Coupled harness
# ───────────────────────────────────────────────────────────────────────────
@dataclass
class RunResult:
    scenario: str
    master_steps: int
    final_digest: str
    energy_readings: List[EnergyReading] = field(default_factory=list)
    checkpoints: List[SaveResult] = field(default_factory=list)
    seconds: float = 0.0
    ledger: Optional["EnergyLedger"] = None

    @property
    def first_energy(self) -> Optional[EnergyReading]:
        return self.energy_readings[0] if self.energy_readings else None

    @property
    def last_energy(self) -> Optional[EnergyReading]:
        return self.energy_readings[-1] if self.energy_readings else None


class CoupledHarness:
    """Deterministic master-clock driver for a set of coupled solvers."""

    def __init__(self, solvers: Sequence[ArraySolverBase], grid: CouplingTimeGrid,
                 exchanges: Iterable[CouplingExchange] = (), *,
                 name: str = "coupled") -> None:
        if not solvers:
            raise ValueError("a harness needs at least one solver")
        self.grid = grid
        self.name = str(name)
        names = [s.name for s in solvers]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate solver names {names}")
        self.solvers: Dict[str, ArraySolverBase] = {s.name: s for s in solvers}
        self.order: Tuple[str, ...] = tuple(sorted(self.solvers))
        for s in self.solvers.values():
            expected = grid.sub_dt(s.steps_per_master)
            if abs(s.dt - expected) > 0:
                raise ValueError(
                    f"solver {s.name!r} dt={s.dt!r} does not match grid sub_dt={expected!r}"
                )
            if s.time != 0.0:
                raise ValueError(f"solver {s.name!r} must start at t=0, got {s.time!r}")
        self.exchanges: Tuple[CouplingExchange, ...] = tuple(exchanges)
        for ex in self.exchanges:
            if ex.src not in self.solvers or ex.dst not in self.solvers:
                raise ValueError(f"exchange references unknown solver: {ex.describe()}")
        self.master_index = 0
        self.sub_index = 0
        self._sync_phases()

    # -- state ------------------------------------------------------------ #
    def snapshot(self) -> StateBundle:
        return bundle_from_solvers(self.master_index, self.solvers.values())

    def restore(self, bundle: StateBundle) -> None:
        if set(bundle.names()) != set(self.solvers):
            raise ValueError(
                f"bundle solvers {sorted(bundle.names())} != harness solvers {sorted(self.solvers)}"
            )
        for name in self.order:
            self.solvers[name].restore(bundle.snapshot(name))
        self.master_index = int(bundle.step)
        # a restored state is a coupling instant by construction (STRICT save)
        self.sub_index = 0
        self._sync_phases()

    def _sync_phases(self) -> None:
        """Project each solver's logical time onto the sub-step grid (exact)."""

        dt_sub = self.grid.dt_sub
        for s in self.solvers.values():
            offset = (s.time - self.master_index * self.grid.dt_master) / dt_sub
            rounded = int(round(offset))
            if abs(offset - rounded) > 1e-9:
                raise ValueError(
                    f"solver {s.name!r} time {s.time!r} is not on the sub-step grid "
                    f"of master interval {self.master_index}"
                )
            s.phase = rounded

    # -- stepping --------------------------------------------------------- #
    def tick(self) -> None:
        """Advance one *global* sub-step; completes the interval if needed."""

        j = self.sub_index
        for name in self.order:
            s = self.solvers[name]
            stride = self.grid.n_sub // s.steps_per_master
            if j % stride == 0:
                s.advance(s.dt)
                s.time = s.time + s.dt
        self.sub_index = j + 1
        if self.sub_index >= self.grid.n_sub:
            self._couple()
            self.master_index += 1
            self.sub_index = 0
        self._sync_phases()

    def _couple(self) -> None:
        for ex in self.exchanges:
            ex.apply(self.solvers)

    def advance_master(self, count: int = 1) -> None:
        for _ in range(int(count)):
            if self.sub_index:
                raise RuntimeError(
                    "advance_master called mid-interval; finish the interval with tick() or "
                    "record the inconsistency explicitly"
                )
            for _ in range(self.grid.n_sub):
                self.tick()

    # -- convenience ------------------------------------------------------ #
    def sync_report(self) -> SyncReport:
        return assess_sync(self.snapshot(), self.grid)

    def run(self, master_steps: int, *, store: CheckpointStore | None = None,
            checkpoint_every: int = 0, ledger: EnergyLedger | None = None,
            label: str = "", extra: Optional[Mapping[str, Any]] = None) -> RunResult:
        import time as _time

        started = _time.perf_counter()
        readings: List[EnergyReading] = []
        saved: List[SaveResult] = []
        if ledger is not None:
            readings.append(ledger.measure(self.snapshot()))
        for _ in range(int(master_steps)):
            self.advance_master(1)
            if ledger is not None:
                readings.append(ledger.measure(self.snapshot()))
            if store is not None and checkpoint_every and self.master_index % checkpoint_every == 0:
                saved.append(store.save(self.master_index, self.snapshot(), label=label,
                                        extra=dict(extra or {})))
        return RunResult(
            scenario=self.name, master_steps=self.master_index,
            final_digest=self.snapshot().digest, energy_readings=readings,
            checkpoints=saved, seconds=_time.perf_counter() - started,
            ledger=ledger,
        )


# ───────────────────────────────────────────────────────────────────────────
# Scenarios
# ───────────────────────────────────────────────────────────────────────────
SCENARIOS: Dict[str, Dict[str, Any]] = {
    "kinetic_only": {
        "level": 1,
        "solver_kinds": ("kinetic",),
        "description": "one distribution-array solver (streaming only, conservative)",
    },
    "kinetic_field": {
        "level": 2,
        "solver_kinds": ("kinetic", "nodal"),
        "description": "distribution array coupled to a nodal field (2-solver exchange)",
    },
    "kinetic_field_particles": {
        "level": 3,
        "solver_kinds": ("kinetic", "nodal", "particles"),
        "description": "distribution array + nodal field + particle table (3-solver chain)",
    },
}
SCENARIO_LEVELS = {spec["level"]: key for key, spec in SCENARIOS.items()}


def scenario_name(level: int | str) -> str:
    if isinstance(level, str):
        if level in SCENARIOS:
            return level
        if level.isdigit() and int(level) in SCENARIO_LEVELS:
            return SCENARIO_LEVELS[int(level)]
        raise KeyError(f"unknown scenario {level!r}; known: {sorted(SCENARIOS)}")
    if int(level) not in SCENARIO_LEVELS:
        raise KeyError(f"unknown scenario level {level!r}; known: {sorted(SCENARIO_LEVELS)}")
    return SCENARIO_LEVELS[int(level)]


def build_harness(level: int | str = 3, *, damped: bool = False,
                  grid: Optional[CouplingTimeGrid] = None,
                  kinetic_shape: Tuple[int, int] = (16, 16),
                  nodes: int = 64, particles: int = 16,
                  seed_base: int = 1000) -> Tuple[CoupledHarness, EnergyLedger]:
    """Build a deterministic scenario (level 1/2/3) with its energy ledger."""

    name = scenario_name(level)
    spec = SCENARIOS[name]
    grid = grid or CouplingTimeGrid(dt_master=1.0, n_sub=4)
    omega = 1.2 if damped else 0.0
    solvers: List[ArraySolverBase] = []
    terms: List[Tuple[str, str, float]] = []
    exchanges: List[CouplingExchange] = []

    for kind in spec["solver_kinds"]:
        if kind == "kinetic":
            solvers.append(KineticGrid(
                "kinetic", nx=kinetic_shape[0], ny=kinetic_shape[1],
                seed=seed_base + 11, omega=omega, steps_per_master=4, dt=grid.sub_dt(4),
            ))
            terms.append(("kinetic", "f", 1.0))
        elif kind == "nodal":
            solvers.append(NodalField("nodal", nodes=nodes, seed=seed_base + 23,
                                      steps_per_master=2, dt=grid.sub_dt(2)))
            terms += [("nodal", "u", 1.0), ("nodal", "v", 1.0)]
        elif kind == "particles":
            solvers.append(ParticleSet("particles", count=particles, seed=seed_base + 37,
                                       steps_per_master=1, dt=grid.sub_dt(1)))
            terms.append(("particles", "v", 1.0))
        else:  # pragma: no cover - table driven
            raise KeyError(kind)

    patch = 24
    if "nodal" in spec["solver_kinds"]:
        exchanges.append(CouplingExchange("kinetic", "f", "nodal", "u", patch, 0.03))
    if "particles" in spec["solver_kinds"]:
        exchanges.append(CouplingExchange("nodal", "v", "particles", "v", patch, 0.05))

    functionals = {
        "E_total": quadratic_functional(terms),
    }
    if len(spec["solver_kinds"]) > 1:
        # per-solver diagnostics: energy *moves* between solvers at each exchange,
        # so these are reported but not gated; E_total is the invariant.
        for kind in spec["solver_kinds"]:
            sub_terms = [t for t in terms if t[0] == kind]
            if sub_terms:
                functionals[f"E_{kind}"] = quadratic_functional(sub_terms)
    ledger = EnergyLedger(functionals, primary="E_total", gate=("E_total",))
    harness = CoupledHarness(solvers, grid, exchanges, name=name)
    return harness, ledger


def scenario_table() -> Dict[str, Dict[str, Any]]:
    return {name: dict(spec) for name, spec in SCENARIOS.items()}