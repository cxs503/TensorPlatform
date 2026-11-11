"""The three interfaces of the cross-library conservation gate, as ledgers.

Each function runs one hand-off on *synthetic* geometry/fields (the gate itself
knows no physics) and returns a sealed :class:`ConservationLedger` plus a
detail dictionary with everything the report needs:

``fem_dem_ledger``
    Fracture hand-off.  A solid element is cut into the actual convex cells of
    the fracture surface; the four ledgers (mass / linear momentum / angular
    momentum / energy) plus the full inertia tensor and the volume are recorded
    two-sided.  The DEM side receives rigid fragments; the released strain
    energy is invested in a centroid-neutral expansion mode, so the energy
    ledger closes without polluting the momentum ledgers.

``lbm_fvm_ledger``
    Flow hand-off.  Density/momentum inventory is transferred with the
    supermesh (volume-overlap) remap and the mass flux crossing the coupling
    plane with the area-weighted operator, both two-sided.

``clock_ledger``
    Time hand-off.  A conservative barrier run (100 windows, adversarial step
    ratios) plus an optimistic run with latency-jittered messages and
    anti-message rollback.  Both are recorded: barrier invariants as
    ``bound``/``flag`` entries, optimism as a rollback rate and a replay error.

No domain semantics: elements, cells, faces, windows, messages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .ledger import ConservationLedger, LedgerEntry, sha256_of

VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# 1. fracture hand-off (FEM -> DEM)
# --------------------------------------------------------------------------- #


def fem_dem_ledger(element, fracture_faces: Sequence[Any],
                   energy_mode: str = "expansion",
                   transfer=None) -> Tuple[ConservationLedger, dict]:
    """Consume the fracture surface and record the five two-sided ledgers."""
    from ..fem_to_dem_exact import ExactFractureTransfer

    tr = transfer if transfer is not None else ExactFractureTransfer()
    frags, led = tr.transfer(element, fracture_faces, energy_mode=energy_mode)

    entries = [
        LedgerEntry("mass", "scalar", led.mass_source, led.mass_target,
                    tolerance=tr.tolerance["mass"], note="sum m_i vs rho V"),
        LedgerEntry("momentum", "vector", led.momentum_source, led.momentum_target,
                    tolerance=tr.tolerance["momentum"]),
        LedgerEntry("angular_momentum", "vector", led.angular_source, led.angular_target,
                    tolerance=tr.tolerance["angular_momentum"],
                    note="about the element centroid"),
        LedgerEntry("energy", "scalar", led.energy_source, led.energy_target,
                    tolerance=tr.tolerance["energy"],
                    note="kinetic + released strain energy"),
        LedgerEntry("inertia_tensor", "tensor", led.inertia_source, led.inertia_target,
                    tolerance=tr.tolerance.get("inertia_tensor", 1e-10),
                    note="parallel-axis sum vs element tensor"),
        LedgerEntry("volume", "scalar", led.volume_source, led.volume_target,
                    tolerance=tr.tolerance.get("volume", 1e-12)),
    ]
    faces_digest = sha256_of([[list(map(float, v)) for v in f.vertices]
                              for f in fracture_faces])
    element_digest = sha256_of({
        "density": float(element.density),
        "velocity": [float(x) for x in element.velocity],
        "angular_velocity": [float(x) for x in element.angular_velocity],
        "strain_energy": float(element.elastic_strain_energy),
    })
    ledger = ConservationLedger(
        face="fem_dem",
        entries=entries,
        producer={"name": "conservation_gate.fem_dem_ledger", "version": VERSION,
                  "energy_mode": energy_mode},
        inputs=[faces_digest, element_digest],
        meta={"n_fragments": len(frags), "n_faces": len(fracture_faces)},
    )
    ledger.seal_ledger()

    detail = {
        "n_fragments": len(frags),
        "fragment_masses": [float(f.mass) for f in frags],
        "fragment_volumes": [float(f.volume) for f in frags],
        "fragment_inertia_spd": [bool(_is_spd(f.inertia)) for f in frags],
        "mass_residual": led.mass_residual,
        "momentum_residual": led.momentum_residual,
        "angular_residual": led.angular_residual,
        "energy_residual": led.energy_residual,
        "inertia_residual": led.inertia_residual,
        "volume_residual": led.volume_residual,
        "residuals": ledger.residuals(),
    }
    return ledger, detail


def _is_spd(a: np.ndarray) -> bool:
    try:
        np.linalg.cholesky(np.asarray(a, dtype=float))
    except np.linalg.LinAlgError:
        return False
    return bool(np.all(np.isfinite(a)))


def sphere_surrogate_ledger(element, fracture_faces: Sequence[Any]):
    """Negative baseline: the same hand-off with sphere-equivalent fragments."""
    from ..fem_to_dem_exact import ExactFractureTransfer

    tr = ExactFractureTransfer()
    return tr.compare_with_sphere_surrogate(element, fracture_faces)


def sphere_surrogate_tensors(element, fracture_faces: Sequence[Any]) -> Dict[str, Any]:
    """Aggregate inertia tensor / angular momentum of the sphere-equivalent model.

    The comparison helper reports residuals only; the gate's substitution
    control needs the *numbers* the shortcut produces, so they are rebuilt here
    on the identical input (same partition, same sphere surrogate).
    """
    import numpy as np

    from ..fem_to_dem_exact import ExactFractureTransfer
    from ..fem_to_dem_transfer import (
        FailureFace as LegacyFace,
        FEMElementState as LegacyElement,
        FEMtoDEMTransfer,
    )

    cells = ExactFractureTransfer().partition(element, fracture_faces)
    legacy_faces = []
    for cell in cells:
        tri = cell.vertices[:3]
        normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        area = 0.5 * float(np.linalg.norm(normal))
        legacy_faces.append(LegacyFace(vertices=tri,
                                       normal=normal / max(np.linalg.norm(normal), 1e-300),
                                       area=area))
    legacy_element = LegacyElement(
        mass=element.mass, velocity=element.velocity,
        angular_velocity=element.angular_velocity,
        inertia_tensor=element.inertia,
        elastic_strain_energy=element.elastic_strain_energy,
        centroid=element.centroid)
    frags, _ = FEMtoDEMTransfer(tolerance=1e-9).transfer(
        legacy_element, legacy_faces, n_fragments=len(cells))
    c_elem = element.centroid
    inertia = np.zeros((3, 3))
    angular = np.zeros(3)
    for f in frags:
        d = f.position - c_elem
        inertia += f.inertia_tensor + f.mass * (float(np.dot(d, d)) * np.eye(3)
                                               - np.outer(d, d))
        angular += f.angular_momentum + np.cross(d, f.linear_momentum)
    return {"inertia_tensor": inertia.tolist(),
            "angular_momentum": [float(x) for x in angular],
            "isotropic": all(np.allclose(f.inertia_tensor,
                                         np.eye(3) * f.inertia_tensor[0, 0], atol=1e-12)
                             for f in frags)}


# --------------------------------------------------------------------------- #
# 2. flow hand-off (LBM -> FVM)
# --------------------------------------------------------------------------- #
def lbm_fvm_ledger(source: Sequence[Any], target: Sequence[Any],
                   point: Sequence[float], normal: Sequence[float],
                   baseline: bool = False) -> Tuple[ConservationLedger, dict]:
    """Transfer a lattice field onto a finite-volume grid across plane Gamma."""
    from ..lbm_to_fvm_conservative_remap import InterfaceFluxTransfer, SupermeshRemap

    remap = SupermeshRemap()
    remapped = remap.remap(source, target)
    flux_tr = InterfaceFluxTransfer()
    if baseline:
        # classical shortcut: sample the donor at the receiver centroid
        flux = flux_tr.transfer_by_cell_centres(source, target, point, normal)
        label = "inverse_distance"
    else:
        flux = flux_tr.transfer(source, target, point, normal)
        label = "area_weighted"

    entries = [
        LedgerEntry("mass_inventory", "scalar", remapped.mass_source, remapped.mass_target,
                    tolerance=1e-12, note="sum rho V"),
        LedgerEntry("momentum_inventory", "vector", remapped.momentum_source,
                    remapped.momentum_target, tolerance=1e-9),
        LedgerEntry("interface_mass_flux", "scalar", flux.flux_source, flux.flux_target,
                    tolerance=1e-10, note="sum rho u.n A on Gamma"),
    ]
    ledger = ConservationLedger(
        face="lbm_fvm",
        entries=entries,
        producer={"name": "conservation_gate.lbm_fvm_ledger", "version": VERSION,
                  "flux_operator": label},
        inputs=[sha256_of({"point": list(map(float, point)),
                           "normal": list(map(float, normal)),
                           "n_source": len(source), "n_target": len(target)})],
        meta={"n_source": len(source), "n_target": len(target),
              "coverage": remapped.coverage,
              "max_uncovered_fraction": remapped.max_uncovered_fraction},
    )
    ledger.seal_ledger()
    detail = {
        "operator": label,
        "mass_residual": remapped.mass_residual,
        "momentum_residual": remapped.momentum_residual,
        "flux_residual": flux.flux_residual,
        "area_residual": flux.area_residual,
        "flux_source": flux.flux_source,
        "flux_target": flux.flux_target,
        "coverage": remapped.coverage,
        "max_uncovered_fraction": remapped.max_uncovered_fraction,
        "residuals": ledger.residuals(),
    }
    return ledger, detail


# --------------------------------------------------------------------------- #
# 3. time hand-off (clock semantics)
# --------------------------------------------------------------------------- #
def _lcm(a: int, b: int) -> int:
    return abs(a * b) // math.gcd(a, b)


@dataclass
class BarrierReport:
    windows: int
    window_ticks: int
    tick: Fraction
    steps: Dict[str, int]
    max_skew: float
    future_reads: int
    stalls: int
    deadlock: bool
    monotone: bool
    committed: Dict[str, Fraction]

    def as_dict(self) -> dict:
        return {
            "windows": self.windows,
            "window": float(self.window_ticks * self.tick),
            "steps": dict(self.steps),
            "max_skew": self.max_skew,
            "future_reads": self.future_reads,
            "stalls": self.stalls,
            "deadlock": self.deadlock,
            "monotone": self.monotone,
            "committed": {k: float(v) for k, v in self.committed.items()},
        }


def barrier_run(dt_ticks: Sequence[int], n_windows: int = 100,
                window_override: Optional[int] = None,
                peek: bool = False) -> BarrierReport:
    """Conservative barrier over ``n_windows`` LCM windows.

    Every window is one LCM multiple of all solver step sizes, so each solver
    completes a whole number of steps inside it and the barrier commits at the
    same instant for all solvers: ``max_skew == 0`` exactly (integer ticks, no
    floating-point drift).  ``window_override`` shrinks the window below the LCM
    — a solver then cannot finish its work, which is precisely the deadlock the
    detector must catch.  ``peek=True`` lets a solver consume data stamped
    inside the *current* (uncommitted) window, the causality violation the
    auditor must count.
    """
    tick = Fraction(1, 1000)
    dts = [int(v) for v in dt_ticks]
    window = window_override if window_override is not None else 1
    if window_override is None:
        for d in dts:
            window = _lcm(window, d)

    time = [0] * len(dts)
    steps = [0] * len(dts)
    committed_stamp = 0
    stalls = 0
    future_reads = 0
    max_skew = 0
    monotone = True
    prev_commit = 0

    for w in range(n_windows):
        target = (w + 1) * window
        if target <= prev_commit:
            monotone = False
        prev_commit = target
        for i, d in enumerate(dts):
            while time[i] + d <= target:
                if peek and time[i] + d > committed_stamp:
                    future_reads += 1
                time[i] += d
                steps[i] += 1
            max_skew = max(max_skew, abs(time[i] - target))
        committed_stamp = target
        if any(t != target for t in time):
            stalls += 1
    return BarrierReport(
        windows=n_windows,
        window_ticks=window,
        tick=tick,
        steps={f"solver{i}": steps[i] for i in range(len(dts))},
        max_skew=float(max_skew * tick),
        future_reads=future_reads,
        stalls=stalls,
        deadlock=stalls > 0,
        monotone=monotone,
        committed={f"solver{i}": time[i] * tick for i in range(len(dts))},
    )


@dataclass
class OptimisticReport:
    """Optimistic (speculate + anti-message rollback) run of one consumer LP."""

    periods: int
    steps_per_period: int
    mean_latency_steps: float
    rollback_steps: int
    total_steps: int
    rollback_rate: float
    replay_error: float
    no_rollback_error: float
    latency_histogram: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "periods": self.periods,
            "steps_per_period": self.steps_per_period,
            "mean_latency_steps": self.mean_latency_steps,
            "rollback_steps": self.rollback_steps,
            "total_steps": self.total_steps,
            "rollback_rate": self.rollback_rate,
            "replay_error": self.replay_error,
            "no_rollback_error": self.no_rollback_error,
            "latency_histogram": dict(self.latency_histogram),
        }


def optimistic_run(periods: int = 40, steps_per_period: int = 50,
                   latency_steps: Sequence[int] = (0, 1, 2, 3, 4),
                   seed: int = 1729, rollback: bool = True,
                   rate: float = 1.0, omega: float = 2.0 * math.pi,
                   period: float = 0.05) -> OptimisticReport:
    """Optimistic coupling of one consumer ODE against a sampled partner field.

    Consumer:  ``dx/dt = -rate * x + s(t)``  (first-order relaxation, stable).
    Partner:   ``s(t) = sin(omega t)`` sampled at ``period``.

    The consumer must take the steps of period ``k`` with sample ``s_k``.  Under
    latency it *speculates*: the first ``m_k`` steps run with the stale sample
    ``s_{k-1}``; when ``s_k`` arrives the anti-message invalidates exactly those
    ``m_k`` steps.  With rollback the consumer restores the state at the period
    boundary and replays all ``steps_per_period`` steps with the truth, so the
    end state is identical to the deterministic replay; without rollback the
    speculative contribution stays in the trajectory and the run drifts.

    ``rollback_rate = sum(m_k) / (periods * steps_per_period)`` — the cost of
    optimism.  Latency is drawn per period from ``latency_steps`` (deterministic
    given ``seed``), so the rate is reproducible.
    """
    rng = np.random.default_rng(seed)
    dt = period / steps_per_period

    def truth(k: int) -> float:
        return math.sin(omega * (k + 1) * period)

    def integrate(x: float, source: float, n: int) -> float:
        for _ in range(n):
            x += dt * (-rate * x + source)
        return x

    x_ref = 0.0
    x_spec = 0.0
    x_noroll = 0.0
    rollback_steps = 0
    hist: Dict[str, int] = {}
    latencies: List[int] = []
    sample_prev = truth(-1)

    for k in range(periods):
        sample_k = truth(k)
        m = int(latency_steps[int(rng.integers(0, len(latency_steps)))])
        m = max(0, min(m, steps_per_period))
        latencies.append(m)
        hist[str(m)] = hist.get(str(m), 0) + 1

        # deterministic replay (reference): every step uses the true sample
        x_ref = integrate(x_ref, sample_k, steps_per_period)

        # optimistic with rollback: speculate m steps on the stale sample,
        # then (on arrival) restore and replay the whole period with truth
        if rollback:
            # speculative excursion on the stale sample, then discarded when the
            # anti-message arrives: the period is replayed from the boundary
            integrate(x_spec, sample_prev, m)
            rollback_steps += m
            x_spec = integrate(x_spec, sample_k, steps_per_period)
        else:
            x_spec = integrate(x_spec, sample_prev, m)
            x_spec = integrate(x_spec, sample_k, steps_per_period - m)

        # optimistic without any rollback (failure mode kept for the baseline)
        x_noroll = integrate(x_noroll, sample_prev, m)
        x_noroll = integrate(x_noroll, sample_k, steps_per_period - m)

        sample_prev = sample_k

    total = periods * steps_per_period
    return OptimisticReport(
        periods=periods,
        steps_per_period=steps_per_period,
        mean_latency_steps=float(np.mean(latencies)),
        rollback_steps=rollback_steps,
        total_steps=total,
        rollback_rate=rollback_steps / total,
        replay_error=abs(x_spec - x_ref),
        no_rollback_error=abs(x_noroll - x_ref),
        latency_histogram=hist,
    )


def clock_ledger(barrier: BarrierReport, optimistic: OptimisticReport,
                 face_id: str = "clock") -> Tuple[ConservationLedger, dict]:
    """Record the clock semantics audit as ``bound``/``flag`` entries."""
    entries = [
        LedgerEntry("barrier_skew", "scalar", barrier.max_skew, 0.0,
                    tolerance=1e-12, note="exact when the window is the LCM"),
        LedgerEntry("deadlocks", "scalar", float(barrier.stalls), 0.0,
                    tolerance=0.0, note="windows where a solver could not commit"),
        LedgerEntry("future_reads", "scalar", float(barrier.future_reads), 0.0,
                    tolerance=0.0, note="consumption of uncommitted partner data"),
        LedgerEntry("global_time_monotone", "flag", bool(barrier.monotone), True),
        LedgerEntry("optimistic_rollback_rate", "scalar", optimistic.rollback_rate,
                    0.0, tolerance=0.05, note="invalidated steps / total steps"),
        LedgerEntry("optimistic_replay_error", "scalar", optimistic.replay_error,
                    0.0, tolerance=1e-12),
    ]
    ledger = ConservationLedger(
        face=face_id,
        entries=entries,
        producer={"name": "conservation_gate.clock_ledger", "version": VERSION,
                  "protocol": "barrier+timewarp"},
        inputs=[sha256_of({"windows": barrier.windows, "window_ticks": barrier.window_ticks,
                           "periods": optimistic.periods,
                           "steps_per_period": optimistic.steps_per_period,
                           "mean_latency_steps": optimistic.mean_latency_steps})],
        meta={"windows": barrier.windows,
              "no_rollback_error": optimistic.no_rollback_error},
    )
    ledger.seal_ledger()
    detail = {"barrier": barrier.as_dict(), "optimistic": optimistic.as_dict()}
    return ledger, detail


__all__ = [
    "VERSION",
    "BarrierReport",
    "OptimisticReport",
    "barrier_run",
    "clock_ledger",
    "fem_dem_ledger",
    "lbm_fvm_ledger",
    "optimistic_run",
    "sphere_surrogate_ledger",
    "sphere_surrogate_tensors",
]