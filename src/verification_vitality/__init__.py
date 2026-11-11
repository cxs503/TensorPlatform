"""验证面活性巡检（verification-surface vitality patrol）。

本包解决的是一个**静默失效**问题：测试文件可以存在、CI 可以是绿的，但测试从未被
收集执行过——典型成因是符号迁移（模块改名/符号搬家/再导出被删）。此时能力台账
仍写 REAL，而支撑它的验证面其实已经死了。

三个部件：

* :mod:`verification_vitality.imports`   —— AST 静态导入解析（模块/符号是否可解析）
* :mod:`verification_vitality.collector` —— ``pytest --collect-only`` 的真实收集结果
* :mod:`verification_vitality.patrol`    —— 二者合成 alive / dead / unmapped 三态

以及两个消费方：

* :mod:`verification_vitality.ledger_cross` —— 与能力台账交叉，dead 且台账 REAL 时
  fail-closed 并给出降级建议（不改台账，只出建议 JSON）
* :mod:`verification_vitality.upgrade`      —— 证据驱动升账（WITHHELD/MOCK → REAL）
  带 rollback guard

零领域语义：能力与验证面的对应关系全部来自注册表 ``config/capabilities.json`` 的
``test_patterns``（模式匹配），包内不硬编码任何域词条。
"""
from __future__ import annotations

from .collector import CollectResult, PassRateResult, run_pytest_collect, run_pytest_pass_rate
from .imports import ImportFinding, scan_file, scan_tree
from .ledger_cross import cross_ledger, load_deltas, load_feature_ledger
from .patrol import patrol, patrol_summary
from .upgrade import (UpgradeCriteria, apply_upgrades, downgrade, evaluate_upgrades, load_state,
                      rollback, save_state)

__all__ = [
    "CollectResult",
    "PassRateResult",
    "run_pytest_collect",
    "run_pytest_pass_rate",
    "ImportFinding",
    "scan_file",
    "scan_tree",
    "cross_ledger",
    "load_deltas",
    "load_feature_ledger",
    "patrol",
    "patrol_summary",
    "UpgradeCriteria",
    "evaluate_upgrades",
    "apply_upgrades",
    "downgrade",
    "load_state",
    "save_state",
    "rollback",
]

SCHEMA = "verification-vitality/1.0"