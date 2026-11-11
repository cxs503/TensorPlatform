"""Negative-control battery: a gate that cannot reject is a formatter.

A valid bundle is mutated in sixteen ways, each violating exactly one
admissibility property, and the gate must reject every mutation **with the
expected reason code**.  The battery is what separates a real ingest gate from
a green check:

* a gate that accepts everything fails all sixteen controls;
* a gate that rejects everything fails the *positive* control (the untouched
  bundle must be ingested);
* a gate that rejects for the wrong reason fails the code assertion — for
  instance a "reject on any anomaly" stub would answer ``NONFINITE`` where the
  control expects ``BALANCE_VIOLATION``.

Most mutations are re-sealed after editing (so the rejection must come from the
physics, not from the hash); ``seal_tamper`` and ``no_seal`` deliberately leave
the hash broken to exercise the integrity path.

No domain semantics.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .gate import ConservationGate, GatePolicy, GateVerdict
from .ledger import ConservationLedger, LedgerEntry

_MISSING = object()


def clone(ledgers: Sequence[ConservationLedger]) -> List[ConservationLedger]:
    return [ConservationLedger.from_dict(copy.deepcopy(lg.as_dict())) for lg in ledgers]


def _face(ledgers: Sequence[ConservationLedger], name: str) -> ConservationLedger:
    for lg in ledgers:
        if lg.face == name:
            return lg
    raise KeyError(name)


def _entry(lg: ConservationLedger, name: str) -> LedgerEntry:
    e = lg.entry(name)
    if e is None:
        raise KeyError(f"{lg.face}.{name}")
    return e


def _scale(vec: Any, factor: float) -> Any:
    if isinstance(vec, (int, float)):
        return float(vec) * factor
    if isinstance(vec, list):
        return [_scale(v, factor) for v in vec]
    return vec


# --------------------------------------------------------------------------- #
# mutations: name -> (mutator, expected reason code)
# --------------------------------------------------------------------------- #
def _mass_off(bundle):
    lg = _face(bundle, "fem_dem")
    e = _entry(lg, "mass")
    e.target = e.target * 1.01
    return bundle


def _drop_inertia(bundle):
    lg = _face(bundle, "fem_dem")
    lg.entries = [e for e in lg.entries if e.name != "inertia_tensor"]
    return bundle


def _nan_momentum(bundle):
    lg = _face(bundle, "fem_dem")
    e = _entry(lg, "momentum")
    e.source = [float("nan"), e.source[1], e.source[2]]
    return bundle


def _asymmetric(bundle):
    lg = _face(bundle, "fem_dem")
    e = _entry(lg, "inertia_tensor")
    e.target = [list(row) for row in e.target]
    e.target[0][1] = e.target[0][1] + 1e-3 * max(1.0, abs(e.target[0][0]))
    return bundle


def _non_spd(bundle):
    lg = _face(bundle, "fem_dem")
    e = _entry(lg, "inertia_tensor")
    t = [list(row) for row in e.target]
    t[0][0] = -abs(t[0][0]) - 1.0
    e.target = t
    return bundle


def _energy_lost(bundle):
    lg = _face(bundle, "fem_dem")
    e = _entry(lg, "energy")
    e.target = e.target * 0.999
    return bundle


def _rollback_inflated(bundle):
    lg = _face(bundle, "clock")
    _entry(lg, "optimistic_rollback_rate").source = 0.12
    return bundle


def _deadlock(bundle):
    lg = _face(bundle, "clock")
    _entry(lg, "deadlocks").source = 1.0
    return bundle


def _future_read(bundle):
    lg = _face(bundle, "clock")
    _entry(lg, "future_reads").source = 3.0
    return bundle


def _not_monotone(bundle):
    lg = _face(bundle, "clock")
    _entry(lg, "global_time_monotone").source = False
    return bundle


def _flux_leak(bundle):
    lg = _face(bundle, "lbm_fvm")
    e = _entry(lg, "interface_mass_flux")
    e.target = e.target * (1.0 + 1e-4)     # the O(1e-4) inverse-distance error
    return bundle


def _doc_relaxes_tolerance(bundle):
    """Producer edits wrecked numbers *and* claims a loose tolerance."""
    for lg in bundle:
        for e in lg.entries:
            e.tolerance = 1.0
    lg = _face(bundle, "fem_dem")
    _entry(lg, "momentum").target = _scale(_entry(lg, "momentum").target, 1.02)
    return bundle


def _duplicate_entry(bundle):
    lg = _face(bundle, "fem_dem")
    e = _entry(lg, "mass")
    lg.entries.append(LedgerEntry("mass", e.kind, e.source, e.target, e.tolerance))
    return bundle


def _missing_face(bundle):
    return [lg for lg in bundle if lg.face != "lbm_fvm"]


def _missing_provenance(bundle):
    for lg in bundle:
        lg.producer = {}
    return bundle


def _seal_tamper(bundle):
    lg = _face(bundle, "fem_dem")
    _entry(lg, "volume").target = _entry(lg, "volume").target * 1.5
    return bundle  # deliberately NOT re-sealed


def _no_seal(bundle):
    for lg in bundle:
        lg.seal = ""
    return bundle


MUTATIONS: Tuple[Tuple[str, Callable, str, bool], ...] = (
    # name, mutator, expected code, reseal?
    ("mass_target_plus_1pct", _mass_off, "BALANCE_VIOLATION", True),
    ("drop_inertia_ledger", _drop_inertia, "MISSING_ENTRY", True),
    ("nan_momentum_source", _nan_momentum, "NONFINITE", True),
    ("asymmetric_inertia", _asymmetric, "ASYMMETRIC_TENSOR", True),
    ("non_spd_inertia", _non_spd, "NON_SPD_TENSOR", True),
    ("energy_lost_1e-3", _energy_lost, "BALANCE_VIOLATION", True),
    ("rollback_rate_12pct", _rollback_inflated, "BOUND_VIOLATION", True),
    ("deadlock_counted", _deadlock, "BOUND_VIOLATION", True),
    ("future_read_counted", _future_read, "BOUND_VIOLATION", True),
    ("clock_not_monotone", _not_monotone, "FLAG_FALSE", True),
    ("flux_leak_1e-4", _flux_leak, "BALANCE_VIOLATION", True),
    ("producer_relaxes_tolerance", _doc_relaxes_tolerance, "BALANCE_VIOLATION", True),
    ("duplicate_mass_entry", _duplicate_entry, "DUPLICATE_ENTRY", True),
    ("missing_flow_face", _missing_face, "MISSING_FACE", True),
    ("missing_provenance", _missing_provenance, "MISSING_PROVENANCE", True),
    ("edited_after_seal", _seal_tamper, "SEAL_MISMATCH", False),
    ("never_sealed", _no_seal, "MISSING_SEAL", False),
)


def run_controls(bundle: Sequence[ConservationLedger],
                 policy: Optional[GatePolicy] = None,
                 gate: Optional[ConservationGate] = None) -> Dict[str, Any]:
    """Run the positive control plus the whole negative-control battery."""
    if gate is None:
        if policy is None:
            from .gate import cross_library_policy

            policy = cross_library_policy()
        gate = ConservationGate(policy)
    positive = gate.adjudicate(clone(bundle))
    rows: List[Dict[str, Any]] = []
    for name, mutator, expected, reseal in MUTATIONS:
        candidate = mutator(clone(bundle))
        if reseal:
            for lg in candidate:
                lg.seal_ledger()
        verdict = gate.adjudicate(candidate)
        codes = verdict.codes()
        rows.append({
            "name": name,
            "expected_code": expected,
            "observed_codes": sorted(set(codes)),
            "rejected": not verdict.accepted,
            "code_matched": expected in codes,
            "ok": (not verdict.accepted) and (expected in codes),
        })
    n_ok = sum(1 for r in rows if r["ok"])
    return {
        "positive_accepted": bool(positive.accepted),
        "positive_failures": positive.failures,
        "controls": rows,
        "n_controls": len(rows),
        "n_rejected_with_expected_code": n_ok,
        "all_ok": bool(positive.accepted) and n_ok == len(rows),
        "false_accept_rate": (len(rows) - sum(1 for r in rows if r["rejected"])) / len(rows),
        "specificity": n_ok / len(rows),
    }


def run_mutator_substitution(bundle: Sequence[ConservationLedger], *,
                             value: Any, face: str, entry: str,
                             expected: str = "BALANCE_VIOLATION",
                             policy: Optional[GatePolicy] = None,
                             gate: Optional[ConservationGate] = None) -> Dict[str, Any]:
    """Substitute a *real* alternative operator's numbers (not a scaled fake).

    Used for the two physically meaningful substitutions: the sphere-equivalent
    inertia ledger and the inverse-distance interface flux.  Scaling a number by
    1.01 proves the tolerance comparison works; substituting the actual output
    of the shortcut model proves the gate recognises the *physical* failure mode.
    """
    gate = gate or ConservationGate(policy)
    candidate = clone(bundle)
    lg = _face(candidate, face)
    e = _entry(lg, entry)
    e.target = value
    lg.seal_ledger()
    verdict = gate.adjudicate(candidate)
    codes = verdict.codes()
    return {
        "face": face,
        "entry": entry,
        "expected_code": expected,
        "observed_codes": sorted(set(codes)),
        "rejected": not verdict.accepted,
        "code_matched": expected in codes,
        "ok": (not verdict.accepted) and (expected in codes),
    }


__all__ = [
    "MUTATIONS",
    "clone",
    "run_controls",
    "run_mutator_substitution",
]