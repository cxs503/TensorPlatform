"""战役播种治理机制（零领域语义）。

四个纯机制模块，供派发器/仲裁者/平台 API 复用：

- ``proposal_dedup``   提案去重门：三层表征（表层/骨架/IDF 余弦）+ 关键词扫描
                       + 近重复兄弟聚类，阈值全部可调、fail-closed。
- ``dag``              战役 DAG：依赖归一、只引用本 plan 节点、Kahn 拓扑排序、
                       显式环路径回填（环检测给出可读的环）。
- ``machine_gate``     机器门自动裁定：节点数 [5,12]、ext_id 全库唯一、repo 白名单、
                       goal 厚度、里程碑归属与数量、成熟化单条数、DAG 无环。
- ``evidence_ledger``  证据台账自动登记：验收通过 → 追加式（hash 链）台账 + S 级评价触发。

纪律：本包只认「节点/依赖/摘要/证据」这类平台通用结构，**不含任何学科词条、
判据或算例**；判据阈值/白名单/参考语料一律经参数或配置文件注入。
"""
from __future__ import annotations

__all__ = ["proposal_dedup", "dag", "machine_gate", "evidence_ledger"]