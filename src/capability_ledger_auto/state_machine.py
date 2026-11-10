"""证据等级状态机 v2：五态 + 不可判态（UNDECIDABLE）的 fail-closed 语义。

状态语义（C1）
---------------
================  ============================================================  ===========
状态              定义                                                          可采信秩
================  ============================================================  ===========
NOT_SUPPORTED     无任何代码/测试/报告痕迹且无验证锚 → 能力明确不存在                 0
STUB              仅接口/骨架，无实质实现（stub 标记）                               1
MOCK              有实现但被机器证据否证（决定性 FAIL 锚 / 反例 / 测试未通过）        2
WITHHELD          有实现、无否证、但尚未收集到验证锚 → 证据不足，能力可能存在           3
REAL              机器判据闭合：≥N 独立决定性 PASS 锚 + 多类来源 + 测试通过率达标      4
UNDECIDABLE       有验证锚但机器无法裁决（不确定度带跨判决线 / 缺参考值）→ fail-closed  -1
================  ============================================================  ===========

**UNDECIDABLE ≠ WITHHELD**：WITHHELD 表示「还没试过验证」（无锚），
UNDECIDABLE 表示「试过但证据不闭合」（有锚、不可判）。二者在门禁上不同：
WITHHELD 允许标注后人工复核窗口，UNDECIDABLE 一律 fail-closed（不得采信、
不得回落为 WITHHELD、不得升级）。

升级/降级全部由 :class:`EvidenceStateMachine` 的规则表机器执行（C3/C4），
任何人工指定等级都走 :meth:`EvidenceLevelLedger.manual_override` → 恒抛
:class:`ManualOverrideRejected`（C5），仅留痕不改状态。

零领域语义：本模块只见数值与证据摘要，不含任何域词条/域判据。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Sequence

from .anchors import (
    FAIL,
    PASS,
    UNDECIDABLE as ANCHOR_UNDECIDABLE,
    VerificationAnchor,
    adverse_undecidables,
    decisive_failures,
    distinct_kinds,
    inconclusive_undecidables,
    independent_decisive_passes,
    judge_anchors,
)
from .provenance import (
    DOWNGRADE,
    HOLD,
    INITIAL,
    REJECTED,
    UNDECIDABLE_LOCK,
    UPGRADE,
    ProvenanceChain,
    ProvenanceEntry,
    canonical_json,
    sha256_text,
)
from .registry import LEVELS

# ── 状态集合 ────────────────────────────────────────────────────
UNDECIDABLE = "UNDECIDABLE"
BASE_LEVELS = tuple(LEVELS)  # NOT_SUPPORTED, STUB, MOCK, WITHHELD, REAL
EXTENDED_STATES = BASE_LEVELS + (UNDECIDABLE,)

# 可采信秩：UNDECIDABLE 低于 NOT_SUPPORTED（fail-closed：不确定即最不可信）
ADMISSIBLE_RANK: dict[str, int] = {lv: i for i, lv in enumerate(BASE_LEVELS)}
ADMISSIBLE_RANK[UNDECIDABLE] = -1

STATE_SEMANTICS: dict[str, str] = {
    "NOT_SUPPORTED": "无代码/测试/报告痕迹且无验证锚：能力明确不存在",
    "STUB": "仅接口或骨架，无实质实现（stub 标记命中）",
    "MOCK": "有实现但被机器证据否证（决定性 FAIL 锚／反例／测试未通过）",
    "WITHHELD": "有实现、无否证、但未收集到验证锚：证据不足，能力可能存在",
    "REAL": "机器判据闭合：≥N 条独立决定性 PASS 锚 + ≥K 类来源 + 测试通过率达标",
    "UNDECIDABLE": "有验证锚但机器无法裁决（不确定度带跨判决线／缺参考值）：fail-closed，不得采信",
}

# 门禁结论
ALLOW = "ALLOW"
BLOCK = "BLOCK"
BLOCK_UNDECIDABLE = "BLOCK_UNDECIDABLE"

# ── 规则 ID（每条变更必须引用其中之一，C5 零人工覆盖） ────────────
RULES: dict[str, str] = {
    "R01_NO_TRACE": "无任何痕迹 → NOT_SUPPORTED",
    "R02_STUB_MARKER": "命中 stub 标记 → STUB",
    "R03_COUNTEREXAMPLE": "出现决定性 FAIL 锚或反例 → MOCK（降级）",
    "R04_TEST_FAILURE": "测试通过率低于阈值 → MOCK",
    "R05_UNDECIDABLE_ADVERSE": "存在偏坏侧的不可判锚 → UNDECIDABLE（fail-closed）",
    "R06_REAL_THRESHOLD_MET": "独立决定性 PASS 锚数与来源类别数达标 → REAL（升级）",
    "R07_UNDECIDABLE_INCONCLUSIVE": "锚存在但全部不可判/不足且无决定性通过 → UNDECIDABLE",
    "R08_WITHHELD_INSUFFICIENT_ANCHORS": "决定性 PASS 锚数不足 N → WITHHELD",
    "R09_WITHHELD_NO_ANCHOR": "有实现但无任何验证锚 → WITHHELD",
    "R10_FALLBACK_WITHHELD": "兜底：证据不足 → WITHHELD",
    "R_MANUAL_OVERRIDE": "人工指定等级：恒拒绝（fail-closed，仅留痕）",
}
MANUAL_OVERRIDE_RULE = "R_MANUAL_OVERRIDE"


class ManualOverrideRejected(PermissionError):
    """人工覆盖被拒（C5）。"""

    code = "MANUAL_OVERRIDE_FORBIDDEN"

    def __init__(self, capability_id: str, requested_state: str, actor: str, reason: str = "") -> None:
        self.capability_id = capability_id
        self.requested_state = requested_state
        self.actor = actor
        self.detail = reason
        super().__init__(
            f"[{self.code}] 禁止人工指定等级: capability={capability_id} "
            f"requested={requested_state} actor={actor}；等级只能由机器判据变更"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "capability_id": self.capability_id,
            "requested_state": self.requested_state,
            "actor": self.actor,
            "detail": self.detail,
        }


class DowngradeWithoutTrigger(RuntimeError):
    """降级缺少可追溯触发证据（新反例/新证据摘要）。"""

    code = "DOWNGRADE_WITHOUT_TRIGGER"


class CapabilityAlreadyRegistered(ValueError):
    code = "ALREADY_REGISTERED"


# ── 策略（机器判据阈值，全部可配、可复算） ────────────────────────
@dataclass(frozen=True)
class EvidencePolicy:
    """判据阈值。

    tolerance : 不可接受误差上限（默认 0.15 = 15%，平台精度标准）。
    coverage_z : 不确定度覆盖因子（默认 2σ ≈ 95%）。
    min_independent_anchors : 升级到 REAL 所需独立决定性 PASS 锚数（默认 3）。
    min_distinct_anchor_kinds : 升级到 REAL 所需来源类别数（默认 2）。
    test_pass_threshold : 测试通过率下限（默认 0.80）。
    """

    tolerance: float = 0.15
    coverage_z: float = 2.0
    min_independent_anchors: int = 3
    min_distinct_anchor_kinds: int = 2
    test_pass_threshold: float = 0.80

    def __post_init__(self) -> None:
        if self.tolerance <= 0.0:
            raise ValueError("tolerance 必须为正")
        if self.coverage_z <= 0.0:
            raise ValueError("coverage_z 必须为正")
        if self.min_independent_anchors < 1:
            raise ValueError("min_independent_anchors 必须 ≥1")
        if self.min_distinct_anchor_kinds < 1:
            raise ValueError("min_distinct_anchor_kinds 必须 ≥1")
        if not 0.0 <= self.test_pass_threshold <= 1.0:
            raise ValueError("test_pass_threshold 必须在 [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tolerance": self.tolerance,
            "coverage_z": self.coverage_z,
            "min_independent_anchors": self.min_independent_anchors,
            "min_distinct_anchor_kinds": self.min_distinct_anchor_kinds,
            "test_pass_threshold": self.test_pass_threshold,
        }


DEFAULT_POLICY = EvidencePolicy()


# ── 反例（降级触发器） ───────────────────────────────────────────
@dataclass(frozen=True)
class Counterexample:
    """新反例：某算例上方法被参考值否证。

    evidence_digest 必填（进 provenance 链）；是否决定性由数字判：
    ``rel_error > tol`` 或（rel_error 为 None 且 decisive=True）。
    """

    counterexample_id: str
    description: str
    evidence_digest: str
    rel_error: float | None = None
    tolerance: float | None = None
    decisive: bool = True

    def __post_init__(self) -> None:
        if not self.counterexample_id:
            raise ValueError("counterexample_id 不能为空")
        if not self.evidence_digest:
            raise ValueError("反例必须带 evidence_digest（可追溯）")
        if self.rel_error is not None and self.rel_error < 0.0:
            raise ValueError("rel_error 必须非负")

    def is_decisive(self, default_tol: float) -> bool:
        tol = self.tolerance if self.tolerance is not None else default_tol
        if self.rel_error is not None:
            return self.rel_error > tol
        return self.decisive

    def to_dict(self) -> dict[str, Any]:
        return {
            "counterexample_id": self.counterexample_id,
            "description": self.description,
            "evidence_digest": self.evidence_digest,
            "rel_error": self.rel_error,
            "tolerance": self.tolerance,
            "decisive": self.decisive,
        }


# ── 观测（一次能力评估的输入） ──────────────────────────────────
@dataclass(frozen=True)
class Observation:
    """一次机器可读的能力观测。

    可由扫描器（scanner.ScanResult）+ 验证锚直接构造，不依赖域词条。
    """

    capability_id: str
    has_implementation: bool = False
    has_tests: bool = False
    has_reports: bool = False
    test_pass_rate: float = 0.0
    stub_markers: tuple[str, ...] = ()
    trace_count: int = 0
    anchors: tuple[VerificationAnchor, ...] = ()
    counterexamples: tuple[Counterexample, ...] = ()
    evidence_digests: tuple[str, ...] = ()
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id 不能为空")
        if not 0.0 <= self.test_pass_rate <= 1.0:
            raise ValueError("test_pass_rate 必须在 [0,1]")

    @classmethod
    def from_scan(cls, scan: Any, **kwargs: Any) -> "Observation":
        """从 scanner.ScanResult（或同构对象）构造观测。"""
        stub_markers = tuple(n for n in getattr(scan, "scan_notes", []) if "STUB" in n)
        return cls(
            capability_id=scan.capability_id,
            has_implementation=bool(getattr(scan, "has_implementation", False)),
            has_tests=bool(getattr(scan, "has_tests", False)),
            has_reports=bool(getattr(scan, "has_reports", False)),
            test_pass_rate=float(getattr(scan, "test_pass_rate", 0.0)),
            stub_markers=stub_markers,
            trace_count=int(
                getattr(scan, "code_count", 0)
                + getattr(scan, "test_count", 0)
                + getattr(scan, "report_count", 0)
            ),
            **kwargs,
        )

    def all_evidence_digests(self) -> tuple[str, ...]:
        digests = list(self.evidence_digests)
        digests.extend(a.digest() for a in self.anchors)
        digests.extend(c.evidence_digest for c in self.counterexamples)
        # 去重保序
        seen: set[str] = set()
        out: list[str] = []
        for d in digests:
            if d and d not in seen:
                seen.add(d)
                out.append(d)
        return tuple(out)


# ── 判决结果 ────────────────────────────────────────────────────
@dataclass(frozen=True)
class StateDecision:
    capability_id: str
    state: str
    rule_id: str
    reasons: tuple[str, ...]
    criteria: dict[str, Any]
    anchor_verdicts: tuple[dict[str, Any], ...] = ()
    evidence_digests: tuple[str, ...] = ()
    prev_state: str | None = None
    transition: str = INITIAL
    admissible_rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "state": self.state,
            "state_definition": STATE_SEMANTICS[self.state],
            "rule_id": self.rule_id,
            "rule": RULES[self.rule_id],
            "reasons": list(self.reasons),
            "criteria": self.criteria,
            "anchor_verdicts": list(self.anchor_verdicts),
            "evidence_digests": list(self.evidence_digests),
            "prev_state": self.prev_state,
            "transition": self.transition,
            "admissible_rank": self.admissible_rank,
            "gate": gate_decision(self.state),
        }


# ── 门禁语义 ────────────────────────────────────────────────────
def admissible_rank(state: str) -> int:
    if state not in ADMISSIBLE_RANK:
        raise ValueError(f"未知状态: {state!r}（应取 {EXTENDED_STATES}）")
    return ADMISSIBLE_RANK[state]


def gate_decision(state: str, min_rank: int = ADMISSIBLE_RANK["WITHHELD"]) -> str:
    """把证据等级映射为采信门禁结论（fail-closed）。

    - REAL            → ALLOW
    - UNDECIDABLE     → BLOCK_UNDECIDABLE（不可判 ≠ WITHHELD，独立成类）
    - 其余            → BLOCK
    """
    rank = admissible_rank(state)
    if state == UNDECIDABLE:
        return BLOCK_UNDECIDABLE
    return ALLOW if rank >= min_rank else BLOCK


def classify_transition(prev_state: str | None, new_state: str) -> str:
    if prev_state is None:
        return INITIAL
    if prev_state == new_state:
        return HOLD
    if new_state == UNDECIDABLE:
        return UNDECIDABLE_LOCK
    if admissible_rank(new_state) > admissible_rank(prev_state):
        return UPGRADE
    return DOWNGRADE


# ── 状态机 ──────────────────────────────────────────────────────
class EvidenceStateMachine:
    """五态 + 不可判态的机器判定器（纯函数式，无 IO）。"""

    def __init__(self, policy: EvidencePolicy | None = None) -> None:
        self.policy = policy or DEFAULT_POLICY

    # 单次判定 ────────────────────────────────────────────────
    def evaluate(self, obs: Observation) -> StateDecision:
        p = self.policy
        verdicts = judge_anchors(obs.anchors, tol=p.tolerance, coverage_z=p.coverage_z)
        passes, collapsed = independent_decisive_passes(verdicts)
        fails = decisive_failures(verdicts)
        adverse = adverse_undecidables(verdicts)
        inconclusive = inconclusive_undecidables(verdicts)
        pass_kinds = distinct_kinds(passes)
        decisive_cex = [c for c in obs.counterexamples if c.is_decisive(p.tolerance)]

        criteria: dict[str, Any] = {
            "policy": p.to_dict(),
            "trace_count": obs.trace_count,
            "has_implementation": obs.has_implementation,
            "has_tests": obs.has_tests,
            "test_pass_rate": obs.test_pass_rate,
            "stub_marker_count": len(obs.stub_markers),
            "anchor_count": len(obs.anchors),
            "anchor_verdict_counts": {
                PASS: sum(1 for v in verdicts if v.verdict == PASS),
                FAIL: len(fails),
                ANCHOR_UNDECIDABLE: len(inconclusive),
            },
            "independent_decisive_pass_anchors": len(passes),
            "collapsed_same_source_anchors": sum(n - 1 for n in collapsed.values() if n > 1),
            "decisive_fail_anchors": len(fails),
            "undecidable_anchors": len(inconclusive),
            "adverse_undecidable_anchors": len(adverse),
            "distinct_pass_kinds": len(pass_kinds),
            "counterexample_count": len(decisive_cex),
        }
        digests = obs.all_evidence_digests()

        def _decide(state: str, rule_id: str, reasons: Sequence[str]) -> StateDecision:
            return StateDecision(
                capability_id=obs.capability_id,
                state=state,
                rule_id=rule_id,
                reasons=tuple(reasons),
                criteria=criteria,
                anchor_verdicts=tuple(v.to_dict() for v in verdicts),
                evidence_digests=digests,
                admissible_rank=admissible_rank(state),
            )

        # R01 无任何痕迹
        if obs.trace_count == 0 and not obs.anchors and not obs.has_implementation:
            return _decide(
                "NOT_SUPPORTED", "R01_NO_TRACE",
                ["trace_count=0 且无实现、无验证锚"],
            )

        # R02 stub
        if obs.stub_markers:
            return _decide(
                "STUB", "R02_STUB_MARKER",
                [f"命中 stub 标记 {len(obs.stub_markers)} 处", *obs.stub_markers[:3]],
            )

        # R03 决定性否证（锚或反例）→ MOCK
        if fails or decisive_cex:
            reasons = [f"决定性 FAIL 锚 {len(fails)} 条"] if fails else ["决定性反例触发否证"]
            reasons.extend(v.reason for v in fails[:3])
            reasons.extend(
                f"反例 {c.counterexample_id}: rel_error={c.rel_error} > tol" for c in decisive_cex[:3]
            )
            return _decide("MOCK", "R03_COUNTEREXAMPLE", reasons)

        # R04 测试未通过 → MOCK
        if obs.has_tests and obs.test_pass_rate < p.test_pass_threshold:
            return _decide(
                "MOCK", "R04_TEST_FAILURE",
                [f"测试通过率 {obs.test_pass_rate:.3f} < {p.test_pass_threshold:.2f}"],
            )

        # R05 偏坏侧不可判 → UNDECIDABLE（fail-closed，优先于升级）
        if adverse:
            reasons = [f"偏坏侧不可判锚 {len(adverse)} 条：证据不闭合"]
            reasons.extend(v.reason for v in adverse[:3])
            return _decide(UNDECIDABLE, "R05_UNDECIDABLE_ADVERSE", reasons)

        # R06 REAL（升级）
        real_ok = (
            obs.has_implementation
            and len(passes) >= p.min_independent_anchors
            and len(pass_kinds) >= p.min_distinct_anchor_kinds
            and (not obs.has_tests or obs.test_pass_rate >= p.test_pass_threshold)
        )
        if real_ok:
            return _decide(
                "REAL", "R06_REAL_THRESHOLD_MET",
                [
                    f"独立决定性 PASS 锚 {len(passes)} ≥ {p.min_independent_anchors}",
                    f"来源类别 {len(pass_kinds)} ≥ {p.min_distinct_anchor_kinds}",
                    f"测试通过率 {obs.test_pass_rate:.3f} ≥ {p.test_pass_threshold:.2f}"
                    if obs.has_tests else "无测试证据（不阻断升级，已计入 criteria）",
                ],
            )

        # R07 锚存在但不可判（且无法升级）→ UNDECIDABLE
        if inconclusive and not passes:
            reasons = [f"验证锚 {len(obs.anchors)} 条全部不可判：无法机器裁决（fail-closed）"]
            reasons.extend(v.reason for v in inconclusive[:3])
            return _decide(UNDECIDABLE, "R07_UNDECIDABLE_INCONCLUSIVE", reasons)

        # R08 决定性 PASS 不足 N → WITHHELD
        if passes:
            return _decide(
                "WITHHELD", "R08_WITHHELD_INSUFFICIENT_ANCHORS",
                [
                    f"独立决定性 PASS 锚 {len(passes)} < {p.min_independent_anchors}",
                    f"来源类别 {len(pass_kinds)} < {p.min_distinct_anchor_kinds}"
                    if len(pass_kinds) < p.min_distinct_anchor_kinds else "来源类别达标但锚数不足",
                ],
            )

        # R09 有实现但无锚 → WITHHELD
        if obs.has_implementation and not obs.anchors:
            return _decide(
                "WITHHELD", "R09_WITHHELD_NO_ANCHOR",
                ["有实现但未收集到任何验证锚：证据不足，能力可能存在"],
            )

        # R10 兜底
        return _decide("WITHHELD", "R10_FALLBACK_WITHHELD", ["兜底：证据不足"])


# ── 台账（状态 + provenance） ─────────────────────────────────────
class EvidenceLevelLedger:
    """能力证据等级台账：唯一写入口是「机器判定 + provenance 落链」。

    - :meth:`register`  能力发现联动（C6）：新能力的初始等级由机器判据决定，
      不允许调用方指定。
    - :meth:`observe`   后续观测：升级/降级由规则表自动执行（C3/C4）。
    - :meth:`manual_override` 恒抛 :class:`ManualOverrideRejected`（C5），
      但会在链上留一条 outcome=REJECTED 记录（可审计）。
    """

    def __init__(
        self,
        policy: EvidencePolicy | None = None,
        chain: ProvenanceChain | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.policy = policy or DEFAULT_POLICY
        self.machine = EvidenceStateMachine(self.policy)
        self.chain = chain or ProvenanceChain(clock=clock)
        self._states: dict[str, str] = {}
        self._decisions: dict[str, StateDecision] = {}

    # 读 ────────────────────────────────────────────────────
    @property
    def states(self) -> dict[str, str]:
        return dict(self._states)

    def state_of(self, capability_id: str) -> str | None:
        return self._states.get(capability_id)

    def decision_of(self, capability_id: str) -> StateDecision | None:
        return self._decisions.get(capability_id)

    def history(self, capability_id: str) -> list[ProvenanceEntry]:
        return self.chain.history(capability_id)

    def summary(self) -> dict[str, Any]:
        tally = {s: 0 for s in EXTENDED_STATES}
        for s in self._states.values():
            tally[s] += 1
        return {
            "capability_count": len(self._states),
            "state_tally": tally,
            "provenance_entries": len(self.chain),
            "policy": self.policy.to_dict(),
        }

    # 写（机器） ────────────────────────────────────────────
    def register(self, obs: Observation) -> StateDecision:
        """能力发现联动：初始等级由测试/证据机器判定（C6）。"""
        if obs.capability_id in self._states:
            raise CapabilityAlreadyRegistered(
                f"能力 {obs.capability_id} 已登记；后续变更请用 observe()"
            )
        return self._commit(obs, allow_initial=True)

    def observe(self, obs: Observation) -> StateDecision:
        """新观测：自动升级/降级（C3/C4）。"""
        if obs.capability_id not in self._states:
            raise KeyError(f"能力 {obs.capability_id} 未登记；请先 register()")
        return self._commit(obs, allow_initial=False)

    def _commit(self, obs: Observation, *, allow_initial: bool) -> StateDecision:
        prev = self._states.get(obs.capability_id)
        if prev is None and not allow_initial:
            raise KeyError(f"能力 {obs.capability_id} 未登记")

        decision = self.machine.evaluate(obs)
        transition = classify_transition(prev, decision.state)

        # 降级必须带可追溯触发证据（新反例/新证据摘要），否则拒绝
        if transition == DOWNGRADE and not decision.evidence_digests:
            raise DowngradeWithoutTrigger(
                f"[{DowngradeWithoutTrigger.code}] capability={obs.capability_id} "
                f"{prev}→{decision.state} 无证据摘要，降级不可追溯"
            )

        self.chain.append(
            capability_id=obs.capability_id,
            prev_state=prev,
            new_state=decision.state,
            rule_id=decision.rule_id,
            transition=transition,
            reasons=decision.reasons,
            criteria={
                **decision.criteria,
                "prev_state": prev,
                "context": obs.context,
            },
            evidence_digests=decision.evidence_digests,
            actor="machine",
            outcome="ACCEPTED",
        )
        decision = replace(
            decision, prev_state=prev, transition=transition
        )
        self._states[obs.capability_id] = decision.state
        self._decisions[obs.capability_id] = decision
        return decision

    # 写（人工）→ 恒拒绝 ────────────────────────────────────
    def manual_override(
        self,
        capability_id: str,
        requested_state: str,
        actor: str,
        reason: str = "",
    ) -> None:
        """人工指定等级：恒抛 ManualOverrideRejected（C5），仅留痕。"""
        if requested_state not in EXTENDED_STATES:
            raise ValueError(f"未知状态: {requested_state!r}")
        prev = self._states.get(capability_id)
        self.chain.append(
            capability_id=capability_id,
            prev_state=prev,
            new_state=None,
            rule_id=MANUAL_OVERRIDE_RULE,
            transition=REJECTED,
            reasons=[f"人工覆盖请求被拒: {prev}→{requested_state}", reason or "无理由"],
            criteria={"requested_state": requested_state, "actor": actor},
            evidence_digests=(),
            actor=actor,
            outcome="REJECTED",
        )
        raise ManualOverrideRejected(capability_id, requested_state, actor, reason)

    # 一致性 ────────────────────────────────────────────────
    def verify(self) -> tuple[bool, int | None]:
        return self.chain.verify()


# ── 便捷入口：单次判定（无状态） ────────────────────────────────
def decide(
    obs: Observation, policy: EvidencePolicy | None = None
) -> StateDecision:
    return EvidenceStateMachine(policy).evaluate(obs)


def digest_of(payload: Any) -> str:
    """任意可 JSON 化载荷的 sha256（供调用方构造 evidence_digests）。"""
    return sha256_text(canonical_json(payload))


__all__ = [
    "UNDECIDABLE",
    "BASE_LEVELS",
    "EXTENDED_STATES",
    "ADMISSIBLE_RANK",
    "STATE_SEMANTICS",
    "ALLOW",
    "BLOCK",
    "BLOCK_UNDECIDABLE",
    "RULES",
    "MANUAL_OVERRIDE_RULE",
    "ManualOverrideRejected",
    "DowngradeWithoutTrigger",
    "CapabilityAlreadyRegistered",
    "EvidencePolicy",
    "DEFAULT_POLICY",
    "Counterexample",
    "Observation",
    "StateDecision",
    "admissible_rank",
    "gate_decision",
    "classify_transition",
    "EvidenceStateMachine",
    "EvidenceLevelLedger",
    "decide",
    "digest_of",
]