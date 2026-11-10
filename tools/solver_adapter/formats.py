"""Declared native I/O formats for the eight libraries, and their protocol mapping.

Every library exposes *records* — arrays with a shape, a dtype and a physical
dimension — that must cross library boundaries.  This module declares, for each
of the eight solvers, which of those records exist and which protocol field they
map to, and :mod:`tools.solver_adapter.format_coverage` machine-checks that the
mapping covers 100 % of them.

Provenance of the declarations (stated plainly, because it bounds the claim)
---------------------------------------------------------------------------
The eight solver repositories are not vendored here, so these records are
declared by the adapter scaffold on the *protocol* side.  ``verified_against_repo``
is therefore ``False`` for every entry: the coverage scan proves internal
consistency (each declared record has a protocol field of the same kind and
dimensional exponents, both directions, all eight solvers), **not** that a real
library's HDF5/NumPy output has been inspected.  Promoting an entry to
``verified_against_repo=True`` requires a pointer to the library source or an
artifact captured from it; that is the follow-up work, and it is not claimed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

__all__ = ["IOFormat", "NATIVE_FORMATS", "formats_for"]


@dataclass(frozen=True)
class IOFormat:
    """One native record and the protocol field it is mapped to."""

    solver_id: str
    direction: str                 # "input" | "output"
    record: str                    # native record name
    shape: str                     # symbolic shape, e.g. "(n_cells,)"
    dtype: str
    dim_vector: Tuple[int, int, int, int]
    protocol_field: str            # "" means *unmapped* (coverage must then fail)
    verified_against_repo: bool = False

    def __post_init__(self) -> None:
        if self.direction not in ("input", "output"):
            raise ValueError(f"direction must be input|output, got {self.direction!r}")
        if len(self.dim_vector) != 4:
            raise ValueError("dim_vector must have four exponents")


def _f(solver_id: str, direction: str, record: str, protocol_field: str,
       dim_vector: Tuple[int, int, int, int], shape: str = "(n_cells,)",
       dtype: str = "float64") -> IOFormat:
    return IOFormat(solver_id, direction, record, shape, dtype, dim_vector, protocol_field)


NATIVE_FORMATS: Dict[str, Tuple[IOFormat, ...]] = {
    "lbm": (
        _f("lbm", "output", "moment_scalar", "density", (1, -3, 0, 0)),
        _f("lbm", "output", "velocity_component", "velocity_x", (0, 1, -1, 0)),
        _f("lbm", "input", "initial_scalar", "density", (1, -3, 0, 0)),
        _f("lbm", "input", "mesh_offset", "offset_x", (0, 1, 0, 0)),
    ),
    "fem": (
        _f("fem", "output", "nodal_to_cell", "density", (1, -3, 0, 0)),
        _f("fem", "output", "dof_component", "offset_x", (0, 1, 0, 0)),
        _f("fem", "input", "cell_scalar", "density", (1, -3, 0, 0)),
        _f("fem", "input", "dof_component", "offset_x", (0, 1, 0, 0)),
    ),
    "fvm": (
        _f("fvm", "output", "cell_average", "density", (1, -3, 0, 0)),
        _f("fvm", "output", "face_flux", "flux_x", (1, -2, -1, 0), shape="(n_faces,)", dtype="float64"),
        _f("fvm", "input", "cell_average", "density", (1, -3, 0, 0)),
        _f("fvm", "input", "face_flux", "flux_x", (1, -2, -1, 0), shape="(n_faces,)", dtype="float64"),
    ),
    "dem": (
        _f("dem", "output", "particle_mass", "density", (1, 0, 0, 0)),
        _f("dem", "output", "particle_velocity", "velocity_x", (0, 1, -1, 0)),
        _f("dem", "input", "velocity_component", "velocity_x", (0, 1, -1, 0)),
    ),
    "bem": (
        _f("bem", "output", "panel_scalar", "density", (1, -3, 0, 0)),
        _f("bem", "output", "panel_flux", "flux_x", (1, -2, -1, 0), shape="(n_faces,)", dtype="float64"),
        _f("bem", "input", "panel_scalar", "density", (1, -3, 0, 0)),
        _f("bem", "input", "panel_flux", "flux_x", (1, -2, -1, 0), shape="(n_faces,)", dtype="float64"),
    ),
    "sph": (
        _f("sph", "output", "particle_average", "density", (1, -3, 0, 0)),
        _f("sph", "output", "particle_velocity", "velocity_x", (0, 1, -1, 0)),
        _f("sph", "input", "particle_average", "density", (1, -3, 0, 0)),
        _f("sph", "input", "particle_velocity", "velocity_x", (0, 1, -1, 0)),
    ),
    "dg": (
        _f("dg", "output", "modal_to_cell", "density", (1, -3, 0, 0)),
        _f("dg", "output", "numerical_flux", "flux_x", (1, -2, -1, 0), shape="(n_faces,)", dtype="float64"),
        _f("dg", "input", "modal_to_cell", "density", (1, -3, 0, 0)),
        _f("dg", "input", "numerical_flux", "flux_x", (1, -2, -1, 0), shape="(n_faces,)", dtype="float64"),
    ),
    "cad": (
        _f("cad", "output", "volume_measure", "density", (1, -3, 0, 0)),
        _f("cad", "input", "cell_scalar", "density", (1, -3, 0, 0)),
    ),
}


def formats_for(solver_id: str) -> Tuple[IOFormat, ...]:
    try:
        return NATIVE_FORMATS[solver_id]
    except KeyError:
        raise KeyError(f"no native formats declared for solver {solver_id!r}") from None