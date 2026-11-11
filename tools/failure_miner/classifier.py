"""Six-class failure signature classifier.

Classifies failed missions into one of six signature types:
  infra_lost / timeout / criteria_infeasible / duplicate_seed /
  physics_honest_fail / report_owed

Zero domain semantics: domain blacklist is injected at runtime via
``domain_blacklist`` parameter; no domain vocabulary is hardcoded here.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .signature_miner import FailedMissionView, SIGNATURE_TYPES

# ─────────────────────────────── 分类规则 ───────────────────────────────

# Timeout indicators (wall clock / budget exhaustion)
_TIMEOUT_PATTERNS = [
    r"wall_exhausted",
    r"budget_exhausted",
    r"max_wall_seconds",
    r"max_tool_calls",
    r"max_tokens",
    r"timeout",
    r"timed out",
    r"deadline exceeded",
    r"wall clock",
    r"时间耗尽",
    r"超时",
    r"预算耗尽",
]

# Infrastructure lost indicators (agent lost, connection lost, endpoint disease)
_INFRA_LOST_PATTERNS = [
    r"agent_lost",
    r"worker.crash",
    r"worker_crash",
    r"endpoint",
    r"connection refused",
    r"connection reset",
    r"unable to access",
    r"could not fetch",
    r"Failed to connect",
    r"RPC failed",
    r"network",
    r"promisor remote",
    r"infrastructure",
    r"服务不可用",
    r"连接失败",
    r"端点",
]

# Criteria infeasible indicators (acceptance criteria impossible)
_CRITERIA_INFEASIBLE_PATTERNS = [
    r"criteria.*infeasible",
    r"acceptance.*impossible",
    r"判据.*不可",
    r"目标不可判定",
    r"判据缺失",
    r"acceptance_criteria",
    r"goal.*unverifiable",
    r"unverifiable",
]

# Honest fail indicators (physics honest failure)
_HONEST_FAIL_PATTERNS = [
    r"honest_fail",
    r"honest.fail",
    r"honest-FAIL",
    r"honest fail",
    r"诚实失败",
    r"物理限制",
    r"physics.*limit",
    r"theoretical.*limit",
    r"cannot.*achieve",
    r"无法达到",
]

# Report owed indicators (mission completed but no report)
_REPORT_OWED_PATTERNS = [
    r"report_owed",
    r"no.*report",
    r"missing.*report",
    r"未提交报告",
    r"报告缺失",
    r"交付留痕",
]


def _match_patterns(text: str, patterns: list[str]) -> bool:
    """Check if any pattern matches the text (case-insensitive)."""
    if not text:
        return False
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def _safe_get_result(m: FailedMissionView) -> Mapping[str, Any]:
    """Safely get result dict, handling string/None cases."""
    result = m.result
    if result is None:
        return {}
    if isinstance(result, str):
        return {"_raw": result}
    if isinstance(result, Mapping):
        return result
    return {"_raw": str(result)}


def classify_mission(m: FailedMissionView, *,
                     domain_blacklist: list[str] | None = None) -> str:
    """Classify a failed mission into one of six signature types.

    Priority order (first match wins):
      1. report_owed — status=failed/stopped AND no report AND no other strong signal
      2. timeout — wall clock / budget exhaustion
      3. infra_lost — agent lost / connection lost / endpoint disease
      4. criteria_infeasible — acceptance criteria impossible
      5. physics_honest_fail — honest physics failure
      6. duplicate_seed — (detected at clustering stage, not here)

    If none match, default to infra_lost (catch-all for unknown failures).

    Args:
        m: FailedMissionView
        domain_blacklist: domain vocabulary list (injected, not hardcoded)

    Returns:
        One of SIGNATURE_TYPES
    """
    reason = _extract_full_reason(m)
    fail_stage = (m.fail_stage or "").lower()
    status = (m.status or "").lower()

    # ── report_owed: failed/stopped + no report + no strong failure signal ──
    if not m.has_report and status in ("failed", "stopped"):
        # Check if there's any strong failure signal
        has_strong_signal = (
            _match_patterns(reason, _TIMEOUT_PATTERNS)
            or _match_patterns(reason, _INFRA_LOST_PATTERNS)
            or _match_patterns(reason, _CRITERIA_INFEASIBLE_PATTERNS)
            or _match_patterns(reason, _HONEST_FAIL_PATTERNS)
        )
        if not has_strong_signal:
            return "report_owed"

    # ── timeout ──
    if _match_patterns(reason, _TIMEOUT_PATTERNS):
        return "timeout"
    if fail_stage in ("wall_exhausted", "budget_exhausted"):
        return "timeout"

    # ── infra_lost ──
    if _match_patterns(reason, _INFRA_LOST_PATTERNS):
        return "infra_lost"
    if fail_stage in ("agent_lost", "worker_crash", "worker-crash", "endpoint"):
        return "infra_lost"

    # ── criteria_infeasible ──
    if _match_patterns(reason, _CRITERIA_INFEASIBLE_PATTERNS):
        return "criteria_infeasible"
    if fail_stage in ("intake",) and _match_patterns(reason, ["不可判定", "判据", "unverifiable"]):
        return "criteria_infeasible"

    # ── physics_honest_fail ──
    if _match_patterns(reason, _HONEST_FAIL_PATTERNS):
        return "physics_honest_fail"
    if fail_stage in ("honest_fail", "honest-fail"):
        return "physics_honest_fail"

    # ── report_owed (secondary check: has_report=False is strong signal) ──
    if not m.has_report:
        return "report_owed"

    # ── default: infra_lost (catch-all) ──
    return "infra_lost"


def _extract_full_reason(m: FailedMissionView) -> str:
    """Extract all available reason text from mission evidence."""
    parts = []
    result = _safe_get_result(m)

    # From result.evidence.checks.reason
    evidence = result.get("evidence")
    if isinstance(evidence, Mapping):
        checks = evidence.get("checks")
        if isinstance(checks, Mapping):
            reason = checks.get("reason")
            if isinstance(reason, str) and reason.strip():
                parts.append(reason)
            fail_stage = checks.get("fail_stage")
            if isinstance(fail_stage, str) and fail_stage.strip():
                parts.append(fail_stage)

    # From result.headline
    headline = result.get("headline")
    if isinstance(headline, str) and headline.strip():
        parts.append(headline)

    # From result.stop_condition
    stop = result.get("stop_condition")
    if isinstance(stop, str) and stop.strip():
        parts.append(stop)

    # From fail_stage column
    if m.fail_stage:
        parts.append(str(m.fail_stage))

    # From lifecycle events
    for evt in m.lifecycle_events:
        detail = evt.get("detail", "")
        if isinstance(detail, str) and detail.strip():
            parts.append(detail)
        elif isinstance(detail, dict):
            reason = detail.get("reason", "")
            if isinstance(reason, str) and reason.strip():
                parts.append(reason)

    return " ".join(parts)


def classify_batch(missions: list[FailedMissionView], *,
                   domain_blacklist: list[str] | None = None) -> list[tuple[FailedMissionView, str]]:
    """Classify a batch of missions.

    Returns:
        List of (mission, signature_type) tuples.
    """
    return [(m, classify_mission(m, domain_blacklist=domain_blacklist)) for m in missions]


def classification_report(classified: list[tuple[FailedMissionView, str]]) -> dict[str, Any]:
    """Generate classification summary statistics."""
    counts: dict[str, int] = {t: 0 for t in SIGNATURE_TYPES}
    for _, sig_type in classified:
        counts[sig_type] = counts.get(sig_type, 0) + 1

    total = len(classified)
    return {
        "total": total,
        "distribution": counts,
        "percentages": {
            k: round(v / total * 100, 2) if total > 0 else 0
            for k, v in counts.items()
        },
    }
