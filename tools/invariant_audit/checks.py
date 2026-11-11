#!/usr/bin/env python3
"""Invariant evaluators (the only place where a residual is defined).

Each evaluator maps ``(InvariantSpec, EvalContext) -> CheckOutcome``.  ``kind``
is the dispatch key; adding a law means adding a handler here and nothing else.

Handler contract
----------------
* **Never raise for missing evidence** - return ``WITHHELD`` with a
  ``observable_missing:<name>`` (or ``declaration_missing:<name>``) reason.
* **Raise/return ``ERROR`` only for contract violations** (wrong shape, callable
  raised), never to hide a physical violation.
* The residual and the scale it is compared against are always reported, so an
  auditor can recompute the verdict with the same mixed band
  ``|residual| <= atol + rtol * scale`` used by the reference comparison
  machinery of :mod:`tools.verification_harness`.

Kinds
-----
``conservation``          ``final - initial - flux == 0`` for an opaque quantity
                          (mass / momentum / energy / charge / anything declared)
``covariance``            ``f(M x + o) == P f(x) + q`` for an injected linear
                          transform pair: rotations and reflections use
                          ``M == P``, a Galilean boost uses ``M = I, o = shift``
                          and ``P = I`` with the expected output shift
``dimension_consistency`` exponent algebra: a declared formula must reproduce
                          the declared dimension (and optional absolute units)
``relation``              a numeric algebraic relation between observed
                          quantities, e.g. ``force == mass * acceleration``
"""
from __future__ import annotations

import ast
import copy
import math
import operator
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .dimensions import Dimension, DimensionError, dimension_table
from .model import (
    DIMENSIONS_KEY,
    RESERVED_OBSERVABLE_PREFIX,
    InvariantKind,
    InvariantSpec,
    InvariantState,
)

# ---------------------------------------------------------------------------
# Context / outcome
# ---------------------------------------------------------------------------


@dataclass
class EvalContext:
    """Everything an evaluator may look at."""

    call: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    config: Mapping[str, Any]
    observables: Mapping[str, Any]
    dimensions: Mapping[str, Any]
    base_dimensions: Sequence[str]

    def invoke(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        result = self.call(copy.deepcopy(dict(config)))
        if not isinstance(result, Mapping):
            raise TypeError(
                f"target callable returned {type(result).__name__}, expected a mapping")
        return result


@dataclass
class CheckOutcome:
    state: InvariantState
    residual: Optional[float] = None
    scale: Optional[float] = None
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------
def _vector(name: str, value: Any) -> Tuple[float, ...]:
    """Coerce a scalar or a flat sequence to a tuple of floats (contract check)."""
    if isinstance(value, bool) or value is None:
        raise TypeError(f"observable {name!r} is not numeric: {value!r}")
    if isinstance(value, (int, float)):
        return (float(value),)
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise TypeError(f"observable {name!r} contains a non-numeric entry: {item!r}")
            out.append(float(item))
        if not out:
            raise TypeError(f"observable {name!r} is an empty sequence")
        return tuple(out)
    raise TypeError(f"observable {name!r} has unsupported type {type(value).__name__}")


def _missing(context: EvalContext, names: Sequence[str]) -> List[str]:
    return [n for n in names if n not in context.observables]


def _band(spec: InvariantSpec, residual: float, scale: float) -> bool:
    return abs(residual) <= float(spec.tolerance.atol) + float(spec.tolerance.rtol) * abs(scale)


def _verdict(spec: InvariantSpec, residual: float, scale: float) -> InvariantState:
    return InvariantState.PASS if _band(spec, residual, scale) else InvariantState.FAIL


def _matvec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> Tuple[float, ...]:
    if len(matrix) != len(vector):
        raise ValueError(f"transform has {len(matrix)} rows but the vector has "
                         f"{len(vector)} entries")
    out = []
    for row in matrix:
        if len(row) != len(vector):
            raise ValueError(f"transform row has {len(row)} entries, expected {len(vector)}")
        out.append(sum(float(a) * float(b) for a, b in zip(row, vector)))
    return tuple(out)


def _identity(n: int) -> List[List[float]]:
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


# ---------------------------------------------------------------------------
# handler: conservation
# ---------------------------------------------------------------------------
def _check_conservation(spec: InvariantSpec, ctx: EvalContext) -> CheckOutcome:
    """``final - initial - flux == 0`` (a positive flux is net inflow)."""
    params = spec.params
    for key in ("initial", "final"):
        if key not in params:
            raise ValueError(f"conservation invariant {spec.invariant_id!r} needs param {key!r}")
    initial_name = str(params["initial"])
    final_name = str(params["final"])
    flux_name = params.get("flux")
    required = [initial_name, final_name] + ([str(flux_name)] if flux_name else [])
    missing = _missing(ctx, required)
    if missing:
        return CheckOutcome(
            state=InvariantState.WITHHELD,
            reason="observable_missing:" + ",".join(missing),
            details={"required_observables": required, "available": sorted(ctx.observables)},
        )
    try:
        initial = _vector(initial_name, ctx.observables[initial_name])
        final = _vector(final_name, ctx.observables[final_name])
        flux = (_vector(str(flux_name), ctx.observables[str(flux_name)]) if flux_name
                else tuple(0.0 for _ in initial))
    except TypeError as exc:
        return CheckOutcome(state=InvariantState.ERROR, reason=f"observable_contract:{exc}")
    if not (len(initial) == len(final) == len(flux)):
        return CheckOutcome(
            state=InvariantState.ERROR,
            reason=(f"length_mismatch: initial={len(initial)} final={len(final)} "
                    f"flux={len(flux)}"),
        )
    residual = [f - i - q for i, f, q in zip(initial, final, flux)]
    residual_max = max(abs(r) for r in residual)
    scale = max(max(abs(i) for i in initial), max(abs(f) for f in final),
                max(abs(q) for q in flux))
    if scale == 0.0:
        scale = 1.0
    state = _verdict(spec, residual_max, scale)
    return CheckOutcome(
        state=state,
        residual=residual_max,
        scale=scale,
        reason="" if state is InvariantState.PASS else "residual_outside_band",
        details={
            "quantity": str(params.get("quantity", "")),
            "total_initial": math.fsum(initial),
            "total_final": math.fsum(final),
            "total_flux": math.fsum(flux),
            "residual_vector": residual,
            "components": len(initial),
        },
    )


# ---------------------------------------------------------------------------
# handler: covariance (rotation / reflection / Galilean boost / any affine pair)
# ---------------------------------------------------------------------------
def _check_covariance(spec: InvariantSpec, ctx: EvalContext) -> CheckOutcome:
    params = spec.params
    for key in ("input_key", "observable", "input_transform", "output_transform"):
        if key not in params:
            raise ValueError(f"covariance invariant {spec.invariant_id!r} needs param {key!r}")
    input_key = str(params["input_key"])
    observable = str(params["observable"])
    if input_key not in ctx.config:
        return CheckOutcome(
            state=InvariantState.WITHHELD,
            reason=f"config_missing:{input_key}",
            details={"config_keys": sorted(ctx.config)},
        )
    missing = _missing(ctx, [observable])
    if missing:
        return CheckOutcome(
            state=InvariantState.WITHHELD,
            reason="observable_missing:" + ",".join(missing),
            details={"required_observables": [observable],
                     "available": sorted(ctx.observables)},
        )
    try:
        x = _vector(input_key, ctx.config[input_key])
        in_tf = params["input_transform"]
        out_tf = params["output_transform"]
        m_in = in_tf.get("matrix") or _identity(len(x))
        o_in = tuple(float(v) for v in (in_tf.get("offset") or (0.0,) * len(x)))
        if len(o_in) != len(x):
            raise ValueError(f"input offset has {len(o_in)} entries, expected {len(x)}")
        x2 = tuple(a + b for a, b in zip(_matvec(m_in, x), o_in))
        y1 = _vector(observable, ctx.observables[observable])
        m_out = out_tf.get("matrix") or _identity(len(y1))
        o_out = tuple(float(v) for v in (out_tf.get("offset") or (0.0,) * len(y1)))
        if len(o_out) != len(y1):
            raise ValueError(f"output offset has {len(o_out)} entries, expected {len(y1)}")
        expected = tuple(a + b for a, b in zip(_matvec(m_out, y1), o_out))
        config2 = copy.deepcopy(dict(ctx.config))
        # Preserve the scalar/vector shape of the original parameter so the target
        # sees the same interface it was written against.
        scalar_input = isinstance(ctx.config[input_key], (int, float)) and not isinstance(
            ctx.config[input_key], bool)
        if scalar_input and len(x2) == 1:
            config2[input_key] = float(x2[0])
        else:
            config2[input_key] = list(x2)
        obs2 = ctx.invoke(config2)
    except (TypeError, ValueError) as exc:
        return CheckOutcome(state=InvariantState.ERROR, reason=f"transform_contract:{exc}")
    except Exception as exc:  # callable raised: interface bug, not a physics verdict
        return CheckOutcome(state=InvariantState.ERROR,
                            reason=f"callable_raised:{type(exc).__name__}:{exc}")
    if observable not in obs2:
        return CheckOutcome(
            state=InvariantState.WITHHELD,
            reason=f"observable_missing:{observable}",
            details={"note": "observable absent from the transformed run"},
        )
    try:
        y2 = _vector(observable, obs2[observable])
    except TypeError as exc:
        return CheckOutcome(state=InvariantState.ERROR, reason=f"observable_contract:{exc}")
    if len(y2) != len(expected):
        return CheckOutcome(
            state=InvariantState.ERROR,
            reason=f"length_mismatch: base={len(y1)} transformed={len(y2)}",
        )
    residuals = [b - a for a, b in zip(expected, y2)]
    residual_max = max(abs(r) for r in residuals)
    scale = max(max(abs(a) for a in expected), max(abs(b) for b in y2))
    if scale == 0.0:
        scale = 1.0
    state = _verdict(spec, residual_max, scale)
    return CheckOutcome(
        state=state,
        residual=residual_max,
        scale=scale,
        reason="" if state is InvariantState.PASS else "covariance_residual_outside_band",
        details={
            "observable": observable,
            "input_base": list(x),
            "input_transformed": list(x2),
            "output_base": list(y1),
            "output_transformed": list(y2),
            "output_expected": list(expected),
            "residual_vector": residuals,
            "label": str(params.get("label", "")),
        },
    )


# ---------------------------------------------------------------------------
# handler: dimensional consistency
# ---------------------------------------------------------------------------
def _declared_dimensions(ctx: EvalContext) -> Dict[str, Any]:
    if ctx.dimensions:
        return dict(ctx.dimensions)
    raw = ctx.observables.get(DIMENSIONS_KEY)
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _check_dimension_consistency(spec: InvariantSpec, ctx: EvalContext) -> CheckOutcome:
    declared = _declared_dimensions(ctx)
    if not declared:
        return CheckOutcome(
            state=InvariantState.WITHHELD,
            reason="declaration_missing:" + DIMENSIONS_KEY,
            details={"note": "target supplies no dimension declarations"},
        )
    try:
        table = dimension_table(ctx.base_dimensions, declared)
    except DimensionError as exc:
        return CheckOutcome(state=InvariantState.ERROR, reason=f"declaration_invalid:{exc}")
    checks: List[Dict[str, Any]] = []
    withheld: List[str] = []
    failures = 0
    params = spec.params

    for quantity, expected in (params.get("required") or {}).items():
        quantity = str(quantity)
        if quantity not in declared:
            withheld.append(f"declaration_missing:{quantity}")
            continue
        try:
            expected_dim = Dimension.parse(expected, symbols=set(table))
        except DimensionError as exc:
            return CheckOutcome(state=InvariantState.ERROR,
                                reason=f"declaration_invalid:{exc}")
        actual_dim = table[quantity]
        ok = actual_dim == expected_dim
        failures += 0 if ok else 1
        checks.append({
            "quantity": quantity,
            "declared": actual_dim.to_expr(),
            "expected": expected_dim.to_expr(),
            "holds": ok,
        })

    for relation in (params.get("relations") or []):
        if not isinstance(relation, Mapping) or "quantity" not in relation or "from" not in relation:
            return CheckOutcome(state=InvariantState.ERROR,
                                reason="declaration_invalid:relation needs 'quantity' and 'from'")
        quantity = str(relation["quantity"])
        source = str(relation["from"])
        if quantity not in declared:
            withheld.append(f"declaration_missing:{quantity}")
            continue
        try:
            derived = _eval_dimension_expression(source, table)
        except DimensionError as exc:
            return CheckOutcome(state=InvariantState.ERROR, reason=f"relation_invalid:{exc}")
        actual_dim = table[quantity]
        ok = actual_dim == derived
        failures += 0 if ok else 1
        checks.append({
            "quantity": quantity,
            "declared": actual_dim.to_expr(),
            "derived_from": source,
            "derived": derived.to_expr(),
            "holds": ok,
        })

    if failures:
        return CheckOutcome(
            state=InvariantState.FAIL,
            residual=float(failures),
            scale=float(len(checks)),
            reason="dimension_mismatch",
            details={"checks": checks},
        )
    if withheld:
        return CheckOutcome(state=InvariantState.WITHHELD, reason=";".join(withheld),
                            details={"checks": checks})
    return CheckOutcome(state=InvariantState.PASS, residual=0.0, scale=1.0,
                        reason="", details={"checks": checks})


def _eval_dimension_expression(expression: str, table: Mapping[str, Dimension]) -> Dimension:
    """Evaluate a dimension expression whose symbols resolve through ``table``.

    Only ``*``, ``/`` and ``**`` are allowed; every symbol must be declared, so a
    typo cannot silently pass as an independent axis.
    """
    text = expression.strip()
    if not text:
        raise DimensionError("empty dimension expression")
    if text in {"1", "dimensionless"}:
        return Dimension.unit()
    pos = 0
    result: Optional[Dimension] = None
    pending = 1  # +1 after '*', -1 after '/'
    import re

    token = re.compile(r"\s*(?P<op>[*/]?)\s*(?P<symbol>[A-Za-z_][A-Za-z_0-9]*)"
                       r"(?:\s*\*\*\s*(?P<exp>-?\d+(?:\s*/\s*\d+)?))?")
    while pos < len(text):
        match = token.match(text, pos)
        if not match or match.end() == pos:
            raise DimensionError(f"cannot parse dimension expression {expression!r} "
                                 f"at offset {pos}")
        op = match.group("op")
        if result is None:
            if op == "/":
                raise DimensionError(f"expression may not start with '/': {expression!r}")
            pending = 1
        elif op == "/":
            pending = -1
        elif op == "*":
            pending = 1
        else:
            raise DimensionError(f"missing operator before offset {match.start('symbol')} "
                                 f"in {expression!r}")
        symbol = match.group("symbol")
        if symbol not in table:
            raise DimensionError(f"undeclared symbol {symbol!r} in {expression!r}")
        power = match.group("exp")
        exponent = 1 if power is None else float(power) if "/" not in power else None
        if exponent is None:
            num, den = power.split("/")
            from fractions import Fraction
            frac = Fraction(int(num.strip()), int(den.strip()))
            dim = table[symbol] ** frac
        else:
            dim = table[symbol] ** int(exponent)
        if result is None:
            result = dim
        elif pending > 0:
            result = result * dim
        else:
            result = result / dim
        pos = match.end()
    return result if result is not None else Dimension.unit()


# ---------------------------------------------------------------------------
# handler: numeric algebraic relation
# ---------------------------------------------------------------------------
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}


def safe_eval(expression: str, names: Mapping[str, float]) -> float:
    """Evaluate a numeric expression over ``names`` with an AST whitelist.

    No ``eval`` of arbitrary source: only ``+ - * / **`` on declared names and
    numeric literals is accepted.
    """
    tree = ast.parse(expression, mode="eval")

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError(f"non-numeric literal {node.value!r} in {expression!r}")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ValueError(f"undeclared variable {node.id!r} in {expression!r}")
            return float(names[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return float(_BIN_OPS[type(node.op)](walk(node.left), walk(node.right)))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        raise ValueError(f"disallowed construct {type(node).__name__} in {expression!r}")

    return walk(tree)


def _check_relation(spec: InvariantSpec, ctx: EvalContext) -> CheckOutcome:
    params = spec.params
    if "quantity" not in params or "expression" not in params:
        raise ValueError(f"relation invariant {spec.invariant_id!r} needs "
                         f"'quantity' and 'expression'")
    quantity = str(params["quantity"])
    expression = str(params["expression"])
    if quantity not in ctx.observables:
        return CheckOutcome(state=InvariantState.WITHHELD,
                            reason=f"observable_missing:{quantity}",
                            details={"available": sorted(ctx.observables)})
    numeric = {k: float(v) for k, v in ctx.observables.items()
               if not str(k).startswith(RESERVED_OBSERVABLE_PREFIX)
               and isinstance(v, (int, float)) and not isinstance(v, bool)}
    try:
        expected = safe_eval(expression, numeric)
    except ValueError as exc:
        return CheckOutcome(state=InvariantState.WITHHELD,
                            reason=f"expression_unevaluable:{exc}",
                            details={"available_numeric": sorted(numeric)})
    actual = float(ctx.observables[quantity])
    residual = actual - expected
    scale = max(abs(actual), abs(expected))
    if scale == 0.0:
        scale = 1.0
    state = _verdict(spec, residual, scale)
    return CheckOutcome(
        state=state,
        residual=residual,
        scale=scale,
        reason="" if state is InvariantState.PASS else "relation_residual_outside_band",
        details={"quantity": quantity, "expression": expression,
                 "observed": actual, "expected": expected},
    )


KIND_HANDLERS: Dict[InvariantKind, Callable[[InvariantSpec, EvalContext], CheckOutcome]] = {
    InvariantKind.CONSERVATION: _check_conservation,
    InvariantKind.COVARIANCE: _check_covariance,
    InvariantKind.DIMENSION: _check_dimension_consistency,
    InvariantKind.RELATION: _check_relation,
}


def evaluate(spec: InvariantSpec, ctx: EvalContext) -> CheckOutcome:
    """Dispatch one invariant; contract errors become ``ERROR``, never a crash."""
    handler = KIND_HANDLERS.get(spec.kind)
    if handler is None:  # pragma: no cover - Enum guards this at load time
        return CheckOutcome(state=InvariantState.ERROR,
                            reason=f"unknown_kind:{spec.kind}")
    try:
        return handler(spec, ctx)
    except (TypeError, ValueError) as exc:
        return CheckOutcome(state=InvariantState.ERROR,
                            reason=f"invariant_contract:{type(exc).__name__}:{exc}")
    except Exception as exc:  # pragma: no cover - defensive
        return CheckOutcome(state=InvariantState.ERROR,
                            reason=f"evaluator_raised:{type(exc).__name__}:{exc}")