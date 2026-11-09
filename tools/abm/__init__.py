"""ABM（Agent-Based Modeling）攻关编排协议 —— 通用机制层，零领域语义。

设计边界（TensorPlatform 铁律）
--------------------------------
本包只提供**与领域无关**的机制：五角色席位、七环过门、预测盲（承诺-揭示）、
六维评价表、验收聚合、哈希链留痕账本、六维统计功效门。领域内容（物理判据、文献基准、算例）
一律由调用方以 config/工件注入，**不得**在本包内出现任何域词条或域阈值。

模块
----
- ``ledger``         : 只追加、哈希链的留痕账本（JSONL），可独立复算链接完整性
- ``roles``          : 五角色席位定义、知识包校验、独立性（自批防护）校验
- ``gates``          : 七环有序过门状态机（前环未过 → 后环不可闭）
- ``blind``          : 预测盲登记器（先承诺 sha256、后揭示观测；拒绝事后改预测）
- ``dimensions``     : 六维评价表 + 多臂对照门（≥N 维可比才通过）
- ``acceptance``     : 把账本/盲登记/六维表聚合成机器可裁的验收 JSON
- ``six_dim_power``  : 六维统计功效门（置换检验 / MDE 曲线 / A/A 误报率 / 回溯功效）

纯函数优先：所有对外结果都是可确定性序列化的 dict，便于落库与逐字节复算。
"""

from .ledger import TraceLedger, canonical_json, sha256_hex  # noqa: F401
from .roles import ROLES, ROLE_LABELS, validate_knowledge_packs, check_independence  # noqa: F401
from .gates import GATES, GATE_INDEX, GateSequence  # noqa: F401
from .blind import BlindRegistry  # noqa: F401
from .dimensions import DIMENSIONS, build_table, comparison_gate  # noqa: F401
from .acceptance import evaluate  # noqa: F401
from .six_dim_power import (  # noqa: F401
    analytic_mde,
    analytic_power_paired,
    cohens_d,
    min_n_for_effect,
    paired_median_diff,
    permutation_test_six_dim,
    mde_curve,
    solve_n_for_mde,
    aa_false_positive_rate,
    retrospective_power,
    six_dim_power_gate,
    DIMENSION_DIRECTION,
    build_pairs,
    oriented_deltas,
    paired_effect,
    sign_flip_permutation_test,
    holm_adjust,
    six_dim_advantage,
    null_family_sim,
    aa_null_control,
    power_positive_control,
    power_vs_rho,
    family_decision_power_curve,
    required_n_for_family_decision,
)

__all__ = [
    "TraceLedger",
    "canonical_json",
    "sha256_hex",
    "ROLES",
    "ROLE_LABELS",
    "validate_knowledge_packs",
    "check_independence",
    "GATES",
    "GATE_INDEX",
    "GateSequence",
    "BlindRegistry",
    "DIMENSIONS",
    "build_table",
    "comparison_gate",
    "evaluate",
    "analytic_mde",
    "analytic_power_paired",
    "cohens_d",
    "paired_median_diff",
    "permutation_test_six_dim",
    "mde_curve",
    "min_n_for_effect",
    "solve_n_for_mde",
    "aa_false_positive_rate",
    "retrospective_power",
    "six_dim_power_gate",
    "DIMENSION_DIRECTION",
    "build_pairs",
    "oriented_deltas",
    "paired_effect",
    "sign_flip_permutation_test",
    "holm_adjust",
    "six_dim_advantage",
    "null_family_sim",
    "aa_null_control",
    "power_positive_control",
    "power_vs_rho",
    "family_decision_power_curve",
    "required_n_for_family_decision",
]
