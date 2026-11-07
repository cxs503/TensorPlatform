"""数据模型：PR / 任务记录 / 判据结果 / 判决（纯数据，无 IO）。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA = "tp.merge_master.decision/v1"


class Reason:
    """留队列的理由码（机器可聚合；人可读说明在 detail 里）。"""

    NO_MISSION = "no_mission_record"          # 找不到任务验收记录
    ACCEPTANCE_MISSING = "acceptance_missing"  # 任务记录在，但验收未通过
    QUALITY_RED = "quality_red"               # 质量门红（新增失败检查）
    QUALITY_MISSING = "quality_evidence_missing"
    BOUNDARY_FAIL = "boundary_gate_fail"       # 边界扫描门判越界
    BOUNDARY_ERROR = "boundary_gate_error"     # 门自身跑不起来（保守判拒）
    TESTS_MISSING = "test_evidence_missing"    # 无测试证据 / 通过数不足
    CONFLICT = "merge_conflict"                # GitHub 判不可合并
    PR_CLOSED = "pr_closed_unmerged"
    ALREADY_MERGED = "already_merged"          # 幂等：无需再并
    ALREADY_DECIDED = "already_decided"        # 同 head SHA 已判过并合并
    BUDGET = "budget_exhausted"                # 本轮治理预算用尽
    MERGE_FAILED = "merge_api_failed"          # 合并动作本身失败
    WRITEBACK_FAILED = "mission_writeback_failed"
    DRY_RUN = "dry_run_eligible"               # plan 模式：本来可并，未执行


ACTION_MERGE = "MERGE"
ACTION_LEAVE = "LEAVE"
ACTION_SKIP = "SKIP"


@dataclass
class PullRequest:
    repo: str
    number: int
    head_sha: str
    branch: str = ""
    state: str = "open"          # open / closed
    merged: bool = False
    mergeable: Optional[bool] = None
    mergeable_state: str = ""
    base_ref: str = "main"
    title: str = ""

    @property
    def key(self) -> str:
        return "%s#%d" % (self.repo, self.number)

    def to_json(self) -> Dict[str, Any]:
        return {
            "repo": self.repo, "number": self.number, "head_sha": self.head_sha,
            "branch": self.branch, "state": self.state, "merged": self.merged,
            "mergeable": self.mergeable, "mergeable_state": self.mergeable_state,
            "base_ref": self.base_ref,
        }


@dataclass
class MissionRecord:
    """任务系统里的一条验收记录（判据一的唯一来源）。"""

    mission_id: Optional[int] = None
    status: str = ""
    accepted: bool = False
    decided_by: str = ""
    checks_text: str = ""
    report_id: Optional[int] = None
    pr_number: Optional[int] = None
    repo: str = ""
    notes: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id, "status": self.status,
            "accepted": self.accepted, "decided_by": self.decided_by,
            "report_id": self.report_id, "pr_number": self.pr_number,
            "repo": self.repo,
            "checks_text_sha256": hashlib.sha256(
                (self.checks_text or "").encode("utf-8")).hexdigest(),
        }


@dataclass
class GateOutcome:
    gate: str
    ok: bool
    detail: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {"gate": self.gate, "ok": self.ok, "detail": self.detail,
                "evidence": self.evidence}


@dataclass
class Decision:
    pr: PullRequest
    action: str
    reasons: List[str] = field(default_factory=list)
    gates: List[GateOutcome] = field(default_factory=list)
    detail: str = ""
    merge_commit: Optional[str] = None
    mission_id: Optional[int] = None
    mission_status: str = ""
    rollback: Dict[str, Any] = field(default_factory=dict)
    decided_at: str = ""

    @property
    def ok(self) -> bool:
        return self.action == ACTION_MERGE

    def to_json(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "pr": self.pr.to_json(),
            "action": self.action,
            "reasons": self.reasons,
            "detail": self.detail,
            "gates": [g.to_json() for g in self.gates],
            "merge_commit": self.merge_commit,
            "mission_id": self.mission_id,
            "mission_status": self.mission_status,
            "rollback": self.rollback,
            "decided_at": self.decided_at,
        }

    def fingerprint(self) -> str:
        blob = json.dumps(
            {"pr": self.pr.key, "sha": self.pr.head_sha,
             "action": self.action, "reasons": sorted(self.reasons)},
            sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]