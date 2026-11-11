"""Temporal interpolation operators for heterogeneous-time-step field exchange.

Physical background
-------------------
Coupled solvers advance with different time steps (an explicit discrete-velocity
solver with a stiff stability limit, an implicit continuum solver that is
limited by accuracy, a contact solver limited by a contact stiffness, ...).  The
exchange therefore needs the *fine* solver's field at times that are **not**
sample points of the fine solver.  The exchange operator is a temporal
interpolation operator

    I_t[f](t') = sum_{k in S(t')} w_k(t') f_k ,      sum_k w_k(t') = 1

where ``S(t')`` is a stencil of stored samples around ``t'``.  Three schemes are
implemented:

===========  ==============================================  ==================
method       construction                                    error (smooth data)
===========  ==============================================  ==================
``nearest``  zero-order hold (piecewise constant)             O(dt)
``linear``   two-point Lagrange on arbitrary abscissas        O(dt^2)
``cubic``    four-point Lagrange on arbitrary abscissas       O(dt^4)
===========  ==============================================  ==================

Consequences that the acceptance tests rely on:

* **Node identity.**  If ``t'`` coincides with a stored sample (to
  ``identity_tol``) the stored snapshot is returned unchanged (bit-exact copy,
  error identically zero, far below the 1e-12 acceptance line).  This is what
  makes the operator a *degenerate identity* when the two solvers share a
  clock.
* **Partition of unity.**  ``sum_k w_k = 1`` exactly (Lagrange property), so a
  spatially uniform field is reproduced to machine precision and a spatially
  uniform *conserved* quantity (mass) is preserved exactly.
* **Convexity and boundedness.**  Linear weights are non-negative, hence the
  interpolated value cannot leave the range of the two bracketing samples.
  Cubic weights on non-uniform abscissas can be negative: the interpolant may
  overshoot, so :class:`TemporalInterpolator` offers an optional ``bounds``
  limiter and always reports the clamped fraction.
* **Energy.**  For an oscillatory field ``f(t) = A cos(omega t)`` the linear
  interpolant has an amplitude deficit ``1 - cos(omega dt / 2)``, i.e. an
  energy error ``~ (omega dt)^2 / 4``.  The residual is therefore a *resolution*
  statement: it falls below 1 % as soon as ``omega dt < 0.2``.  See
  :func:`convergence_study` and :class:`EnergyAudit`.

Convergence-order convention
----------------------------
Two order statements are reported, because both are used in the coupling
literature and they differ:

* intrinsic interpolation order for ``C^inf`` data -- ``linear``: 2,
  ``cubic``: 4;
* order measured on the *coupled trajectory*, i.e. when the samples themselves
  carry the solver's temporal error (first-order explicit integration, or a
  slope discontinuity such as an event ramp) -- ``linear``: 1.  This is the
  operative order for a first-order-in-time coupled scheme.

Both are produced by :func:`convergence_study` with ``profile="smooth"`` /
``profile="kink"`` / ``profile="euler"``.

Zero domain semantics: fields are plain arrays, "energy" is the quadratic
functional the caller supplies via the cell-weight vector.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "TemporalInterpError",
    "TemporalSeries",
    "TemporalInterpolator",
    "TemporalInterpResult",
    "EnergyAudit",
    "lagrange_weights",
    "convergence_study",
]


class TemporalInterpError(ValueError):
    """Invalid temporal-interpolation request (fail-closed)."""


# ---------------------------------------------------------------------------
# Lagrange basis on arbitrary abscissas
# ---------------------------------------------------------------------------

def lagrange_weights(nodes: Sequence[float], t: float) -> np.ndarray:
    """Exact Lagrange cardinal-basis weights at ``t`` for arbitrary ``nodes``.

    ``w_k = prod_{m != k} (t - x_m) / (x_k - x_m)``.  The weights are the unique
    polynomial interpolant weights of degree ``n-1`` through the nodes, they sum
    to one, and ``sum_k w_k f(x_k)`` is exact for any polynomial of degree
    ``< n``.  Fail-closed for coincident nodes.
    """
    x = np.asarray(nodes, dtype=float)
    if x.ndim != 1 or x.size == 0:
        raise TemporalInterpError("nodes must be a non-empty 1D sequence")
    if np.any(np.diff(x) <= 0.0):
        raise TemporalInterpError("nodes must be strictly increasing")
    n = x.size
    w = np.ones(n, dtype=float)
    for k in range(n):
        num = 1.0
        den = 1.0
        for m in range(n):
            if m == k:
                continue
            num *= float(t) - x[m]
            den *= x[k] - x[m]
        w[k] = num / den
    return w


# ---------------------------------------------------------------------------
# Sample container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TemporalSeries:
    """A time-ordered stack of field snapshots on a fixed spatial discretisation.

    Parameters
    ----------
    times : (n_t,) strictly increasing sample times.
    snapshots : (n_t, n_cells) field values at those times.
    weights : (n_cells,) optional cell weights (cell volumes / areas).  Used for
        the mass and energy audits; ``None`` means uniform unit weights.
    label : free-form tag carried into the audit records.
    """

    times: np.ndarray
    snapshots: np.ndarray
    weights: Optional[np.ndarray] = None
    label: str = ""

    def __post_init__(self) -> None:
        t = np.asarray(self.times, dtype=float)
        s = np.asarray(self.snapshots, dtype=float)
        if t.ndim != 1 or t.size == 0:
            raise TemporalInterpError("times must be a non-empty 1D array")
        if s.ndim != 2:
            raise TemporalInterpError("snapshots must be 2D (n_t, n_cells)")
        if s.shape[0] != t.size:
            raise TemporalInterpError(
                f"snapshots has {s.shape[0]} rows but {t.size} times were given"
            )
        if not np.all(np.isfinite(t)) or np.any(np.diff(t) <= 0.0):
            raise TemporalInterpError("times must be finite and strictly increasing")
        if not np.all(np.isfinite(s)):
            raise TemporalInterpError("snapshots contain non-finite values")
        w = None if self.weights is None else np.asarray(self.weights, dtype=float)
        if w is not None:
            if w.ndim != 1 or w.shape[0] != s.shape[1]:
                raise TemporalInterpError("weights must be 1D with one entry per cell")
            if np.any(w <= 0.0):
                raise TemporalInterpError("cell weights must be strictly positive")
        object.__setattr__(self, "times", t)
        object.__setattr__(self, "snapshots", s)
        if w is not None:
            object.__setattr__(self, "weights", w)

    @property
    def n_samples(self) -> int:
        return int(self.times.size)

    @property
    def n_cells(self) -> int:
        return int(self.snapshots.shape[1])

    @property
    def span(self) -> Tuple[float, float]:
        return float(self.times[0]), float(self.times[-1])

    def uniform_step(self) -> float:
        """Mean sample spacing (the ``dt`` of the series)."""
        return float((self.times[-1] - self.times[0]) / max(self.n_samples - 1, 1))

    def mass(self, field: np.ndarray) -> float:
        """Discrete global integral ``sum_c w_c f_c`` of a snapshot."""
        f = np.asarray(field, dtype=float)
        if self.weights is None:
            return float(f.sum())
        return float(np.dot(self.weights, f))

    def energy(self, field: np.ndarray) -> float:
        """Discrete quadratic functional ``sum_c w_c f_c^2`` (caller's "energy")."""
        f = np.asarray(field, dtype=float)
        if self.weights is None:
            return float(np.dot(f, f))
        return float(np.dot(self.weights, f * f))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TemporalInterpResult:
    """Interpolated field plus the audit of *how* it was produced."""

    field: np.ndarray
    time: float
    method: str
    stencil: Tuple[int, ...]
    weights: np.ndarray
    at_node: bool
    clamped_fraction: float

    @property
    def weight_sum_error(self) -> float:
        """``|sum_k w_k - 1|`` -- consistency (partition-of-unity) defect."""
        return float(abs(self.weights.sum() - 1.0))

    def to_dict(self) -> dict:
        return {
            "time": self.time,
            "method": self.method,
            "stencil": list(self.stencil),
            "weights": [float(w) for w in self.weights],
            "at_node": self.at_node,
            "weight_sum_error": self.weight_sum_error,
            "clamped_fraction": self.clamped_fraction,
        }


@dataclass(frozen=True)
class EnergyAudit:
    """Mass/energy comparison of an interpolated field against a reference."""

    time: float
    mass_interpolated: float
    mass_reference: float
    energy_interpolated: float
    energy_reference: float

    @property
    def mass_rel_error(self) -> float:
        return _rel(self.mass_interpolated, self.mass_reference)

    @property
    def energy_rel_error(self) -> float:
        return _rel(self.energy_interpolated, self.energy_reference)

    def to_dict(self) -> dict:
        return {
            "time": self.time,
            "mass_interpolated": self.mass_interpolated,
            "mass_reference": self.mass_reference,
            "mass_rel_error": self.mass_rel_error,
            "energy_interpolated": self.energy_interpolated,
            "energy_reference": self.energy_reference,
            "energy_rel_error": self.energy_rel_error,
        }


def _rel(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1e-300)
    return abs(a - b) / scale


# ---------------------------------------------------------------------------
# Interpolator
# ---------------------------------------------------------------------------

class TemporalInterpolator:
    """Temporal interpolation operator ``I_t`` on a :class:`TemporalSeries`.

    Parameters
    ----------
    series : sample container (fixed spatial discretisation).
    method : ``"nearest"`` | ``"linear"`` | ``"cubic"``.
    extrapolation : ``"fail"`` (default, fail-closed) | ``"hold"`` (clamp to the
        nearest endpoint sample) | ``"linear"`` (two-point linear extension).
    bounds : optional ``(lo, hi)`` pair.  ``None`` disables the limiter.  When
        enabled, interpolated values are clipped into the interval and the
        clamped fraction is reported (never silently).
    identity_tol : a request within this distance of a stored sample returns that
        sample unchanged (bit-exact), so the operator degenerates to the
        identity on a shared clock and has exactly zero round-trip error.
    """

    METHODS = ("nearest", "linear", "cubic")
    EXTRAPOLATIONS = ("fail", "hold", "linear")

    def __init__(
        self,
        series: TemporalSeries,
        method: str = "linear",
        *,
        extrapolation: str = "fail",
        bounds: Optional[Tuple[Optional[float], Optional[float]]] = None,
        identity_tol: float = 1e-12,
    ) -> None:
        if method not in self.METHODS:
            raise TemporalInterpError(
                f"unknown method {method!r}; expected one of {self.METHODS}"
            )
        if extrapolation not in self.EXTRAPOLATIONS:
            raise TemporalInterpError(
                f"unknown extrapolation {extrapolation!r}; "
                f"expected one of {self.EXTRAPOLATIONS}"
            )
        if identity_tol < 0.0:
            raise TemporalInterpError("identity_tol must be >= 0")
        self.series = series
        self.method = method
        self.extrapolation = extrapolation
        self.bounds = bounds
        self.identity_tol = float(identity_tol)

    # -- public API --------------------------------------------------------
    def __call__(self, t: float) -> TemporalInterpResult:
        return self.interpolate(t)

    def interpolate(self, t: float) -> TemporalInterpResult:
        t = float(t)
        if not math.isfinite(t):
            raise TemporalInterpError("target time must be finite")
        t0, t1 = self.series.span
        if t < t0 - self.identity_tol or t > t1 + self.identity_tol:
            t = self._handle_extrapolation(t, t0, t1)

        stencil, weights, at_node = self._stencil_weights(t)
        if at_node:
            field = self.series.snapshots[stencil[0]].copy()
        else:
            field = np.tensordot(weights, self.series.snapshots[list(stencil)], axes=(0, 0))

        clamped_fraction = 0.0
        if self.bounds is not None:
            lo, hi = self.bounds
            clipped = field
            if lo is not None:
                clipped = np.maximum(clipped, lo)
            if hi is not None:
                clipped = np.minimum(clipped, hi)
            if not np.array_equal(clipped, field):
                clamped_fraction = float(np.mean(clipped != field))
                field = clipped

        return TemporalInterpResult(
            field=field,
            time=t,
            method=self.method,
            stencil=tuple(int(i) for i in stencil),
            weights=np.asarray(weights, dtype=float),
            at_node=bool(at_node),
            clamped_fraction=clamped_fraction,
        )

    def interpolate_many(self, times: Iterable[float]) -> list:
        return [self.interpolate(t) for t in times]

    def energy_audit(self, t: float, reference_field: np.ndarray) -> EnergyAudit:
        """Mass/energy of the interpolant at ``t`` versus a reference field."""
        res = self.interpolate(t)
        return EnergyAudit(
            time=res.time,
            mass_interpolated=self.series.mass(res.field),
            mass_reference=self.series.mass(reference_field),
            energy_interpolated=self.series.energy(res.field),
            energy_reference=self.series.energy(reference_field),
        )

    def global_mass(self) -> np.ndarray:
        """Global integral of every stored snapshot (conservation audit)."""
        if self.series.weights is None:
            return self.series.snapshots.sum(axis=1)
        return self.series.snapshots @ self.series.weights

    # -- internals ---------------------------------------------------------
    def _handle_extrapolation(self, t: float, t0: float, t1: float) -> float:
        if self.extrapolation == "fail":
            raise TemporalInterpError(
                f"target time {t} outside sample span [{t0}, {t1}] "
                "(set extrapolation='hold'/'linear' to allow it)"
            )
        if self.extrapolation == "hold":
            return t0 if t < t0 else t1
        return t  # "linear": the stencil construction extends the end interval

    def _nearest_node(self, t: float) -> Optional[int]:
        idx = int(np.argmin(np.abs(self.series.times - t)))
        if abs(self.series.times[idx] - t) <= self.identity_tol:
            return idx
        return None

    def _stencil_weights(self, t: float) -> Tuple[Tuple[int, ...], np.ndarray, bool]:
        times = self.series.times
        n = times.size
        node = self._nearest_node(t)
        if node is not None:
            w = np.zeros(1)
            w[0] = 1.0
            return (node,), w, True

        if self.method == "nearest":
            idx = int(np.argmin(np.abs(times - t)))
            return (idx,), np.ones(1), False

        if n < 2:
            raise TemporalInterpError("linear interpolation needs >= 2 samples")

        # bracketing interval
        k = int(np.searchsorted(times, t, side="right")) - 1
        k = min(max(k, 0), n - 2)

        if self.method == "linear" or n < 4:
            stencil = (k, k + 1)
        else:
            start = min(max(k - 1, 0), n - 4)
            stencil = tuple(range(start, start + 4))
        weights = lagrange_weights(times[list(stencil)], t)
        return stencil, weights, False


# ---------------------------------------------------------------------------
# Convergence / energy studies (used by tests and by the acceptance runner)
# ---------------------------------------------------------------------------

def _profile_samples(profile: str, n: int, t_end: float, decay: float, corner: float) -> np.ndarray:
    """Samples of the test signal on ``n`` uniformly spaced instants."""
    t = np.linspace(0.0, t_end, n)
    if profile == "smooth":
        return np.cos(2.0 * math.pi * t / t_end)
    if profile == "piecewise_linear":
        # a C0 signal whose corner sits exactly on a sample: a linear operator
        # must reproduce it exactly (no order statement, an exactness statement)
        return np.abs(t - corner * t_end)
    if profile == "euler":
        # explicit (forward) Euler for dy/dt = -y/decay, i.e. a first-order
        # accurate-in-time trajectory -- the operative case in coupling.
        dt = t[1] - t[0] if n > 1 else t_end
        y = np.empty(n)
        y[0] = 1.0
        for k in range(1, n):
            y[k] = y[k - 1] - dt * y[k - 1] / decay
        return y
    raise TemporalInterpError(f"unknown profile {profile!r}")


def _profile_exact(profile: str, t: np.ndarray, t_end: float, decay: float, corner: float) -> np.ndarray:
    t = np.asarray(t, dtype=float)
    if profile == "smooth":
        return np.cos(2.0 * math.pi * t / t_end)
    if profile == "piecewise_linear":
        return np.abs(t - corner * t_end)
    if profile == "euler":
        return np.exp(-t / decay)
    raise TemporalInterpError(f"unknown profile {profile!r}")


def convergence_study(
    profile: str = "smooth",
    *,
    method: str = "linear",
    n_levels: Sequence[int] = (33, 65, 129, 257, 513),
    t_end: float = 1.0,
    decay: float = 1.0,
    corner: float = 0.5,
    phase: float = 0.37,
) -> dict:
    """Measure the temporal-interpolation order on a refinement sequence.

    The fine solver samples the profile on ``n`` points over ``[0, t_end]``; the
    coarse solver requests the field at the *same fractional position* ``phase``
    inside every fine interval, i.e. ``t_j = (j + phase) * dt_f``.  Using a fixed
    phase makes the error measure independent of the accident of a request
    landing on a sample, so the fitted order is stable.

    Returns
    -------
    dict with ``profile``, ``method``, ``dt``, ``err``, ``order`` (least-squares
    slope of ``log err`` vs ``log dt``) and ``zero_error`` (True when the
    operator reproduces the profile exactly).
    """
    dts, errs = [], []
    for n in n_levels:
        n = int(n)
        if n < 5:
            raise TemporalInterpError("n_levels entries must be >= 5")
        t = np.linspace(0.0, t_end, n)
        dt = t[1] - t[0]
        snap = _profile_samples(profile, n, t_end, decay, corner)
        series = TemporalSeries(times=t, snapshots=snap.reshape(n, 1))
        interp = TemporalInterpolator(series, method=method, extrapolation="fail")
        targets = (np.arange(1, n - 2) + phase) * dt
        worst = 0.0
        for tq in targets:
            got = float(interp.interpolate(float(tq)).field[0])
            ref = float(_profile_exact(profile, np.array([tq]), t_end, decay, corner)[0])
            worst = max(worst, abs(got - ref))
        dts.append(dt)
        errs.append(worst)

    dts_a = np.asarray(dts)
    errs_a = np.asarray(errs)
    zero_error = bool(np.max(errs_a) <= 1e-13)
    if zero_error:
        order = float("inf")
    else:
        order = float(np.polyfit(np.log(dts_a), np.log(errs_a), 1)[0])
    return {
        "profile": profile,
        "method": method,
        "dt": [float(x) for x in dts_a],
        "err": [float(x) for x in errs_a],
        "order": order,
        "zero_error": zero_error,
        "phase": phase,
    }
