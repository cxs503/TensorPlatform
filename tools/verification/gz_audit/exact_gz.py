"""Independent exact references for transverse stability (KN / GZ cross curves).

Two independent ground-truth paths, neither of which reads the platform model:

1. ``wall_sided_kn`` -- closed-form cross curve of a wall-sided (rectangular
   section) hull.  For 0 <= phi <= deck-edge immersion angle the metacentre is
   fixed, so

       KN(phi) = sin(phi) * [ KB + BM * (1 + 1/2 tan^2 phi) ]
       GZ(phi) = KN(phi) - KG * sin(phi)

   is exact (not an approximation): it is the classical wall-sided result, and
   we cross-check it below against voxel volume integration.

2. ``VoxelHull.kn`` -- direct numerical cross curves.  The hull is a solid
   sampled on an equal-volume voxel grid; for a heel angle phi the solid is
   rotated about the keel (x-axis) and the waterplane is placed so that the
   immersed volume equals the upright displacement volume.  KN is then the
   transverse offset of the centre of buoyancy in the world frame, i.e.
   KN = mean(y_w) over immersed cells.  Because the cells carry equal volume,
   "constant displacement" reduces to "keep the N lowest cells by z_w", which
   makes the free-surface solve exact (no iteration).

The voxel path is validated against the closed form on a box barge (see
``selftest_box``) before being used on hulls that have no closed form.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List

import numpy as np

RHO_SEAWATER = 1025.0


# ---------------------------------------------------------------------------
# 1. Closed form: wall-sided hull
# ---------------------------------------------------------------------------
def wall_sided_kn(phi_deg: float, KB: float, BM: float) -> float:
    """Exact KN of a wall-sided hull at heel ``phi_deg`` (valid to deck edge)."""
    p = math.radians(phi_deg)
    t = math.tan(p)
    return math.sin(p) * (KB + BM * (1.0 + 0.5 * t * t))


def wall_sided_gz(phi_deg: float, KB: float, BM: float, KG: float) -> float:
    return wall_sided_kn(phi_deg, KB, BM) - KG * math.sin(math.radians(phi_deg))


def box_hydrostatics(B: float, T: float) -> Dict[str, float]:
    """Exact KB, BM, KM of a rectangular-section (box) hull."""
    KB = T / 2.0
    BM = B * B / (12.0 * T)          # I/vol = (L B^3/12)/(L B T)
    return {"KB": KB, "BM": BM, "KM": KB + BM}


def box_deck_edge_angle(B: float, T: float, D: float) -> float:
    """Heel angle at which the deck edge immerses (box hull)."""
    if D <= T:
        return 90.0
    return math.degrees(math.atan((D - T) / (B / 2.0)))


# ---------------------------------------------------------------------------
# 2. Numerical reference: voxel cross curves
# ---------------------------------------------------------------------------
class VoxelHull:
    """Solid hull sampled on an equal-volume voxel grid, with exact free-surface solve."""

    def __init__(self, solid: Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray],
                 L: float, B: float, D: float, nx: int = 64, ny: int = 48, nz: int = 48):
        self.L, self.B, self.D = float(L), float(B), float(D)
        self.dx, self.dy, self.dz = L / nx, B / ny, D / nz
        self.dV = self.dx * self.dy * self.dz
        xs = -L / 2.0 + (np.arange(nx) + 0.5) * self.dx
        ys = -B / 2.0 + (np.arange(ny) + 0.5) * self.dy
        zs = (np.arange(nz) + 0.5) * self.dz
        X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
        inside = np.asarray(solid(X, Y, Z), dtype=bool) & (Z <= D) & (Z >= 0.0)
        self.x, self.y, self.z = X[inside], Y[inside], Z[inside]
        self.n = int(self.x.size)

    def volume_below(self, T: float) -> float:
        return float(np.count_nonzero(self.z <= T)) * self.dV

    def kn(self, phi_deg: float, V0: float) -> float:
        """KN from an exact constant-displacement free-surface solve."""
        p = math.radians(phi_deg)
        c, s = math.cos(p), math.sin(p)
        yw = self.y * c + self.z * s      # world transverse
        zw = -self.y * s + self.z * c     # world vertical
        k = int(round(V0 / self.dV))
        k = max(1, min(k, self.n))
        idx = np.argpartition(zw, k - 1)[:k]
        return float(np.mean(yw[idx]))


def wigley_solid(L: float, B: float, T: float):
    """Wigley-parabolic hull, y = (B/2)(1-(2x/L)^2)(1-(z/T)^2); Cb = 4/9 exactly."""
    def solid(X, Y, Z):
        half = (B / 2.0) * (1.0 - (2.0 * X / L) ** 2) * (1.0 - (Z / T) ** 2)
        return np.abs(Y) <= half
    return solid


def parabolic_wall_sided_solid(L: float, B: float):
    """Wall-sided hull with parabolic waterlines: b(x) = (B/2)(1-(2x/L)^2) at every height."""
    def solid(X, Y, Z):
        half = (B / 2.0) * (1.0 - (2.0 * X / L) ** 2)
        return np.abs(Y) <= half
    return solid


def parabolic_hydrostatics(L: float, B: float, T: float, n: int = 20001) -> Dict[str, float]:
    """Exact KB/BM/KM of the parabolic-waterplane wall-sided hull by quadrature."""
    x = np.linspace(-L / 2.0, L / 2.0, n)
    b = (B / 2.0) * (1.0 - (2.0 * x / L) ** 2)
    b = np.clip(b, 0.0, None)
    A_wp = 2.0 * np.trapezoid(b, x)                    # waterplane area
    V = A_wp * T                                        # wall-sided => prismatic
    I_T = (2.0 / 3.0) * np.trapezoid(b ** 3, x)        # transverse inertia of waterplane
    Cb = V / (L * B * T)
    return {"KB": T / 2.0, "BM": I_T / V, "KM": T / 2.0 + I_T / V,
            "Cb": Cb, "Awp": A_wp, "V": V, "I_T": I_T}


def box_solid(L: float, B: float):
    def solid(X, Y, Z):
        return (np.abs(X) <= L / 2.0) & (np.abs(Y) <= B / 2.0)
    return solid


def selftest_box(B: float = 20.0, T: float = 10.0, D: float = 20.0, L: float = 100.0,
                 angles: List[float] = (0.0, 5.0, 10.0, 20.0, 30.0, 40.0)) -> Dict[str, float]:
    """Validate the voxel path against the closed form on a box barge."""
    hull = VoxelHull(box_solid(L, B), L, B, D, nx=40, ny=80, nz=80)
    V0 = hull.volume_below(T)
    hs = box_hydrostatics(B, T)
    worst = 0.0
    for a in angles:
        kn_num = hull.kn(a, V0)
        kn_ref = wall_sided_kn(a, hs["KB"], hs["BM"])
        worst = max(worst, abs(kn_num - kn_ref) / max(kn_ref, 1e-9))
    return {"voxel_vs_closed_form_max_rel_err": worst, "V0_m3": V0,
            "V0_analytic_m3": L * B * T}
