"""memory_distill — 协调者「记忆蒸馏」职能的机器实现（IN27-H6）。

把 succeeded 报告与 shadow 处置日志蒸馏成 **候选判例**（candidate case law），
经确定性门控（gate）审后 append-only 入池，并把合格判例 append-only 同步到
bootstrap 的 PLAYBOOK 节。

设计三条硬纪律（可机器裁，见 tests/test_memory_distill_*.py）：
1. 候选判例必须自带 >=3 个引用数字或证据路径（``citations.extract`` 计数）；
2. 入池/入册一律 append-only —— 既有字节前缀哈希必须不变（``store`` / ``promote``）；
3. 全流程确定性 —— 同一输入两次运行产出同一批 fingerprint（无随机、无时间戳参与指纹）。
"""

from .citations import Citation, extract_citations, extract_numbers, extract_paths
from .distill import Boundary, Candidate, derive_candidates
from .gate import GateConfig, GateDecision, gate_candidate, jaccard
from .promote import PLAYBOOK_HEADER, promote, read_playbook_lines
from .sources import (
    load_reports,
    load_shadow_dispositions,
    write_reports_snapshot,
)
from .store import AppendOnlyStore

__all__ = [
    "Citation",
    "extract_citations",
    "extract_numbers",
    "extract_paths",
    "Boundary",
    "Candidate",
    "derive_candidates",
    "GateConfig",
    "GateDecision",
    "gate_candidate",
    "jaccard",
    "PLAYBOOK_HEADER",
    "promote",
    "read_playbook_lines",
    "load_reports",
    "load_shadow_dispositions",
    "write_reports_snapshot",
    "AppendOnlyStore",
]