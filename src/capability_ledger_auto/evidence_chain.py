"""证据链构建器（evidence chain builder）。

对每项能力收集 theory/code/config/data/report 五元，缺件标记。
零领域语义：仅通过能力定义注册表的模式匹配。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .registry import CapabilityDef, EVIDENCE_TYPES
from .scanner import ScanResult

# 五元证据类型
FIVE_TUPLE_TYPES = ["theory", "code", "config", "data", "report"]


@dataclass
class EvidenceItem:
    """单条证据项。"""
    evidence_type: str  # theory/code/config/data/report
    source_path: str
    content_hash: str
    description: str
    is_present: bool
    quality_score: float = 0.0  # 0.0-1.0


@dataclass
class FiveTuple:
    """五元证据组。"""
    theory: EvidenceItem | None = None
    code: EvidenceItem | None = None
    config: EvidenceItem | None = None
    data: EvidenceItem | None = None
    report: EvidenceItem | None = None

    @property
    def present_count(self) -> int:
        """已存在的证据类型数。"""
        return sum(1 for item in [self.theory, self.code, self.config, self.data, self.report]
                   if item is not None and item.is_present)

    @property
    def completeness(self) -> float:
        """完整度（0.0-1.0）。"""
        return self.present_count / len(FIVE_TUPLE_TYPES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "theory": _item_to_dict(self.theory),
            "code": _item_to_dict(self.code),
            "config": _item_to_dict(self.config),
            "data": _item_to_dict(self.data),
            "report": _item_to_dict(self.report),
            "present_count": self.present_count,
            "completeness": round(self.completeness, 4),
        }


def _item_to_dict(item: EvidenceItem | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "evidence_type": item.evidence_type,
        "source_path": item.source_path,
        "content_hash": item.content_hash,
        "description": item.description,
        "is_present": item.is_present,
        "quality_score": round(item.quality_score, 4),
    }


@dataclass
class EvidenceChain:
    """能力证据链。

    包含五元证据组、扫描结果摘要、缺失项标记。
    """
    capability_id: str
    five_tuple: FiveTuple
    scan_result: ScanResult
    missing_items: list[str] = field(default_factory=list)
    chain_quality: float = 0.0  # 0.0-1.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "five_tuple": self.five_tuple.to_dict(),
            "scan_summary": {
                "code_count": self.scan_result.code_count,
                "test_count": self.scan_result.test_count,
                "report_count": self.scan_result.report_count,
                "has_implementation": self.scan_result.has_implementation,
                "has_tests": self.scan_result.has_tests,
                "has_reports": self.scan_result.has_reports,
                "test_pass_rate": self.scan_result.test_pass_rate,
                "report_verdicts": self.scan_result.report_verdicts,
            },
            "missing_items": self.missing_items,
            "chain_quality": round(self.chain_quality, 4),
            "notes": self.notes,
        }


def build_evidence_chain(
    cap: CapabilityDef,
    scan_result: ScanResult,
    root: Path,
) -> EvidenceChain:
    """为单条能力构建证据链。

    五元证据构建规则：
    - theory: 从 docs/ 或 src/ 中查找理论描述（物理方程/算法说明）
    - code: 从 scan_result.code_evidence 提取
    - config: 从 config/ 或 *.json/*.yaml 中查找配置
    - data: 从 data/ 或 tensor_reports/ 中查找数据/报告
    - report: 从 scan_result.report_evidence 提取
    """
    five_tuple = FiveTuple()
    missing = []

    # 1. theory: 查找理论文档
    theory_item = _find_theory_evidence(cap, root)
    five_tuple.theory = theory_item
    if not theory_item or not theory_item.is_present:
        missing.append("theory")

    # 2. code: 从扫描结果提取
    if scan_result.code_evidence:
        ev = scan_result.code_evidence[0]
        five_tuple.code = EvidenceItem(
            evidence_type="code",
            source_path=ev.file_path,
            content_hash=ev.file_sha256,
            description=f"代码实现: {ev.matched_text}",
            is_present=True,
            quality_score=min(1.0, scan_result.code_count * 0.3),
        )
    else:
        missing.append("code")

    # 3. config: 查找配置文件
    config_item = _find_config_evidence(cap, root)
    five_tuple.config = config_item
    if not config_item or not config_item.is_present:
        missing.append("config")

    # 4. data: 查找数据文件
    data_item = _find_data_evidence(cap, root)
    five_tuple.data = data_item
    if not data_item or not data_item.is_present:
        missing.append("data")

    # 5. report: 从扫描结果提取
    if scan_result.report_evidence:
        ev = scan_result.report_evidence[0]
        # 报告质量评分：PASS=1.0, WARN=0.5, FAIL/HONEST_FAIL=0.0
        verdict_score = {
            "PASS": 1.0,
            "WARN": 0.5,
            "FAIL": 0.0,
            "HONEST_FAIL": 0.0,
            "WITHHELD": 0.3,
        }.get(ev.verdict, 0.0)
        five_tuple.report = EvidenceItem(
            evidence_type="report",
            source_path=ev.file_path,
            content_hash=ev.file_sha256,
            description=f"报告: scenario={ev.scenario_key}, verdict={ev.verdict}",
            is_present=True,
            quality_score=verdict_score,
        )
    else:
        missing.append("report")

    # 计算链质量
    scores = []
    for item in [five_tuple.theory, five_tuple.code, five_tuple.config, five_tuple.data, five_tuple.report]:
        if item and item.is_present:
            scores.append(item.quality_score)
        else:
            scores.append(0.0)
    chain_quality = sum(scores) / len(scores) if scores else 0.0

    notes = []
    if scan_result.scan_notes:
        notes.extend(scan_result.scan_notes)
    if missing:
        notes.append(f"缺失证据项: {missing}")

    return EvidenceChain(
        capability_id=cap.capability_id,
        five_tuple=five_tuple,
        scan_result=scan_result,
        missing_items=missing,
        chain_quality=round(chain_quality, 4),
        notes=notes,
    )


def _find_theory_evidence(cap: CapabilityDef, root: Path) -> EvidenceItem | None:
    """查找理论证据（docs/ 下的文档）。"""
    docs_dir = root / "docs"
    if not docs_dir.exists():
        return EvidenceItem(
            evidence_type="theory",
            source_path="",
            content_hash="",
            description="docs/ 目录不存在",
            is_present=False,
        )

    # 搜索包含能力关键词的文档
    patterns = cap.code_patterns + [cap.capability_id, cap.label]
    for pattern in patterns:
        for fpath in docs_dir.rglob("*.md"):
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                if re.search(pattern, content, re.IGNORECASE):
                    # 检查是否有理论内容（方程、公式、算法描述）
                    theory_markers = ("方程", "equation", "算法", "algorithm", "理论", "theory",
                                     "公式", "formula", "governing")
                    has_theory = any(m in content.lower() for m in theory_markers)
                    if has_theory:
                        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                        return EvidenceItem(
                            evidence_type="theory",
                            source_path=str(fpath.relative_to(root)),
                            content_hash=content_hash,
                            description=f"理论文档: {fpath.name}",
                            is_present=True,
                            quality_score=0.7,
                        )
            except Exception:
                continue

    return EvidenceItem(
        evidence_type="theory",
        source_path="",
        content_hash="",
        description="未找到理论文档",
        is_present=False,
    )


def _find_config_evidence(cap: CapabilityDef, root: Path) -> EvidenceItem | None:
    """查找配置证据（config/ 下的配置文件）。"""
    config_dir = root / "config"
    if not config_dir.exists():
        return EvidenceItem(
            evidence_type="config",
            source_path="",
            content_hash="",
            description="config/ 目录不存在",
            is_present=False,
        )

    patterns = cap.code_patterns + [cap.capability_id]
    for pattern in patterns:
        for ext in ("*.json", "*.yaml", "*.yml", "*.toml", "*.ini"):
            for fpath in config_dir.rglob(ext):
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    if re.search(pattern, content, re.IGNORECASE):
                        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                        return EvidenceItem(
                            evidence_type="config",
                            source_path=str(fpath.relative_to(root)),
                            content_hash=content_hash,
                            description=f"配置文件: {fpath.name}",
                            is_present=True,
                            quality_score=0.5,
                        )
                except Exception:
                    continue

    return EvidenceItem(
        evidence_type="config",
        source_path="",
        content_hash="",
        description="未找到配置文件",
        is_present=False,
    )


def _find_data_evidence(cap: CapabilityDef, root: Path) -> EvidenceItem | None:
    """查找数据证据（data/ 下的数据文件）。"""
    data_dir = root / "data"
    if not data_dir.exists():
        return EvidenceItem(
            evidence_type="data",
            source_path="",
            content_hash="",
            description="data/ 目录不存在",
            is_present=False,
        )

    patterns = cap.code_patterns + [cap.capability_id]
    for pattern in patterns:
        for ext in ("*.json", "*.csv", "*.txt", "*.dat"):
            for fpath in data_dir.rglob(ext):
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    if re.search(pattern, content, re.IGNORECASE):
                        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                        return EvidenceItem(
                            evidence_type="data",
                            source_path=str(fpath.relative_to(root)),
                            content_hash=content_hash,
                            description=f"数据文件: {fpath.name}",
                            is_present=True,
                            quality_score=0.6,
                        )
                except Exception:
                    continue

    return EvidenceItem(
        evidence_type="data",
        source_path="",
        content_hash="",
        description="未找到数据文件",
        is_present=False,
    )
