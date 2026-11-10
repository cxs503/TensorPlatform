"""升级引擎：路径 → 基准 → 判据 → 证据链 → 等级裁定（含负控）。

裁定规则（顺序即优先级，全部显式、无默认放行）::

    already REAL        → no_op_already_real        （负控：等级不变）
    域边界外（实现不在本仓）→ blocked_domain_boundary
    判据未过            → rejected_criteria / rejected_not_independent / ...
    证据链缺件          → rejected_chain_incomplete
    以上皆过            → promoted（等级 → REAL）

引擎**只升不降**：升级流水线无权降级；降级必须走台账对账（OVERCLAIM 规则）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .chain import EvidenceChain
from .criteria import AcceptanceCriteria, PREDECLARED
from .executor import TrialResult, run_trial
from .model import TARGET_LEVEL, AuditTable, level_rank
from .paths import UpgradePath, plan_paths
from .validators import ProbeRegistry

PROMOTED = "promoted"
NO_OP_REAL = "no_op_already_real"
BLOCKED_DOMAIN = "blocked_domain_boundary"
REJECTED_PREFIX = "rejected_"


@dataclass(frozen=True)
class PromotionDecision:
    capability_id: str
    before: str
    after: str
    status: str
    reasons: List[str] = field(default_factory=list)
    criteria_sha256: str = ""
    chain_digest: str = ""
    trial_verdict: str = ""

    @property
    def level_changed(self) -> bool:
        return self.before != self.after

    def as_dict(self) -> Dict[str, Any]:
        return {"capability_id": self.capability_id, "before": self.before,
                "after": self.after, "status": self.status,
                "level_changed": self.level_changed, "reasons": list(self.reasons),
                "criteria_sha256": self.criteria_sha256,
                "chain_digest": self.chain_digest, "trial_verdict": self.trial_verdict}


class MaturityUpgradeEngine:
    """能力成熟化升级引擎（域无关）。"""

    def __init__(self, table: AuditTable, criteria: AcceptanceCriteria = PREDECLARED,
                 registry: Optional[ProbeRegistry] = None,
                 blockers: Optional[Mapping[str, Sequence[str]]] = None) -> None:
        self.table = table
        self.criteria = criteria
        self.registry = registry or ProbeRegistry()
        self.blockers = {k: tuple(v) for k, v in (blockers or {}).items()}
        self._levels: Dict[str, str] = {r.cap_id: r.reconciled for r in table.rows}
        self.paths: List[UpgradePath] = []
        self.decisions: List[PromotionDecision] = []
        self.trials: Dict[str, TrialResult] = {}

    # ── 路径 ─────────────────────────────────────────────────────────
    def plan(self, done: Optional[Mapping[str, Iterable[str]]] = None) -> List[UpgradePath]:
        self.paths = plan_paths(self.table.rows, self.criteria, self.blockers, done)
        return self.paths

    def level(self, cap_id: str) -> str:
        if cap_id in self._levels:
            return self._levels[cap_id]
        raise KeyError(f"审计表里没有能力 {cap_id!r}")

    # ── 试验 + 裁定 ───────────────────────────────────────────────────
    def execute(self, spec: Mapping[str, Any]) -> TrialResult:
        trial = run_trial(spec, self.registry, self.criteria)
        self.trials[trial.capability_id] = trial
        return trial

    def promote(self, trial: TrialResult, chain: Optional[EvidenceChain] = None,
                cap_id: Optional[str] = None) -> PromotionDecision:
        cap = cap_id or trial.capability_id
        before = self.level(cap)
        crit_sha = self.criteria.sha256()
        chain_digest = chain.digest() if chain is not None else ""

        def decide(after: str, status: str, reasons: List[str]) -> PromotionDecision:
            d = PromotionDecision(capability_id=cap, before=before, after=after, status=status,
                                  reasons=reasons, criteria_sha256=crit_sha,
                                  chain_digest=chain_digest, trial_verdict=trial.verdict)
            self.decisions.append(d)
            if after != before:
                self._levels[cap] = after
            return d

        # 负控一：已是 REAL → 不得因升级流水线改变等级
        if before == TARGET_LEVEL:
            return decide(before, NO_OP_REAL,
                          ["等级已是 REAL：升级流水线为 no-op（负控），等级保持不变"])
        # 域边界：实现不在本仓 → 有路径但不可在本仓执行
        if cap in self.blockers:
            return decide(before, BLOCKED_DOMAIN, list(self.blockers[cap]))
        if not trial.passed:
            return decide(before, trial.verdict if trial.verdict.startswith(REJECTED_PREFIX)
                          else REJECTED_PREFIX + trial.verdict, list(trial.reasons))
        if chain is None:
            return decide(before, "rejected_chain_incomplete", ["未提供证据链"])
        completeness = chain.complete(self.criteria)
        if not completeness["complete"]:
            return decide(before, "rejected_chain_incomplete",
                          [f"证据链缺件：{completeness['missing']}"])
        if level_rank(before) >= level_rank(TARGET_LEVEL):
            return decide(before, NO_OP_REAL, ["等级已达上限 REAL"])
        return decide(TARGET_LEVEL, PROMOTED,
                      list(trial.reasons) + [f"证据链完整（digest={chain_digest[:12]}）"])

    # ── 汇总 ─────────────────────────────────────────────────────────
    def register_snapshot(self) -> Dict[str, str]:
        """升级后的等级快照（负控要求：REAL 能力的值必须与升级前一致）。"""
        return dict(sorted(self._levels.items()))

    def negative_control(self, cap_ids: Iterable[str]) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        ok = True
        for cap in cap_ids:
            before = next((r.reconciled for r in self.table.rows if r.cap_id == cap), None)
            after = self._levels.get(cap)
            unchanged = before == after == TARGET_LEVEL
            ok = ok and unchanged
            rows.append({"capability_id": cap, "before": before, "after": after,
                         "unchanged_real": unchanged})
        return {"controls": rows, "pass": ok}

    def audit_log(self) -> List[Dict[str, Any]]:
        return [d.as_dict() for d in self.decisions]
