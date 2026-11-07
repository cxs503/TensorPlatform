"""merge_master: 平台合并口自动化（PR 合并判据机）.

本包把「PR 能不能自动合并」从人判变成机器判：四条判据（任务验收记录在案、
质量门绿含边界扫描门、测试证据、无冲突）全绿才自动合并，否则留在队列并附
理由。所有变更动作落 append-only 审计链，每次合并带回滚路径。

零领域语义：判据只认「记录/证据/状态」字段，不认任何学科术语；越界检查本身
由 ``tools/boundary/pr_gate_scan.py`` + ``data/boundary/pr_boundary_lexicon.json``
承担（词表外置，本包不含词条）。
"""

from .model import (  # noqa: F401
    Decision,
    GateOutcome,
    MissionRecord,
    PullRequest,
    Reason,
)
from .audit import AuditLog  # noqa: F401

__all__ = [
    "AuditLog",
    "Decision",
    "GateOutcome",
    "MissionRecord",
    "PullRequest",
    "Reason",
]