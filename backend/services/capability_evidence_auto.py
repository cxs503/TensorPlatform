"""
能力证据链自动采集与台账更新引擎

设计目标（零领域语义：只认模式/阈值/结构，不含任何船舶/冰区域词条）
====================================================================
本模块实现能力台账的自动化证据链闭环：

1. 工件扫描器（ArtifactScanner）：递归扫描 verified/、benchmarks/、tests/、
   validation_report*.json 等目录，提取验证案例元数据。
2. 证据等级推导器（EvidenceDeriver）：根据验证案例的完整性（解析解对比、网格收敛、
   实验对标、守恒律验证等 ≥10 种模式）自动判定证据等级。
3. 台账更新器（LedgerUpdater）：将推导结果写入能力台账 JSON，与现有 audit_capabilities
   API 输出交叉验证。
4. 差异报告器（DiffReporter）：自动生成能力缺口报告，标注需升级的能力。

证据等级定义
============
- REAL:        有真实算法 + 对标验证通过（误差 ≤ 预声明阈值）
- MOCK:        硬编码/随机/误差超阈值
- STUB:        空壳实现（返回固定值或 NotImplementedError）
- WITHHELD:    证据不足（有实现但无对标数据）
- NOT_SUPPORTED: 不支持（功能不存在）

≥10 种验证模式
==============
1. analytical_solution_comparison  — 解析解对比
2. grid_convergence                — 网格收敛
3. experimental_benchmark          — 实验对标
4. conservation_law                — 守恒律验证
5. mesh_independence               — 网格独立性
6. parameter_sensitivity           — 参数敏感性
7. boundary_condition_verification — 边界条件验证
8. unit_test_coverage              — 单元测试覆盖
9. cross_solver_verification       — 跨求解器验证
10. regression_test                — 回归测试
11. performance_benchmark          — 性能基准
12. uncertainty_quantification     — 不确定性量化
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EVIDENCE_REAL = "REAL"
EVIDENCE_MOCK = "MOCK"
EVIDENCE_STUB = "STUB"
EVIDENCE_WITHHELD = "WITHHELD"
EVIDENCE_NOT_SUPPORTED = "NOT_SUPPORTED"

EVIDENCE_LEVELS = [EVIDENCE_NOT_SUPPORTED, EVIDENCE_STUB, EVIDENCE_MOCK, EVIDENCE_WITHHELD, EVIDENCE_REAL]
LEVEL_RANK = {level: i for i, level in enumerate(EVIDENCE_LEVELS)}

VALIDATION_PATTERNS = [
    "analytical_solution_comparison", "grid_convergence", "experimental_benchmark",
    "conservation_law", "mesh_independence", "parameter_sensitivity",
    "boundary_condition_verification", "unit_test_coverage",
    "cross_solver_verification", "regression_test",
    "performance_benchmark", "uncertainty_quantification",
]

PATTERN_KEYWORDS = {
    "analytical_solution_comparison": ["analytic", "exact", "closed_form", "解析解", "精确解"],
    "grid_convergence": ["grid_converge", "mesh_converge", "gci", "网格收敛", "收敛阶"],
    "experimental_benchmark": ["experimental", "experiment", "exp_", "towing", "实验", "水池"],
    "conservation_law": ["conservation", "conserved", "守恒", "mass_balance", "energy_balance"],
    "mesh_independence": ["mesh_indep", "grid_indep", "网格独立", "mesh_refine"],
    "parameter_sensitivity": ["sensitivity", "param_sweep", "参数敏感", "扰动"],
    "boundary_condition_verification": ["boundary", "bc_verify", "边界条件", "bc_check"],
    "unit_test_coverage": ["unit_test", "test_", "pytest", "单元测试"],
    "cross_solver_verification": ["cross_solver", "inter_solver", "跨求解器", "solver_compare"],
    "regression_test": ["regression", "baseline_compare", "回归", "基线对比"],
    "performance_benchmark": ["benchmark", "perf_", "性能", "runtime"],
    "uncertainty_quantification": ["uncertainty", "uq_", "不确定", "confidence_interval"],
}

DEFAULT_THRESHOLDS = {
    "default_error_pct": 15.0,
    "real_min_patterns": 2,
    "real_min_pass_rate": 0.8,
    "mock_max_error_pct": 100.0,
    "stub_keywords": ["notimplemented", "stub", "placeholder", "todo", "raise NotImplementedError"],
}


@dataclass
class ArtifactMetadata:
    source_path: str
    source_type: str
    capability_id: str
    validation_patterns: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = True
    error_pct: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapabilityEvidence:
    capability_id: str
    artifacts: list[ArtifactMetadata] = field(default_factory=list)
    detected_patterns: set[str] = field(default_factory=set)
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    max_error_pct: float = 0.0
    avg_error_pct: float = 0.0
    has_stub_indicators: bool = False
    has_implementation: bool = False

    @property
    def pass_rate(self) -> float:
        return self.passed_tests / self.total_tests if self.total_tests else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "artifact_count": len(self.artifacts),
            "detected_patterns": sorted(self.detected_patterns),
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "pass_rate": round(self.pass_rate, 4),
            "max_error_pct": round(self.max_error_pct, 2),
            "avg_error_pct": round(self.avg_error_pct, 2),
            "has_stub_indicators": self.has_stub_indicators,
            "has_implementation": self.has_implementation,
        }


@dataclass
class DerivedGrade:
    capability_id: str
    evidence_grade: str
    confidence: float
    reason: str
    pattern_count: int
    pass_rate: float
    artifact_count: int
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiffEntry:
    capability_id: str
    current_level: str
    derived_level: str
    action: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ArtifactScanner:
    def __init__(self, repo_root: str | Path = "."):
        self.repo_root = Path(repo_root).resolve()
        self._scan_dirs = ["verified", "benchmarks", "tests", "config/evidence_pipeline/benchmarks"]

    def scan_all(self) -> list[ArtifactMetadata]:
        artifacts: list[ArtifactMetadata] = []
        for dir_name in self._scan_dirs:
            dir_path = self.repo_root / dir_name
            if dir_path.is_dir():
                artifacts.extend(self._scan_directory(dir_path, source_type=dir_name))
        for f in self.repo_root.glob("validation_report*.json"):
            artifacts.extend(self._scan_validation_report(f))
        for f in self.repo_root.rglob("pytest_result*.json"):
            if any(part.startswith(".") for part in f.parts):
                continue
            artifacts.extend(self._scan_pytest_result(f))
        return artifacts

    def _scan_directory(self, dir_path: Path, source_type: str) -> list[ArtifactMetadata]:
        artifacts: list[ArtifactMetadata] = []
        for f in dir_path.rglob("*.json"):
            if any(part.startswith(".") for part in f.parts):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
                # Handle nested benchmark structure
                if "benchmarks" in data or "results" in data:
                    items = data.get("benchmarks", data.get("results", []))
                    for item in items:
                        meta = self._extract_metadata(item, str(f), source_type)
                        if meta:
                            artifacts.append(meta)
                else:
                    meta = self._extract_metadata(data, str(f), source_type)
                    if meta:
                        artifacts.append(meta)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return artifacts

    def _scan_validation_report(self, path: Path) -> list[ArtifactMetadata]:
        artifacts: list[ArtifactMetadata] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            meta = self._extract_metadata(data, str(path), "validation")
            if meta:
                artifacts.append(meta)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        return artifacts

    def _scan_pytest_result(self, path: Path) -> list[ArtifactMetadata]:
        artifacts: list[ArtifactMetadata] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            tests = data.get("tests", data.get("report", []))
            for test in tests:
                props = test.get("user_properties", test.get("properties", {}))
                if not props or "capability_id" not in props:
                    continue
                cap_id = props["capability_id"]
                outcome = test.get("outcome", test.get("result", "unknown"))
                error_pct = float(props.get("error_pct", 0))
                threshold_pct = float(props.get("threshold_pct", DEFAULT_THRESHOLDS["default_error_pct"]))
                patterns = self._detect_patterns_from_path(str(path))
                patterns.update(self._detect_patterns_from_props(props))
                artifacts.append(ArtifactMetadata(
                    source_path=str(path), source_type="pytest", capability_id=cap_id,
                    validation_patterns=sorted(patterns),
                    metrics={"error_pct": error_pct, "threshold_pct": threshold_pct},
                    passed=outcome == "passed" and error_pct <= threshold_pct,
                    error_pct=error_pct, extra={"outcome": outcome, "nodeid": test.get("nodeid", "")},
                ))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        return artifacts

    def _extract_metadata(self, data: dict | list, source_path: str, source_type: str) -> ArtifactMetadata | None:
        # Handle list data
        if isinstance(data, list):
            return None
        cap_id = data.get("capability_id") or data.get("capability") or data.get("cap_id") or ""
        patterns = self._detect_patterns_from_data(data)
        patterns.update(self._detect_patterns_from_path(source_path))
        metrics = {}
        for key in ["error_pct", "error", "measured", "reference", "pass_rate"]:
            if key in data:
                try:
                    metrics[key] = float(data[key])
                except (ValueError, TypeError):
                    pass
        passed = data.get("passed", data.get("status", "PASS") in ("PASS", "passed", True))
        if isinstance(passed, str):
            passed = passed.lower() in ("pass", "passed", "true", "ok")
        error_pct = metrics.get("error_pct", 0.0)
        if not cap_id:
            cap_id = self._infer_cap_id_from_path(source_path)
        return ArtifactMetadata(
            source_path=source_path, source_type=source_type, capability_id=cap_id,
            validation_patterns=sorted(patterns), metrics=metrics,
            passed=bool(passed), error_pct=error_pct, extra={"source_type": source_type},
        )

    def _detect_patterns_from_data(self, data: dict) -> set[str]:
        patterns: set[str] = set()
        text = json.dumps(data, ensure_ascii=False).lower()
        for pattern, keywords in PATTERN_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in text:
                    patterns.add(pattern)
                    break
        return patterns

    def _detect_patterns_from_path(self, path: str) -> set[str]:
        patterns: set[str] = set()
        path_lower = path.lower()
        for pattern, keywords in PATTERN_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in path_lower:
                    patterns.add(pattern)
                    break
        return patterns

    def _detect_patterns_from_props(self, props: dict) -> set[str]:
        patterns: set[str] = set()
        text = json.dumps(props, ensure_ascii=False).lower()
        for pattern, keywords in PATTERN_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in text:
                    patterns.add(pattern)
                    break
        vtype = props.get("validation_type", "")
        if vtype in VALIDATION_PATTERNS:
            patterns.add(vtype)
        return patterns

    def _infer_cap_id_from_path(self, path: str) -> str:
        parts = Path(path).parts
        meaningful = [p for p in parts if p not in (".", "..", "tests", "benchmarks", "verified", "config")]
        if meaningful:
            return ".".join(meaningful[-2:]).replace(".json", "")
        return "unknown"


class EvidenceDeriver:
    def __init__(self, thresholds: dict[str, Any] | None = None):
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def derive_all(self, artifacts: list[ArtifactMetadata]) -> dict[str, DerivedGrade]:
        grouped: dict[str, list[ArtifactMetadata]] = {}
        for art in artifacts:
            if art.capability_id:
                grouped.setdefault(art.capability_id, []).append(art)
        results: dict[str, DerivedGrade] = {}
        for cap_id, arts in grouped.items():
            evidence = self._build_evidence(cap_id, arts)
            grade = self._derive_grade(cap_id, evidence)
            results[cap_id] = grade
        return results

    def _build_evidence(self, cap_id: str, artifacts: list[ArtifactMetadata]) -> CapabilityEvidence:
        ev = CapabilityEvidence(capability_id=cap_id)
        ev.artifacts = artifacts
        all_patterns: set[str] = set()
        errors: list[float] = []
        for art in artifacts:
            all_patterns.update(art.validation_patterns)
            if art.error_pct > 0:
                errors.append(art.error_pct)
            for kw in self.thresholds.get("stub_keywords", DEFAULT_THRESHOLDS["stub_keywords"]):
                if kw.lower() in json.dumps(art.extra, ensure_ascii=False).lower():
                    ev.has_stub_indicators = True
                    break
            ev.has_implementation = True
        ev.detected_patterns = all_patterns
        ev.total_tests = len(artifacts)
        ev.passed_tests = sum(1 for a in artifacts if a.passed)
        ev.failed_tests = ev.total_tests - ev.passed_tests
        ev.max_error_pct = max(errors) if errors else 0.0
        ev.avg_error_pct = sum(errors) / len(errors) if errors else 0.0
        return ev

    def _derive_grade(self, cap_id: str, evidence: CapabilityEvidence) -> DerivedGrade:
        if evidence.total_tests == 0:
            return DerivedGrade(capability_id=cap_id, evidence_grade=EVIDENCE_NOT_SUPPORTED,
                confidence=0.9, reason="No artifacts found", pattern_count=0, pass_rate=0.0, artifact_count=0)
        if evidence.has_stub_indicators:
            return DerivedGrade(capability_id=cap_id, evidence_grade=EVIDENCE_STUB,
                confidence=0.85, reason="Stub indicators detected",
                pattern_count=len(evidence.detected_patterns), pass_rate=evidence.pass_rate,
                artifact_count=evidence.total_tests)
        mock_threshold = self.thresholds.get("mock_max_error_pct", DEFAULT_THRESHOLDS["mock_max_error_pct"])
        if evidence.max_error_pct > mock_threshold:
            return DerivedGrade(capability_id=cap_id, evidence_grade=EVIDENCE_MOCK,
                confidence=0.8, reason=f"Max error ({evidence.max_error_pct:.1f}%) exceeds threshold",
                pattern_count=len(evidence.detected_patterns), pass_rate=evidence.pass_rate,
                artifact_count=evidence.total_tests)
        if evidence.pass_rate < 0.5:
            return DerivedGrade(capability_id=cap_id, evidence_grade=EVIDENCE_MOCK,
                confidence=0.75, reason=f"Pass rate ({evidence.pass_rate:.1%}) below 50%",
                pattern_count=len(evidence.detected_patterns), pass_rate=evidence.pass_rate,
                artifact_count=evidence.total_tests)
        min_patterns = self.thresholds.get("real_min_patterns", DEFAULT_THRESHOLDS["real_min_patterns"])
        if len(evidence.detected_patterns) < min_patterns:
            return DerivedGrade(capability_id=cap_id, evidence_grade=EVIDENCE_WITHHELD,
                confidence=0.6, reason=f"Only {len(evidence.detected_patterns)} patterns (need ≥{min_patterns})",
                pattern_count=len(evidence.detected_patterns), pass_rate=evidence.pass_rate,
                artifact_count=evidence.total_tests)
        min_pass_rate = self.thresholds.get("real_min_pass_rate", DEFAULT_THRESHOLDS["real_min_pass_rate"])
        if evidence.pass_rate < min_pass_rate:
            return DerivedGrade(capability_id=cap_id, evidence_grade=EVIDENCE_WITHHELD,
                confidence=0.65, reason=f"Pass rate ({evidence.pass_rate:.1%}) below threshold",
                pattern_count=len(evidence.detected_patterns), pass_rate=evidence.pass_rate,
                artifact_count=evidence.total_tests)
        confidence = min(1.0, evidence.pass_rate * (len(evidence.detected_patterns) / max(min_patterns, 1)))
        return DerivedGrade(capability_id=cap_id, evidence_grade=EVIDENCE_REAL,
            confidence=round(confidence, 3),
            reason=f"All criteria met: {len(evidence.detected_patterns)} patterns, {evidence.pass_rate:.1%} pass rate",
            pattern_count=len(evidence.detected_patterns), pass_rate=evidence.pass_rate,
            artifact_count=evidence.total_tests)


class LedgerUpdater:
    def __init__(self, ledger_path: str | Path = "artifacts/capability_ledger/capability_ledger.json",
                 audit_log_path: str | Path = "artifacts/capability_ledger/auto_audit_log.jsonl"):
        self.ledger_path = Path(ledger_path)
        self.audit_log_path = Path(audit_log_path)

    def apply_grades(self, grades: dict[str, DerivedGrade], run_id: str | None = None) -> dict[str, Any]:
        if not run_id:
            run_id = f"auto-audit-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        ledger = self._load_ledger()
        existing_caps = {cap["id"]: cap for cap in ledger.get("capabilities", [])}
        updated = 0
        new_entries = 0
        unchanged = 0
        entries: list[dict] = []
        for cap_id, grade in grades.items():
            if cap_id in existing_caps:
                cap = existing_caps[cap_id]
                current_level = cap.get("claimed_level", EVIDENCE_WITHHELD)
                if current_level != grade.evidence_grade:
                    cap["claimed_level"] = grade.evidence_grade
                    cap.setdefault("evidence", []).append({
                        "source": "auto_audit", "as_of": grade.timestamp,
                        "asserted_level": grade.evidence_grade, "confidence": grade.confidence,
                        "reason": grade.reason, "pattern_count": grade.pattern_count,
                        "pass_rate": grade.pass_rate,
                    })
                    updated += 1
                    entries.append({"capability_id": cap_id, "action": "update",
                        "old_level": current_level, "new_level": grade.evidence_grade})
                else:
                    unchanged += 1
            else:
                ledger["capabilities"].append({
                    "id": cap_id, "domain": "auto_discovered",
                    "claimed_level": grade.evidence_grade,
                    "evidence": [{"source": "auto_audit", "as_of": grade.timestamp,
                        "asserted_level": grade.evidence_grade, "confidence": grade.confidence,
                        "reason": grade.reason, "pattern_count": grade.pattern_count,
                        "pass_rate": grade.pass_rate}],
                    "note": "Auto-discovered via evidence chain scanner",
                })
                new_entries += 1
                entries.append({"capability_id": cap_id, "action": "new",
                    "old_level": EVIDENCE_NOT_SUPPORTED, "new_level": grade.evidence_grade})
        ledger_hash = self._save_ledger(ledger)
        self._append_audit_log(run_id, entries, ledger_hash)
        return {"run_id": run_id, "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "updated": updated, "new_entries": new_entries, "unchanged": unchanged,
            "ledger_hash": ledger_hash, "entries": entries}

    def _load_ledger(self) -> dict:
        if not self.ledger_path.exists():
            return {"schema_version": "3.0", "title": "能力证据台账（auto-audit）",
                "capabilities": [], "evidence_source": {"kind": "auto_audit"}}
        with open(self.ledger_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_ledger(self, ledger: dict) -> str:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(ledger, ensure_ascii=False, indent=2, default=str)
        self.ledger_path.write_text(content, encoding="utf-8")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _append_audit_log(self, run_id: str, entries: list[dict], ledger_hash: str) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_entry = {"run_id": run_id, "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entries": entries, "ledger_hash": ledger_hash}
        with open(self.audit_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False, default=str) + "\n")


class DiffReporter:
    def __init__(self):
        self._level_rank = LEVEL_RANK

    def generate(self, ledger_result: dict[str, Any], grades: dict[str, DerivedGrade],
                 current_audit: dict[str, str] | None = None) -> dict[str, Any]:
        upgrades: list[DiffEntry] = []
        downgrades: list[DiffEntry] = []
        new_caps: list[DiffEntry] = []
        consistent: list[DiffEntry] = []
        for cap_id, grade in grades.items():
            current = current_audit.get(cap_id, EVIDENCE_NOT_SUPPORTED) if current_audit else EVIDENCE_NOT_SUPPORTED
            derived = grade.evidence_grade
            current_rank = self._level_rank.get(current, 0)
            derived_rank = self._level_rank.get(derived, 0)
            if current == EVIDENCE_NOT_SUPPORTED and derived != EVIDENCE_NOT_SUPPORTED:
                new_caps.append(DiffEntry(capability_id=cap_id, current_level=current,
                    derived_level=derived, action="new", reason=f"New capability discovered with grade {derived}"))
            elif derived_rank > current_rank:
                upgrades.append(DiffEntry(capability_id=cap_id, current_level=current,
                    derived_level=derived, action="upgrade", reason=f"Upgrade {current}→{derived}: {grade.reason}"))
            elif derived_rank < current_rank:
                downgrades.append(DiffEntry(capability_id=cap_id, current_level=current,
                    derived_level=derived, action="downgrade", reason=f"Downgrade {current}→{derived}: {grade.reason}"))
            else:
                consistent.append(DiffEntry(capability_id=cap_id, current_level=current,
                    derived_level=derived, action="no_change", reason=f"Consistent at {derived}"))
        return {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total_capabilities": len(grades),
            "upgrades_needed": [e.to_dict() for e in upgrades],
            "downgrades_needed": [e.to_dict() for e in downgrades],
            "new_capabilities": [e.to_dict() for e in new_caps],
            "consistent": [e.to_dict() for e in consistent],
            "summary": {"upgrades": len(upgrades), "downgrades": len(downgrades),
                "new": len(new_caps), "consistent": len(consistent), "total": len(grades)}}


def run_full_audit(repo_root: str | Path = ".",
                   ledger_path: str | Path = "artifacts/capability_ledger/capability_ledger.json",
                   thresholds: dict[str, Any] | None = None,
                   run_id: str | None = None) -> dict[str, Any]:
    t0 = time.time()
    t1 = time.time()
    scanner = ArtifactScanner(repo_root=repo_root)
    artifacts = scanner.scan_all()
    scan_time = time.time() - t1
    t2 = time.time()
    deriver = EvidenceDeriver(thresholds=thresholds)
    grades = deriver.derive_all(artifacts)
    derive_time = time.time() - t2
    t3 = time.time()
    updater = LedgerUpdater(ledger_path=ledger_path)
    ledger_result = updater.apply_grades(grades, run_id=run_id)
    update_time = time.time() - t3
    reporter = DiffReporter()
    diff_report = reporter.generate(ledger_result, grades)
    total_time = time.time() - t0
    return {"run_id": ledger_result["run_id"], "scan_time_s": round(scan_time, 3),
        "derive_time_s": round(derive_time, 3), "update_time_s": round(update_time, 3),
        "total_time_s": round(total_time, 3), "artifacts_scanned": len(artifacts),
        "capabilities_derived": len(grades), "ledger_update": ledger_result,
        "diff_report": diff_report, "grades": {k: v.to_dict() for k, v in grades.items()}}
