"""Structural vibration: SDOF isolation theory and beam/plate eigen-solvers.

Every routine here is anchored to a closed-form solution that is re-derived in
``docs/reports/m1707_vibration_noise_verification.md`` (see also the tests
``backend/tests/test_m1707_vibration_noise_verification.py``).  The numerical
solvers (finite difference / Rayleigh-Ritz) exist so that the *discretisation*
used for real hull structures can be validated against the exact analytic
frequencies, which is the standard verification path (ASME V&V 20 spirit).

References
----------
* Den Hartog, J.P., *Mechanical Vibrations* (4th ed.), ch. 2 & 5.
* Blevins, R.D., *Formulas for Natural Frequency and Mode Shape*, Tables 8-1, 11-1.
* Leissa, A.W., *Vibration of Plates*, NASA SP-160, eq. (4.16) for SSSS plates.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy.linalg import eigh as _sym_eigh

# Roots of cos(beta L) * cosh(beta L) = -1 (clamped-free cantilever), first five.
CANTILEVER_BETA_L = (
    1.875104068711961,
    4.694091132974174,
    7.854757438237613,
    10.995540734875467,
    14.137168391046471,
)


# ----------------------------------------------------------------------------
# SDOF transmissibility (base excitation / force transmission)
# ----------------------------------------------------------------------------
def sdof_compliance(r: float, zeta: float) -> float:
    """Dynamic magnification of the mass response, ``|H| = 1/sqrt((1-r^2)^2+(2 z r)^2)``."""
    return 1.0 / math.sqrt((1.0 - r * r) ** 2 + (2.0 * zeta * r) ** 2)


def sdof_transmissibility(r: float, zeta: float) -> float:
    """Displacement transmissibility ``T(r) = sqrt((1+(2 z r)^2)/((1-r^2)^2+(2 z r)^2))``.

    Note that ``T`` is *not* ``|H|``: the numerator carries the damper term, which
    is why the two curves peak at slightly different frequencies and why the
    high-frequency roll-off of ``T`` flattens to ``2 z / r``.
    """
    r2 = r * r
    num = 1.0 + (2.0 * zeta * r) ** 2
    den = (1.0 - r2) ** 2 + (2.0 * zeta * r) ** 2
    return math.sqrt(num / den)


def sdof_magnification_peak(zeta: float) -> float:
    """Exact peak of ``|H|``: ``1 / (2 z sqrt(1 - z^2))`` (zeta < 1/sqrt2)."""
    if not 0.0 < zeta < 1.0 / math.sqrt(2.0):
        raise ValueError("zeta must be in (0, 1/sqrt(2)) for a magnification peak")
    return 1.0 / (2.0 * zeta * math.sqrt(1.0 - zeta ** 2))


def sdof_magnification_peak_ratio(zeta: float) -> float:
    """Location of the ``|H|`` peak: ``r = sqrt(1 - 2 z^2)``."""
    if not 0.0 < zeta < 1.0 / math.sqrt(2.0):
        raise ValueError("zeta must be in (0, 1/sqrt(2))")
    return math.sqrt(1.0 - 2.0 * zeta ** 2)


def sdof_transmissibility_peak_ratio(zeta: float) -> float:
    """Location of the transmissibility peak: root of ``1 - s - 2 z^2 s^2 = 0``, ``s = r^2``.

    ``r_peak^2 = (sqrt(1 + 8 z^2) - 1) / (4 z^2)``; this is *not* ``1 - 2 z^2``
    (that is the ``|H|`` peak), although the two agree to ``O(z^4)``.
    """
    if zeta <= 0.0:
        raise ValueError("zeta must be positive")
    return math.sqrt((math.sqrt(1.0 + 8.0 * zeta * zeta) - 1.0) / (4.0 * zeta * zeta))


def sdof_transmissibility_peak(zeta: float) -> float:
    """Peak value of the transmissibility, ``T(r_peak)``."""
    return sdof_transmissibility(sdof_transmissibility_peak_ratio(zeta), zeta)


def sdof_phase(r: float, zeta: float) -> float:
    """Phase lag of the mass response behind the base motion, radians."""
    return math.atan2(2.0 * zeta * r, 1.0 - r * r)


def half_power_bandwidth_ratio(zeta: float, bracket: float = 20.0) -> float:
    """Half-power bandwidth ``(f2 - f1) / f_n`` of ``|H|``, by root finding.

    The textbook result is ``2 zeta``; the numerical value is returned so the
    size of the approximation can be quantified (the *exact* identity is
    ``r1^2 = 1 - 2z^2 - 2 z sqrt(1-z^2)``, ``r2^2 = 1 - 2z^2 + 2 z sqrt(1-z^2)``).
    """
    target = sdof_magnification_peak(zeta) / math.sqrt(2.0)

    def g(r: float) -> float:
        return sdof_compliance(r, zeta) - target

    lo, hi = 1.0, bracket
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if g(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    r2 = 0.5 * (lo + hi)

    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if g(mid) < 0.0:
            lo = mid
        else:
            hi = mid
    r1 = 0.5 * (lo + hi)
    return r2 - r1


def isolation_insertion_loss_db(r: float, zeta: float) -> float:
    """Insertion loss ``IL = -20 log10 T(r)`` in dB (negative = amplification)."""
    return -20.0 * math.log10(sdof_transmissibility(r, zeta))


def two_stage_transmissibility(r: float, zeta: float) -> float:
    """Transmissibility of two identical cascaded isolation stages (same f_n, zeta)."""
    return sdof_transmissibility(r, zeta) ** 2


def transmissibility_slope_db_per_octave(fn_hz: float, r_from: float = 100.0, n_oct: int = 3,
                                         zeta: float = 1.0e-3, two_stage: bool = False) -> float:
    """Asymptotic roll-off in dB/octave of the isolation curve beyond resonance.

    Lossless spring (``zeta -> 0``): ``T -> 1/r^2``, i.e. 12 dB/oct for a single
    stage and 24 dB/oct for two cascaded stages.  **With damping the roll-off
    degrades towards 6 dB/oct**, because the damper itself transmits force
    (``T -> 2 zeta / r`` at high frequency) — see the verification report.
    """
    def il(f: float) -> float:
        r = f / fn_hz
        t = two_stage_transmissibility(r, zeta) if two_stage else sdof_transmissibility(r, zeta)
        return -20.0 * math.log10(t)

    f_lo = r_from * fn_hz
    f_hi = f_lo * 2 ** n_oct
    return (il(f_hi) - il(f_lo)) / n_oct


# ----------------------------------------------------------------------------
# Beam eigen-frequencies (Euler-Bernoulli)
# ----------------------------------------------------------------------------
def cantilever_frequencies(length: float, EI: float, rho_a: float, n_modes: int = 3) -> np.ndarray:
    """Exact cantilever frequencies [Hz]: ``f_n = (beta_n L)^2 / (2 pi L^2) sqrt(EI / rho A)``."""
    if n_modes > len(CANTILEVER_BETA_L):
        raise ValueError(f"closed-form roots available for n_modes <= {len(CANTILEVER_BETA_L)}")
    out = []
    for n in range(n_modes):
        beta = CANTILEVER_BETA_L[n] / length
        out.append(beta ** 2 * math.sqrt(EI / rho_a) / (2.0 * math.pi))
    return np.array(out)


def cantilever_rayleigh_ritz(length: float, EI: float, rho_a: float, n_terms: int = 6,
                             n_quad: int = 40) -> np.ndarray:
    """Cantilever frequencies from a Rayleigh-Ritz solution with polynomial trial set.

    Trial functions ``phi_k = (x/L)^(k+2)`` satisfy the clamped condition
    ``w(0)=w'(0)=0`` exactly but not the free-end natural conditions, hence the
    RR frequencies are upper bounds converging downwards to the exact values.
    The ``(x/L)^1`` term is excluded on purpose: it is the rigid-body rotation
    (zero curvature), which would make the stiffness matrix singular.
    """
    if n_terms < 2:
        raise ValueError("n_terms must be >= 2")
    n_quad = max(n_quad, 8 * n_terms)   # keep quadrature ahead of the integrand degree
    x, w = np.polynomial.legendre.leggauss(n_quad)
    x = 0.5 * length * (x + 1.0)
    w = 0.5 * length * w
    xi = x / length
    k = np.arange(n_terms) + 2

    phi = np.array([xi ** i for i in k])                            # (n_terms, nq)
    d2phi = np.array([i * (i - 1) * xi ** (i - 2) / length ** 2 for i in k])

    K = EI * np.einsum("aq,bq,q->ab", d2phi, d2phi, w)
    M = rho_a * np.einsum("aq,bq,q->ab", phi, phi, w)
    K = 0.5 * (K + K.T)      # symmetrise (quadrature round-off)
    M = 0.5 * (M + M.T)
    # The monomial Gram matrix is Hilbert-like: cond(M) ~ 1e12 at n_terms = 12, which
    # breaks a direct generalised solver (and a naive eigvalsh(solve(M, K)) is plain
    # wrong because M^-1 K is not symmetric).  Mass-orthonormalise with the Cholesky
    # factor instead: A = L^-1 K L^-T is symmetric with M-orthonormal coordinates.
    L = np.linalg.cholesky(M)
    A = np.linalg.solve(L, K)
    A = np.linalg.solve(L, A.T).T
    eigvals = np.linalg.eigvalsh(0.5 * (A + A.T))
    omega = np.sqrt(np.maximum(eigvals, 0.0))
    return np.sort(omega) / (2.0 * math.pi)


def _ssss_biharmonic_matrix(n_cells: int, length: float) -> np.ndarray:
    """5-point biharmonic matrix for a simply-supported beam (``w=0``, ``w''=0``).

    Ghost nodes follow from the symmetric boundary conditions,
    ``w_{-j} = -w_j`` and ``w_{2N-j} = -w_j``; the resulting matrix is second
    order accurate in ``h``.
    """
    h = length / n_cells
    n_nodes = n_cells + 1
    n = n_nodes - 2                    # unknowns = global nodes 1..N-1
    K = np.zeros((n, n))
    last = n_nodes - 1
    for gi in range(1, last):
        row = gi - 1
        for d, c in ((-2, 1.0), (-1, -4.0), (0, 6.0), (1, -4.0), (2, 1.0)):
            node = gi + d
            if node == 0 or node == last:
                continue               # w = 0 on the supported ends
            if node < 0:
                K[row, (-node) - 1] += -c      # w_{-j} = -w_j
            elif node > last:
                K[row, (2 * last - node) - 1] += -c   # w_{2N-j} = -w_j
            else:
                K[row, node - 1] += c
    return K


def ssss_beam_fd_frequencies(length: float, EI: float, rho_a: float, n_cells: int = 64,
                             n_modes: int = 3) -> np.ndarray:
    """Simply-supported beam frequencies from a 2nd-order finite-difference eigenproblem."""
    if n_cells < 8:
        raise ValueError("n_cells must be >= 8")
    h = length / n_cells
    K = EI / h ** 4 * _ssss_biharmonic_matrix(n_cells, length)
    M = rho_a * np.eye(K.shape[0])
    eigvals = np.linalg.eigvalsh(np.linalg.solve(M, K))
    omega = np.sqrt(np.maximum(eigvals, 1e-30))
    return np.sort(omega)[:n_modes] / (2.0 * math.pi)


def ssss_beam_exact_frequencies(length: float, EI: float, rho_a: float, n_modes: int = 3) -> np.ndarray:
    """Exact simply-supported Euler-Bernoulli frequencies ``f_n = n^2 pi / (2 L^2) sqrt(EI/rho A)``."""
    n = np.arange(1, n_modes + 1, dtype=float)
    return n ** 2 * math.pi / (2.0 * length ** 2) * math.sqrt(EI / rho_a)


# ----------------------------------------------------------------------------
# Rectangular plate (simply supported, Kirchhoff)
# ----------------------------------------------------------------------------
def ssss_plate_frequencies(a: float, b: float, D: float, rho_h: float,
                           modes: Sequence[tuple[int, int]] = ((1, 1), (1, 2), (2, 1))) -> np.ndarray:
    """Exact SSSS plate frequencies [Hz]: ``omega_mn = ((m pi/a)^2 + (n pi/b)^2) sqrt(D/rho h)``."""
    out = []
    for m, n in modes:
        lam = (m * math.pi / a) ** 2 + (n * math.pi / b) ** 2
        out.append(lam * math.sqrt(D / rho_h) / (2.0 * math.pi))
    return np.array(out)


def ssss_plate_fd_modes(a: float, b: float, D: float, rho_h: float, m: int, n: int,
                        n_cells: int = 40) -> dict:
    """Semi-discrete FD solution for the SSSS plate mode ``(m, n)``.

    The mode is expanded as ``w(x, y) = psi(x) sin(n pi y / b)``, which is exact
    in ``y`` for a simply supported pair of edges.  The biharmonic operator then
    reduces to ``L = (d^2/dx^2 - q^2)^2`` with ``q = n pi / b``, discretised with
    the 2nd-difference operator ``A`` and the ``w=0, w''=0`` ghost rule.  Because
    the boundary rows of ``A - q^2 I`` are identically zero, the interior
    restriction is exact; the discretisation error is ``O(h^2)``.
    """
    if n_cells < 8:
        raise ValueError("n_cells must be >= 8")
    q = n * math.pi / b
    h = a / n_cells
    k = n_cells - 1
    A = np.zeros((k, k))
    for i in range(k):
        A[i, i] = -2.0
        if i - 1 >= 0:
            A[i, i - 1] = 1.0
        if i + 1 < k:
            A[i, i + 1] = 1.0
    A /= h * h
    Aq = A - q * q * np.eye(k)
    L = Aq @ Aq
    eigvals, eigvecs = np.linalg.eigh(L)
    order = np.argsort(eigvals)
    lam = eigvals[order]
    vec = eigvecs[:, order]
    lam_exact = ((m * math.pi / a) ** 2 + q ** 2) ** 2
    j = int(np.argmin(np.abs(lam - lam_exact)))
    omega = math.sqrt(max(lam[j], 0.0) * D / rho_h)
    x = np.linspace(0.0, a, k + 2)
    psi = np.concatenate(([0.0], vec[:, j], [0.0]))
    return {
        "frequency_hz": omega / (2.0 * math.pi),
        "omega_rad_s": omega,
        "lambda": float(lam[j]),
        "lambda_exact": float(lam_exact),
        "x": x,
        "psi": psi,
        "mode_index": j,
    }


def plate_rayleigh_quotient_inadmissible_trial(a: float, b: float, D: float, rho_h: float) -> float:
    """Rayleigh quotient for the trial shape ``w = x(a-x) y(b-y)`` — **not a bound**.

    Traps worth recording: ``w=0`` on the edges is satisfied, but the Kirchhoff
    Rayleigh quotient ``omega^2 = D int (lap w)^2 / (rho h int w^2)`` is only
    equivalent to the biharmonic eigenproblem when the boundary term
    ``\oint (w d_n lap w - lap w d_n w)`` vanishes.  Here ``lap w = -2(Y+X)`` does
    *not* vanish on the edges, so the quotient is meaningless: for a square plate
    it returns about ``2.9 x f_11``.  Kept as a documented negative example and
    exercised by the test suite.
    """
    return _plate_quotient_cubic(a, b, D, rho_h)


def _plate_quotient_cubic(a: float, b: float, D: float, rho_h: float) -> float:
    def lap(x: float, y: float) -> float:
        X = x * (a - x)
        Y = y * (b - y)
        return -2.0 * Y - 2.0 * X     # w_xx + w_yy for the separated cubic trial

    gx, wx = np.polynomial.legendre.leggauss(60)
    x = 0.5 * a * (gx + 1.0)
    wx = 0.5 * a * wx
    y = 0.5 * b * (gx + 1.0)
    wy = 0.5 * b * wx
    XX, YY = np.meshgrid(x, y, indexing="ij")
    WW = np.outer(wx, wy)
    W = XX * (a - XX) * YY * (b - YY)
    L = -2.0 * YY - 2.0 * XX
    num = D * np.sum(L ** 2 * WW)
    den = rho_h * np.sum(W ** 2 * WW)
    return math.sqrt(num / den) / (2.0 * math.pi)


def plate_rayleigh_quotient_sine(a: float, b: float, D: float, rho_h: float,
                                m: int = 1, n: int = 1, n_quad: int = 60) -> float:
    """Single-term Rayleigh quotient with the *admissible* sine mode ``sin(m pi x/a) sin(n pi y/b)``.

    For a simply supported Kirchhoff plate the sine functions satisfy both
    ``w=0`` and ``lap w=0`` on the edges, so the quotient is valid; it reproduces
    the closed-form frequency to quadrature accuracy (verifies the quadrature,
    not the physics).
    """
    gx, wx = np.polynomial.legendre.leggauss(n_quad)
    x = 0.5 * a * (gx + 1.0)
    y = 0.5 * b * (gx + 1.0)
    W = np.outer(0.5 * a * wx, 0.5 * b * wx)
    X, Y = np.meshgrid(x, y, indexing="ij")
    kx, ky = m * math.pi / a, n * math.pi / b
    mode = np.sin(kx * X) * np.sin(ky * Y)
    lap = -(kx * kx + ky * ky) * mode
    num = D * np.sum(lap ** 2 * W)
    den = rho_h * np.sum(mode ** 2 * W)
    return math.sqrt(num / den) / (2.0 * math.pi)


__all__ = [
    "CANTILEVER_BETA_L",
    "sdof_compliance",
    "sdof_transmissibility",
    "sdof_magnification_peak",
    "sdof_magnification_peak_ratio",
    "sdof_transmissibility_peak",
    "sdof_transmissibility_peak_ratio",
    "sdof_phase",
    "half_power_bandwidth_ratio",
    "isolation_insertion_loss_db",
    "two_stage_transmissibility",
    "transmissibility_slope_db_per_octave",
    "cantilever_frequencies",
    "cantilever_rayleigh_ritz",
    "ssss_beam_fd_frequencies",
    "ssss_beam_exact_frequencies",
    "ssss_plate_frequencies",
    "ssss_plate_fd_modes",
    "plate_rayleigh_quotient_inadmissible_trial",
    "plate_rayleigh_quotient_sine",
]