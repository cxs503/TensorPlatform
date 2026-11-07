"""对账引擎：把「PR 状态」重新推导成「合并 / 留因」，并落审计 + 回写任务状态。

三条硬约束（任务书基因：owner 渐退令）：
  1. **不依赖任何会话**：状态全在磁盘（决策账本 + 审计链），进程随时可被
     重启/换机；崩溃点在「合并成功、状态未落」时，下轮由 PR 的 ``merged``
     事实走幂等回写，不会重复合并。
  2. **预算内动作**：每轮有合并数与变更调用上限；超限的 PR 不丢，留在队列里
     附「budget_exhausted」，下轮继续——治理动作永不无界。
  3. **可回滚**：每次合并同时写一条 ``rollback`` 规格（revert -m 1 <merge
     commit> + 验证门），人/机制都能照单执行。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import gates as G
from .audit import AuditLog, now_iso
from .model import (ACTION_LEAVE, ACTION_MERGE, ACTION_SKIP, Decision,
                    MissionRecord, PullRequest, Reason)

DEFAULT_REPOS = ("TensorPlatform",)
SOLVER_REPOS = ("TensorSPH", "TensorDG", "TensorCAD", "TensorFEM",
                "TensorBEM", "TensorFVM", "TensorDEM", "TensorLBM")
ALL_REPOS = DEFAULT_REPOS + SOLVER_REPOS
REASON_ZH = {
    Reason.NO_MISSION: "任务系统无验收记录",
    Reason.ACCEPTANCE_MISSING: "验收未通过/记录不完整",
    Reason.QUALITY_RED: "质量门出现新增红",
    Reason.QUALITY_MISSING: "缺质量证据",
    Reason.BOUNDARY_FAIL: "边界门判越界",
    Reason.BOUNDARY_ERROR: "边界门故障（保守判拒）",
    Reason.TESTS_MISSING: "无测试证据",
    Reason.CONFLICT: "存在冲突/合并性未定",
    Reason.PR_CLOSED: "PR 已关闭未合并",
    Reason.ALREADY_MERGED: "已合并（幂等）",
    Reason.ALREADY_DECIDED: "同 head SHA 已处理",
    Reason.BUDGET: "本轮治理预算用尽",
    Reason.MERGE_FAILED: "合并动作失败",
    Reason.WRITEBACK_FAILED: "任务状态回写失败",
    Reason.DRY_RUN: "plan 模式（判据全绿，未执行合并）",
}


@dataclass
class Budget:
    max_merges: int = 3          # 每轮最多合并几个 PR
    max_mutations: int = 8       # 每轮最多多少个变更型外部调用（merge+回写）
    max_prs: int = 60            # 每轮最多清算多少张 PR
    note: str = "治理预算"

    def as_dict(self) -> Dict[str, Any]:
        return {"max_merges": self.max_merges,
                "max_mutations": self.max_mutations,
                "max_prs": self.max_prs}


@dataclass
class CycleReport:
    cycle: int
    generated_at: str
    dry_run: bool
    decisions: List[Decision] = field(default_factory=list)
    budget: Dict[str, Any] = field(default_factory=dict)
    audit: Dict[str, Any] = field(default_factory=dict)
    queue_path: str = ""

    def counts(self) -> Dict[str, int]:
        c = {"total": len(self.decisions), "merged": 0, "left": 0,
             "skipped": 0}
        for d in self.decisions:
            if d.action == ACTION_MERGE:
                c["merged"] += 1
            elif d.action == ACTION_LEAVE:
                c["left"] += 1
            else:
                c["skipped"] += 1
        return c

    def leaves(self) -> List[Decision]:
        return [d for d in self.decisions if d.action == ACTION_LEAVE]

    def merges(self) -> List[Decision]:
        return [d for d in self.decisions if d.action == ACTION_MERGE]

    def to_json(self) -> Dict[str, Any]:
        return {
            "schema": "tp.merge_master.cycle/v1",
            "cycle": self.cycle,
            "generated_at": self.generated_at,
            "dry_run": self.dry_run,
            "counts": self.counts(),
            "budget": self.budget,
            "audit": self.audit,
            "queue_path": self.queue_path,
            "decisions": [d.to_json() for d in self.decisions],
        }


def build_rollback(pr: PullRequest, merge_commit: str,
                   owner: str = "cxs503") -> Dict[str, Any]:
    """回滚规格：可被任何执行器照单执行（命令 + 验证门 + 状态修复）。"""
    return {
        "kind": "revert_merge",
        "repo": pr.repo,
        "pr": pr.number,
        "merge_commit": merge_commit,
        "base_ref": pr.base_ref or "main",
        "steps": [
            "git clone https://github.com/%s/%s.git" % (owner, pr.repo),
            "git checkout %s" % (pr.base_ref or "main"),
            "git revert -m 1 %s" % merge_commit,
            "git push origin %s" % (pr.base_ref or "main"),
        ],
        "verify": {
            "pytest": "python3 -m pytest tests/test_merge_master.py -q",
            "mission_state": "pr-gate(open) → awaiting_pr_merge",
        },
        "created_at": now_iso(),
    }


class Reconciler:
    def __init__(self, *, queue, ledger, checks, diffs, merger, audit,
                 state_path: str, gates: Optional[G.GateSuite] = None,
                 budget: Optional[Budget] = None, dry_run: bool = True,
                 method: str = "merge", max_diff_chars: int = 2_000_000):
        self.queue = queue
        self.ledger = ledger
        self.checks = checks
        self.diffs = diffs
        self.merger = merger
        self.audit: AuditLog = audit
        self.state_path = state_path
        self.gates = gates or G.GateSuite()
        self.budget = budget or Budget()
        self.dry_run = dry_run
        self.method = method
        self.max_diff_chars = max_diff_chars
        self.state = self._load_state()

    # ------------------------------------------------------------------ 状态
    def _load_state(self) -> Dict[str, Any]:
        if self.state_path and os.path.exists(self.state_path):
            try:
                return json.load(open(self.state_path, "r", encoding="utf-8"))
            except ValueError:
                return {}
        return {}

    def _save_state(self) -> None:
        if not self.state_path:
            return
        d = os.path.dirname(self.state_path)
        if d:
            os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".mm-state-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=1,
                      sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.state_path)

    # ------------------------------------------------------------------ 单张
    def _diff_text(self, pr: PullRequest) -> Optional[str]:
        txt = self.diffs.diff(pr.repo, pr.number) if self.diffs else None
        if txt is None:
            return None
        return txt[: self.max_diff_chars]

    def evaluate(self, pr: PullRequest,
                 mission: Optional[MissionRecord] = None) -> List[G.GateOutcome]:
        """判据链（懒取 diff：前三条不全绿就不花 API 预算拿 diff）。"""
        mission = mission if mission is not None else \
            self.ledger.find(pr.repo, pr.number)
        checks = self.checks.for_sha(pr.repo, pr.head_sha) \
            if (self.checks and pr.head_sha) else []
        baseline = self.checks.main_failing(pr.repo) if self.checks else []
        outs = [G.gate_acceptance(mission),
                G.gate_quality(checks, baseline),
                G.gate_tests(mission, checks,
                             min_passed=self.gates.min_tests_passed),
                G.gate_conflict(pr)]
        if pr.state == "open" and not pr.merged and all(o.ok for o in outs) \
                and G.G5 not in self.gates.disabled:
            outs.append(G.gate_boundary(
                self._diff_text(pr),
                lexicon_path=self.gates.lexicon_path,
                repo_root=self.gates.repo_root))
        if self.gates.disabled:
            outs = [o for o in outs if o.gate not in self.gates.disabled]
        return outs

    def reconcile_pr(self, pr: PullRequest) -> Decision:
        d = Decision(pr=pr, action=ACTION_LEAVE, decided_at=now_iso())
        key = pr.key
        prev = (self.state.get("decided") or {}).get(key) or {}

        mission = self.ledger.find(pr.repo, pr.number) if self.ledger else None
        d.mission_id = mission.mission_id if mission else None
        d.mission_status = mission.status if mission else ""

        # 1) PR 已被合并（事实优先于账本）→ 只补回写：
        #    覆盖「合并成功后崩溃」「别的合并器并了」「上一轮回写失败」三种情形
        if pr.merged:
            d.action = ACTION_SKIP
            d.reasons = [Reason.ALREADY_MERGED]
            d.detail = "PR 已合并，无需再并"
            d.merge_commit = prev.get("merge_commit") or pr.head_sha
            self._writeback(d, mission, note="reconcile: PR already merged")
            self._remember(d, prev)
            return self._finish(d)

        # 2) 幂等：同 head SHA 已判过并执行过 → 不再动远端
        if prev.get("action") == ACTION_MERGE and prev.get("sha") == pr.head_sha \
                and not self.dry_run:
            d.action = ACTION_SKIP
            d.reasons = [Reason.ALREADY_DECIDED]
            d.detail = "同 head SHA 已合并（决策账本）"
            d.merge_commit = prev.get("merge_commit")
            self._remember(d, prev)
            return self._finish(d)

        # 3) 判据
        outcomes = self.evaluate(pr, mission)
        d.gates = outcomes
        reasons = G.GateSuite.reasons(outcomes)
        if reasons:
            d.reasons = reasons
            d.detail = "；".join("%s: %s" % (o.gate, o.detail)
                                for o in outcomes if not o.ok)
            self._remember(d, prev)
            return self._finish(d)

        # 4) 预算闸门
        used = self.state.setdefault("__cycle__", {})
        merges = int(used.get("merges", 0))
        mutations = int(used.get("mutations", 0))
        if merges >= self.budget.max_merges or \
                mutations + 2 > self.budget.max_mutations:
            d.reasons = [Reason.BUDGET]
            d.detail = "本轮预算已用尽（merges=%d mutations=%d）" % (
                merges, mutations)
            self._remember(d, prev)
            return self._finish(d)

        # 5) plan 模式：判据全绿但不执行（治理动作只在 --execute 下发生）
        if self.dry_run:
            d.action = ACTION_MERGE
            d.detail = "判据全绿（plan 模式未执行合并）"
            d.reasons = [Reason.DRY_RUN]
            self._remember(d, prev)
            return self._finish(d)

        # 6) 执行合并
        res = self.merger.merge(pr.repo, pr.number, pr.head_sha,
                                self.method) or {}
        if not res.get("ok"):
            d.reasons = [Reason.MERGE_FAILED]
            d.detail = "merge API: status=%s %s" % (
                res.get("status"), (res.get("message") or "")[:160])
            used["mutations"] = mutations + 1
            self._remember(d, prev)
            return self._finish(d)
        d.action = ACTION_MERGE
        d.merge_commit = res.get("merge_commit") or ""
        d.detail = "已合并（%s）" % self.method
        d.rollback = build_rollback(pr, d.merge_commit or pr.head_sha)
        used["merges"] = merges + 1
        used["mutations"] = mutations + 2
        self._remember(d, prev)
        self.audit.append("merge", {
            "pr": pr.key, "sha": pr.head_sha,
            "merge_commit": d.merge_commit, "rollback": d.rollback,
            "gates": [o.to_json() for o in outcomes]})
        self._writeback(d, mission, note="auto-merge via merge_master: "
                                        "gates G1-G4+boundary PASS")
        return self._finish(d)

    # ---------------------------------------------------------------- 回写
    def _writeback(self, d: Decision, mission: Optional[MissionRecord],
                   note: str) -> None:
        if not mission or not mission.mission_id:
            d.reasons = list(d.reasons) + [Reason.NO_MISSION]
            d.detail += "；无任务记录无法回写"
            self.audit.append("writeback_skipped",
                              {"pr": d.pr.key, "reason": Reason.NO_MISSION})
            return
        if self.dry_run:
            return
        res = self.ledger.writeback(mission.mission_id, "merged", d.pr.repo,
                                   d.pr.number, d.pr.head_sha, note) or {}
        d.mission_status = str(res.get("mission_status") or d.mission_status)
        if res.get("ok"):
            self.audit.append("writeback", {
                "pr": d.pr.key, "mission_id": mission.mission_id,
                "state": "merged", "status": d.mission_status})
            self.state.setdefault("writeback_pending", {}).pop(d.pr.key, None)
        else:
            d.reasons = list(d.reasons) + [Reason.WRITEBACK_FAILED]
            d.detail += "；回写失败(status=%s)" % res.get("status")
            self.state.setdefault("writeback_pending", {})[d.pr.key] = {
                "mission_id": mission.mission_id, "repo": d.pr.repo,
                "pr": d.pr.number, "sha": d.pr.head_sha,
                "at": now_iso(), "error": str(res.get("error"))[:200]}
            self.audit.append("writeback_failed", {
                "pr": d.pr.key, "mission_id": mission.mission_id,
                "status": res.get("status"),
                "error": str(res.get("error"))[:200]})
            self._save_state()

    # ---------------------------------------------------------------- 记账
    def _remember(self, d: Decision, prev: Dict[str, Any]) -> None:
        decided = self.state.setdefault("decided", {})
        attempts = int(prev.get("attempts", 0)) + 1
        decided[d.pr.key] = {
            "sha": d.pr.head_sha, "action": d.action, "reasons": d.reasons,
            "at": d.decided_at, "attempts": attempts,
            "merge_commit": d.merge_commit or prev.get("merge_commit"),
            "mission_id": d.mission_id,
        }
        q = self.state.setdefault("queue", {})
        if d.action == ACTION_MERGE and not self.dry_run:
            q.pop(d.pr.key, None)
        elif d.action == ACTION_LEAVE or d.reasons:
            q[d.pr.key] = {"reasons": d.reasons, "at": d.decided_at,
                           "detail": d.detail[:300], "sha": d.pr.head_sha,
                           "attempts": attempts}
        self._save_state()

    def _finish(self, d: Decision) -> Decision:
        self.audit.append("decision", d.to_json())
        return d

    # ------------------------------------------------------------------ 轮
    def run_cycle(self, prs: Optional[Sequence[PullRequest]] = None,
                  limit: Optional[int] = None) -> CycleReport:
        self.state["__cycle__"] = {"merges": 0, "mutations": 0}
        cycle = int(self.state.get("cycles", 0)) + 1
        self.state["cycles"] = cycle
        self._save_state()
        self.audit.append("cycle_start", {
            "cycle": cycle, "dry_run": self.dry_run,
            "budget": self.budget.as_dict(),
            "boundary_lexicon": G.lexicon_digest(self.gates.lexicon_path)})

        items = list(prs if prs is not None else self.queue.list_awaiting())
        cap = min(limit or self.budget.max_prs, self.budget.max_prs)
        rep = CycleReport(cycle=cycle, generated_at=now_iso(),
                          dry_run=self.dry_run, budget=self.budget.as_dict())
        for pr in items[:cap]:
            try:
                rep.decisions.append(self.reconcile_pr(pr))
            except Exception as exc:  # 单张异常不得中断整轮
                d = Decision(pr=pr, action=ACTION_LEAVE,
                             reasons=[Reason.MERGE_FAILED],
                             detail="engine error: %r" % (exc,)[:200],
                             decided_at=now_iso())
                self.audit.append("engine_error",
                                  {"pr": pr.key, "error": repr(exc)[:300]})
                rep.decisions.append(d)
        seq, head = self.audit.head
        rep.audit = {"path": self.audit.path, "seq": seq, "head": head}
        self.state["last_cycle"] = rep.to_json()["counts"]
        self._save_state()
        self.audit.append("cycle_end", {
            "cycle": cycle, "counts": rep.counts(),
            "merges": [d.pr.key for d in rep.merges()],
            "leaves": {d.pr.key: d.reasons for d in rep.leaves()}})
        return rep