"""tournament —— 百人锦标赛编排器（数量→质变的引擎件，IN25-T1）。

模块地图
--------
``model``        基因组/席位/问题规格/反同族查重/杂交算子（领域无关，零业务语义）
``ledger``       审计哈希链 + S-N 负知识轨（篡改可检）
``orchestrator`` 状态机：生成席位 → 落盘 mission → 执行取工件 → 物理锚打分 → 排名淘汰
                 → 杂交重组 → PASS 早停 / 总席位硬顶 / 冠军或全灭裁决
``report``       账本 → 机器可验 JSON + markdown
``dry_run``      受控干跑装置（解析 SDOF 物理 + 受控合成打分器）与三个演练场景

三条锦标赛基因（缺一即退化为裸数量）
------------------------------------
1. **多样性**：席位基因组含 ``model_family × framework × blind_prediction`` 三轴 +
   ``physics_anchor``；同代 ``(model_family, framework)`` 唯一且相似度上限硬门。
2. **物理锚淘汰**：打分器由调用方注入，且必须是 *参考/实验锚定* 的（``scorer_basis``
   只允许 ``reference``/``experiment``，``model`` 直接拒绝）——模型自评被结构性排除，
   即 Goodhart 免疫；席位自报分数一律丢弃并留痕。
3. **赢家杂交回流**：晋级席位的基因组交叉 + 父代实测量平均回流给子代，冠军谱系可追。
"""

from __future__ import annotations

from .ledger import AuditLog, NegativeLedger, verify_chain
from .model import (
    DIVERSITY_AXES,
    Gene,
    ProblemSpec,
    Seat,
    admissibility,
    build_goal,
    build_mission,
    candidate_pool,
    diversity_clause,
    gene_similarity,
    hybrid_gene,
    select_fresh,
)
from .orchestrator import TournamentOrchestrator, scorer_fingerprint
from .report import render_markdown, report_digest

__all__ = [
    "DIVERSITY_AXES",
    "Gene",
    "ProblemSpec",
    "Seat",
    "admissibility",
    "build_goal",
    "build_mission",
    "candidate_pool",
    "diversity_clause",
    "gene_similarity",
    "hybrid_gene",
    "select_fresh",
    "AuditLog",
    "NegativeLedger",
    "verify_chain",
    "TournamentOrchestrator",
    "scorer_fingerprint",
    "render_markdown",
    "report_digest",
]