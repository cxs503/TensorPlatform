"""Hull-girder vibration kernel — pure functions, single source of truth.

Mission m1684 (platform 席 自主工时).  Before this module the ``振动噪声``
discipline of the 船舶总体性能 platform had **no implementation at all**: a grep
for ``vibration`` over ``backend/services`` + ``backend/physics`` returned only
unrelated ML/sensor plumbing, and none of the 21 audited capabilities exposed a
vibration entry point.  This is the from-zero kernel that closes that hole at
the level where closed-form truth exists, i.e. it is *verifiable* rather than
merely plausible:

===============  ==============================================================
component        basis / referee
===============  ==============================================================
``free_free_beam_eigenvalues``  exact Euler-Bernoulli free-free characteristic
                 roots (cos x cosh x = 1, x1 = 4.730040744862704...)
``uniform_beam_frequency``  closed form ``f_n = (b_n L)^2/(2*pi*L^2) * sqrt(EI/m)``
``hull_girder_modal``  finite-element free-free EB beam, cubic Hermite elements,
                 piecewise EI(x) and m(x) — verified against the two exact
                 brackets (see ``analytic_bracket``) and against similitude
``blade_rate_frequency``  definitional ``f = n[rps] * Z``
``resonance_separation``  DNV-RP-C103 style coincidence avoidance
                 (recommended separation 15 % below / 10 % above the
                 blade-rate / blade-rate multiple), criterion supplied by caller
``sdof_amplification``  exact damped SDOF dynamic magnifier
===============  ==============================================================

Applicability / scope honesty
-----------------------------
* Model class is **hull girder vertical (or horizontal) 2-node bending**.  It is
  *not* a 3-D FEM of the aft body, not a machinery-foundation model, and does
  not predict local panel vibration, structure-borne noise paths or underwater
  radiated noise spectra.  Those stay ``NOT_SUPPORTED``.
* ``m_per_length`` must already carry the entrained-water (added) mass.  The
  helper :func:`effective_mass_per_length` applies a documented added-mass
  factor; the default 0.25 is the classical vertical-bending entrained water
  allowance for full hulls (0.1–0.25 range is the usual band, use the low end
  for fine forms).
* Vibration *limits* are deliberately **not** hard-coded.  :func:`velocity_limit_check`
  compares an RMS velocity against a limit the caller supplies from the
  applicable class rule (e.g. an ISO 6954:2000 comfort category curve).  Encoding
  standard limit digits from memory would be exactly the kind of unverifiable
  hard-coding this platform is being audited for, so the limit stays an input.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

G = 9.81

# Exact free-free Euler-Bernoulli characteristic roots b_n * L (rad).
# x1 = 4.730040744862704026, x2 = 7.853204624095837594, x_n -> (2n+1)pi/2.
_FREE_FREE_ROOTS: Tuple[float, ...] = (
    4.730040744862704,
    7.853204624095838,
    10.995607838001671,
    14.137165491257464,
    17.278759657399481,
    20.420352245626061,
    23.561944902040455,
    26.703537555508186,
)


# ---------------------------------------------------------------------------
# Exact uniform beam
# ---------------------------------------------------------------------------

def free_free_beam_eigenvalue(mode: int = 1, length: float = 1.0) -> float:
    """Characteristic ``b_n * L`` of the n-th elastic mode of a free-free beam.

    ``mode`` is 1-based and counts *elastic* (non rigid-body) modes.  Modes
    beyond the tabulated eight are continued with the exact asymptotic branch
    ``(2n+1)*pi/2`` refined by two Newton steps of ``cos(x)*cosh(x) = 1``, so the
    function stays converged for high ``mode`` (used by the mesh-convergence
    study in the m1684 verification harness).
    """
    if mode < 1:
        raise ValueError("mode must be >= 1")
    if mode <= len(_FREE_FREE_ROOTS):
        return _FREE_FREE_ROOTS[mode - 1]
    x = (2 * mode + 1) * math.pi / 2.0
    for _ in range(60):
        f = math.cos(x) * math.cosh(x) - 1.0
        # d/dx[cos x cosh x] = cos x sinh x - sin x cosh x
        df = math.cos(x) * math.sinh(x) - math.sin(x) * math.cosh(x)
        dx = f / df
        x -= dx
        if abs(dx) < 1e-14 * max(1.0, abs(x)):
            break
    return x


def uniform_beam_frequency(
    E: float, I: float, mass_per_length: float, length: float, mode: int = 1
) -> float:
    """Natural frequency [Hz] of a uniform free-free Euler-Bernoulli beam.

    ``f_n = (b_n L)^2 / (2 pi L^2) * sqrt(E I / m')``

    ``E`` in Pa, ``I`` in m^4, ``mass_per_length`` in kg/m (entrained water
    included), ``length`` in m.
    """
    if E <= 0 or I <= 0 or mass_per_length <= 0 or length <= 0:
        raise ValueError("E, I, mass_per_length and length must be positive")
    bL = free_free_beam_eigenvalue(mode)
    return bL ** 2 / (2.0 * math.pi * length ** 2) * math.sqrt(E * I / mass_per_length)


def uniform_beam_mode_shape(
    x: Sequence[float], length: float, mode: int = 1, normalize: bool = True
) -> np.ndarray:
    """Exact free-free mode shape ``phi(x)`` of a uniform beam.

    ``phi(x) = cos(bx) + cosh(bx) - sigma (sin(bx) + sinh(bx))`` with
    ``sigma = (cosh(bL) - cos(bL)) / (sinh(bL) - sin(bL))``.
    """
    bL = free_free_beam_eigenvalue(mode)
    b = bL / length
    sigma = (math.cosh(bL) - math.cos(bL)) / (math.sinh(bL) - math.sin(bL))
    xa = np.asarray(x, dtype=float)
    phi = (np.cos(b * xa) + np.cosh(b * xa)) - sigma * (np.sin(b * xa) + np.sinh(b * xa))
    if normalize:
        peak = np.max(np.abs(phi))
        if peak > 0:
            phi = phi / peak
    return phi


def analytic_bracket(
    E: float,
    I_sections: Sequence[float],
    mass_per_length: Sequence[float],
    length: float,
    mode: int = 1,
) -> Tuple[float, float]:
    """Rigorous lower/upper frequency bracket for a *non-uniform* beam.

    The free-free eigenvalue is monotone in the (positive) bending rigidity
    field: replacing ``EI(x)`` by the constant fields ``EI_min`` / ``EI_max``
    gives two uniform beams whose exact frequencies bracket every admissible
    ``EI(x)`` between them (mass distribution held at its mean).  Any credible
    non-uniform solver — including :func:`hull_girder_modal` — must land inside
    the bracket, which makes it a hard, reference-free judge.
    """
    I = np.asarray(I_sections, dtype=float)
    m = np.asarray(mass_per_length, dtype=float)
    if np.any(I <= 0) or np.any(m <= 0):
        raise ValueError("I and mass_per_length must be positive")
    m_mean = float(np.mean(m))
    lo = uniform_beam_frequency(E, float(I.min()), m_mean, length, mode)
    hi = uniform_beam_frequency(E, float(I.max()), m_mean, length, mode)
    return lo, hi


# ---------------------------------------------------------------------------
# Finite-element free-free beam (piecewise EI, piecewise mass)
# ---------------------------------------------------------------------------

def _element_matrices(EI: float, m: float, h: float) -> Tuple[np.ndarray, np.ndarray]:
    """Cubic Hermite Euler-Bernoulli element (dofs: w1, th1, w2, th2)."""
    Ke = EI / h ** 3 * np.array(
        [[12.0, 6.0 * h, -12.0, 6.0 * h],
         [6.0 * h, 4.0 * h * h, -6.0 * h, 2.0 * h * h],
         [-12.0, -6.0 * h, 12.0, -6.0 * h],
         [6.0 * h, 2.0 * h * h, -6.0 * h, 4.0 * h * h]]
    )
    Me = m * h / 420.0 * np.array(
        [[156.0, 22.0 * h, 54.0, -13.0 * h],
         [22.0 * h, 4.0 * h * h, 13.0 * h, -3.0 * h * h],
         [54.0, 13.0 * h, 156.0, -22.0 * h],
         [-13.0 * h, -3.0 * h * h, -22.0 * h, 4.0 * h * h]]
    )
    return Ke, Me



def _reduced_eigh(K: np.ndarray, M: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Generalised symmetric eigenproblem ``K phi = lam M phi`` with numpy only.

    ``M`` is symmetric positive definite (consistent Hermite mass matrix), so
    Cholesky reduces the pencil to the standard form ``L^-1 K L^-T y = lam y``
    and ``np.linalg.eigh`` solves it; ``phi = L^-T y``.

    Note: ``scipy.linalg.eigh(K, M)`` was used first and **segfaults**
    deterministically in this image from ``ndof = 42`` upwards (exit 139, the
    generalised driver never returns), so the numeric core deliberately depends
    on numpy alone — one fewer native LAPACK path to trust, and the module stays
    importable in environments without scipy.
    """
    L = np.linalg.cholesky(M)
    Li = np.linalg.inv(L)
    lam, y = np.linalg.eigh(Li @ K @ Li.T)
    vec = Li.T @ y
    # normalise so that phi^T M phi = 1 (mode shapes are re-normalised to unit
    # peak by the caller, this keeps the raw eigenvectors well conditioned)
    for j in range(vec.shape[1]):
        nrm = float(np.sqrt(vec[:, j] @ M @ vec[:, j]))
        if nrm > 0:
            vec[:, j] /= nrm
    return lam, vec


def hull_girder_modal(
    E: float,
    I_sections: Sequence[float],
    mass_per_length: Sequence[float],
    length: float,
    n_elastic_modes: int = 4,
) -> Dict[str, object]:
    """Free-free hull-girder eigenproblem on a piecewise-uniform beam.

    Parameters
    ----------
    E : Young's modulus [Pa] (steel 2.06e11).
    I_sections : section second moment about the transverse axis [m^4] at the
        ``n_nodes`` equally spaced stations (``I_sections[0]`` stern).
    mass_per_length : effective vibrating mass per unit length [kg/m],
        entrained water included.
    length : L [m].
    n_elastic_modes : number of elastic modes returned (rigid-body modes are
        discarded from the output but are part of the eigenproblem).

    Returns
    -------
    dict with ``frequencies`` [Hz], ``mode_shapes`` (unit-peak ``w`` at the
    stations), ``stations`` [m], ``n_elements`` and ``rigid_body_frequencies``
    (should be ~0; a large value is a discretisation/BC bug, so it is exposed
    for the verification harness rather than hidden).
    """
    I = np.asarray(I_sections, dtype=float).ravel()
    m = np.asarray(mass_per_length, dtype=float).ravel()
    if I.size != m.size:
        raise ValueError("I_sections and mass_per_length must have equal length")
    if I.size < 3:
        raise ValueError("at least 3 stations are required")
    if np.any(I <= 0) or np.any(m <= 0):
        raise ValueError("I and mass_per_length must be positive")
    if length <= 0 or E <= 0:
        raise ValueError("length and E must be positive")

    n_nodes = I.size
    n_el = n_nodes - 1
    h = length / n_el
    ndof = 2 * n_nodes
    K = np.zeros((ndof, ndof))
    M = np.zeros((ndof, ndof))

    for e in range(n_el):
        EI_e = 0.5 * (E * I[e] + E * I[e + 1])
        m_e = 0.5 * (m[e] + m[e + 1])
        Ke, Me = _element_matrices(EI_e, m_e, h)
        dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        for a in range(4):
            for b in range(4):
                K[dofs[a], dofs[b]] += Ke[a, b]
                M[dofs[a], dofs[b]] += Me[a, b]

    lam, vec = _reduced_eigh(K, M)
    eig = np.sqrt(np.clip(lam, 0.0, None)) / (2.0 * math.pi)

    stations = np.linspace(0.0, length, n_nodes)
    shapes: List[np.ndarray] = []
    freqs: List[float] = []
    for k in range(2, min(2 + n_elastic_modes, ndof)):
        w = vec[0::2, k]
        peak = float(np.max(np.abs(w)))
        shapes.append(w / peak if peak > 0 else w)
        freqs.append(float(eig[k]))

    return {
        "frequencies": np.array(freqs),
        "mode_shapes": np.array(shapes),
        "stations": stations,
        "n_elements": n_el,
        "element_length": h,
        "rigid_body_frequencies": eig[:2],
    }


def nodal_points(x: Sequence[float], shape: Sequence[float]) -> List[float]:
    """Interior zero crossings of a mode shape (nodal lines), by linear search.

    Used to compare a numerical mode shape with the exact 2-node locations of a
    uniform free-free beam (``0.2242 L`` and ``0.7758 L``) — a shape-level check
    that is independent of the frequency.
    """
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(shape, dtype=float)
    out: List[float] = []
    for i in range(len(ya) - 1):
        a, b = ya[i], ya[i + 1]
        if a == 0.0:
            out.append(float(xa[i]))
        elif a * b < 0.0:
            out.append(float(xa[i] - a * (xa[i + 1] - xa[i]) / (b - a)))
    return out


# ---------------------------------------------------------------------------
# Mass distribution helper
# ---------------------------------------------------------------------------

def effective_mass_per_length(
    displacement_tonnes: float,
    length: float,
    n_stations: int,
    added_mass_factor: float = 0.25,
    distribution: Optional[Sequence[float]] = None,
) -> np.ndarray:
    """Effective vibrating mass per unit length [kg/m].

    ``m(x) = (1 + added_mass_factor) * rho(x)`` where the dry hull+outfit mass
    is the displacement spread over ``length``.  ``distribution`` optionally
    supplies a non-dimensional shape (normalised to mean 1), e.g. a fuller aft
    body.
    """
    if displacement_tonnes <= 0 or length <= 0 or n_stations < 2:
        raise ValueError("bad inputs")
    if added_mass_factor < 0:
        raise ValueError("added_mass_factor must be >= 0")
    base = displacement_tonnes * 1000.0 / length  # kg/m
    if distribution is None:
        return np.full(n_stations, base * (1.0 + added_mass_factor))
    shape = np.asarray(distribution, dtype=float).ravel()
    if shape.size != n_stations or np.any(shape <= 0):
        raise ValueError("distribution must be positive with n_stations entries")
    shape = shape / shape.mean()
    return base * shape * (1.0 + added_mass_factor)


# ---------------------------------------------------------------------------
# Excitation and assessment
# ---------------------------------------------------------------------------

def blade_rate_frequency(rpm: float, n_blades: int) -> float:
    """Blade-passing (blade-rate) frequency [Hz] = (rpm/60) * Z."""
    if rpm <= 0 or n_blades < 1:
        raise ValueError("rpm must be > 0 and n_blades >= 1")
    return rpm / 60.0 * n_blades


def resonance_separation(
    f_natural: float,
    f_excitation: float,
    lower_margin: float = 0.15,
    upper_margin: float = 0.10,
    label: str = "",
) -> Dict[str, object]:
    """Coincidence check between a hull natural frequency and an excitation.

    Engineering criterion (DNV-RP-C103 practice): the natural frequency should
    lie at least ``lower_margin`` (15 %) *below* the excitation frequency, or at
    least ``upper_margin`` (10 %) *above* it.  The margins are parameters so a
    stricter in-house rule can be applied without touching this code.
    """
    if f_natural <= 0 or f_excitation <= 0:
        raise ValueError("frequencies must be positive")
    ratio = f_natural / f_excitation
    lo = 1.0 - lower_margin
    hi = 1.0 + upper_margin
    if ratio <= lo:
        verdict = "OK_BELOW"
        margin = lo - ratio
    elif ratio >= hi:
        verdict = "OK_ABOVE"
        margin = ratio - hi
    else:
        verdict = "COINCIDENCE"
        margin = -min(ratio - lo, hi - ratio)
    return {
        "label": label or f"f_nat/f_exc={ratio:.3f}",
        "f_natural": f_natural,
        "f_excitation": f_excitation,
        "ratio": ratio,
        "verdict": verdict,
        "margin": margin,
        "band": (f_excitation * lo, f_excitation * hi),
    }


def sdof_amplification(f_excitation: float, f_natural: float, damping: float) -> float:
    """Exact steady-state dynamic magnifier of a damped SDOF system.

    ``|H| = 1 / sqrt((1 - r^2)^2 + (2 zeta r)^2)``, ``r = f_exc / f_nat``.
    """
    if f_natural <= 0 or f_excitation < 0:
        raise ValueError("bad frequencies")
    if not 0.0 < damping < 1.0:
        raise ValueError("damping must be in (0, 1)")
    r = f_excitation / f_natural
    return 1.0 / math.sqrt((1.0 - r * r) ** 2 + (2.0 * damping * r) ** 2)


def velocity_limit_check(v_rms_mm_s: float, limit_mm_s: float) -> Dict[str, object]:
    """Compare a frequency-weighted RMS velocity with a caller-supplied limit.

    The limit is *not* hard-coded here on purpose (see module docstring): pass
    the value from the applicable rule (e.g. an ISO 6954:2000 comfort category)
    so the norm's digits live with the norm-based caller, not in a physics
    kernel where they cannot be traced.
    """
    if limit_mm_s <= 0 or v_rms_mm_s < 0:
        raise ValueError("limit must be > 0 and velocity >= 0")
    ratio = v_rms_mm_s / limit_mm_s
    return {
        "v_rms_mm_s": v_rms_mm_s,
        "limit_mm_s": limit_mm_s,
        "utilisation": ratio,
        "margin_mm_s": limit_mm_s - v_rms_mm_s,
        "verdict": "PASS" if ratio <= 1.0 else "EXCEED",
    }


def assess_hull_vibration(
    E: float,
    I_sections: Sequence[float],
    displacement_tonnes: float,
    length: float,
    design_rpm: float,
    n_blades: int,
    rpm_harmonics: Iterable[int] = (1, 2, 3, 4),
    n_modes: int = 6,
    added_mass_factor: float = 0.25,
    damping: float = 0.015,
) -> Dict[str, object]:
    """End-to-end hull-girder screening for one ship.

    Computes the lowest ``n_modes`` elastic free-free bending frequencies of the
    non-uniform hull girder, then screens **every** mode against the blade-rate
    family ``k * Z * n`` (k = 1..4 by default).  For a slow-speed two-stroke the
    blade rate itself (6-9 Hz) sits far above the 2-node mode, so coincidence is
    normally a *higher-mode* problem — screening only mode 1 would report a
    vacuous "OK".

    ``damping`` ~ 0.015 (1.5 % critical) is a typical welded-steel hull-girder
    value; 0.005-0.03 is the usual band.  The amplification is the exact damped
    SDOF magnifier, quoted so the user can see what a coincidence would cost.
    """
    n_st = len(I_sections)
    m = effective_mass_per_length(
        displacement_tonnes, length, n_st, added_mass_factor=added_mass_factor
    )
    modal = hull_girder_modal(E, I_sections, m, length, n_elastic_modes=n_modes)
    freqs = [float(f) for f in modal["frequencies"]]
    f_bpf = blade_rate_frequency(design_rpm, n_blades)
    checks = []
    for i, f_nat in enumerate(freqs, start=1):
        for k in rpm_harmonics:
            checks.append(
                resonance_separation(
                    f_nat, f_bpf * k, label=f"mode{i} {f_nat:.2f} Hz vs {k}x BPF {k*f_bpf:.2f} Hz"
                )
            )
    worst = min(checks, key=lambda c: c["margin"]) if checks else None
    n_coincident = sum(1 for c in checks if c["verdict"] == "COINCIDENCE")
    return {
        "frequencies": freqs,
        "f_2node_vertical": freqs[0],
        "nodal_points": nodal_points(modal["stations"], modal["mode_shapes"][0]),
        "f_blade_rate": f_bpf,
        "resonance_checks": checks,
        "n_coincident": n_coincident,
        "worst": worst,
        "resonant_amplification": sdof_amplification(worst["f_excitation"], worst["f_natural"], damping)
        if worst
        else None,
        "modal": modal,
    }
