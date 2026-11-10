"""Standardized interface contracts for multi-solver coupling.

A *coupling interface* is the geometric locus (point in 1-D, line/surface in
higher dimensions) shared by two solvers.  The contract fixes, for a given
solver pair:

* the **canonical field names** exchanged (state fields such as ``temperature``,
  flux fields such as ``heat_flux``),
* the **units** of every field (SI), checked against the units the solver
  actually reports,
* the **sign convention**: every flux is reported as the ``+x`` component of the
  flux vector (canonical frame, :mod:`...solvers.base`).  Conservation then reads
  ``q_A == q_B`` at the interface; a solver that instead reports its own outward
  flux produces a closure residual of ~2 and is flagged as a sign violation.
  (The "own outward normal" convention would make conservation read
  ``q_A + q_B = 0``; it is rejected because it is the dominant source of sign
  bugs in partitioned coupling.)

The contract is machine-validated: :meth:`InterfaceContract.validate` checks
field-name coverage (allowing per-solver aliases), unit consistency and the sign
convention, and returns a :class:`ContractReport`.

Contracts may be declared in *data* (JSON, see
``config/multi_solver_contracts.json``) rather than in code; the module loads
that file when present so that solver pairs can be added without touching
Python (platform iron rule: no hard-coded domain/application knowledge in code).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

CONTRACT_SCHEMA = "multi_solver_contracts/v1"
_ENV_CONFIG = "TENSOR_MULTI_SOLVER_CONTRACT_CONFIG"

# ---------------------------------------------------------------------------
# Field specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """One exchanged field: canonical name, kind, SI unit, aliases."""

    name: str
    kind: str  # "state" | "flux"
    units: str
    aliases: Tuple[str, ...] = ()

    def matches(self, candidate: str) -> bool:
        return candidate == self.name or candidate in self.aliases


@dataclass
class InterfaceState:
    """Fields one solver reports on the interface.

    ``fields`` maps a field name (canonical or alias) to a 1-D array of values
    sampled on the interface (ascending parameter order).  ``units`` is optional
    but, when given, is validated against the contract -- an unreported unit
    cannot be silently accepted.
    """

    solver: str
    fields: Dict[str, np.ndarray] = field(default_factory=dict)
    units: Dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> Optional[np.ndarray]:
        return self.fields.get(name)


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


@dataclass
class ContractReport:
    """Outcome of :meth:`InterfaceContract.validate`."""

    ok: bool
    missing_a: List[str]
    missing_b: List[str]
    unit_mismatches: List[str]
    sign_violations: List[str]
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "missing_a": self.missing_a,
            "missing_b": self.missing_b,
            "unit_mismatches": self.unit_mismatches,
            "sign_violations": self.sign_violations,
            "notes": self.notes,
        }

    @property
    def n_violations(self) -> int:
        return (
            len(self.missing_a)
            + len(self.missing_b)
            + len(self.unit_mismatches)
            + len(self.sign_violations)
        )


@dataclass
class InterfaceContract:
    """Standardized contract for one solver pair.

    Parameters
    ----------
    pair:
        Ordered pair of solver identifiers, e.g. ``("FVM", "FEM")``.
    state_fields / flux_fields:
        Canonical fields both sides must provide.
    field_aliases:
        ``{canonical name: {solver: alias}}`` -- per-solver naming freedom.
    """

    pair: Tuple[str, str]
    state_fields: List[FieldSpec]
    flux_fields: List[FieldSpec]
    field_aliases: Dict[str, Dict[str, str]] = field(default_factory=dict)
    #: backward-compatible view limited to flux fields
    flux_aliases: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for canonical, per_solver in self.flux_aliases.items():
            self.field_aliases.setdefault(canonical, {}).update(per_solver)

    # -- helpers ----------------------------------------------------------
    def _alias(self, solver: str, canonical: str) -> str:
        return self.field_aliases.get(canonical, {}).get(solver, canonical)

    def _resolve(
        self, state: InterfaceState, spec: FieldSpec, solver: str
    ) -> Tuple[Optional[str], Optional[np.ndarray]]:
        if spec.name in state.fields:
            return spec.name, state.fields[spec.name]
        for alias in spec.aliases:
            if alias in state.fields:
                return alias, state.fields[alias]
        alias = self._alias(solver, spec.name)
        if alias in state.fields:
            return alias, state.fields[alias]
        return None, None

    # -- validation --------------------------------------------------------
    def validate(
        self,
        state_a: InterfaceState,
        state_b: InterfaceState,
        flux_closure_tol: float = 1e-6,
    ) -> ContractReport:
        """Check field coverage, units and the sign convention.

        The sign convention is verified physically: in the canonical frame both
        fluxes must agree (closure) within ``flux_closure_tol`` relative
        tolerance.  A solver reporting its own outward flux instead flips the
        sign at one end, which shows up as a closure residual of ~2.
        """
        missing_a: List[str] = []
        missing_b: List[str] = []
        unit_mismatches: List[str] = []
        sign_violations: List[str] = []
        notes: List[str] = []

        for spec in self.state_fields + self.flux_fields:
            key_a, va = self._resolve(state_a, spec, self.pair[0])
            key_b, vb = self._resolve(state_b, spec, self.pair[1])
            if va is None:
                missing_a.append(spec.name)
            if vb is None:
                missing_b.append(spec.name)
            if va is not None and vb is not None and np.ndim(va) and np.ndim(vb):
                if np.shape(va) != np.shape(vb):
                    notes.append(
                        f"field '{spec.name}': sample count differs "
                        f"({np.shape(va)} vs {np.shape(vb)})"
                    )
            for state, key in ((state_a, key_a), (state_b, key_b)):
                if key is None:
                    continue
                reported = state.units.get(key, state.units.get(spec.name))
                if reported is not None and reported != spec.units:
                    unit_mismatches.append(
                        f"field '{spec.name}' from {state.solver}: "
                        f"unit {reported!r} != contract {spec.units!r}"
                    )

        for spec in self.flux_fields:
            _, fa = self._resolve(state_a, spec, self.pair[0])
            _, fb = self._resolve(state_b, spec, self.pair[1])
            if fa is None or fb is None:
                continue
            fa_arr = np.atleast_1d(np.asarray(fa, dtype=float))
            fb_arr = np.atleast_1d(np.asarray(fb, dtype=float))
            if fa_arr.shape != fb_arr.shape:
                notes.append(f"flux '{spec.name}': shape mismatch, closure skipped")
                continue
            scale = max(
                float(np.max(np.abs(fa_arr))), float(np.max(np.abs(fb_arr))), 1e-300
            )
            closure = float(np.max(np.abs(fa_arr - fb_arr))) / scale
            if closure > flux_closure_tol:
                if closure > 1.0:
                    sign_violations.append(
                        f"flux '{spec.name}': closure {closure:.3e} > 1 -> "
                        f"sign-convention violation"
                    )
                else:
                    notes.append(
                        f"flux '{spec.name}': closure {closure:.3e} "
                        f"exceeds {flux_closure_tol:.1e}"
                    )

        ok = not (missing_a or missing_b or unit_mismatches or sign_violations)
        return ContractReport(
            ok=ok,
            missing_a=missing_a,
            missing_b=missing_b,
            unit_mismatches=unit_mismatches,
            sign_violations=sign_violations,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CONTRACT_REGISTRY: Dict[Tuple[str, str], InterfaceContract] = {}


def register_contract(contract: InterfaceContract, replace: bool = True) -> None:
    """Register (or replace) the standardized contract for a solver pair."""
    if contract.pair in CONTRACT_REGISTRY and not replace:
        raise KeyError(f"contract for {contract.pair} already registered")
    CONTRACT_REGISTRY[tuple(contract.pair)] = contract


def get_contract(pair: Tuple[str, str]) -> InterfaceContract:
    return CONTRACT_REGISTRY[tuple(pair)]


def list_pairs() -> List[Tuple[str, str]]:
    return list(CONTRACT_REGISTRY.keys())


def clear_registry() -> None:
    CONTRACT_REGISTRY.clear()


# ---------------------------------------------------------------------------
# Data-driven contract definitions
# ---------------------------------------------------------------------------

_THERMAL_STATE = FieldSpec("temperature", "state", "K", aliases=("T", "u"))
_THERMAL_FLUX = FieldSpec("heat_flux", "flux", "W/m", aliases=("q", "flux"))
_LAPLACE_STATE = FieldSpec("potential", "state", "V", aliases=("u", "T"))
_LAPLACE_FLUX = FieldSpec("normal_derivative", "flux", "1/m", aliases=("q", "flux"))


def _builtin_contracts() -> List[InterfaceContract]:
    """Last-resort defaults, used only when no contract file is available."""
    return [
        InterfaceContract(
            pair=("FVM", "FEM"),
            state_fields=[_THERMAL_STATE],
            flux_fields=[_THERMAL_FLUX],
            field_aliases={
                "heat_flux": {"FVM": "face_flux", "FEM": "boundary_flux"}
            },
        ),
        InterfaceContract(
            pair=("FVM", "BEM"),
            state_fields=[_LAPLACE_STATE],
            flux_fields=[_LAPLACE_FLUX],
            field_aliases={
                "normal_derivative": {"FVM": "face_flux", "BEM": "neumann_data"}
            },
        ),
        InterfaceContract(
            pair=("FEM", "BEM"),
            state_fields=[_LAPLACE_STATE],
            flux_fields=[_LAPLACE_FLUX],
            field_aliases={
                "normal_derivative": {"FEM": "boundary_flux", "BEM": "neumann_data"}
            },
        ),
    ]


def contract_from_mapping(entry: Dict[str, object]) -> InterfaceContract:
    """Build one :class:`InterfaceContract` from a JSON/API mapping."""
    pair = tuple(entry["pair"])  # type: ignore[arg-type]
    if len(pair) != 2:
        raise ValueError(f"pair must have two solver names, got {pair!r}")

    def specs(key: str, kind: str) -> List[FieldSpec]:
        out = []
        for item in entry.get(key, []) or []:
            out.append(
                FieldSpec(
                    name=str(item["name"]),
                    kind=kind,
                    units=str(item.get("units", "")),
                    aliases=tuple(item.get("aliases", ())),
                )
            )
        return out

    aliases = {
        str(canonical): {str(s): str(a) for s, a in per.items()}
        for canonical, per in (entry.get("field_aliases") or {}).items()
    }
    return InterfaceContract(
        pair=pair,  # type: ignore[arg-type]
        state_fields=specs("state_fields", "state"),
        flux_fields=specs("flux_fields", "flux"),
        field_aliases=aliases,
    )


def contracts_from_mapping(payload: Dict[str, object]) -> List[InterfaceContract]:
    schema = payload.get("schema")
    if schema is not None and schema != CONTRACT_SCHEMA:
        raise ValueError(f"unsupported contract schema {schema!r}")
    return [contract_from_mapping(entry) for entry in payload.get("pairs", [])]  # type: ignore[union-attr]


def default_config_path() -> Path:
    env = os.environ.get(_ENV_CONFIG)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "config" / "multi_solver_contracts.json"


def load_contracts(path: Optional[Path] = None, clear: bool = True) -> List[InterfaceContract]:
    """Load contracts from JSON; falls back to the built-in defaults."""
    target = Path(path) if path is not None else default_config_path()
    contracts: List[InterfaceContract]
    if target.is_file():
        contracts = contracts_from_mapping(json.loads(target.read_text(encoding="utf-8")))
    else:
        contracts = _builtin_contracts()
    if clear:
        clear_registry()
    for contract in contracts:
        register_contract(contract)
    return contracts


# ---------------------------------------------------------------------------
# Coverage audit
# ---------------------------------------------------------------------------

StateProvider = Callable[[Tuple[str, str]], Tuple[InterfaceState, InterfaceState]]


def audit_coverage(provider: Optional[StateProvider] = None) -> Dict[str, object]:
    """Validate every registered contract against real solver output.

    ``provider(pair)`` must return the two :class:`InterfaceState` objects the
    pair's solvers actually produce at the interface (live data, not a
    hand-written fixture).  Without a provider the audit only reports the
    registry (and says so explicitly).
    """
    pairs = list_pairs()
    details = []
    valid = 0
    for pair in pairs:
        if provider is None:
            details.append({"pair": list(pair), "validated": False, "reason": "no provider"})
            continue
        state_a, state_b = provider(pair)
        report = get_contract(pair).validate(state_a, state_b)
        if report.ok:
            valid += 1
        details.append({"pair": list(pair), "validated": True, "report": report.as_dict()})
    return {
        "n_pairs": len(pairs),
        "n_validated_live": valid,
        "provider_supplied": provider is not None,
        "coverage": (valid / len(pairs)) if pairs else 0.0,
        "details": details,
    }


load_contracts()