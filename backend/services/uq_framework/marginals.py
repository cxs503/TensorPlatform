"""Uncertain-input marginals with an explicit isoprobabilistic map.

Why this file exists
--------------------
The platform's existing pieces (``backend/services/uq_propagation.py``,
``backend/services/pce_uq/marginals.py``) cover normal / lognormal / uniform
inputs.  Two gaps block a *generic* uncertainty framework:

1. **Triangular** marginals (the honest way to state "expert min / most likely /
   max") were not supported, although they are the only bounded, skewed family
   that needs no truncation argument at all.
2. Different families are handled by different orthogonal bases, which means a
   mixed-family input vector cannot be fed through one polynomial-chaos fit.

Both are solved here by keeping one canonical representation: every marginal
exposes its inverse CDF ``from_unit`` and the *isoprobabilistic map*

    xi = 2 F_X(x) - 1  in  [-1, 1],  dzeta = 1/2 on [-1, 1],

so any continuous input becomes uniform on ``[-1, 1]`` regardless of family.
Polynomial chaos on ``xi`` is then a fixed Legendre chaos (see :mod:`.pce`),
and -- because the Sobol'-Hoeffding decomposition is invariant under
coordinate-wise bijections -- the variance shares computed in ``xi``-space are
the variance shares of the *physical* inputs.  The map is monotone, hence
invertible; the Jacobian only rescales ``f(x)`` pointwise and cannot move
variance between input subsets.

No distribution constants are invented: triangular moments are the textbook
``(a + b + c)/3`` and ``(a^2 + b^2 + c^2 - a b - a c - b c)/18``, uniform
moments ``(a+b)/2`` and ``(b-a)^2/12``, normal moments ``mu`` and ``sigma^2``
(Bessel-corrected sample moments are used for all *empirical* comparisons).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Sequence

import numpy as np

__all__ = ["InputDistribution", "distributions_from_specs", "validate_distributions"]

_FAMILIES = ("normal", "uniform", "triangular")


@dataclass(frozen=True)
class InputDistribution:
    """One uncertain scalar input.

    Parameters are carried in ``params`` so that the object is trivially
    JSON-serialisable and survives a round trip through a report file.

    ``normal``     : ``mean``, ``std``
    ``uniform``    : ``lower``, ``upper``
    ``triangular`` : ``lower``, ``mode``, ``upper``
    """

    name: str
    family: str
    params: Mapping[str, float] = field(default_factory=dict)

    # -- construction ------------------------------------------------------
    def __post_init__(self) -> None:
        fam = str(self.family).lower()
        if fam not in _FAMILIES:
            raise ValueError(f"{self.name}: family must be one of {_FAMILIES}, got {self.family!r}")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("input name must be a non-empty string")
        object.__setattr__(self, "family", fam)
        object.__setattr__(self, "params", {k: float(v) for k, v in dict(self.params).items()})
        p = dict(self.params)
        if fam == "normal":
            if not (np.isfinite(p.get("std", np.nan)) and p["std"] > 0.0):
                raise ValueError(f"{self.name}: normal needs std > 0")
            if not np.isfinite(p.get("mean", np.nan)):
                raise ValueError(f"{self.name}: normal needs a finite mean")
        elif fam == "uniform":
            if not (np.isfinite(p.get("lower", np.nan)) and np.isfinite(p.get("upper", np.nan))):
                raise ValueError(f"{self.name}: uniform needs finite lower/upper")
            if not p["upper"] > p["lower"]:
                raise ValueError(f"{self.name}: uniform needs upper > lower")
        else:  # triangular
            need = ("lower", "mode", "upper")
            if any(not np.isfinite(p.get(k, np.nan)) for k in need):
                raise ValueError(f"{self.name}: triangular needs finite lower/mode/upper")
            if not (p["lower"] < p["upper"]):
                raise ValueError(f"{self.name}: triangular needs upper > lower")
            if not (p["lower"] <= p["mode"] <= p["upper"]):
                raise ValueError(f"{self.name}: triangular needs lower <= mode <= upper")

    # -- named constructors ------------------------------------------------
    @staticmethod
    def normal(name: str, mean: float, std: float) -> "InputDistribution":
        return InputDistribution(name, "normal", {"mean": mean, "std": std})

    @staticmethod
    def uniform(name: str, lower: float, upper: float) -> "InputDistribution":
        return InputDistribution(name, "uniform", {"lower": lower, "upper": upper})

    @staticmethod
    def triangular(name: str, lower: float, mode: float, upper: float) -> "InputDistribution":
        return InputDistribution(name, "triangular",
                                 {"lower": lower, "mode": mode, "upper": upper})

    @staticmethod
    def from_spec(spec: Mapping[str, Any]) -> "InputDistribution":
        """Build from a JSON-friendly dict.

        Accepts either ``{"name":.., "family":.., **params}`` (flat, convenient
        in case files) or ``{"name":.., "family":.., "params":{...}}``.
        """
        fam = str(spec["family"]).lower()
        if "params" in spec:
            params = dict(spec["params"])
        else:
            params = {k: v for k, v in spec.items() if k not in ("name", "family", "params")}
        return InputDistribution(str(spec["name"]), fam, params)

    # -- moments -----------------------------------------------------------
    @property
    def mean(self) -> float:
        p = self.params
        if self.family == "normal":
            return float(p["mean"])
        if self.family == "uniform":
            return float(0.5 * (p["lower"] + p["upper"]))
        a, c, b = p["lower"], p["mode"], p["upper"]
        return float((a + b + c) / 3.0)

    @property
    def variance(self) -> float:
        p = self.params
        if self.family == "normal":
            return float(p["std"] ** 2)
        if self.family == "uniform":
            return float((p["upper"] - p["lower"]) ** 2 / 12.0)
        a, c, b = p["lower"], p["mode"], p["upper"]
        return float((a * a + b * b + c * c - a * b - a * c - b * c) / 18.0)

    @property
    def std(self) -> float:
        return float(np.sqrt(self.variance))

    # -- inverse CDF (isoprobabilistic map) --------------------------------
    def from_unit(self, u: Any) -> np.ndarray:
        """Map ``u`` in (0, 1) onto the physical support (inverse CDF)."""
        u = np.clip(np.asarray(u, dtype=float), 1e-15, 1.0 - 1e-15)
        p = self.params
        if self.family == "normal":
            from scipy.special import ndtri  # local import keeps numpy-only use cheap

            return float(p["mean"]) + float(p["std"]) * ndtri(u)
        if self.family == "uniform":
            return float(p["lower"]) + (float(p["upper"]) - float(p["lower"])) * u
        a, c, b = float(p["lower"]), float(p["mode"]), float(p["upper"])
        span = b - a
        split = (c - a) / span
        left = u <= split
        out = np.empty_like(u)
        # left branch: x = a + sqrt(u * (b-a) * (c-a))
        out[left] = a + np.sqrt(u[left] * span * (c - a))
        # right branch: x = b - sqrt((1-u) * (b-a) * (b-c))
        out[~left] = b - np.sqrt((1.0 - u[~left]) * span * (b - c))
        return np.clip(out, a, b)

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "family": self.family, "params": dict(self.params)}

    def describe(self) -> str:
        inner = ", ".join(f"{k}={v:g}" for k, v in sorted(self.params.items()))
        return f"{self.name}:{self.family}({inner})"


def distributions_from_specs(specs: Sequence[Any]) -> list:
    """Coerce a mixed list of dicts / :class:`InputDistribution` to objects."""
    out = []
    for s in specs:
        out.append(s if isinstance(s, InputDistribution) else InputDistribution.from_spec(s))
    return out


def validate_distributions(specs: Sequence[Any]) -> list:
    """Coerce *and* reject duplicate / empty input sets."""
    dists = distributions_from_specs(specs)
    if not dists:
        raise ValueError("at least one input distribution is required")
    names = [d.name for d in dists]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate input names: {names}")
    return dists