"""FEM→DEM fracture hand-off with four *exact* conservation ledgers.

The FEM side emits a fracture surface (``failure_faces.json``: per face the
vertices, the outward unit normal and the area).  The DEM side must turn the
broken element into fragments and continue the simulation.  The hand-off is
only admissible when four ledgers balance *exactly* (to round-off), because a
leaking ledger is indistinguishable from a physical source term and silently
biases every later result:

======================  =====================================================
mass                    Σ m_i = ρ V_elem
linear momentum         Σ m_i v_i = M v
angular momentum        Σ (I_i ω_i + m_i d_i × v_i) = I_elem ω
energy                  Σ (½m_i v_i² + ½ ω_i·I_i·ω_i) = ½Mv² + ½ω·I_elem·ω + U
inertia tensor          Σ (I_i + m_i(|d_i|²1 - d_i d_i^T)) = I_elem
======================  =====================================================

``d_i`` is the fragment centroid relative to the element centroid, ``I_i`` the
fragment inertia tensor about its own centroid and ``U`` the elastic strain
energy released by the fracture.

Two design decisions carry the exactness:

* Geometry, not a surrogate.  Fragments are the *actual* convex cells produced
  by the fracture planes, and their inertia tensors are evaluated from the
  tetrahedron moments of :mod:`src.coupling.geometry.polytope`
  (``∫ρ(|r|²1 - rr^T)dV``).  The older sphere-equivalent model
  (:mod:`src.coupling.fem_to_dem_transfer`) cannot represent an anisotropic
  tensor and therefore cannot close the inertia or angular-momentum ledgers;
  see :meth:`ExactFractureTransfer.compare_with_sphere_surrogate`.
* Energy release without momentum pollution.  The released strain energy is
  invested in a mass-centred expansion mode ``u_i = α d_i`` with
  ``α = sqrt(2U / Σ m_i |d_i|²)``.  Then ``Σ m_i u_i = 0`` (centroid identity),
  ``Σ m_i d_i × u_i = 0`` (radial mode) and ``Σ ½m_i|u_i|² = U``, and the cross
  term ``Σ m_i v_i·u_i`` vanishes for the same reason, so the energy ledger
  closes without touching the momentum or angular-momentum ledgers.

No domain semantics: the module speaks of elements, faces, fragments, density
and energy only.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .geometry import Polytope, polytope_from_vertices, split


# --------------------------------------------------------------------------- #
# Fracture surface produced by the FEM side
# --------------------------------------------------------------------------- #
@dataclass
class FractureFace:
    """One triangle/polygon of the fracture surface."""

    vertices: np.ndarray
    normal: np.ndarray
    area: float

    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=float).reshape(-1, 3)
        self.normal = np.asarray(self.normal, dtype=float).reshape(3)
        norm = float(np.linalg.norm(self.normal))
        if norm > 1e-14:
            self.normal = self.normal / norm

    @property
    def centroid(self) -> np.ndarray:
        return self.vertices.mean(axis=0)

    def polygon_area(self) -> float:
        """Newell area of the vertex loop (independent of the declared area)."""
        v = self.vertices
        acc = np.zeros(3)
        for k in range(v.shape[0]):
            acc += np.cross(v[k], v[(k + 1) % v.shape[0]])
        return 0.5 * float(np.linalg.norm(acc))

    def polygon_normal(self) -> np.ndarray:
        v = self.vertices
        acc = np.zeros(3)
        for k in range(v.shape[0]):
            acc += np.cross(v[k], v[(k + 1) % v.shape[0]])
        n = float(np.linalg.norm(acc))
        return acc / n if n > 1e-14 else np.zeros(3)

    def validation(self) -> Dict[str, float]:
        """Consistency of the emitted face with its own vertices."""
        declared = float(self.area)
        geometric = self.polygon_area()
        area_rel = abs(declared - geometric) / max(abs(geometric), 1e-300)
        cos_n = float(abs(np.dot(self.normal, self.polygon_normal())))
        return {
            "declared_area": declared,
            "geometric_area": geometric,
            "area_rel_error": area_rel,
            "normal_alignment": cos_n,       # 1.0 = perfect
            "normal_angle_deg": math.degrees(math.acos(min(1.0, max(-1.0, cos_n)))),
        }


def load_failure_faces(path: str) -> Tuple[List[FractureFace], dict]:
    """Consume a ``failure_faces.json`` artifact.

    Returns the faces and a validation report.  A face whose declared area does
    not match the area implied by its own vertices, or whose normal is not
    aligned with the vertex loop, is *reported*, not silently repaired: the
    caller decides whether the artifact is trustworthy.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    raw_faces = payload.get("faces", payload if isinstance(payload, list) else [])
    faces: List[FractureFace] = []
    report: List[dict] = []
    for idx, item in enumerate(raw_faces):
        face = FractureFace(
            vertices=np.asarray(item["vertices"], dtype=float),
            normal=np.asarray(item.get("normal", [0.0, 0.0, 1.0]), dtype=float),
            area=float(item.get("area", 0.0)),
        )
        check = face.validation()
        check["index"] = idx
        check["ok"] = bool(check["area_rel_error"] <= 1e-9 and check["normal_alignment"] >= 1.0 - 1e-9)
        report.append(check)
        faces.append(face)
    summary = {
        "path": path,
        "n_faces": len(faces),
        "n_valid": sum(1 for r in report if r["ok"]),
        "max_area_rel_error": max((r["area_rel_error"] for r in report), default=0.0),
        "min_normal_alignment": min((r["normal_alignment"] for r in report), default=1.0),
        "faces": report,
        "all_valid": all(r["ok"] for r in report) if report else False,
    }
    return faces, summary


def dump_failure_faces(faces: Sequence[FractureFace], path: str) -> str:
    """Write a ``failure_faces.json`` artifact (FEM-side producer)."""
    payload = {
        "schema": "failure_faces/v1",
        "faces": [
            {
                "vertices": np.asarray(f.vertices, dtype=float).tolist(),
                "normal": np.asarray(f.normal, dtype=float).tolist(),
                "area": float(f.area),
                "centroid": f.centroid.tolist(),
            }
            for f in faces
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


# --------------------------------------------------------------------------- #
# Element / fragment states
# --------------------------------------------------------------------------- #
@dataclass
class FEMElementState:
    """Pre-fracture state of one FEM element (rigid-body motion + stored energy)."""

    polyhedron: Polytope
    density: float
    velocity: np.ndarray
    angular_velocity: np.ndarray
    elastic_strain_energy: float = 0.0

    def __post_init__(self) -> None:
        self.velocity = np.asarray(self.velocity, dtype=float).reshape(3)
        self.angular_velocity = np.asarray(self.angular_velocity, dtype=float).reshape(3)

    @property
    def mass(self) -> float:
        return self.polyhedron.mass(self.density)

    @property
    def centroid(self) -> np.ndarray:
        return self.polyhedron.centroid

    @property
    def inertia(self) -> np.ndarray:
        return self.polyhedron.inertia(self.density)

    @property
    def translational_energy(self) -> float:
        return 0.5 * self.mass * float(np.dot(self.velocity, self.velocity))

    @property
    def rotational_energy(self) -> float:
        return 0.5 * float(self.angular_velocity @ self.inertia @ self.angular_velocity)

    @property
    def linear_momentum(self) -> np.ndarray:
        return self.mass * self.velocity

    @property
    def angular_momentum(self) -> np.ndarray:
        return self.inertia @ self.angular_velocity


@dataclass
class DEMFragment:
    """A DEM particle born from one convex fracture cell."""

    polyhedron: Polytope
    mass: float
    centroid: np.ndarray
    velocity: np.ndarray
    angular_velocity: np.ndarray
    inertia: np.ndarray
    volume: float

    @property
    def translational_energy(self) -> float:
        return 0.5 * self.mass * float(np.dot(self.velocity, self.velocity))

    @property
    def rotational_energy(self) -> float:
        return 0.5 * float(self.angular_velocity @ self.inertia @ self.angular_velocity)

    @property
    def kinetic_energy(self) -> float:
        return self.translational_energy + self.rotational_energy

    @property
    def linear_momentum(self) -> np.ndarray:
        return self.mass * self.velocity

    def angular_momentum_about(self, origin: np.ndarray) -> np.ndarray:
        """I ω + d × (m v) about ``origin``."""
        d = self.centroid - np.asarray(origin, dtype=float)
        return self.inertia @ self.angular_velocity + np.cross(d, self.linear_momentum)


# --------------------------------------------------------------------------- #
# Ledger result
# --------------------------------------------------------------------------- #
@dataclass
class FourLedgerExact:
    """Four conservation ledgers of one FEM→DEM fracture hand-off."""

    mass_source: float
    mass_target: float
    mass_residual: float

    momentum_source: np.ndarray
    momentum_target: np.ndarray
    momentum_residual: float

    angular_source: np.ndarray
    angular_target: np.ndarray
    angular_residual: float

    energy_source: float
    energy_target: float
    energy_residual: float

    inertia_source: np.ndarray
    inertia_target: np.ndarray
    inertia_residual: float

    volume_source: float
    volume_target: float
    volume_residual: float

    tolerance: Dict[str, float]

    def passed(self) -> Dict[str, bool]:
        return {
            "mass": self.mass_residual <= self.tolerance["mass"],
            "momentum": self.momentum_residual <= self.tolerance["momentum"],
            "angular_momentum": self.angular_residual <= self.tolerance["angular_momentum"],
            "energy": self.energy_residual <= self.tolerance["energy"],
            "inertia": self.inertia_residual <= self.tolerance["inertia"],
            "volume": self.volume_residual <= self.tolerance["volume"],
        }

    def summary(self) -> dict:
        checks = self.passed()
        return {
            "mass": {
                "source": self.mass_source,
                "target": self.mass_target,
                "residual": self.mass_residual,
                "passed": checks["mass"],
            },
            "momentum": {
                "source": self.momentum_source.tolist(),
                "target": self.momentum_target.tolist(),
                "residual": self.momentum_residual,
                "passed": checks["momentum"],
            },
            "angular_momentum": {
                "source": self.angular_source.tolist(),
                "target": self.angular_target.tolist(),
                "residual": self.angular_residual,
                "passed": checks["angular_momentum"],
            },
            "energy": {
                "source": self.energy_source,
                "target": self.energy_target,
                "residual": self.energy_residual,
                "passed": checks["energy"],
            },
            "inertia": {
                "source": self.inertia_source.tolist(),
                "target": self.inertia_target.tolist(),
                "residual": self.inertia_residual,
                "passed": checks["inertia"],
            },
            "volume": {
                "source": self.volume_source,
                "target": self.volume_target,
                "residual": self.volume_residual,
                "passed": checks["volume"],
            },
            "tolerances": dict(self.tolerance),
            "all_passed": all(checks.values()),
        }


DEFAULT_TOLERANCE = {
    "mass": 1e-12,
    "momentum": 1e-10,
    "angular_momentum": 1e-10,
    "energy": 1e-10,
    "inertia": 1e-10,
    "volume": 1e-12,
}


# --------------------------------------------------------------------------- #
# Transfer
# --------------------------------------------------------------------------- #
class ExactFractureTransfer:
    """Cut one convex element by its fracture planes and close four ledgers."""

    def __init__(self, tolerance: Optional[Dict[str, float]] = None,
                 min_relative_volume: float = 1e-9):
        self.tolerance = dict(DEFAULT_TOLERANCE)
        if tolerance:
            self.tolerance.update(tolerance)
        self.min_relative_volume = min_relative_volume

    # -- partition --------------------------------------------------------- #
    def partition(self, element: FEMElementState,
                  faces: Sequence[FractureFace]) -> List[Polytope]:
        """Split the element by every fracture plane; return the convex cells.

        Each fracture face defines a plane (centroid, unit normal); the set of
        planes partitions the element into convex cells whose union is the
        element itself, so ``Σ V_cell = V_element`` is an identity of the
        construction and a check of the implementation.
        """
        cells: List[Polytope] = [element.polyhedron]
        v_elem = element.polyhedron.volume
        for face in faces:
            point = face.centroid
            normal = face.normal
            if float(np.linalg.norm(normal)) < 1e-14:
                continue
            nxt: List[Polytope] = []
            for cell in cells:
                pos, neg = split(cell, point, normal)
                for part in (pos, neg):
                    if part is not None and part.volume > self.min_relative_volume * v_elem:
                        nxt.append(part)
            if nxt:
                cells = nxt
        return cells

    # -- fragments + ledgers ----------------------------------------------- #
    def transfer(self, element: FEMElementState,
                 faces: Sequence[FractureFace],
                 energy_mode: str = "expansion") -> Tuple[List[DEMFragment], FourLedgerExact]:
        """Consume the fracture surface and produce DEM fragments + ledgers."""
        cells = self.partition(element, faces)
        if not cells:
            raise ValueError("fracture planes produced no fragments")

        c_elem = element.centroid
        omega = element.angular_velocity
        v_elem = element.velocity

        frags: List[DEMFragment] = []
        for cell in cells:
            mass = cell.mass(element.density)
            d = cell.centroid - c_elem
            vel = v_elem + np.cross(omega, d)
            frags.append(DEMFragment(
                polyhedron=cell,
                mass=mass,
                centroid=cell.centroid.copy(),
                velocity=vel,
                angular_velocity=omega.copy(),
                inertia=cell.inertia(element.density),
                volume=cell.volume,
            ))

        if energy_mode == "expansion":
            self._invest_strain_energy(frags, element)
        elif energy_mode != "none":
            raise ValueError(f"unknown energy_mode {energy_mode!r}")

        return frags, self.ledgers(element, frags)

    def _invest_strain_energy(self, frags: List[DEMFragment],
                              element: FEMElementState) -> dict:
        """Add the released strain energy as a momentum-free expansion mode."""
        u_rel = float(element.elastic_strain_energy)
        if u_rel <= 0.0 or not frags:
            return {"alpha": 0.0, "invested": 0.0}
        c_elem = element.centroid
        inertia_sum = sum(f.mass * float(np.dot(f.centroid - c_elem, f.centroid - c_elem))
                          for f in frags)
        if inertia_sum <= 1e-300:
            return {"alpha": 0.0, "invested": 0.0}
        alpha = math.sqrt(2.0 * u_rel / inertia_sum)
        for f in frags:
            f.velocity = f.velocity + alpha * (f.centroid - c_elem)
        invested = 0.5 * alpha ** 2 * inertia_sum
        return {
            "alpha": alpha,
            "invested": invested,
            "mode": "mass_centred_expansion",
            "momentum_pollution": float(np.linalg.norm(
                sum((f.mass * alpha * (f.centroid - c_elem) for f in frags), np.zeros(3)))),
        }

    # -- ledger arithmetic -------------------------------------------------- #
    @staticmethod
    def _rel(a: float, b: float) -> float:
        return abs(a - b) / max(0.5 * (abs(a) + abs(b)), 1e-300)

    def ledgers(self, element: FEMElementState,
                frags: Sequence[DEMFragment]) -> FourLedgerExact:
        c_elem = element.centroid
        m_elem = element.mass
        mom_src = element.linear_momentum
        ang_src = element.angular_momentum
        e_src = (element.translational_energy + element.rotational_energy
                 + float(element.elastic_strain_energy))
        i_src = element.inertia

        m_tgt = float(sum(f.mass for f in frags))
        mom_tgt = sum((f.linear_momentum for f in frags), np.zeros(3))
        ang_tgt = sum((f.angular_momentum_about(c_elem) for f in frags), np.zeros(3))
        e_tgt = float(sum(f.kinetic_energy for f in frags))
        i_tgt = np.zeros((3, 3))
        for f in frags:
            d = f.centroid - c_elem
            i_tgt += f.inertia + f.mass * (float(np.dot(d, d)) * np.eye(3) - np.outer(d, d))
        v_tgt = float(sum(f.volume for f in frags))

        scale_mom = max(0.5 * (np.linalg.norm(mom_src) + np.linalg.norm(mom_tgt)), 1e-300)
        scale_ang = max(0.5 * (np.linalg.norm(ang_src) + np.linalg.norm(ang_tgt)), 1e-300)
        scale_i = max(0.5 * (np.linalg.norm(i_src) + np.linalg.norm(i_tgt)), 1e-300)

        return FourLedgerExact(
            mass_source=m_elem,
            mass_target=m_tgt,
            mass_residual=self._rel(m_elem, m_tgt),
            momentum_source=mom_src,
            momentum_target=mom_tgt,
            momentum_residual=float(np.linalg.norm(mom_src - mom_tgt) / scale_mom),
            angular_source=ang_src,
            angular_target=ang_tgt,
            angular_residual=float(np.linalg.norm(ang_src - ang_tgt) / scale_ang),
            energy_source=e_src,
            energy_target=e_tgt,
            energy_residual=self._rel(e_src, e_tgt),
            inertia_source=i_src,
            inertia_target=i_tgt,
            inertia_residual=float(np.linalg.norm(i_src - i_tgt) / scale_i),
            volume_source=float(element.polyhedron.volume),
            volume_target=v_tgt,
            volume_residual=self._rel(element.polyhedron.volume, v_tgt),
            tolerance=dict(self.tolerance),
        )

    # -- baseline comparison ------------------------------------------------ #
    def compare_with_sphere_surrogate(self, element: FEMElementState,
                                      faces: Sequence[FractureFace]) -> dict:
        """Run the sphere-equivalent model on the same hand-off and compare.

        The surrogate (``src.coupling.fem_to_dem_transfer``) replaces every
        fragment by a solid sphere of equal mass.  An isotropic sphere tensor
        cannot reproduce an anisotropic element tensor, so the inertia and
        angular-momentum ledgers are structurally unreachable there.  This
        method quantifies the gap on identical input, which is the before/after
        baseline for the exact path.
        """
        from .fem_to_dem_transfer import (
            FailureFace as LegacyFace,
            FEMElementState as LegacyElement,
            FEMtoDEMTransfer,
        )

        cells = self.partition(element, faces)
        legacy_faces = []
        for cell in cells:
            # One representative triangular facet per cell, oriented outward.
            tri = cell.vertices[:3]
            normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
            area = 0.5 * float(np.linalg.norm(normal))
            legacy_faces.append(LegacyFace(
                vertices=tri,
                normal=normal / max(np.linalg.norm(normal), 1e-300),
                area=area,
            ))
        legacy_element = LegacyElement(
            mass=element.mass,
            velocity=element.velocity,
            angular_velocity=element.angular_velocity,
            inertia_tensor=element.inertia,
            elastic_strain_energy=element.elastic_strain_energy,
            centroid=element.centroid,
        )
        legacy = FEMtoDEMTransfer(tolerance=1e-9)
        legacy_frags, legacy_ledger = legacy.transfer(legacy_element, legacy_faces, n_fragments=len(cells))

        c_elem = element.centroid
        ang = np.zeros(3)
        inertia = np.zeros((3, 3))
        for f in legacy_frags:
            d = f.position - c_elem
            ang += f.angular_momentum + np.cross(d, f.linear_momentum)
            inertia += f.inertia_tensor + f.mass * (float(np.dot(d, d)) * np.eye(3) - np.outer(d, d))
        ang_src = element.angular_momentum
        i_src = element.inertia
        return {
            "n_fragments": len(legacy_frags),
            "legacy_mass_residual": legacy_ledger.mass_residual,
            "legacy_trace_inertia_residual": legacy_ledger.inertia_residual,
            "legacy_full_tensor_inertia_residual": float(
                np.linalg.norm(inertia - i_src) / max(np.linalg.norm(i_src), 1e-300)),
            "legacy_angular_momentum_residual": float(
                np.linalg.norm(ang - ang_src) / max(np.linalg.norm(ang_src), 1e-300)),
            "legacy_is_isotropic_fragment_inertia": all(
                np.allclose(f.inertia_tensor, np.eye(3) * f.inertia_tensor[0, 0], atol=1e-12)
                for f in legacy_frags),
        }


def cube_element(center: Sequence[float] = (0.0, 0.0, 0.0),
                 half: float = 0.5,
                 density: float = 1.0,
                 velocity: Sequence[float] = (1.0, 0.5, 0.0),
                 angular_velocity: Sequence[float] = (0.2, -0.4, 0.6),
                 elastic_strain_energy: float = 0.3) -> FEMElementState:
    """A synthetic cube element (no domain semantics)."""
    from .geometry import box

    return FEMElementState(
        polyhedron=box(center, [half, half, half]),
        density=density,
        velocity=np.asarray(velocity, dtype=float),
        angular_velocity=np.asarray(angular_velocity, dtype=float),
        elastic_strain_energy=elastic_strain_energy,
    )


def plane_cut_faces(plane_points: Sequence[Sequence[float]],
                    normals: Sequence[Sequence[float]]) -> List[FractureFace]:
    """Convenience: synthetic triangular faces lying in the given planes."""
    faces: List[FractureFace] = []
    for p, n in zip(plane_points, normals):
        p = np.asarray(p, dtype=float)
        n = np.asarray(n, dtype=float)
        n = n / np.linalg.norm(n)
        tangent = np.cross(n, [0.0, 0.0, 1.0])
        if np.linalg.norm(tangent) < 1e-8:
            tangent = np.cross(n, [1.0, 0.0, 0.0])
        tangent = tangent / np.linalg.norm(tangent)
        other = np.cross(n, tangent)
        verts = np.array([p + 0.2 * tangent, p - 0.2 * tangent, p + 0.2 * other])
        faces.append(FractureFace(vertices=verts, normal=n, area=0.5 * np.linalg.norm(
            np.cross(verts[1] - verts[0], verts[2] - verts[0]))))
    return faces


__all__ = [
    "DEMFragment",
    "ExactFractureTransfer",
    "FEMElementState",
    "FourLedgerExact",
    "FractureFace",
    "cube_element",
    "dump_failure_faces",
    "load_failure_faces",
    "plane_cut_faces",
]