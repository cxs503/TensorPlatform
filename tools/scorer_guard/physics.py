"""Closed-form physics invariants used as *independent* cross-checks (IN25-T5).

Every function here has a closed form or a definitional relation whose value can
be re-derived without the scorer: that is what makes a "known solution must earn
a known score" red-team case meaningful.  Conservation-law / invariance checks
(:func:`froude_invariance_residual`, :func:`thrust_balance_residual`,
:func:`additivity_residual`, :func:`hydrostatic_mass_residual`) are the physics
gates a submission has to survive before it earns physics-based reward.
"""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Any, Dict, Mapping

# tolerance for exact algebraic identities (additivity, hydrostatic mass)
EXACT_TOL = 1.0e-6
# tolerance for scaling-invariance / equilibrium checks on submitted numbers
INVARIANCE_TOL = 1.0e-3


def ittc1957_cf(re: float) -> float:
    """ITTC-1957 friction line, Cf = 0.075 / (log10 Re - 2)^2."""
    lr = math.log10(re)
    if lr <= 2.0:
        raise ValueError("ITTC-1957 line is only defined for log10(Re) > 2")
    return 0.075 / (lr - 2.0) ** 2


def ittc1957_cf_exact(re: float) -> float:
    """Same value from the exact rational 0.075 = 3/40 (independent recheck).

    ``log10(Re)`` is taken as exact when ``Re`` is a power of ten.
    """
    k = math.log10(re)
    kr = round(k)
    if abs(k - kr) < 1e-12:
        frac = Fraction(3, 40) / Fraction(int(kr) - 2) ** 2
        return float(frac)
    return ittc1957_cf(re)


def blasius_plate_cf(re: float) -> float:
    """Blasius laminar flat plate, mean skin friction, both sides: 1.328/sqrt(Re)."""
    return 1.328 / math.sqrt(re)


def schoenherr_explicit_cf(re: float) -> float:
    """Explicit approximation of the Schoenherr line: 0.455 / (log10 Re)^2.58."""
    return 0.455 / math.log10(re) ** 2.58


def schoenherr_implicit_cf(re: float, tol: float = 1e-14, iters: int = 200) -> float:
    """Root of the Karman-Schoenherr relation 1/sqrt(Cf) = 4.13 log10(Re Cf).

    Solved by bisection on ``log10 Cf`` in [-7, -1]; monotone in Cf over that
    bracket, so bisection is bracket-safe (no derivative needed).
    """
    def resid(cf: float) -> float:
        return 1.0 / math.sqrt(cf) - 4.13 * math.log10(re * cf)

    lo, hi = math.log10(1e-7), math.log10(2e-1)
    f_lo = resid(10.0 ** lo)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        f_mid = resid(10.0 ** mid)
        if abs(f_mid) < tol or abs(hi - lo) < 1e-15:
            return 10.0 ** mid
        if (f_lo < 0.0) == (f_mid < 0.0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return 10.0 ** (0.5 * (lo + hi))


def schoenherr_newton_cf(re: float, cf0: float = 2.0e-3) -> float:
    """Newton re-derivation of the implicit Schoenherr root (independent method)."""
    cf = cf0
    for _ in range(100):
        f = 1.0 / math.sqrt(cf) - 4.13 * math.log10(re * cf)
        df = -0.5 * cf ** -1.5 - 4.13 / (cf * math.log(10.0))
        step = f / df
        cf -= step
        if abs(step) < 1e-16 * max(cf, 1e-12):
            break
    return cf


def recompute_anchor(rec: Mapping[str, Any]) -> Dict[str, Any]:
    """Independently re-derive an anchor value from its formula id.

    Returns ``{"value", "abs_dev", "rel_dev", "method"}`` against the stored
    anchor value; ``rel_dev`` is the anchor's own audit number.
    """
    src = rec.get("source") or {}
    fid = src.get("formula_id")
    cov = rec.get("covariates") or {}
    if fid == "ittc1957_cf":
        re = float(cov["Re"])
        val = ittc1957_cf(re)
        # exact rational: log10(Re) = 6, 7, 9 -> Cf = 3 / (40 (k-2)^2)
        k = int(round(math.log10(re)))
        exact = float(Fraction(3, 40) / Fraction(k - 2) ** 2)
        method = "exact rational 3/(40 (log10Re-2)^2)"
        val2 = exact
    elif fid == "blasius_plate_cf":
        re = float(cov["Re"])
        val = blasius_plate_cf(re)
        # independent numeric integration of 0.664/sqrt(x) local coefficient
        n = 200000
        h = 1.0 / n
        acc = 0.0
        for i in range(n):
            xn = (i + 0.5) * h
            acc += 0.664 / math.sqrt(xn) * h
        val2 = acc
        method = "local 0.664/sqrt(x) integrated (midpoint, n=200000)"
    elif fid == "schoenherr_implicit_cf":
        re = float(cov["Re"])
        val = schoenherr_implicit_cf(re)
        val2 = schoenherr_newton_cf(re)
        method = "Newton solve of 1/sqrt(Cf)=4.13 log10(Re Cf)"
    else:
        raise ValueError(f"no independent recheck registered for formula_id={fid!r}")

    stored = rec.get("value")
    abs_dev = abs(float(stored) - float(val))
    rel_dev = abs_dev / abs(float(val)) if val else float("inf")
    rel_dev2 = abs(float(stored) - float(val2)) / abs(float(val2)) if val2 else float("inf")
    return {
        "value": val,
        "recheck_value": val2,
        "abs_dev": abs_dev,
        "rel_dev": rel_dev,
        "rel_dev_vs_recheck": rel_dev2,
        "method": method,
    }


# --------------------------------------------------------------------------- #
# Physics / conservation gates
# --------------------------------------------------------------------------- #
def froude_invariance_residual(cw_model: float, cw_ship: float) -> float:
    """Wave-resistance coefficient is Froude invariant: Cw_model == Cw_ship.

    Returns the relative residual; exact Froude scaling gives ~0.
    """
    if cw_ship == 0.0:
        return abs(cw_model)
    return abs(cw_model - cw_ship) / abs(cw_ship)


def thrust_balance_residual(thrust: float, thrust_deduction: float, resistance: float) -> float:
    """Self-propulsion equilibrium T (1 - t) = R_T; returns relative residual."""
    if resistance == 0.0:
        return abs(thrust * (1.0 - thrust_deduction))
    return abs(thrust * (1.0 - thrust_deduction) - resistance) / abs(resistance)


def additivity_residual(ct: float, cf: float, form_factor: float, cw: float) -> float:
    """ITTC-78 decomposition Ct = (1+k) Cf + Cw must close exactly."""
    pred = (1.0 + form_factor) * cf + cw
    if ct == 0.0:
        return abs(pred)
    return abs(pred - ct) / abs(ct)


def hydrostatic_mass(rho: float, cb: float, length: float, beam: float, draught: float) -> float:
    """Displacement mass Delta = rho Cb L B T."""
    return rho * cb * length * beam * draught


def hydrostatic_mass_residual(
    rho: float, cb: float, length: float, beam: float, draught: float, delta_reported: float
) -> float:
    """Relative residual of a reported displacement against rho Cb L B T."""
    pred = hydrostatic_mass(rho, cb, length, beam, draught)
    if pred == 0.0:
        return abs(delta_reported)
    return abs(delta_reported - pred) / abs(pred)


PHYSICS_INVARIANTS = {
    "froude_invariance": {
        "formula": "Cw_model = Cw_ship (Froude scaling invariance)",
        "tol": INVARIANCE_TOL,
        "keys": ("cw_model", "cw_ship"),
    },
    "thrust_balance": {
        "formula": "T (1 - t) = R_T (self-propulsion equilibrium)",
        "tol": INVARIANCE_TOL,
        "keys": ("thrust", "thrust_deduction", "resistance"),
    },
    "additivity": {
        "formula": "Ct = (1 + k) Cf + Cw (ITTC-78 decomposition closes exactly)",
        "tol": EXACT_TOL,
        "keys": ("ct", "cf", "form_factor", "cw"),
    },
    "hydrostatic_mass": {
        "formula": "Delta = rho Cb L B T (displacement identity)",
        "tol": EXACT_TOL,
        "keys": ("rho", "cb", "length", "beam", "draught", "delta_reported"),
    },
}


def check_claims(claims: Mapping[str, Any]) -> list:
    """Evaluate submitted physics claims; return a list of violations.

    Each violation is ``{"name", "residual", "tol", "formula"}``.  A claim whose
    key set is incomplete is reported as ``MALFORMED`` (fail-closed).
    """
    out = []
    for name, spec in PHYSICS_INVARIANTS.items():
        claim = claims.get(name)
        if claim is None:
            continue
        keys = spec["keys"]
        if not isinstance(claim, Mapping) or not all(k in claim for k in keys):
            out.append({"name": name, "residual": float("inf"), "tol": spec["tol"],
                        "formula": spec["formula"], "malformed": True})
            continue
        if name == "froude_invariance":
            resid = froude_invariance_residual(claim["cw_model"], claim["cw_ship"])
        elif name == "thrust_balance":
            resid = thrust_balance_residual(
                claim["thrust"], claim["thrust_deduction"], claim["resistance"]
            )
        elif name == "additivity":
            resid = additivity_residual(
                claim["ct"], claim["cf"], claim["form_factor"], claim["cw"]
            )
        else:
            resid = hydrostatic_mass_residual(
                claim["rho"], claim["cb"], claim["length"], claim["beam"],
                claim["draught"], claim["delta_reported"],
            )
        if not math.isfinite(resid) or resid > spec["tol"]:
            out.append({"name": name, "residual": resid, "tol": spec["tol"],
                        "formula": spec["formula"], "malformed": False})
    return out