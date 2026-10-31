"""Hermite-cubic Euler-Bernoulli frame girder: forward FE model.

This mirrors the beam element formulation used by the TensorPhysics / TensorFEM
solver (``hermite_cubic_euler_bernoulli``) but is self-contained so the SHM loop
runs inside the platform without an external solver process.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .constants import DEFAULT_YOUNG_MODULUS_PA, MIN_SLENDERNESS_L_OVER_H
from .section import BeamSection


def _as_float_array(values: Sequence[float], name: str) -> np.ndarray:
    arr = np.asarray(list(values), dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional, got shape {arr.shape}")
    if arr.size == 0:
        raise ValueError(f"{name} must not be empty")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains non-finite values")
    return arr


def validate_structural_geometry(
    length_m: float,
    n_elements: int,
    section: BeamSection,
    young_modulus_pa: float = DEFAULT_YOUNG_MODULUS_PA,
    boundary: str = "fixed_fixed",
) -> Dict[str, Any]:
    """Validate the structural idealisation before any matrix is assembled.

    The platform rule "geometry must be validated before results are trusted" is
    applied to the structural domain: span, mesh and section are checked and the
    Euler-Bernoulli slenderness criterion is reported.
    """
    if not math.isfinite(float(length_m)) or float(length_m) <= 0.0:
        raise ValueError(f"length_m must be a finite positive length, got {length_m!r}")
    n_el = int(n_elements)
    if float(n_elements) != float(n_el) or n_el < 2:
        raise ValueError(f"n_elements must be an integer >= 2, got {n_elements!r}")
    if not math.isfinite(float(young_modulus_pa)) or float(young_modulus_pa) <= 0.0:
        raise ValueError(f"young_modulus_pa must be positive, got {young_modulus_pa!r}")
    if boundary not in ("fixed_fixed", "cantilever"):
        raise ValueError(f"boundary must be 'fixed_fixed' or 'cantilever', got {boundary!r}")
    section.validate()

    element_length = float(length_m) / n_el
    slenderness = float(length_m) / section.height_m
    return {
        "length_m": float(length_m),
        "n_elements": n_el,
        "element_length_m": element_length,
        "boundary": boundary,
        "young_modulus_pa": float(young_modulus_pa),
        "slenderness_l_over_h": slenderness,
        "euler_bernoulli_valid": bool(slenderness >= MIN_SLENDERNESS_L_OVER_H),
        "aspect_ratio_element_l_over_h": element_length / section.height_m,
        "section": section.as_dict(),
    }


class IceBeltFrameModel:
    """Two-node Hermite beam model of an ice-belt frame girder."""

    formulation = "hermite_cubic_euler_bernoulli"

    def __init__(
        self,
        length_m: float = 3.0,
        n_elements: int = 12,
        section: Optional[BeamSection] = None,
        young_modulus_pa: float = DEFAULT_YOUNG_MODULUS_PA,
        boundary: str = "fixed_fixed",
        fibre_offset_m: Optional[float] = None,
    ) -> None:
        self.section = section if section is not None else BeamSection()
        self.geometry_validation = validate_structural_geometry(
            length_m, n_elements, self.section, young_modulus_pa, boundary
        )
        self.length_m = float(length_m)
        self.n_elements = int(n_elements)
        self.young_modulus_pa = float(young_modulus_pa)
        self.boundary = boundary
        self.fibre_offset_m = (
            float(fibre_offset_m)
            if fibre_offset_m is not None
            else float(self.section.fibre_from_flange_m)
        )

        self.nodes_x = np.linspace(0.0, self.length_m, self.n_elements + 1)
        self.element_length_m = self.length_m / self.n_elements
        self.n_nodes = self.n_elements + 1
        self.n_dof = 2 * self.n_nodes

        self._element_stiffness: List[np.ndarray] = [
            self._element_stiffness_matrix(self.element_length_m) for _ in range(self.n_elements)
        ]
        self.K = self._assemble_stiffness()
        if self.boundary == "fixed_fixed":
            self.constrained_dofs = [0, 1, self.n_dof - 2, self.n_dof - 1]
        else:
            self.constrained_dofs = [0, 1]
        self.free_dofs = [d for d in range(self.n_dof) if d not in self.constrained_dofs]
        self._free_index = np.asarray(self.free_dofs, dtype=int)
        self.K_ff = self.K[np.ix_(self._free_index, self._free_index)]
        self._K_ff_inv = np.linalg.inv(self.K_ff)

    # -- element / global matrices ------------------------------------------

    def _element_stiffness_matrix(self, le: float) -> np.ndarray:
        c = self.young_modulus_pa * self.section.inertia_m4 / le ** 3
        return c * np.array(
            [
                [12.0, 6.0 * le, -12.0, 6.0 * le],
                [6.0 * le, 4.0 * le ** 2, -6.0 * le, 2.0 * le ** 2],
                [-12.0, -6.0 * le, 12.0, -6.0 * le],
                [6.0 * le, 2.0 * le ** 2, -6.0 * le, 4.0 * le ** 2],
            ]
        )

    def _assemble_stiffness(self) -> np.ndarray:
        K = np.zeros((self.n_dof, self.n_dof))
        for e in range(self.n_elements):
            dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
            K[np.ix_(dofs, dofs)] += self._element_stiffness[e]
        return K

    # -- forward solver -----------------------------------------------------

    def solve(self, nodal_load_n: Sequence[float]) -> np.ndarray:
        """Solve ``K u = f`` for a full nodal load vector, returning all DOF."""
        f = _as_float_array(nodal_load_n, "nodal_load_n")
        if f.size != self.n_dof:
            raise ValueError(f"nodal load must have {self.n_dof} entries, got {f.size}")
        u = np.zeros(self.n_dof)
        u[self._free_index] = self._K_ff_inv @ f[self._free_index]
        return u

    def nodal_load(self, loads_n: Sequence[float], load_nodes: Sequence[int]) -> np.ndarray:
        """Map a candidate-node load vector onto the full nodal load vector."""
        loads = _as_float_array(loads_n, "loads_n")
        nodes = [int(n) for n in load_nodes]
        if len(nodes) != loads.size:
            raise ValueError("loads_n and load_nodes must have the same length")
        for n in nodes:
            if n < 0 or n >= self.n_nodes:
                raise ValueError(f"load node {n} outside [0, {self.n_nodes - 1}]")
        if len(set(nodes)) != len(nodes):
            raise ValueError("load_nodes must be unique")
        f = np.zeros(self.n_dof)
        for node, value in zip(nodes, loads):
            f[2 * node] += float(value)
        return f

    def solve_candidates(self, loads_n: Sequence[float], load_nodes: Sequence[int]) -> np.ndarray:
        return self.solve(self.nodal_load(loads_n, load_nodes))

    # -- field recovery -----------------------------------------------------

    def _element_of(self, x: float) -> Tuple[int, float]:
        x = float(x)
        if x < -1e-12 or x > self.length_m + 1e-12:
            raise ValueError(f"abscissa {x} outside [0, {self.length_m}]")
        x = min(max(x, 0.0), self.length_m)
        e = int(math.floor(x / self.element_length_m))
        e = min(e, self.n_elements - 1)
        s = (x - e * self.element_length_m) / self.element_length_m
        return e, s

    def _curvature_b2(self, s: float, le: float) -> np.ndarray:
        """Second derivative of the Hermite shape functions (d2N/dx2)."""
        return np.array(
            [
                (-6.0 + 12.0 * s) / le ** 2,
                (-4.0 + 6.0 * s) / le,
                (6.0 - 12.0 * s) / le ** 2,
                (-2.0 + 6.0 * s) / le,
            ]
        )

    def curvature_at(self, x: float, u: Sequence[float]) -> float:
        """Curvature ``kappa = d2v/dx2`` at abscissa ``x`` (1/m)."""
        u_arr = np.asarray(u, dtype=float)
        e, s = self._element_of(x)
        return float(self._curvature_b2(s, self.element_length_m) @ u_arr[2 * e: 2 * e + 4])

    def strain_at(
        self, x: float, u: Sequence[float], fibre_offset_m: Optional[float] = None
    ) -> float:
        """Strain at abscissa ``x`` on the fibre ``y = fibre_offset_m``."""
        y = self.fibre_offset_m if fibre_offset_m is None else float(fibre_offset_m)
        return -y * self.curvature_at(x, u)

    def nodes_for_positions(self, positions_m: Sequence[float], tol: float = 1e-9) -> np.ndarray:
        pos = _as_float_array(positions_m, "positions_m")
        nodes: List[int] = []
        for p in pos:
            idx = int(np.argmin(np.abs(self.nodes_x - p)))
            if abs(self.nodes_x[idx] - p) > tol:
                raise ValueError(
                    f"position {p} is not on a mesh node (nearest {self.nodes_x[idx]}, "
                    f"tolerance {tol}); refine n_elements or snap to a node"
                )
            nodes.append(idx)
        if len(set(nodes)) != len(nodes):
            raise ValueError("duplicate load positions map onto the same node")
        return np.asarray(nodes, dtype=int)

    def build_influence_matrix(
        self,
        load_positions_m: Sequence[float],
        gauge_positions_m: Sequence[float],
        fibre_offset_m: Optional[float] = None,
    ) -> "StrainInfluence":
        """Assemble ``H`` (n_gauge x n_load) with the forward relation ``eps = H F``."""
        load_positions = _as_float_array(load_positions_m, "load_positions_m")
        gauge_positions = _as_float_array(gauge_positions_m, "gauge_positions_m")
        load_nodes = self.nodes_for_positions(load_positions)
        if np.any(gauge_positions < 0.0) or np.any(gauge_positions > self.length_m):
            raise ValueError(f"gauge positions must lie in [0, {self.length_m}]")

        n_load = load_nodes.size
        P = np.zeros((self.n_dof, n_load))
        for j, node in enumerate(load_nodes):
            P[2 * int(node), j] = 1.0
        U = np.zeros((self.n_dof, n_load))
        U[self._free_index, :] = self._K_ff_inv @ P[self._free_index, :]

        H = np.zeros((gauge_positions.size, n_load))
        for i, g in enumerate(gauge_positions):
            for j in range(n_load):
                H[i, j] = self.strain_at(float(g), U[:, j], fibre_offset_m)
        return StrainInfluence(
            H=H,
            load_nodes=load_nodes,
            load_positions_m=load_positions,
            gauge_positions_m=gauge_positions,
            fibre_offset_m=(
                self.fibre_offset_m if fibre_offset_m is None else float(fibre_offset_m)
            ),
        )

    # -- section forces and stress -----------------------------------------

    def internal_forces(self, u: Sequence[float], samples_per_element: int = 3) -> Dict[str, np.ndarray]:
        """Bending moment, shear and stress along the girder.

        ``sigma_x`` is returned for both extreme fibres, ``tau`` is the mean web
        shear stress (beam shear carried by the web) and ``sigma_vm`` the von
        Mises equivalent of whichever fibre is more severely stressed.
        """
        u_arr = np.asarray(u, dtype=float)
        ei = self.young_modulus_pa * self.section.inertia_m4
        web_area = self.section.shear_area_m2
        n_s = max(1, int(samples_per_element))
        y_plat = -self.section.fibre_from_plating_m
        y_fl = self.section.fibre_from_flange_m

        buckets: Dict[str, List[float]] = {
            k: [] for k in (
                "x_m", "moment_nm", "shear_n", "sigma_x_plating_pa",
                "sigma_x_flange_pa", "tau_pa", "sigma_vm_pa",
            )
        }
        for e in range(self.n_elements):
            u_e = u_arr[2 * e: 2 * e + 4]
            f_e = self._element_stiffness[e] @ u_e
            v_el = float(f_e[0])
            # sample element ends + interior so nodal extremes (point loads) are
            # captured, not just element midpoints
            s_vals = np.linspace(0.0, 1.0, n_s + 1) if n_s > 1 else np.array([0.5])
            for s in s_vals:
                kappa = float(self._curvature_b2(s, self.element_length_m) @ u_e)
                tau = v_el / web_area
                sig_plat = -self.young_modulus_pa * kappa * y_plat
                sig_fl = -self.young_modulus_pa * kappa * y_fl
                vm_plat = math.sqrt(sig_plat ** 2 + 3.0 * tau ** 2)
                vm_fl = math.sqrt(sig_fl ** 2 + 3.0 * tau ** 2)
                buckets["x_m"].append((e + s) * self.element_length_m)
                buckets["moment_nm"].append(ei * kappa)
                buckets["shear_n"].append(v_el)
                buckets["sigma_x_plating_pa"].append(sig_plat)
                buckets["sigma_x_flange_pa"].append(sig_fl)
                buckets["tau_pa"].append(tau)
                buckets["sigma_vm_pa"].append(max(vm_plat, vm_fl))
        return {k: np.asarray(v) for k, v in buckets.items()}

    # -- linear stress maps (batch / Monte-Carlo evaluation) ---------------

    def stress_maps(
        self, load_nodes: Sequence[int], samples_per_element: int = 3
    ) -> Dict[str, Any]:
        """Pre-compute the *linear* maps from candidate loads to stress drivers.

        Curvature and shear are both linear in the load vector, so a batch of
        (e.g. Monte-Carlo) load realisations can be assessed with one matrix
        product instead of one linear solve each -- this is what keeps the
        real-time monitoring cycle inside its time budget.
        """
        nodes = [int(n) for n in load_nodes]
        for n in nodes:
            if n < 0 or n >= self.n_nodes:
                raise ValueError(f"load node {n} outside [0, {self.n_nodes - 1}]")
        n_load = len(nodes)
        P = np.zeros((self.n_dof, n_load))
        for j, node in enumerate(nodes):
            P[2 * node, j] = 1.0
        U = np.zeros((self.n_dof, n_load))
        U[self._free_index, :] = self._K_ff_inv @ P[self._free_index, :]

        n_s = max(1, int(samples_per_element))
        s_vals = np.linspace(0.0, 1.0, n_s + 1) if n_s > 1 else np.array([0.5])
        n_pts = self.n_elements * s_vals.size
        kappa_map = np.zeros((n_pts, n_load))
        shear_map = np.zeros((n_pts, n_load))
        x_pts = np.zeros(n_pts)
        idx = 0
        for e in range(self.n_elements):
            u_rows = slice(2 * e, 2 * e + 4)
            for sv in s_vals:
                kappa_map[idx, :] = self._curvature_b2(float(sv), self.element_length_m) @ U[u_rows, :]
                shear_map[idx, :] = self._element_stiffness[e][0, :] @ U[u_rows, :]
                x_pts[idx] = (e + float(sv)) * self.element_length_m
                idx += 1
        return {
            "load_nodes": np.asarray(nodes, dtype=int),
            "x_m": x_pts,
            "kappa_map": kappa_map,
            "shear_map": shear_map,
            "deflection_map": U[0::2, :],
            "samples_per_element": s_vals.size,
            "web_area_m2": self.section.shear_area_m2,
            "y_plating_m": -self.section.fibre_from_plating_m,
            "y_flange_m": self.section.fibre_from_flange_m,
        }

    def stress_state_many(
        self, loads_matrix: Sequence[Sequence[float]], load_nodes: Sequence[int],
        maps: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, np.ndarray]:
        """Vectorised stress state for a batch of load realisations."""
        L = np.atleast_2d(np.asarray(loads_matrix, dtype=float))
        if L.shape[1] != len(list(load_nodes)):
            raise ValueError("loads_matrix second dimension must match load_nodes")
        mp = maps if maps is not None else self.stress_maps(load_nodes)
        ei = self.young_modulus_pa * self.section.inertia_m4
        e_mod = self.young_modulus_pa
        kappa = L @ mp["kappa_map"].T
        shear = L @ mp["shear_map"].T
        tau = shear / mp["web_area_m2"]
        sig_plat = -e_mod * kappa * mp["y_plating_m"]
        sig_fl = -e_mod * kappa * mp["y_flange_m"]
        sigma_vm = np.sqrt(np.maximum(sig_plat ** 2, sig_fl ** 2) + 3.0 * tau ** 2)
        i_max = np.argmax(sigma_vm, axis=1)
        rows = np.arange(L.shape[0])
        deflection = (L @ mp["deflection_map"].T)
        return {
            "max_von_mises_pa": sigma_vm[rows, i_max],
            "max_von_mises_location_m": mp["x_m"][i_max],
            "max_bending_moment_nm": np.max(np.abs(ei * kappa), axis=1),
            "max_shear_n": np.max(np.abs(shear), axis=1),
            "max_deflection_m": np.max(np.abs(deflection), axis=1),
        }

    def stress_state(self, loads_n: Sequence[float], load_nodes: Sequence[int]) -> Dict[str, Any]:
        """Full stress state for a candidate-node load vector."""
        u = self.solve_candidates(loads_n, load_nodes)
        profile = self.internal_forces(u)
        idx = int(np.argmax(profile["sigma_vm_pa"]))
        return {
            "profile": profile,
            "displacements": u,
            "max_von_mises_pa": float(profile["sigma_vm_pa"][idx]),
            "max_von_mises_location_m": float(profile["x_m"][idx]),
            "max_bending_moment_nm": float(np.max(np.abs(profile["moment_nm"]))),
            "max_shear_n": float(np.max(np.abs(profile["shear_n"]))),
            "max_deflection_m": float(np.max(np.abs(u[0::2]))),
        }

    def info(self) -> Dict[str, Any]:
        return {
            "formulation": self.formulation,
            "length_m": self.length_m,
            "n_elements": self.n_elements,
            "n_nodes": self.n_nodes,
            "n_dof": self.n_dof,
            "n_free_dof": len(self.free_dofs),
            "boundary": self.boundary,
            "young_modulus_pa": self.young_modulus_pa,
            "fibre_offset_m": self.fibre_offset_m,
            "geometry_validation": self.geometry_validation,
        }


@dataclass
class StrainInfluence:
    """Container for the strain influence (compliance) matrix ``H``."""

    H: np.ndarray
    load_nodes: np.ndarray
    load_positions_m: np.ndarray
    gauge_positions_m: np.ndarray
    fibre_offset_m: float

    @property
    def n_gauge(self) -> int:
        return int(self.H.shape[0])

    @property
    def n_load(self) -> int:
        return int(self.H.shape[1])

    @property
    def condition_number(self) -> float:
        return float(np.linalg.cond(self.H))

    @property
    def rank(self) -> int:
        return int(np.linalg.matrix_rank(self.H))

    def forward(self, loads_n: Sequence[float]) -> np.ndarray:
        return self.H @ _as_float_array(loads_n, "loads_n")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "shape": [self.n_gauge, self.n_load],
            "load_positions_m": self.load_positions_m.tolist(),
            "load_nodes": self.load_nodes.tolist(),
            "gauge_positions_m": self.gauge_positions_m.tolist(),
            "fibre_offset_m": self.fibre_offset_m,
            "condition_number": self.condition_number,
            "rank": self.rank,
            "H": self.H.tolist(),
        }