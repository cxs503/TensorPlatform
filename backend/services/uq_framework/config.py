"""``UQConfig`` -- the single declarative object a UQ run is defined by.

Everything that can change a reported number is a field here: the marginals,
the propagation method, sample budgets, the numerical-error evidence, the
model-form ensemble, and the acceptance thresholds.  Nothing is read from a
module-level constant, so a run is reproducible from ``config.to_dict()`` alone
and an auditor never has to guess an implicit default.

Validation happens in ``__post_init__`` (fail fast, before any model is
evaluated), and unknown keys are rejected rather than silently ignored -- a
typo in a case file must not become a silently different analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .marginals import InputDistribution, validate_distributions

__all__ = ["UQConfig", "DEFAULT_CRITERIA", "PROPAGATION_METHODS", "jsonable"]

PROPAGATION_METHODS = ("monte_carlo", "pce", "both")

#: Acceptance thresholds of the framework contract (v1).
DEFAULT_CRITERIA: Dict[str, float] = {
    "mc_moment_rel_err_max": 0.02,      # C1: MC mean/variance vs analytic
    "pce_vs_mc_rel_max": 0.05,          # C2: PCE statistics vs MC statistics
    "pce_speedup_min": 10.0,            # C2: model evaluations saved
    "gci_order_rel_err_max": 0.10,      # C3: observed order vs formal order
    "model_form_rel_err_max": 0.10,     # C4: ensemble mean vs truth
    "pce_r2_min": 0.99,                 # surrogate adequacy gate
}

_TOP_KEYS = {
    "case_id", "title", "input_distributions", "propagation_method", "n_mc",
    "design", "pce_order", "pce_train_factor", "pce_holdout", "ci_level",
    "quantiles", "sobol", "sobol_n_base", "sobol_max_order", "seed",
    "numerical", "model_form", "criteria", "call_style", "notes", "reference",
}



def jsonable(obj: Any) -> Any:
    """Reversible-enough JSON view of a config value.

    Evidence payloads legitimately carry numpy arrays and callables (a grid
    triplet's linear system, a truth model).  Embedding them verbatim in a
    report would make it huge and unserialisable, so arrays collapse to
    ``{"array": [shape], "n": size}`` and callables to ``"<callable>"``.  The
    numbers that *decide* a criterion are never inside those objects, so no
    verdict becomes unauditable.
    """
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if callable(obj):
        return "<callable>"
    if isinstance(obj, Mapping):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    shape = getattr(obj, "shape", None)
    if shape is not None:
        return {"array": [int(x) for x in shape], "n": int(getattr(obj, "size", 0))}
    return str(obj)


@dataclass
class UQConfig:
    """Declarative definition of one uncertainty-quantification analysis."""

    case_id: str
    input_distributions: Sequence[Any]
    title: str = ""
    propagation_method: str = "both"
    n_mc: int = 1000
    design: str = "lhs"
    pce_order: int = 3
    pce_train_factor: float = 3.0
    pce_holdout: int = 64
    ci_level: float = 0.95
    quantiles: Sequence[float] = (0.05, 0.5, 0.95)
    sobol: bool = True
    sobol_n_base: int = 512
    sobol_max_order: Optional[int] = None
    seed: int = 0
    numerical: Optional[Mapping[str, Any]] = None
    model_form: Optional[Mapping[str, Any]] = None
    criteria: Mapping[str, float] = field(default_factory=dict)
    call_style: str = "kwargs"
    notes: str = ""
    #: optional analytic reference ``{"mean":.., "std":..}`` used to score C1
    reference: Optional[Mapping[str, Any]] = None

    def __post_init__(self) -> None:
        if not str(self.case_id):
            raise ValueError("case_id must be a non-empty string")
        self.input_distributions = validate_distributions(self.input_distributions)
        method = str(self.propagation_method).lower()
        if method not in PROPAGATION_METHODS:
            raise ValueError(f"propagation_method must be one of {PROPAGATION_METHODS}")
        self.propagation_method = method
        if self.design not in ("mc", "lhs"):
            raise ValueError("design must be 'mc' or 'lhs'")
        if int(self.n_mc) < 10:
            raise ValueError("n_mc must be >= 10")
        if int(self.pce_order) < 1:
            raise ValueError("pce_order must be >= 1")
        if float(self.pce_train_factor) <= 0:
            raise ValueError("pce_train_factor must be > 0")
        if not (0.0 < float(self.ci_level) < 1.0):
            raise ValueError("ci_level must lie in (0, 1)")
        if self.call_style not in ("kwargs", "array"):
            raise ValueError("call_style must be 'kwargs' or 'array'")
        if self.sobol_max_order is not None:
            d = len(self.input_distributions)
            self.sobol_max_order = max(1, min(int(self.sobol_max_order), d))
        user = dict(self.criteria or {})
        unknown = sorted(set(user) - set(DEFAULT_CRITERIA))
        if unknown:
            raise ValueError(f"unknown criteria keys: {unknown}")
        self.criteria = {**DEFAULT_CRITERIA, **{k: float(v) for k, v in user.items()}}
        for key in ("numerical", "model_form", "reference"):
            val = getattr(self, key)
            if val is not None:
                setattr(self, key, dict(val))

    # -- derived -----------------------------------------------------------
    @property
    def names(self) -> List[str]:
        return [d.name for d in self.input_distributions]

    @property
    def dimension(self) -> int:
        return len(self.input_distributions)

    def pce_train_size(self, n_terms: int) -> int:
        return int(max(n_terms + 5, round(float(self.pce_train_factor) * n_terms)))

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "input_distributions": [d.to_dict() for d in self.input_distributions],
            "propagation_method": self.propagation_method,
            "n_mc": int(self.n_mc),
            "design": self.design,
            "pce_order": int(self.pce_order),
            "pce_train_factor": float(self.pce_train_factor),
            "pce_holdout": int(self.pce_holdout),
            "ci_level": float(self.ci_level),
            "quantiles": [float(q) for q in self.quantiles],
            "sobol": bool(self.sobol),
            "sobol_n_base": int(self.sobol_n_base),
            "sobol_max_order": self.sobol_max_order,
            "seed": int(self.seed),
            "numerical": jsonable(dict(self.numerical)) if self.numerical else None,
            "model_form": jsonable(dict(self.model_form)) if self.model_form else None,
            "criteria": {k: float(v) for k, v in self.criteria.items()},
            "call_style": self.call_style,
            "notes": self.notes,
            "reference": dict(self.reference) if self.reference else None,
        }

    @staticmethod
    def from_dict(doc: Mapping[str, Any]) -> "UQConfig":
        unknown = sorted(set(doc) - _TOP_KEYS)
        if unknown:
            raise ValueError(f"unknown config keys: {unknown}")
        payload = {k: v for k, v in doc.items() if k in _TOP_KEYS}
        if "input_distributions" not in payload:
            raise ValueError("config needs input_distributions")
        return UQConfig(**payload)

    def describe(self) -> str:
        inputs = ", ".join(d.describe() for d in self.input_distributions)
        return f"{self.case_id} [{self.propagation_method}] inputs({inputs}) n_mc={self.n_mc}"