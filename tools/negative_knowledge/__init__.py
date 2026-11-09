"""否定性知识台账（Negative Knowledge Registry）

跨域否定性知识的登记、存储与检索。零领域语义——schema 通用，
域数据经配置/工件注入。把已证伪假设炼成可查询知识库，避免重复踩坑。

核心概念
--------
* **否定性知识条目**：一条被证伪的假设/失败的方法/被阻塞的路径。
  包含假设描述、证伪证据、失败原因、适用条件边界。
* **登记**：mission 完成时自动提取 FAIL/blocked 节点入台账。
* **检索门**：派单前对提案标题/关键词做语义检索，返回相似否定记录。
* **差异化声明**：若检索命中，提案必须声明与已证伪假设的差异点。
"""

from .store import NegativeKnowledgeStore, NegativeKnowledgeEntry
from .search import NegativeKnowledgeSearcher, SearchHit
from .config import NegativeKnowledgeConfig, load_config

__all__ = [
    "NegativeKnowledgeStore",
    "NegativeKnowledgeEntry",
    "NegativeKnowledgeSearcher",
    "SearchHit",
    "NegativeKnowledgeConfig",
    "load_config",
]
