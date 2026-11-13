"""demand_intake —— 需求自动接驳（FEAT-DEMAND-AUTOINTAKE）。

问题（实测，2026-11-13）
------------------------
``tensor_dev_missions`` 里 10874 张 ``succeeded`` 单，agent 的 finish 建议（「下一步
该做什么」「这里还有缺口」）只以自然语言躺在 ``result->>'headline'`` / ``checks`` 里；
``feature_ledger`` 里 ``source`` 形如 ``agent-proposed`` 的锚数量为 **0**——
需求「白提」：报告文本写完即沉底，没有任何机器路径把它变成榜上可领的锚。

本包把「提出 → 入榜 → 审计 → 可领」做成一条可重放、幂等的机器链
---------------------------------------------------------------
::

    finish result (tensor_dev_missions.result)  ─┐
    tensor_reports.markdown「局限/待办」节        ─┼─► parser ─► demand_intake_log
                                                  │              │
                                                  │              ├─► feature_ledger
                                                  │              │   status=pending_review
                                                  │              │   source=agent-proposed
                                                  │              ▼
                                                  │            gate（三判据）
                                                  │              ├─ 过 → status=open（进 feeder/board-claim 供给面）
                                                  │              │        │
                                                  │              │        ▼
                                                  │              │   audit（事后抽样，帽 20/日，min_chars=8）
                                                  │              │        ├─ pass → 保持 open（留痕）
                                                  │              │        └─ fail → dropped + 未终态单 revoked
                                                  │              └─ 不过 → status=dropped（留痕 + 原因码）
                                                  └─► demand_backlog（报告待办候选，供 coordinator 周批采认）

模块分工
--------
- :mod:`tools.demand_intake.slugs`      slug 规范化/校验、标题相似度（纯函数）
- :mod:`tools.demand_intake.parser`     finish result → 候选锚（结构化 JSON 字段 + 文本宽松扫描）
- :mod:`tools.demand_intake.reports`    tensor_reports 正文 → 局限/待办候选
- :mod:`tools.demand_intake.gate`       三判据审计门（纯函数，出原因码）
- :mod:`tools.demand_intake.audit`      事后抽样审计（日帽 / 回撤未终态单 / 留痕；判据复用 gate）
- :mod:`tools.demand_intake.store`      DDL + SQL builder + 幂等 apply（可注入 schema 做真库红队）
- :mod:`tools.demand_intake.transport`  psql 传输（只读硬闸 + statement_timeout）
- :mod:`tools.demand_intake.chronicle`  chronicle_snapshots 留痕
- :mod:`tools.demand_intake.cli`        入口：``ingest-missions`` / ``gate`` / ``ingest-reports`` /
  ``redteam`` / ``measure`` / ``status`` / ``audit``

平台中性纪律
------------
本包只认「账本税则」（slug 形状、量化验收的机器判据、状态机），**零领域语义**：
候选的 domain/title/acceptance 全部来自 DB 注入的行；测试夹具用合成 slug
（``FEAT-SYNTH-*``），域词条不进代码。
"""

from __future__ import annotations

__version__ = "1.1.0"