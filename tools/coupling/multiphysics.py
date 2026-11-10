"""Multi-physics coupling verification framework (domain-neutral).

A coupled multi-solver run is only trustworthy if five statements can be *shown*
to hold, each with a number and a threshold instead of an opinion:

``A1 interface flux conservation``
    The flux that crosses a coupling interface must be the same whether it is
    computed by the side that sends it or by the side that receives it; the
    relative imbalance per face is the coupling error.
``A2 global conservation ledger``
    For every conserved quantity the global budget must close: (flux out) -
    (flux in) - (interface sources) - (volumetric sources) = 0, to the accuracy
    of the data actually exchanged.
``A3 coupling iteration convergence``
    The interface unknowns must converge geometrically; the observed order ``p``
    of the residual sequence (classical ``r_{n+1} ~ r_n**p`` regression) and the
    spectral ratio ``rho`` are both measured from the run.
``A4 error propagation``
    A relative perturbation of one input parameter must not be amplified by the
    coupling: the sensitivity coefficient ``S = (dy/y)/(dp/p)`` must stay below
    one, and is cross-checked against a closed-form value.
``A5 negative control``
    With the coupling switched off, the coupled driver must reproduce the
    single-domain solution — a framework that leaks interaction when nobody
    asked for it fails here.

Model used by the reference driver (a *generic* two-code transport chain, not a
physical case): ``N`` blocks ``[x_{i-1}, x_i]`` each holding
``-d/dx (k_i du/dx) = f_i``, joined at ``N-1`` coupling faces carrying surface
sources ``s_i`` (so the flux jumps by ``s_i`` there), with ``u`` prescribed at
the two outer ends.  That model is chosen because it has a closed-form solution,
so every measured error has an independent reference; the framework itself only
sees blocks, coefficients, sources and faces.

Nothing here knows what ``u``, ``k`` or the quantity labels mean: quantity names
are strings supplied at the call site and the module never branches on them.

Everything is deterministic: the same case spec yields the same run digest, which
is what lets a report bind a number to a run.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "Block",
    "BlockSolution",
    "InterfaceCase",
    "CoupledRun",
    "AnalyticSolution",
    "solve_block_dirichlet",
    "analytic_solution",
    "analytic_block_values",
    "run_coupled",
    "convergence_metrics",
    "interface_anchor",
    "budget_anchor",
    "sensitivity_anchor",
    "decoupling_anchor",
    "acceptance_record",
    "case_digest",
    "perturb_case",
]

# acceptance thresholds of this node, kept in one place so a report cannot
# quote a threshold that the gate did not actually use
THRESHOLDS = {
    "interface_flux_rel_error": 0.01,  # A1
    "global_budget_rel_error": 0.02,  # A2
    "convergence_order": 1.0,  # A3
    "order_estimator_tolerance": 1e-3,  # A3: regression noise floor of the order estimate
    "spectral_ratio": 1.0,  # A3 (must be < 1: residual decays)
    "sensitivity_coefficient": 1.0,  # A4
    "decoupled_deviation": 0.001,  # A5
}


# ---------------------------------------------------------------------------
# Block: one sub-domain of the chain, discretised on its own graded mesh
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    """One sub-domain: length, coefficient, volumetric source, own cell layout.

    ``grading`` > 1 stretches the cells geometrically along the block, the way
    two independent codes never share a cell layout.  Each side keeps its own
    mesh — the framework must not require a matching interface discretisation.
    """

    length: float
    coefficient: float
    source: float = 0.0
    n_cells: int = 20
    grading: float = 1.04

    def __post_init__(self) -> None:
        if not self.length > 0.0:
            raise ValueError("block length must be positive")
        if not self.coefficient > 0.0:
            raise ValueError("block coefficient must be positive")
        if int(self.n_cells) < 3:
            raise ValueError("a block needs at least three cells")
        if not self.grading > 0.0:
            raise ValueError("block grading must be positive")

    @property
    def volume(self) -> float:
        return float(self.length)

    def edges(self) -> np.ndarray:
        """Cell edges ``(n_cells + 1,)`` — geometric grading, exact total length."""
        n = int(self.n_cells)
        if abs(self.grading - 1.0) < 1e-12:
            widths = np.ones(n, dtype=float)
        else:
            widths = self.grading ** np.arange(n, dtype=float)
        widths = widths / widths.sum() * float(self.length)
        return np.concatenate(([0.0], np.cumsum(widths)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "length": self.length,
            "coefficient": self.coefficient,
            "source": self.source,
            "n_cells": int(self.n_cells),
            "grading": self.grading,
        }


@dataclass(frozen=True)
class BlockSolution:
    """Cell-centred solution of one block plus the fluxes *that block* computed."""

    centers: np.ndarray
    values: np.ndarray
    widths: np.ndarray
    flux_left: float
    flux_right: float

    def right_face_value(self) -> float:
        """Second-order one-sided extrapolation of the cell averages to the right face."""
        if self.values.size < 2:
            return float(self.values[-1])
        return float(1.5 * self.values[-1] - 0.5 * self.values[-2])

    def left_face_value(self) -> float:
        if self.values.size < 2:
            return float(self.values[0])
        return float(1.5 * self.values[0] - 0.5 * self.values[1])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_cells": int(self.values.size),
            "flux_left": self.flux_left,
            "flux_right": self.flux_right,
            "value_min": float(self.values.min()),
            "value_max": float(self.values.max()),
        }


# ---------------------------------------------------------------------------
# The generic case: a chain of blocks joined by coupling faces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InterfaceCase:
    """Geometry + coefficients + sources + coupling numerics for one coupled run.

    ``quantity`` is a free label (the reference driver passes ``"mass"``,
    ``"momentum"``, ``"energy"``); the framework never interprets it — it only
    records it so the ledger can be tabulated per quantity.

    ``transfer`` selects how the value carried across a face is produced:

    ``"halo"``  the sending side extrapolates its own cell averages to the face
                (what independent codes really do; costs an O(h**2) consistency
                error at the interface);
    ``"face"``  the face value itself is carried (exact for this model; used to
                isolate the mechanism from the discretisation).
    """

    name: str
    blocks: Tuple[Block, ...]
    interface_sources: Tuple[float, ...]
    u_left: float
    u_right: float
    quantity: str = "conserved"
    tolerance: float = 1e-10
    max_iterations: int = 120
    relaxation: float = 0.5
    transfer: str = "face"

    def __post_init__(self) -> None:
        if len(self.blocks) < 2:
            raise ValueError("a coupled case needs at least two blocks")
        if len(self.interface_sources) != len(self.blocks) - 1:
            raise ValueError("need one interface source per coupling face")
        if self.transfer not in ("halo", "face"):
            raise ValueError("transfer must be 'halo' or 'face'")
        if not 0.0 < self.relaxation <= 2.0:
            raise ValueError("relaxation must lie in (0, 2]")

    @property
    def n_faces(self) -> int:
        return len(self.blocks) - 1

    @property
    def total_length(self) -> float:
        return float(sum(b.length for b in self.blocks))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "quantity": self.quantity,
            "blocks": [b.to_dict() for b in self.blocks],
            "interface_sources": list(self.interface_sources),
            "u_left": self.u_left,
            "u_right": self.u_right,
            "tolerance": self.tolerance,
            "max_iterations": int(self.max_iterations),
            "relaxation": self.relaxation,
            "transfer": self.transfer,
        }

    def digest(self) -> str:
        return case_digest(self)


def case_digest(case: InterfaceCase) -> str:
    blob = json.dumps(case.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


# ---------------------------------------------------------------------------
# One block solve: conservative finite volume, Dirichlet at both ends
# ---------------------------------------------------------------------------


def solve_block_dirichlet(block: Block, u_left: float, u_right: float) -> BlockSolution:
    """Solve ``-d/dx(k du/dx) = f`` on one block with Dirichlet data at both ends.

    Conservative face fluxes (one flux per face) so the discrete divergence
    theorem holds cell by cell; the returned ``flux_left``/``flux_right`` are
    computed by *this* block from *its own* stencil — exactly the data a coupling
    interface has to agree on.
    """
    edges = block.edges()
    widths = np.diff(edges)
    centers = 0.5 * (edges[:-1] + edges[1:])
    n = widths.size
    k = float(block.coefficient)
    f = float(block.source)

    mat = np.zeros((n, n), dtype=float)
    rhs = np.zeros(n, dtype=float)
    for j in range(n):
        if j == 0:
            g = k / (0.5 * widths[0])
            mat[j, j] += g
            rhs[j] += g * float(u_left)
        else:
            g = k / (0.5 * (widths[j - 1] + widths[j]))
            mat[j, j] += g
            mat[j, j - 1] -= g
        if j == n - 1:
            g = k / (0.5 * widths[-1])
            mat[j, j] += g
            rhs[j] += g * float(u_right)
        else:
            g = k / (0.5 * (widths[j] + widths[j + 1]))
            mat[j, j] += g
            mat[j, j + 1] -= g
        rhs[j] += f * widths[j]

    values = np.linalg.solve(mat, rhs)
    g_left = k / (0.5 * widths[0])
    g_right = k / (0.5 * widths[-1])
    flux_left = -g_left * (values[0] - float(u_left))
    flux_right = -g_right * (float(u_right) - values[-1])
    return BlockSolution(
        centers=centers,
        values=values,
        widths=widths,
        flux_left=float(flux_left),
        flux_right=float(flux_right),
    )


def analytic_block_values(
    block: Block, u_left: float, u_right: float, x: Optional[Iterable[float]] = None
) -> np.ndarray:
    """Closed-form block solution — independent of the finite-volume assembly."""
    k = float(block.coefficient)
    f = float(block.source)
    length = float(block.length)
    slope = (float(u_right) - float(u_left) + f * length * length / (2.0 * k)) / length
    xi = np.asarray(list(x) if x is not None else block.edges(), dtype=float)
    return float(u_left) + slope * xi - f / (2.0 * k) * xi * xi


# ---------------------------------------------------------------------------
# Closed-form reference for the whole chain
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalyticSolution:
    """Closed-form interface values and face fluxes for one case.

    Per block ``i``: ``u_i(xi) = A_i + B_i xi - f_i/(2 k_i) xi**2`` and
    ``q_i(xi) = -k_i B_i + f_i xi`` (``xi`` from the block's left end).  The
    unknowns ``(A_i, B_i)`` solve ``2 + 2(N-1)`` linear conditions: two outer
    Dirichlet values, ``N-1`` value continuities and ``N-1`` flux jumps.
    """

    block_a: np.ndarray
    block_b: np.ndarray
    interface_values: np.ndarray
    flux_left_side: np.ndarray  # flux at the face seen from block i
    flux_right_side: np.ndarray  # flux at the face seen from block i+1
    throughput: float  # flux at the far left end

    def value_at(self, block_index: int, xi) -> np.ndarray:
        return np.asarray(xi, dtype=float)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interface_values": self.interface_values.tolist(),
            "flux_left_side": self.flux_left_side.tolist(),
            "flux_right_side": self.flux_right_side.tolist(),
            "throughput": self.throughput,
        }


def analytic_solution(case: InterfaceCase) -> AnalyticSolution:
    """Solve the chain in closed form (independent reference for every number)."""
    blocks = case.blocks
    n = len(blocks)
    n_unknown = 2 * n
    mat = np.zeros((n_unknown, n_unknown), dtype=float)
    rhs = np.zeros(n_unknown, dtype=float)
    row = 0

    # unknown layout: (A_0, B_0, A_1, B_1, ...)
    def idx(i: int, which: int) -> int:
        return 2 * i + which

    # left outer Dirichlet: A_0 = u_left
    mat[row, idx(0, 0)] = 1.0
    rhs[row] = float(case.u_left)
    row += 1

    for i in range(n - 1):
        bi, bj = blocks[i], blocks[i + 1]
        f_i, k_i, l_i = float(bi.source), float(bi.coefficient), float(bi.length)
        # value continuity: A_i + B_i L_i - f_i L_i^2/(2 k_i) = A_{i+1}
        mat[row, idx(i, 0)] = 1.0
        mat[row, idx(i, 1)] = l_i
        mat[row, idx(i + 1, 0)] = -1.0
        rhs[row] = f_i * l_i * l_i / (2.0 * k_i)
        row += 1
        # flux jump: q_{i+1}(0) - q_i(L_i) = s_i  ->  -k_{i+1} B_{i+1} + k_i B_i - f_i L_i = s_i
        mat[row, idx(i, 1)] = k_i
        mat[row, idx(i + 1, 1)] = -float(bj.coefficient)
        rhs[row] = float(case.interface_sources[i]) + f_i * l_i
        row += 1

    # right outer Dirichlet on the last block
    bn = blocks[-1]
    mat[row, idx(n - 1, 0)] = 1.0
    mat[row, idx(n - 1, 1)] = float(bn.length)
    rhs[row] = float(case.u_right) + float(bn.source) * bn.length**2 / (2.0 * bn.coefficient)
    row += 1

    assert row == n_unknown, "closed-form condition count mismatch"
    sol = np.linalg.solve(mat, rhs)
    block_a = sol[0::2]
    block_b = sol[1::2]

    interface_values = np.empty(n - 1)
    flux_left_side = np.empty(n - 1)
    flux_right_side = np.empty(n - 1)
    for i in range(n - 1):
        bi = blocks[i]
        interface_values[i] = (
            block_a[i] + block_b[i] * bi.length - bi.source * bi.length**2 / (2.0 * bi.coefficient)
        )
        flux_left_side[i] = -bi.coefficient * block_b[i] + bi.source * bi.length
        flux_right_side[i] = -blocks[i + 1].coefficient * block_b[i + 1]
    throughput = float(-blocks[0].coefficient * block_b[0])
    return AnalyticSolution(
        block_a=block_a,
        block_b=block_b,
        interface_values=interface_values,
        flux_left_side=flux_left_side,
        flux_right_side=flux_right_side,
        throughput=throughput,
    )


def analytic_cell_values(case: InterfaceCase, analytic: AnalyticSolution) -> List[np.ndarray]:
    """Closed-form solution sampled at every block's cell centres."""
    out = []
    for i, block in enumerate(case.blocks):
        centers = 0.5 * (block.edges()[:-1] + block.edges()[1:])
        k, f = block.coefficient, block.source
        out.append(
            analytic.block_a[i] + analytic.block_b[i] * centers - f * centers * centers / (2.0 * k)
        )
    return out


# ---------------------------------------------------------------------------
# The coupling driver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoupledRun:
    """Everything the anchors need from one coupled run (no hidden state)."""

    case: InterfaceCase
    solution: AnalyticSolution  # closed-form reference
    blocks: Tuple[BlockSolution, ...]
    interface_values: np.ndarray  # face values used as Dirichlet data
    flux_left_side: np.ndarray  # flux computed by block i at face i
    flux_right_side: np.ndarray  # flux computed by block i+1 at face i
    sent_flux: np.ndarray  # flux handed over by the sending side
    carried_value: np.ndarray  # value the receiving side received
    residual_series: List[float]  # ||d phi|| / ||phi||  per iteration
    imbalance_series: List[float]  # flux imbalance per iteration
    iterations: int
    converged: bool
    relaxation_series: List[float]
    preconditioner: np.ndarray

    @property
    def run_digest(self) -> str:
        blob = json.dumps(
            {
                "case": self.case.digest(),
                "interface_values": [float(v) for v in self.interface_values],
                "residual_series": [float(v) for v in self.residual_series],
                "iterations": int(self.iterations),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case": self.case.to_dict(),
            "case_digest": self.case.digest(),
            "run_digest": self.run_digest,
            "interface_values": [float(v) for v in self.interface_values],
            "flux_left_side": [float(v) for v in self.flux_left_side],
            "flux_right_side": [float(v) for v in self.flux_right_side],
            "sent_flux": [float(v) for v in self.sent_flux],
            "carried_value": [float(v) for v in self.carried_value],
            "residual_series": [float(v) for v in self.residual_series],
            "imbalance_series": [float(v) for v in self.imbalance_series],
            "iterations": int(self.iterations),
            "converged": bool(self.converged),
            "relaxation_series": [float(v) for v in self.relaxation_series],
            "preconditioner": np.asarray(self.preconditioner, dtype=float).tolist(),
            "analytic": self.solution.to_dict(),
            "blocks": [b.to_dict() for b in self.blocks],
        }


def _evaluate(
    case: InterfaceCase, phi: np.ndarray, halo: Optional[np.ndarray] = None
) -> Tuple[Tuple[BlockSolution, ...], np.ndarray, np.ndarray, np.ndarray]:
    """Solve every block once with the current face values.

    The value at each coupling face is shared in ``transfer="face"`` mode.  In
    ``transfer="halo"`` mode the receiving side only gets what the sending side
    could reconstruct from its own cell averages (``halo``, the previous
    iterate), which is what independent codes actually exchange.

    Returns ``(solutions, flux_left_side, flux_right_side, carried_value)``.
    ``flux_left_side[i]`` is block ``i``'s flux at face ``i`` (the sending side),
    ``flux_right_side[i]`` is block ``i+1``'s flux at the same face (the
    receiving side) — the pair A1 has to reconcile.
    """
    n = len(case.blocks)
    solutions: List[BlockSolution] = []
    for i, block in enumerate(case.blocks):
        if i == 0:
            left = float(case.u_left)
        elif halo is not None:
            left = float(halo[i - 1])
        else:
            left = float(phi[i - 1])
        right = float(case.u_right) if i == n - 1 else float(phi[i])
        solutions.append(solve_block_dirichlet(block, left, right))
    flux_left_side = np.array([sol.flux_right for sol in solutions[: n - 1]])
    flux_right_side = np.array([solutions[i + 1].flux_left for i in range(n - 1)])
    if case.transfer == "face":
        carried = np.array([float(phi[i]) for i in range(n - 1)])
    else:
        carried = np.array([solutions[i].right_face_value() for i in range(n - 1)])
    return tuple(solutions), flux_left_side, flux_right_side, carried


def _imbalance(
    case: InterfaceCase, flux_left_side: np.ndarray, flux_right_side: np.ndarray
) -> np.ndarray:
    """Per-face flux imbalance: what the receiver computed minus what it was given.

    The sending side hands over its own flux plus the surface source of the face;
    that is the only data a conservative interface may transmit.
    """
    sent = np.asarray(flux_left_side, dtype=float) + np.asarray(case.interface_sources, dtype=float)
    return np.asarray(flux_right_side, dtype=float) - sent


def _imbalance_scale(case: InterfaceCase, flux_left_side, flux_right_side) -> np.ndarray:
    scale = np.maximum.reduce(
        [
            np.abs(np.asarray(flux_left_side, dtype=float)),
            np.abs(np.asarray(flux_right_side, dtype=float)),
            np.abs(np.asarray(case.interface_sources, dtype=float)),
        ]
    )
    return np.where(scale > 0.0, scale, 1.0)


def _face_jacobian(case: InterfaceCase, phi: np.ndarray, step: float = 1e-6) -> np.ndarray:
    """Finite-difference Jacobian ``dr/dphi`` of the face imbalance map."""
    n = case.n_faces
    base = _imbalance(case, *_evaluate(case, phi)[1:3])
    jac = np.zeros((n, n), dtype=float)
    span = max(float(np.max(np.abs(phi))), 1.0)
    for j in range(n):
        delta = np.zeros(n)
        delta[j] = step * span
        shifted = _imbalance(case, *_evaluate(case, phi + delta)[1:3])
        jac[:, j] = (shifted - base) / delta[j]
    return jac


def run_coupled(
    case: InterfaceCase,
    max_updates: Optional[int] = None,
    use_aitken: bool = False,
    scheme: str = "newton",
) -> CoupledRun:
    """Run the coupled fixed point and record the convergence history.

    ``phi`` holds the value on each coupling face.  Every block is solved with
    those values as Dirichlet data, both sides report their own flux, and the
    imbalance ``r`` drives the update ``phi -= omega * D^-1 r``:

    ``scheme="newton"``  ``D`` is the measured face Jacobian ``dr/dphi`` (one
                         finite-difference sweep at the initial guess, exactly the
                         interface-quasi-Newton idea used by partitioned solvers).
                         For a linear problem the residual then decays with the
                         *predicted* ratio ``rho = |1 - omega|``, which is what
                         makes the measured rate verifiable rather than fitted.
    ``scheme="jacobi"``  ``D = diag(dr/dphi)`` — diagonal preconditioning only.
    ``scheme="scalar"``  ``D = I`` and ``omega`` is the scalar relaxation, i.e. the
                         naive Dirichlet-Neumann iteration, kept so that a *bad*
                         scheme can be shown to stall or diverge.

    ``max_updates`` limits the number of *updates* (0 = solved once, coupling
    off) which is what the negative control needs.  ``use_aitken`` switches on
    dynamic Aitken (Irons-Tuck) relaxation of ``omega``.
    """
    n = len(case.blocks)
    edges_cum = np.cumsum([0.0] + [b.length for b in case.blocks])
    frac = edges_cum[1:-1] / case.total_length
    phi = np.asarray(
        float(case.u_left) + (float(case.u_right) - float(case.u_left)) * frac, dtype=float
    )

    jac = _face_jacobian(case, phi)
    if scheme == "newton":
        diag = jac
    elif scheme == "jacobi":
        diag = np.diag(np.diag(jac).copy())
    elif scheme == "scalar":
        diag = np.eye(n - 1)
    else:  # pragma: no cover - guarded by InterfaceCase
        raise ValueError(f"unknown coupling scheme {scheme!r}")
    if np.any(~np.isfinite(diag)):
        raise ValueError("coupling preconditioner is not finite")

    omega = float(case.relaxation)
    residual_series: List[float] = []
    imbalance_series: List[float] = []
    relaxation_series: List[float] = []
    halo: Optional[np.ndarray] = None
    phi_prev: Optional[np.ndarray] = None
    r_prev: Optional[np.ndarray] = None
    dr_prev: Optional[np.ndarray] = None
    converged = False
    updates = 0
    max_updates = case.max_iterations if max_updates is None else int(max_updates)

    for _step in range(case.max_iterations + 1):
        solutions, q_left, q_right, carried = _evaluate(case, phi, halo)
        r = _imbalance(case, q_left, q_right)
        scale = _imbalance_scale(case, q_left, q_right)
        imbalance_series.append(float(np.max(np.abs(r) / scale)))
        if phi_prev is not None:
            denom = max(float(np.max(np.abs(phi_prev))), 1e-300)
            residual_series.append(float(np.max(np.abs(phi - phi_prev)) / denom))
        if imbalance_series[-1] <= case.tolerance and (
            not residual_series or residual_series[-1] <= case.tolerance
        ):
            converged = True
            break
        if updates >= max_updates:
            break

        if use_aitken and r_prev is not None and dr_prev is not None:
            dr = r - r_prev
            denom = float(np.dot(dr - dr_prev, dr - dr_prev))
            if denom > 0.0:
                omega = -omega * float(np.dot(dr_prev, dr - dr_prev)) / denom
                omega = float(min(max(omega, 0.05), 2.0))
        phi_hold = phi
        phi = phi - omega * np.linalg.solve(diag, r)
        relaxation_series.append(omega)
        phi_prev = phi_hold
        if case.transfer == "halo":
            halo = carried.copy()
        r_prev, dr_prev = r, (r - r_prev) if r_prev is not None else None
        updates += 1

    sent = np.asarray(q_left, dtype=float) + np.asarray(case.interface_sources, dtype=float)
    return CoupledRun(
        case=case,
        solution=analytic_solution(case),
        blocks=solutions,
        interface_values=phi.copy(),
        flux_left_side=q_left,
        flux_right_side=q_right,
        sent_flux=sent,
        carried_value=carried,
        residual_series=residual_series,
        imbalance_series=imbalance_series,
        iterations=updates,
        converged=converged,
        relaxation_series=relaxation_series or [omega],
        preconditioner=np.asarray(diag, dtype=float),
    )


# ---------------------------------------------------------------------------
# A3: measured convergence of the coupling iteration
# ---------------------------------------------------------------------------


def convergence_metrics(run: CoupledRun) -> Dict[str, Any]:
    """Spectral ratio, observed order and decades/iteration of the residual.

    ``rho`` is the geometric decay ratio fitted over the asymptotic tail of
    ``||d phi|| / ||phi||``; ``order`` is the classical observed order, i.e. the
    slope of ``ln r_{n+1}`` against ``ln r_n`` (1 for a linearly converging fixed
    point, >1 for a superlinear one).  The order alone cannot detect divergence,
    so the gate requires ``rho < 1`` as well.
    """
    series = np.asarray([v for v in run.residual_series if np.isfinite(v) and v > 0.0])
    record: Dict[str, Any] = {
        "iterations": int(run.iterations),
        "converged": bool(run.converged),
        "final_residual": float(series[-1]) if series.size else float("nan"),
        "final_imbalance": float(run.imbalance_series[-1]),
        "spectral_ratio": float("nan"),
        "order": float("nan"),
        "decades_per_iteration": float("nan"),
        "n_points": int(series.size),
    }
    if series.size >= 6:
        # discard the start-up transient and the floating-point floor, then fit
        # both the geometric decay and the classical order on the clean window
        floor = float(series.max()) * 1e-10
        clean = series[series > max(floor, 1e-300)]
        clean = clean[max(1, int(round(0.2 * clean.size))) :]
        if clean.size >= 4:
            slope = float(np.polyfit(np.arange(clean.size, dtype=float), np.log(clean), 1)[0])
            record["spectral_ratio"] = float(math.exp(slope))
            record["decades_per_iteration"] = float(-slope / math.log(10.0))
            a, b = clean[:-1], clean[1:]
            if a.size >= 2 and float(np.ptp(np.log(a))) > 1e-12:
                record["order"] = float(np.polyfit(np.log(a), np.log(b), 1)[0])
    record["passes"] = bool(
        record["converged"]
        and math.isfinite(record["spectral_ratio"])
        and 0.0 < record["spectral_ratio"] < THRESHOLDS["spectral_ratio"]
        and math.isfinite(record["order"])
        and record["order"]
        >= THRESHOLDS["convergence_order"] - THRESHOLDS["order_estimator_tolerance"]
    )
    return record


# ---------------------------------------------------------------------------
# A1: interface flux conservation
# ---------------------------------------------------------------------------


def interface_anchor(run: CoupledRun, transfer_scale: float = 1.0) -> Dict[str, Any]:
    """Per-face flux conservation of the converged coupling.

    ``transfer_scale`` is the defect-injection hook: it multiplies the flux the
    sending side hands over, so a deliberately non-conservative exchange has to
    trip the gate.  ``1.0`` is the production setting.
    """
    case = run.case
    sent_flux = transfer_scale * (
        run.flux_left_side + np.asarray(case.interface_sources, dtype=float)
    )
    imbalance = run.flux_right_side - sent_flux
    scale = _imbalance_scale(case, run.flux_left_side, run.flux_right_side)
    rel = np.abs(imbalance) / scale
    analytic = run.solution
    ref_scale = np.maximum.reduce(
        [
            np.abs(analytic.flux_right_side),
            np.abs(analytic.flux_left_side),
            np.abs(np.asarray(case.interface_sources, dtype=float)),
        ]
    )
    ref_scale = np.where(ref_scale > 0.0, ref_scale, 1.0)
    rel_vs_analytic = np.abs(run.flux_right_side - analytic.flux_right_side) / ref_scale
    faces = []
    for i in range(case.n_faces):
        faces.append(
            {
                "face": int(i),
                "interface_source": float(case.interface_sources[i]),
                "flux_left_side": float(run.flux_left_side[i]),
                "flux_right_side": float(run.flux_right_side[i]),
                "flux_sent": float(sent_flux[i]),
                "imbalance": float(imbalance[i]),
                "scale": float(scale[i]),
                "rel_error": float(rel[i]),
                "analytic_left_side": float(analytic.flux_left_side[i]),
                "analytic_right_side": float(analytic.flux_right_side[i]),
                "rel_error_vs_analytic": float(rel_vs_analytic[i]),
            }
        )
    worst = float(np.max(rel))
    return {
        "faces": faces,
        "n_faces": int(case.n_faces),
        "max_rel_error": worst,
        "mean_rel_error": float(np.mean(rel)),
        "max_rel_error_vs_analytic": float(np.max(rel_vs_analytic)),
        "transfer_scale": float(transfer_scale),
        "threshold": THRESHOLDS["interface_flux_rel_error"],
        "passes": bool(worst < THRESHOLDS["interface_flux_rel_error"]),
    }


# ---------------------------------------------------------------------------
# A2: global conservation ledger
# ---------------------------------------------------------------------------


def budget_anchor(run: CoupledRun, transfer_scale: float = 1.0) -> Dict[str, Any]:
    """Global budget for one conserved quantity of one coupled run.

    ``closed_residual`` uses each block's own balance plus the interface jump
    condition, so it only tests the discrete divergence theorem.
    ``exchanged_residual`` adds the flux actually carried across each face, so
    anything the exchange loses or gains lands in the global budget.
    """
    case = run.case
    flux_in = float(run.blocks[0].flux_left)
    flux_out = float(run.blocks[-1].flux_right)
    src_volume = float(sum(b.source * b.volume for b in case.blocks))
    src_face = float(sum(case.interface_sources))
    closed = (flux_out - flux_in) - src_volume - src_face

    sent_flux = transfer_scale * (run.flux_left_side + np.asarray(case.interface_sources, float))
    leak = float(np.sum(run.flux_right_side - sent_flux))
    exchanged = closed + leak

    block_balance = [
        float((sol.flux_right - sol.flux_left) - block.source * block.volume)
        for sol, block in zip(run.blocks, case.blocks)
    ]
    scale = max(abs(flux_in), abs(flux_out), abs(src_volume), abs(src_face), 1e-300)
    return {
        "quantity": case.quantity,
        "case": case.name,
        "flux_in": flux_in,
        "flux_out": flux_out,
        "source_volume": src_volume,
        "source_interface": src_face,
        "closed_residual": closed,
        "exchanged_residual": exchanged,
        "closed_rel_error": abs(closed) / scale,
        "exchanged_rel_error": abs(exchanged) / scale,
        "max_block_balance_rel": float(max(abs(v) for v in block_balance) / scale),
        "scale": scale,
        "threshold": THRESHOLDS["global_budget_rel_error"],
        "passes": bool(abs(exchanged) / scale < THRESHOLDS["global_budget_rel_error"]),
    }


# ---------------------------------------------------------------------------
# A4: error propagation
# ---------------------------------------------------------------------------


def _throughput(run: CoupledRun) -> float:
    """Coupled output used for sensitivity: the flux entering at the far end."""
    return float(run.blocks[0].flux_left)


def perturb_case(case: InterfaceCase, kind: str, index: int, factor: float) -> InterfaceCase:
    """Return a copy of ``case`` with one input parameter scaled by ``factor``."""
    if kind == "coefficient":
        blocks = list(case.blocks)
        blocks[index] = replace(blocks[index], coefficient=blocks[index].coefficient * factor)
        return replace(case, blocks=tuple(blocks))
    if kind == "source_volume":
        blocks = list(case.blocks)
        blocks[index] = replace(blocks[index], source=blocks[index].source * factor)
        return replace(case, blocks=tuple(blocks))
    if kind == "source_interface":
        srcs = list(case.interface_sources)
        srcs[index] = srcs[index] * factor if srcs[index] != 0.0 else (factor - 1.0)
        return replace(case, interface_sources=tuple(srcs))
    if kind == "u_left":
        return replace(case, u_left=case.u_left * factor)
    if kind == "u_right":
        return replace(case, u_right=case.u_right * factor)
    raise ValueError(f"unknown perturbation kind {kind!r}")


def _parameter_value(case: InterfaceCase, kind: str, index: int) -> float:
    if kind in ("coefficient", "source_volume"):
        block = case.blocks[index]
        return float(block.coefficient if kind == "coefficient" else block.source)
    if kind == "source_interface":
        return float(case.interface_sources[index])
    if kind == "u_left":
        return float(case.u_left)
    if kind == "u_right":
        return float(case.u_right)
    raise ValueError(f"unknown parameter kind {kind!r}")


def _analytic_throughput(case: InterfaceCase) -> float:
    return float(analytic_solution(case).throughput)


def sensitivity_anchor(
    case: InterfaceCase,
    parameters: Sequence[Tuple[str, str, int]],
    rel_step: float = 1e-3,
    use_aitken: bool = False,
    scheme: str = "newton",
) -> Dict[str, Any]:
    """Central-difference sensitivity of the coupled output w.r.t. the inputs.

    ``S = (dy/y) / (dp/p)``.  Every coefficient is computed twice — from the
    coupled solver (production path) and from the closed-form chain — so the
    amplification factor has an independent reference.
    """
    base_run = run_coupled(case, use_aitken=use_aitken, scheme=scheme)
    base_y = _throughput(base_run)
    base_analytic = _analytic_throughput(case)
    rows = []
    for label, kind, index in parameters:
        p0 = _parameter_value(case, kind, index)
        plus = perturb_case(case, kind, index, 1.0 + rel_step)
        minus = perturb_case(case, kind, index, 1.0 - rel_step)
        y_plus = _throughput(run_coupled(plus, use_aitken=use_aitken, scheme=scheme))
        y_minus = _throughput(run_coupled(minus, use_aitken=use_aitken, scheme=scheme))
        numeric = (y_plus - y_minus) / (2.0 * rel_step * base_y) if p0 != 0 else float("nan")
        a_plus = _analytic_throughput(plus)
        a_minus = _analytic_throughput(minus)
        analytic_s = (a_plus - a_minus) / (2.0 * rel_step * base_analytic)
        rows.append(
            {
                "parameter": label,
                "kind": kind,
                "index": int(index),
                "value": float(p0),
                "y_plus": float(y_plus),
                "y_minus": float(y_minus),
                "sensitivity": float(numeric),
                "analytic_sensitivity": float(analytic_s),
                "abs_sensitivity": abs(float(numeric)),
                "abs_diff_vs_analytic": abs(float(numeric) - float(analytic_s)),
            }
        )
    worst = max(r["abs_sensitivity"] for r in rows)
    return {
        "output": float(base_y),
        "analytic_output": float(base_analytic),
        "rel_step": float(rel_step),
        "parameters": rows,
        "max_abs_sensitivity": worst,
        "threshold": THRESHOLDS["sensitivity_coefficient"],
        "passes": bool(worst < THRESHOLDS["sensitivity_coefficient"]),
    }


# ---------------------------------------------------------------------------
# A5: negative control (coupling off -> single-domain solution)
# ---------------------------------------------------------------------------


def decoupling_anchor(
    case: InterfaceCase,
    extra_updates: Sequence[int] = (1, 2, 3),
    scheme: str = "newton",
    injected_leak: float = 1e-2,
) -> Dict[str, Any]:
    """Coupling off must reproduce the single-domain (per-block) solution.

    The reference is a *direct* single-domain solve of every block with the
    interface data frozen at the driver's initial guess — no iteration, no
    exchange, no shared state.  A driver that leaks interaction when the coupling
    is switched off cannot pass this.

    ``injected_leak`` documents the discriminating power of the control: the same
    comparison is repeated with an artificial interface contamination of
    ``injected_leak`` times the global potential drop, which has to trip the
    threshold (otherwise the control proves nothing).
    """
    n = len(case.blocks)
    edges_cum = np.cumsum([0.0] + [b.length for b in case.blocks])
    frac = edges_cum[1:-1] / case.total_length
    phi0 = float(case.u_left) + (float(case.u_right) - float(case.u_left)) * frac
    left0 = [float(case.u_left) if i == 0 else float(phi0[i - 1]) for i in range(n)]
    right0 = [float(case.u_right) if i == n - 1 else float(phi0[i]) for i in range(n)]

    off = run_coupled(case, max_updates=0, scheme=scheme)
    direct = [
        solve_block_dirichlet(block, left0[i], right0[i]) for i, block in enumerate(case.blocks)
    ]

    def _dev(blocks, left, right) -> List[float]:
        out = []
        for sol_drv, sol_ref in zip(blocks, direct):
            denom = max(float(np.max(np.abs(sol_ref.values))), 1e-300)
            out.append(float(np.max(np.abs(sol_drv.values - sol_ref.values)) / denom))
        return out

    deviation = _dev(off.blocks, left0, right0)

    lever = float(injected_leak) * (float(case.u_right) - float(case.u_left))
    leaky = [
        solve_block_dirichlet(
            block,
            left0[i] + (lever if i > 0 else 0.0),
            right0[i] - (lever if i < n - 1 else 0.0),
        )
        for i, block in enumerate(case.blocks)
    ]
    leak_deviation = _dev(leaky, left0, right0)

    trend = []
    for updates in extra_updates:
        run = run_coupled(case, max_updates=int(updates), scheme=scheme)
        trend.append(
            {
                "coupling_updates": int(updates),
                "max_deviation": float(max(_dev(run.blocks, left0, right0))),
            }
        )

    worst = float(max(deviation))
    return {
        "per_block_deviation": deviation,
        "max_deviation": worst,
        "threshold": THRESHOLDS["decoupled_deviation"],
        "trend_with_coupling": trend,
        "injected_leak": float(injected_leak),
        "injected_leak_deviation": float(max(leak_deviation)),
        "injected_leak_detected": bool(max(leak_deviation) > THRESHOLDS["decoupled_deviation"]),
        "passes": bool(
            worst < THRESHOLDS["decoupled_deviation"]
            and max(leak_deviation) > THRESHOLDS["decoupled_deviation"]
        ),
    }


# ---------------------------------------------------------------------------
# Acceptance record (A1..A5) for a set of cases
# ---------------------------------------------------------------------------


def acceptance_record(
    interface_cases: Sequence[InterfaceCase],
    budget_cases: Sequence[InterfaceCase],
    sensitivity_case: InterfaceCase,
    sensitivity_parameters: Sequence[Tuple[str, str, int]],
    decoupling_case: InterfaceCase,
    use_aitken: bool = False,
    scheme: str = "newton",
) -> Dict[str, Any]:
    """Run the five anchors and return one machine-checkable record."""
    runs = [run_coupled(c, use_aitken=use_aitken, scheme=scheme) for c in interface_cases]
    interface = [interface_anchor(run) for run in runs]
    a1_worst = max(item["max_rel_error"] for item in interface)

    budget_runs = [run_coupled(c, use_aitken=use_aitken, scheme=scheme) for c in budget_cases]
    budget = [budget_anchor(run) for run in budget_runs]
    a2_worst = max(item["exchanged_rel_error"] for item in budget)

    convergence = [convergence_metrics(run) for run in runs]
    a3_ok = all(item["passes"] for item in convergence)

    sensitivity = sensitivity_anchor(
        sensitivity_case, sensitivity_parameters, use_aitken=use_aitken, scheme=scheme
    )
    decoupling = decoupling_anchor(decoupling_case, scheme=scheme)

    record = {
        "thresholds": dict(THRESHOLDS),
        "convention": {
            "rel_error": "|imbalance| / max(|flux_left_side|, |flux_right_side|, |interface_source|)",
            "order": "slope of ln r_{n+1} vs ln r_n over the asymptotic tail of ||d phi||/||phi||",
            "sensitivity": "central difference, 1e-3 relative perturbation of one input",
        },
        "A1_interface_flux": {
            "cases": [
                dict(item, case=run.case.name, case_digest=run.case.digest())
                for item, run in zip(interface, runs)
            ],
            "max_rel_error": a1_worst,
            "threshold": THRESHOLDS["interface_flux_rel_error"],
            "passes": bool(a1_worst < THRESHOLDS["interface_flux_rel_error"]),
        },
        "A2_global_budget": {
            "quantities": budget,
            "max_rel_error": a2_worst,
            "threshold": THRESHOLDS["global_budget_rel_error"],
            "passes": bool(a2_worst < THRESHOLDS["global_budget_rel_error"]),
        },
        "A3_convergence": {
            "cases": [
                dict(item, case=run.case.name, case_digest=run.case.digest())
                for item, run in zip(convergence, runs)
            ],
            "thresholds": {
                "order": THRESHOLDS["convergence_order"],
                "order_estimator_tolerance": THRESHOLDS["order_estimator_tolerance"],
                "spectral_ratio": THRESHOLDS["spectral_ratio"],
            },
            "passes": bool(a3_ok),
        },
        "A4_error_propagation": sensitivity,
        "A5_negative_control": decoupling,
        "runs": [run.to_dict() for run in runs + budget_runs],
    }
    record["passes"] = bool(
        record["A1_interface_flux"]["passes"]
        and record["A2_global_budget"]["passes"]
        and record["A3_convergence"]["passes"]
        and sensitivity["passes"]
        and decoupling["passes"]
    )
    return record
