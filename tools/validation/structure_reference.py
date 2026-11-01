"""Independent closed-form references for the ship platform's structural domain.

Nothing here imports platform code: every function is a textbook / classification
society formula reproduced from the primary source, so that the served results can
be judged against an *external* baseline rather than against themselves.

Sources
-------
* Plate buckling (simply supported / clamped, uniaxial, biaxial, shear):
  Timoshenko & Gere, "Theory of Elastic Stability", 2nd ed., Ch. 9 (exact
  energy solution; integer half-wave minimisation used here instead of a fitted
  polynomial).  Clamped-edge coefficients follow DNV-RP-C201 Table 6-1
  (k = 4.00 simply supported, 6.97 clamped, long plate).
* Ultimate strength of a slender plate in compression: Faulkner (1975),
  ``sigma_u/sigma_y = 2/beta - 1/beta**2`` for beta >= 1 (with beta the
  plate slenderness b/t*sqrt(sigma_y/E)), used by DNV-RP-C201 Sec. 6 as the
  "plate-induced" reference.
* S-N curves: DNV-RP-C203 (2021) App. A, curve D in air;
  BS 7608:2014 Table 2, class D (stress range at 2e6 cycles = 53.4 MPa).
* Cumulative damage: Palmgren-Miner, D = sum(n_i / N_i).

All stresses in the reference functions are MPa unless the name says otherwise.
"""

from __future__ import annotations

import math

# --------------------------------------------------------------------------
# plate buckling
# --------------------------------------------------------------------------


def plate_rigidity(E: float, t: float, nu: float) -> float:
    """Flexural rigidity D = E t^3 / (12 (1 - nu^2)).  SI units in, SI out."""
    return E * t ** 3 / (12.0 * (1.0 - nu ** 2))


def k_uniaxial_x(alpha: float, m_max: int = 12) -> float:
    """Buckling coefficient for uniaxial compression along side ``a``.

    Exact Timoshenko solution for a simply supported plate a x b with
    ``alpha = a/b``:  k = min_m (m/alpha + alpha/m)**2,
    sigma_cr = k pi^2 D / b^2.  For alpha <= 1 the minimum is at m = 1.
    """
    best = math.inf
    for m in range(1, m_max + 1):
        k = (m / alpha + alpha / m) ** 2
        best = min(best, k)
    return best


def k_uniaxial_y(alpha: float, n_max: int = 12) -> float:
    """Buckling coefficient for uniaxial compression along side ``b``.

    Same energy solution but with the load normal to the ``b`` span; minimising
    over the number of half waves ``n`` along ``a`` gives
    k = min_n (alpha/n + n/alpha)**2 · (1/alpha**2)... expressed directly in the
    ``sigma_cr = k pi^2 D / b^2`` convention:

        sigma_cr = min_{n>=1} pi^2 D [ (1/b)^2 + (n/a)^2 ]^2 / (1/b)^2
                 = pi^2 D / b^2 · [1 + (n/alpha)**2]^2 ,  alpha = a/b

    which is bounded below by 1.0 (long plate, n -> 0); any served value below
    1.0 therefore violates the variational lower bound and cannot be a plate
    buckling coefficient in this convention.
    """
    best = math.inf
    for n in range(1, n_max + 1):
        k = (1.0 + (n / alpha) ** 2) ** 2
        best = min(best, k)
    return best


def k_shear(alpha: float, m_max: int = 12) -> float:
    """Simply supported shear buckling coefficient, k_s = 5.34 + 4/alpha^2 (alpha>=1).

    For alpha < 1 the roles of the sides swap: k_s = 5.34 + 4 alpha^2.
    """
    if alpha >= 1.0:
        return 5.34 + 4.0 / alpha ** 2
    return 5.34 + 4.0 * alpha ** 2


K_CLAMPED = 6.97  # DNV-RP-C201 Table 6-1, clamped edges, long plate


def sigma_cr(k: float, E: float, t: float, b: float, nu: float) -> float:
    """Critical stress in MPa: k pi^2 D / (b^2 t).

    Note the division by ``t``: pi^2 D / b^2 has units of force per unit length
    (N/m), it only becomes a stress after dividing by the plate thickness.
    """
    return k * math.pi ** 2 * plate_rigidity(E, t, nu) / (b ** 2 * t) / 1e6


def plate_slenderness(b: float, t: float, sigma_y_mpa: float, E_mpa: float) -> float:
    """Plate slenderness beta = b/t sqrt(sigma_y / E) (dimensionless)."""
    return (b / t) * math.sqrt(sigma_y_mpa / E_mpa)


def faulkner_ultimate(sigma_y_mpa: float, beta: float) -> float:
    """Faulkner (1975) ultimate strength of a plate in compression, MPa.

    sigma_u / sigma_y = 2/beta - 1/beta**2 for beta >= 1, = 1 for beta < 1.
    Note this is the *post-buckling* capacity and therefore always exceeds the
    elastic critical stress for a slender panel.
    """
    if beta >= 1.0:
        return sigma_y_mpa * (2.0 / beta - 1.0 / beta ** 2)
    return sigma_y_mpa


# --------------------------------------------------------------------------
# fatigue
# --------------------------------------------------------------------------

# DNV-RP-C203 Air, curve D: log10(N) = loga - m log10(dsigma)
DNV_D_LOGA_M3 = 12.164
DNV_D_LOGA_M5 = 15.606
DNV_KNEE_CYCLES = 1.0e7

# BS 7608:2014 class D: reference stress range 53.4 MPa at 2e6 cycles, m = 3
BS_D_SREF_2E6 = 53.4
BS_D_M3 = 3.0
BS_D_M5 = 5.0
BS_KNEE_CYCLES = 1.0e7


def sn_cycles(dsigma_mpa: float, loga_m3: float, loga_m5: float | None = None,
              knee: float = 1.0e7, m3: float = 3.0, m5: float = 5.0) -> float:
    """Bi-linear S-N: m3 branch, then m5 beyond ``knee`` cycles."""
    n3 = 10.0 ** (loga_m3 - m3 * math.log10(dsigma_mpa))
    if n3 <= knee or loga_m5 is None:
        return n3
    # knee stress where the m3 line crosses ``knee`` cycles
    s_knee = 10.0 ** ((loga_m3 - math.log10(knee)) / m3)
    return 10.0 ** (loga_m5 - m5 * math.log10(dsigma_mpa)) if dsigma_mpa < s_knee else n3


def dnv_d_cycles(dsigma_mpa: float) -> float:
    return sn_cycles(dsigma_mpa, DNV_D_LOGA_M3, DNV_D_LOGA_M5, DNV_KNEE_CYCLES, 3.0, 5.0)


def bs_d_cycles(dsigma_mpa: float) -> float:
    # log a for the m=3 branch: log10(2e6) + 3 log10(53.4)
    loga_m3 = math.log10(2e6) + BS_D_M3 * math.log10(BS_D_SREF_2E6)
    loga_m5 = math.log10(BS_KNEE_CYCLES) + BS_D_M5 * math.log10(
        10.0 ** ((loga_m3 - math.log10(BS_KNEE_CYCLES)) / BS_D_M3)
    )
    return sn_cycles(dsigma_mpa, loga_m3, loga_m5, BS_KNEE_CYCLES, BS_D_M3, BS_D_M5)


def knee_cycles(loga_m3: float, loga_m5: float, m3: float = 3.0, m5: float = 5.0) -> tuple[float, float]:
    """Intersection of the two branches: returns (N_knee, dsigma_knee[MPa])."""
    log_ds = (loga_m5 - loga_m3) / (m5 - m3)
    s_knee = 10.0 ** log_ds
    n_knee = 10.0 ** (loga_m3 - m3 * log_ds)
    return n_knee, s_knee


def miner_damage(stress_ranges_mpa, cycles, cycles_fn) -> float:
    """Palmgren-Miner damage sum."""
    return sum(n / cycles_fn(s) for s, n in zip(stress_ranges_mpa, cycles))


def rel_err(computed: float, reference: float) -> float:
    """Relative error in percent, guarded against zero reference."""
    if reference == 0:
        return math.inf if computed else 0.0
    return (computed - reference) / reference * 100.0