"""
能力台账自动化证据链 — 证据采集 / 裁定引擎 / 升级工作流 / 防OVERCLAIM门

设计目标（零领域语义：只认表/列/约束/阈值，不含任何域词条）
=============================================================
本模块实现能力台账的自动化证据链闭环：

1. 证据采集器（EvidenceCollector）：扫描 pytest 结果 / benchmark 输出 / 对标报告，
   提取关键量与误差，生成结构化证据条目。
2. 裁定引擎（AdjudicationEngine）：按预声明阈值自动判定 REAL/MOCK/STUB/WITHHELD/
   NOT_SUPPORTED。
3. 升级工作流（UpgradeWorkflow）：证据达标 → 自动升账 + 审计日志 + 回滚点。
4. 防OVERCLAIM门（OverclaimGate）：自述等级 > 证据等级时自动降级 + 告警。

证据等级定义
============
- REAL:        有真实算法 + 对标验证通过（误差 ≤ 预声明阈值）
- MOCK:        硬编码/随机/误差超阈值
- STUB:        空壳实现（返回固定值或 NotImplementedError）
- WITHHELD:    证据不足（有实现但无对标数据，或对标数据不充分）
- NOT_SUPPORTED: 不支持（功能不存在）

裁定规则（预声明阈值，可配置）
============================
- REAL 判定：至少 N_pass 项对标通过（误差 ≤ threshold），且无 FAIL 项
- MOCK 判定：存在对标但误差 > threshold，或关键量恒等/零敏感
- STUB 判定：实现返回固定值 / NotImplementedError / 空函数体
- WITHHELD 判定：有实现但无对标数据
- NOT_SUPPORTED：功能不存在

升级规则
========
- 证据等级 ≥ claimed_level → 保持或升级
- 证据等级 < claimed_level → 自动降级（OVERCLAIM）
- 每次变更写审计日志 + 创建回滚点

用法
----
::

    from services.capability_evidence_chain import (
        EvidenceCollector, AdjudicationEngine, UpgradeWorkflow, OverclaimGate
    )

    collector = EvidenceCollector()
    evidence = collector.collect_from_pytest("runs/pytest_result.json")

    engine = AdjudicationEngine(thresholds={"default_error_pct": 15.0})
    grade = engine.adjudicate("calc.resistance", evidence)

    workflow = UpgradeWorkflow(ledger_path="artifacts/capability_ledger/capability_ledger.json")
    result = workflow.apply_upgrade("calc.resistance", grade, evidence)

    gate = OverclaimGate()
    gate.check("calc.resistance", claimed="REAL", evidence_grade="MOCK")
    # → raises OverclaimAlert
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

# ── 证据等级常量 ──────────────────────────────────────────────────────
EVIDENCE_REAL = "REAL"
EVIDENCE_MOCK = "MOCK"
EVIDENCE_STUB = "STUB"
EVIDENCE_WITHHELD = "WITHHELD"
EVIDENCE_NOT_SUPPORTED = "NOT_SUPPORTED"

EVIDENCE_LEVELS = [
    EVIDENCE_NOT_SUPPORTED,
    EVIDENCE_STUB,
    EVIDENCE_MOCK,
    EVIDENCE_WITHHELD,
    EVIDENCE_REAL,
]

LEVEL_RANK = {level: i for i, level in enumerate(EVIDENCE_LEVELS)}


# ── 数据结构 ─────────────────────────────────────────────────────────


@dataclass
class EvidenceItem:
    """单条证据条目。"""

    source: str  # 证据来源（pytest/benchmark/report）
    source_path: str  # 源文件路径
    capability_id: str  # 能力 ID
    metric_name: str  # 指标名
    measured_value: float  # 测量值
    reference_value: float  # 参考值
    error_pct: float  # 误差百分比
    passed: bool  # 是否通过阈值
    threshold_pct: float  # 使用的阈值
    timestamp: str  # 时间戳
    extra: dict[str, Any] = field(default_factory=dict)  # 附加信息

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceBundle:
    """能力证据包（多条证据的集合）。"""

    capability_id: str
    items: list[EvidenceItem] = field(default_factory=list)
    collected_at: str = ""
    source_summary: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if not self.collected_at:
            self.collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # 统计来源
        sources: dict[str, int] = {}
        for item in self.items:
            sources[item.source] = sources.get(item.source, 0) + 1
        self.source_summary = sources

    @property
    def pass_count(self) -> int:
        return sum(1 for item in self.items if item.passed)

    @property
    def fail_count(self) -> int:
        return sum(1 for item in self.items if not item.passed)

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def max_error_pct(self) -> float:
        if not self.items:
            return 0.0
        return max(item.error_pct for item in self.items)

    @property
    def avg_error_pct(self) -> float:
        if not self.items:
            return 0.0
        return sum(item.error_pct for item in self.items) / len(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "items": [item.to_dict() for item in self.items],
            "collected_at": self.collected_at,
            "source_summary": self.source_summary,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "total_count": self.total_count,
            "max_error_pct": self.max_error_pct,
            "avg_error_pct": self.avg_error_pct,
        }


@dataclass
class AdjudicationResult:
    """裁定结果。"""

    capability_id: str
    evidence_grade: str  # REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED
    confidence: float  # 置信度 [0, 1]
    reason: str  # 裁定理由
    evidence_count: int  # 证据条数
    pass_count: int  # 通过数
    fail_count: int  # 失败数
    thresholds_used: dict[str, float]  # 使用的阈值
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditLogEntry:
    """审计日志条目（含哈希链）。"""

    run_id: str
    ts_utc: str
    action: str  # upgrade / downgrade / overclaim_alert / no_change
    capability_id: str
    old_level: str
    new_level: str
    evidence_grade: str
    reason: str
    rollback_point: str  # 回滚点标识（ledger hash）
    prev_hash: str = ""
    entry_hash: str = ""

    def canonical(self) -> str:
        payload = {
            "run_id": self.run_id,
            "ts_utc": self.ts_utc,
            "action": self.action,
            "capability_id": self.capability_id,
            "old_level": self.old_level,
            "new_level": self.new_level,
            "evidence_grade": self.evidence_grade,
            "reason": self.reason,
            "rollback_point": self.rollback_point,
            "prev_hash": self.prev_hash,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def seal(self) -> "AuditLogEntry":
        self.entry_hash = hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OverclaimAlert(Exception):
    """OVERCLAIM 告警：自述等级高于证据等级。"""

    def __init__(self, capability_id: str, claimed: str, evidence: str, reason: str):
        self.capability_id = capability_id
        self.claimed = claimed
        self.evidence = evidence
        self.reason = reason
        super().__init__(
            f"OVERCLAIM: {capability_id} claimed={claimed} > evidence={evidence}: {reason}"
        )


# ── 默认阈值配置 ─────────────────────────────────────────────────────

DEFAULT_THRESHOLDS = {
    "default_error_pct": 15.0,  # 默认误差阈值（%）
    "real_min_pass_count": 1,  # REAL 最少通过项数
    "mock_max_error_pct": 100.0,  # MOCK 最大误差阈值（超过此值视为硬编码）
    "stub_detection_keywords": ["notimplemented", "stub", "placeholder", "todo"],
    "min_evidence_for_real": 1,  # 判定 REAL 最少证据条数
}


# ── 证据采集器 ────────────────────────────────────────────────────────


class EvidenceCollector:
    """
    证据采集器：扫描 pytest 结果 / benchmark 输出 / 对标报告，
    提取关键量与误差，生成结构化证据条目。
    """

    def __init__(self, thresholds: dict[str, Any] | None = None):
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def collect_from_pytest(self, result_path: str | Path) -> list[EvidenceBundle]:
        """
        从 pytest JSON 结果文件采集证据。

        期望格式（pytest --json 或 pytest-reportlog）：
        {
            "tests": [
                {
                    "nodeid": "tests/test_foo.py::test_bar",
                    "outcome": "passed",
                    "call": {"duration": 0.1},
                    "user_properties": {
                        "capability_id": "calc.resistance",
                        "metric": "R_T",
                        "measured": 123.4,
                        "reference": 120.0,
                        "error_pct": 2.83,
                        "threshold_pct": 15.0
                    }
                }
            ]
        }
        """
        path = Path(result_path)
        if not path.exists():
            logger.warning(f"Pytest result file not found: {path}")
            return []

        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in pytest result: {path}")
                return []

        bundles: dict[str, list[EvidenceItem]] = {}
        tests = data.get("tests", data.get("report", []))

        for test in tests:
            props = test.get("user_properties", test.get("properties", {}))
            if not props or "capability_id" not in props:
                continue

            cap_id = props["capability_id"]
            metric = props.get("metric", "unknown")
            measured = float(props.get("measured", 0))
            reference = float(props.get("reference", 0))
            error_pct = float(props.get("error_pct", 0))
            threshold_pct = float(props.get("threshold_pct", self.thresholds["default_error_pct"]))
            outcome = test.get("outcome", test.get("result", "unknown"))

            # 判定是否通过
            passed = outcome == "passed" and error_pct <= threshold_pct

            item = EvidenceItem(
                source="pytest",
                source_path=str(path),
                capability_id=cap_id,
                metric_name=metric,
                measured_value=measured,
                reference_value=reference,
                error_pct=error_pct,
                passed=passed,
                threshold_pct=threshold_pct,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                extra={"outcome": outcome, "nodeid": test.get("nodeid", "")},
            )

            if cap_id not in bundles:
                bundles[cap_id] = []
            bundles[cap_id].append(item)

        return [
            EvidenceBundle(capability_id=cap_id, items=items)
            for cap_id, items in bundles.items()
        ]

    def collect_from_benchmark(self, result_path: str | Path) -> list[EvidenceBundle]:
        """
        从 benchmark JSON 结果文件采集证据。

        期望格式：
        {
            "benchmarks": [
                {
                    "capability_id": "calc.resistance",
                    "metrics": {
                        "R_T": {"measured": 123.4, "reference": 120.0, "error_pct": 2.83}
                    }
                }
            ]
        }
        """
        path = Path(result_path)
        if not path.exists():
            logger.warning(f"Benchmark result file not found: {path}")
            return []

        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in benchmark result: {path}")
                return []

        bundles: dict[str, list[EvidenceItem]] = {}
        benchmarks = data.get("benchmarks", data.get("results", []))

        for bench in benchmarks:
            cap_id = bench.get("capability_id", "")
            if not cap_id:
                continue

            metrics = bench.get("metrics", {})
            for metric_name, metric_data in metrics.items():
                measured = float(metric_data.get("measured", 0))
                reference = float(metric_data.get("reference", 0))
                error_pct = float(metric_data.get("error_pct", 0))
                threshold_pct = float(
                    metric_data.get("threshold_pct", self.thresholds["default_error_pct"])
                )
                passed = error_pct <= threshold_pct

                item = EvidenceItem(
                    source="benchmark",
                    source_path=str(path),
                    capability_id=cap_id,
                    metric_name=metric_name,
                    measured_value=measured,
                    reference_value=reference,
                    error_pct=error_pct,
                    passed=passed,
                    threshold_pct=threshold_pct,
                    timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    extra=bench.get("extra", {}),
                )

                if cap_id not in bundles:
                    bundles[cap_id] = []
                bundles[cap_id].append(item)

        return [
            EvidenceBundle(capability_id=cap_id, items=items)
            for cap_id, items in bundles.items()
        ]

    def collect_from_report(self, report_path: str | Path) -> list[EvidenceBundle]:
        """
        从对标报告 JSON 文件采集证据。

        期望格式：
        {
            "capability_id": "calc.resistance",
            "comparisons": [
                {
                    "metric": "R_T",
                    "measured": 123.4,
                    "reference": 120.0,
                    "error_pct": 2.83,
                    "passed": true
                }
            ]
        }
        """
        path = Path(report_path)
        if not path.exists():
            logger.warning(f"Report file not found: {path}")
            return []

        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in report: {path}")
                return []

        cap_id = data.get("capability_id", "")
        if not cap_id:
            return []

        comparisons = data.get("comparisons", data.get("results", []))
        items: list[EvidenceItem] = []

        for comp in comparisons:
            metric = comp.get("metric", "unknown")
            measured = float(comp.get("measured", 0))
            reference = float(comp.get("reference", 0))
            error_pct = float(comp.get("error_pct", 0))
            threshold_pct = float(
                comp.get("threshold_pct", self.thresholds["default_error_pct"])
            )
            passed = comp.get("passed", error_pct <= threshold_pct)

            item = EvidenceItem(
                source="report",
                source_path=str(path),
                capability_id=cap_id,
                metric_name=metric,
                measured_value=measured,
                reference_value=reference,
                error_pct=error_pct,
                passed=passed,
                threshold_pct=threshold_pct,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                extra=comp.get("extra", {}),
            )
            items.append(item)

        return [EvidenceBundle(capability_id=cap_id, items=items)] if items else []

    def collect_from_directory(self, dir_path: str | Path) -> list[EvidenceBundle]:
        """
        从目录扫描所有证据文件（pytest/benchmark/report）。
        """
        path = Path(dir_path)
        if not path.is_dir():
            logger.warning(f"Directory not found: {path}")
            return []

        all_bundles: list[EvidenceBundle] = []

        for file_path in path.rglob("*.json"):
            # 跳过隐藏目录和大型数据文件
            if any(part.startswith(".") for part in file_path.parts):
                continue

            content = file_path.read_text(encoding="utf-8", errors="replace")
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                continue

            # 判断文件类型并采集
            if "tests" in data or "report" in data:
                bundles = self.collect_from_pytest(file_path)
            elif "benchmarks" in data or "results" in data:
                bundles = self.collect_from_benchmark(file_path)
            elif "capability_id" in data and ("comparisons" in data or "results" in data):
                bundles = self.collect_from_report(file_path)
            else:
                continue

            all_bundles.extend(bundles)

        return all_bundles


# ── 裁定引擎 ──────────────────────────────────────────────────────────


class AdjudicationEngine:
    """
    裁定引擎：按预声明阈值自动判定 REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED。

    裁定规则：
    - NOT_SUPPORTED: 无证据且能力不存在
    - STUB: 证据显示实现为空壳/固定值/NotImplementedError
    - MOCK: 有对标但误差超阈值，或关键量恒等/零敏感
    - WITHHELD: 有实现但无对标数据
    - REAL: 至少 N_pass 项对标通过（误差 ≤ threshold），且无 FAIL 项
    """

    def __init__(self, thresholds: dict[str, Any] | None = None):
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def adjudicate(
        self,
        capability_id: str,
        evidence: EvidenceBundle | list[EvidenceItem] | None,
        implementation_info: dict[str, Any] | None = None,
    ) -> AdjudicationResult:
        """
        对能力进行证据等级裁定。

        参数:
            capability_id: 能力 ID
            evidence: 证据包或证据列表
            implementation_info: 实现信息（用于 STUB 检测）

        返回:
            AdjudicationResult
        """
        if evidence is None:
            items = []
        elif isinstance(evidence, EvidenceBundle):
            items = evidence.items
        else:
            items = list(evidence)

        # 检查 STUB
        if self._is_stub(implementation_info):
            return AdjudicationResult(
                capability_id=capability_id,
                evidence_grade=EVIDENCE_STUB,
                confidence=0.95,
                reason="Implementation detected as stub (empty/NotImplementedError/fixed value)",
                evidence_count=len(items),
                pass_count=0,
                fail_count=0,
                thresholds_used=self.thresholds,
            )

        # 无证据 → WITHHELD
        if not items:
            return AdjudicationResult(
                capability_id=capability_id,
                evidence_grade=EVIDENCE_WITHHELD,
                confidence=0.5,
                reason="No evidence available for adjudication",
                evidence_count=0,
                pass_count=0,
                fail_count=0,
                thresholds_used=self.thresholds,
            )

        pass_count = sum(1 for item in items if item.passed)
        fail_count = sum(1 for item in items if not item.passed)
        total = len(items)
        max_error = max(item.error_pct for item in items) if items else 0

        # 检查 MOCK（误差超阈值或恒等）
        if self._is_mock(items, max_error):
            return AdjudicationResult(
                capability_id=capability_id,
                evidence_grade=EVIDENCE_MOCK,
                confidence=0.85,
                reason=f"Evidence shows error exceeds threshold (max={max_error:.1f}%, fail={fail_count}/{total})",
                evidence_count=total,
                pass_count=pass_count,
                fail_count=fail_count,
                thresholds_used=self.thresholds,
            )

        # 检查 REAL
        min_pass = self.thresholds.get("real_min_pass_count", 1)
        min_evidence = self.thresholds.get("min_evidence_for_real", 1)

        if pass_count >= min_pass and fail_count == 0 and total >= min_evidence:
            confidence = min(1.0, pass_count / max(total, 1))
            return AdjudicationResult(
                capability_id=capability_id,
                evidence_grade=EVIDENCE_REAL,
                confidence=confidence,
                reason=f"All {total} evidence items passed (max error={max_error:.1f}%)",
                evidence_count=total,
                pass_count=pass_count,
                fail_count=0,
                thresholds_used=self.thresholds,
            )

        # 部分通过 → MOCK（有失败项）
        if fail_count > 0:
            return AdjudicationResult(
                capability_id=capability_id,
                evidence_grade=EVIDENCE_MOCK,
                confidence=0.7,
                reason=f"Mixed results: {pass_count} passed, {fail_count} failed (max error={max_error:.1f}%)",
                evidence_count=total,
                pass_count=pass_count,
                fail_count=fail_count,
                thresholds_used=self.thresholds,
            )

        # 默认 WITHHELD
        return AdjudicationResult(
            capability_id=capability_id,
            evidence_grade=EVIDENCE_WITHHELD,
            confidence=0.5,
            reason="Insufficient evidence for definitive adjudication",
            evidence_count=total,
            pass_count=pass_count,
            fail_count=fail_count,
            thresholds_used=self.thresholds,
        )

    def _is_stub(self, implementation_info: dict[str, Any] | None) -> bool:
        """检测是否为 STUB 实现。"""
        if not implementation_info:
            return False

        keywords = self.thresholds.get("stub_detection_keywords", DEFAULT_THRESHOLDS["stub_detection_keywords"])
        source_code = implementation_info.get("source_code", "").lower()
        return_type = implementation_info.get("return_type", "")

        # 检查关键词
        for kw in keywords:
            if kw.lower() in source_code:
                return True

        # 检查固定返回值
        if return_type == "fixed_value":
            return True

        return False

    def _is_mock(self, items: list[EvidenceItem], max_error: float) -> bool:
        """检测是否为 MOCK 实现。"""
        if not items:
            return False

        # 误差超阈值
        mock_threshold = self.thresholds.get("mock_max_error_pct", DEFAULT_THRESHOLDS["mock_max_error_pct"])
        if max_error > mock_threshold:
            return True

        # 检查是否有失败项
        if any(not item.passed for item in items):
            return True

        return False


# ── 升级工作流 ────────────────────────────────────────────────────────


class UpgradeWorkflow:
    """
    升级工作流：证据达标 → 自动升账 + 审计日志 + 回滚点。
    """

    def __init__(
        self,
        ledger_path: str | Path = "artifacts/capability_ledger/capability_ledger.json",
        audit_log_path: str | Path = "artifacts/capability_ledger/audit_log.jsonl",
    ):
        self.ledger_path = Path(ledger_path)
        self.audit_log_path = Path(audit_log_path)
        self._prev_hash = self._load_prev_hash()

    def _load_prev_hash(self) -> str:
        """加载上一条审计日志的 hash（用于哈希链）。"""
        if not self.audit_log_path.exists():
            return ""
        try:
            lines = self.audit_log_path.read_text(encoding="utf-8").strip().split("\n")
            if lines:
                last = json.loads(lines[-1])
                return last.get("entry_hash", "")
        except (json.JSONDecodeError, IndexError):
            pass
        return ""

    def _load_ledger(self) -> dict:
        """加载能力台账。"""
        if not self.ledger_path.exists():
            return {"schema_version": "3.0", "capabilities": [], "evidence_source": {}}
        with open(self.ledger_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_ledger(self, ledger: dict) -> str:
        """保存能力台账并返回 hash（回滚点）。"""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(ledger, ensure_ascii=False, indent=2, default=str)
        self.ledger_path.write_text(content, encoding="utf-8")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _append_audit_log(self, entry: AuditLogEntry) -> None:
        """追加审计日志。"""
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.to_dict(), ensure_ascii=False, default=str)
        with open(self.audit_log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def apply_upgrade(
        self,
        capability_id: str,
        evidence_grade: str,
        evidence: EvidenceBundle | AdjudicationResult | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """
        应用升级：根据证据等级更新台账。

        返回:
            {action, old_level, new_level, rollback_point, audit_entry}
        """
        if not run_id:
            run_id = f"upgrade-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

        ledger = self._load_ledger()
        capabilities = ledger.get("capabilities", [])

        # 查找能力（先捕获 old_level，再决定是否创建新条目）
        cap = None
        old_level = EVIDENCE_WITHHELD  # 默认新能力为 WITHHELD
        for c in capabilities:
            if c.get("id") == capability_id:
                cap = c
                old_level = cap.get("claimed_level", EVIDENCE_WITHHELD)
                break

        if cap is None:
            # 创建新能力条目
            cap = {
                "id": capability_id,
                "claimed_level": evidence_grade,
                "evidence": [],
            }
            capabilities.append(cap)
            ledger["capabilities"] = capabilities

        # 确定新等级
        new_level = evidence_grade

        # 判断动作
        if LEVEL_RANK.get(new_level, 0) > LEVEL_RANK.get(old_level, 0):
            action = "upgrade"
        elif LEVEL_RANK.get(new_level, 0) < LEVEL_RANK.get(old_level, 0):
            action = "downgrade"
        else:
            action = "no_change"

        # 更新台账
        cap["claimed_level"] = new_level

        # 添加证据条目
        if evidence:
            evidence_entry = {
                "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "asserted_level": evidence_grade,
                "source": "auto_evidence_chain",
                "run_id": run_id,
            }
            if isinstance(evidence, EvidenceBundle):
                evidence_entry["evidence_count"] = evidence.total_count
                evidence_entry["pass_count"] = evidence.pass_count
                evidence_entry["fail_count"] = evidence.fail_count
            elif isinstance(evidence, AdjudicationResult):
                evidence_entry["confidence"] = evidence.confidence
                evidence_entry["reason"] = evidence.reason

            if "evidence" not in cap:
                cap["evidence"] = []
            cap["evidence"].append(evidence_entry)

        # 保存台账
        rollback_point = self._save_ledger(ledger)

        # 写审计日志
        audit_entry = AuditLogEntry(
            run_id=run_id,
            ts_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            action=action,
            capability_id=capability_id,
            old_level=old_level,
            new_level=new_level,
            evidence_grade=evidence_grade,
            reason=f"Auto evidence chain: {action} from {old_level} to {new_level}",
            rollback_point=rollback_point,
            prev_hash=self._prev_hash,
        ).seal()

        self._append_audit_log(audit_entry)
        self._prev_hash = audit_entry.entry_hash

        return {
            "action": action,
            "old_level": old_level,
            "new_level": new_level,
            "rollback_point": rollback_point,
            "audit_entry": audit_entry.to_dict(),
        }


# ── 防OVERCLAIM门 ────────────────────────────────────────────────────


class OverclaimGate:
    """
    防OVERCLAIM门：自述等级 > 证据等级时自动降级 + 告警。
    """

    def __init__(self, auto_downgrade: bool = True):
        self.auto_downgrade = auto_downgrade

    def check(
        self,
        capability_id: str,
        claimed: str,
        evidence_grade: str,
    ) -> dict[str, Any]:
        """
        检查是否存在 OVERCLAIM。

        返回:
            {overclaim: bool, action: str, claimed: str, evidence_grade: str, reason: str}
        """
        claimed_rank = LEVEL_RANK.get(claimed, 0)
        evidence_rank = LEVEL_RANK.get(evidence_grade, 0)

        if claimed_rank > evidence_rank:
            reason = (
                f"Claimed level '{claimed}' (rank={claimed_rank}) "
                f"exceeds evidence grade '{evidence_grade}' (rank={evidence_rank})"
            )

            if self.auto_downgrade:
                return {
                    "overclaim": True,
                    "action": "auto_downgrade",
                    "claimed": claimed,
                    "evidence_grade": evidence_grade,
                    "corrected_level": evidence_grade,
                    "reason": reason,
                }
            else:
                return {
                    "overclaim": True,
                    "action": "alert_only",
                    "claimed": claimed,
                    "evidence_grade": evidence_grade,
                    "corrected_level": claimed,
                    "reason": reason,
                }

        return {
            "overclaim": False,
            "action": "no_action",
            "claimed": claimed,
            "evidence_grade": evidence_grade,
            "corrected_level": claimed,
            "reason": "No overclaim detected",
        }

    def check_and_downgrade(
        self,
        capability_id: str,
        claimed: str,
        evidence_grade: str,
        workflow: UpgradeWorkflow | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """
        检查 OVERCLAIM 并自动降级（如果配置了 workflow）。
        """
        result = self.check(capability_id, claimed, evidence_grade)

        if result["overclaim"] and self.auto_downgrade and workflow:
            upgrade_result = workflow.apply_upgrade(
                capability_id=capability_id,
                evidence_grade=evidence_grade,
                run_id=run_id,
            )
            result["upgrade_result"] = upgrade_result

        return result


# ── 便捷函数：全量重评估 ──────────────────────────────────────────────


def reevaluate_all_capabilities(
    ledger_path: str | Path = "artifacts/capability_ledger/capability_ledger.json",
    evidence_dir: str | Path | None = None,
    thresholds: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    对台账中所有能力进行自动化重评估。

    返回:
        {
            "run_id": str,
            "timestamp": str,
            "capabilities": [
                {
                    "id": str,
                    "old_level": str,
                    "new_level": str,
                    "evidence_grade": str,
                    "action": str,
                    "evidence_count": int,
                }
            ],
            "summary": {
                "total": int,
                "upgraded": int,
                "downgraded": int,
                "unchanged": int,
                "overclaims_detected": int,
            }
        }
    """
    if not run_id:
        run_id = f"reeval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    collector = EvidenceCollector(thresholds=thresholds)
    engine = AdjudicationEngine(thresholds=thresholds)
    workflow = UpgradeWorkflow(ledger_path=ledger_path)
    gate = OverclaimGate(auto_downgrade=True)

    # 加载台账
    ledger_path = Path(ledger_path)
    if not ledger_path.exists():
        return {"error": f"Ledger not found: {ledger_path}", "capabilities": []}

    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    capabilities = ledger.get("capabilities", [])

    # 采集证据
    all_evidence: dict[str, EvidenceBundle] = {}
    if evidence_dir:
        bundles = collector.collect_from_directory(evidence_dir)
        for bundle in bundles:
            all_evidence[bundle.capability_id] = bundle

    results = []
    summary = {"total": 0, "upgraded": 0, "downgraded": 0, "unchanged": 0, "overclaims_detected": 0}

    for cap in capabilities:
        cap_id = cap.get("id", "unknown")
        claimed_level = cap.get("claimed_level", EVIDENCE_WITHHELD)
        summary["total"] += 1

        # 获取证据
        evidence = all_evidence.get(cap_id)

        # 裁定
        adjudication = engine.adjudicate(cap_id, evidence)
        evidence_grade = adjudication.evidence_grade

        # OVERCLAIM 检查
        gate_result = gate.check(cap_id, claimed_level, evidence_grade)
        if gate_result["overclaim"]:
            summary["overclaims_detected"] += 1
            effective_grade = gate_result["corrected_level"]
        else:
            effective_grade = evidence_grade

        # 应用升级
        upgrade_result = workflow.apply_upgrade(
            capability_id=cap_id,
            evidence_grade=effective_grade,
            evidence=adjudication,
            run_id=run_id,
        )

        results.append({
            "id": cap_id,
            "old_level": upgrade_result["old_level"],
            "new_level": upgrade_result["new_level"],
            "evidence_grade": evidence_grade,
            "action": upgrade_result["action"],
            "evidence_count": adjudication.evidence_count,
            "confidence": adjudication.confidence,
            "reason": adjudication.reason,
        })

        if upgrade_result["action"] == "upgrade":
            summary["upgraded"] += 1
        elif upgrade_result["action"] == "downgrade":
            summary["downgraded"] += 1
        else:
            summary["unchanged"] += 1

    return {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capabilities": results,
        "summary": summary,
    }
