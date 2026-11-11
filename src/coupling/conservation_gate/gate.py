"""Fail-closed ingest gate for cross-library conservation ledgers.

The gate answers exactly one question per bundle: *may this transfer be
ingested?*  It is fail-closed — anything it cannot positively verify is a
rejection, and every rejection carries a machine-readable reason code:

===========================  ==================================================
``NO_LEDGER``                empty bundle
``MISSING_SEAL``             producer never sealed the document
``SEAL_MISMATCH``            numbers changed after sealing (tampering)
``MISSING_PROVENANCE``       producer name / version / input digest absent
``DUPLICATE_ENTRY``          two entries with the same name in one face
``MISSING_FACE``             a required interface is not in the bundle
``MISSING_ENTRY``            a criterion's quantity is absent
``UNKNOWN_KIND``             entry kind outside the schema
``SHAPE_MISMATCH``           source/target shapes differ
``NONFINITE``                NaN / inf on either side
``ASYMMETRIC_TENSOR``        tensor entry not symmetric (not a valid tensor)
``NON_SPD_TENSOR``           inertia tensor not positive definite
``NEGATIVE_QUANTITY``        a mass / volume / energy side is negative
``BALANCE_VIOLATION``        conserved quantity not balanced within tolerance
``BOUND_VIOLATION``          bounded quantity (rate, skew, count) too large
``FLAG_FALSE``               an invariant that must hold did not hold
``CRITERIA_EMPTY``           policy without criteria (gate would be vacuous)
===========================  ==================================================

Two properties matter more than any single green check:

* **The gate owns the tolerance.**  A document may declare its own
  ``tolerance``; the gate recomputes the residual from the raw values and
  compares against the *policy* tolerance.  A producer cannot relax its way to
  acceptance.
* **Reason codes are specific.**  The negative-control battery in
  :mod:`src.coupling.conservation_gate.controls` mutates a valid bundle in
  fourteen different ways, and every mutation must be rejected with the
  expected code — a gate that rejects everything (including the valid bundle)
  would fail the positive control just as loudly.

No domain semantics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .ledger import (
    KINDS,
    ConservationLedger,
    LedgerEntry,
    as_numbers,
    bundle_digest,
    canonical_json,
    rel_residual,
    sha256_of,
)

BALANCE = "balance"
BOUND = "bound"
FLAG = "flag_true"
OPS: Tuple[str, ...] = (BALANCE, BOUND, FLAG)


@dataclass(frozen=True)
class Criterion:
    """One admissible statement about one entry of one face."""

    face: str
    entry: str
    op: str
    tol: float = 0.0
    checks: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError(f"unknown op {self.op!r}")

    @property
    def key(self) -> str:
        return f"{self.face}.{self.entry}"


@dataclass(frozen=True)
class GatePolicy:
    """Required faces + criteria that a bundle must satisfy to be ingested."""

    name: str
    criteria: Tuple[Criterion, ...]
    required_faces: Tuple[str, ...] = ()
    require_provenance: bool = True
    finite_only: bool = True

    def __post_init__(self) -> None:
        if not self.criteria:
            raise ValueError("policy without criteria would be vacuous")
        faces = set(self.required_faces) | {c.face for c in self.criteria}
        object.__setattr__(self, "required_faces", tuple(sorted(faces)))


@dataclass
class GateVerdict:
    """Machine-checkable adjudication of one bundle."""

    policy: str
    accepted: bool
    rows: List[Dict[str, Any]] = field(default_factory=list)
    failures: List[Dict[str, Any]] = field(default_factory=list)
    bundle_sha256: str = ""
    seal: str = ""

    def codes(self) -> List[str]:
        return [f["code"] for f in self.failures]

    def row(self, key: str) -> Optional[Dict[str, Any]]:
        for r in self.rows:
            if r["key"] == key:
                return r
        return None

    def as_dict(self) -> dict:
        body = {
            "policy": self.policy,
            "accepted": self.accepted,
            "rows": self.rows,
            "failures": self.failures,
            "bundle_sha256": self.bundle_sha256,
        }
        return {**body, "seal": self.seal}

    def seal_verdict(self) -> str:
        self.seal = sha256_of(
            {
                "policy": self.policy,
                "accepted": self.accepted,
                "rows": self.rows,
                "failures": self.failures,
                "bundle_sha256": self.bundle_sha256,
            }
        )
        return self.seal


# --------------------------------------------------------------------------- #
# structural checks
# --------------------------------------------------------------------------- #
def _spd(matrix: Sequence[float]) -> bool:
    a = np.asarray(matrix, dtype=float).reshape(3, 3)
    if not np.all(np.isfinite(a)):
        return False
    if np.max(np.abs(a - a.T)) > 1e-12 * max(1.0, float(np.max(np.abs(a)))):
        return False
    try:
        np.linalg.cholesky(a)
    except np.linalg.LinAlgError:
        return False
    return True


def _symmetric(matrix: Sequence[float], tol: float = 1e-12) -> bool:
    a = np.asarray(matrix, dtype=float).reshape(3, 3)
    scale = max(1.0, float(np.max(np.abs(a))))
    return float(np.max(np.abs(a - a.T))) <= tol * scale


def _finite_entry(entry: LedgerEntry) -> bool:
    try:
        vals = as_numbers(entry.kind, entry.source) + as_numbers(entry.kind, entry.target)
    except (TypeError, ValueError):
        return False
    return all(math.isfinite(v) for v in vals)


def structural_failures(ledgers: Sequence[ConservationLedger],
                        policy: GatePolicy) -> List[Dict[str, Any]]:
    """Fail-closed checks that do not depend on any criterion."""
    fails: List[Dict[str, Any]] = []
    if not ledgers:
        fails.append({"code": "NO_LEDGER", "detail": "empty bundle"})
        return fails

    faces = {lg.face for lg in ledgers}
    for face in policy.required_faces:
        if face not in faces:
            fails.append({"code": "MISSING_FACE", "face": face,
                          "detail": f"required interface {face!r} absent"})

    for lg in ledgers:
        if not lg.seal:
            fails.append({"code": "MISSING_SEAL", "face": lg.face,
                          "detail": "document was never sealed"})
        elif not lg.verify_seal():
            fails.append({"code": "SEAL_MISMATCH", "face": lg.face,
                          "detail": "payload hash differs from recorded seal"})

        if policy.require_provenance:
            prod = lg.producer or {}
            missing = [k for k in ("name", "version") if not prod.get(k)]
            if missing or not lg.inputs:
                fails.append({"code": "MISSING_PROVENANCE", "face": lg.face,
                              "detail": f"missing producer fields {missing} "
                                        f"or empty input digests"})

        for dup in lg.duplicate_names():
            fails.append({"code": "DUPLICATE_ENTRY", "face": lg.face,
                          "detail": f"entry {dup!r} appears more than once"})

        if not lg.entries:
            fails.append({"code": "MISSING_ENTRY", "face": lg.face,
                          "detail": "document carries no entries"})

        for e in lg.entries:
            if e.kind not in KINDS:
                fails.append({"code": "UNKNOWN_KIND", "face": lg.face,
                              "entry": e.name, "detail": e.kind})
                continue
            if policy.finite_only and not _finite_entry(e):
                fails.append({"code": "NONFINITE", "face": lg.face, "entry": e.name,
                              "detail": "NaN/inf or unparsable value"})
                continue
            try:
                if e.kind == "tensor" and not _symmetric(e.source):
                    fails.append({"code": "ASYMMETRIC_TENSOR", "face": lg.face,
                                  "entry": e.name,
                                  "detail": "source tensor not symmetric"})
                if e.kind == "tensor" and not _symmetric(e.target):
                    fails.append({"code": "ASYMMETRIC_TENSOR", "face": lg.face,
                                  "entry": e.name,
                                  "detail": "target tensor not symmetric"})
                if e.kind != "flag":
                    s = as_numbers(e.kind, e.source)
                    t = as_numbers(e.kind, e.target)
                    if len(s) != len(t):
                        fails.append({"code": "SHAPE_MISMATCH", "face": lg.face,
                                      "entry": e.name,
                                      "detail": f"{len(s)} vs {len(t)}"})
                if e.kind == "tensor" and not _spd(e.source):
                    fails.append({"code": "NON_SPD_TENSOR", "face": lg.face,
                                  "entry": e.name,
                                  "detail": "source tensor not positive definite"})
                if e.kind == "tensor" and not _spd(e.target):
                    fails.append({"code": "NON_SPD_TENSOR", "face": lg.face,
                                  "entry": e.name,
                                  "detail": "target tensor not positive definite"})
            except (TypeError, ValueError) as exc:  # unparsable shapes
                fails.append({"code": "SHAPE_MISMATCH", "face": lg.face,
                              "entry": e.name, "detail": str(exc)})
    return fails


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
class ConservationGate:
    """Adjudicate bundles against a :class:`GatePolicy` (fail-closed)."""

    def __init__(self, policy: GatePolicy):
        self.policy = policy

    # -- single criterion ------------------------------------------------- #
    def _row(self, crit: Criterion, entry: LedgerEntry) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "key": crit.key,
            "face": crit.face,
            "entry": crit.entry,
            "op": crit.op,
            "tol": float(crit.tol),
            "status": "PASS",
            "code": "",
        }
        if crit.op == FLAG:
            ok = entry.source is True
            row.update(measured=bool(entry.source), value=bool(entry.source))
            if not ok:
                row.update(status="FAIL", code="FLAG_FALSE")
            return row

        if crit.op == BOUND:
            value = float(entry.source)
            row.update(measured=value, value=value)
            if not (value <= crit.tol):
                row.update(status="FAIL", code="BOUND_VIOLATION")
            return row

        # balance: the gate recomputes the residual from the raw values and
        # uses ITS OWN tolerance (the document's tolerance is advisory only).
        residual = rel_residual(entry.kind, entry.source, entry.target)
        row.update(measured=residual, value=residual,
                   declared_tolerance=float(entry.tolerance))
        if not (residual <= crit.tol):
            row.update(status="FAIL", code="BALANCE_VIOLATION")
        if "spd" in crit.checks and not _spd(entry.source):
            row.update(status="FAIL", code="NON_SPD_TENSOR")
        if "non_negative" in crit.checks:
            vals = as_numbers(entry.kind, entry.source)
            if any(v < 0.0 for v in vals):
                row.update(status="FAIL", code="NEGATIVE_QUANTITY")
        return row

    # -- bundle ----------------------------------------------------------- #
    def adjudicate(self, ledgers: Sequence[ConservationLedger]) -> GateVerdict:
        ledgers = list(ledgers)
        verdict = GateVerdict(policy=self.policy.name, accepted=False,
                              bundle_sha256=bundle_digest(ledgers) if ledgers else "")
        failures = structural_failures(ledgers, self.policy)
        by_face = {lg.face: lg for lg in ledgers}

        for crit in self.policy.criteria:
            lg = by_face.get(crit.face)
            if lg is None:
                failures.append({"code": "MISSING_FACE", "face": crit.face,
                                 "entry": crit.entry,
                                 "detail": f"interface {crit.face!r} absent"})
                verdict.rows.append({"key": crit.key, "face": crit.face,
                                     "entry": crit.entry, "op": crit.op,
                                     "tol": float(crit.tol), "status": "FAIL",
                                     "code": "MISSING_FACE", "measured": None})
                continue
            entry = lg.entry(crit.entry)
            if entry is None:
                failures.append({"code": "MISSING_ENTRY", "face": crit.face,
                                 "entry": crit.entry,
                                 "detail": "criterion has no matching entry"})
                verdict.rows.append({"key": crit.key, "face": crit.face,
                                     "entry": crit.entry, "op": crit.op,
                                     "tol": float(crit.tol), "status": "FAIL",
                                     "code": "MISSING_ENTRY", "measured": None})
                continue
            if not _finite_entry(entry):
                failures.append({"code": "NONFINITE", "face": crit.face,
                                 "entry": crit.entry,
                                 "detail": "cannot evaluate criterion"})
                verdict.rows.append({"key": crit.key, "face": crit.face,
                                     "entry": crit.entry, "op": crit.op,
                                     "tol": float(crit.tol), "status": "FAIL",
                                     "code": "NONFINITE", "measured": None})
                continue
            row = self._row(crit, entry)
            verdict.rows.append(row)
            if row["status"] != "PASS":
                failures.append({"code": row["code"], "face": crit.face,
                                 "entry": crit.entry,
                                 "detail": f"measured={row['measured']!r} "
                                           f"tol={crit.tol!r}"})

        verdict.failures = failures
        verdict.accepted = not failures
        verdict.seal_verdict()
        return verdict


# --------------------------------------------------------------------------- #
# reference policies
# --------------------------------------------------------------------------- #
def cross_library_policy(name: str = "cross-library-v1") -> GatePolicy:
    """The policy of this node: three interfaces, four ledgers each as required.

    Thresholds come from the node contract: fracture hand-off ledgers ``<1e-9``,
    flow-field mass flux ``<1e-6`` (inventory tighter, ``1e-9``), clock barrier
    invariants exact, optimistic rollback rate ``<5%`` with replay equivalence
    to round-off.
    """
    c: List[Criterion] = []
    for q in ("mass", "momentum", "angular_momentum", "energy", "inertia_tensor"):
        checks = ("non_negative",) if q in ("mass", "energy") else ()
        if q == "inertia_tensor":
            checks = ("spd",)
        c.append(Criterion("fem_dem", q, BALANCE, 1e-9, checks))
    c.append(Criterion("fem_dem", "volume", BALANCE, 1e-9, ("non_negative",)))
    c += [
        Criterion("lbm_fvm", "mass_inventory", BALANCE, 1e-9, ("non_negative",)),
        Criterion("lbm_fvm", "momentum_inventory", BALANCE, 1e-9),
        Criterion("lbm_fvm", "interface_mass_flux", BALANCE, 1e-6),
        Criterion("clock", "barrier_skew", BOUND, 1e-9),
        Criterion("clock", "deadlocks", BOUND, 0.0),
        Criterion("clock", "future_reads", BOUND, 0.0),
        Criterion("clock", "global_time_monotone", FLAG, 0.0),
        Criterion("clock", "optimistic_rollback_rate", BOUND, 0.05),
        Criterion("clock", "optimistic_replay_error", BOUND, 1e-9),
    ]
    return GatePolicy(name=name, criteria=tuple(c))


def adjudicate(ledgers: Sequence[ConservationLedger],
               policy: Optional[GatePolicy] = None) -> GateVerdict:
    return ConservationGate(policy or cross_library_policy()).adjudicate(ledgers)


__all__ = [
    "BALANCE",
    "BOUND",
    "FLAG",
    "OPS",
    "ConservationGate",
    "Criterion",
    "GatePolicy",
    "GateVerdict",
    "adjudicate",
    "cross_library_policy",
    "structural_failures",
]