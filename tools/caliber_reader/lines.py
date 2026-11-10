"""Schema-driven correlation-line evaluator.

Platform rule (zero application-domain semantics): the *mechanism* lives in code,
every physical number lives in injected configuration.  A "correlation line" is a
named scalar function of one positive input, fully described by a JSON spec::

    {"kind": "log_power",    "a": .., "b": .., "c": ..}          f(x) = a / (log10(x) - b)**c
    {"kind": "power_offset", "a": .., "b": .., "c": .., "d": ..} f(x) = a*(x + b)**(-c) + d
    {"kind": "constant",     "value": ..}                       f(x) = const

Optional keys per line:

``valid_range``  ``[lo, hi]`` (inclusive).  Requests outside the range are *not*
                   silently extrapolated: the result carries ``in_range=False`` and
                   callers must treat the value as unusable (fail-closed).
``clamp``        bool, clamp the argument into ``valid_range`` before evaluation
                   (recorded in the note), implies ``in_range=True`` on the clamped
                   value but leaves ``clamped=True`` visible.
``label``        free-text provenance string carried through to reports.

Nothing in this module knows what the lines physically mean; the caller decides
which line plays which role (e.g. ``viscous_line`` / ``allowance_line``).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional, Tuple


class LineError(ValueError):
    """Raised when a line spec is malformed or cannot be evaluated at all."""


@dataclass(frozen=True)
class LineResult:
    """Outcome of one line evaluation (never raises on range issues)."""

    name: str
    x: float
    value: float
    in_range: bool
    clamped: bool = False
    note: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "line": self.name,
            "x": self.x,
            "value": self.value,
            "in_range": self.in_range,
            "clamped": self.clamped,
            "note": self.note,
        }


@dataclass(frozen=True)
class LineSpec:
    name: str
    kind: str
    params: Dict[str, float] = field(default_factory=dict)
    valid_range: Optional[Tuple[float, float]] = None
    clamp: bool = False
    label: str = ""

    @classmethod
    def from_mapping(cls, name: str, spec: Mapping[str, object]) -> "LineSpec":
        if not isinstance(spec, Mapping):
            raise LineError(f"line {name!r}: spec must be a mapping")
        kind = str(spec.get("kind", ""))
        if kind not in ("log_power", "power_offset", "constant"):
            raise LineError(f"line {name!r}: unknown kind {kind!r}")
        params = {k: float(v) for k, v in spec.items()
                  if k in ("a", "b", "c", "d", "value")}
        need = {"log_power": ("a", "b", "c"),
                "power_offset": ("a", "b", "c"),
                "constant": ("value",)}[kind]
        missing = [k for k in need if k not in params]
        if missing:
            raise LineError(f"line {name!r}: missing params {missing}")
        rng = spec.get("valid_range")
        valid_range = None
        if rng is not None:
            lo, hi = float(rng[0]), float(rng[1])
            if hi <= lo:
                raise LineError(f"line {name!r}: valid_range must be increasing")
            valid_range = (lo, hi)
        return cls(name=name, kind=kind, params=params, valid_range=valid_range,
                   clamp=bool(spec.get("clamp", False)), label=str(spec.get("label", "")))

    def evaluate(self, x: float) -> LineResult:
        x = float(x)
        if not math.isfinite(x):
            raise LineError(f"line {self.name!r}: non-finite argument {x!r}")
        clamped = False
        in_range = True
        note = ""
        if self.valid_range is not None:
            lo, hi = self.valid_range
            if x < lo or x > hi:
                if self.clamp:
                    x = min(max(x, lo), hi)
                    clamped = True
                    note = f"clamped into [{lo:g}, {hi:g}]"
                else:
                    in_range = False
                    note = f"argument outside declared range [{lo:g}, {hi:g}]"
        value = self._raw(x)
        if not math.isfinite(value):
            raise LineError(f"line {self.name!r}: non-finite result at x={x!r}")
        return LineResult(name=self.name, x=x, value=value, in_range=in_range,
                          clamped=clamped, note=note)

    def _raw(self, x: float) -> float:
        if self.kind == "constant":
            return float(self.params["value"])
        if self.kind == "log_power":
            if x <= 0.0:
                raise LineError(f"line {self.name!r}: log-line needs x > 0 (got {x!r})")
            a, b, c = self.params["a"], self.params["b"], self.params["c"]
            denom = math.log10(x) - b
            if denom == 0.0:
                raise LineError(f"line {self.name!r}: singular at x={x!r}")
            return a / (denom ** c)
        if self.kind == "power_offset":
            a, b, c = self.params["a"], self.params["b"], self.params["c"]
            d = self.params.get("d", 0.0)
            base = x + b
            if base <= 0.0:
                raise LineError(f"line {self.name!r}: (x + b) must be > 0 (got {base!r})")
            return a * (base ** (-c)) + d
        raise LineError(f"line {self.name!r}: unknown kind {self.kind!r}")

    def as_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {"kind": self.kind, **self.params}
        if self.valid_range is not None:
            out["valid_range"] = list(self.valid_range)
        if self.clamp:
            out["clamp"] = True
        if self.label:
            out["label"] = self.label
        return out


class LineSet:
    """A named bundle of :class:`LineSpec`; the only way the engine gets numbers."""

    def __init__(self, specs: Mapping[str, LineSpec], source_fingerprint: str = ""):
        self._specs: Dict[str, LineSpec] = dict(specs)
        self.source_fingerprint = source_fingerprint

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "LineSet":
        specs = {str(name): LineSpec.from_mapping(str(name), spec)
                 for name, spec in mapping.items()}
        payload = json.dumps({n: s.as_dict() for n, s in sorted(specs.items())},
                             sort_keys=True, separators=(",", ":"))
        return cls(specs, hashlib.sha256(payload.encode("utf-8")).hexdigest())

    # -- access ------------------------------------------------------------
    def names(self) -> Iterable[str]:
        return tuple(self._specs)

    def has(self, name: str) -> bool:
        return name in self._specs

    def spec(self, name: str) -> LineSpec:
        if name not in self._specs:
            raise LineError(f"line {name!r} is not configured (have {sorted(self._specs)})")
        return self._specs[name]

    def eval(self, name: str, x: float) -> LineResult:
        return self.spec(name).evaluate(x)

    def evaluate_optional(self, name: Optional[str], x: float,
                          default: float = 0.0) -> LineResult:
        """Evaluate ``name`` when configured, else a zero-valued constant line."""
        if name and self.has(name):
            return self.eval(name, x)
        return LineResult(name=name or "<none>", x=float(x), value=float(default),
                          in_range=True, note="line not configured -> neutral value")

    def as_dict(self) -> Dict[str, object]:
        return {n: self._specs[n].as_dict() for n in sorted(self._specs)}


def load_lineset(path) -> LineSet:
    """Load a ``LineSet`` from a JSON file (the configuration-injection point)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, Mapping) and "lines" in data:
        data = data["lines"]
    return LineSet.from_mapping(data)