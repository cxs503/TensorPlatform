"""Synthetic two-solver twin for end-to-end consistency gating (domain-neutral).

A conservation gate on a single remap says nothing about the *coupled loop*: two
solvers exchange fields every master step, each applies its own update, and the
clock decides when.  :func:`run_twin` builds the smallest loop that still tests
all of that, on a synthetic domain — no physics vocabulary, only:

* **solver A** on a fine mesh, advancing ``k`` sub-steps of a conservative
  (zero-flux) diffusion operator per master step and publishing a sample at every
  sub-step time;
* **solver B** on a coarse mesh, which receives A's field, advances one master
  step, and feeds the result back to A;
* an explicit :class:`~tools.coupling.clock.ClockContract` with
  ``dt_master = k · dt_sub``.  In ``SYNC`` mode the master-tick request must hit a
  stored sub-step sample exactly; the other modes either interpolate, extrapolate
  at most one step, or answer with an explicitly stale event value — never more.

Both representations hold the *same* conserved quantity, so the loop is a
consistency test between them:

``residual_series``      ``|M_A(cycle) - M_A(0)| / M_A(0)`` — total drift of the loop
``representation_gap``   ``|M_A - M_B| / M_A`` — disagreement between the two representations
``roundtrip_error``      ``coarse → fine → coarse`` relative error (nested meshes:
                         exact identity for the overlap operator)
``clock_violations``     contract violations raised inside the loop (must be 0)

No global mass fixer is applied anywhere in the loop, so an operator without a
conservation correction *must* drift — the negative control is real, not a
self-fulfilling test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from .clock import ClockContract, ClockContractViolation, ClockMode, TimeSeriesExchange
from .mesh import BoxMesh, Field, QuantityKind
from .provenance import canonical_digest
from .remap import RemapOperator, RemapSpec, remap

__all__ = ["TwinSpec", "run_twin", "diffusion_step", "zero_flux_laplacian"]


@dataclass(frozen=True)
class TwinSpec:
    """Configuration of the synthetic twin (everything that affects the result)."""

    n_coarse: int = 6                 # coarse cells per dimension (solver B)
    refinement: int = 2               # fine = coarse × refinement
    dim: int = 2
    layout: str = "nested"            # nested | shifted  (shifted => meshes are not nested)
    cycles: int = 40                  # master steps
    dt_master: float = 0.02
    substeps: int = 2                 # k in dt_master = k·dt_sub
    mode: ClockMode = ClockMode.SYNC
    operator: RemapOperator = RemapOperator.OVERLAP_L2
    apply_conservation_correction: bool = True
    diffusivity: float = 1e-3
    amplitude: float = 1.0
    clock_slack_steps: int = 0        # 0 => request exactly on a sub-step time
    max_extrapolation_steps: int = 1
    max_staleness_steps: int = 1

    def __post_init__(self) -> None:
        if self.n_coarse <= 0 or self.refinement <= 0 or self.cycles <= 0:
            raise ValueError("n_coarse, refinement and cycles must be positive")
        if self.dim not in (1, 2, 3):
            raise ValueError("dim must be 1, 2 or 3")
        if self.layout not in ("nested", "shifted"):
            raise ValueError("layout must be 'nested' or 'shifted'")
        if self.substeps < 1:
            raise ValueError("substeps must be >= 1")
        if self.clock_slack_steps < 0:
            raise ValueError("clock_slack_steps must be >= 0")
        try:
            object.__setattr__(self, "mode", ClockMode(self.mode))
            object.__setattr__(self, "operator", RemapOperator(self.operator))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @property
    def dt_sub(self) -> float:
        return self.dt_master / self.substeps

    def contract(self) -> ClockContract:
        return ClockContract(
            dt_master=self.dt_master,
            dt_sub=self.dt_sub,
            mode=self.mode,
            max_extrapolation_steps=self.max_extrapolation_steps,
            max_staleness_steps=self.max_staleness_steps,
        )

    def digest(self) -> str:
        return canonical_digest(self)


def zero_flux_laplacian(field: np.ndarray, grid: tuple, spacing: tuple) -> np.ndarray:
    """Discrete Laplacian with zero-flux boundaries whose sum is exactly zero."""
    u = field.reshape(grid)
    out = np.zeros_like(u)
    for axis, dx in enumerate(spacing):
        lo = np.concatenate(
            [np.take(u, [0], axis=axis), np.take(u, np.arange(u.shape[axis] - 1), axis=axis)], axis=axis
        )
        hi = np.concatenate(
            [np.take(u, np.arange(1, u.shape[axis]), axis=axis), np.take(u, [-1], axis=axis)], axis=axis
        )
        out += (lo - 2.0 * u + hi) / (dx * dx)
    return out.reshape(-1)


def diffusion_step(field: Field, grid: tuple, spacing: tuple, dt: float, diffusivity: float) -> Field:
    """One explicit conservative diffusion sub-step (total preserved to round-off).

    The update is divided by the cell volume (relative to the mean volume) so that
    ``sum(update_i * V_i) = D·dt·(V_ref/V_mean)·sum(lap_i) = 0`` holds on
    non-uniform meshes too — a volume-blind update leaks mass as soon as cell
    volumes differ, which is exactly the failure mode a conservation gate should
    not have to rediscover inside a solver.
    """
    lap = zero_flux_laplacian(field.values, grid, spacing)
    volume = field.mesh.volume
    update = diffusivity * dt * lap * (volume.mean() / volume)
    return field.with_values(field.values + update)


def _blob(centers: np.ndarray, centre, width: float, amplitude: float) -> np.ndarray:
    d2 = ((centers - np.asarray(centre)) ** 2).sum(axis=1)
    return amplitude * np.exp(-d2 / (2.0 * width * width))


def _shifted_mesh(n: int, dim: int, name: str = "coarse-shifted") -> BoxMesh:
    """Same domain ``[0,1]^dim``, same cell count, half-cell offset (non-nested)."""
    h = 1.0 / n
    edges = np.concatenate(([0.0], 0.5 * h + h * np.arange(n - 1), [1.0]))
    per_dim = [edges] * dim
    grids = np.meshgrid(*[ed[:-1] for ed in per_dim], indexing="ij")
    del grids
    shape = tuple([n] * dim)
    lo_c = np.empty(shape + (dim,))
    hi_c = np.empty(shape + (dim,))
    for k, ed in enumerate(per_dim):
        view = [-1 if j == k else 1 for j in range(dim)]
        lo_c[..., k] = np.broadcast_to(ed[:-1].reshape(view), shape)
        hi_c[..., k] = np.broadcast_to(ed[1:].reshape(view), shape)
    return BoxMesh(lo=lo_c.reshape(-1, dim), hi=hi_c.reshape(-1, dim), name=name)


def _meshes(spec: TwinSpec):
    dim = spec.dim
    n_fine = spec.n_coarse * spec.refinement
    if spec.layout == "nested":
        coarse = BoxMesh.uniform([0.0] * dim, [1.0] * dim, [spec.n_coarse] * dim, name="coarse")
    else:
        # half-cell offset mesh: same domain, nothing is nested with the fine mesh
        coarse = _shifted_mesh(spec.n_coarse, dim)
    fine = BoxMesh.uniform([0.0] * dim, [1.0] * dim, [n_fine] * dim, name="fine")
    return coarse, fine


def run_twin(spec: Optional[TwinSpec] = None) -> Dict[str, Any]:
    """Run the synthetic two-solver loop and return its consistency record."""
    spec = TwinSpec() if spec is None else spec
    dim = spec.dim
    n_fine = spec.n_coarse * spec.refinement
    coarse, fine = _meshes(spec)
    grid_f, grid_c = tuple([n_fine] * dim), tuple([spec.n_coarse] * dim)
    spacing_f = tuple([1.0 / n_fine] * dim)
    spacing_c = tuple([1.0 / spec.n_coarse] * dim)

    u = Field(_blob(fine.centers, [0.35] * dim, 0.18, spec.amplitude), fine,
              QuantityKind.DENSITY, name="fine_field")
    v = Field(_blob(coarse.centers, [0.6] * dim, 0.22, 0.8 * spec.amplitude), coarse,
              QuantityKind.DENSITY, name="coarse_field")

    contract = spec.contract()
    exchange = TimeSeriesExchange(contract, name="twin-exchange")
    rho_spec = RemapSpec(operator=spec.operator, quantity_kind=QuantityKind.DENSITY,
                         apply_conservation_correction=spec.apply_conservation_correction)

    m_a0 = u.total
    residual_series: List[float] = []
    gap_series: List[float] = []
    clock_violations = 0
    exchanges: List[Dict[str, Any]] = []

    for cycle in range(spec.cycles):
        # ---- solver A: k sub-steps, one published sample per sub-step time
        for t_sub in contract.substep_times(cycle):
            u = diffusion_step(u, grid_f, spacing_f, spec.dt_sub, spec.diffusivity)
            exchange.store(float(t_sub), u.values)

        # master tick: B requests A's state through the clock contract
        t_request = contract.master_time(cycle) + spec.clock_slack_steps * contract.dt_sub
        try:
            sample = exchange.request(t_request)
        except ClockContractViolation:
            clock_violations += 1
            raise
        u_tick = u.with_values(sample.values, name="fine_at_tick")

        # ---- exchange: A -> B, solver B advances one master step, B -> A
        fwd = remap(u_tick, fine, coarse, QuantityKind.DENSITY, spec=rho_spec)
        v = diffusion_step(fwd.field, grid_c, spacing_c, spec.dt_master, spec.diffusivity)
        back = remap(v, coarse, fine, QuantityKind.DENSITY, spec=rho_spec)
        u = back.field

        m_a, m_b = u.total, v.total
        residual_series.append(abs(m_a - m_a0) / max(abs(m_a0), 1e-300))
        gap_series.append(abs(m_a - m_b) / max(abs(m_a), 1e-300))
        exchanges.append({
            "cycle": cycle,
            "t_master": contract.master_time(cycle),
            "mass_fine": m_a,
            "mass_coarse": m_b,
            "clock_mode": sample.mode,
            "interpolated": sample.interpolated,
            "extrapolated": sample.extrapolated,
            "stale": sample.stale,
            "remap_digest_forward": fwd.report.remap_digest,
            "remap_digest_backward": back.report.remap_digest,
            "remap_sum_raw_forward": fwd.report.sum_dst_raw,
            "remap_sum_raw_backward": back.report.sum_dst_raw,
        })

    # ---- round trip on the loop's own meshes: coarse -> fine -> coarse
    probe = Field(_blob(coarse.centers, [0.5] * dim, 0.25, 1.0), coarse, QuantityKind.DENSITY, name="probe")
    plain = RemapSpec(operator=RemapOperator.OVERLAP_L2, quantity_kind=QuantityKind.DENSITY,
                      apply_conservation_correction=False)
    fwd = remap(probe, coarse, fine, QuantityKind.DENSITY, spec=plain)
    rtd = remap(fwd.field, fine, coarse, QuantityKind.DENSITY, spec=plain)
    denom = max(float(np.abs(probe.values).max()), 1e-300)
    roundtrip_error = float(np.abs(rtd.field.values - probe.values).max() / denom)

    max_residual = max(residual_series) if residual_series else 0.0
    return {
        "spec": {
            "n_coarse": spec.n_coarse,
            "refinement": spec.refinement,
            "dim": spec.dim,
            "layout": spec.layout,
            "cycles": spec.cycles,
            "dt_master": spec.dt_master,
            "dt_sub": spec.dt_sub,
            "substeps": spec.substeps,
            "mode": ClockMode(spec.mode).value,
            "operator": RemapOperator(spec.operator).value,
            "apply_conservation_correction": spec.apply_conservation_correction,
            "diffusivity": spec.diffusivity,
            "clock_slack_steps": spec.clock_slack_steps,
        },
        "spec_digest": spec.digest(),
        "mass_initial": {"fine": m_a0},
        "mass_final": {"fine": u.total, "coarse": v.total},
        "residual_series": residual_series,
        "representation_gap_series": gap_series,
        "max_residual": max_residual,
        "final_residual": residual_series[-1] if residual_series else 0.0,
        "max_representation_gap": max(gap_series) if gap_series else 0.0,
        "roundtrip_error": roundtrip_error,
        "clock_violations": clock_violations,
        "clock_ledger": exchange.ledger.to_dict(),
        "clock": exchange.to_dict(),
        "exchanges": exchanges,
        "run_digest": canonical_digest({
            "spec": spec.digest(),
            "residual_series": residual_series,
            "gap_series": gap_series,
            "roundtrip_error": roundtrip_error,
            "mass_final": [u.total, v.total],
        }),
    }