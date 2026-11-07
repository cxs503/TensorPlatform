"""Shared wall-force extractor (staircase surface integration).

Both solvers (FVM and LBM) hand the *same* cell-centred fields ``(p, u, v)`` and
the *same* solid mask to :func:`surface_force`, so the drag of the obstruction
set is by construction the same functional of the discrete solution in both
discretisations.  Any remaining difference is therefore a modelling /
discretisation difference, not an extraction artefact.

Discretisation
--------------
For every solid cell face adjacent to a fluid cell, with ``n`` the outward
normal of the solid (pointing into the fluid) and the fluid cell at distance
``h/2``:

* normal viscous stress from the no-slip wall, first-order one-sided gradient
  ``dU/dn = 2 U_f / h``  (exact for a parabolic profile to ``O(h/H)``);
* pressure evaluated at the face by linear extrapolation from the two fluid
  cells along the normal (second order in ``h``):

      p_wall = 1.5 p_(1) - 0.5 p_(2)

  falling back to ``p_(1)`` when the second cell is not fluid.

Traction on the solid::

    F = sum_faces A * ( -p_wall * n + mu * (dU/dn_fluid) )

with ``dU/dn_fluid = 2 U_f / h`` the wall-normal derivative taken from the fluid
cell towards the wall.  For a vertical face (``n = (+-1, 0)``) this gives
``Fx = -p n_x + 4 mu u_f/h``, and for a horizontal face (``n = (0, +-1)``)
``Fx = +2 mu u_f/h``, ``Fy = -p n_y + 4 mu v_f/h``.  Note that the tangential
(friction) contribution is *always* in the direction of the local fluid
velocity: an early version multiplied it by ``n`` as well, which made the two
channel walls cancel exactly -- the wall-friction verification in
``scripts/vv/benchmarks.py`` catches that error.

Verification: applied to the two channel walls of an *unobstructed* channel, the
extractor reproduces the analytic wall friction force
``g rho H Lx / 2`` per wall to better than 2 % (see ``tests/vv``), and applied
to a body-free domain it returns zero.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np

__all__ = ["surface_force", "shift_field"]

_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def shift_field(a: np.ndarray, dx: int, dy: int) -> Tuple[np.ndarray, np.ndarray]:
    """``out[j, i] = a[j + dy, i + dx]`` (x periodic, y non-periodic).

    Returns ``(out, valid)`` where ``valid`` is False wherever the shifted index
    falls outside the domain.  ``NaN``-safe for integer/boolean inputs.
    """
    out = np.zeros_like(a)
    valid = np.zeros(a.shape, dtype=bool)
    ny, nx = a.shape
    j_src = slice(max(0, dy), ny + min(0, dy))
    j_dst = slice(max(0, -dy), ny - max(0, dy))
    rolled = np.roll(a, -dx, axis=1)          # x periodic: out[i] = a[i + dx]
    out[j_dst, :] = rolled[j_src, :]
    valid[j_dst, :] = True
    return out, valid


def surface_force(
    p: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    solid: np.ndarray,
    h: float,
    mu: float,
) -> Tuple[float, float]:
    """Net force on the solid bodies (per unit depth) from the discrete fields."""
    if not (p.shape == u.shape == v.shape == solid.shape):
        raise ValueError("p, u, v and solid must share the same shape")
    fluid = ~solid
    fx = 0.0
    fy = 0.0
    for dx, dy in _DIRS:
        fluid_nb, valid = shift_field(fluid, dx, dy)
        face = solid & fluid_nb & valid
        if not face.any():
            continue
        p1, v1 = shift_field(p, dx, dy)
        u1, _v = shift_field(u, dx, dy)
        v_1, _v = shift_field(v, dx, dy)
        # second fluid cell along the normal, for the pressure extrapolation
        p2, v2 = shift_field(p, 2 * dx, 2 * dy)
        fluid2, _v = shift_field(fluid, 2 * dx, 2 * dy)
        p_wall = np.where(fluid2 & v2, 1.5 * p1 - 0.5 * p2, p1)
        n_x, n_y = float(dx), float(dy)
        a = h  # face length per unit depth
        # A face normal to x: the x-traction is the pressure only (the
        # *tangential* velocity v is the one tied to the wall by no-slip, so it
        # carries the y-traction 2 mu v_f/h; the normal derivative of the
        # normal velocity is NOT a no-slip quantity and 2 mu du/dx at the wall
        # is an O(1/Re) term that must not be faked with a one-sided gradient --
        # doing so added a spurious +4 mu u_f/h that dominated the budget).
        if dx != 0:
            fx += float((a * (-p_wall * n_x))[face].sum())
            fy += float((a * (2.0 * mu * v_1 / h))[face].sum())
        else:
            fx += float((a * (2.0 * mu * u1 / h))[face].sum())
            fy += float((a * (-p_wall * n_y))[face].sum())
    return fx, fy