"""
Truth Gates — Independence-Convergence Verdict Mechanism (m613)

Three pillars:
  1. Repro verdict 2-of-3 majority voting (cheat detection)
  2. Platform-side independent metering & reconciliation
  3. Multi-constraint sidecar evidence checkers

References:
  - N-version programming: Avizienis 1985, "The N-version approach to fault-tolerant software"
  - Measurement audit: ISO/IEC 17025 (testing lab competence)
  - Byzantine fault tolerance: Lamport et al. 1982
"""
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text,
    ForeignKey, JSON, UniqueConstraint, Index
)
from database.config import Base
from models.core import JSONColumn


class ReproVerdictVote(Base):
    """
    Individual vote in a 2-of-3 majority verdict group.

    Each candidate that is about to be judged 'cheat' must be independently
    reproduced by 2 additional voters (different model pins, blind ordering,
    fresh worktrees). 3 votes per vote_group_id; >=2 'cheat' => strike confirmed.
    """
    __tablename__ = "repro_verdict_votes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vote_group_id = Column(String(64), nullable=False, index=True)
    mission_id = Column(Integer, nullable=True, index=True)
    voter_model = Column(String(64), nullable=False)  # e.g. "gpt-4", "claude-3", "local-llama"
    voter_worktree = Column(String(256), nullable=True)
    verdict = Column(String(16), nullable=False)  # cheat | pass | partial | fail
    confidence = Column(Float, nullable=True)  # 0.0 - 1.0
    evidence_hash = Column(String(64), nullable=True)  # sha256 of evidence bundle
    reasoning = Column(Text, nullable=True)
    is_blind = Column(Boolean, default=True)  # voter didn't see other votes
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("vote_group_id", "voter_model", name="uq_vote_group_voter"),
        Index("ix_verdict_group", "vote_group_id", "verdict"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "vote_group_id": self.vote_group_id,
            "mission_id": self.mission_id,
            "voter_model": self.voter_model,
            "voter_worktree": self.voter_worktree,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "evidence_hash": self.evidence_hash,
            "reasoning": self.reasoning,
            "is_blind": self.is_blind,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SplitVerdictEvent(Base):
    """
    Recorded when 2-of-3 voting fails to reach consensus (split verdict).
    E.g., 1 cheat + 1 pass + 1 partial => no majority.
    """
    __tablename__ = "split_verdict_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vote_group_id = Column(String(64), nullable=False, index=True)
    mission_id = Column(Integer, nullable=True)
    vote_tally = Column(JSONColumn, nullable=True)  # {"cheat": 1, "pass": 1, "partial": 1}
    resolution = Column(String(32), default="unresolved")  # unresolved | escalated | re_voted
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "vote_group_id": self.vote_group_id,
            "mission_id": self.mission_id,
            "vote_tally": self.vote_tally,
            "resolution": self.resolution,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class MeteredUsage(Base):
    """
    Platform-side independent metering record.
    ONLY the platform middleware may INSERT — agent accounts MUST be rejected (403).
    This is the ground truth for reconciliation against self-reported usage.
    """
    __tablename__ = "metered_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mission_id = Column(Integer, nullable=False, index=True)
    agent_session_id = Column(String(64), nullable=True)
    tool_calls = Column(Integer, default=0)
    tokens_used = Column(Integer, default=0)
    wall_seconds = Column(Float, default=0.0)
    api_calls_detail = Column(JSONColumn, nullable=True)  # per-endpoint breakdown
    recorded_by = Column(String(64), default="platform_middleware")
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_metered_mission", "mission_id"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "mission_id": self.mission_id,
            "agent_session_id": self.agent_session_id,
            "tool_calls": self.tool_calls,
            "tokens_used": self.tokens_used,
            "wall_seconds": self.wall_seconds,
            "api_calls_detail": self.api_calls_detail,
            "recorded_by": self.recorded_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class IntegrityEvent(Base):
    """
    Recorded when self-reported usage deviates from metered usage by >20%.
    Feeds into credit_ledger (negative score) and guide_tracker dashboard.
    """
    __tablename__ = "integrity_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mission_id = Column(Integer, nullable=False, index=True)
    event_type = Column(String(32), nullable=False)  # usage_deviation | hash_mismatch |计量不符单
    self_reported = Column(JSONColumn, nullable=True)
    metered = Column(JSONColumn, nullable=True)
    deviation_pct = Column(Float, nullable=True)
    severity = Column(String(16), default="warning")  # info | warning | critical
    resolved = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "mission_id": self.mission_id,
            "event_type": self.event_type,
            "self_reported": self.self_reported,
            "metered": self.metered,
            "deviation_pct": self.deviation_pct,
            "severity": self.severity,
            "resolved": self.resolved,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SidecarCheckResult(Base):
    """
    Result of a sidecar physical-property constraint check.
    Failure = red flag (does NOT kill the mission, but auto-enqueues for reproduction).
    """
    __tablename__ = "sidecar_check_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mission_id = Column(Integer, nullable=True, index=True)
    checker_name = Column(String(64), nullable=False)
    output_type = Column(String(64), nullable=False)  # e.g. "holtrop_cd_re", "wave_cw_fn"
    passed = Column(Boolean, nullable=False)
    details = Column(JSONColumn, nullable=True)
    red_flag = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_sidecar_checker", "checker_name", "output_type"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "mission_id": self.mission_id,
            "checker_name": self.checker_name,
            "output_type": self.output_type,
            "passed": self.passed,
            "details": self.details,
            "red_flag": self.red_flag,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
