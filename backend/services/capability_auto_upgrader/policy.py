"""The upgrade policy: thresholds as data, with a digest for provenance.

The rule the node asks for is stated once here and nowhere else:

    a capability is promoted to ``REAL`` when the last ``promotion_window``
    (default 3) consecutive runs *each* show a pytest pass rate >= 90 % and a
    benchmark error < 15 % against a reference value.

Two guards live next to it and are just as important:

* **No promotion on frozen evidence.**  A streak is built from *distinct* run
  digests, so replaying one good report three times promotes nothing.
* **No demotion on missing evidence.**  A run with no executed tests (or too few
  benchmark samples) is *insufficient*, not *contradictory*: it breaks a
  promotion streak but never withdraws an existing level.  Only a conclusive
  failure demotes, and only down to ``min_level`` (default ``MOCK``), because a
  red suite cannot turn an implementation into an empty shell.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Mapping

from .model import LEVEL_INDEX, LEVELS, SCHEMA, EvidenceError, normalise_level, sha256_obj


@dataclass(frozen=True)
class UpgradePolicy:
    promotion_window: int = 3
    demotion_window: int = 1
    min_pass_rate: float = 0.90
    max_error_pct: float = 15.0
    min_benchmark_samples: int = 1
    demotion_steps: int = 1
    min_level: str = "MOCK"
    schema: str = f"{SCHEMA}/policy"

    def __post_init__(self) -> None:
        for name in ("promotion_window", "demotion_window", "min_benchmark_samples", "demotion_steps"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise EvidenceError(f"policy.{name} must be a positive integer, got {value!r}")
        if not isinstance(self.min_pass_rate, (int, float)) or isinstance(self.min_pass_rate, bool):
            raise EvidenceError("policy.min_pass_rate must be numeric")
        if not 0.0 <= float(self.min_pass_rate) <= 1.0:
            raise EvidenceError("policy.min_pass_rate must be within [0, 1]")
        if not isinstance(self.max_error_pct, (int, float)) or isinstance(self.max_error_pct, bool):
            raise EvidenceError("policy.max_error_pct must be numeric")
        if float(self.max_error_pct) <= 0.0:
            raise EvidenceError("policy.max_error_pct must be > 0")
        object.__setattr__(self, "min_level", normalise_level(self.min_level))
        if LEVEL_INDEX[self.min_level] >= LEVEL_INDEX["REAL"]:
            raise EvidenceError("policy.min_level must be below REAL")

    # -- serialisation ---------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["min_pass_rate"] = float(self.min_pass_rate)
        payload["max_error_pct"] = float(self.max_error_pct)
        return payload

    @property
    def digest(self) -> str:
        return sha256_obj(self.as_dict())

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "UpgradePolicy":
        if not isinstance(payload, Mapping):
            raise EvidenceError("policy must be a mapping")
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(payload) - known
        if unknown:
            raise EvidenceError(f"unknown policy keys: {sorted(unknown)}")
        return cls(**dict(payload))

    def describe(self) -> str:
        return (
            f"promote when {self.promotion_window} consecutive runs have "
            f"pass_rate >= {self.min_pass_rate:.2%} and error < {self.max_error_pct:g}%; "
            f"demote one rung after {self.demotion_window} consecutive conclusive failure(s), "
            f"floor = {self.min_level}"
        )