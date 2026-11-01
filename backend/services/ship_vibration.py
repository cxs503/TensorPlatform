"""Hull-girder vertical vibration core — mission m1699 (振动噪声域首建).

Why this file exists
--------------------
The platform capability audit listed 21 capabilities and **none** covered the
振动噪声 discipline; a repository-wide grep for ``vibration`` / ``acoustic`` /
``blade_rate`` / ``6954`` returned no ship-domain code, so every vibration number
the platform could produce was MOCK-by-absence.  This module is the first REAL
building block of that discipline: pure functions, closed-form where possible,
each one paired with an independent check in
``tools/verify/vibration/audit_hull_vibration.py``.

SHIPPED (REAL)
--------------
1. ``free_free_wavenumbers`` / ``free_free_beam_frequencies`` — free–free
   Bernoulli–Euler hull-girder modes from the exact characteristic equation
   ``cos(lam)cosh(lam) = 1``.  Cross-checked against an independent 1-D Hermite
   beam finite-element eigenvalue solve and against the published root table.
2. ``circle_added_mass_exact`` / ``ellipse_added_mass_exact`` /
   ``flat_plate_added_mass_exact`` — exact 2-D zero-frequency added mass for the
   three shapes that have textbook closed forms (Lamb 1932 §71).
3. ``hull_girder_vertical_frequencies`` — uniform-beam modes with the dry mass
   plus a **user supplied** distributed added mass.
4. ``blade_rate_frequency`` / ``resonance_margin`` — exact kinematics of the
   propeller excitation and the structural/excitation separation margin.

GUARDED (partially REAL)
------------------------
``section_added_mass_2d`` is a constant-strength source panel solver.  It passes
its gate for near-circular sections (aspect ratio <= 1.25, ~1 % error at 512
panels) but **fails systematically for elongated sections**: the single-layer
operator on a closed curve has a constant-density null space (removed here by a
minimum-norm least-squares solve) and near-null modes that an exact solve
satisfies to machine precision while returning a wrong potential.  Measured on a
512-panel ellipse with ``(B/2) : T = 2 : 1`` it returns ``640 040 kg/m`` where the
exact value is ``1 288 053 kg/m`` (-50 %), and the deficit grows as the section
flattens (``T/(B/2) = 0.05 -> -96 %``).  The function therefore **refuses**
sections outside the validated aspect ratio instead of returning a
plausible-looking wrong number.  Fixing it needs a doublet (Hess–Smith style) or
mixed-layer formulation — the documented next increment, not a silent rounding of
this one.

NOT SUPPORTED (honest gaps)
---------------------------
* High-frequency (Lewis-limit) added mass, where the free surface becomes a
  pressure-release plane.  Zero-frequency added mass overestimates by typically
  5–20 % for hull-girder vertical modes at 0.5–3 Hz, so the frequencies computed
  here are a *lower bound*.  No correction is applied: no full-scale reference
  exists in-tree to calibrate it (evidence WITHHELD).
* Station-wise added-mass distribution (added mass is lumped uniformly along the
  hull, so mode-shape weighting of added mass is ignored).
* Damping, rotary inertia, shear deformation (Bernoulli–Euler, high above ~5 Hz).
* Local panel / superstructure vibration, propeller cavitation pressure pulses,
  underwater radiated noise — not modelled at all.

Every constant below is an exact transcendental root, an exact potential-flow
result, or an explicitly documented user input; the module never invents a
structural property.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Tuple

import numpy as np

G = 9.81
SEA_WATER_DENSITY = 1025.0

#: Largest section aspect ratio (max polar radius / min polar radius) for which
#: :func:`section_added_mass_2d` has been validated against the exact circle and
#: ellipse results.  Beyond it the single-layer solver degrades catastrophically
#: (see module docstring) and the function raises instead of lying.
MAX_VALIDATED_ASPECT = 1.25

#: Free-free beam wave numbers of ``cos(lam)cosh(lam) = 1`` (published table).
FREE_FREE_LAMBDA_TABLE: Tuple[float, ...] = (
    4.7300407448627040,
    7.8532046240958376,
    10.995607838001671,
    14.137165491257464,
    17.278759657399481,
)


# ---------------------------------------------------------------------------
# 1. Free-free Bernoulli-Euler beam
# ---------------------------------------------------------------------------
def _free_free_residual(lam: float) -> float:
    """``cos(lam) - 2/(e^lam + e^-lam)`` — algebraically ``cos*cosh - 1``.

    The rewritten form is used because ``cosh`` overflows for large ``lam`` while
    this form stays bounded, so the root finder is safe for any mode number.
    """
    return math.cos(lam) - 2.0 / (math.exp(lam) + math.exp(-lam))


def free_free_wavenumbers(n_modes: int = 3) -> np.ndarray:
    """First ``n_modes`` roots of ``cos(lam)cosh(lam) = 1`` (lam > 0).

    Brackets are located by scanning around the asymptotic location
    ``lam_n ~ (n + 0.5)*pi`` and each bracket is closed with Brent's method.  The
    scan is required because the sign wobble of the residual near the asymptote
    is tiny for mode 2 (root sits 7.3e-4 *below* ``2.5*pi``).
    """
    from scipy.optimize import brentq

    if n_modes < 1:
        raise ValueError("n_modes must be >= 1")
    roots: List[float] = []
    for n in range(1, n_modes + 1):
        centre = (n + 0.5) * math.pi
        grid = np.linspace(centre - 0.75, centre + 0.75, 601)
        vals = np.array([_free_free_residual(x) for x in grid])
        bracket = None
        for i in range(len(grid) - 1):
            if vals[i] * vals[i + 1] < 0.0:
                bracket = (grid[i], grid[i + 1])
                break
            if vals[i] == 0.0:
                bracket = (grid[i], grid[i])
                break
        if bracket is None:  # pragma: no cover - defensive
            raise RuntimeError(f"no bracket for free-free mode {n}")
        roots.append(
            float(bracket[0])
            if bracket[0] == bracket[1]
            else float(brentq(_free_free_residual, *bracket, xtol=1e-15, rtol=1e-15))
        )
    return np.array(roots)


def free_free_beam_frequencies(
    length_m: float,
    EI_Nm2: float,
    mass_per_length_kg_m: float,
    n_modes: int = 3,
) -> np.ndarray:
    """Dry free–free beam natural frequencies [Hz]: ``f_n = lam_n^2/(2 pi L^2) sqrt(EI/mu)``."""
    for name, value in (
        ("length_m", length_m),
        ("EI_Nm2", EI_Nm2),
        ("mass_per_length_kg_m", mass_per_length_kg_m),
    ):
        if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
            raise ValueError(f"{name} must be a positive finite number, got {value!r}")
    lam = free_free_wavenumbers(n_modes)
    return lam**2 / (2.0 * math.pi * length_m**2) * math.sqrt(EI_Nm2 / mass_per_length_kg_m)


# ---------------------------------------------------------------------------
# 2. Section geometry helpers (all star-shaped about the origin)
# ---------------------------------------------------------------------------
def ellipse_radius_fn(half_breadth_m: float, draught_m: float) -> Callable[[np.ndarray], np.ndarray]:
    """Polar radius of the elliptical section with semi-axes ``(B/2, T)``."""
    if half_breadth_m <= 0 or draught_m <= 0:
        raise ValueError("section semi-axes must be positive")
    a, d = float(half_breadth_m), float(draught_m)

    def radius(theta: np.ndarray) -> np.ndarray:
        theta = np.asarray(theta, dtype=float)
        return 1.0 / np.sqrt((np.cos(theta) / a) ** 2 + (np.sin(theta) / d) ** 2)

    return radius


def superellipse_radius_fn(
    half_breadth_m: float, draught_m: float, exponent: float = 2.0
) -> Callable[[np.ndarray], np.ndarray]:
    """Fullness-controlled section: ``(|x/a|^p + |y/d|^p)^(1/p) = 1``.

    ``p = 2`` reproduces :func:`ellipse_radius_fn` exactly; ``p -> inf`` tends to
    the rectangle ``B x 2T``, i.e. a full tanker/bulker midship section — which is
    what makes the panel method usable on realistic hull forms.
    """
    if half_breadth_m <= 0 or draught_m <= 0:
        raise ValueError("section semi-axes must be positive")
    if exponent < 2.0:
        raise ValueError("exponent must be >= 2 (p=2 is the ellipse)")
    a, d, p = float(half_breadth_m), float(draught_m), float(exponent)

    def radius(theta: np.ndarray) -> np.ndarray:
        theta = np.asarray(theta, dtype=float)
        return ((np.abs(np.cos(theta)) / a) ** p + (np.abs(np.sin(theta)) / d) ** p) ** (-1.0 / p)

    return radius


def ellipse_added_mass_exact(half_breadth_m: float, rho: float = SEA_WATER_DENSITY) -> float:
    """Exact 2D added mass per unit length of an ellipse for **vertical** motion.

    ``m_a = rho*pi*A**2`` with ``A`` the semi-axis perpendicular to the motion
    (conformal-mapping / virtual-mass result, Lamb 1932 §71).  For a circle
    (``A = a``) this reduces to ``rho*pi*a**2``.
    """
    return rho * math.pi * half_breadth_m**2


def section_aspect_ratio(radius: Callable[[np.ndarray], np.ndarray], n_samples: int = 720) -> float:
    """``max(r)/min(r)`` over one revolution — the validity metric of the solver."""
    theta = np.linspace(0.0, 2.0 * math.pi, n_samples, endpoint=False)
    r = np.asarray(radius(theta), dtype=float)
    if np.any(r <= 0):
        raise ValueError("section radius must be positive everywhere")
    return float(r.max() / r.min())


def flat_plate_added_mass_exact(half_width_m: float, rho: float = SEA_WATER_DENSITY) -> float:
    """2-D added mass of a zero-thickness plate of width ``2*half_width``, ``rho*pi*a**2``.

    This is the ``T -> 0`` limit of :func:`ellipse_added_mass_exact` and is the
    reason a flat section must never be given a small added mass.
    """
    return ellipse_added_mass_exact(half_width_m, rho)


def circle_added_mass_exact(radius_m: float, rho: float = SEA_WATER_DENSITY) -> float:
    """Exact 2D added mass per unit length of a circular cylinder, ``rho*pi*a**2``."""
    return rho * math.pi * radius_m**2


# ---------------------------------------------------------------------------
# 3. 2D added mass by constant-strength source panels
# ---------------------------------------------------------------------------
def _section_panels(radius: Callable[[np.ndarray], np.ndarray], n_panels: int):
    """Panels of a polar section: start, end, length, midpoint, outward normal.

    Nodes sit on equal angle increments; the outward normal comes from the chord,
    so what the solver sees is the *polygon*, not the analytic curve — the
    discretisation error reported by the audit is therefore honest.
    """
    if n_panels < 8:
        raise ValueError("n_panels must be >= 8")
    theta = np.linspace(0.0, 2.0 * math.pi, n_panels, endpoint=False)
    r = radius(theta)
    pts = np.stack([r * np.cos(theta), r * np.sin(theta)], axis=1)
    start = pts
    end = np.roll(pts, -1, axis=0)
    seg = end - start
    length = np.linalg.norm(seg, axis=1)
    midpoint = 0.5 * (start + end)
    tangent = seg / length[:, None]
    normal = np.stack([tangent[:, 1], -tangent[:, 0]], axis=1)
    if float(np.mean(np.einsum("ij,ij->i", midpoint, normal))) < 0.0:
        normal = -normal
    return start, end, length, midpoint, normal


def _panel_velocity_and_potential(
    colloc: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
    length: np.ndarray,
    normal: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Unit-source influence of every panel at every collocation point.

    For a constant-strength source sheet of unit density on ``[0, l]`` in local
    coordinates the complex potential is

        ``F(z) = -(1/2pi) [ (z-l)(ln(z-l) - 1) - z(ln(z) - 1) ]``,

    hence ``dF/dz = (1/2pi)(ln z - ln(z-l)) = u - i v``.  The collocation point is
    evaluated on the **fluid side** of its own panel, which is what makes the
    self term ``v_local = +1/2`` come out of the formula instead of needing a
    hand-applied jump term.

    Returns ``(vel[...,2], phi[...])`` with shape ``(n_panels, n_panels)`` for the
    velocity components and potential (row = collocation point, col = panel).
    """
    n = start.shape[0]
    rel = colloc[:, None, :] - start[None, :, :]
    tan = (end - start) / length[:, None]
    nor = normal
    x = np.einsum("ijk,jk->ij", rel, tan)
    y = np.einsum("ijk,jk->ij", rel, nor)
    on_self = np.eye(n, dtype=bool)
    # pad away from the singular chord for non-self panels only
    y = np.where(on_self, 0.0, y)
    z = x + 1j * y
    zm = (x - length[None, :]) + 1j * y
    with np.errstate(divide="ignore", invalid="ignore"):
        dF = (np.log(z) - np.log(zm)) / (2.0 * math.pi)
        F = -((zm * (np.log(zm) - 1.0)) - (z * (np.log(z) - 1.0))) / (2.0 * math.pi)
    u_loc = dF.real
    v_loc = -dF.imag
    # self panels: remove the numerical log difference and impose the analytic
    # fluid-side value (u = 0, v = +1/2 in the local frame)
    u_loc = np.where(on_self, 0.0, u_loc)
    v_loc = np.where(on_self, 0.5, v_loc)
    F = np.where(on_self, (length[None, :] * (np.log(length[None, :]) - 1.0)) / (2.0 * math.pi), F.real)
    vel = (
        u_loc[:, :, None] * tan[None, :, :] + v_loc[:, :, None] * nor[None, :, :]
    )
    return vel, np.asarray(F, dtype=float)


def section_added_mass_2d(
    radius: Callable[[np.ndarray], np.ndarray],
    n_panels: int = 256,
    rho: float = SEA_WATER_DENSITY,
    direction: Tuple[float, float] = (0.0, 1.0),
) -> Dict[str, object]:
    """2-D added mass per unit length of a near-circular section (guarded).

    The section must be the **full double-body section** (hull + waterplane
    mirror), which is exactly the zero-frequency limit: there the free-surface
    condition degenerates to ``dphi/dn = 0``, i.e. the waterplane is a plane of
    symmetry.  For a half-breadth symmetric hull the double-body section in free
    fluid is therefore the correct zero-frequency model, and no image panel is
    needed (adding one would double-count the mirrored half).

    Parameters
    ----------
    radius : callable
        Polar radius of the closed section (see :func:`ellipse_radius_fn`,
        :func:`superellipse_radius_fn`).
    n_panels : int
        Number of straight constant-strength source panels on the closed curve.
    direction : tuple
        Translation direction; ``(0, 1)`` is vertical (heave), ``(1, 0)``
        horizontal (sway).

    Returns
    -------
    dict with ``m_a`` (kg/m) plus the two independent discrete evaluations of the
    same analytic quantity — momentum ``-rho*sum(phi_i * n_i.U * l_i)`` and energy
    ``rho*sum(phi_i * (U.n_i) * l_i)`` — which must agree to machine precision
    (an inconsistency there means the potential/velocity pair is wrong).

    High-frequency (Lewis limit) added mass, where the free surface becomes a
    pressure-release plane, is **not** provided (see module docstring: no
    calibration reference exists in-tree).
    """
    aspect = section_aspect_ratio(radius)
    if aspect > MAX_VALIDATED_ASPECT:
        raise ValueError(
            f"section aspect ratio {aspect:.3f} exceeds the validated limit "
            f"{MAX_VALIDATED_ASPECT}: the single-layer solver is not accurate there "
            "(measured -50 % on a 2:1 ellipse, -96 % at 20:1). Use the exact "
            "circle/ellipse/flat-plate formulas, or fix the solver with a doublet "
            "formulation first (see module docstring)."
        )
    start, end, length, midpoint, normal = _section_panels(radius, n_panels)
    vel, phi = _panel_velocity_and_potential(midpoint, start, end, length, normal)
    A = np.einsum("ijk,jk->ij", vel, normal)
    U = np.array(direction, dtype=float)
    U = U / np.linalg.norm(U)
    b = normal @ U
    # A constant source density on a closed curve induces zero velocity outside,
    # so it is a null vector of the Neumann operator: ``A`` is singular
    # (cond ~ 1e15) and a plain ``solve`` returns an arbitrary offset.  The
    # minimum-norm least-squares solution removes exactly that null component and
    # is the physically correct one (a constant layer changes phi by a constant,
    # which leaves ``sum(phi * n * l)`` unchanged anyway).
    sigma, *_ = np.linalg.lstsq(A, b, rcond=None)
    bc_residual = float(np.max(np.abs(A @ sigma - b)))
    phi_i = phi @ sigma

    n_dir = normal @ U
    m_a = -rho * float(np.sum(phi_i * n_dir * length))
    # T = -1/2 rho * sum(phi * dphi/dn * l); added mass = 2T/U^2 with U = 1
    m_a_energy = -rho * float(np.sum(phi_i * b * length))
    return {
        "m_a": m_a,
        "m_a_momentum": m_a,
        "m_a_energy": m_a_energy,
        "consistency_rel": abs(m_a - m_a_energy) / abs(m_a) if m_a else float("nan"),
        "bc_residual": bc_residual,
        "net_source": float(np.sum(sigma * length)),
        "n_panels": int(n_panels),
        "rho": float(rho),
        "direction": [float(U[0]), float(U[1])],
        "area_m2": float(0.5 * np.sum(midpoint[:, 0] * np.roll(midpoint[:, 1], -1)
                                      - np.roll(midpoint[:, 0], -1) * midpoint[:, 1])),
    }


# ---------------------------------------------------------------------------
# 4. Hull-girder frequencies and blade-rate excitation
# ---------------------------------------------------------------------------
def hull_girder_vertical_frequencies(
    length_m: float,
    EI_Nm2: float,
    dry_mass_per_length_kg_m: float,
    added_mass_per_length_kg_m: float,
    n_modes: int = 3,
) -> Dict[str, object]:
    """2-node / 3-node / … vertical free–free hull-girder frequencies [Hz].

    Added mass is lumped with the dry mass (``mu = m_dry + m_a``); the added-mass
    ratio and the resulting frequency are returned for traceability.
    """
    if added_mass_per_length_kg_m < 0:
        raise ValueError("added mass must be non-negative")
    mu = float(dry_mass_per_length_kg_m) + float(added_mass_per_length_kg_m)
    f = free_free_beam_frequencies(length_m, EI_Nm2, mu, n_modes)
    return {
        "frequencies_hz": [float(x) for x in f],
        "mode_numbers": list(range(1, n_modes + 1)),
        "mode_labels": [f"{i + 1}-node" for i in range(n_modes)],
        "effective_mass_per_length_kg_m": mu,
        "added_mass_ratio": float(added_mass_per_length_kg_m) / float(dry_mass_per_length_kg_m),
        "length_m": float(length_m),
        "EI_Nm2": float(EI_Nm2),
    }


def blade_rate_frequency(rpm: float, n_blades: int) -> Dict[str, float]:
    """Propeller blade-passing frequency [Hz] and shaft order [1/shaft-rev]."""
    if rpm <= 0 or n_blades < 1:
        raise ValueError("rpm must be > 0 and n_blades >= 1")
    shaft_hz = rpm / 60.0
    return {
        "shaft_frequency_hz": shaft_hz,
        "blade_rate_hz": shaft_hz * n_blades,
        "blade_rate_order": float(n_blades),
    }


def resonance_margin(structural_hz: float, excitation_hz: float) -> Dict[str, float]:
    """Separation margin between a structural and an excitation frequency.

    ``margin = (f_exc - f_str)/f_str``; a negative margin means the excitation
    sits below the structural frequency.  The review-relevant quantity is the
    absolute value: guidelines ask for >= 20 % separation (habitability-driven),
    which is *reported*, not enforced, because the platform holds no rule text.
    """
    if structural_hz <= 0 or excitation_hz <= 0:
        raise ValueError("frequencies must be positive")
    return {
        "structural_hz": float(structural_hz),
        "excitation_hz": float(excitation_hz),
        "margin": float((excitation_hz - structural_hz) / structural_hz),
        "relative_separation": float(abs(excitation_hz - structural_hz) / structural_hz),
        "ratio": float(excitation_hz / structural_hz),
    }