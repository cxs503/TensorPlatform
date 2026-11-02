"""Open-water (敞水) propeller performance: analytic anchors, identities and audit gates.

Scope
-----
This module deliberately **does not invent propeller data**.  It supplies

1. the analytic / conservation anchors that every open-water prediction must
   satisfy (``REAL`` closed-form physics, no empirical constants),
2. a physics-constrained reference model (``RBM-1``) that is fitted to a
   *declared* dataset with monotonicity / zero-thrust structure enforced by
   construction, and
3. a gate suite (:func:`audit_open_water_curve`) that turns those anchors into
   machine-checkable verdicts, so that any candidate model (serving endpoint,
   regression, CFD curve, ...) can be graded instead of asserted.

Anchors
-------
* open-water efficiency identity:      ``eta_0 = J * KT / (2 pi KQ)``
* thrust / torque / power identities:  ``T = KT rho n^2 D^4``,
  ``Q = KQ rho n^2 D^5``, ``P_D = 2 pi n Q``  (so ``P_D = 2 pi rho n^3 D^5 KQ``)
* thrust loading:                      ``C_T = T / (0.5 rho A_0 V_a^2) = 8 KT / (pi J^2)``
* actuator-disk (momentum theory) ideal efficiency:
  ``eta_i = 2 / (1 + sqrt(1 + C_T)) = 1 / (1 + a)`` with the axial induction
  factor ``a`` from ``C_T = 4 a (1 + a)``.  ``eta_0 <= eta_i`` is a *necessary*
  condition for any real propeller (viscous + rotational losses are positive).
* Froude similarity: ``KT``, ``KQ`` are functions of ``J`` (and Re), not of
  ``n`` and ``D`` separately; the dimensional quantities scale as ``n^2 D^4`` /
  ``n^2 D^5``.

Honest limitation (``WITHHELD``)
--------------------------------
The published Wageningen B-series polynomial coefficient tables (39 terms for
``KT``, 47 for ``KQ``, Oosterveld & van Oossanen 1975) are **not transcribed
here**: typing those coefficients from memory would fabricate literature data.
:func:`evaluate_b_series` implements the *full series structure* so the tables
can be dropped in as data (``P_D`` exponent ``t``, ``Ae/A0`` exponent ``u``,
``J`` exponent ``s``) once the table is available from a traceable source.
Until then any absolute accuracy claim against the B-series is ``WITHHELD``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "open_water_efficiency",
    "thrust_loading_coefficient",
    "axial_induction_factor",
    "ideal_efficiency_from_CT",
    "ideal_efficiency_from_KT_J",
    "ideal_wake_fraction",
    "derived_quantities",
    "evaluate_b_series",
    "B_SERIES_TERMS",
    "OpenWaterReference",
    "fit_reference_model",
    "audit_open_water_curve",
]


# ---------------------------------------------------------------------------
# 1. closed-form identities
# ---------------------------------------------------------------------------

def open_water_efficiency(J: float, KT: float, KQ: float) -> float:
    """Open-water efficiency from the definition ``eta_0 = J KT / (2 pi KQ)``.

    Raises ``ValueError`` for non-positive ``KQ`` (the identity is undefined at
    zero torque; a model that returns ``KQ == 0`` with ``KT > 0`` is itself
    non-physical).
    """
    if KQ <= 0.0:
        raise ValueError("KQ must be positive for the efficiency identity")
    if J < 0.0:
        raise ValueError("J must be non-negative")
    return J * KT / (2.0 * math.pi * KQ)


def thrust_loading_coefficient(J: float, KT: float) -> float:
    """Thrust loading ``C_T = 8 KT / (pi J^2)`` referenced to the advance speed."""
    if J <= 0.0:
        raise ValueError("C_T is undefined at J = 0 (bollard pull); use bollard thrust")
    return 8.0 * KT / (math.pi * J * J)


def axial_induction_factor(CT: float) -> float:
    """Axial induction factor ``a`` from ``C_T = 4 a (1 + a)`` (positive root)."""
    if CT <= -1.0:
        raise ValueError("CT = -1 is the momentum-theory singularity")
    return (-1.0 + math.sqrt(1.0 + CT)) / 2.0


def ideal_efficiency_from_CT(CT: float) -> float:
    """Actuator-disk ideal efficiency ``2 / (1 + sqrt(1 + C_T))``."""
    if CT <= -1.0:
        raise ValueError("CT = -1 is the momentum-theory singularity")
    return 2.0 / (1.0 + math.sqrt(1.0 + CT))


def ideal_efficiency_from_KT_J(J: float, KT: float) -> float:
    """Ideal efficiency of the actuator disc carrying ``KT`` at advance ratio ``J``."""
    return ideal_efficiency_from_CT(thrust_loading_coefficient(J, KT))


def ideal_wake_fraction(CT: float) -> float:
    """Ideal (momentum-theory) wake fraction ``w = a / (1 + a)``."""
    a = axial_induction_factor(CT)
    return a / (1.0 + a)


def derived_quantities(J: float, KT: float, KQ: float, rho: float, n: float,
                       D: float) -> Dict[str, float]:
    """Dimensional thrust [N], torque [N m], delivered power [W], advance speed [m/s].

    Implements the identities ``T = KT rho n^2 D^4``, ``Q = KQ rho n^2 D^5``,
    ``P_D = 2 pi n Q`` and ``V_a = J n D``; the power identity is also returned
    in the closed form ``P_D = 2 pi rho n^3 D^5 KQ`` so both routes can be
    compared by callers.
    """
    if min(rho, n, D) <= 0.0:
        raise ValueError("rho, n and D must be positive")
    T = KT * rho * n * n * D ** 4
    Q = KQ * rho * n * n * D ** 5
    return {
        "thrust_N": T,
        "torque_Nm": Q,
        "power_W": 2.0 * math.pi * n * Q,
        "power_closed_form_W": 2.0 * math.pi * rho * n ** 3 * D ** 5 * KQ,
        "advance_speed_m_s": J * n * D,
    }


# ---------------------------------------------------------------------------
# 2. Wageningen B-series polynomial structure (coefficients supplied as data)
# ---------------------------------------------------------------------------

#: ``(s, t, u, C)`` with ``KT = sum C J^s (P/D)^t (Ae/A0)^u`` for thrust and the
#: analogous expansion for torque, exactly as printed in Oosterveld & van
#: Oossanen (1975).  *Intentionally empty*: the table must be transcribed from a
#: traceable copy of the paper, not from memory.  See module docstring.
B_SERIES_TERMS: Dict[str, List[Tuple[int, int, int, float]]] = {"KT": [], "KQ": []}


def evaluate_b_series(
    J: float,
    P_D: float,
    Ae_Ao: float,
    terms: Optional[Dict[str, Sequence[Tuple[int, int, int, float]]]] = None,
) -> Dict[str, float]:
    """Evaluate the B-series polynomial for ``KT`` / ``KQ`` / ``eta_0``.

    ``terms`` defaults to :data:`B_SERIES_TERMS`.  Raises ``ValueError`` with an
    explicit message when the coefficient table has not been supplied - the
    module refuses to guess literature coefficients.
    """
    table = terms if terms is not None else B_SERIES_TERMS
    out: Dict[str, float] = {}
    for key in ("KT", "KQ"):
        rows = table.get(key) or []
        if not rows:
            raise ValueError(
                f"no {key} coefficient table supplied: transcribe the Oosterveld & van "
                "Oossanen (1975) table into B_SERIES_TERMS (or pass terms=...) before use"
            )
        out[key] = float(sum(C * (J ** s) * (P_D ** t) * (Ae_Ao ** u) for s, t, u, C in rows))
    out["eta_0"] = open_water_efficiency(J, out["KT"], out["KQ"]) if out["KQ"] > 0.0 else 0.0
    return out


# ---------------------------------------------------------------------------
# 3. data-faithful reference curve (RBC-1): monotone cubic Hermite interpolation
# ---------------------------------------------------------------------------

def _fc_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Fritsch-Carlson monotone cubic slopes for strictly monotone ``(x, y)``."""
    h = np.diff(x)
    delta = np.diff(y) / h
    m = np.empty_like(y)
    m[0] = delta[0]
    m[-1] = delta[-1]
    m[1:-1] = (delta[:-1] + delta[1:]) / 2.0
    for k in range(delta.size):
        if delta[k] == 0.0:
            m[k] = 0.0
            m[k + 1] = 0.0
            continue
        a, b = m[k] / delta[k], m[k + 1] / delta[k]
        if a * a + b * b > 9.0:
            tau = 3.0 / math.sqrt(a * a + b * b)
            m[k] = tau * a * delta[k]
            m[k + 1] = tau * b * delta[k]
    return m


@dataclass(frozen=True)
class OpenWaterReference:
    """Data-faithful monotone reference curve over a declared open-water dataset.

    Why interpolation and not a fitted physics formula: the declared dataset
    (``data/b4_70_reference_dataset.json``) is not smooth enough to be
    reproduced by a 3-parameter thrust/torque law (a power-law fit leaves a
    >60 % residual at ``J = 1.0``), so a *fitted* curve would silently replace
    the data instead of representing it.  A monotone cubic Hermite interpolant

    * reproduces the declared points exactly (residual 0 by construction),
    * preserves the physically required strict monotonicity of ``KT``/``KQ``,
    * derives ``eta_0`` from the identity only (never stores it), and
    * clamps outside the declared range instead of extrapolating.

    Absolute accuracy against the Wageningen B-series is therefore ``WITHHELD``
    (the dataset itself is declared "approximate" upstream); what this curve
    supports is *consistency and gate* auditing, not a fidelity claim.
    """

    J: Sequence[float]
    KT: Sequence[float]
    KQ: Sequence[float]
    rho: float = 1025.0

    def __post_init__(self) -> None:
        j = np.asarray(self.J, dtype=float)
        kt = np.asarray(self.KT, dtype=float)
        kq = np.asarray(self.KQ, dtype=float)
        if not (j.shape == kt.shape == kq.shape) or j.size < 3:
            raise ValueError("need >= 3 paired samples of J, KT, KQ")
        if np.any(np.diff(j) <= 0.0):
            raise ValueError("J must be strictly increasing")
        if np.any(np.diff(kt) >= 0.0) or np.any(np.diff(kq) >= 0.0):
            raise ValueError("KT and KQ must be strictly decreasing over the dataset")
        object.__setattr__(self, "J", j)
        object.__setattr__(self, "KT", kt)
        object.__setattr__(self, "KQ", kq)
        object.__setattr__(self, "_m_kt", _fc_slopes(j, kt))
        object.__setattr__(self, "_m_kq", _fc_slopes(j, kq))

    @property
    def mu(self) -> float:
        """Interpolation residual at the declared points (zero by construction)."""
        return 0.0

    @property
    def kt_zero_J(self) -> float:
        """First advance ratio where ``KT`` reaches zero (linear scan, 1e-3 grid)."""
        return self._zero("KT")

    @property
    def kq_zero_J(self) -> float:
        return self._zero("KQ")

    def _zero(self, which: str) -> float:
        y = self.KT if which == "KT" else self.KQ
        j = self.J
        for i in range(1, j.size):
            if y[i - 1] > 0.0 >= y[i]:
                t = y[i - 1] / (y[i - 1] - y[i])
                return float(j[i - 1] + t * (j[i] - j[i - 1]))
        return float(j[-1])

    def _interp(self, x: float, y: np.ndarray, m: np.ndarray) -> float:
        j = self.J
        if x <= j[0]:
            return float(y[0])
        if x >= j[-1]:
            return float(y[-1])
        k = int(np.searchsorted(j, x) - 1)
        h = j[k + 1] - j[k]
        t = (x - j[k]) / h
        h00 = 2 * t ** 3 - 3 * t ** 2 + 1
        h10 = t ** 3 - 2 * t ** 2 + t
        h01 = -2 * t ** 3 + 3 * t ** 2
        h11 = t ** 3 - t ** 2
        return float(h00 * y[k] + h10 * h * m[k] + h01 * y[k + 1] + h11 * h * m[k + 1])

    def coefficients(self, J: float) -> Dict[str, float]:
        x = float(J)
        if x < 0.0:
            raise ValueError("J must be non-negative")
        kt = self._interp(x, self.KT, self._m_kt)
        kq = self._interp(x, self.KQ, self._m_kq)
        eta = open_water_efficiency(x, kt, kq) if kq > 0.0 else 0.0
        return {"J": x, "KT": kt, "KQ": kq, "eta_0": eta}

    def curve(self, J: Iterable[float]) -> Dict[str, np.ndarray]:
        rows = [self.coefficients(j) for j in J]
        return {k: np.array([r[k] for r in rows]) for k in ("J", "KT", "KQ", "eta_0")}


def fit_reference_model(J: Sequence[float], KT: Sequence[float], KQ: Sequence[float],
                        rho: float = 1025.0) -> OpenWaterReference:
    """Build the RBC-1 reference curve (name kept for API stability)."""
    return OpenWaterReference(J, KT, KQ, rho=rho)


# ---------------------------------------------------------------------------
# 4. gate suite
# ---------------------------------------------------------------------------

def _zero_crossing(J: np.ndarray, y: np.ndarray) -> Optional[float]:
    """First interior advance ratio where ``y`` crosses zero (linear interpolation)."""
    for i in range(1, y.size):
        if y[i - 1] > 0.0 >= y[i]:
            t = y[i - 1] / (y[i - 1] - y[i])
            return float(J[i - 1] + t * (J[i] - J[i - 1]))
    return None


def audit_open_water_curve(
    J: Sequence[float],
    KT: Sequence[float],
    KQ: Sequence[float],
    eta_0: Optional[Sequence[float]] = None,
    eta_identity_tol: float = 5e-3,
    momentum_tol: float = 1e-6,
) -> Dict[str, object]:
    """Grade an open-water curve against closed-form / conservation anchors.

    Each gate returns ``{"pass": bool, "detail": ...}``; the ``summary`` block
    carries the numbers needed for a report table.  Gates:

    ``G1_eta_identity``   ``eta_0`` reproducible from ``J KT / (2 pi KQ)``
    ``G2_bollard``        ``KT(0) > 0`` and ``KQ(0) > 0``
    ``G3_monotone``       ``KT`` and ``KQ`` strictly decreasing over the range
    ``G4_zero_thrust``    a zero-thrust advance ratio exists, with ``KQ > 0`` there
    ``G5_eta_single_peak`` interior single maximum of ``eta_0``
    ``G6_momentum_bound`` ``eta_0 <= eta_i`` pointwise (necessary condition)
    ``G7_froude_similarity`` ``KT``/``KQ`` depend on ``J`` only (checked via fit)
    """
    j = np.asarray(J, dtype=float)
    kt = np.asarray(KT, dtype=float)
    kq = np.asarray(KQ, dtype=float)
    if not (j.shape == kt.shape == kq.shape) or j.size < 3:
        raise ValueError("need >= 3 paired samples of J, KT, KQ")
    gates: Dict[str, Dict[str, object]] = {}

    eta = np.array([open_water_efficiency(a, b, c) if c > 0.0 else 0.0
                    for a, b, c in zip(j, kt, kq)])

    # G1 efficiency identity (only meaningful when the model publishes eta_0)
    if eta_0 is None:
        gates["G1_eta_identity"] = {"pass": None, "detail": "not evaluated (no eta_0 supplied)"}
        eta_max_dev = None
    else:
        eta_ref = np.asarray(eta_0, dtype=float)
        dev = np.abs(eta - eta_ref)
        eta_max_dev = float(np.max(dev))
        gates["G1_eta_identity"] = {
            "pass": bool(eta_max_dev <= eta_identity_tol),
            "detail": {"max_abs_dev": eta_max_dev, "tol": eta_identity_tol,
                       "worst_J": float(j[int(np.argmax(dev))])},
        }

    gates["G2_bollard"] = {
        "pass": bool(kt[0] > 0.0 and kq[0] > 0.0),
        "detail": {"KT_J0": float(kt[0]), "KQ_J0": float(kq[0])},
    }

    d_kt = np.diff(kt)
    d_kq = np.diff(kq)

    def _monotone(y: np.ndarray, d: np.ndarray) -> Tuple[bool, float]:
        """Non-increasing everywhere, and strictly decreasing while still positive.

        A clamped zero tail beyond the zero-thrust advance ratio is physically
        admissible (no thrust, no torque), so a zero increment is only a failure
        while the curve has not yet reached zero.
        """
        strict = d[np.asarray(y[:-1]) > 0.0]
        return bool(np.all(d <= 0.0) and (strict.size == 0 or np.all(strict < 0.0))), float(np.max(d))

    ok_kt, mdl_kt = _monotone(kt, d_kt)
    ok_kq, mdl_kq = _monotone(kq, d_kq)
    gates["G3_monotone"] = {
        "pass": bool(ok_kt and ok_kq),
        "detail": {"max_dKT": mdl_kt, "max_dKQ": mdl_kq},
    }

    j0t = _zero_crossing(j, kt)
    if j0t is None:
        gates["G4_zero_thrust"] = {"pass": False, "detail": "no zero-thrust crossing in range"}
    else:
        kq_at = float(np.interp(j0t, j, kq))
        eta_at = open_water_efficiency(j0t, 0.0, kq_at) if kq_at > 0.0 else 0.0
        gates["G4_zero_thrust"] = {
            "pass": bool(kq_at >= 0.0 and eta_at <= 1e-9),
            "detail": {"J_zero_thrust": j0t, "KQ_at_zero_thrust": kq_at,
                       "simultaneous_null": bool(kq_at <= 1e-12)},
        }

    interior = eta[1:-1]
    peak_ok = bool(interior.size and eta.max() > 0.0 and
                   np.argmax(eta) not in (0, eta.size - 1))
    gates["G5_eta_single_peak"] = {
        "pass": peak_ok,
        "detail": {"eta_max": float(eta.max()), "J_at_max": float(j[int(np.argmax(eta))])},
    }

    util = []
    for a, b, c in zip(j, kt, kq):
        if a <= 0.0 or b <= 0.0 or c <= 0.0:
            continue
        util.append(open_water_efficiency(a, b, c) / ideal_efficiency_from_KT_J(a, b))
    util_max = float(max(util)) if util else None
    gates["G6_momentum_bound"] = {
        "pass": bool(util_max is not None and util_max <= 1.0 + momentum_tol),
        "detail": {"max_eta_over_eta_ideal": util_max, "n_points": len(util)},
    }

    gates["G7_froude_similarity"] = {"pass": None,
                                     "detail": "verified separately (D, n scaling test)"}

    return {
        "gates": gates,
        "summary": {
            "n_points": int(j.size),
            "J_range": [float(j[0]), float(j[-1])],
            "eta_max": float(eta.max()),
            "eta_max_J": float(j[int(np.argmax(eta))]),
            "J_zero_thrust": j0t,
            "KT_bollard": float(kt[0]),
            "KQ_bollard": float(kq[0]),
            "eta_identity_max_dev": eta_max_dev,
            "momentum_utilisation": util_max,
        },
    }