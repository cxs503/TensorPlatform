"""四条合并判据（gate）——每条都是纯函数，证据由 provider 注入。

判据（任务书「续航军 R3」）：
  G1 acceptance —— 任务系统验收记录在案（accepted + 决策人 + 报告/PR 关联）
  G2 quality    —— 质量门绿：CI 检查无「新增红」（相对 main 基线）+ **边界扫描门**
                   （``tools/boundary/pr_gate_scan.py``）判 PASS 作为 pre-merge 阻断门
  G3 tests      —— 测试证据：任务记录里的 pytest 通过数 / 测试类检查绿
  G4 conflict   —— 无冲突：GitHub 合并性 oracle（mergeable=True 且非 dirty）

G2 的「相对红」口径来自既有 merge_master 的经验：绝对红会把 main 上早已存在的
失败算到 PR 头上，导致队列永久堵死；判「是否新增红」才是 PR 的责任边界。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .model import GateOutcome, MissionRecord, PullRequest, Reason

G1 = "G1_acceptance"
G2 = "G2_quality"
G3 = "G3_tests"
G4 = "G4_conflict"
G5 = "G2b_boundary"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_RE = re.compile(r"(\d+)\s+failed")
TEST_CHECK_RE = re.compile(r"(pytest|test|suite|spec)", re.I)


def _int_first(pattern: re.Pattern, text: str) -> Optional[int]:
    m = pattern.search(text or "")
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# G1 任务验收记录在案
# --------------------------------------------------------------------------- #
def gate_acceptance(mission: Optional[MissionRecord]) -> GateOutcome:
    if mission is None or not mission.mission_id:
        return GateOutcome(G1, False,
                           "任务系统里找不到该 PR 的验收记录",
                           {"reason": Reason.NO_MISSION})
    problems = []
    if not mission.accepted:
        problems.append("accepted=false")
    if mission.status and mission.status.lower() in (
            "failed", "cancelled", "rejected", "blocked", "queued", "running"):
        problems.append("status=%s" % mission.status)
    if not (mission.decided_by or mission.report_id):
        problems.append("no_decider_and_no_report")
    ok = not problems
    return GateOutcome(
        G1, ok,
        "验收记录在案" if ok else "验收记录不完整: " + ",".join(problems),
        {"mission_id": mission.mission_id, "status": mission.status,
         "accepted": mission.accepted, "decided_by": mission.decided_by,
         "report_id": mission.report_id,
         "reason": "" if ok else Reason.ACCEPTANCE_MISSING})


# --------------------------------------------------------------------------- #
# G2 质量门（CI 检查相对绿）
# --------------------------------------------------------------------------- #
def _conclusion(c: Dict[str, Any]) -> str:
    return str(c.get("conclusion") or "").lower()


def failing_names(checks: Sequence[Dict[str, Any]]) -> List[str]:
    bad = []
    for c in checks or []:
        if str(c.get("status")) == "completed" and _conclusion(c) not in (
                "success", "skipped", "neutral"):
            bad.append(str(c.get("name")))
    return sorted(set(bad))


def gate_quality(checks: Sequence[Dict[str, Any]],
                 baseline_failing: Iterable[str] = ()) -> GateOutcome:
    checks = list(checks or [])
    baseline: Set[str] = set(baseline_failing or ())
    if not checks:
        return GateOutcome(G2, True, "该仓无 CI 检查（以边界门为质量阻断面）",
                           {"ci": "absent", "baseline_failing": sorted(baseline)})
    new_red = [n for n in failing_names(checks) if n not in baseline]
    pending = [str(c.get("name")) for c in checks
               if str(c.get("status")) != "completed"]
    ok = not new_red and not pending
    detail = "质量门绿（无新增红）"
    if new_red:
        detail = "新增失败检查: " + ",".join(new_red)
    elif pending:
        detail = "检查未完成: " + ",".join(pending)
    return GateOutcome(G2, ok, detail,
                       {"total": len(checks), "new_red": new_red,
                        "baseline_failing": sorted(baseline),
                        "pending": pending,
                        "reason": "" if ok else Reason.QUALITY_RED})


# --------------------------------------------------------------------------- #
# G2b 边界扫描门（pre-merge 阻断）
# --------------------------------------------------------------------------- #
def _import_scanner():
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from tools.boundary import pr_gate_scan  # noqa: WPS433
    return pr_gate_scan


def gate_boundary(diff_text: Optional[str],
                  *, lexicon_path: Optional[str] = None,
                  repo_root: Optional[Path] = None) -> GateOutcome:
    """把 PR 新增行过一遍边界词表；FAIL 即阻断合并（不依赖任何人工 review）。"""
    if diff_text is None:
        return GateOutcome(G5, False, "拿不到 PR diff，保守判拒",
                           {"reason": Reason.BOUNDARY_ERROR,
                            "error": "diff_unavailable"})
    try:
        scanner = _import_scanner()
        lex = scanner.resolve_lexicon(lexicon_path,
                                      repo_root or _REPO_ROOT)
        res = scanner.scan_diff(diff_text, lex, mode="diff")
        rep = res.to_json()
    except Exception as exc:  # 门自身故障 → 保守判拒（不放行未判的 PR）
        return GateOutcome(G5, False, "边界门运行失败，保守判拒: %r" % (exc,),
                           {"reason": Reason.BOUNDARY_ERROR,
                            "error": repr(exc)[:200]})
    ok = rep.get("verdict") == "PASS"
    counts = (rep.get("totals") or rep.get("summary") or {})
    return GateOutcome(
        G5, ok,
        "边界门 PASS" if ok else
        "边界门 FAIL：%s 处越界" % counts.get("violations", 0),
        {"verdict": rep.get("verdict"),
         "violations": (rep.get("violations") or [])[:10],
         "counts": counts,
         "lexicon": rep.get("lexicon"),
         "reason": "" if ok else Reason.BOUNDARY_FAIL})


# --------------------------------------------------------------------------- #
# G3 测试证据
# --------------------------------------------------------------------------- #
def gate_tests(mission: Optional[MissionRecord],
               checks: Sequence[Dict[str, Any]] = (),
               *, min_passed: int = 1) -> GateOutcome:
    text = (mission.checks_text if mission else "") or ""
    passed = _int_first(_PASSED_RE, text)
    failed = _int_first(_FAILED_RE, text)
    test_checks = [c for c in (checks or [])
                   if TEST_CHECK_RE.search(str(c.get("name", "")))]
    green = [str(c.get("name")) for c in test_checks
             if _conclusion(c) in ("success", "skipped", "neutral")]
    evidence = {"pytest_passed": passed, "pytest_failed": failed,
                "test_checks_green": green,
                "test_checks_total": len(test_checks)}
    ok_text = passed is not None and passed >= min_passed and not failed
    ok_ci = bool(green) and (failed in (None, 0))
    ok = bool(ok_text or ok_ci)
    if ok:
        return GateOutcome(G3, True, "测试证据在案（passed=%s, ci_green=%d）"
                           % (passed, len(green)), evidence)
    return GateOutcome(G3, False,
                       "无可用测试证据（passed=%s failed=%s ci_green=%d）"
                       % (passed, failed, len(green)),
                       dict(evidence, reason=Reason.TESTS_MISSING))


# --------------------------------------------------------------------------- #
# G4 冲突 oracle
# --------------------------------------------------------------------------- #
def gate_conflict(pr: PullRequest) -> GateOutcome:
    if pr.merged:
        return GateOutcome(G4, True, "PR 已合并（幂等）",
                           {"mergeable": pr.mergeable,
                            "state": pr.mergeable_state})
    if pr.state == "closed":
        return GateOutcome(G4, False, "PR 已关闭未合并",
                           {"reason": Reason.PR_CLOSED})
    if pr.mergeable is False or (pr.mergeable_state or "") in (
            "dirty", "conflicting"):
        return GateOutcome(G4, False,
                           "GitHub 判不可合并（state=%s）" % pr.mergeable_state,
                           {"mergeable": pr.mergeable,
                            "state": pr.mergeable_state,
                            "reason": Reason.CONFLICT})
    if pr.mergeable is None:
        return GateOutcome(G4, False, "GitHub 合并性未定（稍后重试）",
                           {"mergeable": None,
                            "state": pr.mergeable_state,
                            "reason": Reason.CONFLICT})
    return GateOutcome(G4, True, "无冲突（state=%s）" % pr.mergeable_state,
                       {"mergeable": True, "state": pr.mergeable_state})


# --------------------------------------------------------------------------- #
# 组合
# --------------------------------------------------------------------------- #
class GateSuite:
    """把四条判据按固定顺序跑一遍；任一红即不可并。"""

    def __init__(self, *, min_tests_passed: int = 1,
                 lexicon_path: Optional[str] = None,
                 repo_root: Optional[Path] = None,
                 disabled: Sequence[str] = ()):
        self.min_tests_passed = min_tests_passed
        self.lexicon_path = lexicon_path
        self.repo_root = repo_root
        # 消融实验用：显式关掉某道门（只允许在 plan/排练里用，执行路径不开口子）
        self.disabled = set(disabled or ())

    def evaluate(self, pr: PullRequest,
                 mission: Optional[MissionRecord],
                 checks: Sequence[Dict[str, Any]] = (),
                 baseline_failing: Iterable[str] = (),
                 diff_text: Optional[str] = None) -> List[GateOutcome]:
        out = [gate_acceptance(mission),
               gate_quality(checks, baseline_failing),
               gate_tests(mission, checks, min_passed=self.min_tests_passed),
               gate_conflict(pr)]
        # 已合并 / 已关闭的 PR 不再白跑 diff 扫描（省 API 预算）
        if pr.state == "open" and not pr.merged:
            out.append(gate_boundary(diff_text, lexicon_path=self.lexicon_path,
                                     repo_root=self.repo_root))
        return out

    @staticmethod
    def reasons(outcomes: Sequence[GateOutcome]) -> List[str]:
        rs: List[str] = []
        for g in outcomes:
            r = (g.evidence or {}).get("reason")
            if not g.ok and r and r not in rs:
                rs.append(r)
        return rs


def dump(outcomes: Sequence[GateOutcome]) -> str:
    return json.dumps([g.to_json() for g in outcomes], ensure_ascii=False)


def lexicon_digest(lexicon_path: Optional[str] = None) -> str:
    """审计链里记一句词表指纹：门是「哪版词表」判的要可追溯。"""
    scanner = _import_scanner()
    lex = scanner.resolve_lexicon(lexicon_path, _REPO_ROOT)
    rep = lex.describe() if hasattr(lex, "describe") else {}
    return "%s@%s" % (rep.get("path", "?"), (rep.get("sha256") or "")[:12])