"""战役集成与验收门（campaign integration gate）· 平台共性机制层。

**零领域语义**：节点名、仓库、判据文字、接口名、量名、单位、证据目录全部由 JSON 工件
注入（``data/campaign_integration/*.json``）；本包只提供「收集 → 契约 → 裁定 → 成熟化单」
的通用机器。

四件事，各自可单测：

* :mod:`tools.campaign_integration.sources` —— 异构来源收集成 **presence 文档**
  （节点工作树的规范交付位 + 战役注册表快照 + 报告索引），缺就写缺，不补值；
* :mod:`tools.campaign_integration.contracts` —— 跨节点**接口契约**检查 + 注册表自洽检查，
  任一侧无证据即 ``WITHHELD``（「两边都没交付所以没矛盾」不算 0 违反）；
* :mod:`tools.campaign_integration.maturity` —— 能力台账成熟化单（只给证据成立的节点写
  ``INSERT``，并生成可回滚脚本；``WITHHELD`` 绝不写成 ``REAL``）；
* :mod:`tools.campaign_integration.report` —— 论文级六要素报告渲染 + 图（数字全注入）。

裁定引擎不重复造：节点/判据/检查三级判定、三值裁定、钉桩、互裁全部复用
:mod:`tools.maturity_gate`（同一套语义，磁盘与内存两条实现路径）。
"""

from __future__ import annotations

__all__ = ["sources", "contracts", "maturity", "report", "fetch"]

SCHEMA_VERSION = "1.0"