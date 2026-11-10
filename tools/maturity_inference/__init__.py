"""五级成熟度推断引擎（证据图 → 标签；零领域语义）。

对外稳定面（供 CLI / 报告 / 后续节点复用）::

    from tools.maturity_inference import (
        LADDER, rank_of, legacy_cap,               # 等级阶梯
        EvidenceNode, validate_node,               # 证据节点 + fail-closed 校验
        infer, Inference,                          # 推断引擎
        check_key, screen_rows,                    # 命名空间门
        load_golden, evaluate_golden,              # 黄金集
        link_capabilities,                         # 证据链装配（桩/真两态）
        plan_migration, apply_migration,           # node.* 记账行迁移
    )

设计铁律
--------
* **只降不升**：自述等级（claimed_level）不参与抬级，只能被证据推翻后下调。
* **fail-closed**：任一环证据缺件（artifact_sha256 / 重跑命令 / 判据数值+容差 /
  provenance）即在该环截断，等级停在其下一级，且原因入 ``reasons``。
* **零领域语义**：本包不出现任何域词条；域能力键与域算例经工件（ledger / config）
  注入（见 ``interface_contract``）。
"""

from .ladder import (  # noqa: F401
    LADDER,
    RANK_OF,
    legacy_cap,
    level_of_rank,
    rank_of,
)
from .evidence import EvidenceNode, validate_node, RUNG_REQUIREMENTS  # noqa: F401
from .namespace import check_key, screen_rows, KEY_RE, RESERVED_PREFIXES  # noqa: F401
from .engine import Inference, infer  # noqa: F401
from .golden import evaluate_golden, load_golden  # noqa: F401
from .linker import link_capabilities  # noqa: F401
from .migrate import apply_migration, plan_migration  # noqa: F401

__all__ = [
    "LADDER",
    "RANK_OF",
    "legacy_cap",
    "level_of_rank",
    "rank_of",
    "EvidenceNode",
    "validate_node",
    "RUNG_REQUIREMENTS",
    "check_key",
    "screen_rows",
    "KEY_RE",
    "RESERVED_PREFIXES",
    "Inference",
    "infer",
    "evaluate_golden",
    "load_golden",
    "link_capabilities",
    "plan_migration",
    "apply_migration",
]