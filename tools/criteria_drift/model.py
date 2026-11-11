"""Data model for the criteria-drift auditor (platform-generic, domain-neutral).

Vocabulary discipline
---------------------
Nothing in this module knows about ships, ice, resistance or any other domain.
"Criteria vocabulary" here means *acceptance-criteria grammar* (comparison
operators, units, criterion kinds such as error bound / convergence order /
p-value) which is shared by every repo in the ecosystem.  Metric names,
thresholds and examples live in data artifacts, never in code.

Wire schema: ``criteria-drift/1.0``
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "criteria-drift/1.0"

# ── criterion kinds (grammar, not domain) ────────────────────────────────
KIND_ERROR = "error_threshold"          # 相对/绝对误差上限
KIND_ORDER = "convergence_order"        # 收敛阶下界
KIND_CONSERVATION = "conservation"      # 守恒量/不变量的偏差上限
KIND_PVALUE = "statistical_pvalue"      # 统计检验 p 值
KIND_COVERAGE = "coverage"              # 覆盖率/占比下界
KIND_COUNT = "count"                    # 计数下界（用例数/项数）
KIND_THRESHOLD = "threshold"            # 其余标量上下界（R²、比值…）
KIND_PRESENCE = "presence"              # 无数字的存在性判据

KINDS: Tuple[str, ...] = (
    KIND_ERROR,
    KIND_ORDER,
    KIND_CONSERVATION,
    KIND_PVALUE,
    KIND_COVERAGE,
    KIND_COUNT,
    KIND_THRESHOLD,
    KIND_PRESENCE,
)

# unit classes after normalisation; ``fraction`` means value is stored as a
# plain ratio (3% -> 0.03) so that percent / permille / bare ratio compare.
UNIT_FRACTION = "fraction"
UNIT_COUNT = "count"
UNIT_ORDER = "order"
UNIT_PVALUE = "pvalue"
UNIT_DIMENSIONLESS = "dimensionless"

UPPER_OPS = ("<", "<=")
LOWER_OPS = (">", ">=")
EQUAL_OPS = ("==",)
RANGE_OP = "range"
PRESENCE_OP = "presence"

# strictness rank: larger = stricter.  ``<`` is stricter than ``<=`` and an
# equality is stricter than any one-sided bound; a two-sided range is handled
# separately (containment / width).
OP_RANK: Dict[str, int] = {
    "<": 3,
    ">": 3,
    "<=": 2,
    ">=": 2,
    "==": 4,
    RANGE_OP: 3,
    PRESENCE_OP: 0,
}

_OP_GLYPHS = {"≤": "<=", "≦": "<=", "⩽": "<=", "≥": ">=", "≧": ">=", "⩾": ">=", "=<": "<=", "=>": ">="}


def canonical_operator(text: str) -> str:
    """Normalise operator spellings to one of the canonical wire values."""
    t = (text or "").strip()
    t = _OP_GLYPHS.get(t, t)
    t = t.replace(" ", "")
    if t in ("<=", "≤", "≦", "⩽", "=<", "not more than", "at most", "no more than", "within"):
        return "<="
    if t in (">=", "≥", "≧", "⩾", "=>", "not less than", "at least", "no less than"):
        return ">="
    if t in ("<", "less than", "below", "under"):
        return "<"
    if t in (">", "greater than", "above", "over", "more than"):
        return ">"
    if t in ("=", "==", "equal", "equals"):
        return "=="
    return t


def operator_direction(op: str) -> str:
    if op in UPPER_OPS:
        return "upper"
    if op in LOWER_OPS:
        return "lower"
    if op in EQUAL_OPS:
        return "equal"
    if op == RANGE_OP:
        return "range"
    return "none"


def sha256_json(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Criterion:
    """One parsed acceptance criterion.

    ``value`` is stored in the kind's *canonical* unit (ratio for percentages,
    integer for counts, dimensionless otherwise); ``display_value`` keeps the
    number as written so audit tables can quote the original without a
    hand-copy.  ``tolerance`` is the optional ± band attached to the threshold.
    """

    raw: str
    kind: str
    op: str
    value: Optional[float]
    unit: str
    subject: str
    tokens: Tuple[str, ...]
    display_value: Optional[float] = None
    display_unit: str = ""
    tolerance: Optional[float] = None          # canonical unit
    tolerance_display: Optional[float] = None
    lo: Optional[float] = None                 # range lower bound (canonical)
    hi: Optional[float] = None                 # range upper bound (canonical)
    locator: str = ""
    alternatives: Tuple[float, ...] = ()       # other numbers seen (context only)
    extras: Tuple[str, ...] = ()
    compound: bool = False                     # two opposed bounds joined by 且/and

    # ── derived helpers ───────────────────────────────────────────────
    @property
    def direction(self) -> str:
        return operator_direction(self.op)

    @property
    def token_set(self) -> frozenset:
        return frozenset(self.tokens)

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tokens"] = list(self.tokens)
        d["alternatives"] = list(self.alternatives)
        d["extras"] = list(self.extras)
        return d

    def identity(self) -> str:
        return f"{self.kind}|{self.op}|{self.value}|{self.unit}|{self.subject}"


@dataclass(frozen=True)
class Waiver:
    """An explicit, attributable relaxation declaration inside a report."""

    target: str
    reason: str
    approved_by: str
    locator: str = ""

    @property
    def declared(self) -> bool:
        return bool(self.reason.strip()) and bool(self.approved_by.strip())


@dataclass
class CriterionDrift:
    """Comparison outcome for one proposal criterion (or one report-only one)."""

    proposal: Optional[Criterion]
    report: Optional[Criterion]
    match_score: float = 0.0
    status: str = "ok"               # ok | significant | declared_waiver | added
    reasons: List[str] = field(default_factory=list)
    relaxation_ratio: Optional[float] = None   # >0 => threshold loosened
    operator_downgrade: bool = False
    direction_change: bool = False
    kind_change: bool = False
    tolerance_widening: Optional[float] = None
    waiver: Optional[Waiver] = None
    impact: str = ""
    action: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "proposal": self.proposal.as_dict() if self.proposal else None,
            "report": self.report.as_dict() if self.report else None,
            "match_score": round(self.match_score, 4),
            "status": self.status,
            "reasons": list(self.reasons),
            "relaxation_ratio": None if self.relaxation_ratio is None else round(self.relaxation_ratio, 6),
            "operator_downgrade": self.operator_downgrade,
            "direction_change": self.direction_change,
            "kind_change": self.kind_change,
            "tolerance_widening": None if self.tolerance_widening is None else round(self.tolerance_widening, 6),
            "waiver": asdict(self.waiver) if self.waiver else None,
            "impact": self.impact,
            "action": self.action,
        }


@dataclass
class DriftAudit:
    """Audit of one proposal document against one acceptance report."""

    proposal_ref: str
    report_ref: str
    rows: List[CriterionDrift] = field(default_factory=list)
    schema: str = SCHEMA_VERSION
    policy: Dict[str, Any] = field(default_factory=dict)

    # ── aggregates ────────────────────────────────────────────────────
    @property
    def significant(self) -> List[CriterionDrift]:
        return [r for r in self.rows if r.status == "significant"]

    @property
    def waived(self) -> List[CriterionDrift]:
        return [r for r in self.rows if r.status == "declared_waiver"]

    @property
    def dropped(self) -> List[CriterionDrift]:
        return [r for r in self.rows if r.proposal is not None and r.report is None]

    @property
    def added(self) -> List[CriterionDrift]:
        return [r for r in self.rows if r.proposal is None and r.report is not None]

    @property
    def max_relaxation(self) -> float:
        vals = [r.relaxation_ratio for r in self.rows if r.relaxation_ratio is not None]
        return max(vals) if vals else 0.0

    @property
    def drift_score(self) -> float:
        """0..1 severity: relax ratio (capped 1), +0.15 op downgrade, +0.35 drop,
        +0.25 kind/direction swap; capped at 1.  Pure function of the rows."""
        score = 0.0
        for r in self.significant:
            s = min(1.0, max(0.0, r.relaxation_ratio or 0.0))
            if r.operator_downgrade:
                s += 0.15
            if r.kind_change or r.direction_change:
                s += 0.25
            if r.report is None:
                s += 0.35
            score = max(score, s)
        return round(min(1.0, score), 4)

    @property
    def block(self) -> bool:
        return bool(self.significant)

    def summary(self) -> Dict[str, Any]:
        return {
            "n_proposal": sum(1 for r in self.rows if r.proposal is not None),
            "n_report": sum(1 for r in self.rows if r.report is not None),
            "n_matched": sum(1 for r in self.rows if r.proposal is not None and r.report is not None),
            "n_significant": len(self.significant),
            "n_declared_waiver": len(self.waived),
            "n_dropped": len(self.dropped),
            "n_added": len(self.added),
            "max_relaxation": round(self.max_relaxation, 6),
            "drift_score": self.drift_score,
            "block": self.block,
        }

    def _fingerprint(self) -> Dict[str, Any]:
        """Hashable core (no block_reason -> no recursion).

        Source locators are deliberately excluded: the audit *verdict* must not
        depend on the position of a criterion inside the report (re-ordering the
        report is not drift), while the locators stay in ``as_dict`` for
        traceability.
        """
        rows = []
        for r in self.rows:
            d = r.as_dict()
            for side in ("proposal", "report"):
                if d[side]:
                    d[side]["locator"] = ""
            rows.append(d)
        return {
            "schema": self.schema,
            "proposal_ref": self.proposal_ref,
            "report_ref": self.report_ref,
            "policy": self.policy,
            "rows": rows,
            "summary": self.summary(),
        }

    def audit_sha256(self) -> str:
        return sha256_json(self._fingerprint())

    def as_dict(self) -> Dict[str, Any]:
        payload = self._fingerprint()
        payload["recommended_actions"] = self.recommended_actions()
        payload["block_reason"] = self.block_reason()
        payload["audit_sha256"] = self.audit_sha256()
        return payload

    def block_reason(self) -> str:
        if not self.block:
            return ""
        sig = self.significant
        parts = [
            f"criteria_drift:significant={len(sig)}",
            f"max_relax={self.max_relaxation:.4f}",
            f"ops={sum(1 for r in sig if r.operator_downgrade)}",
            f"dropped={len(self.dropped)}",
            f"score={self.drift_score:.3f}",
            f"audit={self.audit_sha256()[:12]}",
        ]
        return ";".join(parts)

    def recommended_actions(self) -> List[str]:
        actions: List[str] = []
        for r in self.significant:
            if r.action:
                actions.append(r.action)
        # de-duplicate, keep order
        seen, out = set(), []
        for a in actions:
            if a not in seen:
                seen.add(a)
                out.append(a)
        return out[:8]


def tokens_from_text(text: str, stop: Sequence[str] = ()) -> Tuple[str, ...]:
    """Deterministic, domain-free tokeniser: latin words + CJK character bigrams."""
    import re

    s = (text or "").lower()
    toks: List[str] = []
    for m in re.finditer(r"[a-z][a-z0-9_]{1,}", s):
        w = m.group(0).strip("_")
        if len(w) >= 2:
            toks.append(w)
    cjk = re.findall(r"[\u4e00-\u9fff]+", s)
    for run in cjk:
        if len(run) == 1:
            toks.append(run)
        else:
            for i in range(len(run) - 1):
                toks.append(run[i:i + 2])
    stops = set(stop or ())
    return tuple(sorted({t for t in toks if t not in stops}))