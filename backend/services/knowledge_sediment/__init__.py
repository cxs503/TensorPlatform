"""知识沉淀管线（knowledge sedimentation pipeline）。

把「已完成 mission 的报告工件」变成「可检索的四契约知识条目」的**平台共性机制**：

    work/m<id>/{report.json, REPORT_GATE_SUMMARY.md, acceptance*.json}
        --extract--> {problem, method, evidence, limitations} + 溯源 sha256
        --gate-->    无 evidence / 契约不全 / 不可归属 → 拒绝入库（落 rejected 日志）
        --store-->   结构化知识库（JSONL，sha256 去重 + 相关 mission 链接）
        --api-->     GET /api/v1/knowledge/search?q=&domain=&limit=

铁律：**零领域语义**。本包只认识「报告章节 ≈ 科研过程契约」这一层形状；
领域名由配置映射（``KM_SEDIMENT_DOMAIN_MAP``）或源报文自身的 ``domain`` /
``tags`` 字段注入，代码里没有、也不允许出现任何领域词条。

模块分工::

    contracts.py  四契约字段/章节词表/完整性判据/摘要计算
    extractor.py  报告工件 → 四契约（模板 + 正则 + 归属门，来源可溯源）
    store.py      结构化存储 + sha256 去重 + 关联链接 + 检索索引 + rejected 日志
    pipeline.py   两遍扫描（索引遍算共享件 → 沉淀遍）→ 汇总 → 召回评估

设计取舍见 ``docs/knowledge-sediment-pipeline.md``。
"""

from __future__ import annotations

from .contracts import (
    CONTRACT_FIELDS,
    ENTRY_FIELDS,
    REQUIRED_ENTRY_FIELDS,
    content_sha256,
    entry_is_complete,
    has_evidence_signal,
    normalize_text,
    section_field,
)
from .extractor import (
    REASON_INCOMPLETE,
    REASON_NO_ATTRIBUTION,
    REASON_NO_EVIDENCE,
    REASON_NO_SOURCE,
    Extraction,
    extract_from_dir,
    load_domain_map,
)
from .pipeline import recall_against_reference, scan_root
from .store import KnowledgeStore, StoreResult, store_dir

__all__ = [
    "CONTRACT_FIELDS",
    "ENTRY_FIELDS",
    "REQUIRED_ENTRY_FIELDS",
    "REASON_INCOMPLETE",
    "REASON_NO_ATTRIBUTION",
    "REASON_NO_EVIDENCE",
    "REASON_NO_SOURCE",
    "Extraction",
    "KnowledgeStore",
    "StoreResult",
    "content_sha256",
    "entry_is_complete",
    "extract_from_dir",
    "has_evidence_signal",
    "load_domain_map",
    "normalize_text",
    "recall_against_reference",
    "scan_root",
    "section_field",
    "store_dir",
]