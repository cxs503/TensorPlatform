"""Per-run verdicts, streak analysis and the promote/demote decision.

The module is pure: it takes ordered evidence runs plus a policy and returns
numbers and a decision, touching no I/O.  That is what makes the mechanism
reviewable — every level change in the ledger can be recomputed from these three
functions alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .model import (
    LEVEL_INDEX,
    LEVELS,
    TARGET_LEVEL,
    UPGRADABLE_LEVELS,
    EvidenceRun,
    dedupe_runs,
    normalise_level,
)
from .policy import UpgradePolicy

#: A run produced a verdict of "pass".
PASS = "pass"
#: A run contradicted the claim: too many failing tests.
FAIL_TESTS = "fail_tests"
#: A run contradicted the claim: error above the policy bound.
FAIL_ACCURACY = "fail_accuracy"
#: A run carried no usable evidence (no executed tests / too few samples).
INSUFFICIENT = "insufficient"

CONCLUSIVE_FAILURES = (FAIL_TESTS, FAIL_ACCURACY)

ACTION_PROMOTE = "promote"
ACTION_DEMOTE = "demote"
ACTION_HOLD = "hold"


@dataclass(frozen=True)
class RunVerdict:
    """One run judged against the policy."""

    run_digest: str
    capability_id: str
    seq: int
    kind: str
    codes: Tuple[str, ...]
    pass_rate: Optional[float]
    error_pct: float
    n_samples: int

    @property
    def is_pass(self) -> bool:
        return self.kind == PASS

    @property
    def is_conclusive_failure(self) -> bool:
        return self.kind in CONCLUSIVE_FAILURES

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_digest": self.run_digest,
            "capability_id": self.capability_id,
            "seq": self.seq,
            "kind": self.kind,
            "codes": list(self.codes),
            "pass_rate": self.pass_rate,
            "error_pct": self.error_pct,
            "n_samples": self.n_samples,
        }


def judge_run(run: EvidenceRun, policy: UpgradePolicy) -> RunVerdict:
    """Judge one run.  Precedence: red tests > accuracy breach > insufficient."""
    codes: List[str] = []
    pass_rate = run.tests.pass_rate
    error_pct = run.benchmark.error_pct

    if run.tests.executed == 0:
        codes.append("no_executed_tests")
    elif pass_rate is not None and pass_rate < float(policy.min_pass_rate):
        codes.append("test_pass_rate_below_floor")

    if run.benchmark.n_samples < int(policy.min_benchmark_samples):
        codes.append("benchmark_samples_below_min")

    if error_pct > float(policy.max_error_pct):
        codes.append("accuracy_breach")

    if "test_pass_rate_below_floor" in codes or "no_executed_tests" in codes:
        kind = FAIL_TESTS if "test_pass_rate_below_floor" in codes else INSUFFICIENT
    elif "accuracy_breach" in codes:
        kind = FAIL_ACCURACY
    elif "benchmark_samples_below_min" in codes:
        kind = INSUFFICIENT
    else:
        kind = PASS

    return RunVerdict(
        run_digest=run.digest,
        capability_id=run.capability_id,
        seq=run.seq,
        kind=kind,
        codes=tuple(codes),
        pass_rate=pass_rate,
        error_pct=error_pct,
        n_samples=run.benchmark.n_samples,
    )


@dataclass(frozen=True)
class StreakAnalysis:
    """Streaks measured from the tail of the run history."""

    verdicts: Tuple[RunVerdict, ...]
    pass_streak: int
    fail_streak: int
    insufficient_streak: int
    window_digests: Tuple[str, ...]

    @property
    def latest(self) -> Optional[RunVerdict]:
        return self.verdicts[-1] if self.verdicts else None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "n_runs": len(self.verdicts),
            "pass_streak": self.pass_streak,
            "fail_streak": self.fail_streak,
            "insufficient_streak": self.insufficient_streak,
            "window_digests": list(self.window_digests),
            "verdicts": [v.as_dict() for v in self.verdicts],
        }


def analyse(runs: Iterable[EvidenceRun], policy: UpgradePolicy) -> StreakAnalysis:
    """Dedupe, order, judge every run and measure the tail streaks."""
    ordered = dedupe_runs(runs)
    verdicts = tuple(judge_run(run, policy) for run in ordered)

    pass_streak = 0
    for verdict in reversed(verdicts):
        if verdict.is_pass:
            pass_streak += 1
        else:
            break

    fail_streak = 0
    for verdict in reversed(verdicts):
        if verdict.is_conclusive_failure:
            fail_streak += 1
        else:
            break

    insufficient_streak = 0
    for verdict in reversed(verdicts):
        if verdict.kind == INSUFFICIENT:
            insufficient_streak += 1
        else:
            break

    window = max(int(policy.promotion_window), int(policy.demotion_window))
    window_digests = tuple(v.run_digest for v in verdicts[-window:])
    return StreakAnalysis(
        verdicts=verdicts,
        pass_streak=pass_streak,
        fail_streak=fail_streak,
        insufficient_streak=insufficient_streak,
        window_digests=window_digests,
    )


@dataclass(frozen=True)
class Decision:
    action: str
    level_before: str
    level_after: str
    reason_codes: Tuple[str, ...]
    window_digests: Tuple[str, ...]
    policy_digest: str

    @property
    def changed(self) -> bool:
        return self.level_after != self.level_before

    def as_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "level_before": self.level_before,
            "level_after": self.level_after,
            "changed": self.changed,
            "reason_codes": list(self.reason_codes),
            "window_digests": list(self.window_digests),
            "policy_digest": self.policy_digest,
        }


def _floor_index(policy: UpgradePolicy) -> int:
    return LEVEL_INDEX[normalise_level(policy.min_level)]


def decide(level: str, analysis: StreakAnalysis, policy: UpgradePolicy) -> Decision:
    """Map (current level, streaks, policy) -> decision.  Pure and total."""
    level = normalise_level(level)
    idx = LEVEL_INDEX[level]

    promote_needed = int(policy.promotion_window)
    if level in UPGRADABLE_LEVELS and analysis.pass_streak >= promote_needed:
        return Decision(
            action=ACTION_PROMOTE,
            level_before=level,
            level_after=TARGET_LEVEL,
            reason_codes=(f"pass_streak={analysis.pass_streak}>={promote_needed}", "policy_satisfied"),
            window_digests=analysis.window_digests,
            policy_digest=policy.digest,
        )

    demote_needed = int(policy.demotion_window)
    if analysis.fail_streak >= demote_needed:
        target_idx = max(_floor_index(policy), idx - int(policy.demotion_steps))
        target = LEVELS[target_idx]
        if target == level:
            # Already at (or below) the floor: the failure is recorded in the
            # evaluation log, but the level is not pushed into "empty shell"
            # territory by an automated rule.
            return Decision(
                action=ACTION_HOLD,
                level_before=level,
                level_after=level,
                reason_codes=(
                    "demotion_floor_reached",
                    f"floor={policy.min_level}",
                    *(analysis.latest.codes if analysis.latest else ()),
                ),
                window_digests=analysis.window_digests,
                policy_digest=policy.digest,
            )
        return Decision(
            action=ACTION_DEMOTE,
            level_before=level,
            level_after=target,
            reason_codes=(
                f"fail_streak={analysis.fail_streak}>={demote_needed}",
                *(analysis.latest.codes if analysis.latest else ()),
            ),
            window_digests=analysis.window_digests,
            policy_digest=policy.digest,
        )

    if level == TARGET_LEVEL and analysis.insufficient_streak >= demote_needed:
        return Decision(
            action=ACTION_HOLD,
            level_before=level,
            level_after=level,
            reason_codes=("insufficient_evidence_blocks_demotion",),
            window_digests=analysis.window_digests,
            policy_digest=policy.digest,
        )

    codes: Tuple[str, ...] = ()
    if level in UPGRADABLE_LEVELS and analysis.pass_streak:
        codes = (f"pass_streak={analysis.pass_streak}<{promote_needed}",)
    elif level == TARGET_LEVEL:
        codes = ("level_is_stable",)
    if not codes:
        codes = ("no_qualifying_streak",)
    return Decision(
        action=ACTION_HOLD,
        level_before=level,
        level_after=level,
        reason_codes=codes,
        window_digests=analysis.window_digests,
        policy_digest=policy.digest,
    )