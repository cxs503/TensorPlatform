"""提案→战役播种供给链三道机器门（零领域语义）。

模块分工（每一层都可脱库/脱网单测）：

* :mod:`tools.proposal_gate.config`   —— 唯一参数来源（config/proposal_gate.json），
  拒绝代码里散落阈值；域词黑名单也从配置文件注入。
* :mod:`tools.proposal_gate.textsim`  —— 文本归一化（NFKC/折叠空白/去标点/小写）+
  字符 n-gram 集合 + Jaccard/包含度。
* :mod:`tools.proposal_gate.contract` —— JSON Schema 双版本契约（v1 历史口径 / v2 任务书口径）
  + v1→规范模型的显式 compat 层 + fail-closed 校验。
* :mod:`tools.proposal_gate.index`    —— 双索引（campaign_nodes 真库 + 历史提案池）。
* :mod:`tools.proposal_gate.ledger`   —— proposal_gate_ledger 四态账本（幂等唯一键）。
* :mod:`tools.proposal_gate.gate`     —— 仲裁入口（契约门 → 幂等门 → 去重门 → 状态落账）。
"""

from __future__ import annotations

from .config import GateConfig, load_config
from .contract import ContractResult, ProposalDoc, validate_proposal
from .gate import GateResult, ProposalGate
from .index import IndexEntry, ProposalIndex
from .ledger import LedgerStore
from .textsim import SimResult, normalize, shingles, similarity

__all__ = [
    "GateConfig",
    "load_config",
    "ContractResult",
    "ProposalDoc",
    "validate_proposal",
    "GateResult",
    "ProposalGate",
    "IndexEntry",
    "ProposalIndex",
    "LedgerStore",
    "SimResult",
    "normalize",
    "shingles",
    "similarity",
]