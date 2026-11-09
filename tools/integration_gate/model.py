"""数据模型与等级代数 (纯 stdlib，无领域语义)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# 证据等级：与 artifacts/capability_ledger/capability_ledger.json 的 level_rank 对齐
LEVEL_RANK: dict[str, int] = {
    "NOT_SUPPORTED": 0,
    "STUB": 1,
    "MOCK": 2,
    "WITHHELD": 3,
    "REAL": 4,
}
LEVELS = tuple(LEVEL_RANK)

# 判据结论 -> 该结论允许的最高证据等级 (证据强于判据 = 越证据自述，禁止)
VERDICT_LEVEL_CAP: dict[str, str] = {
    "PASS": "REAL",
    "FAIL": "MOCK",       # honest FAIL：能力存在但未达标，不得回填 REAL
    "WITHHELD": "WITHHELD",  # 无证据
    "SKIP": "WITHHELD",
}

# 互裁结论
VERDICTS = ("AGREE", "DISAGREE", "DEGENERATE", "WITHHELD")
_SEVERITY = {"AGREE": 0, "AGREE_CAVEAT": 1, "WITHHELD": 2, "DEGENERATE": 3, "DISAGREE": 4}


def rank(level: str) -> int:
    if level not in LEVEL_RANK:
        raise KeyError(f"unknown evidence level: {level!r}")
    return LEVEL_RANK[level]


def weaker(a: str, b: str) -> str:
    return a if rank(a) <= rank(b) else b


def cap(level: str, ceiling: str) -> str:
    return weaker(level, ceiling)


def worst_verdict(verdicts: Iterable[str]) -> str:
    out = "AGREE"
    for v in verdicts:
        if _SEVERITY.get(v, 9) > _SEVERITY.get(out, 0):
            out = v
    return out


@dataclass
class Criterion:
    """单条验收判据：被测值 vs 参考值/阈值，结论可由数字重算。"""

    id: str
    verdict: str
    measured: float | None = None
    reference: float | None = None
    op: str | None = None
    threshold: float | None = None
    rel_error: float | None = None
    note: str = ""
    source: str = ""
    # "capability_gate" = 验证实现本身（FAIL 则能力不得高于 MOCK）
    # "case_verdict"    = 对某个算例/工况的工程判决（FAIL 是结论不是缺陷，不压等级）
    role: str = "capability_gate"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "verdict": self.verdict,
            "measured": self.measured,
            "reference": self.reference,
            "op": self.op,
            "threshold": self.threshold,
            "rel_error": self.rel_error,
            "note": self.note,
            "source": self.source,
            "role": self.role,
        }


@dataclass
class Attestation:
    """节点自证：验收判据 + 能力声明 + 基线事实 + 出处 sha256。"""

    node_ext_id: str
    repo: str
    mission_id: int | None
    branch: str | None
    base_commit: str | None
    head_commit: str | None
    source_kind: str
    source_ref: str
    source_sha256: str
    criteria: list[Criterion] = field(default_factory=list)
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    # ── 判据统计 ──────────────────────────────────────────────
    def counts(self) -> dict[str, int]:
        out = {"PASS": 0, "FAIL": 0, "WITHHELD": 0, "SKIP": 0}
        for c in self.criteria:
            out[c.verdict] = out.get(c.verdict, 0) + 1
        out["total"] = len(self.criteria)
        return out

    def gate_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.role == "capability_gate"]

    def case_verdict_fails(self) -> list[str]:
        return [c.id for c in self.criteria if c.role != "capability_gate" and c.verdict == "FAIL"]

    def honest_fail(self) -> bool:
        """honest FAIL = 验证实现本身的门判据失败（工程判决失败不算）。"""
        return any(c.verdict == "FAIL" for c in self.gate_criteria())

    def pass_rate(self) -> float | None:
        n = self.counts()
        graded = n["PASS"] + n["FAIL"]
        return (n["PASS"] / graded) if graded else None

    def verdict(self) -> str:
        """节点总体判据结论：有 FAIL→FAIL；无判据→WITHHELD；否则 PASS。"""
        n = self.counts()
        if n["total"] == 0:
            return "WITHHELD"
        if n["FAIL"]:
            return "FAIL"
        if n["PASS"] == 0:
            return "WITHHELD"
        return "PASS"

    def ceiling(self) -> str:
        """判据允许的最高等级 = 各 capability_gate 判据 cap 的最弱值。"""
        gates = self.gate_criteria()
        if not gates:
            return "WITHHELD"
        c = "REAL"
        for cr in gates:
            c = weaker(c, VERDICT_LEVEL_CAP.get(cr.verdict, "WITHHELD"))
        return c

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_ext_id": self.node_ext_id,
            "repo": self.repo,
            "mission_id": self.mission_id,
            "branch": self.branch,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "source": {"kind": self.source_kind, "ref": self.source_ref,
                       "sha256": self.source_sha256},
            "criteria": [c.as_dict() for c in self.criteria],
            "capabilities": list(self.capabilities),
            "counts": self.counts(),
            "pass_rate": self.pass_rate(),
            "verdict": self.verdict(),
            "ceiling": self.ceiling(),
            "honest_fail": self.honest_fail(),
            "case_verdict_fails": self.case_verdict_fails(),
            "notes": self.notes,
        }


@dataclass
class Trace:
    """一条可复算量测轨迹：同一物理量沿某参数网格的取值。"""

    id: str
    kind: str                      # callable | script | expression | unavailable
    param: str
    values: list[float]
    rows: list[dict[str, Any]]     # [{"param": p, "value": v}]
    status: str                    # ok | error
    meta: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def series(self) -> list[tuple[float, float]]:
        return [(float(r["param"]), float(r["value"])) for r in self.rows
                if r.get("value") is not None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "param": self.param,
            "values": list(self.values),
            "rows": list(self.rows),
            "status": self.status,
            "meta": dict(self.meta),
            "provenance": dict(self.provenance),
            "error": self.error,
        }


@dataclass
class CheckResult:
    check: str
    verdict: str
    severity: str                  # blocking | advisory
    detail: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.check, "verdict": self.verdict,
                "severity": self.severity, "detail": self.detail, "note": self.note}


@dataclass
class PairResult:
    id: str
    title: str
    kind: str
    checks: list[CheckResult] = field(default_factory=list)
    counterpart_status: str = ""
    note: str = ""
    cap_level: str | None = None   # 该互裁结论对能力的等级上限

    def blocking_verdicts(self) -> list[str]:
        return [c.verdict for c in self.checks if c.severity == "blocking"]

    def verdict(self) -> str:
        v = self.blocking_verdicts()
        return worst_verdict(v) if v else "AGREE"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "verdict": self.verdict(),
            "counterpart_status": self.counterpart_status,
            "cap_level": self.cap_level,
            "checks": [c.as_dict() for c in self.checks],
            "note": self.note,
        }