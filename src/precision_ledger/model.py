"""Core data model for the precision ledger (KPI-GATE-100).

The ledger turns the owner rule "对标误差 >15% 不可接受、3% 为目标" from prose
into a machine-checkable comparison entry:

    entry = computed  vs  baseline{value, low, high, source, evidence_grade}

All relative errors are *signed* and defined as ``(computed - ref) / ref``
with three references taken from the baseline interval:

    rel_err.low   ref = baseline.low    (区间下端口径)
    rel_err.mid   ref = baseline.value  (中值口径  <-- 本台账采用的判决口径)
    rel_err.high  ref = baseline.high   (区间上端口径)

Gate decisions (:class:`GateBasis`) are declared *before* looking at results;
see ``docs/precision_ledger/REPORT.md`` §3 for the adjudication.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# --- thresholds (owner rule, predeclared) ---------------------------------
TOL_HARD = 0.15   # > 15% unacceptable
TOL_TARGET = 0.03  # 3% target
REL_ERR_TOL = 1e-6  # recompute agreement tolerance used by the checker
#: A relative error that lands exactly on the threshold must PASS (``<=``), so the
#: comparison carries a 1e-12 guard against binary rounding (0.03 -> 0.030000000000000027).
GATE_EPS = 1e-12


def within(rel: Optional[float], tol: float) -> bool:
    """``abs(rel) <= tol`` with a float-rounding guard."""
    return rel is not None and abs(rel) <= tol + GATE_EPS

GRADES = ("REAL", "MOCK", "STUB", "WITHHELD", "NOT_SUPPORTED")

#: Gate basis used for the ``pass_15pct`` / ``pass_3pct`` verdicts.
ADOPTED_GATE_BASIS = "mid"

GATE_BASIS_OPTIONS = {
    "endpoints": "区间端点口径：max(abs(rel_err_low), abs(rel_err_high)) <= tol，"
                 "即计算值须同时落在两端点的 tol 邻域内",
    "mid": "中值口径：abs(rel_err_mid) <= tol（rel_err_mid 以带中值为参照；本台账采用）",
    "containment": "带内包含口径：baseline.low <= computed <= baseline.high，容差为 0",
}


def _rel(computed: Optional[float], ref: Optional[float]) -> Optional[float]:
    """Signed relative error, ``None`` when undefined (missing or ref == 0)."""
    if computed is None or ref is None:
        return None
    ref = float(ref)
    if ref == 0.0:
        return None
    return (float(computed) - ref) / ref


@dataclass
class Baseline:
    """Reference value (experimental / analytical / regression) for one quantity."""

    value: Optional[float]          # judge reference (midpoint when a band exists)
    low: Optional[float]
    high: Optional[float]
    source: str
    evidence_grade: str             # REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED
    kind: str = "experiment"        # experiment | analytical | regression | derived | none
    citation: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        # a point baseline has a degenerate interval
        if self.low is None and self.value is not None:
            self.low = self.value
        if self.high is None and self.value is not None:
            self.high = self.value
        if self.value is None and self.low is not None and self.high is not None:
            self.value = 0.5 * (float(self.low) + float(self.high))
        if self.evidence_grade not in GRADES:
            raise ValueError(f"bad evidence_grade {self.evidence_grade!r}")

    @property
    def has_band(self) -> bool:
        return (
            self.low is not None
            and self.high is not None
            and self.value is not None
            and not math.isclose(float(self.low), float(self.high))
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Entry:
    """One comparable (computed vs baseline) measurement."""

    entry_id: str
    case_id: str
    quantity: str
    computed: Optional[float]
    baseline: Baseline
    evidence_grade: str
    source_file: str
    adapter: str
    family: str = ""
    unit: str = "-"
    method: str = ""
    note: str = ""
    withheld_reason: str = ""
    attribution: str = "UNATTRIBUTED"
    ship_type: str = ""
    fr: Optional[float] = None
    ref_as_recorded: Optional[float] = None   # baseline value literally present in the source file
    self_referential: bool = False
    mirrors: List[str] = field(default_factory=list)  # byte-identical copies collapsed into this entry

    # --- derived ------------------------------------------------------
    @property
    def judged(self) -> bool:
        """True when the entry can carry an accuracy verdict at all."""
        return (
            self.evidence_grade in ("REAL", "MOCK")
            and self.computed is not None
            and self.baseline.value is not None
        )

    def rel_err(self) -> Dict[str, Optional[float]]:
        c = self.computed
        return {
            "low": _rel(c, self.baseline.low),
            "mid": _rel(c, self.baseline.value),
            "high": _rel(c, self.baseline.high),
        }

    def band_contained(self) -> Optional[bool]:
        """Band containment; ``None`` when the baseline has no interval (point value)."""
        if self.computed is None or self.baseline.low is None or self.baseline.high is None:
            return None
        if math.isclose(float(self.baseline.low), float(self.baseline.high)):
            return None
        return float(self.baseline.low) <= float(self.computed) <= float(self.baseline.high)

    def gate(self, tol: float, basis: str = ADOPTED_GATE_BASIS) -> bool:
        """Gate decision under a declared basis.  ``False`` when not judgeable."""
        if not self.judged:
            return False
        if basis == "containment":
            return bool(self.band_contained())
        rel = self.rel_err()
        if basis == "mid":
            return within(rel["mid"], tol)
        if basis == "endpoints":
            lo, hi = rel["low"], rel["high"]
            if lo is None or hi is None:
                return False
            return within(max(abs(lo), abs(hi)), tol)
        raise ValueError(f"unknown gate basis {basis!r}")

    # --- serialisation ------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        rel = self.rel_err()
        return {
            "entry_id": self.entry_id,
            "case_id": self.case_id,
            "ship_type": self.ship_type,
            "quantity": self.quantity,
            "unit": self.unit,
            "computed": self.computed,
            "baseline": self.baseline.as_dict(),
            "rel_err": rel,
            "abs_rel_err_mid": None if rel["mid"] is None else abs(rel["mid"]),
            "band_contained": self.band_contained(),
            "gate_basis": ADOPTED_GATE_BASIS,
            "tolerance_hard": TOL_HARD,
            "tolerance_target": TOL_TARGET,
            "pass_15pct": self.gate(TOL_HARD),
            "pass_3pct": self.gate(TOL_TARGET),
            # auxiliary (non-binding) verdicts, kept for the adjudication section
            "aux_pass_15pct_endpoints": self.gate(TOL_HARD, "endpoints"),
            "aux_pass_15pct_containment": self.gate(TOL_HARD, "containment"),
            "evidence_grade": self.evidence_grade,
            "judged": self.judged,
            "self_referential": self.self_referential,
            "withheld_reason": self.withheld_reason,
            "attribution": self.attribution,
            "source_file": self.source_file,
            "adapter": self.adapter,
            "family": self.family,
            "fr": self.fr,
            "method": self.method,
            "note": self.note,
            "ref_as_recorded": self.ref_as_recorded,
            "mirrors": list(self.mirrors),
        }


def make_withheld_entry(entry: Entry, reason: str) -> Entry:
    """Force an entry into WITHHELD state (no verdict, no PASS)."""
    entry.evidence_grade = "WITHHELD"
    entry.withheld_reason = reason
    entry.baseline.evidence_grade = "WITHHELD"
    return entry