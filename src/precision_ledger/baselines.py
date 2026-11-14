"""Canonical baseline registry + declared experimental bands.

Two layers:

* **canonical registry** — ``backend/data/benchmark_reference.json`` (v3.0), the
  adjudicated reference dataset.  Rows carry ``rt_status`` / ``rt_basis``: only
  ``DERIVED`` rows may be used for an accuracy verdict, ``WITHHELD`` rows must be
  reported as such (that policy already exists in the repo, see
  ``integrity_gate.policy`` in that file; here it becomes load-bearing).
* **declared bands** — quantities for which the literature gives an interval
  rather than a single number (SUBOFF bare-hull Ct: 0.0035–0.0040, Roddy 1990).
  A band is resolved as ``low/high`` with ``value`` = midpoint.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

from .model import Baseline

CANON_PATH = "backend/data/benchmark_reference.json"
CANON_V2_PATH = "backend/data/benchmark_reference_sources_v2.json"

#: ship_type prefix -> canonical ship label
SHIP_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("SUBOFF", "SUBOFF"),
    ("DTMB5415", "DTMB-5415"),
    ("DTMB-5415", "DTMB-5415"),
    ("KVLCC2", "KVLCC2"),
)

#: Literature bands, declared *before* scanning (see REPORT.md §3).
DECLARED_BANDS: Dict[Tuple[str, str], Dict[str, object]] = {
    ("SUBOFF", "Ct"): {
        "low": 0.0035,
        "high": 0.0040,
        "source": "Roddy 1990 (DTRC-90/012) SUBOFF bare-hull Ct experimental band 0.0035-0.0040 (Fr~0.39)",
        "citation": "Roddy, J.D. (1990), DARPA SUBOFF Model Experiments, DTRC-90/012",
        "kind": "experiment",
        "applies_to": ("SUBOFF-01", "SUBOFF-S0"),
    },
}


def ship_type_of(case_id: str) -> str:
    up = (case_id or "").upper()
    for pref, label in SHIP_PREFIXES:
        if up.startswith(pref.upper()):
            return label
    return ""


class CanonicalRegistry:
    """Read-only view over the adjudicated benchmark reference dataset."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.rows: Dict[str, dict] = {}
        self.meta: dict = {}
        path = self.root / CANON_PATH
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.meta = {
                "path": CANON_PATH,
                "version": data.get("version"),
                "integrity_gate": data.get("integrity_gate"),
            }
            for row in data.get("cases", []):
                cid = row.get("case_id")
                if cid:
                    self.rows[cid] = row
        self.v2_rows: Dict[str, dict] = {}
        p2 = self.root / CANON_V2_PATH
        if p2.exists():
            try:
                data2 = json.loads(p2.read_text(encoding="utf-8"))
                for row in data2.get("cases", []):
                    cid = row.get("case_id")
                    if cid:
                        self.v2_rows[cid] = row
            except (ValueError, OSError):
                pass

    # --- ct -----------------------------------------------------------
    def ct_baseline(self, case_id: str, ship_type: str, recorded: Optional[float]) -> Baseline:
        band = DECLARED_BANDS.get((ship_type, "Ct"))
        if band and case_id in band["applies_to"]:
            return Baseline(
                value=0.5 * (float(band["low"]) + float(band["high"])),
                low=float(band["low"]),
                high=float(band["high"]),
                source=str(band["source"]),
                evidence_grade="REAL",
                kind=str(band["kind"]),
                citation=str(band["citation"]),
                note="declared experimental band; judge reference = band midpoint",
            )
        row = self.rows.get(case_id)
        if row is not None and row.get("experimental_ct") is not None:
            v = float(row["experimental_ct"])
            if recorded is not None and abs(float(recorded) - v) > max(1e-3 * abs(v), 1e-12):
                note = (
                    f"source ref_ct={recorded} differs from canonical v3.0 {v}; "
                    "canonical value adjudicated (R2/R3 closure checks)"
                )
            else:
                note = "canonical v3.0 point value (no declared band)"
            return Baseline(
                value=v,
                low=v,
                high=v,
                source=str(row.get("source_citation") or row.get("source") or CANON_PATH),
                evidence_grade="REAL",
                kind="experiment",
                citation=str(row.get("source") or ""),
                note=note,
            )
        if recorded is not None:
            return Baseline(
                value=float(recorded),
                low=float(recorded),
                high=float(recorded),
                source="ref value as recorded in the source file (not in canonical dataset)",
                evidence_grade="REAL",
                kind="experiment",
                note="baseline provenance not machine-verifiable against the canonical dataset",
            )
        return Baseline(
            value=None, low=None, high=None,
            source="none",
            evidence_grade="WITHHELD",
            kind="none",
            note="no baseline registered for this case/quantity",
        )

    # --- rt -----------------------------------------------------------
    def rt_baseline(self, case_id: str, recorded: Optional[float]) -> Baseline:
        row = self.rows.get(case_id)
        if row is None:
            return Baseline(
                value=None, low=None, high=None, source="none",
                evidence_grade="WITHHELD", kind="none",
                note="case not present in canonical dataset",
            )
        status = (row.get("rt_status") or "").upper()
        if status == "DERIVED" and row.get("experimental_rt_n") is not None:
            v = float(row["experimental_rt_n"])
            note = str(row.get("rt_basis") or "")
            if recorded is not None and abs(float(recorded) - v) > max(1e-2 * abs(v), 1e-12):
                note += (
                    f" | source ref_rt={recorded} differs from canonical v3.0 {v}: "
                    "legacy value superseded by R1 (Rt = Ct*0.5*rho*V^2*S) re-derivation"
                )
            return Baseline(
                value=v, low=v, high=v,
                source=str(row.get("source_citation") or CANON_PATH),
                evidence_grade="REAL",
                kind="derived",
                citation=str(row.get("source") or ""),
                note=note,
            )
        reason = row.get("rt_basis") or "rt_status not DERIVED in canonical dataset"
        return Baseline(
            value=None, low=None, high=None,
            source=str(row.get("source_citation") or CANON_PATH),
            evidence_grade="WITHHELD",
            kind="none",
            note=f"canonical rt_status={status or 'MISSING'} -> WITHHELD: {reason}",
        )


def load_registry(root: Path) -> CanonicalRegistry:
    return CanonicalRegistry(root)