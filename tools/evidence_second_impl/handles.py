"""Executable capability handles: a deterministic synthetic population.

The bench needs numeric handles it can execute, mutate and compare.  Real
domain capabilities live in their own repositories and are reached over HTTP, so
they cannot be perturbed in-process; they arrive here as *declared proxies*
(``ledger_proxy_for`` in the manifest points at a ledger capability id).

Every handle provides **two implementations of one specification**:

* ``primary``   — the reference path (closed form);
* ``secondary`` — an independently written path (different algorithm: series /
  compensated summation / normalisation), rounded to ``precision_digits``
  significant digits so that the residual is realistic (~1e-9) rather than zero.

Handle identifiers are synthetic by construction (``synth.*``) so that the
mechanism layer never carries domain vocabulary.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping

from .model import HandleSpec, RelationKind, RelationSpec

SCHEMA_VERSION = "second-impl-manifest/1.0"

Oracle = Callable[[Mapping[str, float]], float]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _round_sig(value: float, digits: int) -> float:
    if value == 0.0 or not math.isfinite(value):
        return value
    return float(f"%.{max(digits - 1, 0)}e" % value)


def _kahan(values: List[float]) -> float:
    total = 0.0
    comp = 0.0
    for v in values:
        y = v - comp
        t = total + y
        comp = (t - total) - y
        total = t
    return total


# ---------------------------------------------------------------------------
# families: primary + secondary implementation of one specification
# ---------------------------------------------------------------------------

def _linear(params: Mapping[str, Any], digits: int):
    a = float(params["a"])

    def primary(p):
        return a * float(p["x"])

    def secondary(p):
        # same spec, different path: repeated addition accumulated with Kahan
        x = float(p["x"])
        n = 8
        return _round_sig(_kahan([a * x / n] * n), digits)

    return primary, secondary


def _power(params: Mapping[str, Any], digits: int):
    a, k = float(params["a"]), float(params["k"])

    def primary(p):
        return a * float(p["x"]) ** k

    def secondary(p):
        # exp/log path instead of pow
        x = float(p["x"])
        if x == 0.0:
            return 0.0
        return _round_sig(a * math.exp(k * math.log(x)), digits)

    return primary, secondary


def _quadratic(params: Mapping[str, Any], digits: int):
    a = float(params["a"])

    def primary(p):
        x = float(p["x"])
        return a * x * x

    def secondary(p):
        x = float(p["x"])
        # multiplication-free: (x+x+...+x) accumulated, then scaled
        xx = _kahan([abs(x)] * 4) * (1.0 if x >= 0 else -1.0)  # |x|*4 with sign
        return _round_sig(a * (xx * xx) / 16.0, digits)

    return primary, secondary


def _cubic(params: Mapping[str, Any], digits: int):
    a = float(params["a"])

    def primary(p):
        x = float(p["x"])
        return a * x * x * x

    def secondary(p):
        x = float(p["x"])
        if x == 0.0:
            return 0.0
        return _round_sig(a * math.exp(3.0 * math.log(abs(x))) * (1.0 if x >= 0 else -1.0), digits)

    return primary, secondary


def _superposition(params: Mapping[str, Any], digits: int):
    a, b = float(params["a"]), float(params["b"])

    def primary(p):
        return a * float(p["x1"]) + b * float(p["x2"])

    def secondary(p):
        parts = [a * float(p["x1"]), b * float(p["x2"]), 0.0]
        return _round_sig(_kahan(parts), digits)

    return primary, secondary


def _partition(params: Mapping[str, Any], digits: int):
    weights = [float(w) for w in params["weights"]]

    def primary(p):
        i = int(round(float(p["i"])))
        return weights[i] * float(p["total"])

    def secondary(p):
        i = int(round(float(p["i"])))
        total = float(p["total"])
        # normalise integer weight counts instead of reusing the float weights
        counts = [round(w * 1e9) for w in weights]
        norm = _kahan([float(c) for c in counts])
        return _round_sig(total * counts[i] / norm, digits)

    return primary, secondary


FAMILIES: Dict[str, Callable[[Mapping[str, Any], int], tuple[Oracle, Oracle]]] = {
    "linear": _linear,
    "power": _power,
    "quadratic": _quadratic,
    "cubic": _cubic,
    "superposition": _superposition,
    "partition": _partition,
}


def build_oracles(spec: HandleSpec) -> tuple[Oracle, Oracle]:
    """Return (primary, secondary) callables for a handle spec."""
    builder = FAMILIES.get(spec.family)
    if builder is None:
        raise KeyError(f"unknown family {spec.family!r} for handle {spec.handle_id}")
    return builder(spec.params, spec.precision_digits)


# ---------------------------------------------------------------------------
# probe construction
# ---------------------------------------------------------------------------

def _rel(kind: RelationKind, **params) -> RelationSpec:
    return RelationSpec(kind=kind, params=params)


def _probes(family: str, params: Mapping[str, Any]) -> Dict[str, Any]:
    """Probe points for a family (all numbers, serialisable)."""
    base = {"x": 1.3} if family != "superposition" else {"x1": 1.3, "x2": 0.7}
    if family == "partition":
        base = {"i": 0, "total": 12.5}
    p = {
        "base": base,
        "lam": 1.7,
        "shift": 0.6,
    }
    if family == "superposition":
        p["add_pair"] = [{"x1": 0.9, "x2": 0.4}, {"x1": 0.3, "x2": 0.8}]
        p["zero"] = {"x1": 0.0, "x2": 0.0}
    elif family == "partition":
        p["zero"] = {"i": 0, "total": 0.0}
        p["add_pair"] = [{"i": 0, "total": 5.0}, {"i": 0, "total": 7.5}]
        p["partition_index_range"] = len(params.get("weights", []))
        p["partition_total"] = 12.5
    else:
        p["zero"] = {"x": 0.0}
        p["add_pair"] = [{"x": 0.9}, {"x": 0.4}]
    return p


_SIG = 1e-6  # relative tolerance of the equivalence relation (second-impl noise ~1e-9)


def _relations(family: str) -> List[RelationSpec]:
    rels = [_rel(RelationKind.EQUIVALENCE, metric="rel_abs_diff", tolerance=_SIG)]
    if family == "linear":
        rels += [_rel(RelationKind.SCALING, degree=1.0, metric="rel_abs_diff", tolerance=1e-6),
                 _rel(RelationKind.ZERO_POINT, metric="abs_diff", tolerance=1e-9),
                 _rel(RelationKind.ADDITIVITY, metric="rel_abs_diff", tolerance=1e-6)]
    elif family == "power":
        rels += [_rel(RelationKind.SCALING, degree=2.0, metric="rel_abs_diff", tolerance=1e-6),
                 _rel(RelationKind.ZERO_POINT, metric="abs_diff", tolerance=1e-9)]
    elif family == "quadratic":
        rels += [_rel(RelationKind.SCALING, degree=2.0, metric="rel_abs_diff", tolerance=1e-6),
                 _rel(RelationKind.ZERO_POINT, metric="abs_diff", tolerance=1e-9),
                 _rel(RelationKind.MIRROR_PARITY, parity=1.0, metric="rel_abs_diff", tolerance=1e-6)]
    elif family == "cubic":
        rels += [_rel(RelationKind.SCALING, degree=3.0, metric="rel_abs_diff", tolerance=1e-6),
                 _rel(RelationKind.ZERO_POINT, metric="abs_diff", tolerance=1e-9),
                 _rel(RelationKind.MIRROR_PARITY, parity=-1.0, metric="rel_abs_diff", tolerance=1e-6)]
    elif family == "superposition":
        rels += [_rel(RelationKind.SCALING, degree=1.0, metric="rel_abs_diff", tolerance=1e-6),
                 _rel(RelationKind.ZERO_POINT, metric="abs_diff", tolerance=1e-9),
                 _rel(RelationKind.ADDITIVITY, metric="rel_abs_diff", tolerance=1e-6)]
    elif family == "partition":
        rels += [_rel(RelationKind.CONSERVATION, metric="rel_abs_diff", tolerance=1e-6),
                 _rel(RelationKind.ZERO_POINT, metric="abs_diff", tolerance=1e-9),
                 _rel(RelationKind.ADDITIVITY, metric="rel_abs_diff", tolerance=1e-6)]
    return rels


# ---------------------------------------------------------------------------
# population builder
# ---------------------------------------------------------------------------

_FAMILY_CYCLE = ["linear", "power", "quadratic", "cubic", "superposition", "partition"]

_TARGET_FAMILIES = ["partition", "power", "superposition"]


def build_population(n_controls: int = 20, n_targets: int = 3,
                     proxy_ids: List[str] | None = None) -> List[HandleSpec]:
    """Deterministically build ``n_targets`` targets plus ``n_controls`` controls.

    Targets carry a ``ledger_proxy_for`` id (the real ledger capability they
    stand in for); controls carry none.
    """
    proxy_ids = list(proxy_ids or [])
    specs: List[HandleSpec] = []

    for i in range(n_targets):
        family = _TARGET_FAMILIES[i % len(_TARGET_FAMILIES)]
        params = _family_params(family, i)
        specs.append(HandleSpec(
            handle_id=f"synth.target_{i + 1:02d}_{family}",
            family=family,
            handle_class="target",
            params=params,
            relations=tuple(_relations(family)),
            probes=_probes(family, params),
            precision_digits=9,
            ledger_proxy_for=(proxy_ids[i] if i < len(proxy_ids) else ""),
        ))

    for j in range(n_controls):
        family = _FAMILY_CYCLE[j % len(_FAMILY_CYCLE)]
        params = _family_params(family, j + 100)
        specs.append(HandleSpec(
            handle_id=f"synth.control_{j + 1:02d}_{family}",
            family=family,
            handle_class="control",
            params=params,
            relations=tuple(_relations(family)),
            probes=_probes(family, params),
            precision_digits=9,
        ))
    return specs


def _family_params(family: str, seed: int) -> Dict[str, Any]:
    if family == "linear":
        return {"a": round(0.5 + 0.037 * (seed % 17), 6)}
    if family == "power":
        return {"a": round(0.8 + 0.021 * (seed % 13), 6), "k": 2.0}
    if family == "quadratic":
        return {"a": round(0.3 + 0.013 * (seed % 11), 6)}
    if family == "cubic":
        return {"a": round(0.2 + 0.007 * (seed % 9), 6)}
    if family == "superposition":
        return {"a": round(0.6 + 0.019 * (seed % 7), 6), "b": round(1.1 + 0.011 * (seed % 5), 6)}
    if family == "partition":
        k = 4
        raw = [1.0 + 0.25 * ((seed + t) % 5) for t in range(k)]
        total = sum(raw)
        return {"weights": [round(w / total, 12) for w in raw]}
    raise KeyError(family)


def build_manifest(n_controls: int = 20, n_targets: int = 3,
                   proxy_ids: List[str] | None = None) -> Dict[str, Any]:
    """Serialisable manifest artifact for the bench."""
    specs = build_population(n_controls=n_controls, n_targets=n_targets, proxy_ids=proxy_ids)
    return {
        "schema_version": SCHEMA_VERSION,
        "note": ("Synthetic handle population. Handles marked 'target' are the injected "
                 "capabilities (ledger_proxy_for names the real ledger capability they "
                 "stand in for); 'control' handles are untouched and measure false alarms."),
        "n_controls": n_controls,
        "n_targets": n_targets,
        "handles": [s.to_dict() for s in specs],
    }


def load_manifest(manifest: Mapping[str, Any]) -> List[HandleSpec]:
    return [HandleSpec.from_dict(raw) for raw in manifest["handles"]]