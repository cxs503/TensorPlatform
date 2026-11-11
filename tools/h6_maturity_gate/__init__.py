"""H6-MATURITY-GATE：能力增量账本 + 五级成熟度 + 交叉一致性。

本包实现 H6 验收门的完整闭环：
1. 自述快照(baseline) vs 证据台账(current) 逐能力秩比较
2. 四轴交叉一致性（自述↔对账 / 引文逐字绑定 / 人读表↔台账重算 / 等级↔证据同向）
3. 增量账本（逐能力 direction + 前驱哈希链）
4. 五级成熟度画像 + 判据 C1..C8 → 裁定

零领域语义：所有能力名/域名通过配置注入，代码内无域词条。
"""
from __future__ import annotations

from .runner import H6MaturityGate, H6GateResult
from .adjudicator import cross_consistency_check, increment_ledger
from .reporter import render_h6_report

__all__ = [
    "H6MaturityGate", "H6GateResult",
    "cross_consistency_check", "increment_ledger",
    "render_h6_report",
]

__version__ = "1.0.0"
