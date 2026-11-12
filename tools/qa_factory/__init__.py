"""QA 产线（FEAT-QA-FACTORY）：程序化三查机器门 + 分层抽样深检 + 口径一致性裁决 + 清账调度。

预注册判据：docs/qa_factory/PREREG_QA_FACTORY.md
"""
from .gate import (EvidenceRoots, GateVerdict, TIERS, gate_report, summarize,  # noqa: F401
                   check_structure, check_pointers, check_numbers, decide_tier,
                   extract_pointers, extract_key_numbers)
__all__ = ["EvidenceRoots", "GateVerdict", "TIERS", "gate_report", "summarize",
           "check_structure", "check_pointers", "check_numbers", "decide_tier",
           "extract_pointers", "extract_key_numbers"]
