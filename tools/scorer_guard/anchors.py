"""Anchor registry with a hash-chained provenance ledger (IN25-T5).

A tournament scorer is only as trustworthy as the numbers it scores against.
Every scoring anchor is a first-class record carrying

* quantity + covariates + acceptance envelope,
* source block (citation, formula id, derivation file, retrieval date),
* uncertainty and a per-anchor score tolerance ``score_e_max``,
* provenance status ``VERIFIED`` (re-derivable inside this repo) or
  ``QUARANTINED`` (no re-derivable source -> the scorer MUST refuse it).

Records are chained ``h_i = sha256(h_{i-1} || canonical_json(anchor_i))`` and
only the head is published, so editing an anchor value or re-ordering anchors
breaks the head: anchors cannot be silently re-tuned after pre-registration.

Anchor values are *derived*, never hand typed: :func:`build_default_registry`
evaluates the closed forms in :mod:`tools.scorer_guard.physics`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = ROOT / "data" / "scorer_guard" / "anchors_v1.json"

REGISTRY_ID = "ship_perf_tournament_anchors"
REGISTRY_VERSION = "1.0.0"

SOURCE_TYPES = ("ANALYTIC_FORMULA", "STANDARD_TEXTBOOK", "EXPERIMENT")
STATUSES = ("VERIFIED", "QUARANTINED")


def canonical_json(obj: Any) -> str:
    """Deterministic JSON encoding (sorted keys, no whitespace) used for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Anchor:
    """One scoring anchor: a target number plus everything needed to audit it."""

    anchor_id: str
    quantity: str
    covariates: Dict[str, float]
    value: Optional[float]
    unit: str
    source_type: str
    source: Dict[str, Any]
    uncertainty_rel: float
    score_e_max: float
    envelope: Dict[str, List[float]] = field(default_factory=dict)
    provenance_status: str = "VERIFIED"
    weight: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, rec: Mapping[str, Any]) -> "Anchor":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in rec.items() if k in known})


def build_default_registry() -> Dict[str, Any]:
    """Materialise the v1 anchor set; every value is computed from a closed form."""
    from . import physics

    anchors: List[Anchor] = []

    # ITTC-1957 friction line Cf = 0.075 / (log10 Re - 2)^2  (exact closed form)
    for re_power in (6, 7, 9):
        re = float(10 ** re_power)
        anchors.append(
            Anchor(
                anchor_id=f"A-CF-ITTC57-RE1E{re_power}",
                quantity="CF_plate_mean",
                covariates={"Re": re},
                value=physics.ittc1957_cf(re),
                unit="-",
                source_type="ANALYTIC_FORMULA",
                source={
                    "citation": "ITTC 1957 line: Cf = 0.075 / (log10(Re) - 2)^2",
                    "formula_id": "ittc1957_cf",
                    "derivation_file": "tools/scorer_guard/physics.py",
                    "independent_recheck": "exact rational (Fraction/Decimal)",
                    "retrieved": "2026-11-06",
                },
                uncertainty_rel=0.02,
                score_e_max=0.05,
                envelope={"Re": [1.0e5, 1.0e10]},
            )
        )

    # Blasius laminar flat plate, mean skin friction both sides CF = 1.328/sqrt(Re)
    re = 1.0e5
    anchors.append(
        Anchor(
            anchor_id="A-CF-BLAS-RE1E5",
            quantity="CF_plate_mean",
            covariates={"Re": re},
            value=physics.blasius_plate_cf(re),
            unit="-",
            source_type="ANALYTIC_FORMULA",
            source={
                "citation": "Blasius (1908) similarity solution: CF = 1.328 / sqrt(Re_L)",
                "formula_id": "blasius_plate_cf",
                "derivation_file": "tools/scorer_guard/physics.py",
                "independent_recheck": "classic 0.664/sqrt(Re_x) local, integrated analytically",
                "retrieved": "2026-11-06",
            },
            uncertainty_rel=0.01,
            score_e_max=0.08,
            envelope={"Re": [1.0e4, 5.0e5]},
        )
    )

    # Karman-Schoenherr implicit line: root of 1/sqrt(Cf) = 4.13 log10(Re Cf)
    re = 1.0e9
    anchors.append(
        Anchor(
            anchor_id="A-CF-KS-RE1E9",
            quantity="CF_plate_mean",
            covariates={"Re": re},
            value=physics.schoenherr_implicit_cf(re),
            unit="-",
            source_type="ANALYTIC_FORMULA",
            source={
                "citation": "Karman-Schoenherr (1932): 1/sqrt(Cf) = 4.13 log10(Re Cf)",
                "formula_id": "schoenherr_implicit_cf",
                "derivation_file": "tools/scorer_guard/physics.py",
                "independent_recheck": "explicit 0.455/(log10 Re)^2.58",
                "retrieved": "2026-11-06",
            },
            uncertainty_rel=0.03,
            score_e_max=0.05,
            envelope={"Re": [1.0e6, 1.0e10]},
        )
    )

    # Deliberately quarantined EXPERIMENT anchor: primary source table is not in
    # this worktree, so no value is invented.  Exists to exercise the fail-closed
    # path -- the scorer must refuse to score against it.
    anchors.append(
        Anchor(
            anchor_id="A-CT-EXPERIMENT-PENDING",
            quantity="CT_total",
            covariates={"Fr": 0.25},
            value=None,
            unit="-",
            source_type="EXPERIMENT",
            source={
                "citation": "",
                "doc_sha256": "",
                "note": "no experimental value ingested until the source table is hash-recorded",
                "retrieved": "2026-11-06",
            },
            uncertainty_rel=0.05,
            score_e_max=0.05,
            envelope={"Fr": [0.10, 0.45]},
            provenance_status="QUARANTINED",
        )
    )

    registry: Dict[str, Any] = {
        "registry_id": REGISTRY_ID,
        "version": REGISTRY_VERSION,
        "anchors": [a.to_dict() for a in anchors],
    }
    registry["ledger"] = {
        "chain": chain_of(registry["anchors"]),
        "head": chain_head(registry["anchors"]),
    }
    return registry


def anchor_hash(prev: str, rec: Mapping[str, Any]) -> str:
    return sha256_text(prev + canonical_json(dict(rec)))


def chain_of(records: Sequence[Mapping[str, Any]]) -> List[str]:
    prev, out = "GENESIS", []
    for rec in records:
        prev = anchor_hash(prev, rec)
        out.append(prev)
    return out


def chain_head(records: Sequence[Mapping[str, Any]]) -> str:
    """Order-sensitive hash-chain head over the anchor records."""
    chain = chain_of(records)
    return chain[-1] if chain else sha256_text("EMPTY")


def anchors_from(registry: Mapping[str, Any]) -> List[Anchor]:
    return [Anchor.from_dict(r) for r in registry["anchors"]]


def verify_ledger(registry: Mapping[str, Any]) -> Dict[str, Any]:
    """Recompute the chain and compare it with the published head and chain."""
    records = registry["anchors"]
    chain = chain_of(records)
    head = chain[-1] if chain else sha256_text("EMPTY")
    declared = registry.get("ledger") or {}
    ok = declared.get("head") == head and list(declared.get("chain") or []) == chain
    return {
        "ok": ok,
        "head": head,
        "declared_head": declared.get("head"),
        "reason": "" if ok else "LEDGER_HEAD_MISMATCH",
        "n_anchors": len(records),
    }


def validate_anchor(rec: Mapping[str, Any]) -> List[str]:
    """Return provenance/format problems (empty list == admissible anchor)."""
    problems: List[str] = []
    status = rec.get("provenance_status", "")
    src = rec.get("source") or {}
    if status not in STATUSES:
        problems.append(f"BAD_STATUS:{status}")
    if rec.get("source_type") not in SOURCE_TYPES:
        problems.append(f"BAD_SOURCE_TYPE:{rec.get('source_type')}")
    e_max = rec.get("score_e_max")
    if not isinstance(e_max, (int, float)) or not 0.0 < float(e_max) <= 1.0:
        problems.append("BAD_SCORE_E_MAX")
    unc = rec.get("uncertainty_rel")
    if not isinstance(unc, (int, float)) or float(unc) < 0.0:
        problems.append("BAD_UNCERTAINTY")
    if status == "VERIFIED":
        if rec.get("value") is None:
            problems.append("VERIFIED_WITHOUT_VALUE")
        if not str(src.get("citation") or "").strip():
            problems.append("MISSING_CITATION")
        if not (src.get("formula_id") or src.get("doc_sha256")):
            problems.append("MISSING_FORMULA_OR_DOC_HASH")
    else:  # QUARANTINED: must not carry a number
        if rec.get("value") is not None:
            problems.append("QUARANTINED_WITH_VALUE")
    for key in rec.get("envelope") or {}:
        if key not in (rec.get("covariates") or {}):
            problems.append(f"ENVELOPE_KEY_NOT_IN_COVARIATES:{key}")
    return problems


def load_registry(path: Optional[Path] = None) -> Dict[str, Any]:
    with open(Path(path) if path is not None else DEFAULT_REGISTRY_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_registry(registry: Mapping[str, Any], path: Optional[Path] = None) -> Path:
    p = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def provenance_report(registry: Mapping[str, Any]) -> Dict[str, Any]:
    """Audit trail: per-anchor hash plus independent re-derivation agreement."""
    from . import physics

    ledger = verify_ledger(registry)
    rows: List[Dict[str, Any]] = []
    for rec, h in zip(registry["anchors"], chain_of(registry["anchors"])):
        problems = validate_anchor(rec)
        row: Dict[str, Any] = {
            "anchor_id": rec["anchor_id"],
            "record_sha256": h,
            "provenance_status": rec["provenance_status"],
            "source_type": rec["source_type"],
            "citation": (rec.get("source") or {}).get("citation", ""),
            "value": rec.get("value"),
            "problems": problems,
        }
        if rec["provenance_status"] == "VERIFIED" and not problems:
            recheck = physics.recompute_anchor(rec)
            row.update(
                {
                    "recomputed": recheck["value"],
                    "abs_dev": recheck["abs_dev"],
                    "rel_dev": recheck["rel_dev"],
                    "recompute_method": recheck["method"],
                }
            )
        rows.append(row)
    return {
        "registry_id": registry.get("registry_id"),
        "version": registry.get("version"),
        "ledger": ledger,
        "n_verified": sum(1 for r in rows if r["provenance_status"] == "VERIFIED"),
        "n_quarantined": sum(1 for r in rows if r["provenance_status"] == "QUARANTINED"),
        "max_rel_dev_verified": max(
            [r.get("rel_dev", 0.0) for r in rows if r["provenance_status"] == "VERIFIED"] or [0.0]
        ),
        "anchors": rows,
    }