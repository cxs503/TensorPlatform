"""demand_intake —— 需求入榜 v2（FEAT-DEMAND-AUTOINTAKE，m17134）。

一句话：**agent 的建议自动变单子，门在事后**。v1（m17133）把建议扣在
``pending_review`` 等采认，结构上让「独立思考」停在半路；v2 用四件事换掉那道门：

① 拆事前门 —— 铸造即 ``feature_ledger.status='open'`` + **同步** ``POST /tensor/missions``
   （提案即单子），不再有 ``pending_review``；
② 审计改事后 —— 抽样帽 20/日（agent-provided 锚），不过则 ``dropped`` + 关联**未终态**单
   ``revoked`` + ``chronicle_snapshots`` 留痕；
③ 机器门 —— 帽 10/agent/日，``strikes ≥ 3`` 降级 1/日，并写 ``worker_credit_tiers`` 联动；
④ 对照计量 —— 周报给出 agent 铸造 vs 协调者铸造的过审率/单子成功率对照表（能力量尺）。

模块分工
--------
- :mod:`.slugs`      slug 规范化/校验、标题相似度（纯函数，v1 原样复用）
- :mod:`.parser`     finish 结果 → 候选锚（结构化 JSON + 文本兜底，v1 原样复用）
- :mod:`.judge`      三条质量判据（纯函数，v2 用作**事后**审计判据）
- :mod:`.store`      DDL / SQL builder / psql 传输 / 只读硬闸
- :mod:`.mint`       铸造机门（原子配额 + slug 唯一）+ strike/信用档联动
- :mod:`.dispatch`   「提案即单子」的同步建单客户端（可注入，测试不联网）
- :mod:`.audit`      事后抽样审计 + 处置链（dropped / revoke / strike / chronicle）
- :mod:`.report`     周报对照计量（SQL 聚合 → Markdown 表）
- :mod:`.redteam`    五条可失败红队场景（并发同 slug / 帽超限 / strike 降级 /
                     审计回撤 / 审计帽）
- :mod:`.cli`        入口：``ddl`` / ``mint`` / ``scan`` / ``audit`` / ``report`` /
                     ``redteam`` / ``status``

平台中性纪律：本包只认「账本税则」（slug 形状、量化验收的机器判据、状态机、额度），
**零领域语义**——候选的 domain/title/acceptance 全部来自注入的行；测试与红队用合成
slug（``FEAT-SYNTH-*``），域词条不进代码。
"""

from __future__ import annotations

__version__ = "2.0.0"

__all__ = ["slugs", "parser", "judge", "store", "mint", "dispatch", "audit",
           "report", "redteam", "chronicle", "cli"]