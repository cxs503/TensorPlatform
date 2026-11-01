"""
Hull-girder vertical vibration (振动噪声域) — solver + design checks.

Why this module exists
----------------------
The platform covers 阻力 / 推进 / 耐波 / 操纵 / 稳性 / 结构, but the
振动噪声 discipline had **no physics-based capability at all**: ``grep -rn
"vibration" backend/services`` used to hit only data-driven machinery health
models (monitoring / fault prediction), never a hull-girder structural
dynamics solver.  This module closes that gap for the one quantity that is
both (a) checkable against an exact anchor and (b) actually used at concept
stage: the **global vertical hull-girder natural frequencies** (2-node,
3-node, ...) and their separation from propeller blade-rate excitation.

Derivation of the anchor (no empirical data needed)
---------------------------------------------------
Global vertical hull vibration is modelled as a free-free Euler-Bernoulli
beam::

    EI * d4w/dx4 = m(x) * omega^2 * w(x)

For a **uniform** beam the exact solution is
``w = A cosh(bx) + B sinh(bx) + C cos(bx) + D sin(bx)`` with
``b^4 = m omega^2 / (EI)``; the free-free (M = V = 0 at both ends) boundary
conditions give the classical transcendental equation::

    cosh(bL) * cos(bL) = 1

with the first non-trivial roots ``bL = 4.7300407448627, 7.8532046240958,
10.9956078380017, 14.1371654912575, ...``.  Then
``f_n = (b_n L)^2 / (2 pi L^2) * sqrt(EI / m)``.

Three *independent* solvers are provided so the number can be cross-examined
instead of trusted:

* ``uniform_beam_frequencies``  — the closed form above (roots obtained by
  bisection, never hard-coded);
* ``tmm_frequencies``           — exact transfer-matrix solution for
  **piecewise-constant** beams: state vector ``[w, w', M, V]`` propagated
  through each segment with a 4x4 matrix exponential, eigenvalues found from
  ``det(T21(omega)) = 0`` (T21 = the M/V rows restricted to the w/w' columns).
  This solver is exact within each segment, so it is the reference for
  *stepped* beams where no closed form exists;
* ``fem_frequencies``          — Hermite cubic beam finite elements
  (consistent mass matrix, free-free, generalised eigenproblem solved through
  a Cholesky reduction with ``numpy.linalg.eigh``).

Verification (see ``docs/verification/hull_girder_vibration.md``): the closed
form, the transfer-matrix solver and the FE solver agree to <1e-9 / <1e-3
on a uniform beam, and the FE error decays at the theoretical O(h^4) rate.

What is *not* verified (honest scope)
-------------------------------------
The **added-mass coefficient** for the vertical 2-node mode in water is an
empirical parameter (default ``added_mass_ratio = 0.6`` of the displaced
water mass, uniformly distributed over the length).  Nothing in this repo
carries a trial measurement of a hull-girder frequency, so a computed
frequency for a real ship must be quoted with that parameter's sensitivity
(the ``sensitivity`` block returns it), never as a validated number.

Conventions
-----------
* ``x`` runs from AP (0) to FP (L), origin at the aft perpendicular.
* ``w`` is vertical deflection (positive up), ``M`` bending moment,
  ``V`` shear force; state form ``u' = A u`` with
  ``u = [w, w', M, V]`` and ``A = [[0,1,0,0],[0,0,-1/EI,0],[0,0,0,1],[m w^2,0,0,0]]``.
* Mode numbering follows the vibrating-systems convention: 2-node first
  (one interior node pair == one half wave along the hull), i.e. ``n = 1``
  here is the "2-node" mode used by ship designers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

#: First five non-trivial roots of cosh(bL)cos(bL) = 1.  Used only as an
#: *independent* reference in the tests — the solver recomputes them.
UNIFORM_BEAM_ROOT_REFERENCE = (
    4.7300407448627040,
    7.8532046240958376,
    10.995607838001671,
    14.137165491257464,
    17.278759657399481,
)

#: steel density / Young's modulus defaults (structural steel)
RHO_STEEL = 7850.0
E_STEEL = 2.06e11
RHO_SEA_WATER = 1025.0


# ---------------------------------------------------------------------------
# 1. Closed form for the uniform free-free beam
# ---------------------------------------------------------------------------

def _free_free_residual(x: float) -> float:
    """Scaled form of ``cosh(x) cos(x) - 1``: ``cos(x) - sech(x)``.

    Same zeros as the classical equation (divide by ``cosh(x) > 0``) but
    numerically stable for large ``x``, where ``cosh`` would overflow.
    """
    return math.cos(x) - 1.0 / math.cosh(x)


def uniform_beam_roots(n_modes: int, tol: float = 1e-14) -> np.ndarray:
    """Roots of ``cosh(x) * cos(x) = 1`` (x > 0), found by bisection.

    ``x = bL``.  The bracket grid is the multiples of ``pi / 2``: between two
    consecutive grid points the residual ``cos(x) - sech(x)`` is monotone in
    sign change (``cos`` dominates: ``sech`` < 0.2 for x > 2.3), so a sign
    change on a grid cell brackets exactly one root.  Note the first root
    (4.7300) sits *just above* ``3 pi / 2`` and the second (7.8532) *just
    below* ``5 pi / 2`` -- a "one root per half wave" guess would miss both,
    which is why the grid is scanned instead of predicted.
    """
    if n_modes < 1:
        raise ValueError("n_modes must be >= 1")
    step = math.pi / 2.0
    roots = np.empty(n_modes, dtype=float)
    found = 0
    lo = 0.5 * step                              # skip the rigid-body region
    f_lo = _free_free_residual(lo)
    while found < n_modes:
        hi = lo + step
        f_hi = _free_free_residual(hi)
        if f_lo == 0.0:
            roots[found] = lo
            found += 1
        elif f_lo * f_hi < 0.0:
            a, b, fa = lo, hi, f_lo
            for _ in range(200):
                mid = 0.5 * (a + b)
                fm = _free_free_residual(mid)
                if fa * fm <= 0.0:
                    b = mid
                else:
                    a, fa = mid, fm
                if b - a <= tol * max(1.0, abs(b)):
                    break
            roots[found] = 0.5 * (a + b)
            found += 1
        lo, f_lo = hi, f_hi
        if lo > 1000.0:
            raise RuntimeError("root scan failed to converge")
    return roots


def uniform_beam_frequencies(
    EI: float, m_per_len: float, length: float, n_modes: int = 4
) -> np.ndarray:
    """Natural frequencies [Hz] of a uniform free-free Euler-Bernoulli beam.

    ``f_n = (bL)_n^2 / (2 pi L^2) * sqrt(EI / m)``.
    """
    if EI <= 0.0 or m_per_len <= 0.0 or length <= 0.0:
        raise ValueError("EI, mass per length and length must all be > 0")
    bL = uniform_beam_roots(n_modes)
    return bL ** 2 / (2.0 * math.pi * length ** 2) * math.sqrt(EI / m_per_len)


def uniform_beam_mode_shape(bL: float, length: float, x: np.ndarray) -> np.ndarray:
    """Exact free-free mode shape normalised to ``w(0) = 1``."""
    b = bL / length
    sig = (math.cosh(bL) - math.cos(bL)) / (math.sinh(bL) - math.sin(bL))
    xx = np.asarray(x, dtype=float)
    phi = (
        np.cosh(b * xx)
        + np.cos(b * xx)
        - sig * (np.sinh(b * xx) + np.sin(b * xx))
    )
    return phi / phi[0] if phi[0] != 0.0 else phi


# ---------------------------------------------------------------------------
# 2. Beam description / small linear algebra helpers
# ---------------------------------------------------------------------------

@dataclass
class BeamSegment:
    """A piece of beam with constant ``EI`` and constant mass per length."""

    length: float
    EI: float
    m_per_len: float


def _validate_segments(segments: Sequence[BeamSegment]) -> List[BeamSegment]:
    segs = list(segments)
    if not segs:
        raise ValueError("at least one segment is required")
    for s in segs:
        if s.length <= 0.0:
            raise ValueError("segment length must be > 0")
        if s.EI <= 0.0:
            raise ValueError("segment EI must be > 0")
        if s.m_per_len <= 0.0:
            raise ValueError("segment mass per length must be > 0")
    return segs


def _expm4(A: np.ndarray) -> np.ndarray:
    """Matrix exponential for a 4x4 real matrix (scaling & squaring + Taylor).

    Implemented locally so the module stays numpy-only (``scipy`` is not a
    declared API dependency).
    """
    norm = float(np.max(np.sum(np.abs(A), axis=1)))
    if norm == 0.0:
        return np.eye(4)
    k = max(0, int(math.ceil(math.log2(norm / 0.5)))) if norm > 0.5 else 0
    B = A / (2.0 ** k)
    T = np.eye(4)
    term = np.eye(4)
    for n in range(1, 20):
        term = term @ B / n
        T = T + term
    for _ in range(k):
        T = T @ T
    return T


def _transfer_matrix(seg: BeamSegment, omega: float) -> np.ndarray:
    """Exact 4x4 transfer matrix of one segment at angular frequency omega."""
    A = np.array(
        [
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, -1.0 / seg.EI, 0.0],
            [0.0, 0.0, 0.0, 1.0],
            [-seg.m_per_len * omega * omega, 0.0, 0.0, 0.0],
        ]
    )
    return _expm4(A * seg.length)


# ---------------------------------------------------------------------------
# 3. Transfer-matrix reference solver (exact for piecewise-constant beams)
# ---------------------------------------------------------------------------

def _tmm_boundary_det(segments: Sequence[BeamSegment], omega: float) -> float:
    """Scaled determinant of the free-free boundary condition matrix T21."""
    T = np.eye(4)
    for seg in segments:
        T = _transfer_matrix(seg, omega) @ T
    T21 = T[2:, :2]                                # (M, V) rows, (w, theta) cols
    scale = float(np.sum(T21 * T21))
    return float(np.linalg.det(T21)) / max(scale, 1e-300)


def tmm_frequencies(
    segments: Sequence[BeamSegment],
    n_modes: int = 4,
    n_scan: int = 4000,
    omega_max: Optional[float] = None,
) -> np.ndarray:
    """Natural frequencies [Hz] from the exact transfer-matrix method.

    The free-free condition is ``T21(omega) * [w0, theta0]^T = 0``, so
    eigenvalues are the roots of ``det T21(omega)``.  The scan range is
    ``[0.02, 2.5] * omega_est`` where ``omega_est`` is the uniform-beam
    estimate using length-averaged ``EI`` and mass, then each sign change is
    refined by bisection (60 halvings => machine precision).
    """
    segs = _validate_segments(segments)
    total_L = sum(s.length for s in segs)
    EI_bar = sum(s.length for s in segs) / sum(s.length / s.EI for s in segs)
    m_bar = sum(s.m_per_len * s.length for s in segs) / total_L
    f_uniform = uniform_beam_frequencies(EI_bar, m_bar, total_L, n_modes)
    if omega_max is None:
        # stepped beams shift the uniform estimates by well under the factor
        # used here; 2x the uniform estimate of the highest wanted mode gives
        # a scan window that cannot hide a root of interest.
        omega_max = 2.0 * 2.0 * math.pi * float(f_uniform[-1])
    omega_min = 0.02 * 2.0 * math.pi * float(f_uniform[0])

    grid = np.linspace(omega_min, omega_max, n_scan)
    vals = np.array([_tmm_boundary_det(segs, w) for w in grid])
    roots: List[float] = []
    for i in range(len(grid) - 1):
        a, b = grid[i], grid[i + 1]
        fa, fb = vals[i], vals[i + 1]
        if fa == 0.0:
            roots.append(a)
            continue
        if fa * fb < 0.0:
            for _ in range(80):
                mid = 0.5 * (a + b)
                fm = _tmm_boundary_det(segs, mid)
                if fa * fm <= 0.0:
                    b, fb = mid, fm
                else:
                    a, fa = mid, fm
                if b - a <= 1e-14 * max(1.0, abs(b)):
                    break
            roots.append(0.5 * (a + b))
        if len(roots) >= n_modes:
            break
    if len(roots) < n_modes:
        raise RuntimeError(
            f"transfer-matrix scan found {len(roots)} of {n_modes} modes; "
            "increase n_scan / omega_max"
        )
    return np.array([w / (2.0 * math.pi) for w in roots[:n_modes]])


# ---------------------------------------------------------------------------
# 4. Hermite beam finite elements (independent numerical path)
# ---------------------------------------------------------------------------

def _hermite_element(seg: BeamSegment) -> tuple:
    l = seg.length
    k = seg.EI / l ** 3 * np.array(
        [
            [12.0, 6 * l, -12.0, 6 * l],
            [6 * l, 4 * l * l, -6 * l, 2 * l * l],
            [-12.0, -6 * l, 12.0, -6 * l],
            [6 * l, 2 * l * l, -6 * l, 4 * l * l],
        ]
    )
    m = seg.m_per_len * l / 420.0 * np.array(
        [
            [156.0, 22 * l, 54.0, -13 * l],
            [22 * l, 4 * l * l, 13 * l, -3 * l * l],
            [54.0, 13 * l, 156.0, -22 * l],
            [-13 * l, -3 * l * l, -22 * l, 4 * l * l],
        ]
    )
    return k, m


def fem_frequencies(
    segments: Sequence[BeamSegment],
    n_modes: int = 4,
    n_elements: int = 64,
) -> Dict[str, Any]:
    """Free-free beam natural frequencies [Hz] from Hermite cubic FE.

    Each segment is subdivided into ``n_elements`` equal finite elements
    (so mesh refinement is uniform in x).  Two rigid-body modes (``f = 0``)
    are dropped from the returned spectrum.
    """
    segs = _validate_segments(segments)
    n_elem_total = n_elements
    total_L = sum(s.length for s in segs)
    K = np.zeros((2 * (n_elem_total + 1), 2 * (n_elem_total + 1)))
    M = np.zeros_like(K)

    # subdivide every segment proportionally to its length
    pieces: List[BeamSegment] = []
    for s in segs:
        n_sub = max(1, int(round(n_elements * s.length / total_L)))
        for _ in range(n_sub):
            pieces.append(BeamSegment(s.length / n_sub, s.EI, s.m_per_len))

    for e, seg in enumerate(pieces):
        k, m = _hermite_element(seg)
        dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        K[np.ix_(dofs, dofs)] += k
        M[np.ix_(dofs, dofs)] += m

    # generalised eigenproblem K phi = omega^2 M phi, M SPD -> Cholesky reduction
    Lchol = np.linalg.cholesky(M)
    Linv = np.linalg.inv(Lchol)
    A = Linv @ K @ Linv.T
    A = 0.5 * (A + A.T)
    eigvals, eigvecs = np.linalg.eigh(A)
    omega2 = np.clip(eigvals, 0.0, None)
    order = np.argsort(omega2)
    omega2 = omega2[order]
    modes_std = eigvecs[:, order]
    phi = Linv.T @ modes_std                       # back to physical dofs

    freqs = np.sqrt(omega2) / (2.0 * math.pi)
    keep = np.arange(2, 2 + n_modes)               # skip the two rigid modes
    sel = freqs[keep]
    x_nodes = np.linspace(0.0, total_L, len(pieces) + 1)
    shapes = np.vstack([phi[0::2, k] for k in keep])  # translational dofs
    return {
        "frequencies_hz": sel,
        "mode_shapes": shapes,
        "x": x_nodes,
        "n_elements": len(pieces),
        "rigid_body": freqs[:2],
        "K": K,
        "M": M,
        "phi": phi[:, keep],
    }


# ---------------------------------------------------------------------------
# 5. Hull-girder idealisation (box girder midship section)
# ---------------------------------------------------------------------------

def box_girder_section(
    beam_m: float,
    depth_m: float,
    t_deck_m: float,
    t_bottom_m: float,
    t_side_m: float,
    rho_steel: float = RHO_STEEL,
) -> Dict[str, float]:
    """Vertical-bending properties of a thin-walled rectangular box girder.

    Idealisation (documented, deliberately crude): deck and bottom are flat
    plates of area ``B * t`` at ``z = D`` / ``z = 0``; the two side shells are
    vertical plates of thickness ``t_side`` spanning ``0..D``.  Longitudinal
    stiffeners, bilge radius, hatch openings and superstructures are ignored.

    Reference height is the keel (``z = 0``); the neutral axis is at
    ``z_NA = sum(A_i z_i) / sum(A_i)`` and
    ``I = sum(A_i (z_i - z_NA)^2 + own_i)``.

    Analytic limit used as a test anchor: for ``t_side -> 0`` and equal deck /
    bottom areas ``A`` the result collapses to ``I = A D^2 / 2``.
    """
    if min(beam_m, depth_m, t_deck_m, t_bottom_m) <= 0.0:
        raise ValueError("beam, depth and deck/bottom thickness must be > 0")
    if t_side_m < 0.0:
        raise ValueError("side thickness must be >= 0")

    parts = [
        # (area, z_centroid, own second moment about its own centroid axis)
        (beam_m * t_deck_m, depth_m, beam_m * t_deck_m ** 3 / 12.0),
        (beam_m * t_bottom_m, 0.0, beam_m * t_bottom_m ** 3 / 12.0),
    ]
    if t_side_m > 0.0:
        a_side = t_side_m * depth_m
        parts.append((2.0 * a_side, 0.5 * depth_m, 2.0 * t_side_m * depth_m ** 3 / 12.0))

    area = sum(p[0] for p in parts)
    z_na = sum(p[0] * p[1] for p in parts) / area
    I = sum(p[0] * (p[1] - z_na) ** 2 + p[2] for p in parts)
    return {
        "section_area_m2": area,
        "neutral_axis_above_keel_m": z_na,
        "I_vertical_m4": I,
        "steel_mass_per_m": area * rho_steel,
        "section_modulus_deck_m3": I / max(depth_m - z_na, 1e-9),
        "section_modulus_keel_m3": I / max(z_na, 1e-9),
    }


# ---------------------------------------------------------------------------
# 6. Top-level: hull-girder vertical vibration of a ship
# ---------------------------------------------------------------------------

@dataclass
class HullGirderResult:
    frequencies_hz: np.ndarray
    mode_shapes: np.ndarray
    x_m: np.ndarray
    section: Dict[str, float]
    mass: Dict[str, float]
    solver: str
    n_elements: int
    blade_rate: Dict[str, float]
    margins: List[Dict[str, Any]]
    sensitivity: List[Dict[str, float]]
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "frequencies_hz": [round(float(f), 6) for f in self.frequencies_hz],
            "mode_labels": [f"{i + 2}-node" for i in range(len(self.frequencies_hz))],
            "x_m": [round(float(v), 4) for v in self.x_m],
            "mode_shapes": [
                [round(float(v), 9) for v in row] for row in self.mode_shapes
            ],
            "section": {
                k: (round(float(v), 6) if isinstance(v, (int, float)) else v)
                for k, v in self.section.items()
            },
            "mass": {
                k: (round(float(v), 3) if isinstance(v, (int, float)) else v)
                for k, v in self.mass.items()
            },
            "solver": self.solver,
            "n_elements": self.n_elements,
            "blade_rate": {k: round(float(v), 6) for k, v in self.blade_rate.items()},
            "margins": self.margins,
            "sensitivity": self.sensitivity,
            "notes": self.notes,
            "warnings": self.warnings,
        }


def blade_rate_frequency(rpm: float, n_blades: int) -> Dict[str, float]:
    """Propeller blade-passing frequency [Hz] and its first harmonics."""
    if rpm <= 0.0 or n_blades < 1:
        raise ValueError("rpm must be > 0 and n_blades >= 1")
    f_shaft = rpm / 60.0
    f_bp = f_shaft * n_blades
    return {
        "shaft_hz": f_shaft,
        "blade_rate_hz": f_bp,
        "blade_rate_2x_hz": 2.0 * f_bp,
    }


def _margin(f_mode: float, f_exc: float, tol: float = 0.10) -> Dict[str, Any]:
    rel = (f_mode - f_exc) / f_exc
    return {
        "mode_hz": round(f_mode, 6),
        "excitation_hz": round(f_exc, 6),
        "relative_separation": round(rel, 6),
        "resonance_risk": bool(abs(rel) < tol),
        "criterion": f"|df|/f_exc >= {tol:.0%} (configurable engineering rule, not a verified literature value)",
    }


def hull_girder_vibration(
    length_m: float,
    beam_m: float,
    depth_m: float,
    draft_m: float,
    t_deck_m: float,
    t_bottom_m: float,
    t_side_m: float,
    cb: float = 0.82,
    rpm: Optional[float] = 90.0,
    n_blades: int = 4,
    E_pa: float = E_STEEL,
    rho_steel: float = RHO_STEEL,
    rho_water: float = RHO_SEA_WATER,
    nonstructural_mass_per_m: float = 0.0,
    added_mass_ratio: float = 0.6,
    n_modes: int = 4,
    n_elements: int = 64,
    solver: str = "fem",
    sensitivity_added_mass: Sequence[float] = (0.0, 0.3, 0.6, 0.9),
    I_vertical_m4: Optional[float] = None,
    steel_mass_per_m: Optional[float] = None,
) -> HullGirderResult:
    """Global vertical hull-girder vibration frequencies and blade-rate margins.

    ``added_mass_ratio`` is the **empirical** entrained-water mass as a
    fraction of the displaced water mass, spread uniformly over the length
    (a standard concept-stage simplification).  It is a parameter, not a
    verified constant; ``sensitivity`` reports the resulting frequency
    spread so the caller can see how much of the answer depends on it.
    """
    if not (0.0 < cb <= 1.0):
        raise ValueError("cb must lie in (0, 1]")
    if length_m <= 0.0 or beam_m <= 0.0 or depth_m <= 0.0 or draft_m <= 0.0:
        raise ValueError("principal dimensions must be > 0")

    section = box_girder_section(
        beam_m, depth_m, t_deck_m, t_bottom_m, t_side_m, rho_steel
    )
    warnings: List[str] = []
    # Callers who know the real section (drawings / classification data) can
    # override the plate-only idealisation -- the platform rule is "geometry
    # must be validated first", so the idealisation must never be silently
    # preferred over a measured section modulus.
    if I_vertical_m4 is not None:
        if I_vertical_m4 <= 0.0:
            raise ValueError("I_vertical_m4 must be > 0")
        section["I_vertical_m4_idealised"] = section["I_vertical_m4"]
        section["I_vertical_m4"] = float(I_vertical_m4)
        section["I_source"] = "user"
    else:
        section["I_source"] = "box_girder_idealisation"
    if steel_mass_per_m is not None:
        if steel_mass_per_m <= 0.0:
            raise ValueError("steel_mass_per_m must be > 0")
        section["steel_mass_per_m_idealised"] = section["steel_mass_per_m"]
        section["steel_mass_per_m"] = float(steel_mass_per_m)
        section["mass_source"] = "user"
    else:
        section["mass_source"] = "box_girder_idealisation"
    EI = E_pa * section["I_vertical_m4"]

    displacement_volume = cb * length_m * beam_m * draft_m
    m_displaced = rho_water * displacement_volume / length_m       # per metre
    m_added = added_mass_ratio * m_displaced
    m_structural = section["steel_mass_per_m"] + nonstructural_mass_per_m
    m_total = m_structural + m_added

    displacement_mass_t = rho_water * displacement_volume / 1000.0
    steel_fraction = (m_structural * length_m / 1000.0) / displacement_mass_t
    if section["mass_source"] == "box_girder_idealisation" and steel_fraction < 0.08:
        warnings.append(
            "idealised 4-plate midship section gives a steel-weight / displacement "
            f"fraction of {steel_fraction * 100:.1f}% (< 8% typical for a steel ship): "
            "longitudinal stiffeners, longitudinal bulkheads and superstructures are "
            "not represented -- supply I_vertical_m4 / steel_mass_per_m from the "
            "actual section before quoting an absolute frequency"
        )
    if abs(added_mass_ratio) > 1.5:
        warnings.append(
            "added_mass_ratio outside the plausible 0.0-1.5 band for a vertical "
            "hull-girder mode"
        )

    seg = BeamSegment(length=length_m, EI=EI, m_per_len=m_total)
    if solver == "fem":
        res = fem_frequencies([seg], n_modes=n_modes, n_elements=n_elements)
        freqs = res["frequencies_hz"]
        shapes = res["mode_shapes"]
        x = res["x"]
        n_el = res["n_elements"]
    elif solver == "tmm":
        freqs = tmm_frequencies([seg], n_modes=n_modes)
        x = np.linspace(0.0, length_m, 101)
        shapes = np.vstack(
            [uniform_beam_mode_shape(uniform_beam_roots(n_modes)[k], length_m, x)
             for k in range(n_modes)]
        )
        n_el = 0
    elif solver == "closed_form":
        freqs = uniform_beam_frequencies(EI, m_total, length_m, n_modes)
        x = np.linspace(0.0, length_m, 101)
        shapes = np.vstack(
            [uniform_beam_mode_shape(uniform_beam_roots(n_modes)[k], length_m, x)
             for k in range(n_modes)]
        )
        n_el = 0
    else:
        raise ValueError("solver must be 'fem', 'tmm' or 'closed_form'")

    blade = blade_rate_frequency(rpm, n_blades) if rpm else {}
    margins: List[Dict[str, Any]] = []
    if blade:
        for i, f in enumerate(freqs):
            for label, f_exc in (
                ("blade_rate", blade["blade_rate_hz"]),
                ("blade_rate_2x", blade["blade_rate_2x_hz"]),
            ):
                m = _margin(float(f), f_exc)
                m["mode"] = f"{i + 2}-node"
                m["excitation"] = label
                margins.append(m)

    sens = []
    for ar in sensitivity_added_mass:
        m_a = ar * m_displaced
        f1 = float(
            uniform_beam_frequencies(EI, m_structural + m_a, length_m, 1)[0]
        )
        sens.append(
            {
                "added_mass_ratio": float(ar),
                "f_2node_hz": round(f1, 6),
            }
        )

    notes = [
        "solver verified against the exact free-free Euler-Bernoulli roots (see docs/verification/hull_girder_vibration.md)",
        "midship idealisation is a 4-plate box girder (no stiffeners/hatch openings/bilge) -- EI is indicative only",
        f"added mass = {added_mass_ratio:.2f} x displaced-water mass, uniformly distributed (empirical parameter)",
        "no full-scale hull-girder frequency measurement is available in this repo: absolute frequencies are not externally validated",
    ]

    return HullGirderResult(
        frequencies_hz=np.asarray(freqs, dtype=float),
        mode_shapes=np.asarray(shapes, dtype=float),
        x_m=np.asarray(x, dtype=float),
        section=section,
        mass={
            "steel_mass_per_m": m_structural,
            "added_mass_per_m": m_added,
            "total_mass_per_m": m_total,
            "displaced_volume_m3": displacement_volume,
            "hull_mass_t": m_structural * length_m / 1000.0,
        },
        solver=solver,
        n_elements=n_el,
        blade_rate=blade,
        margins=margins,
        sensitivity=sens,
        notes=notes,
        warnings=warnings,
    )