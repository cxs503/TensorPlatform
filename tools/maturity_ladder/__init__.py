"""Five-level capability maturity auto-upgrade gate (p251w2-n03).

把「能力成熟度」从人工判断变成**机器可裁的五级证据链**：

    L1 Implemented → L2 Tested → L3 Benchmark-verified
    → L4 Coupled prototype → L5 Engineering-qualified

每一级升级/降级都必须通过**机器可裁的证据**（单元测试通过率、代码覆盖率、
基准误差、收敛性、耦合测试、守恒量、独立复核、工程案例、不确定度），
并记录时间、证据、裁决者与回滚点。

**零领域语义**：本包不硬编码任何领域词条/判据/阈值——五级阶梯、过渡判据、
证据度量全部由 JSON 工件注入（``criteria.py`` 的 DSL 是纯数据），代码只处理
「等级 → 判据 → 证据 → 裁定」的通用机器。测试夹具一律用合成域名 ``synth.*``。

公开面::

    from tools.maturity_ladder import (
        MATURITY_LEVELS, LEVEL_RANK, MaturityLadderEngine,
        TransitionCriteria, TransitionCheck, EvidenceBundle,
        RollbackPoint, LevelTransition, validate_ladder_schema,
        DEFAULT_CRITERIA, evaluate_transition,
    )

    engine = MaturityLadderEngine()
    verdict = engine.evaluate("synth.cap", evidence, "Tested")
    record = engine.upgrade("synth.cap", evidence, adjudicator="machine")
    # 每次升级自动记录回滚点；降级/回滚走同一审计链
"""
from __future__ import annotations

from .model import (
    MATURITY_LEVELS, LEVEL_RANK, MaturityLevel, CapabilityMaturity,
    EvidenceBundle, RollbackPoint, LevelTransition, level_rank,
)
from .criteria import (
    DEFAULT_CRITERIA, TransitionCheck, TransitionCriteria,
    evaluate_transition, transition_checks,
)
from .engine import (
    MaturityLadderEngine, TransitionVerdict, UPGRADED, DOWNGRADED,
    HELD, ROLLED_BACK, REJECTED,
)
from .schema import (
    LADDER_SCHEMA, validate_ladder_schema, validate_transition_record,
)

__all__ = [
    "MATURITY_LEVELS", "LEVEL_RANK", "MaturityLevel", "CapabilityMaturity",
    "EvidenceBundle", "RollbackPoint", "LevelTransition", "level_rank",
    "DEFAULT_CRITERIA", "TransitionCheck", "TransitionCriteria",
    "evaluate_transition", "transition_checks",
    "MaturityLadderEngine", "TransitionVerdict",
    "UPGRADED", "DOWNGRADED", "HELD", "ROLLED_BACK", "REJECTED",
    "LADDER_SCHEMA", "validate_ladder_schema", "validate_transition_record",
]
