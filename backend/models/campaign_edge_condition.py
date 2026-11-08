"""Campaign Edge Condition model — conditional DAG routing.

Supports:
  - Gate conditions: metric-based routing (pass/fail → different next nodes)
  - Adaptive loops: on_false can point back to self (with parameter changes)
  - Convergence detection: max_iterations guard with escalation
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, Text,
)
from sqlalchemy.orm import relationship

from database.config import Base


class CampaignEdgeCondition(Base):
    """Explicit conditional edge in campaign DAG."""
    __tablename__ = "campaign_edge_conditions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("campaign_plans.id", ondelete="CASCADE"), nullable=False)
    source_node_id = Column(Integer, ForeignKey("campaign_nodes.id", ondelete="CASCADE"), nullable=False)
    # Stored as JSON text for SQLite compat; parsed to dict in API layer
    condition_expr = Column(Text, nullable=False, default="{}")
    priority = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    source_node = relationship("CampaignNode", foreign_keys=[source_node_id], lazy="select")

    __table_args__ = (
        # Unique constraint handled in migration; SQLite doesn't enforce partial
    )


class CampaignNodeRoutingMixin:
    """Mixin adding routing fields to campaign_nodes.

    These columns are added via migration 108:
      - condition: JSONB — inline condition for this node
      - convergence_check: JSONB — convergence detection config
      - iteration_count: INT — current iteration in adaptive loop
      - parent_node_id: BIGINT — reference to original node (for retry nodes)
    """
    pass  # Columns managed via raw SQL in API layer for SQLite compat
