"""Shared types, validation and error handling for the ice-region digital twin.

Design notes
------------
* All surrogate interfaces are dict-in / dict-out (``Mapping[str, float]``) so
  that they can be serialised over HTTP and versioned independently.
* Geometry is validated before any physics call: a hull must be described by
  consistent ``Cb`` / ``L`` / ``B`` / ``T`` (rule: geometry first, physics
  second).
* Every failure mode used by the orchestrator degrades to a declared
  ``degraded`` flag instead of raising through the API layer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

API_VERSION = "1.0.0"
SCHEMA_VERSION = "2026-10-31"
DT_VERSION = "1.0.0"


class DigitalTwinError(Exception):
    """Base class for digital-twin failures."""


class InputValidationError(DigitalTwinError):
    """Raised when a surrogate / sensor payload violates its schema."""


class SurrogateNotFound(DigitalTwinError):
    """Raised when a surrogate name is not registered."""


class SurrogateExecutionError(DigitalTwinError):
    """Raised when a surrogate fails during ``predict``."""


class GeometryValidationError(DigitalTwinError):
    """Raised when ``Cb`` / ``L`` / ``B`` / ``T`` are inconsistent."""


# ---------------------------------------------------------------------------
# schema helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Field:
    """Declarative description of one surrogate input/output field."""

    name: str
    unit: str = "-"
    lower: float = -math.inf
    upper: float = math.inf
    description: str = ""

    def check(self, value: float) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise InputValidationError(f"field '{self.name}' must be numeric")
        v = float(value)
        if not math.isfinite(v):
            raise InputValidationError(f"field '{self.name}' must be finite")
        if v < self.lower or v > self.upper:
            raise InputValidationError(
                f"field '{self.name}'={v} outside [{self.lower}, {self.upper}]"
            )
        return v


@dataclass(frozen=True)
class Schema:
    """Input or output schema of a surrogate."""

    fields: List[Field] = field(default_factory=list)

    @property
    def names(self) -> List[str]:
        return [f.name for f in self.fields]

    def validate(self, payload: Mapping[str, Any], *, strict: bool = False) -> Dict[str, float]:
        if not isinstance(payload, Mapping):
            raise InputValidationError("payload must be a mapping")
        missing = [f.name for f in self.fields if f.name not in payload and not strict]
        out: Dict[str, float] = {}
        for f in self.fields:
            if f.name in payload:
                out[f.name] = f.check(payload[f.name])
            elif not strict:
                # defaults are supplied by the surrogate adapters, not here
                continue
        if strict and missing:
            raise InputValidationError(f"missing required fields: {missing}")
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fields": [
                {
                    "name": f.name,
                    "unit": f.unit,
                    "range": [None if math.isinf(f.lower) else f.lower,
                              None if math.isinf(f.upper) else f.upper],
                    "description": f.description,
                }
                for f in self.fields
            ]
        }


def require(payload: Mapping[str, Any], fields: Iterable[Field]) -> Dict[str, float]:
    """Validate and return the required subset of ``payload``."""
    out: Dict[str, float] = {}
    for f in fields:
        if f.name not in payload:
            raise InputValidationError(f"missing required field '{f.name}'")
        out[f.name] = f.check(payload[f.name])
    return out


# ---------------------------------------------------------------------------
# latency bookkeeping
# ---------------------------------------------------------------------------

class LatencyRecorder:
    """Bounded latency recorder producing p50 / p95 / p99 percentiles."""

    def __init__(self, capacity: int = 2048):
        self.capacity = int(capacity)
        self._samples: List[float] = []

    def add(self, ms: float) -> None:
        self._samples.append(float(ms))
        if len(self._samples) > self.capacity:
            del self._samples[: len(self._samples) - self.capacity]

    def __len__(self) -> int:
        return len(self._samples)

    @staticmethod
    def _pct(sorted_vals: List[float], q: float) -> Optional[float]:
        if not sorted_vals:
            return None
        if len(sorted_vals) == 1:
            return sorted_vals[0]
        rank = q * (len(sorted_vals) - 1)
        lo = int(math.floor(rank))
        hi = int(math.ceil(rank))
        if lo == hi:
            return sorted_vals[lo]
        w = rank - lo
        return sorted_vals[lo] * (1.0 - w) + sorted_vals[hi] * w

    def stats(self) -> Dict[str, Any]:
        vals = sorted(self._samples)
        if not vals:
            return {"count": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None,
                    "max_ms": None, "mean_ms": None}
        return {
            "count": len(vals),
            "p50_ms": round(self._pct(vals, 0.50), 4),
            "p95_ms": round(self._pct(vals, 0.95), 4),
            "p99_ms": round(self._pct(vals, 0.99), 4),
            "max_ms": round(vals[-1], 4),
            "mean_ms": round(sum(vals) / len(vals), 4),
        }

    def reset(self) -> None:
        self._samples.clear()


# ---------------------------------------------------------------------------
# geometry guard (rule: validate Cb / L / B / T before any physics)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HullGeometry:
    """Principal particulars of the hull, validated for consistency."""

    L: float          # length between perpendiculars [m]
    B: float          # beam [m]
    T: float          # draft [m]
    Cb: float         # block coefficient [-]
    displacement_t: Optional[float] = None  # reported displacement [t]
    rho: float = 1025.0

    def __post_init__(self) -> None:
        for name in ("L", "B", "T"):
            v = getattr(self, name)
            if not math.isfinite(v) or v <= 0:
                raise GeometryValidationError(f"{name} must be positive and finite, got {v}")
        if not (0.20 <= self.Cb <= 0.95):
            raise GeometryValidationError(f"Cb={self.Cb} outside [0.20, 0.95]")
        if not (2.0 <= self.L / self.B <= 14.0):
            raise GeometryValidationError(f"L/B={self.L / self.B:.2f} outside [2, 14]")
        if not (0.005 <= self.B / self.L <= 0.50):
            raise GeometryValidationError(f"B/L={self.B / self.L:.3f} outside [0.005, 0.5]")
        if not (0.02 <= self.T / self.B <= 0.60):
            raise GeometryValidationError(f"T/B={self.T / self.B:.3f} outside [0.02, 0.6]")
        if self.displacement_t is not None:
            vol = self.Cb * self.L * self.B * self.T
            implied = vol * self.rho / 1000.0
            if abs(implied - self.displacement_t) / max(implied, 1e-9) > 0.15:
                raise GeometryValidationError(
                    f"displacement {self.displacement_t} t inconsistent with "
                    f"Cb*L*B*T -> {implied:.0f} t (tolerance 15%)"
                )

    # -- derived quantities -------------------------------------------------
    @property
    def volume(self) -> float:
        return self.Cb * self.L * self.B * self.T

    @property
    def mass(self) -> float:
        return self.volume * self.rho

    def to_dict(self) -> Dict[str, float]:
        return {
            "L": self.L, "B": self.B, "T": self.T, "Cb": self.Cb,
            "volume_m3": round(self.volume, 2),
            "mass_t": round(self.mass / 1000.0, 1),
            "L_over_B": round(self.L / self.B, 3),
            "B_over_T": round(self.B / self.T, 3),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "HullGeometry":
        need = ["L", "B", "T", "Cb"]
        missing = [k for k in need if k not in payload]
        if missing:
            raise GeometryValidationError(f"hull payload missing {missing}")
        return cls(
            L=float(payload["L"]),
            B=float(payload["B"]),
            T=float(payload["T"]),
            Cb=float(payload["Cb"]),
            displacement_t=(float(payload["displacement_t"])
                            if payload.get("displacement_t") is not None else None),
            rho=float(payload.get("rho", 1025.0)),
        )