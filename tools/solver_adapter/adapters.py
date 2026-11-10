"""The eight library adapters: protocol scaffolding with a **synthetic** kernel.

Honesty statement (read before quoting any number from this module)
-------------------------------------------------------------------
The eight solver repositories are *not* vendored into this platform repo, so an
adapter here cannot drive a real LBM/FEM/... kernel.  What each adapter does
implement for real is the **protocol**: declaration of mesh, fields with
dimensional exponents, boundary records, lifecycle, checkpoint/restore, and
conservative exchange.  The numeric payload is produced by a small, generic,
deterministic *test kernel* (finite-difference relaxation or first-order upwind
transport in flux form, on the box mesh the protocol describes).

Consequences, stated plainly:

* exchange / conservation numbers measured here are **protocol properties**
  (they hold for any payload), not evidence about a library's physics;
* every adapter reports ``native_impl = False`` in its manifest, so no reader
  can mistake the scaffold for a validated coupling with the real library;
* the kernels are genuine numerics (a constant field is an exact fixed point,
  the upwind form conserves the total to round-off), so the conservation gate
  is measured against something that could fail — see the negative controls in
  ``tests/test_solver_adapter/test_conservation_mapping.py``.

Field vocabulary is deliberately synthetic (``density``, ``velocity_x``,
``offset_x``, ``flux_x``): protocol slots, not physical claims, and no
domain word appears anywhere in this package.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Type

import numpy as np

from ..coupling.mesh import BoxMesh, Field, QuantityKind
from ..coupling.provenance import canonical_digest
from .base import SolverAdapter, StepReport
from .errors import AdapterError, FieldLookupError, LifecycleError
from .protocol import BoundarySpec, FieldSpec, MappingMode

__all__ = [
    "ADAPTER_CLASSES",
    "adapter_class",
    "build_adapter",
    "all_solver_ids",
    "DENSITY",
    "VELOCITY",
    "OFFSET",
    "FLUX",
    "laplacian",
    "upwind_flux_form",
]

# ---------------------------------------------------------------------------
# Field declarations shared by the adapters (synthetic protocol slots)
# ---------------------------------------------------------------------------
DENSITY = FieldSpec(
    name="density",
    kind=QuantityKind.DENSITY.value,
    dim_vector=(1, -3, 0, 0),
    description="transported scalar; conserved total is sum(value * cell volume)",
)
VELOCITY = FieldSpec(
    name="velocity_x",
    kind=QuantityKind.DENSITY.value,
    dim_vector=(0, 1, -1, 0),
    description="transported vector component carried as a per-volume quantity",
)
OFFSET = FieldSpec(
    name="offset_x",
    kind=QuantityKind.EXTENSIVE.value,
    dim_vector=(0, 1, 0, 0),
    description="per-cell coordinate offset (mesh-motion carrier); the conserved total is a plain sum",
)
FLUX = FieldSpec(
    name="flux_x",
    kind=QuantityKind.FLUX.value,
    dim_vector=(1, -2, -1, 0),
    face_normal=0,
    description="face-normal flux; conserved total is sum(value * face area)",
)

_BOUNDARIES_3D: Tuple[BoundarySpec, ...] = (
    BoundarySpec(name="x_lo", axis=0, side="lo", kind="neumann", value=0.0),
    BoundarySpec(name="x_hi", axis=0, side="hi", kind="neumann", value=0.0),
)


def _variants(*specs: FieldSpec) -> Tuple[FieldSpec, ...]:
    """Expand the ``_x`` templates into one spec per axis (x/y/z)."""
    out: List[FieldSpec] = []
    for spec in specs:
        if spec.name.endswith("_x"):
            for axis, suffix in enumerate(("_x", "_y", "_z")):
                out.append(
                    FieldSpec(
                        name=spec.name[: -2] + suffix,
                        kind=spec.kind,
                        dim_vector=spec.dim_vector,
                        description=spec.description,
                        face_normal=spec.face_normal if spec.face_normal < 0 else axis,
                    )
                )
        else:
            out.append(spec)
    return tuple(out)


# ---------------------------------------------------------------------------
# Generic kernels (protocol-agnostic numerics; zero domain vocabulary)
# ---------------------------------------------------------------------------
def laplacian(values: np.ndarray, grid: Tuple[int, ...]) -> np.ndarray:
    """Zero-flux (edge-replicated) 5-point Laplacian on a structured grid.

    A constant field maps exactly to zero, so the total of the field is an exact
    invariant of the update — which is what makes the kernel usable as a payload
    for a *conservation* gate rather than just a filler.
    """
    g = np.asarray(values, dtype=float).reshape(grid)
    out = np.zeros_like(g)
    for axis in range(g.ndim):
        pad = [(0, 0)] * g.ndim
        pad[axis] = (1, 1)
        p = np.pad(g, pad, mode="edge")
        n = g.shape[axis]
        left = np.take(p, np.arange(0, n), axis=axis)
        right = np.take(p, np.arange(2, n + 2), axis=axis)
        out = out + left + right - 2.0 * g
    return out.reshape(values.shape)


def upwind_flux_form(
    values: np.ndarray, grid: Tuple[int, ...], spacing: Tuple[float, ...],
    speed: float, dt: float, axis: int = 0,
) -> np.ndarray:
    """First-order upwind transport in flux form with zero flux at both walls.

    Faces are numbered ``0..N`` and the face flux is ``F[k] = |u| * (u > 0 ?
    g[k-1] : g[k])`` with ``F[0] = F[N] = 0`` (walls).  Consecutive cells share a
    face, so ``g[i] -= dt/dx * (F[i+1] - F[i])`` telescopes and
    ``sum(g_new) == sum(g)`` to round-off for either sign of ``speed`` — exactly
    the invariant a conservation gate needs its payload to have.
    """
    g = np.asarray(values, dtype=float).reshape(grid)
    dx = float(spacing[axis])
    if dx <= 0.0:
        raise AdapterError("grid spacing must be positive")
    size = g.shape[axis]
    pad = [(0, 0)] * g.ndim
    pad[axis] = (1, 1)
    shifted = np.pad(g, pad, mode="constant", constant_values=0.0)
    upstream = np.take(shifted, np.arange(0, size), axis=axis)   # g[i-1] (0 on the wall)
    face = abs(speed) * np.where(speed > 0.0, upstream, g)       # flux through face i
    face = np.moveaxis(face, axis, 0)
    face[0] = 0.0                                                # left wall: no flux
    face_next = np.empty_like(face)
    face_next[:-1] = face[1:]
    face_next[-1] = 0.0                                          # right wall: no flux
    update = np.moveaxis(face_next - face, 0, axis)
    return (g - (dt / dx) * update).reshape(values.shape)


# ---------------------------------------------------------------------------
# Shared implementation of the 4 abstract hooks
# ---------------------------------------------------------------------------
class _ScaffoldSolver(SolverAdapter):
    """Implements the 4 abstract hooks once, parameterised by ``KERNEL``."""

    KERNEL: str = "diffusion"
    ALPHA: float = 0.1
    SPEED: float = 0.5

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(config)
        # kernel parameters are derived in the constructor (not in init) so a
        # restored checkpoint always finds them, whatever order the caller uses
        self._alpha = float(self.config.get("alpha", type(self).ALPHA))
        self._speed = float(self.config.get("speed", type(self).SPEED))
        self._grid = self._compute_grid()

    def _compute_grid(self) -> Tuple[int, ...]:
        dim = self._mesh.dim
        n = int(round(self._mesh.n_cells ** (1.0 / dim)))
        if n ** dim != self._mesh.n_cells:
            raise AdapterError(
                f"adapter {self.SOLVER_ID!r} needs a structured n^dim mesh, got "
                f"{self._mesh.n_cells} cells in {dim}-D"
            )
        return (n,) * dim

    # -- init ------------------------------------------------------------
    def init(self, config: Optional[Mapping[str, Any]] = None) -> None:
        merged: Dict[str, Any] = {**self.config, **(dict(config or {}))}
        alpha = float(merged.get("alpha", type(self).ALPHA))
        speed = float(merged.get("speed", type(self).SPEED))
        if alpha <= 0.0:
            raise AdapterError(f"alpha must be positive, got {alpha}")
        if speed == 0.0 or abs(speed) > 1e3:
            raise AdapterError(f"speed must be non-zero and bounded, got {speed}")
        self.config.update(merged)
        self._alpha = alpha
        self._speed = speed
        self._seed_fields()

    def _seed_fields(self) -> None:
        """Deterministic synthetic initial condition per declared field.

        The mesh is the structured uniform box the base class builds, so the flat
        payload reshapes to ``(n, n, n)`` in C order (last axis fastest) and the
        stencil kernels can act on it without a connectivity table.
        """
        dim = self._mesh.dim
        centers = self._mesh.centers
        extent = np.maximum(self._mesh.extent, 1e-30)
        xi = (centers - self._mesh.lo.min(axis=0)) / extent
        for spec in type(self).FIELDS:
            axis = spec.face_normal if spec.face_normal >= 0 else 0
            axis %= dim
            base = 1.0 if spec.kind == QuantityKind.DENSITY.value else 0.5
            if spec.kind == QuantityKind.FLUX.value:
                profile = base + 0.25 * np.cos(np.pi * xi[:, axis])
            else:
                profile = base + 0.25 * np.cos(2.0 * np.pi * xi[:, axis])
            if spec.kind == QuantityKind.EXTENSIVE.value:
                profile = profile * self._mesh.volume
            self._fields[spec.name] = np.asarray(profile, dtype=float).reshape(self._mesh.n_cells)

    # -- step ------------------------------------------------------------
    def step(self, dt: float) -> StepReport:
        dt = float(dt)
        if not np.isfinite(dt) or dt <= 0.0:
            raise AdapterError(f"dt must be a positive finite number, got {dt!r}")
        if type(self).KERNEL == "static":
            self._time += dt
            self._step_index += 1
            return self._report(dt)
        spacing = tuple(float(v) for v in self.mesh.extent / np.array(self._grid, dtype=float))
        if type(self).KERNEL == "diffusion":
            stability = dt * self._alpha * 2.0 * self._mesh.dim / min(spacing) ** 2
            if stability > 0.5:
                raise AdapterError(
                    "explicit diffusion update is unstable at this dt "
                    f"(dt*alpha*2*dim/dx^2 = {stability:.3f} > 0.5)"
                )
            for name, values in self._fields.items():
                self._fields[name] = values + dt * self._alpha * laplacian(values, self._grid)
        elif type(self).KERNEL == "upwind":
            cfl = abs(self._speed) * dt / min(spacing)
            if cfl > 1.0:
                raise AdapterError(
                    f"upwind update violates CFL (|u|*dt/dx = {cfl:.3f} > 1)"
                )
            for name, values in self._fields.items():
                self._fields[name] = upwind_flux_form(
                    values, self._grid, spacing, self._speed, dt, axis=0
                )
        else:
            raise AdapterError(f"unknown kernel {type(self).KERNEL!r}")
        self._time += dt
        self._step_index += 1
        return self._report(dt)

    def _report(self, dt: float) -> StepReport:
        stacked = np.concatenate([np.ravel(v) for v in self._fields.values()]) \
            if self._fields else np.zeros(1)
        return StepReport(
            step_index=self._step_index,
            time=self._time,
            dt=dt,
            max_abs=float(np.max(np.abs(stacked))),
            finite=bool(np.all(np.isfinite(stacked))),
            state_digest=self._state_digest(),
        )

    def _state_digest(self) -> str:
        return canonical_digest(
            {
                "solver_id": self.SOLVER_ID,
                "step_index": self._step_index,
                "time": self._time,
                "fields": {k: [float(v) for v in vals] for k, vals in sorted(self._fields.items())},
            }
        )

    # -- extra checkpoint state ------------------------------------------
    def _state_payload(self) -> Dict[str, Any]:
        return {
            "alpha": float(self._alpha),
            "speed": float(self._speed),
            "grid": list(self._grid),
        }

    def _load_state_payload(self, payload: Any) -> None:
        if not isinstance(payload, Mapping):
            raise AdapterError("checkpoint payload 'extra' must be a mapping")
        if not payload:
            return
        try:
            self._alpha = float(payload["alpha"])
            self._speed = float(payload["speed"])
            grid = tuple(int(v) for v in payload["grid"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError(f"malformed kernel state in checkpoint: {exc}") from exc
        if len(grid) != self._mesh.dim or int(np.prod(grid)) != self._mesh.n_cells:
            raise AdapterError(
                f"checkpoint kernel grid {grid} does not match mesh {self._mesh.n_cells} cells"
            )
        self._grid = grid

    # -- get_output ------------------------------------------------------
    def get_output(self, field_name: str) -> Field:
        spec = self._spec(field_name)
        if field_name not in self._fields:
            raise FieldLookupError(f"{self.SOLVER_ID}: field {field_name!r} has no payload")
        return Field(
            values=np.asarray(self._fields[field_name], dtype=float),
            mesh=self.mesh,
            kind=QuantityKind(spec.kind),
            name=spec.name,
            face_normal=spec.face_normal,
        )

    # -- cleanup ---------------------------------------------------------
    def cleanup(self) -> None:
        self._fields = {}


class LBMSolverAdapter(_ScaffoldSolver):
    SOLVER_ID = "lbm"
    VERSION = "1.0.0"
    KERNEL = "diffusion"
    FIELDS = _variants(DENSITY, VELOCITY, OFFSET)
    BOUNDARIES = _BOUNDARIES_3D


class FEMSolverAdapter(_ScaffoldSolver):
    SOLVER_ID = "fem"
    VERSION = "1.0.0"
    KERNEL = "diffusion"
    ALPHA = 0.05
    FIELDS = _variants(DENSITY, OFFSET, VELOCITY)
    BOUNDARIES = _BOUNDARIES_3D


class FVMSolverAdapter(_ScaffoldSolver):
    SOLVER_ID = "fvm"
    VERSION = "1.0.0"
    KERNEL = "upwind"
    FIELDS = _variants(DENSITY, VELOCITY, FLUX)
    BOUNDARIES = _BOUNDARIES_3D


class DGGSolverAdapter(_ScaffoldSolver):
    SOLVER_ID = "dg"
    VERSION = "1.0.0"
    KERNEL = "upwind"
    FIELDS = _variants(DENSITY, FLUX)
    BOUNDARIES = _BOUNDARIES_3D


class SPHSolverAdapter(_ScaffoldSolver):
    SOLVER_ID = "sph"
    VERSION = "1.0.0"
    KERNEL = "diffusion"
    FIELDS = _variants(DENSITY, VELOCITY)
    BOUNDARIES = _BOUNDARIES_3D


class DEMSolverAdapter(_ScaffoldSolver):
    SOLVER_ID = "dem"
    VERSION = "1.0.0"
    KERNEL = "upwind"
    FIELDS = (
        FieldSpec(
            name="density",
            kind=QuantityKind.EXTENSIVE.value,
            dim_vector=(1, 0, 0, 0),
            description="per-cell mass carrier (extensive: the total is a plain sum)",
        ),
    ) + _variants(VELOCITY)
    BOUNDARIES = _BOUNDARIES_3D


class BEMSolverAdapter(_ScaffoldSolver):
    SOLVER_ID = "bem"
    VERSION = "1.0.0"
    KERNEL = "diffusion"
    ALPHA = 0.02
    FIELDS = _variants(DENSITY, FLUX)
    BOUNDARIES = _BOUNDARIES_3D


class CADSolverAdapter(_ScaffoldSolver):
    SOLVER_ID = "cad"
    VERSION = "1.0.0"
    KERNEL = "static"
    FIELDS = _variants(DENSITY)
    BOUNDARIES = _BOUNDARIES_3D


ADAPTER_CLASSES: Dict[str, Type[SolverAdapter]] = {
    cls.SOLVER_ID: cls
    for cls in (
        LBMSolverAdapter,
        FEMSolverAdapter,
        FVMSolverAdapter,
        DEMSolverAdapter,
        BEMSolverAdapter,
        SPHSolverAdapter,
        DGGSolverAdapter,
        CADSolverAdapter,
    )
}


def all_solver_ids() -> Tuple[str, ...]:
    return tuple(sorted(ADAPTER_CLASSES))


def adapter_class(solver_id: str) -> Type[SolverAdapter]:
    try:
        return ADAPTER_CLASSES[solver_id]
    except KeyError:
        raise AdapterError(
            f"unknown solver id {solver_id!r}; registered: {sorted(ADAPTER_CLASSES)}"
        ) from None


def build_adapter(solver_id: str, **config: Any) -> SolverAdapter:
    """Instantiate (not initialise) the adapter for ``solver_id``."""
    return adapter_class(solver_id)(config)