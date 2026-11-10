"""Registry of executable metamorphic relations, each with a negative control.

A relation is *metamorphic* when it constrains the relationship between outputs
obtained from related inputs — it needs no reference value.  This registry holds
the relations that must hold for any correct implementation of a scalar map:

======================  ==========================================  =========================
kind                    statement                                   applicability domain
======================  ==========================================  =========================
equivalence             two implementations of one spec agree        always (pair of oracles)
scaling                 f(lam*x) == lam**degree * f(x)               lam > 0, degree declared
zero_point              f(0) == 0                                    map has no affine term
additivity              f(x1+x2) == f(x1) + f(x2)                    map is additive
mirror_parity           f(m(x)) == parity * f(x)                     map has declared parity
conservation            sum_i f(i) == declared total                 declared index partition
======================  ==========================================  =========================

Anti-vacuity rule: every relation instance is evaluated twice — once on the
honest candidate (must PASS) and once on a mutant built to violate exactly that
relation (must FAIL).  A relation that passes its own negative control is inert
and is reported as ``NEGATIVE_CONTROL_NOT_FAILING``; it may not count as
evidence.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Mapping, Tuple

from .model import (
    HandleSpec,
    RelationKind,
    RelationResult,
    RelationSpec,
    RelationStatus,
)

Oracle = Callable[[Mapping[str, float]], float]

# ---------------------------------------------------------------------------
# catalogue (documentation + machine-readable applicability)
# ---------------------------------------------------------------------------

CATALOGUE: Dict[RelationKind, Dict[str, Any]] = {
    RelationKind.EQUIVALENCE: {
        "statement": "two independent implementations of one specification agree",
        "domain": "always applicable when a reference implementation exists",
        "params": ["metric", "tolerance"],
        "negative_control": "scale_1p05",
    },
    RelationKind.SCALING: {
        "statement": "f(lam * p) == lam**degree * f(p) for lam > 0",
        "domain": "map homogeneous of declared degree; lam > 0; p != 0",
        "params": ["degree", "metric", "tolerance"],
        "negative_control": "nonhomogeneous_term",
    },
    RelationKind.ZERO_POINT: {
        "statement": "f(0) == 0 (zero input degenerates to zero output)",
        "domain": "map without affine offset",
        "params": ["metric", "tolerance"],
        "negative_control": "offset",
    },
    RelationKind.ADDITIVITY: {
        "statement": "f(p1 + p2) == f(p1) + f(p2) (superposition)",
        "domain": "map additive in its inputs",
        "params": ["metric", "tolerance"],
        "negative_control": "offset",
    },
    RelationKind.MIRROR_PARITY: {
        "statement": "f(mirror(p)) == parity * f(p), parity in {+1, -1}",
        "domain": "map of declared parity under sign reversal of the probe key",
        "params": ["parity", "metric", "tolerance"],
        "negative_control": "parity_breaking_term",
    },
    RelationKind.CONSERVATION: {
        "statement": "sum_i f(i) == declared total of the partition",
        "domain": "map distributes a declared total over a declared index range",
        "params": ["metric", "tolerance"],
        "negative_control": "scale_1p05",
    },
}


def catalogue() -> Dict[str, Dict[str, Any]]:
    return {k.value: dict(v) for k, v in CATALOGUE.items()}


# ---------------------------------------------------------------------------
# numeric helpers
# ---------------------------------------------------------------------------

def _scaled_point(point: Mapping[str, float], lam: float) -> Dict[str, float]:
    return {k: float(v) * lam for k, v in point.items()}


def _add_points(p1: Mapping[str, float], p2: Mapping[str, float]) -> Dict[str, float]:
    keys = set(p1) | set(p2)
    return {k: float(p1.get(k, 0.0)) + float(p2.get(k, 0.0)) for k in keys}


def _mirror_point(point: Mapping[str, float], key: str) -> Dict[str, float]:
    out = {k: float(v) for k, v in point.items()}
    out[key] = -out[key]
    return out


def _rel_residual(observed: float, expected: float) -> float:
    denom = abs(expected) if expected != 0.0 else 1.0
    return abs(observed - expected) / denom


def _verdict(handle_id: str, spec: RelationSpec, observed: float, expected: float,
             residual: float, metric: str, detail: str = "") -> RelationResult:
    tol = float(spec.params.get("tolerance", 1e-6))
    status = RelationStatus.PASS if residual <= tol else RelationStatus.FAIL
    return RelationResult(handle_id=handle_id, kind=spec.kind, status=status,
                          observed=float(observed), expected=float(expected),
                          residual=float(residual), tolerance=tol,
                          metric=metric, detail=detail)


# ---------------------------------------------------------------------------
# relation evaluation
# ---------------------------------------------------------------------------

def evaluate(spec: RelationSpec, handle: HandleSpec, candidate: Oracle,
             reference: Oracle | None = None) -> RelationResult:
    """Evaluate one relation instance on a candidate implementation."""
    probes = handle.probes
    kind = spec.kind
    metric = str(spec.params.get("metric", "rel_abs_diff"))
    base = probes["base"]

    if kind is RelationKind.EQUIVALENCE:
        if reference is None:
            return _not_applicable(handle, spec, "no reference implementation supplied")
        expected = reference(base)
        observed = candidate(base)
        return _verdict(handle.handle_id, spec, observed, expected,
                        _rel_residual(observed, expected), metric,
                        "candidate vs reference implementation at the base probe")

    if kind is RelationKind.SCALING:
        degree = float(spec.params.get("degree", 1.0))
        lam = float(probes.get("lam", 1.7))
        expected = (lam ** degree) * candidate(base)
        observed = candidate(_scaled_point(base, lam))
        return _verdict(handle.handle_id, spec, observed, expected,
                        _rel_residual(observed, expected), metric,
                        f"homogeneity degree {degree}, lam={lam}")

    if kind is RelationKind.ZERO_POINT:
        zero = probes.get("zero", {k: 0.0 for k in base})
        observed = candidate(zero)
        return _verdict(handle.handle_id, spec, observed, 0.0, abs(observed),
                        metric, "zero-input degeneracy")

    if kind is RelationKind.ADDITIVITY:
        pair = probes.get("add_pair")
        if not pair or len(pair) != 2:
            return _not_applicable(handle, spec, "no additive probe pair declared")
        p1, p2 = pair
        expected = candidate(p1) + candidate(p2)
        observed = candidate(_add_points(p1, p2))
        return _verdict(handle.handle_id, spec, observed, expected,
                        _rel_residual(observed, expected), metric, "superposition")

    if kind is RelationKind.MIRROR_PARITY:
        parity = float(spec.params.get("parity", 1.0))
        key = str(spec.params.get("key", "x"))
        expected = parity * candidate(base)
        observed = candidate(_mirror_point(base, key))
        return _verdict(handle.handle_id, spec, observed, expected,
                        _rel_residual(observed, expected), metric,
                        f"parity {parity:+.0f} under sign reversal of {key!r}")

    if kind is RelationKind.CONSERVATION:
        n = int(probes.get("partition_index_range", 0))
        total = float(probes.get("partition_total", 0.0))
        if n <= 0 or total == 0.0:
            return _not_applicable(handle, spec, "no declared partition to conserve")
        parts = [candidate({"i": float(i), "total": total}) for i in range(n)]
        observed = math.fsum(parts)
        return _verdict(handle.handle_id, spec, observed, total,
                        _rel_residual(observed, total), metric,
                        f"partition sum over {n} parts vs declared total {total}")

    return _not_applicable(handle, spec, f"unhandled relation kind {kind.value}")


def _not_applicable(handle: HandleSpec, spec: RelationSpec, detail: str) -> RelationResult:
    return RelationResult(handle_id=handle.handle_id, kind=spec.kind,
                          status=RelationStatus.NOT_APPLICABLE, observed=0.0,
                          expected=0.0, residual=0.0,
                          tolerance=float(spec.params.get("tolerance", 0.0)),
                          metric=str(spec.params.get("metric", "rel_abs_diff")),
                          detail=detail)


# ---------------------------------------------------------------------------
# negative controls: mutants that must break exactly one relation
# ---------------------------------------------------------------------------

def _eps(point: Mapping[str, float], rel: float = 5e-2) -> float:
    """Perturbation magnitude tied to the probe magnitude (never accidentally zero)."""
    scale = max((abs(float(v)) for v in point.values()), default=1.0) or 1.0
    return rel * scale


def mutant_scale(candidate: Oracle, factor: float = 1.05) -> Tuple[str, Oracle]:
    return (f"scale_x{factor}", lambda p: factor * candidate(p))


def mutant_offset(candidate: Oracle) -> Tuple[str, Oracle]:
    def oracle(p):
        return candidate(p) + _eps(p)
    return ("offset", oracle)


def mutant_nonhomogeneous(candidate: Oracle, key: str = "") -> Tuple[str, Oracle]:
    """Adds a degree-0 plus degree-2 term, so homogeneity of any degree != 0 breaks.

    The term must *not* scale with the probe (an eps proportional to the probe
    magnitude is itself homogeneous and cancels — measured, see the run report).
    """
    def oracle(p):
        k = key if key in p else (next(iter(p), "") if p else "")
        x = float(p.get(k, 0.0))
        return candidate(p) + 0.05 * (1.0 + x * x)
    return ("nonhomogeneous_term", oracle)


def mutant_quadratic_term(candidate: Oracle) -> Tuple[str, Oracle]:
    """Adds a term quadratic in the probe values: breaks superposition only."""
    def oracle(p):
        x = float(p.get("x", 0.0))
        y = float(p.get("x2", 0.0)) if "x2" in p else 0.0
        if "total" in p:
            return candidate(p) + 0.05 * float(p["total"]) ** 2
        return candidate(p) + 0.05 * (x * x + y * y)
    return ("quadratic_term", oracle)


def mutant_parity_breaking(candidate: Oracle, parity: float = 1.0,
                           key: str = "x") -> Tuple[str, Oracle]:
    def oracle(p):
        x = float(p.get(key, 0.0))
        term = _eps(p) * x if parity > 0 else _eps(p) * x * x
        return candidate(p) + term
    return ("parity_breaking_term", oracle)


NEGATIVE_CONTROLS: Dict[RelationKind, Callable[[Oracle, RelationSpec], Tuple[str, Oracle]]] = {
    RelationKind.EQUIVALENCE: lambda c, s: mutant_scale(c, 1.05),
    RelationKind.SCALING: lambda c, s: mutant_nonhomogeneous(c, str(s.params.get("key", ""))),
    RelationKind.ZERO_POINT: lambda c, s: mutant_offset(c),
    RelationKind.ADDITIVITY: lambda c, s: mutant_quadratic_term(c),
    RelationKind.MIRROR_PARITY: lambda c, s: mutant_parity_breaking(
        c, float(s.params.get("parity", 1.0)), str(s.params.get("key", "x"))),
    RelationKind.CONSERVATION: lambda c, s: mutant_scale(c, 1.05),
}


def negative_control(spec: RelationSpec, handle: HandleSpec, candidate: Oracle,
                     reference: Oracle | None) -> Dict[str, Any]:
    """Run the anti-vacuity check for one relation instance.

    Returns a dict with the honest verdict, the mutant verdict and whether the
    relation is *discriminating* (honest PASS and mutant FAIL).
    """
    honest = evaluate(spec, handle, candidate, reference)
    builder = NEGATIVE_CONTROLS.get(spec.kind)
    if builder is None:
        return {"handle_id": handle.handle_id, "kind": spec.kind.value,
                "mutant": "", "honest": honest.to_dict(), "mutant_result": None,
                "discriminating": False, "reason": "no negative control registered"}
    name, mutant = builder(candidate, spec)
    broken = evaluate(spec, handle, mutant, reference)
    discriminating = (honest.status is RelationStatus.PASS
                      and broken.status is RelationStatus.FAIL)
    return {
        "handle_id": handle.handle_id,
        "kind": spec.kind.value,
        "mutant": name,
        "honest": honest.to_dict(),
        "mutant_result": broken.to_dict(),
        "discriminating": discriminating,
        "reason": ("relation discriminates" if discriminating
                   else "NEGATIVE_CONTROL_NOT_FAILING: relation is inert"),
    }