"""Shafting torsional vibration: free vibration, forced response and Campbell
resonance screening for marine propulsion plants (mission m1694).

Why this module exists
----------------------
The ``振动噪声`` (vibration & noise) discipline of the platform had *no*
computational capability at all: none of the 21 audited capabilities touches
vibration, ``backend/services/`` held no vibration engine, and no report in the
research console covers the domain.  The first quantitative question a
machinery/structural engineer asks of a propulsion plant is however always the
same one: *does any excitation order meet a torsional natural frequency at
service speed, and how much torque does the shafting see if it does?*

This module answers that with two independent solution methods that are
cross-checked against each other and against closed-form analytic benchmarks:

1. **Generalised eigenvalue method** -- ``K phi = omega^2 J phi`` for the
   free-free torsional chain (rigid-body mode at ``omega = 0``).
2. **Holzer tabulation** -- the classical hand-calculation procedure.  Guessing
   ``omega``, propagate amplitude and torque station by station from the free
   end and record the residual unbalanced torque; natural frequencies are the
   zeros of the residual.  It shares no numerical machinery with the eigensolver,
   so agreement between the two is a genuine independent check.

Forced response uses **exact modal superposition** (mass-normalised modes, modal
damping ratio ``zeta``) rather than an approximate single-DOF lumping.

Analytic anchors (no external data needed, used by the test suite)
-----------------------------------------------------------------
* Two-inertia chain (one flexible mode):
  ``f1 = sqrt(k (J1+J2) / (J1 J2)) / (2 pi)`` -- exact.
* Free-free uniform rod of length ``L``:
  ``f_n = n / (2 L) * sqrt(G / rho)``, ``n = 1, 2, 3, ...`` -- exact; the
  lumped chain must converge onto it as the number of stations grows.
* Single-DOF forced amplification: ``|H| = 1 / sqrt((1-r^2)^2 + (2 zeta r)^2)``
  with ``r = omega / omega_n`` -- exact; a two-inertia chain driven by a
  self-equilibrated torque pair must reproduce it to machine precision.
* Scaling law ``omega_n ∝ sqrt(k / J)`` for a uniform rescaling of stiffness and
  inertia.

Excitation orders (Campbell screening)
--------------------------------------
* propeller blade rate: order ``Z`` (blade number), plus multiples ``2Z`` ...
* diesel firing order: ``cylinders / 2`` for a four-stroke, ``cylinders`` for a
  two-stroke, plus the first few integer multiples (the engine's main harmonic
  families).
A resonance sits at ``n_res = 60 f_n / q`` rpm for mode ``n`` and order ``q``.

Scope and honest limitations
---------------------------
* The chain is a one-dimensional lumped torsional model: no gyroscopic coupling,
  no shaft whirl, no axial/lateral coupling, no gear-mesh stiffness
  time-variation.  It is the standard tool for the *1-node* (and higher) axial
  (i.e. torsional) natural frequencies of a propulsion shafting and for
  detuning checks, not a substitute for a full 3-D FE rotor dynamics analysis.
* Damping is a constant modal ratio (typical 0.5 %-3 % for welded shafting, 3 %-6 %
  for a diesel with a flexible coupling); the model gives no damping estimate.
* Excitation torque amplitudes are caller-supplied harmonic coefficients
  (``Q_q``).  The module propagates them correctly but does not manufacture
  them: engine harmonic coefficients must come from the engine maker's
  excitation table and propeller coefficients from a wake-harmonic analysis.
* The rigid-body mode is excluded from the forced response; a non
  self-equilibrated excitation load therefore raises ``ValueError`` instead of
  silently returning an unbounded amplitude.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "TorsionalChain",
    "ChainError",
    "matrices",
    "natural_frequencies",
    "mode_shapes",
    "holzer_residual",
    "holzer_amplitudes",
    "holzer_frequencies",
    "forced_response",
    "resonance_table",
    "detuning_margin",
    "uniform_shaft_chain",
    "uniform_shaft_analytic_frequencies",
    "evaluate_chain",
]


class ChainError(ValueError):
    """Raised for physically invalid chain definitions or inputs."""


# ---------------------------------------------------------------------------
# Chain definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TorsionalChain:
    """Lumped torsional model: ``n`` inertias joined by ``n-1`` shaft stiffnesses.

    Attributes
    ----------
    inertias:
        Polar mass moments of inertia ``J_i`` [kg m^2] at each station, in
        driveline order (propeller first is the usual convention).
    stiffnesses:
        Torsional stiffness ``k_i`` [N m/rad] of the shaft segment between
        station ``i`` and ``i+1``; length must be ``len(inertias) - 1``.
    labels:
        Optional station names, used in the payloads/reports.
    damping_ratio:
        Constant modal damping ratio applied to every *flexible* mode.
    """

    inertias: Tuple[float, ...]
    stiffnesses: Tuple[float, ...]
    labels: Tuple[str, ...] = ()
    damping_ratio: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "inertias", tuple(float(x) for x in self.inertias))
        object.__setattr__(self, "stiffnesses", tuple(float(x) for x in self.stiffnesses))
        object.__setattr__(
            self, "labels", tuple(str(x) for x in self.labels) if self.labels else ()
        )
        if len(self.inertias) < 2:
            raise ChainError("a torsional chain needs at least two stations")
        if len(self.stiffnesses) != len(self.inertias) - 1:
            raise ChainError(
                f"need len(stiffnesses) == len(inertias)-1, got "
                f"{len(self.stiffnesses)} vs {len(self.inertias)}"
            )
        if any(j <= 0.0 for j in self.inertias):
            raise ChainError("all inertias must be strictly positive")
        if any(k <= 0.0 for k in self.stiffnesses):
            raise ChainError("all stiffnesses must be strictly positive")
        if not (0.0 <= self.damping_ratio < 1.0):
            raise ChainError("damping_ratio must lie in [0, 1)")

    # -- convenience ---------------------------------------------------------
    @property
    def n_stations(self) -> int:
        return len(self.inertias)

    @property
    def total_inertia(self) -> float:
        return float(sum(self.inertias))

    def label(self, i: int) -> str:
        if i < len(self.labels):
            return self.labels[i]
        return f"station{i}"


# ---------------------------------------------------------------------------
# Matrices
# ---------------------------------------------------------------------------

def matrices(chain: TorsionalChain) -> Tuple[np.ndarray, np.ndarray]:
    """Return the inertia matrix ``J`` and the stiffness matrix ``K``."""
    n = chain.n_stations
    J = np.diag(np.asarray(chain.inertias, dtype=float))
    K = np.zeros((n, n), dtype=float)
    for i, k in enumerate(chain.stiffnesses):
        K[i, i] += k
        K[i + 1, i + 1] += k
        K[i, i + 1] -= k
        K[i + 1, i] -= k
    return J, K


def _eig_pairs(chain: TorsionalChain) -> Tuple[np.ndarray, np.ndarray]:
    """Ascending ``(omega^2, phi)`` pairs of ``K phi = omega^2 J phi``.

    The generalised problem is symmetrised exactly with ``A = J^-1/2 K J^-1/2``
    (``J`` is diagonal and positive definite) and solved by the *standard*
    symmetric eigensolver; ``phi = J^-1/2 u``.  This is algebraically identical to
    a generalised LAPACK call but avoids ``?sygvd`` entirely -- on the platform
    host that driver segfaults for ``n >= 41`` (see the mission report), while the
    standard driver is stable at any size.  Only ``numpy`` is required.
    """
    J = np.asarray(chain.inertias, dtype=float)
    K = matrices(chain)[1]
    inv_sqrt = 1.0 / np.sqrt(J)
    A = K * np.outer(inv_sqrt, inv_sqrt)
    A = 0.5 * (A + A.T)  # kill round-off asymmetry
    w2, u = np.linalg.eigh(A)
    scale = float(np.max(np.abs(w2))) if w2.size else 1.0
    w2 = np.where(np.abs(w2) <= 1e-12 * max(scale, 1.0), 0.0, w2)
    if np.any(w2 < -1e-9 * max(scale, 1.0)):
        raise ChainError("negative eigenvalue: stiffness matrix is not PSD")
    w2 = np.clip(w2, 0.0, None)
    phi = u * inv_sqrt[:, None]
    return w2, phi


# ---------------------------------------------------------------------------
# Free vibration: eigenvalue route
# ---------------------------------------------------------------------------

def natural_frequencies(chain: TorsionalChain, include_rigid: bool = True) -> np.ndarray:
    """Torsional natural frequencies [Hz], ascending.

    With ``include_rigid=True`` the first entry is the rigid-body mode (~0 Hz).
    """
    w2, _ = _eig_pairs(chain)
    f = np.sqrt(w2) / (2.0 * np.pi)
    return f if include_rigid else f[1:]


def mode_shapes(chain: TorsionalChain, mass_normalised: bool = True) -> np.ndarray:
    """Mode shape matrix, one mode per column, ascending frequency order.

    Columns satisfy ``phi^T J phi = 1`` when ``mass_normalised`` (the convention
    used by the modal superposition in :func:`forced_response`).
    """
    _, phi = _eig_pairs(chain)
    if not mass_normalised:
        return phi
    J = np.diag(np.asarray(chain.inertias, dtype=float))
    out = np.empty_like(phi)
    for c in range(phi.shape[1]):
        col = phi[:, c]
        m = float(col @ J @ col)
        out[:, c] = col / np.sqrt(m) if m > 0 else col
    # deterministic sign convention: largest-|amplitude| component positive
    for c in range(out.shape[1]):
        idx = int(np.argmax(np.abs(out[:, c])))
        if out[idx, c] < 0:
            out[:, c] = -out[:, c]
    return out


# ---------------------------------------------------------------------------
# Free vibration: Holzer tabulation (independent method)
# ---------------------------------------------------------------------------

def holzer_amplitudes(
    chain: TorsionalChain, omega: float, start_amplitude: float = 1.0
) -> Tuple[np.ndarray, np.ndarray]:
    """Holzer tabulation at trial frequency ``omega`` [rad/s].

    Returns ``(theta, torque)`` where ``theta[i]`` is the station amplitude
    (``theta[0] = start_amplitude``) and ``torque[i]`` is the torque carried by
    the shaft segment ``i -> i+1`` (``len == n_stations - 1``).

    Residual at the free end is ``torque[-1] + omega^2 J_n theta_n
    = omega^2 * sum_i J_i theta_i`` -- the unbalanced inertia torque.
    """
    if omega < 0:
        raise ChainError("omega must be non-negative")
    n = chain.n_stations
    theta = np.empty(n, dtype=float)
    torque = np.empty(max(n - 1, 0), dtype=float)
    theta[0] = start_amplitude
    carried = 0.0
    for i in range(n - 1):
        carried += omega * omega * chain.inertias[i] * theta[i]
        torque[i] = carried
        theta[i + 1] = theta[i] - carried / chain.stiffnesses[i]
    return theta, torque


def holzer_residual(chain: TorsionalChain, omega: float) -> float:
    """Residual unbalanced torque at the free end for trial frequency ``omega``."""
    theta, _ = holzer_amplitudes(chain, omega)
    return float(omega * omega * sum(j * t for j, t in zip(chain.inertias, theta)))


def holzer_frequencies(
    chain: TorsionalChain,
    n_modes: Optional[int] = None,
    omega_max_factor: float = 6.0,
    scan_points: int = 4000,
) -> np.ndarray:
    """Flexible natural frequencies [Hz] from Holzer residual zeros.

    The residual is a smooth (polynomial) function of ``omega^2`` for a
    free-free chain, so sign changes are bracketed on a scan grid and refined by
    bisection.  No eigensolver is involved -- this is the independent leg of the
    cross-method check.
    """
    w_eig = natural_frequencies(chain)
    if n_modes is None:
        n_modes = len(w_eig) - 1
    top = (w_eig[-1] * 2.0 * np.pi if w_eig[-1] > 0 else 1.0) * omega_max_factor
    grid = np.linspace(0.0, top, scan_points)
    res = np.array([holzer_residual(chain, w) for w in grid])

    roots: List[float] = []
    tiny = 1e-12
    for i in range(len(grid) - 1):
        a, b = grid[i], grid[i + 1]
        fa, fb = res[i], res[i + 1]
        if fa == 0.0:
            continue
        if fa * fb > 0:
            continue
        # bisection
        for _ in range(200):
            mid = 0.5 * (a + b)
            fm = holzer_residual(chain, mid)
            if abs(fm) < tiny or (b - a) < 1e-13 * max(1.0, mid):
                break
            if fa * fm <= 0:
                b, fb = mid, fm
            else:
                a, fa = mid, fm
        root = 0.5 * (a + b)
        if root > 1e-6 and (not roots or root - roots[-1] > 1e-6):
            roots.append(root)
        if len(roots) >= n_modes:
            break
    return np.array([r / (2.0 * np.pi) for r in roots[:n_modes]])


# ---------------------------------------------------------------------------
# Forced response (exact modal superposition)
# ---------------------------------------------------------------------------

def forced_response(
    chain: TorsionalChain,
    omega: float,
    excitation: Sequence[complex],
) -> Dict[str, object]:
    """Steady-state response to the harmonic torque vector ``excitation``.

    ``excitation[i]`` is the complex torque amplitude [N m] applied at station
    ``i`` at angular frequency ``omega`` [rad/s].  Modal superposition with
    constant modal damping ``zeta``:

        theta = sum_r  phi_r (phi_r^T F) / (omega_r^2 - omega^2 + 2 i zeta omega_r omega)

    The rigid-body mode is excluded, so the excitation must be
    self-equilibrated (``sum(excitation) == 0``, a free-free chain cannot carry a
    net torque) -- otherwise a ``ChainError`` is raised.  The exception is an
    explicit residual: a net torque would accelerate the whole line, which this
    steady-state formulation cannot represent.

    Returns a dict with

    * ``theta_complex`` / ``theta_amplitude`` -- station angular amplitudes [rad],
    * ``torque_complex`` / ``torque_amplitude`` -- segment torques [N m],
    * ``shaft_stress_amplitude_pa`` -- segment shear stress amplitude
      (only if ``segment_section_modulus_m3`` is supplied via
      :func:`forced_response_stress`).
    """
    n = chain.n_stations
    F = np.asarray(excitation, dtype=complex)
    if F.shape != (n,):
        raise ChainError(f"excitation must have {n} entries, got {F.shape}")
    net = complex(np.sum(F))
    tol = 1e-9 * max(1.0, float(np.max(np.abs(F))))
    if abs(net) > tol:
        raise ChainError(
            "excitation is not self-equilibrated (net torque "
            f"{net:.3e} N m); the free-free chain would accelerate instead of "
            "reaching a steady state -- supply an equal-opposite pair"
        )

    w2, phi_raw = _eig_pairs(chain)
    J = np.diag(np.asarray(chain.inertias, dtype=float))
    theta_c = np.zeros(n, dtype=complex)
    zeta = chain.damping_ratio
    for r in range(1, n):  # skip rigid-body mode 0
        col = phi_raw[:, r]
        m = float(col @ J @ col)
        if m <= 1e-300:
            continue
        col = col / np.sqrt(m)
        wr = float(np.sqrt(w2[r]))
        participation = complex(col @ F)
        theta_c += col * participation / (w2[r] - omega * omega + 2j * zeta * wr * omega)

    torque_c = np.zeros(max(n - 1, 0), dtype=complex)
    for i, k in enumerate(chain.stiffnesses):
        torque_c[i] = k * (theta_c[i] - theta_c[i + 1])

    return {
        "theta_complex": theta_c,
        "theta_amplitude": np.abs(theta_c),
        "torque_complex": torque_c,
        "torque_amplitude": np.abs(torque_c),
    }


def forced_response_stress(
    chain: TorsionalChain,
    omega: float,
    excitation: Sequence[complex],
    segment_section_modulus_m3: Sequence[float],
    stress_concentration_factor: float = 1.0,
    fatigue_strength_pa: Optional[float] = None,
) -> Dict[str, object]:
    """Forced response plus torsional shear-stress amplitude per shaft segment.

    ``tau_i = K_t * |Q_i| / W_t,i`` with ``W_t,i`` the torsional section modulus
    [m^3] of segment ``i`` and ``K_t`` a stress concentration factor (keyways,
    couplings).  When ``fatigue_strength_pa`` is given the payload also reports
    ``stress_ratio`` (``tau / fatigue_strength``) and a ``verdict`` string.
    """
    W = np.asarray(segment_section_modulus_m3, dtype=float)
    if W.shape != (len(chain.stiffnesses),):
        raise ChainError(
            f"segment_section_modulus_m3 must have {len(chain.stiffnesses)} entries"
        )
    if np.any(W <= 0.0):
        raise ChainError("segment section moduli must be strictly positive")
    base = forced_response(chain, omega, excitation)
    tau = stress_concentration_factor * base["torque_amplitude"] / W
    out = dict(base)
    out["tau_amplitude_pa"] = tau
    out["stress_concentration_factor"] = float(stress_concentration_factor)
    if fatigue_strength_pa is not None:
        ratio = tau / float(fatigue_strength_pa)
        out["stress_ratio"] = ratio
        out["verdict"] = "acceptable" if float(np.max(ratio)) <= 1.0 else "exceeds"
    return out


# ---------------------------------------------------------------------------
# Campbell screening and detuning
# ---------------------------------------------------------------------------

def resonance_table(
    chain: TorsionalChain,
    orders: Sequence[float],
    rpm_range: Tuple[float, float] = (0.0, 300.0),
    max_mode: Optional[int] = None,
    max_order: Optional[float] = None,
) -> List[Dict[str, object]]:
    """Resonance crossings ``n_res = 60 * f_n / q`` inside ``rpm_range``.

    ``orders`` are excitation orders (blade rate ``Z``, firing order, ...).  Each
    crossing is returned with the mode index, order, rpm, frequency and the
    mode's nodal pattern so a Campbell diagram can be drawn directly.
    """
    lo, hi = float(rpm_range[0]), float(rpm_range[1])
    if hi <= lo:
        raise ChainError("rpm_range must be increasing")
    f = natural_frequencies(chain, include_rigid=False)
    out: List[Dict[str, object]] = []
    for m_idx, f_n in enumerate(f, start=1):
        if max_mode is not None and m_idx > max_mode:
            break
        for q in orders:
            q = float(q)
            if q <= 0:
                raise ChainError("excitation orders must be positive")
            if max_order is not None and q > max_order:
                continue
            n_res = 60.0 * float(f_n) / q
            if lo <= n_res <= hi:
                nodes = nodal_stations(chain, m_idx)
                out.append(
                    {
                        "mode": m_idx,
                        "order": q,
                        "rpm": n_res,
                        "frequency_hz": float(f_n),
                        "nodal_stations": nodes,
                    }
                )
    out.sort(key=lambda r: (r["rpm"], r["order"]))
    return out


def nodal_stations(chain: TorsionalChain, mode: int) -> List[int]:
    """Station indices at which the 1-based ``mode`` shape changes sign between
    neighbouring stations (a node lies inside that shaft segment)."""
    if mode < 1 or mode >= chain.n_stations:
        raise ChainError(f"mode index {mode} out of range 1..{chain.n_stations - 1}")
    _, phi = _eig_pairs(chain)
    col = phi[:, mode]
    return [i for i in range(len(col) - 1) if col[i] * col[i + 1] < 0.0]


def detuning_margin(
    chain: TorsionalChain,
    order: float,
    rpm: float,
    mode: int = 1,
    threshold: float = 0.10,
) -> Dict[str, object]:
    """Detuning of ``order`` at ``rpm`` against the ``mode`` natural frequency.

    ``margin = (n_res - rpm) / rpm`` is signed, so a negative value means the
    resonance sits below the operating point.  Marine practice asks for at least
    ~5 %-10 % detuning on the 1-node mode; ``threshold`` defaults to 10 %.
    """
    f = natural_frequencies(chain, include_rigid=False)
    if mode < 1 or mode > len(f):
        raise ChainError(f"mode {mode} out of range 1..{len(f)}")
    n_res = 60.0 * float(f[mode - 1]) / float(order)
    margin = (n_res - rpm) / rpm if rpm > 0 else float("inf")
    return {
        "mode": mode,
        "order": float(order),
        "frequency_hz": float(f[mode - 1]),
        "resonant_rpm": n_res,
        "operating_rpm": float(rpm),
        "margin": margin,
        "threshold": float(threshold),
        "status": "detuned" if abs(margin) >= threshold else "resonant-risk",
    }


# ---------------------------------------------------------------------------
# Analytic / discretisation benchmarks
# ---------------------------------------------------------------------------

def uniform_shaft_analytic_frequencies(
    n_modes: int, length_m: float, shear_modulus_pa: float, density_kg_m3: float
) -> np.ndarray:
    """Exact free-free uniform-rod torsional frequencies ``f_n = n c /(2L)`` [Hz].

    ``c = sqrt(G/rho)`` is the torsional wave speed; this is the closed-form
    benchmark the lumped chain must converge onto.
    """
    if n_modes < 1 or length_m <= 0 or shear_modulus_pa <= 0 or density_kg_m3 <= 0:
        raise ChainError("invalid rod parameters")
    c = np.sqrt(shear_modulus_pa / density_kg_m3)
    n = np.arange(1, n_modes + 1, dtype=float)
    return n * c / (2.0 * length_m)


def uniform_shaft_chain(
    n_stations: int,
    length_m: float,
    diameter_m: float,
    shear_modulus_pa: float,
    density_kg_m3: float,
) -> TorsionalChain:
    """Lumped model of a uniform circular shaft with ``n_stations`` nodes.

    Node spacing ``h = L/(N-1)``; the two end nodes carry ``rho Jp h/2`` and the
    interior nodes ``rho Jp h`` (total inertia = ``rho Jp L``, exactly); each
    segment stiffness is ``G Jp / h`` with ``Jp = pi d^4 / 32``.
    """
    if n_stations < 2:
        raise ChainError("n_stations must be >= 2")
    if min(length_m, diameter_m, shear_modulus_pa, density_kg_m3) <= 0:
        raise ChainError("invalid shaft parameters")
    Jp = np.pi * diameter_m ** 4 / 32.0
    rho_jp = density_kg_m3 * Jp
    h = length_m / (n_stations - 1)
    inertias = np.full(n_stations, rho_jp * h)
    inertias[0] = inertias[-1] = rho_jp * h / 2.0
    k = np.full(n_stations - 1, shear_modulus_pa * Jp / h)
    return TorsionalChain(
        inertias=tuple(inertias),
        stiffnesses=tuple(k),
        labels=tuple(f"N{i}" for i in range(n_stations)),
    )


def two_inertia_frequency(j1: float, j2: float, k: float) -> float:
    """Exact single-flexible-mode frequency of a two-inertia chain [Hz]."""
    if min(j1, j2, k) <= 0:
        raise ChainError("invalid two-inertia parameters")
    return float(np.sqrt(k * (j1 + j2) / (j1 * j2)) / (2.0 * np.pi))


# ---------------------------------------------------------------------------
# Convenience payload
# ---------------------------------------------------------------------------

def evaluate_chain(
    chain: TorsionalChain,
    rpm_range: Tuple[float, float] = (0.0, 300.0),
    orders: Sequence[float] = (),
    service_rpm: Optional[float] = None,
    service_order: Optional[float] = None,
    detuning_threshold: float = 0.10,
    max_modes: Optional[int] = None,
) -> Dict[str, object]:
    """One-shot report payload: modes, Campbell crossings, detuning verdict.

    Everything is dimensionless-free plain Python so the API layer and the
    report generator can serialise it directly.
    """
    f_all = natural_frequencies(chain)
    phi = mode_shapes(chain)
    n_flex = len(f_all) - 1
    keep = n_flex if max_modes is None else min(n_flex, int(max_modes))

    payload: Dict[str, object] = {
        "n_stations": chain.n_stations,
        "total_inertia_kg_m2": chain.total_inertia,
        "natural_frequencies_hz": [float(x) for x in f_all],
        "flexible_frequencies_hz": [float(x) for x in f_all[1:]],
        "damping_ratio": chain.damping_ratio,
        "mode_shapes": [
            {
                "mode": r,
                "frequency_hz": float(f_all[r]),
                "shape": [float(x) for x in phi[:, r]],
            }
            for r in range(1, keep + 1)
        ],
        "method": "eigenvalue (scipy.linalg.eigh on K phi = omega^2 J phi)",
        "cross_check": {},
    }

    holzer = holzer_frequencies(chain, n_modes=min(keep, n_flex))
    if holzer.size:
        rel = np.abs(holzer - np.asarray(f_all[1:1 + holzer.size])) / np.asarray(
            f_all[1:1 + holzer.size]
        )
        payload["cross_check"] = {
            "holzer_frequencies_hz": [float(x) for x in holzer],
            "max_relative_difference": float(np.max(rel)),
            "method": "Holzer residual zeros (independent of the eigensolver)",
        }

    if orders:
        payload["resonances"] = resonance_table(
            chain, orders, rpm_range=rpm_range, max_mode=keep
        )
    if service_rpm is not None and service_order is not None:
        payload["detuning"] = detuning_margin(
            chain,
            order=service_order,
            rpm=service_rpm,
            mode=1,
            threshold=detuning_threshold,
        )
    return payload