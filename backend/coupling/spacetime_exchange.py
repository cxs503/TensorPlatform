"""Space-time conservative field exchange between two solvers.

This is the composition of the two operators of node ``IN387-PLAT01``:

    source snapshots (fine clock, mesh A)
        -- temporal interpolation  -->  field at the target time on mesh A
        -- moment-preserving remap -->  field at the target time on mesh B

i.e. ``E = R_{A->B} o I_t``.  Why the composition must be audited as a whole:

* the temporal operator ``I_t`` is a convex combination of stored snapshots, so a
  globally conserved quantity is preserved **iff** the fine solver conserves it
  (the weights are a partition of unity) -- the audit reports the interpolated
  mass ``sum_k w_k M_k`` next to the actual mass of the interpolated field;
* the spatial operator ``R`` conserves the 0th moment exactly (conservative base
  transfer with exact overlap geometry) and, with the Lagrange correction, the
  first and second moments to machine precision;
* the quadratic (energy-like) functional is *not* a linear moment, so it is only
  approximately preserved: the temporal part contributes ``O((omega dt)^2)``
  (amplitude deficit of the interpolant) and the spatial part ``O(h^2)``
  (averaging of a smooth field).  Both are reported, never assumed.

Everything here is domain-agnostic: meshes, arrays, volume weights and a scalar
"energy" functional defined by the caller's cell weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

from .mesh import Mesh
from .moment_remap import MomentRemap, MomentRemapError, MomentRemapResult
from .temporal_interp import (
    TemporalInterpError,
    TemporalInterpResult,
    TemporalInterpolator,
    TemporalSeries,
)

__all__ = [
    "SpacetimeExchangeError",
    "ExchangeResult",
    "SpacetimeExchanger",
    "composition_roundtrip",
]


class SpacetimeExchangeError(ValueError):
    """Invalid space-time exchange request (fail-closed)."""


def _rel(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1e-300)
    return abs(a - b) / scale


@dataclass(frozen=True)
class ExchangeResult:
    """Exchanged field plus the complete space-time audit."""

    field: np.ndarray
    target_time: float
    time: TemporalInterpResult
    remap: MomentRemapResult
    mass_at_target: float
    mass_interpolated: float
    mass_source_snapshot: float
    energy_at_target: Optional[float]
    energy_reference: Optional[float]

    @property
    def time_mass_rel_error(self) -> float:
        """Interpolated mass vs the convex combination of the snapshot masses."""
        return _rel(self.mass_at_target, self.mass_interpolated)

    @property
    def spatial_mass_rel_error(self) -> float:
        """Target-mesh mass vs the mass of the interpolated source field."""
        return _rel(self.remap.mass_target, self.remap.mass_source)

    @property
    def energy_rel_error(self) -> Optional[float]:
        if self.energy_at_target is None or self.energy_reference is None:
            return None
        return _rel(self.energy_at_target, self.energy_reference)

    def to_dict(self) -> dict:
        return {
            "target_time": self.target_time,
            "time": self.time.to_dict(),
            "remap": self.remap.to_dict(),
            "mass_at_target": self.mass_at_target,
            "mass_interpolated": self.mass_interpolated,
            "mass_source_snapshot": self.mass_source_snapshot,
            "time_mass_rel_error": self.time_mass_rel_error,
            "spatial_mass_rel_error": self.spatial_mass_rel_error,
            "energy_at_target": self.energy_at_target,
            "energy_reference": self.energy_reference,
            "energy_rel_error": self.energy_rel_error,
        }


class SpacetimeExchanger:
    """Composed temporal-interpolation + moment-preserving remap operator.

    Parameters
    ----------
    src_mesh, tgt_mesh : :class:`~backend.coupling.mesh.Mesh` (2D source/target).
    src_times : (n_t,) strictly increasing sample times of the source solver
        (they need not be uniform, nor commensurate with the target clock).
    time_method : ``"nearest"`` | ``"linear"`` | ``"cubic"``.
    extrapolation : forwarded to :class:`TemporalInterpolator` (``"fail"`` by
        default: asking for data outside the stored window is an error, not a
        silent constant).
    Remaining keyword arguments are forwarded to :class:`MomentRemap`.
    """

    def __init__(
        self,
        src_mesh: Mesh,
        tgt_mesh: Mesh,
        src_times: Sequence[float],
        *,
        time_method: str = "linear",
        extrapolation: str = "fail",
        **remap_kwargs,
    ) -> None:
        if src_mesh.n_cells != tgt_mesh.n_cells and src_mesh.dim != tgt_mesh.dim:
            raise SpacetimeExchangeError("source and target meshes must share a dimension")
        self.src_mesh = src_mesh
        self.tgt_mesh = tgt_mesh
        self.src_times = np.asarray(src_times, dtype=float)
        self.time_method = time_method
        self.extrapolation = extrapolation
        self._remap = MomentRemap(src_mesh, tgt_mesh, **remap_kwargs)

    # -- accessors ---------------------------------------------------------
    @property
    def remap(self) -> MomentRemap:
        return self._remap

    def _series(self, snapshots: np.ndarray) -> TemporalSeries:
        s = np.asarray(snapshots, dtype=float)
        return TemporalSeries(
            times=self.src_times,
            snapshots=s,
            weights=self.src_mesh.volumes,
            label="source",
        )

    # -- main entry point --------------------------------------------------
    def exchange(
        self,
        snapshots: np.ndarray,
        target_time: float,
        *,
        reference_field: Optional[np.ndarray] = None,
    ) -> ExchangeResult:
        """Exchange the source snapshot history to ``target_time`` on the target mesh.

        ``snapshots`` has shape ``(n_t, n_src_cells)``; ``reference_field`` (on the
        **target** mesh) enables the energy audit.
        """
        s = np.asarray(snapshots, dtype=float)
        if s.ndim != 2 or s.shape[0] != self.src_times.size:
            raise SpacetimeExchangeError(
                f"snapshots shape {s.shape} inconsistent with {self.src_times.size} times"
            )
        if s.shape[1] != self.src_mesh.n_cells:
            raise SpacetimeExchangeError(
                f"snapshots have {s.shape[1]} cells but the source mesh has "
                f"{self.src_mesh.n_cells}"
            )
        series = self._series(s)
        interp = TemporalInterpolator(
            series, method=self.time_method, extrapolation=self.extrapolation
        )
        t_res = interp.interpolate(target_time)

        remap_res = self._remap.apply(t_res.field)

        # temporal mass audit: a partition of unity must reproduce the convex
        # combination of the snapshot masses exactly, whatever the method
        snapshot_masses = series.snapshots @ self.src_mesh.volumes
        mass_interp = float(np.dot(t_res.weights, snapshot_masses[list(t_res.stencil)]))

        energy_tgt = None
        energy_ref = None
        if reference_field is not None:
            ref = np.asarray(reference_field, dtype=float)
            if ref.shape != (self.tgt_mesh.n_cells,):
                raise SpacetimeExchangeError(
                    "reference_field must be given on the target mesh"
                )
            energy_tgt = float(np.dot(self.tgt_mesh.volumes, remap_res.field ** 2))
            energy_ref = float(np.dot(self.tgt_mesh.volumes, ref ** 2))

        return ExchangeResult(
            field=remap_res.field,
            target_time=float(t_res.time),
            time=t_res,
            remap=remap_res,
            mass_at_target=float(np.dot(self.src_mesh.volumes, t_res.field)),
            mass_interpolated=mass_interp,
            mass_source_snapshot=float(snapshot_masses[t_res.stencil[0]]),
            energy_at_target=energy_tgt,
            energy_reference=energy_ref,
        )


def composition_roundtrip(
    src_mesh: Mesh,
    tgt_mesh: Mesh,
    field: np.ndarray,
    *,
    n_cycles: int = 1,
    **remap_kwargs,
) -> dict:
    """Apply ``A -> B -> A`` ``n_cycles`` times and measure the accumulated drift.

    Returns a dict with ``field``, ``rel_error`` (max-abs, relative to the input
    amplitude), ``mass_rel_error`` and the two single-pass audits.  The operator
    is exact (to round-off) on the reconstruction space, i.e. on fields that are
    linear in space; for a general field the round trip is lossy by construction
    (``A -> B`` averages onto the coarser space) and the number returned is the
    honest information-loss figure, not a bug.
    """
    if n_cycles < 1:
        raise SpacetimeExchangeError("n_cycles must be >= 1")
    f0 = np.asarray(field, dtype=float)
    fwd = MomentRemap(src_mesh, tgt_mesh, **remap_kwargs)
    bwd = MomentRemap(tgt_mesh, src_mesh, **remap_kwargs)
    cur = f0.copy()
    first_fwd = None
    for _ in range(n_cycles):
        r_f = fwd.apply(cur)
        if first_fwd is None:
            first_fwd = r_f
        cur = bwd.apply(r_f.field).field
    amp = max(float(np.max(np.abs(f0))), 1e-300)
    return {
        "field": cur,
        "rel_error": float(np.max(np.abs(cur - f0)) / amp),
        "mass_rel_error": _rel(
            float(np.dot(src_mesh.volumes, cur)), float(np.dot(src_mesh.volumes, f0))
        ),
        "forward": first_fwd.to_dict(),
        "n_cycles": n_cycles,
    }