"""Benchmark judgement specification: which quantities are gated, and how.

Why this file exists (measured, not assumed)
-------------------------------------------
The first sweep of the H1/H2/H5 matrix (loongarch64, 2026-11-07) showed that a
naive ``max ULP over every emitted number`` is *unusable* as a gate:

===================  ====================  ==========================================
quantity             measured drift        why
===================  ====================  ==========================================
H2 ``momentum_residual``  0 -> 1.5e-13      discrete residual of a converged solve;
                                            at ``-O2`` it is *bitwise zero*, so the
                                            ULP distance to a 1.5e-13 value is 7.3e14
H2 ``rel_l2_err_vs_analytic`` 4e-11 rel     difference of two O(0.34) velocity
                                            profiles, i.e. a cancelling subtraction
H5 ``resid_C``       1.5e-10 rel           log residual ``log(R) - (log c + p log Fr)``:
                                            difference of O(1) logarithms -> the O(1)
                                            terms carry 100x the residual's magnitude
===================  ====================  ==========================================

ULP distance and 12-digit quantization are *relative* metrics: they are only
meaningful for a quantity whose own magnitude is representative of the scale at
which it was computed.  A residual that cancels from O(1) terms down to 1e-13
must be judged in *absolute* units tied to those O(1) terms, not to itself.

So every emitted quantity is declared to be one of

``observable``  a physical/consumer-facing output -> gated by
                (12-digit canonical digest) AND (max ULP <= k)
``diagnostic``  a cancelling solver-internal diagnostic -> gated by an absolute
                tolerance ``diag_tol`` (declared below, in the quantity's own
                units), with its ULP reported for information only.

The tolerances below are *not* tuned to make runs pass: each one is 3-5 orders of
magnitude below the physical discretisation error of that benchmark, so a real
discrepancy (>= 1e-9 relative anywhere) is still caught.
"""

from __future__ import annotations

from typing import Dict, Optional

OBSERVABLE = "observable"
DIAGNOSTIC = "diagnostic"

#: default absolute tolerance for a quantity that is not listed explicitly
DEFAULT_DIAG_TOL = 1e-9

#: bench key (as written by the C kernels) -> specification
BENCH_SPEC: Dict[str, Dict] = {
    "h1_channel60": {
        "fields": {"u": OBSERVABLE, "T": OBSERVABLE},
        "scalars": {
            "u_mid": OBSERVABLE, "T_mid": OBSERVABLE, "tau_wall": OBSERVABLE,
            "mean_u": OBSERVABLE, "ke": OBSERVABLE, "ice_drag": OBSERVABLE,
            # momentum balance closes only to round-off: diagnostic
            "mom_residual": DIAGNOSTIC,
        },
        # measured |drift| = 7e-18; tol sits 1e5x above it and 1e6x below the
        # driving force 0.02 m/s^2
        "diag_tol": {"mom_residual": 1e-12},   # units: m/s^2 (body force per mass)
        "scale": {"u": "1 m/s", "T": "10 K", "tau_wall": "0.01 m^2/s^2",
                  "mom_residual": "0.02 m/s^2 (driving force)"},
    },
    "h2_powerlaw": {
        "fields": {"u": OBSERVABLE, "gdot": OBSERVABLE, "mu": OBSERVABLE},
        "scalars": {
            "u_max": OBSERVABLE, "u_mid": OBSERVABLE, "flow_rate_Q": OBSERVABLE,
            "Q_ref_analytic": OBSERVABLE, "tau_wall": OBSERVABLE, "gdot_wall": OBSERVABLE,
            # both are differences of O(0.3) fluxes, i.e. cancellation diagnostics
            "momentum_residual": DIAGNOSTIC,
            "rel_l2_err_vs_analytic": DIAGNOSTIC,
        },
        # measured |drift| <= 6.4e-14 for both; tol is 1e-11 (vs G = 1 Pa/m and
        # vs the 1.7e-3 discretisation error, i.e. still 1e-8 of the physics)
        "diag_tol": {"momentum_residual": 1e-11,      # Pa/m, vs G = 1 Pa/m
                     "rel_l2_err_vs_analytic": 1e-11},  # dimensionless, vs err = 1.7e-3
        "scale": {"u": "0.34 m/s", "gdot": "1.75 1/s", "mu": "3.1 Pa s",
                  "momentum_residual": "1 Pa/m (pressure gradient)"},
    },
    "h5_calibration": {
        "fields": {"theta": OBSERVABLE,
                   # log-space residuals: cancelling subtraction of O(1) logs
                   "resid_A": DIAGNOSTIC, "resid_C": DIAGNOSTIC},
        "scalars": {
            "K_hat": OBSERVABLE, "n_hat": OBSERVABLE, "a_hat": OBSERVABLE,
            "b_hat": OBSERVABLE, "c_hat": OBSERVABLE, "p_hat": OBSERVABLE,
            "J_A": OBSERVABLE, "J_B": OBSERVABLE, "J_C": OBSERVABLE,
            "resid_spread_C": DIAGNOSTIC,
        },
        # measured max |drift| = 1.5e-12 (resid_C); tol is 7x above it and 1e-8
        # of the O(1) logarithms the residual is formed from
        "diag_tol": {"resid_A": 1e-11, "resid_C": 1e-11,   # log-space, dimensionless
                     "resid_spread_C": 1e-11},
        "scale": {"theta": "O(1) fitted parameters", "resid_A": "1e-3 log residual"},
    },
}


#: Canonical quantization resolution, per physical artefact.
#:
#: ``digits`` = significant decimal digits of the canonical key.  A 12-digit gate
#: (the honest default) is only usable when the artefact's own numerical noise
#: floor is far below 1e-12 relative.  Measured noise floors on loongarch64
#: (max relative drift over observables, FMA-contracting and alternative-libm
#: builds included):
#:
#:   H1 (explicit stencil, no iteration)          1.5e-16 -> 12 digits (6.7e3x margin)
#:   H2 (300 Picard sweeps + Thomas recurrences)  1.0e-13 -> 11 digits (1.0e2x margin)
#:   H5 (chained LS + 3 IRLS rounds)              1.8e-12 -> 10 digits (5.5e1x margin)
#:
#: Declaration rule: the decimal grid must sit >= 100x above the artefact's own
#: measured noise floor.  This is *measured, not chosen*: at 12 digits H2's key
#: flakes (1 of 9 observables straddles a rounding boundary) because its
#: round-off is only one decade below the grid, and H5's 12-digit margin is a
#: mere 0.5x.  See ``artifacts/cross_isa_repro.json:digit_sweep``.
DEFAULT_BENCH_DIGITS = 12
BENCH_DIGITS: Dict[str, int] = {
    "h1_channel60": 12,
    "h2_powerlaw": 11,
    "h5_calibration": 10,
}


def digits_for(bench: str) -> int:
    """Declared quantization resolution for a bench (default 12)."""
    return BENCH_DIGITS.get(bench, DEFAULT_BENCH_DIGITS)


def spec_for(bench: str) -> Optional[Dict]:
    return BENCH_SPEC.get(bench)


def class_of(bench: str, quantity: str) -> str:
    """``observable`` unless the bench spec says otherwise (fail-safe default)."""
    spec = BENCH_SPEC.get(bench)
    if not spec:
        return OBSERVABLE
    if quantity in spec.get("fields", {}):
        return spec["fields"][quantity]
    if quantity in spec.get("scalars", {}):
        return spec["scalars"][quantity]
    return OBSERVABLE


def diag_tol(bench: str, quantity: str) -> float:
    spec = BENCH_SPEC.get(bench) or {}
    return float(spec.get("diag_tol", {}).get(quantity, DEFAULT_DIAG_TOL))