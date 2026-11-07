"""Case library for the W10-N07 numerical-uncertainty certification.

Everything is expressed in *dimensionless lattice-consistent units* (length in
cells ``h``, velocity in units of the reference velocity ``U0``, density 1), so
that the finite-volume and lattice-Boltzmann solvers can be handed the very
same geometry, the very same body force and the very same Reynolds number.

Physical background
-------------------
The resistance of a ship advancing in an ice channel is modelled here by the
canonical *idealised ice-channel obstruction problem*: a plane channel of
height ``H`` (the ice channel between the ice-sheet underside and the sea bed
in a vertical cut, or between the channel walls in a horizontal cut) with the
resistance-generating ice features idealised as blunt obstructions that the
flow must pass.  The quantity of interest is the dimensionless drag
coefficient of the obstruction set

    C_D = F_x / (0.5 * rho * Ubar**2 * A_front),

with ``Ubar`` the bulk velocity (measured, not imposed) and ``A_front`` the
frontal projection of the obstructions.  ``C_D`` is the resistance-generating
coefficient in the same sense as the form-drag coefficient of a hull: it is a
pure output of the discretised flow problem, which is what a GCI study needs.

Two representation cases
------------------------
``level``  continuous level-ice constriction: one square obstruction of side
           ``D`` spanning 25 % of the channel height (blockage ``D/H = 0.25``),
           i.e. the ice channel throttled by a continuous ice keel/berm.
``brash``  broken-ice field: three square floes of side ``d = D/3`` staggered
           across the channel (broached brash-ice pieces that must be pushed
           aside), total frontal area ``3 d``.

Both live in a channel that is **periodic in x** with length ``Lx = 8 D`` and
height ``H = 4 D``.  Periodicity is not a shortcut: the periodic cell is the
standard idealisation of a ship advancing through an (quasi-)homogeneous ice
field, and it removes inlet/outlet boundary conditions as a spurious
grid-dependent error source, which is exactly what one wants when the
*discretisation* error is the measurement target.

Grid triples
------------
All triples satisfy ``r21, r32 >= 1.25`` (ITTC guidance).  Both use the non-uniform
triple ``(r21, r32) = (4/3, 5/4)``, which exercises the general observed-order
solver of :mod:`scripts.vv.gci` on real data instead of only on synthetic
vectors (the uniform-ratio closed form cannot be used there).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

__all__ = [
    "ChannelCase",
    "LEVEL_ICE",
    "BRASH_ICE",
    "GRID_TRIPLES",
    "solid_mask",
    "channel_force",
    "build_case",
]


@dataclass(frozen=True)
class ChannelCase:
    """Idealised ice-channel obstruction case (periodic in x)."""

    name: str
    kind: str                 # "level" | "brash"
    D: float                  # reference (obstruction) length, in cells
    H_over_D: float = 4.0     # channel height / obstruction length
    L_over_D: float = 8.0     # periodic length / obstruction length
    re_ref: float = 20.0      # Re = U0 * D / nu
    u0: float = 0.06          # reference velocity (lattice units)
    description: str = ""
    _floes: Tuple[Tuple[float, float, float], ...] = ()   # (x_c/D, y_c/H, side/D)

    # -- geometry -------------------------------------------------------
    @property
    def H(self) -> float:
        return self.H_over_D * self.D

    @property
    def Lx(self) -> float:
        return self.L_over_D * self.D

    @property
    def nu(self) -> float:
        """Kinematic viscosity that fixes ``Re = u0 * d_floe / nu``."""
        return self.u0 * self.d_ref / self.re_ref

    @property
    def d_ref(self) -> float:
        """Length scale of the Reynolds number: always the obstruction-set scale D.

        Using a *common* ``D`` for both cases keeps the ice-water viscosity
        identical across the case library (``nu = u0 D / Re``).  An earlier
        version scaled ``nu`` with the individual floe size for the brash case,
        which pushed the cell Peclet number to ~5.6 and made the central-flux
        finite-volume solver diverge -- a numerical artefact of the case
        definition, not physics.  The floe-scale Reynolds number is ``Re/3``.
        """
        return self.D

    @property
    def floes(self) -> Tuple[Tuple[float, float, float], ...]:
        if self._floes:
            return self._floes
        if self.kind == "empty":
            return ()
        if self.kind == "level":
            return ((0.5, 0.5, 1.0),)
        # three staggered brash-ice floes, side D/3
        return ((0.25, 0.30, 1.0 / 3.0), (0.55, 0.70, 1.0 / 3.0), (0.85, 0.30, 1.0 / 3.0))

    @property
    def frontal_area(self) -> float:
        """Sum of the frontal (y) projections of the obstructions, in cells."""
        return sum(self.D * s for (_x, _y, s) in self.floes)

    def body_force(self) -> float:
        """Body force per unit mass driving the channel.

        Chosen so that the *unobstructed* plane channel would carry the bulk
        velocity ``u0``: ``u0 = g H**2 / (12 nu)`` for plane Poiseuille.
        """
        return 12.0 * self.nu * self.u0 / self.H ** 2

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        d.update(
            H=self.H,
            Lx=self.Lx,
            nu=self.nu,
            d_ref=self.d_ref,
            frontal_area=self.frontal_area,
            body_force=self.body_force(),
            floes=[list(f) for f in self.floes],
        )
        return d


LEVEL_ICE = ChannelCase(
    name="level_ice_constriction",
    kind="level",
    D=1.0,            # scaled by the grid level, see build_case()
    re_ref=20.0,
    u0=0.06,
    description="single square ice constriction, blockage D/H = 0.25",
)

BRASH_ICE = ChannelCase(
    name="brash_ice_field",
    kind="brash",
    D=1.0,
    re_ref=20.0,
    u0=0.06,
    description="three staggered square floes (side D/3), total frontal area D",
)

#: (nx, H_cells, D_cells) triples; ``H_cells`` is the *open* channel height, so
#: the array shape is ``(H_cells + 2, nx)`` (two staircase wall rows)
GRID_TRIPLES: Dict[str, List[Tuple[int, int, float]]] = {
    "level": [(72, 36, 9.0), (96, 48, 12.0), (120, 60, 15.0)],     # r = 4/3, 5/4
    "brash": [(72, 36, 9.0), (96, 48, 12.0), (120, 60, 15.0)],     # r = 4/3, 5/4
}


def build_case(kind: str, grid_level: int) -> Tuple[ChannelCase, int, int]:
    """Return ``(case, nx, ny)`` for grid level 0 (fine) .. 2 (coarse).

    ``ny`` is the full array height, i.e. the open channel height plus the two
    staircase wall rows, so that ``ny - 2 == 4 D`` for every member of a triple.
    """
    if kind not in GRID_TRIPLES:
        raise KeyError(f"unknown case kind {kind!r}")
    nx, ny, D = GRID_TRIPLES[kind][grid_level]
    base = LEVEL_ICE if kind == "level" else BRASH_ICE
    case = ChannelCase(
        name=base.name,
        kind=base.kind,
        D=D,
        H_over_D=base.H_over_D,
        L_over_D=base.L_over_D,
        re_ref=base.re_ref,
        u0=base.u0,
        description=base.description,
    )
    return case, nx, ny + 2


def solid_mask(case: ChannelCase, nx: int, ny: int, walls: bool = True) -> np.ndarray:
    """Boolean solid mask on an ``(ny, nx)`` cell grid.

    Cell ``(j, i)`` is centred at ``((i + 0.5) h, (j + 0.5) h)``.  With
    ``walls=True`` the first and last rows are solid wall cells, so the open
    channel height is ``ny - 2`` cells and no-slip sits at ``y = h`` and
    ``y = (ny-1) h``, which is the halfway-bounce-back wall of the LBM and the
    mirror-ghost wall of the FVM.

    Obstructions are placed by **cell index**, not by a continuous coordinate
    test: each floes covers exactly ``round(side/h) x round(side/h)`` cells.
    A continuous ``|x - x_c| <= side/2`` test would cover 10 cells for a side of
    9 whenever the centre falls on a cell *face* -- the discretised body then
    changes shape between refinement levels and the three-grid GCI is no longer
    measuring the discretisation error of one geometry (measured: the brash-ice
    drag then fails to converge at all between levels).

    Both solvers consume *this* mask, so the two discretisations see the same
    staircase geometry; the cross-solver comparison therefore measures modelling
    differences, not geometry differences.
    """
    solid = np.zeros((ny, nx), dtype=bool)
    y_off = 1 if walls else 0
    h_open = (ny - 2) if walls else ny
    for (xf, yf, sf) in case.floes:
        n = int(round(sf * case.D / 1.0))
        if n < 1:
            continue
        i0 = int(round(xf * case.Lx - 0.5 * n))
        j0 = int(round(y_off + yf * h_open - 0.5 * n))
        j0 = max(1 if walls else 0, min(j0, (ny - 2 if walls else ny - 1) - n + 1))
        i_idx = (np.arange(n) + i0) % nx
        j_idx = np.arange(n) + j0
        solid[np.ix_(j_idx, i_idx)] = True
    if walls:
        solid[0, :] = True
        solid[-1, :] = True
    return solid


def channel_force(case: ChannelCase) -> float:
    """Body force per unit mass (see :meth:`ChannelCase.body_force`)."""
    return case.body_force()