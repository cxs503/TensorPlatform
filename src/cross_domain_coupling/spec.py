"""Case-pack specification layer for the cross-domain coupling gate.

Structural only: which fields an interface contract must declare, how unit /
dimension compatibility is decided, and how a pack is validated.  Any unknown
key is a configuration error (``SpecError``) — a broken pack must never be
silently interpreted as a failing criterion.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

REQUIRED_CONTRACT_FIELDS: Tuple[str, ...] = (
    "id",
    "quantity",
    "unit",
    "consumer_unit",
    "owner",
    "producer",
    "consumer",
    "version",
    "evidence_grade",
    "frame",
)
EVIDENCE_GRADES: Tuple[str, ...] = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")
GRADE_RANK: Dict[str, int] = {"REAL": 4, "WITHHELD": 3, "STUB": 2, "MOCK": 1, "NOT_SUPPORTED": 0}

_COUPLING_KEYS = {
    "id", "producer", "consumer", "quantity", "unit", "consumer_unit", "owner",
    "version", "evidence_grade", "frame", "tolerance_pct", "primary",
    "independent", "case_key", "atol", "notes", "title",
}
_E2E_KEYS = {"evaluator", "baseline_path", "tolerance_pct", "case_set_key", "title", "metrics"}
_TOP_KEYS = {
    "name", "version", "evaluator_module", "units", "couplings", "end_to_end",
    "cases", "end_to_end_cases", "description", "provenance",
}


class SpecError(ValueError):
    """Malformed case pack (configuration error, not a criterion failure)."""


@dataclass(frozen=True)
class UnitDef:
    name: str
    dimension: str
    to_canonical: float


@dataclass(frozen=True)
class Coupling:
    id: str
    producer: str
    consumer: str
    quantity: str
    unit: str
    consumer_unit: str
    owner: str
    version: str
    evidence_grade: str
    frame: str
    tolerance_pct: float
    primary: str
    independent: str
    case_key: str
    atol: float = 1e-12
    notes: str = ""
    title: str = ""

    def contract_fields(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in REQUIRED_CONTRACT_FIELDS}


@dataclass(frozen=True)
class EndToEnd:
    evaluator: str
    baseline_path: str
    tolerance_pct: float
    case_set_key: str
    title: str = ""
    metrics: Tuple[str, ...] = ()


@dataclass(frozen=True)
class GateSpec:
    name: str
    version: str
    evaluator_module: str
    units: Dict[str, UnitDef]
    couplings: Tuple[Coupling, ...]
    end_to_end: EndToEnd
    cases: Dict[str, Any]
    end_to_end_cases: Dict[str, Any]
    source_path: str
    sha256: str
    description: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)

    # -- derived -------------------------------------------------------
    def unit(self, name: str) -> UnitDef:
        try:
            return self.units[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise SpecError(f"unit {name!r} not declared in pack") from exc

    def conversion_factor(self, src: str, dst: str) -> float:
        """Factor k such that ``value_in_src * k = value_in_dst``."""
        us, ud = self.unit(src), self.unit(dst)
        if us.dimension != ud.dimension:
            raise SpecError(
                f"dimension mismatch: {src!r} ({us.dimension}) vs {dst!r} ({ud.dimension})"
            )
        return us.to_canonical / ud.to_canonical


def _reject_unknown(mapping: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise SpecError(f"unknown key(s) in {where}: {unknown}")


def _require(mapping: Mapping[str, Any], keys: Sequence[str], where: str) -> None:
    missing = [k for k in keys if k not in mapping]
    if missing:
        raise SpecError(f"missing key(s) in {where}: {missing}")


def load_spec(path: str | Path) -> GateSpec:
    p = Path(path)
    raw_bytes = p.read_bytes()
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecError(f"invalid JSON in {p}: {exc}") from exc
    _reject_unknown(raw, _TOP_KEYS, "pack root")
    _require(
        raw,
        ("name", "version", "evaluator_module", "units", "couplings", "end_to_end", "cases"),
        "pack root",
    )

    units: Dict[str, UnitDef] = {}
    for name, spec in raw["units"].items():
        if not isinstance(spec, dict) or set(spec) - {"dimension", "to_canonical"}:
            raise SpecError(f"bad unit declaration for {name!r}")
        units[name] = UnitDef(name, str(spec["dimension"]), float(spec.get("to_canonical", 1.0)))
    if not units:
        raise SpecError("pack declares no units")

    couplings: List[Coupling] = []
    seen: set[str] = set()
    for entry in raw["couplings"]:
        where = f"coupling {entry.get('id', '?')!r}"
        _reject_unknown(entry, _COUPLING_KEYS, where)
        _require(
            entry,
            ("id", "producer", "consumer", "quantity", "unit", "consumer_unit", "owner",
             "version", "evidence_grade", "frame", "tolerance_pct", "primary",
             "independent", "case_key"),
            where,
        )
        cid = str(entry["id"])
        if cid in seen:
            raise SpecError(f"duplicate coupling id {cid!r}")
        seen.add(cid)
        if entry["evidence_grade"] not in EVIDENCE_GRADES:
            raise SpecError(f"{where}: bad evidence grade {entry['evidence_grade']!r}")
        tol = float(entry["tolerance_pct"])
        if not (tol > 0.0):
            raise SpecError(f"{where}: tolerance_pct must be > 0")
        if str(entry["unit"]) not in units or str(entry["consumer_unit"]) not in units:
            raise SpecError(f"{where}: undeclared unit")
        couplings.append(
            Coupling(
                id=cid,
                producer=str(entry["producer"]),
                consumer=str(entry["consumer"]),
                quantity=str(entry["quantity"]),
                unit=str(entry["unit"]),
                consumer_unit=str(entry["consumer_unit"]),
                owner=str(entry["owner"]),
                version=str(entry["version"]),
                evidence_grade=str(entry["evidence_grade"]),
                frame=str(entry["frame"]),
                tolerance_pct=tol,
                primary=str(entry["primary"]),
                independent=str(entry["independent"]),
                case_key=str(entry["case_key"]),
                atol=float(entry.get("atol", 1e-12)),
                notes=str(entry.get("notes", "")),
                title=str(entry.get("title", "")),
            )
        )
    if not couplings:
        raise SpecError("pack declares no couplings")

    e2e_raw = raw["end_to_end"]
    _reject_unknown(e2e_raw, _E2E_KEYS, "end_to_end")
    _require(e2e_raw, ("evaluator", "baseline_path", "tolerance_pct", "case_set_key"), "end_to_end")
    if not (float(e2e_raw["tolerance_pct"]) > 0.0):
        raise SpecError("end_to_end: tolerance_pct must be > 0")
    e2e = EndToEnd(
        evaluator=str(e2e_raw["evaluator"]),
        baseline_path=str(e2e_raw["baseline_path"]),
        tolerance_pct=float(e2e_raw["tolerance_pct"]),
        case_set_key=str(e2e_raw["case_set_key"]),
        title=str(e2e_raw.get("title", "")),
        metrics=tuple(str(m) for m in e2e_raw.get("metrics", ())),
    )

    cases = raw["cases"]
    for c in couplings:
        if c.case_key not in cases:
            raise SpecError(f"coupling {c.id!r} references unknown case {c.case_key!r}")
    e2e_cases = raw.get("end_to_end_cases", {})
    if e2e.case_set_key not in e2e_cases:
        raise SpecError(f"end_to_end references unknown case set {e2e.case_set_key!r}")

    return GateSpec(
        name=str(raw["name"]),
        version=str(raw["version"]),
        evaluator_module=str(raw["evaluator_module"]),
        units=units,
        couplings=tuple(couplings),
        end_to_end=e2e,
        cases=dict(cases),
        end_to_end_cases=dict(e2e_cases),
        source_path=str(p),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        description=str(raw.get("description", "")),
        provenance=dict(raw.get("provenance", {})),
    )
