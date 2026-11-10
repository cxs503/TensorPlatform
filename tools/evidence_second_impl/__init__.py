"""Second-implementation maturity layer for evidence grades (domain-neutral).

Why this package exists
-----------------------
``tools/evidence_grade.py`` turns an evidence grade into a function of the
contract structure (R00..R13).  It still leaves one hole open: a grade can be
*REAL* while every number behind it was produced by the very code under test.
``REAL`` then means "structurally well-formed", not "independently
corroborated".

This package closes that hole with four pieces, all domain-neutral:

1. :mod:`independence` — every REAL claim must *declare* where its reference
   value comes from (analytic closed form / independent algorithm path /
   external data / same-code self reference) **and** back the declaration with a
   verifiable evidence locator (file + line + hash of that line).  A
   self-referential claim, or one whose locator does not re-read, is downgraded
   to ``WITHHELD`` with a machine-readable reason.
2. :mod:`metamorphic` — a registry of relations that must hold for any correct
   implementation (equivalence of two implementations, homogeneous scaling,
   zero-input degeneracy, additivity, mirror parity, conservation of a partition
   sum).  Every relation carries its own *negative control*: a mutant that must
   make the relation fail, so a relation can never be vacuous.
3. :mod:`injection` — an injection bench: three perturbation classes
   (scale ×1.05, constant offset, sign flip) are injected into the *second*
   implementation of three target capabilities; the gate must detect them, and
   must raise zero alarms on the untouched population.
4. :mod:`ledger` — SQLite write-back of the re-grade with a mandatory backup and
   a rollback drill, so a downgrade is auditable and reversible.

Nothing in this package may contain domain vocabulary; the vocabulary scan in
``tools/evidence_domain_scan.py`` is pointed at this directory by the tests.
Domain content arrives only through artifacts (declaration / manifest JSON).
"""

from __future__ import annotations

__version__ = "1.0.0"
SCHEMA_VERSION = "evidence-second-impl/1.0"

from . import handles, independence, injection, ledger, metamorphic  # noqa: E402,F401
from .model import (  # noqa: E402,F401
    DeclarationRecord,
    HandleSpec,
    IndependenceVerdict,
    InjectionType,
    ReferenceSource,
    RelationKind,
    RelationStatus,
    RegradeVerdict,
)

__all__ = [
    "__version__",
    "SCHEMA_VERSION",
    "handles",
    "independence",
    "injection",
    "ledger",
    "metamorphic",
    "DeclarationRecord",
    "HandleSpec",
    "IndependenceVerdict",
    "InjectionType",
    "ReferenceSource",
    "RelationKind",
    "RelationStatus",
    "RegradeVerdict",
]