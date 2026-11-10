"""Energy ledger: invariant bookkeeping across checkpoint boundaries.

The ledger does not know any physics.  It owns a registry of *functionals* --
``fn(bundle) -> float`` -- and answers three questions that a long coupled run
must be able to answer at any moment:

1. **What is the invariant now?**  :meth:`EnergyLedger.measure` -> per-functional
   values, recorded inside every checkpoint manifest.
2. **Did saving/restoring perturb it?**  :meth:`EnergyLedger.check_boundary`,
   relative residual ``|E_after − E_before| / |E_before|`` with the acceptance
   threshold of ``1e-10``.  A bit-exact store must return exactly ``0``; a
   non-zero residual is proof that the serialisation path is not lossless.
3. **Did the run drift while nobody was watching?**  :meth:`EnergyLedger.check_drift`,
   ``|E_last − E_first| / |E_first|`` over a restart (threshold ``1e-3`` = 0.1 %).

The same code therefore *detects* a dissipative configuration instead of
blessing it: the negative-control archetype in
:mod:`tools.multisolver_ckpt.models` is deliberately damped and the ledger
flags it (see the report), which is what makes the ``0`` residuals on the
conservative archetypes meaningful rather than tautological.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Sequence, Tuple

import numpy as np

from .protocol import StateBundle

Functional = Callable[[StateBundle], float]

ACCEPT_REL_RESIDUAL = 1e-10   # AC2: checkpoint-boundary invariant residual
ACCEPT_REL_DRIFT = 1e-3       # AC2: 0.1 % drift over a 1000-step restart
_TINY = 1e-300


class EnergyLedgerError(RuntimeError):
    """The ledger is misconfigured or was asked about an unknown functional."""


@dataclass
class EnergyReading:
    step: int
    values: Dict[str, float]
    primary: str

    @property
    def total(self) -> float:
        return float(self.values[self.primary])

    def to_manifest(self) -> Dict[str, Any]:
        return {
            "step": int(self.step),
            "primary": self.primary,
            "values_hex": {k: float(v).hex() for k, v in sorted(self.values.items())},
        }

    @classmethod
    def from_manifest(cls, payload: Mapping[str, Any]) -> "EnergyReading":
        return cls(
            step=int(payload.get("step", -1)),
            values={k: float.fromhex(v) for k, v in dict(payload.get("values_hex") or {}).items()},
            primary=str(payload.get("primary", "E_total")),
        )


@dataclass
class GateReport:
    """Verdict of one ledger comparison."""

    kind: str                      # "boundary" | "drift"
    ok: bool
    rel_tol: float
    residuals: Dict[str, float] = field(default_factory=dict)
    gate: Tuple[str, ...] = ()
    reference: Dict[str, float] = field(default_factory=dict)
    current: Dict[str, float] = field(default_factory=dict)
    worst: float = 0.0
    primary_residual: float = 0.0
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "ok": bool(self.ok),
            "rel_tol": float(self.rel_tol),
            "residuals": {k: float(v) for k, v in sorted(self.residuals.items())},
            "gate": list(self.gate),
            "reference": {k: float(v) for k, v in sorted(self.reference.items())},
            "current": {k: float(v) for k, v in sorted(self.current.items())},
            "worst": float(self.worst),
            "primary_residual": float(self.primary_residual),
            "detail": self.detail,
        }


class EnergyLedger:
    """Registry of invariants + acceptance gates."""

    def __init__(self, functionals: Mapping[str, Functional] | None = None, *,
                 primary: str = "E_total",
                 gate: Sequence[str] | None = None,
                 boundary_rel_tol: float = ACCEPT_REL_RESIDUAL,
                 drift_rel_tol: float = ACCEPT_REL_DRIFT) -> None:
        self._functionals: Dict[str, Functional] = {}
        self.primary = str(primary)
        self.boundary_rel_tol = float(boundary_rel_tol)
        self.drift_rel_tol = float(drift_rel_tol)
        for name, fn in (functionals or {}).items():
            self.register(name, fn)
        if not self._functionals:
            raise EnergyLedgerError("a ledger needs at least one functional")
        if self.primary not in self._functionals:
            raise EnergyLedgerError(
                f"primary functional {self.primary!r} not registered (have {sorted(self._functionals)})"
            )
        # ``gate`` distinguishes invariants from diagnostics: every functional is
        # measured and reported, but only the gated ones can fail the acceptance
        # check.  (Per-solver sub-energies legitimately move at coupling
        # exchanges; the total must not.)
        self.gate: Tuple[str, ...] = tuple(gate) if gate is not None else tuple(sorted(self._functionals))
        unknown = [g for g in self.gate if g not in self._functionals]
        if unknown:
            raise EnergyLedgerError(f"gated functionals not registered: {unknown}")
        if not self.gate:
            raise EnergyLedgerError("at least one functional must be gated")

    # -- registry --------------------------------------------------------- #
    def register(self, name: str, fn: Functional, *, replace: bool = False) -> None:
        key = str(name)
        if key in self._functionals and not replace:
            raise EnergyLedgerError(f"functional {key!r} already registered (use replace=True)")
        if not callable(fn):
            raise EnergyLedgerError(f"functional {key!r} is not callable")
        self._functionals[key] = fn

    @property
    def names(self) -> Sequence[str]:
        return tuple(sorted(self._functionals))

    def __contains__(self, name: object) -> bool:
        return name in self._functionals

    # -- measurement ------------------------------------------------------ #
    def measure(self, bundle: StateBundle) -> EnergyReading:
        values: Dict[str, float] = {}
        for name in sorted(self._functionals):
            raw = self._functionals[name](bundle)
            value = float(raw)
            if not math.isfinite(value):
                raise EnergyLedgerError(f"functional {name!r} returned non-finite value {raw!r}")
            values[name] = value
        return EnergyReading(step=bundle.step, values=values, primary=self.primary)

    # -- gates ------------------------------------------------------------ #
    def check_boundary(self, before: EnergyReading, after: EnergyReading,
                       *, rel_tol: float | None = None) -> GateReport:
        return self._compare("boundary", before, after, self.boundary_rel_tol if rel_tol is None else rel_tol)

    def check_drift(self, first: EnergyReading, last: EnergyReading,
                    *, rel_tol: float | None = None) -> GateReport:
        return self._compare("drift", first, last, self.drift_rel_tol if rel_tol is None else rel_tol)

    def _compare(self, kind: str, ref: EnergyReading, cur: EnergyReading, rel_tol: float) -> GateReport:
        missing = set(self._functionals) - set(cur.values)
        if missing:
            raise EnergyLedgerError(f"current reading lacks functionals {sorted(missing)}")
        missing = set(self._functionals) - set(ref.values)
        if missing:
            raise EnergyLedgerError(f"reference reading lacks functionals {sorted(missing)}")
        residuals: Dict[str, float] = {}
        for name in sorted(self._functionals):
            a = ref.values[name]
            b = cur.values[name]
            denom = abs(a) if abs(a) > _TINY else _TINY
            residuals[name] = abs(b - a) / denom
        worst_name = max(self.gate, key=lambda k: residuals[k])
        worst = residuals[worst_name]
        primary_residual = residuals[self.primary]
        ok = all(residuals[k] <= rel_tol for k in self.gate)
        detail = (
            f"{kind}: primary {self.primary}={ref.values[self.primary]:.17g} -> "
            f"{cur.values[self.primary]:.17g}, rel residual {primary_residual:.3e} "
            f"(worst {worst_name} {worst:.3e}) vs tol {rel_tol:.1e} -> {'PASS' if ok else 'FAIL'}"
        )
        return GateReport(
            kind=kind, ok=ok, rel_tol=rel_tol, residuals=residuals, gate=self.gate,
            reference=dict(ref.values), current=dict(cur.values),
            worst=worst, primary_residual=primary_residual, detail=detail,
        )


# ───────────────────────────────────────────────────────────────────────────
# Functional factories (generic, no domain vocabulary)
# ───────────────────────────────────────────────────────────────────────────
def quadratic_functional(terms: Sequence[Tuple[str, str, float]], *,
                         offset: float = 0.0) -> Functional:
    """``Σ w · Σ arr²`` over the listed ``(solver, array, weight)`` terms.

    This is the standard "quadratic invariant" any solver can declare (kinetic
    measure, energy of a modal state, kinetic energy of a particle table).  The
    ledger does not care which; it only compares values across time.
    """

    frozen = tuple((str(s), str(a), float(w)) for s, a, w in terms)

    def _fn(bundle: StateBundle) -> float:
        total = float(offset)
        for solver, array, weight in frozen:
            snap = bundle.snapshot(solver)
            if array not in snap.arrays:
                raise EnergyLedgerError(
                    f"quadratic term {solver}/{array} not present (have {sorted(snap.arrays)})"
                )
            arr = snap.arrays[array]
            total += weight * float(np.sum(np.square(arr, dtype=np.float64)))
        return total

    _fn.terms = frozen  # type: ignore[attr-defined]
    return _fn


def absolute_functional(terms: Sequence[Tuple[str, str, float]], *,
                        offset: float = 0.0) -> Functional:
    """``Σ w · Σ |arr|`` — the ``L1`` companion (mass/bookkeeping style invariant)."""

    frozen = tuple((str(s), str(a), float(w)) for s, a, w in terms)

    def _fn(bundle: StateBundle) -> float:
        total = float(offset)
        for solver, array, weight in frozen:
            snap = bundle.snapshot(solver)
            arr = snap.arrays[array]
            total += weight * float(np.sum(np.abs(arr), dtype=np.float64))
        return total

    _fn.terms = frozen  # type: ignore[attr-defined]
    return _fn