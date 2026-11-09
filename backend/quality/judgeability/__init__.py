#!/usr/bin/env python3
"""``backend.quality.judgeability`` —— 判据可裁决性派发前门（P151W1-N10）。

三层：

* ``schema``     三维结构（P1 数值阈值 / P2 参考种类 / P3 工件路径）+ 判据段切分 + 注入区块剥离
* ``classifier`` 纯函数分类（强弱判定 + 逐维命中证据 span），标签即真值可逐例复核
* ``gate``       fail-closed 分级（off/advisory/enforce/block）+ 「证据槽位」追加注入
* ``audit``      三维切片基线审计（repo/campaign/is_maturity）+ 确定性 digest（sha256 可复现）

平台铁律：本包零领域语义——只有形状、符号与方法论词表，域内容经任务书文本注入。
"""

from __future__ import annotations

from .schema import (  # noqa: F401
    CRITERIA_MARKERS,
    DIMENSIONS,
    GATE_MODES,
    MODE_ACTIONS,
    REFERENCE_KINDS,
    SCHEMA_ID,
    SCHEMA_VERSION,
    SLOT_BLOCK_BEGIN,
    SLOT_BLOCK_END,
    as_json_schema,
    criteria_segment,
    load_schema,
    strip_slot_blocks,
)
from .classifier import Judgment, classify_goal, classify_text, confusion  # noqa: F401
from .gate import (  # noqa: F401
    Decision,
    assert_append_only,
    decide,
    inject_slots,
    leak_rate,
    pre_dispatch,
    replay_enforce,
    resolve_mode,
    suggest_slots,
)
from .audit import (  # noqa: F401
    AUDIT_SCHEMA,
    audit_records,
    before_after,
    canonical_bytes,
    reproducibility,
    sha256_of,
)

__all__ = [
    "SCHEMA_ID", "SCHEMA_VERSION", "DIMENSIONS", "REFERENCE_KINDS", "GATE_MODES",
    "MODE_ACTIONS", "CRITERIA_MARKERS", "SLOT_BLOCK_BEGIN", "SLOT_BLOCK_END",
    "as_json_schema", "load_schema", "criteria_segment", "strip_slot_blocks",
    "Judgment", "classify_goal", "classify_text", "confusion",
    "Decision", "decide", "pre_dispatch", "inject_slots", "suggest_slots",
    "assert_append_only", "leak_rate", "replay_enforce", "resolve_mode",
    "AUDIT_SCHEMA", "audit_records", "before_after", "canonical_bytes",
    "reproducibility", "sha256_of",
]