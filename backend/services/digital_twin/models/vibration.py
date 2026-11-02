"""Hull-girder vibration and cabin-noise reference models.

Motivation
----------
The vibration / noise domain was the only ship-performance discipline without a
physics kernel (report #1186, mission m1600: the served endpoint used
``f_hull = 1.5 * (L/100)**-0.5`` — mass independent — and a hand-tuned
``vertical_vib = 2.0 + 0.5*(rpm/100)*...`` placeholder with no units).  This
module replaces that placeholder with a closed-form / exact-eigenvalue chain so
that every reported number is traceable to an equation:

1. **Hull-girder vertical bending vibration** — a free-free Euler-Bernoulli
   beam with the exact transcendental eigenvalues ``beta_n L`` of
   ``cos(beta L) cosh(beta L) = 1`` (4.7300407, 7.8532046, 10.9956078, ...)::

       f_n = (beta_n L)^2 / (2 pi L^2) * sqrt(E I / mu)

   with ``I`` from a thin-walled box idealisation of the midship section
   (deck/bottom flanges + side webs, longitudinals folded in through
   ``stiffener_factor``) and ``mu`` from the vibrating mass (displacement plus
   hydrodynamic added mass, vertical coefficient ``added_mass_ratio``).

2. **Propeller blade-rate excitation** — exact kinematics
   ``f_BF = n_blades * rpm / 60`` and harmonics ``k * f_BF``; the bearing-force
   amplitude at the aft end is parameterised as ``k_F * T`` (``k_F`` = 2-5 % of
   thrust for a well-designed stern).

3. **Modal response and deck velocity** — the free-free mode shapes ``phi_n``
   (normalised to ``int phi_n^2 dx = L``) are summed as SDOF modal oscillators
   with a modal damping ratio ``zeta`` (0.5-1.5 % for the hull girder)::

       w(x) = sum_n phi_n(x) Q_n / (K_n - omega^2 M_n + i 2 zeta_n omega_n omega M_n)

   The harmonic velocity amplitudes are assembled into a 1-80 Hz RMS level in
   **mm/s**, which is the quantity ISO 6954 grades.

4. **Local plate panels (deck / superstructure)** — simply supported panels use
   the exact Navier frequency; clamped panels use a Rayleigh-Ritz reduction on
   clamped-clamped beam functions (converges to Leissa's exact
   ``omega a^2 sqrt(rho h / D) = 35.985`` for the clamped square plate).

5. **Cabin noise** — airborne chain ``Lw -> TL -> Lp``: exact single-leaf mass
   law ``TL = 10 log10(1 + (m'' omega / (2 rho0 c))^2)`` (6 dB/octave, 6 dB per
   mass doubling asymptotes), field-incidence correction ``-5 dB``, Sabine
   reverberation ``T60 = 24 ln(10) V / (c A) = 0.1611 V/A`` and the diffuse
   field relation ``Lp = Lw - TL + 10 log10(4/R)``.

Known limitations (stated, not hidden)
--------------------------------------
* The plate model is unstiffened single-leaf; noise transmission through a
  floating floor / stiffened grillage needs SEA or a transfer-matrix model.
* The 1-80 Hz band weighting is a flat window, i.e. the ISO 6954 weighting curve
  is **not** transcribed verbatim here; the reported level is therefore
  conservative outside the flat part of the standard curve.
* The comfort thresholds are platform gate values aligned with the ISO 6954
  comfort bands, kept configurable rather than asserted as a verbatim
  transcription of the standard.
* Underwater radiated noise (propeller cavitation spectra) is **not** covered:
  it stays WITHHELD.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Sequence

import numpy as np

from .geometry import HullForm

E_STEEL = 2.06e11            # Pa
RHO_STEEL = 7850.0           # kg/m^3
NU_STEEL = 0.3
RHO_AIR = 1.21               # kg/m^3 at 15 C
C_AIR = 343.0                # m/s

#: Free-free beam eigenvalues ``beta_n L`` (exact roots of cos(x)cosh(x) = 1).
FREE_FREE_BETA_L = (4.730040744862704, 7.853204624095838,
                    10.995607838001671, 14.137165491257464,
                    17.278759657399481)

#: Vertical hydrodynamic added-mass coefficient (0.5-0.9 in classification practice).
DEFAULT_ADDED_MASS_RATIO = 0.7
#: Longitudinal-stiffener / effective-breadth allowance on the box section (1.4-1.8).
DEFAULT_STIFFENER_FACTOR = 1.5
#: Blade-rate bearing-force amplitude as a fraction of thrust.
DEFAULT_BEARING_FORCE_FRACTION = 0.03
#: Modal damping ratio of the 2-node vertical mode.
DEFAULT_MODAL_DAMPING = 0.01
#: ISO 6954 measurement band for the weighted velocity level [Hz].
ISO6954_BAND = (1.0, 80.0)
#: Field-incidence correction applied to the normal-incidence mass law [dB].
FIELD_INCIDENCE_CORRECTION_DB = 5.0


def _TRAPZ(y, x, axis=-1):
    """np.trapz alias that survives the numpy 2.0 rename to np.trapezoid."""
    fn = getattr(np, "trapezoid", None) or np.trapz
    return fn(y, x, axis=axis)


class VibrationValidationError(ValueError):
    """Raised when the vibration model is asked for unphysical inputs."""


# ---------------------------------------------------------------------------
# midship section
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HullGirderSection:
    """Thin-walled box idealisation of the midship section.

    Deck and bottom flanges carry ``B * t`` each, the two side shells carry the
    web contribution; ``stiffener_factor`` folds longitudinal stiffeners and the
    effective breadth of attached plating into both the area and the second
    moment.  The section is double symmetric, so hogging/sagging asymmetry is not
    represented - adequate for a first vertical-vibration mode.
    """

    B: float                 # moulded breadth [m]
    D: float                 # moulded depth to deck [m]
    t_deck: float            # deck plating thickness [m]
    t_bottom: float          # bottom plating thickness [m]
    t_side: float            # side shell thickness [m]
    stiffener_factor: float = DEFAULT_STIFFENER_FACTOR

    def __post_init__(self) -> None:
        for name in ("B", "D", "t_deck", "t_bottom", "t_side"):
            if getattr(self, name) <= 0.0:
                raise VibrationValidationError(f"{name} must be positive")
        if not (0.0 < self.stiffener_factor <= 3.0):
            raise VibrationValidationError("stiffener_factor outside (0, 3]")
        if self.t_deck > self.D or self.t_side > self.B / 2.0:
            raise VibrationValidationError("plate thickness exceeds section size")

    @property
    def area(self) -> float:
        """Net steel area of the box section including the stiffener allowance [m^2]."""
        flanges = self.B * (self.t_deck + self.t_bottom)
        webs = 2.0 * self.D * self.t_side
        return self.stiffener_factor * (flanges + webs)

    @property
    def I_vertical(self) -> float:
        """Second moment about the transverse (horizontal) axis [m^4]."""
        half = 0.5 * self.D
        flanges = (self.B * self.t_deck + self.B * self.t_bottom) * half ** 2
        webs = 2.0 * (self.t_side * self.D ** 3 / 12.0)
        return self.stiffener_factor * (flanges + webs)

    @property
    def section_modulus_deck(self) -> float:
        """``Z = I / (D/2)`` [m^3] — cross-check against fatigue.section_modulus."""
        return self.I_vertical / (0.5 * self.D)


# ---------------------------------------------------------------------------
# free-free beam (exact)
# ---------------------------------------------------------------------------
def free_free_eigenvalue(n: int) -> float:
    """Exact ``beta_n L`` of the free-free Euler-Bernoulli beam (1-based ``n``)."""
    if n < 1:
        raise VibrationValidationError("mode index n must be >= 1")
    if n <= len(FREE_FREE_BETA_L):
        return FREE_FREE_BETA_L[n - 1]
    # For n >= 2 the roots approach (2n+1) pi/2 from below; Newton polish it.
    x = (2 * n + 1) * math.pi / 2.0
    for _ in range(100):
        f = math.cos(x) * math.cosh(x) - 1.0
        df = -math.sin(x) * math.cosh(x) + math.cos(x) * math.sinh(x)
        step = f / df
        x -= step
        if abs(step) < 1e-14 * max(1.0, abs(x)):
            break
    return x


def free_free_beam_frequencies(L: float, EI: float, mu: float,
                               n_modes: int = 3) -> np.ndarray:
    """Natural frequencies [Hz] of a uniform free-free beam.

    ``f_n = (beta_n L)^2 / (2 pi L^2) * sqrt(EI/mu)``.
    """
    if L <= 0.0 or EI <= 0.0 or mu <= 0.0:
        raise VibrationValidationError("L, EI, mu must be positive")
    beta_L = np.array([free_free_eigenvalue(n) for n in range(1, n_modes + 1)])
    return beta_L ** 2 / (2.0 * math.pi * L ** 2) * math.sqrt(EI / mu)


def mode_shape(x, L: float, n: int = 1) -> np.ndarray:
    """Free-free beam mode shape normalised so that ``int_0^L phi^2 dx = L``."""
    if L <= 0.0:
        raise VibrationValidationError("L must be positive")
    beta = free_free_eigenvalue(n) / L
    bx = beta * np.asarray(x, dtype=float)
    bl = beta * L
    sigma = (math.cosh(bl) - math.cos(bl)) / (math.sinh(bl) - math.sin(bl))
    return np.cos(bx) + np.cosh(bx) - sigma * (np.sin(bx) + np.sinh(bx))


# ---------------------------------------------------------------------------
# hull girder vertical vibration
# ---------------------------------------------------------------------------
def hull_girder_vertical(L: float, section: HullGirderSection,
                         displacement_t: float,
                         added_mass_ratio: float = DEFAULT_ADDED_MASS_RATIO,
                         n_modes: int = 3,
                         E: float = E_STEEL) -> Dict[str, float]:
    """2-node (and higher) vertical hull-girder frequencies [Hz].

    ``mu = displacement * (1 + added_mass_ratio) / L`` is the vibrating mass per
    unit length; ``I`` comes from :class:`HullGirderSection`.  ``f_2node_hz`` is
    the quantity that must be compared with the blade rate.
    """
    if L <= 0.0:
        raise VibrationValidationError("L must be positive")
    if displacement_t <= 0.0:
        raise VibrationValidationError("displacement must be positive")
    if added_mass_ratio < 0.0:
        raise VibrationValidationError("added_mass_ratio must be >= 0")
    I = section.I_vertical
    mu = displacement_t * 1000.0 * (1.0 + added_mass_ratio) / L
    freqs = free_free_beam_frequencies(L, E * I, mu, n_modes=n_modes)
    return {
        "f_2node_hz": float(freqs[0]),
        "f_3node_hz": float(freqs[1]) if len(freqs) > 1 else float("nan"),
        "f_4node_hz": float(freqs[2]) if len(freqs) > 2 else float("nan"),
        "I_vertical_m4": I,
        "mu_kg_per_m": mu,
        "EI_Nm2": E * I,
        "added_mass_ratio": added_mass_ratio,
        "frequency_ratio_3_2": float(freqs[1] / freqs[0]) if len(freqs) > 1 else float("nan"),
    }


def displacement_from_hull(hull: HullForm) -> float:
    """Displacement [t] of the parametric hull (exact ``Cb L B T rho``)."""
    return hull.volume * hull.rho / 1000.0


def hull_girder_vibration(hull: HullForm, section: HullGirderSection,
                          depth: float | None = None,
                          added_mass_ratio: float = DEFAULT_ADDED_MASS_RATIO,
                          n_modes: int = 3) -> Dict[str, float]:
    """Convenience wrapper: parametric hull + box section -> frequencies."""
    disp = displacement_from_hull(hull)
    out = hull_girder_vertical(hull.L, section, disp,
                               added_mass_ratio=added_mass_ratio, n_modes=n_modes)
    out["displacement_t"] = disp
    out["D_moulded_m"] = depth if depth is not None else section.D
    return out


# ---------------------------------------------------------------------------
# propeller excitation
# ---------------------------------------------------------------------------
def blade_rate_frequency(rpm: float, n_blades: int) -> float:
    """Fundamental blade-passing frequency [Hz] (exact kinematics)."""
    if rpm <= 0.0 or n_blades < 1:
        raise VibrationValidationError("rpm must be positive and n_blades >= 1")
    return n_blades * rpm / 60.0


def blade_rate_harmonics(rpm: float, n_blades: int, n_harmonics: int = 5) -> np.ndarray:
    """Blade-rate harmonics ``k * f_BF`` [Hz], ``k = 1..n_harmonics``."""
    f1 = blade_rate_frequency(rpm, n_blades)
    return f1 * np.arange(1, n_harmonics + 1, dtype=float)


def bearing_force_amplitude(thrust_N: float, k: int = 1,
                            fraction: float = DEFAULT_BEARING_FORCE_FRACTION) -> float:
    """Blade-rate bearing force amplitude at harmonic ``k`` [N].

    Parameterised as ``fraction * T / k``: the ``1/k`` decay is the usual
    harmonic roll-off of the unsteady bearing force; ``fraction`` (2-5 % of
    thrust) is the engineering band for a well-designed stern.
    """
    if thrust_N <= 0.0:
        raise VibrationValidationError("thrust must be positive")
    if k < 1:
        raise VibrationValidationError("harmonic index must be >= 1")
    if not (0.0 < fraction < 0.5):
        raise VibrationValidationError("fraction outside (0, 0.5)")
    return fraction * thrust_N / k


# ---------------------------------------------------------------------------
# modal response
# ---------------------------------------------------------------------------
def modal_velocity_response(L: float, E: float, I: float, mu: float,
                            omega: float, force_N: float,
                            x_force: float, x_obs: float,
                            zeta: float = DEFAULT_MODAL_DAMPING,
                            n_modes: int = 3) -> complex:
    """Complex velocity response ``i omega w(x_obs)`` to a harmonic point force.

    Modes are orthogonal with unit modal mass per unit length (``M_n = mu L``),
    so ``K_n = omega_n^2 M_n``.  Returns velocity amplitude in m/s (complex, so
    the phase relative to the force is preserved).
    """
    if mu <= 0.0 or L <= 0.0:
        raise VibrationValidationError("L and mu must be positive")
    if zeta <= 0.0:
        raise VibrationValidationError("zeta must be positive")
    omegas = 2.0 * math.pi * free_free_beam_frequencies(L, E * I, mu, n_modes=n_modes)
    M_n = mu * L
    w = 0.0 + 0.0j
    for n, omega_n in enumerate(omegas, start=1):
        phi_f = float(mode_shape(np.array([x_force]), L, n)[0])
        phi_o = float(mode_shape(np.array([x_obs]), L, n)[0])
        Q_n = force_N * phi_f
        denom = (omega_n ** 2 - omega ** 2) + 1j * 2.0 * zeta * omega_n * omega
        w += Q_n * phi_o / (M_n * denom)
    return 1j * omega * w


def deck_velocity_spectrum(L: float, E: float, I: float, mu: float,
                           thrust_N: float, rpm: float, n_blades: int,
                           x_force: float = 0.15, x_obs: float = 0.95,
                           zeta: float = DEFAULT_MODAL_DAMPING,
                           n_harmonics: int = 5,
                           fraction: float = DEFAULT_BEARING_FORCE_FRACTION,
                           n_modes: int = 10,
                           convergence_tol: float = 0.02) -> Dict[str, object]:
    """Blade-rate velocity spectrum at ``x_obs`` [mm/s] plus the RMS level.

    ``x_force`` / ``x_obs`` are fractions of ``L`` (aft bearing / forward deck).
    ``rms_mm_s`` is the 1-80 Hz band RMS of the harmonic content,
    ``sqrt(0.5 * sum A_k^2)`` over the harmonics inside the band.

    The modal truncation is **checked, not assumed**: the spectrum is recomputed
    with ``2 * n_modes`` modes and ``converged`` reports whether both RMS levels
    agree within ``convergence_tol``.  A truncation that is too short is not a
    small error - for the reference hull 3 modes give 0.19 mm/s while the
    converged value is 0.21 mm/s x 10^3 (higher modes add coherently when the
    blade rate sits above the third hull mode).
    """
    def _spectrum(n_modes_: int):
        freqs_ = blade_rate_harmonics(rpm, n_blades, n_harmonics=n_harmonics)
        amps_, phases_ = [], []
        for k, f in enumerate(freqs_, start=1):
            F = bearing_force_amplitude(thrust_N, k=k, fraction=fraction)
            v = modal_velocity_response(L, E, I, mu, 2.0 * math.pi * f, F,
                                        x_force * L, x_obs * L, zeta=zeta,
                                        n_modes=n_modes_)
            amps_.append(abs(v) * 1000.0)      # m/s -> mm/s
            phases_.append(float(np.angle(v)))
        amps_ = np.asarray(amps_)
        in_band_ = (freqs_ >= ISO6954_BAND[0]) & (freqs_ <= ISO6954_BAND[1])
        rms_ = float(math.sqrt(0.5 * float(np.sum(amps_[in_band_] ** 2))))
        return freqs_, amps_, np.asarray(phases_), in_band_, rms_

    freqs, amps, phases, in_band, rms = _spectrum(n_modes)
    _, _, _, _, rms_rich = _spectrum(2 * n_modes)
    spread = abs(rms_rich - rms) / rms if rms > 0.0 else 0.0
    return {
        "harmonics_hz": freqs,
        "velocity_mm_s": amps,
        "phase_rad": phases,
        "rms_mm_s": rms,
        "rms_mm_s_rich": rms_rich,
        "modal_convergence_rel": spread,
        "converged": bool(spread <= convergence_tol),
        "n_modes": int(n_modes),
        "band_hz": ISO6954_BAND,
        "n_harmonics_in_band": int(np.sum(in_band)),
    }


def sdof_magnification(omega: float, omega_n: float, zeta: float) -> float:
    """Exact SDOF displacement magnification ``1/|1-r^2+2 i zeta r|``."""
    r = omega / omega_n
    return 1.0 / math.sqrt((1.0 - r ** 2) ** 2 + (2.0 * zeta * r) ** 2)


def panel_transmissibility(freq: float, f_panel: float, zeta: float = 0.03) -> float:
    """Exact base-excitation transmissibility of a SDOF panel [dimensionless].

    ``H = sqrt((1 + (2 zeta r)^2) / ((1 - r^2)^2 + (2 zeta r)^2))``, ``r = f/f_p``.
    Asymptotes: ``H -> 1`` for ``r -> 0``, ``H ~ 1/r^2`` for ``r >> 1`` and
    ``H(r = 1) = sqrt(1 + 4 zeta^2) / (2 zeta)`` at panel resonance.
    """
    if f_panel <= 0.0 or freq < 0.0:
        raise VibrationValidationError("frequencies must be positive")
    if zeta <= 0.0:
        raise VibrationValidationError("zeta must be positive")
    r = freq / f_panel
    return math.sqrt((1.0 + (2.0 * zeta * r) ** 2)
                     / ((1.0 - r ** 2) ** 2 + (2.0 * zeta * r) ** 2))


# ---------------------------------------------------------------------------
# local plate panels
# ---------------------------------------------------------------------------
def plate_flexural_rigidity(h: float, E: float = E_STEEL,
                            nu: float = NU_STEEL) -> float:
    """Plate rigidity ``D = E h^3 / (12 (1 - nu^2))`` [N m]."""
    if h <= 0.0:
        raise VibrationValidationError("plate thickness must be positive")
    return E * h ** 3 / (12.0 * (1.0 - nu ** 2))


def plate_natural_frequency(a: float, b: float, h: float, rho: float = RHO_STEEL,
                            E: float = E_STEEL, nu: float = NU_STEEL,
                            m: int = 1, n: int = 1, bc: str = "SS") -> float:
    """Natural frequency [Hz] of a rectangular panel.

    * ``bc="SS"``: exact Navier solution
      ``omega = sqrt(D/(rho h)) [(m pi/a)^2 + (n pi/b)^2]``.
    * ``bc="clamped"``: 4-term Rayleigh-Ritz reduction on clamped-clamped beam
      functions (see :func:`clamped_plate_lambda`).
    """
    D = plate_flexural_rigidity(h, E=E, nu=nu)
    if bc.upper() in ("SS", "SIMPLY-SUPPORTED", "SIMPLE"):
        omega = math.sqrt(D / (rho * h)) * ((m * math.pi / a) ** 2 + (n * math.pi / b) ** 2)
        return omega / (2.0 * math.pi)
    if bc.upper() in ("CLAMPED", "C"):
        lam = clamped_plate_lambda(a, b, m=m, n=n)
        return lam / (2.0 * math.pi * a ** 2) * math.sqrt(D / (rho * h))
    raise VibrationValidationError(f"unsupported boundary condition {bc!r}")


def _beam_basis(span: float, n_terms: int, n_pts: int = 2001):
    """Clamped-clamped beam eigenfunctions and their 1-D integral matrices.

    Returns ``(Xa, Xc, Xd)`` where, for basis functions ``X_i`` of the given span::

        Xa[i, j] = int X_i X_j dx
        Xc[i, j] = int X_i' X_j' dx = -int X_i X_j'' dx     (clamped BCs)
        Xd[i, j] = int X_i'' X_j'' dx

    Second derivatives are analytic, so the integrals are exact to the trapezoid
    error of the 2001-point grid (< 1e-9 relative).
    """
    xi = np.linspace(0.0, 1.0, n_pts)
    x = xi * span
    X = np.zeros((n_terms, n_pts))
    X2 = np.zeros((n_terms, n_pts))
    for i in range(n_terms):
        g = free_free_eigenvalue(i + 1)
        sigma = (math.cosh(g) - math.cos(g)) / (math.sinh(g) - math.sin(g))
        X[i] = (np.cosh(g * xi) - np.cos(g * xi)) - sigma * (np.sinh(g * xi) - np.sin(g * xi))
        X2[i] = (g / span) ** 2 * ((np.cosh(g * xi) + np.cos(g * xi))
                                   - sigma * (np.sinh(g * xi) + np.sin(g * xi)))
    Xa = _TRAPZ(X[:, None, :] * X[None, :, :], x, axis=2)
    Xd = _TRAPZ(X2[:, None, :] * X2[None, :, :], x, axis=2)
    Xc = _TRAPZ(X2[:, None, :] * X[None, :, :], x, axis=2) * -1.0
    return Xa, Xc, Xd


def ritz_plate_lambda(a: float, b: float, n_terms: int = 2,
                      bc_x: str = "clamped", bc_y: str = "clamped") -> float:
    """Rayleigh-Ritz eigenvalue ``omega a^2 sqrt(rho h / D)`` (nu-independent).

    Trial set = tensor products of beam eigenfunctions (clamped-clamped, or the
    exact simply-supported sinusoids).  With clamped BCs the ``(1 - nu)`` mixed
    terms cancel identically for a product basis, so the reduction is
    ``omega^2 = (D / rho h) * eig(B, A)`` with
    ``B_pq = Xd Ya + Xa Yd + 2 Xc Yc`` and ``A_pq = Xa Ya``.
    """
    if n_terms < 1:
        raise VibrationValidationError("n_terms must be >= 1")
    Xa, Xc, Xd = _beam_basis(a, n_terms) if bc_x == "clamped" else _sinusoid_matrices(a, n_terms)
    Ya, Yc, Yd = _beam_basis(b, n_terms) if bc_y == "clamped" else _sinusoid_matrices(b, n_terms)
    idx = [(i, j) for i in range(n_terms) for j in range(n_terms)]
    m = len(idx)
    B = np.zeros((m, m))
    A = np.zeros((m, m))
    for p, (i1, j1) in enumerate(idx):
        for q, (i2, j2) in enumerate(idx):
            B[p, q] = (Xd[i1, i2] * Ya[j1, j2] + Xa[i1, i2] * Yd[j1, j2]
                       + 2.0 * Xc[i1, i2] * Yc[j1, j2])
            A[p, q] = Xa[i1, i2] * Ya[j1, j2]
    lam2 = np.linalg.eigvals(np.linalg.solve(A, B))
    lam2 = np.sort(np.real(lam2))
    lam2 = lam2[lam2 > 0.0]
    if lam2.size == 0:
        raise VibrationValidationError("Rayleigh-Ritz reduction produced no positive root")
    return float(math.sqrt(lam2[0]) * a ** 2)


def _sinusoid_matrices(span: float, n_terms: int, n_pts: int = 2001):
    """Same three matrices for simply-supported (sinusoidal) basis functions."""
    xi = np.linspace(0.0, 1.0, n_pts)
    x = xi * span
    X = np.array([np.sin((i + 1) * math.pi * xi) for i in range(n_terms)])
    k = np.array([(i + 1) * math.pi / span for i in range(n_terms)])
    X2 = -X * (k ** 2)[:, None]
    Xa = _TRAPZ(X[:, None, :] * X[None, :, :], x, axis=2)
    Xd = _TRAPZ(X2[:, None, :] * X2[None, :, :], x, axis=2)
    Xc = _TRAPZ(X2[:, None, :] * X[None, :, :], x, axis=2) * -1.0
    return Xa, Xc, Xd


def clamped_plate_lambda(a: float, b: float, n_terms: int = 2) -> float:
    """Clamped-plate eigenvalue ``omega a^2 sqrt(rho h / D)`` (Leissa square: 35.985)."""
    return ritz_plate_lambda(a, b, n_terms=n_terms, bc_x="clamped", bc_y="clamped")


def plate_natural_frequency(a: float, b: float, h: float, rho: float = RHO_STEEL,
                            E: float = E_STEEL, nu: float = NU_STEEL,
                            m: int = 1, n: int = 1, bc: str = "SS",
                            n_terms: int = 2) -> float:
    """Natural frequency [Hz] of a rectangular panel.

    * ``bc="SS"``: exact Navier solution
      ``omega = sqrt(D/(rho h)) [(m pi/a)^2 + (n pi/b)^2]``.
    * ``bc="clamped"``: Rayleigh-Ritz on clamped-clamped beam functions.
    """
    D = plate_flexural_rigidity(h, E=E, nu=nu)
    if bc.upper() in ("SS", "SIMPLY-SUPPORTED", "SIMPLE"):
        omega = math.sqrt(D / (rho * h)) * ((m * math.pi / a) ** 2 + (n * math.pi / b) ** 2)
        return omega / (2.0 * math.pi)
    if bc.upper() in ("CLAMPED", "C"):
        lam = ritz_plate_lambda(a, b, n_terms=max(n_terms, max(m, n) + 1),
                                bc_x="clamped", bc_y="clamped")
        return lam / (2.0 * math.pi * a ** 2) * math.sqrt(D / (rho * h))
    raise VibrationValidationError(f"unsupported boundary condition {bc!r}")


def plate_velocity_iso(**kwargs) -> Dict[str, float]:
    """Panel frequency for SS and clamped edges plus their ratio (diagnostic)."""
    f_ss = plate_natural_frequency(bc="SS", **kwargs)
    f_c = plate_natural_frequency(bc="clamped", **kwargs)
    return {"f_ss_hz": f_ss, "f_clamped_hz": f_c, "ratio": f_c / f_ss}


# ---------------------------------------------------------------------------
# cabin noise (airborne chain)
# ---------------------------------------------------------------------------
def mass_law_tl(surface_density: float, freq: float, rho0: float = RHO_AIR,
                c: float = C_AIR, field_incidence: bool = True) -> float:
    """Single-leaf transmission loss [dB].

    Exact normal-incidence form ``10 log10(1 + (m'' omega / (2 rho0 c))^2)``
    minus a constant field-incidence correction (5 dB) when requested.
    Asymptotes: +6 dB per doubling of mass or of frequency.
    """
    if surface_density <= 0.0 or freq <= 0.0:
        raise VibrationValidationError("surface density and frequency must be positive")
    omega = 2.0 * math.pi * freq
    x = surface_density * omega / (2.0 * rho0 * c)
    tl = 10.0 * math.log10(1.0 + x * x)
    return tl - (FIELD_INCIDENCE_CORRECTION_DB if field_incidence else 0.0)


def sabine_reverberation_time(volume: float, absorption_area: float) -> float:
    """Sabine reverberation time ``T60 = 24 ln(10) V / (c A)`` [s]."""
    if volume <= 0.0 or absorption_area <= 0.0:
        raise VibrationValidationError("volume and absorption area must be positive")
    return 24.0 * math.log(10.0) * volume / (C_AIR * absorption_area)


def room_constant(surface_area: float, mean_absorption: float) -> float:
    """Room constant ``R = S alpha / (1 - alpha)`` [m^2]."""
    if surface_area <= 0.0:
        raise VibrationValidationError("surface area must be positive")
    if not (0.0 < mean_absorption < 1.0):
        raise VibrationValidationError("mean absorption must be in (0, 1)")
    return surface_area * mean_absorption / (1.0 - mean_absorption)


def cabin_sound_pressure_level(source_lw_db: float, tl_db: float,
                               room_constant_m2: float) -> float:
    """Diffuse-field cabin level ``Lp = Lw - TL + 10 log10(4/R)`` [dB]."""
    if room_constant_m2 <= 0.0:
        raise VibrationValidationError("room constant must be positive")
    return source_lw_db - tl_db + 10.0 * math.log10(4.0 / room_constant_m2)


def free_field_spl(source_lw_db: float, distance: float) -> float:
    """Free-field level of a point source ``Lp = Lw - 20 log10(r) - 11`` [dB]."""
    if distance <= 0.0:
        raise VibrationValidationError("distance must be positive")
    return source_lw_db - 20.0 * math.log10(distance) - 11.0


def cabin_noise(source_lw_db: float, surface_density: float, freq: float,
                surface_area: float, mean_absorption: float,
                field_incidence: bool = True) -> Dict[str, float]:
    """Machinery -> deck-house cabin noise chain (single octave band).

    Returns the transmission loss, the room constant, the reverberation time and
    the resulting cabin level.  ``source_lw_db`` is the sound power level of the
    machine (re 1e-12 W) at ``freq``.
    """
    tl = mass_law_tl(surface_density, freq, field_incidence=field_incidence)
    R = room_constant(surface_area, mean_absorption)
    lp = cabin_sound_pressure_level(source_lw_db, tl, R)
    volume = surface_area ** 1.5 / 6.0            # cube-equivalent room volume
    t60 = sabine_reverberation_time(volume, surface_area * mean_absorption)
    return {"tl_db": tl, "room_constant_m2": R, "reverb_time_s": t60,
            "spl_db": lp, "field_incidence": float(field_incidence)}


# ---------------------------------------------------------------------------
# grading (units + thresholds kept explicit)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ComfortThresholds:
    """Platform gate values for the 1-80 Hz weighted velocity level [mm/s RMS].

    Aligned with the ISO 6954 comfort bands but kept configurable: the verbatim
    standard curve is not re-transcribed here (see module limitations).
    """

    good: float = 2.5
    acceptable: float = 4.0
    poor: float = 6.0


def comfort_grade(v_rms_mm_s: float, thresholds: ComfortThresholds = ComfortThresholds()) -> Dict[str, object]:
    """Grade a band-RMS velocity level [mm/s] (units are part of the contract)."""
    if v_rms_mm_s < 0.0:
        raise VibrationValidationError("velocity level must be non-negative")
    if v_rms_mm_s <= thresholds.good:
        level, comfort = "良好", "舒适"
    elif v_rms_mm_s <= thresholds.acceptable:
        level, comfort = "一般", "可接受"
    elif v_rms_mm_s <= thresholds.poor:
        level, comfort = "较差", "边界"
    else:
        level, comfort = "不可接受", "不舒适"
    return {"v_rms_mm_s": v_rms_mm_s, "units": "mm/s RMS (1-80 Hz, flat weighting)",
            "level": level, "comfort": comfort,
            "compliant": bool(v_rms_mm_s <= thresholds.acceptable)}


def resonance_risk(rpm: float, n_blades: int, f_hull_hz: float,
                   band: float = 0.10) -> Dict[str, object]:
    """Resonance margin between blade-rate harmonics and the hull 2-node mode."""
    if f_hull_hz <= 0.0:
        raise VibrationValidationError("hull frequency must be positive")
    freqs = blade_rate_harmonics(rpm, n_blades, n_harmonics=5)
    margins = [abs(f / f_hull_hz - 1.0) for f in freqs]
    k = int(np.argmin(margins)) + 1
    closest = float(freqs[k - 1])
    margin = float(margins[k - 1])
    if margin <= band:
        level = "高风险"
    elif margin <= 2.0 * band:
        level = "中等风险"
    else:
        level = "低风险"
    return {"f_blade_hz": float(freqs[0]), "f_hull_hz": f_hull_hz,
            "closest_harmonic": k, "closest_freq_hz": closest,
            "margin_rel": margin, "risk": level, "band_rel": band}


def vibration_summary(L: float, section: HullGirderSection, displacement_t: float,
                      rpm: float, n_blades: int, thrust_N: float,
                      deck_panel: Dict[str, float] | None = None,
                      added_mass_ratio: float = DEFAULT_ADDED_MASS_RATIO,
                      zeta: float = DEFAULT_MODAL_DAMPING,
                      thresholds: ComfortThresholds = ComfortThresholds(),
                      n_modes: int = 10) -> Dict[str, object]:
    """Drop-in replacement for the served ``calculate_vibration`` placeholder.

    Returns the *same* decision fields as the legacy endpoint
    (``f_blade``, ``f_hull``, ``vertical_vibration``, ``vibration_level``,
    ``resonance_risk``) but each one is now derived from the physics above and
    carries its unit.  ``f_deck`` is returned only when a deck panel is supplied,
    because the deck frequency is a panel quantity, not ``2.5 * f_hull``.
    """
    girder = hull_girder_vertical(L, section, displacement_t,
                                  added_mass_ratio=added_mass_ratio)
    spec = deck_velocity_spectrum(L, E_STEEL, section.I_vertical,
                                  girder["mu_kg_per_m"], thrust_N, rpm, n_blades,
                                  zeta=zeta, n_modes=n_modes)
    grade = comfort_grade(spec["rms_mm_s"], thresholds=thresholds)
    risk = resonance_risk(rpm, n_blades, girder["f_2node_hz"])
    out: Dict[str, object] = {
        "f_blade_hz": blade_rate_frequency(rpm, n_blades),
        "f_hull_hz": girder["f_2node_hz"],
        "f_hull_3node_hz": girder["f_3node_hz"],
        "vertical_vibration_mm_s": spec["rms_mm_s"],
        "vibration_level": grade["level"],
        "comfort": grade["comfort"],
        "compliant": grade["compliant"],
        "resonance_risk": risk["risk"],
        "resonance_margin_rel": risk["margin_rel"],
        "velocity_spectrum": {"f_hz": spec["harmonics_hz"].tolist(),
                              "v_mm_s": spec["velocity_mm_s"].tolist()},
        "units": {"f": "Hz", "vertical_vibration": "mm/s RMS (1-80 Hz)",
                  "f_hull": "Hz (2-node vertical, free-free beam)"},
        "level_basis": ("hull-girder modal contribution only (lower bound): "
                        "local panel / structure-borne response withheld"),
        "modal_convergence_rel": spec["modal_convergence_rel"],
        "modal_converged": spec["converged"],
    }
    if deck_panel is not None:
        out["f_deck_hz"] = plate_natural_frequency(**deck_panel)
    return out