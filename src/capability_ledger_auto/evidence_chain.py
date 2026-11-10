"""证据链构建器（evidence chain builder）。

对每项能力收集 theory/code/config/data/report 五元，缺件标记。

零领域语义：仅通过能力定义注册表的模式匹配。

反自指纪律：``exclude_paths`` 用于剔除"注册表文件自身 / 扫描引擎自身"这类
**元数据文件**，否则 ``config/capabilities.json`` 因为含有能力模式字符串，
会让每条能力的 config 证据无条件为真（历史上确实如此，五元完整性被虚抬）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .registry import CapabilityDef, EVIDENCE_TYPES
from .scanner import ScanResult, as_of_sort_key

_REPORT_SOURCE_PRIORITY = {"repo_json": 3, "adjudication_ledger": 2, "corpus": 1}

# 五元证据类型
FIVE_TUPLE_TYPES = ["theory", "code", "config", "data", "report"]

# 报告裁决 → 质量分（治理词表，与写域词条无关）
VERDICT_QUALITY = {
    "PASS": 1.0,
    "REAL": 1.0,
    "WARN": 0.5,
    "WITHHELD": 0.3,
    "UNKNOWN": 0.0,
    "FAIL": 0.0,
    "HONEST_FAIL": 0.0,
    "MOCK": 0.0,
    "STUB": 0.1,
    "NOT_SUPPORTED": 0.0,
}

THEORY_MARKERS = ("方程", "equation", "算法", "algorithm", "理论", "theory",
                  "公式", "formula", "governing", "推导", "derivation")


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
                "substantive_code_count": self.scan_result.substantive_code_count,
                "stub_only_code_count": self.scan_result.stub_only_code_count,
                "test_count": self.scan_result.test_count,
                "report_count": self.scan_result.report_count,
                "has_implementation": self.scan_result.has_implementation,
                "has_tests": self.scan_result.has_tests,
                "has_reports": self.scan_result.has_reports,
                "test_pass_rate": self.scan_result.test_pass_rate,
                "test_evidence_source": self.scan_result.test_evidence_source,
                "report_verdicts": self.scan_result.report_verdicts,
                "adjudicated_level": self.scan_result.adjudicated_level,
                "adjudicated_as_of": self.scan_result.adjudicated_as_of,
                "adjudicated_source": self.scan_result.adjudicated_source,
            },
            "missing_items": self.missing_items,
            "chain_quality": round(self.chain_quality, 4),
            "notes": self.notes,
        }


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _excluded(rel_path: str, exclude_paths: Iterable[str], root: Path | None = None) -> bool:
    """排除判定：同时接受相对路径与绝对路径形态的排除项。"""
    rel_path = rel_path.replace("\\", "/")
    for raw in exclude_paths:
        p = str(raw).replace("\\", "/")
        if rel_path == p or rel_path.startswith(p.rstrip("/") + "/"):
            return True
        if root is not None and Path(raw).is_absolute():
            try:
                p_rel = str(Path(raw).resolve().relative_to(root.resolve())).replace("\\", "/")
            except ValueError:
                continue
            if rel_path == p_rel or rel_path.startswith(p_rel.rstrip("/") + "/"):
                return True
    return False


def build_evidence_chain(
    cap: CapabilityDef,
    scan_result: ScanResult,
    root: Path,
    exclude_paths: Iterable[str] = (),
) -> EvidenceChain:
    """为单条能力构建证据链。

    五元证据构建规则：
    - theory: docs/ 下的文档（含量化/推导性内容才算）
    - code: 从 scan_result.code_evidence 提取（实质实现优先）
    - config: config/ 下的配置文件
    - data: data/ 下的数据文件
    - report: 从 scan_result.report_evidence 取**最新裁决**
    """
    root = Path(root)
    exclude_paths = tuple(exclude_paths)
    five_tuple = FiveTuple()
    missing: list[str] = []

    # 1. theory
    theory_item = _find_theory_evidence(cap, root, exclude_paths)
    five_tuple.theory = theory_item
    if not theory_item or not theory_item.is_present:
        missing.append("theory")

    # 2. code
    substantive = [e for e in scan_result.code_evidence
                   if e.match_type in ("function", "class", "text") and not e.is_stub_like]
    if substantive:
        ev = substantive[0]
        five_tuple.code = EvidenceItem(
            evidence_type="code",
            source_path=ev.file_path,
            content_hash=ev.file_sha256,
            description=f"实质实现: {ev.matched_text}",
            is_present=True,
            quality_score=min(1.0, 0.3 + 0.1 * len(substantive)),
        )
    elif scan_result.code_evidence:
        ev = scan_result.code_evidence[0]
        five_tuple.code = EvidenceItem(
            evidence_type="code",
            source_path=ev.file_path,
            content_hash=ev.file_sha256,
            description=f"仅接口声明: {ev.matched_text}",
            is_present=True,
            quality_score=0.1,
        )
    else:
        missing.append("code")

    # 3. config
    config_item = _find_config_evidence(cap, root, exclude_paths)
    five_tuple.config = config_item
    if not config_item or not config_item.is_present:
        missing.append("config")

    # 4. data
    data_item = _find_data_evidence(cap, root, exclude_paths)
    five_tuple.data = data_item
    if not data_item or not data_item.is_present:
        missing.append("data")

    # 5. report（取最新裁决，非"第一条"）
    report_ev = sorted(
        scan_result.report_evidence,
        key=lambda r: (as_of_sort_key(r.as_of), _REPORT_SOURCE_PRIORITY.get(r.source_kind, 0), r.file_path),
    )
    if report_ev:
        ev = report_ev[-1]
        level = ev.asserted_level or ev.verdict
        five_tuple.report = EvidenceItem(
            evidence_type="report",
            source_path=ev.file_path,
            content_hash=ev.file_sha256,
            description=f"报告: scenario={ev.scenario_key}, 裁决={level}",
            is_present=True,
            quality_score=VERDICT_QUALITY.get(level, 0.0),
        )
    else:
        missing.append("report")

    scores = []
    for item in [five_tuple.theory, five_tuple.code, five_tuple.config, five_tuple.data, five_tuple.report]:
        scores.append(item.quality_score if (item and item.is_present) else 0.0)
    chain_quality = sum(scores) / len(scores) if scores else 0.0

    notes = list(scan_result.scan_notes)
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


def _first_matching_file(
    base: Path,
    root: Path,
    patterns: list[str],
    exclude_paths: Iterable[str],
    extensions: tuple[str, ...],
) -> tuple[Path, str] | None:
    """在目录内找首个匹配文件；返回 (path, content)。排序保证确定性。"""
    if not base.exists():
        return None
    candidates: list[Path] = []
    for ext in extensions:
        candidates.extend(sorted(base.rglob(f"*{ext}")))
    for fpath in sorted(set(candidates)):
        rel = _rel(root, fpath)
        if _excluded(rel, exclude_paths, root):
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if any(re.search(p, content, re.IGNORECASE) for p in patterns):
            return fpath, content
    return None


def _find_theory_evidence(cap: CapabilityDef, root: Path, exclude_paths: Iterable[str] = ()) -> EvidenceItem:
    """查找理论证据（docs/ 下的文档，且含推导/公式内容）。"""
    docs_dir = root / "docs"
    if not docs_dir.exists():
        return EvidenceItem("theory", "", "", "docs/ 目录不存在", False)

    patterns = cap.code_patterns + [cap.capability_id]
    # 逐个候选文档检查"是否真含推导/公式"，不能只看第一篇匹配文件——
    # 短模式（如 GZ/KB）会先命中无关文档，导致理论证据被静默判缺失（历史缺陷）。
    for fpath in sorted(set(docs_dir.rglob("*.md"))):
        rel = _rel(root, fpath)
        if _excluded(rel, exclude_paths, root):
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if not any(re.search(p, content, re.IGNORECASE) for p in patterns):
            continue
        if any(m in content.lower() for m in THEORY_MARKERS):
            return EvidenceItem(
                "theory", rel,
                hashlib.sha256(content.encode()).hexdigest(),
                f"理论文档: {fpath.name}", True, 0.7,
            )
    return EvidenceItem("theory", "", "", "未找到含推导内容的文档", False)


def _find_config_evidence(cap: CapabilityDef, root: Path, exclude_paths: Iterable[str] = ()) -> EvidenceItem:
    """查找配置证据（config/ 下的配置文件）。"""
    config_dir = root / "config"
    if not config_dir.exists():
        return EvidenceItem("config", "", "", "config/ 目录不存在", False)
    patterns = cap.code_patterns + [cap.capability_id]
    found = _first_matching_file(config_dir, root, patterns, exclude_paths,
                                 (".json", ".yaml", ".yml", ".toml", ".ini"))
    if found:
        fpath, content = found
        return EvidenceItem(
            "config", _rel(root, fpath),
            hashlib.sha256(content.encode()).hexdigest(),
            f"配置文件: {fpath.name}", True, 0.5,
        )
    return EvidenceItem("config", "", "", "未找到配置文件", False)


def _find_data_evidence(cap: CapabilityDef, root: Path, exclude_paths: Iterable[str] = ()) -> EvidenceItem:
    """查找数据证据（data/ 下的数据文件）。"""
    data_dir = root / "data"
    if not data_dir.exists():
        return EvidenceItem("data", "", "", "data/ 目录不存在", False)
    patterns = cap.code_patterns + [cap.capability_id]
    found = _first_matching_file(data_dir, root, patterns, exclude_paths,
                                 (".json", ".csv", ".txt", ".dat"))
    if found:
        fpath, content = found
        return EvidenceItem(
            "data", _rel(root, fpath),
            hashlib.sha256(content.encode()).hexdigest(),
            f"数据文件: {fpath.name}", True, 0.6,
        )
    return EvidenceItem("data", "", "", "未找到数据文件", False)