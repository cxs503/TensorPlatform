"""Load, validate, integrity-check and serve the reference-value corpus.

Serving contract (enforced here, surfaced by ``api.py``):

* every entry is validated against ``refs.schema.json`` at load time
  (``SchemaViolation`` otherwise);
* the sha256 anchor of the archived source artefact is recomputed on **every**
  access; a single flipped bit makes the entry unservable
  (``IntegrityError``), i.e. the corpus fails closed;
* queries outside ``validity_range`` raise ``OutOfRangeError`` instead of
  silently extrapolating;
* values are served by monotone PCHIP interpolation of the digitised table.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import jsonschema

from . import digitise, interpolate
from .errors import (
    EntryNotFound,
    IntegrityError,
    OutOfRangeError,
    SchemaViolation,
)

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "reference_corpus"


def default_root() -> Path:
    """Corpus root (overridable with ``REFERENCE_CORPUS_ROOT`` for tests)."""
    env = os.environ.get("REFERENCE_CORPUS_ROOT")
    return Path(env) if env else DEFAULT_ROOT


@dataclass
class ReferenceEntry:
    """One anchored reference value / table."""

    path: Path
    raw: dict
    xs: List[float] = field(default_factory=list)
    ys: List[float] = field(default_factory=list)
    xs_b: List[float] = field(default_factory=list)
    ys_b: List[float] = field(default_factory=list)

    # -- identity ---------------------------------------------------------
    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def quantity(self) -> str:
        return self.raw["quantity"]

    @property
    def unit(self) -> str:
        return self.raw["unit"]

    @property
    def provenance(self) -> str:
        return self.raw["provenance"]

    @property
    def tolerance(self) -> dict:
        return self.raw["tolerance"]

    @property
    def validity(self) -> dict:
        return self.raw["validity_range"]

    @property
    def source(self) -> dict:
        return self.raw["source"]

    @property
    def domain(self) -> str:
        return self.raw.get("domain", "misc")

    def rel_tolerance(self) -> float:
        tol = self.tolerance
        if tol["type"] == "relative":
            return float(tol["value"])
        ymax = max(abs(v) for v in self.ys) if self.ys else 1.0
        return float(tol["value"]) / ymax if ymax else float(tol["value"])

    # -- serving ----------------------------------------------------------
    def evaluate(self, x: float, scheme: str = "pchip") -> float:
        v = self.validity
        x_min, x_max = float(v["x_min"]), float(v["x_max"])
        # tolerance-free comparison: the validity range is the digitised range
        if x < x_min or x > x_max:
            raise OutOfRangeError(self.id, v["x_quantity"], x, x_min, x_max, v.get("note"))
        if scheme == "linear":
            return float(interpolate.linear(self.xs, self.ys, [x])[0])
        return float(interpolate.pchip(self.xs, self.ys, [x])[0])

    def consistency(self, samples_per_interval: int = 7) -> dict:
        return interpolate.interpolation_consistency(self.xs, self.ys, samples_per_interval)

    def dual_pass(self, gate: float = digitise.DEFAULT_GATE) -> dict:
        rep = digitise.pointwise_rel_diff(
            self.xs,
            self.ys,
            self.xs_b,
            self.ys_b,
            gate_scale=float(self.raw.get("dual_pass", {}).get("gate_scale", 0.0)) or None,
        )
        rep["gate"] = gate
        rep["pass"] = bool(rep["max_rel_diff"] <= gate)
        return rep

    def summary(self) -> dict:
        """Compact, JSON-serialisable description (API surface)."""
        return {
            "id": self.id,
            "quantity": self.quantity,
            "unit": self.unit,
            "value": self.raw["value"],
            "tolerance": self.tolerance,
            "k": self.raw["k"],
            "source": self.source,
            "sha256_of_scan": self.raw["sha256_of_scan"],
            "digitisation_error": self.raw["digitisation_error"],
            "validity_range": self.validity,
            "provenance": self.provenance,
            "supersedes": self.raw.get("supersedes"),
            "domain": self.domain,
            "n_points": len(self.xs),
            "notes": self.raw.get("notes", []),
        }


class ReferenceCorpus:
    """A directory of schema-validated, sha256-anchored reference entries."""

    SCHEMA_FILE = "refs.schema.json"
    ENTRIES_DIR = "entries"

    def __init__(self, root: str | Path | None = None, validate: bool = True) -> None:
        self.root = Path(root) if root is not None else default_root()
        self.schema_path = self.root / self.SCHEMA_FILE
        self.schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        self._entries: Dict[str, ReferenceEntry] = {}
        self.schema_errors: List[str] = []
        #: entry id -> (expected, actual) for artefacts whose anchor does not match
        self.integrity_failures: Dict[str, tuple] = {}
        self._load(validate=validate)

    # -- loading ----------------------------------------------------------
    def _load(self, validate: bool = True) -> None:
        entry_dir = self.root / self.ENTRIES_DIR
        if not entry_dir.is_dir():
            raise FileNotFoundError(f"corpus entries directory not found: {entry_dir}")
        validator = jsonschema.Draft202012Validator(self.schema)
        for path in sorted(entry_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if validate:
                errs = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
                if errs:
                    self.schema_errors.append(f"{path.name}: {len(errs)}")
                    raise SchemaViolation(path.name, [e.message for e in errs])
            entry = ReferenceEntry(path=path, raw=raw)
            table = raw["table"]
            # ---- anchor check first: a tampered artefact must never be parsed
            # into served numbers, and must never crash the loader either (the
            # corpus fails *closed*, it does not fail *loudly at import*).
            expected = raw["sha256_of_scan"]
            try:
                actual = digitise.sha256_file(self.root / table["path"])
            except OSError:
                actual = "<unreadable>"
            if actual != expected:
                self.integrity_failures[entry.id] = (expected, actual)
                self._entries[entry.id] = entry
                continue
            sx, sy = digitise.read_table(self.root / table["path"])
            bx, by = digitise.read_table(self.root / table["pass_b_path"])
            entry.xs, entry.ys, entry.xs_b, entry.ys_b = sx, sy, bx, by
            # inline metadata must agree with the archived artefact
            self._check_table_meta(entry, sx, sy)
            self._entries[entry.id] = entry

    @staticmethod
    def _check_table_meta(entry: ReferenceEntry, xs: List[float], ys: List[float]) -> None:
        import math

        t = entry.raw["table"]
        if int(t["n_points"]) != len(xs):
            raise SchemaViolation(
                entry.path.name,
                [f"table.n_points={t['n_points']} but artefact has {len(xs)} rows"],
            )
        if not math.isclose(float(t["x_min"]), xs[0], rel_tol=1e-12, abs_tol=1e-300):
            raise SchemaViolation(entry.path.name, ["table.x_min disagrees with artefact"])
        if not math.isclose(float(t["x_max"]), xs[-1], rel_tol=1e-12, abs_tol=1e-300):
            raise SchemaViolation(entry.path.name, ["table.x_max disagrees with artefact"])

    # -- integrity --------------------------------------------------------
    def verify_integrity(self, entry_id: str) -> str:
        """Recompute the sha256 anchor of the entry's archived artefact."""
        entry = self.get(entry_id)
        artefact = self.root / entry.raw["table"]["path"]
        expected = entry.raw["sha256_of_scan"]
        if entry_id in self.integrity_failures:
            exp, act = self.integrity_failures[entry_id]
            raise IntegrityError(entry_id, str(artefact), exp, act)
        actual = digitise.sha256_file(artefact)
        if actual != expected:
            raise IntegrityError(entry_id, str(artefact), expected, actual)
        return actual

    def verify_all(self) -> Dict[str, bool]:
        out: Dict[str, bool] = {}
        for eid in self._entries:
            try:
                self.verify_integrity(eid)
                out[eid] = True
            except IntegrityError:
                out[eid] = False
        return out

    # -- access -----------------------------------------------------------
    def ids(self) -> List[str]:
        return sorted(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, entry_id: object) -> bool:
        return entry_id in self._entries

    def get(self, entry_id: str) -> ReferenceEntry:
        try:
            return self._entries[entry_id]
        except KeyError:
            raise EntryNotFound(entry_id, self.ids()) from None

    def all(self) -> Iterable[ReferenceEntry]:
        return [self._entries[k] for k in self.ids()]

    def query(
        self,
        quantity: Optional[str] = None,
        validity: Optional[float] = None,
        domain: Optional[str] = None,
        provenance: Optional[str] = None,
    ) -> List[ReferenceEntry]:
        """Filter entries by quantity / domain / provenance / validity point.

        ``validity`` is an abscissa value: only entries whose digitised validity
        range covers it are returned (no extrapolation is ever offered).
        """
        out: List[ReferenceEntry] = []
        for e in self.all():
            if quantity and quantity.lower() not in e.quantity.lower():
                continue
            if domain and e.domain != domain:
                continue
            if provenance and e.provenance != provenance.upper():
                continue
            if validity is not None:
                v = e.validity
                if not (float(v["x_min"]) <= float(validity) <= float(v["x_max"])):
                    continue
            out.append(e)
        return out

    def evaluate(self, entry_id: str, x: float, scheme: str = "pchip") -> dict:
        entry = self.get(entry_id)
        self.verify_integrity(entry_id)
        y = entry.evaluate(x, scheme=scheme)
        return {
            "id": entry_id,
            "quantity": entry.quantity,
            "x": float(x),
            "value": y,
            "unit": entry.unit,
            "scheme": scheme,
            "tolerance": entry.tolerance,
            "k": entry.raw["k"],
            "provenance": entry.provenance,
            "sha256_of_scan": entry.raw["sha256_of_scan"],
            "validity_range": entry.validity,
        }

    # -- audit ------------------------------------------------------------
    def audit(self, dual_pass_gate: float = digitise.DEFAULT_GATE, consistency_gate: float = 1e-3) -> dict:
        """Full corpus audit: schema, anchors, dual pass, interpolation."""
        rows: List[dict] = []
        ok_schema = not self.schema_errors
        for e in self.all():
            anchor_ok = True
            try:
                self.verify_integrity(e.id)
            except IntegrityError:
                anchor_ok = False
            dp = e.dual_pass(gate=dual_pass_gate)
            cons = e.consistency()
            rows.append(
                {
                    "id": e.id,
                    "quantity": e.quantity,
                    "provenance": e.provenance,
                    "n_points": len(e.xs),
                    "has_source": bool(e.source and e.source.get("type") and e.source.get("doc")),
                    "has_sha256": bool(e.raw["sha256_of_scan"]),
                    "has_tolerance": bool(e.tolerance.get("value")),
                    "has_validity": all(k in e.validity for k in ("x_min", "x_max", "x_quantity")),
                    "anchor_ok": anchor_ok,
                    "dual_pass_max_rel_diff": dp["max_rel_diff"],
                    "dual_pass_ok": dp["pass"],
                    "pchip_vs_linear_max_rel_diff": cons["max_rel_diff"],
                    "interp_ok": bool(cons["max_rel_diff"] <= consistency_gate),
                }
            )
        n = len(rows)
        return {
            "root": str(self.root),
            "schema_valid": ok_schema,
            "n_entries": n,
            "schema_completeness": {
                "source": sum(r["has_source"] for r in rows) / n if n else 0.0,
                "sha256": sum(r["has_sha256"] for r in rows) / n if n else 0.0,
                "tolerance": sum(r["has_tolerance"] for r in rows) / n if n else 0.0,
                "validity_range": sum(r["has_validity"] for r in rows) / n if n else 0.0,
            },
            "anchors_ok": all(r["anchor_ok"] for r in rows),
            "dual_pass_ok": all(r["dual_pass_ok"] for r in rows),
            "dual_pass_gate": dual_pass_gate,
            "max_dual_pass_rel_diff": max((r["dual_pass_max_rel_diff"] for r in rows), default=0.0),
            "interp_ok": all(r["interp_ok"] for r in rows),
            "max_pchip_vs_linear_rel_diff": max(
                (r["pchip_vs_linear_max_rel_diff"] for r in rows), default=0.0
            ),
            "rows": rows,
        }