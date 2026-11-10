"""能力台账语义三元分层（capability / delivery / evidence）+ NOT_SUPPORTED 误用治理。

模块地图
--------
``model``     三层数据结构（``DeliveryArtifact`` / ``EvidenceRecord`` / ``SemanticTriple``）
``sources``   声明源与配置装载（域数据唯一入口，代码零领域语义）
``scan``      交付物扫描适配（默认复用仓内三源扫描内核，可注入替身）
``layering``  三层装配与完整性核算（判据 a）
``evidence``  证据等级评定级联（判据 c）
``misuse``    NOT_SUPPORTED 误用扫描 / 补丁 / 复扫验证（判据 b）
``graph``     能力依赖图、环与孤立检测（判据 d）
``pipeline``  一键流水线 + 验收判据 (a)–(f) + sha256 工件链
``figures``   报告图（等级分布 / 三层覆盖 / 依赖图）
"""
from __future__ import annotations

from .evidence import (ACCURACY_TEXT, REASON_TEXT, accuracy_flag, audit_evidence,
                       derive_level, grade_triples)
from .graph import DependencyGraph, build_graph
from .layering import (CLASS_BOTH, CLASS_CAPABILITY_ONLY, CLASS_DELIVERY_ONLY,
                       CLASS_EVIDENCE_ONLY, LayeredLedger, build_layers, classify_tiers)
from .misuse import (KIND_CLAIM_WITHOUT_DELIVERY, KIND_UNDERCLAIM, KIND_UNREGISTERED,
                     MisuseFinding, build_registry_patch, scan_misuse, verify_patch)
from .model import (DELIVERY_KINDS, LEVEL_RANK, LEVELS, TIER_CAPABILITY, TIER_DELIVERY,
                    TIER_EVIDENCE, Declaration, DeliveryArtifact, EvidenceRecord,
                    SemanticTriple, level_rank, normalise_level)
from .pipeline import PipelineResult, run_pipeline
from .sources import (EndpointModule, SemanticsConfig, file_sha256, inventory_endpoint_modules,
                      load_declarations, load_semantics_config, sha256_payload)

__all__ = [
    "LEVELS", "LEVEL_RANK", "DELIVERY_KINDS", "TIER_CAPABILITY", "TIER_DELIVERY",
    "TIER_EVIDENCE", "level_rank", "normalise_level",
    "Declaration", "DeliveryArtifact", "EvidenceRecord", "SemanticTriple",
    "SemanticsConfig", "EndpointModule", "file_sha256", "sha256_payload",
    "load_declarations", "load_semantics_config", "inventory_endpoint_modules",
    "LayeredLedger", "build_layers", "classify_tiers", "CLASS_BOTH",
    "CLASS_CAPABILITY_ONLY", "CLASS_DELIVERY_ONLY", "CLASS_EVIDENCE_ONLY",
    "derive_level", "grade_triples", "audit_evidence", "accuracy_flag",
    "REASON_TEXT", "ACCURACY_TEXT",
    "scan_misuse", "build_registry_patch", "verify_patch", "MisuseFinding",
    "KIND_UNDERCLAIM", "KIND_UNREGISTERED", "KIND_CLAIM_WITHOUT_DELIVERY",
    "DependencyGraph", "build_graph",
    "PipelineResult", "run_pipeline",
]
__version__ = "1.0.0"