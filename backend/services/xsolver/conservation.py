"""Physical conservation-residual gate.

A cross-solver comparison is only meaningful between runs that actually conserve.
The gate therefore runs *before* any arithmetic on the reported coefficients: a
case whose mass / momentum / energy residual exceeds the tolerance of its solver
family is marked ``conservation_fail`` and removed from the comparison set.  The
canonical verdict alphabet and its ordering (``PASS < WITHHELD < WARN < FAIL``)
are the platform canon implemented in ``services/benchmark_integrity.py``; a test
asserts the two orderings agree, so this module cannot silently invent a second
convention.

``WITHHELD`` means the artifact does not record the residual: no conservation
claim can be made.  ``WARN`` means the residuals that *are* recorded pass but at
least one is unmeasured.  Neither is ever reported as PASS.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.benchmark_integrity import FAIL, PASS, WARN, WITHHELD

from .jsonschema_lite import load_config

RANK = {PASS: 0, WITHHELD: 1, WARN: 2, FAIL: 3}
TOLERANCES = load_config("conservation_tolerances")
KINDS = ("mass", "momentum", "energy")


def tolerances_for(family: Optional[str]) -> Dict[str, float]:
    table = TOLERANCES["per_family"]
    return dict(table.get(family or "OTHER", TOLERANCES["defaults"]))


def worst(*statuses: str) -> str:
    if not statuses:
        return WITHHELD
    return max(statuses, key=lambda s: RANK[s])


def gate(residuals: Dict[str, Optional[float]], family: Optional[str] = None,
         overrides: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Evaluate the three conservation residuals against the family tolerance."""
    tol = tolerances_for(family)
    if overrides:
        tol.update(overrides)
    checks = []
    statuses = []
    missing = 0
    for kind in KINDS:
        value = (residuals or {}).get(kind)
        threshold = tol.get(kind)
        if value is None:
            missing += 1
            statuses.append(WITHHELD)
            checks.append({"id": f"{kind}_conservation", "value": None, "threshold": threshold,
                           "status": WITHHELD, "detail": "residual not recorded by the artifact"})
            continue
        status = PASS if value <= threshold else FAIL
        statuses.append(status)
        checks.append({"id": f"{kind}_conservation", "value": float(value),
                       "threshold": threshold, "status": status,
                       "detail": f"{value:.3e} vs {threshold:.1e}"})
    if FAIL in statuses:
        overall = FAIL
    elif missing == len(KINDS):
        overall = WITHHELD
    elif missing:
        overall = WARN
    else:
        overall = PASS
    return {
        "status": overall,
        "conservation_fail": overall == FAIL,
        "family": family or "OTHER",
        "tolerances": tol,
        "checks": checks,
        "excluded_from_comparison": overall == FAIL,
    }