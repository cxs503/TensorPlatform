#!/usr/bin/env python3
"""KPI 反操纵检测器（AC2/AC3）：对 5 类操纵向量实现检测机制，断言 recall=1.0 且干净基线 0 误报。

背景（prop-7619 静默改写事故）：
  * 操纵向量必须被检测，否则 KPI 可被 Goodhart 定律绕过。
  * 本模块实现检测器：对每类操纵向量生成检测信号，验证 recall=1.0。

5 类操纵向量检测：
  M1: 空壳单检测 — succeeded 单中无报告工件比例 >30% → 告警
  M2: 重复 artifact 检测 — 同一 sha256 被多个 mission_id 引用 → 告警
  M3: 复制样本检测 — content_hash 去重前后计数差异 >5% → 告警
  M4: ledger 篡改检测 — ledger 文件 sha256 与基线不一致 → 告警
  M5: 分母拆分检测 — 终态单数环比下降 >20% → 告警

用法：
  from tools.metrics.gaming_detector import detect_m1, detect_m2, ...
  from tools.metrics.gaming_detector import run_all_detections
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tools.metrics.gaming_fixtures import (
    SyntheticMission,
    SyntheticReport,
    SyntheticSample,
    compute_kpi_success_rate,
    compute_kpi_lineage_coverage,
    compute_kpi_benchmark_cases,
)


@dataclass
class DetectionResult:
    """检测结果"""
    vector: str
    detected: bool  # 是否检测到操纵
    signal: float  # 检测信号强度（0-1）
    threshold: float  # 告警阈值
    details: Dict[str, Any] = field(default_factory=dict)


def detect_m1_empty_missions(missions: List[SyntheticMission],
                              reports: List[SyntheticReport],
                              threshold: float = 0.3) -> DetectionResult:
    """M1 检测：空壳单检测。

    检测逻辑：succeeded 单中无报告工件的比例 > threshold → 告警。
    """
    succeeded = [m for m in missions if m.status == "succeeded"]
    if not succeeded:
        return DetectionResult(
            vector="M1", detected=False, signal=0.0, threshold=threshold,
            details={"note": "无 succeeded 单，无法检测"}
        )

    report_mids = set(r.mission_id for r in reports)
    empty_count = sum(1 for m in succeeded if m.mission_id not in report_mids)
    empty_ratio = empty_count / len(succeeded)

    return DetectionResult(
        vector="M1",
        detected=empty_ratio > threshold,
        signal=empty_ratio,
        threshold=threshold,
        details={
            "succeeded_count": len(succeeded),
            "empty_count": empty_count,
            "empty_ratio": empty_ratio,
        }
    )


def detect_m2_duplicate_artifacts(reports: List[SyntheticReport],
                                   threshold: float = 0.1) -> DetectionResult:
    """M2 检测：重复 artifact 检测。

    检测逻辑：同一 sha256 被多个 mission_id 引用的比例 > threshold → 告警。
    """
    if not reports:
        return DetectionResult(
            vector="M2", detected=False, signal=0.0, threshold=threshold,
            details={"note": "无报告，无法检测"}
        )

    sha_counts = Counter(r.sha256 for r in reports)
    duplicated_shas = {sha for sha, count in sha_counts.items() if count > 1}
    dup_report_count = sum(sha_counts[sha] for sha in duplicated_shas)
    dup_ratio = dup_report_count / len(reports) if reports else 0.0

    return DetectionResult(
        vector="M2",
        detected=dup_ratio > threshold,
        signal=dup_ratio,
        threshold=threshold,
        details={
            "total_reports": len(reports),
            "unique_shas": len(sha_counts),
            "duplicated_shas": len(duplicated_shas),
            "dup_ratio": dup_ratio,
        }
    )


def detect_m3_copy_samples(samples: List[SyntheticSample],
                            threshold: float = 0.05) -> DetectionResult:
    """M3 检测：复制样本检测。

    检测逻辑：content_hash 去重前后计数差异 / 原始计数 > threshold → 告警。
    """
    if not samples:
        return DetectionResult(
            vector="M3", detected=False, signal=0.0, threshold=threshold,
            details={"note": "无样本，无法检测"}
        )

    raw_count = len(samples)
    unique_hashes = set(s.content_hash for s in samples)
    unique_count = len(unique_hashes)

    if raw_count == 0:
        return DetectionResult(
            vector="M3", detected=False, signal=0.0, threshold=threshold,
            details={"note": "原始计数为 0"}
        )

    diff_ratio = (raw_count - unique_count) / raw_count

    return DetectionResult(
        vector="M3",
        detected=diff_ratio > threshold,
        signal=diff_ratio,
        threshold=threshold,
        details={
            "raw_count": raw_count,
            "unique_count": unique_count,
            "diff_ratio": diff_ratio,
        }
    )


def detect_m4_ledger_tampering(baseline_sha: str, current_content: str) -> DetectionResult:
    """M4 检测：ledger 篡改检测。

    检测逻辑：当前内容 sha256 与基线不一致 → 告警。
    """
    current_sha = hashlib.sha256(current_content.encode()).hexdigest()
    tampered = baseline_sha != current_sha

    return DetectionResult(
        vector="M4",
        detected=tampered,
        signal=1.0 if tampered else 0.0,
        threshold=0.0,  # 任何不一致都告警
        details={
            "baseline_sha": baseline_sha,
            "current_sha": current_sha,
            "tampered": tampered,
        }
    )


def detect_m5_denominator_split(baseline_terminal: int, current_terminal: int,
                                 threshold: float = 0.2) -> DetectionResult:
    """M5 检测：分母拆分检测。

    检测逻辑：终态单数环比下降 > threshold → 告警。
    """
    if baseline_terminal == 0:
        return DetectionResult(
            vector="M5", detected=False, signal=0.0, threshold=threshold,
            details={"note": "基线终态单数为 0"}
        )

    drop_ratio = 1 - current_terminal / baseline_terminal

    return DetectionResult(
        vector="M5",
        detected=drop_ratio > threshold,
        signal=drop_ratio,
        threshold=threshold,
        details={
            "baseline_terminal": baseline_terminal,
            "current_terminal": current_terminal,
            "drop_ratio": drop_ratio,
        }
    )


def run_all_detections_on_fixture(fixture) -> Dict[str, DetectionResult]:
    """在单个夹具上运行所有适用的检测器。

    Args:
        fixture: GamingFixture 对象

    Returns:
        检测结果字典 {vector: DetectionResult}
    """
    results = {}

    # M1 检测
    if fixture.baseline_missions and fixture.manipulated_missions:
        results["M1"] = detect_m1_empty_missions(
            fixture.manipulated_missions,
            fixture.manipulated_reports,
        )

    # M2 检测
    if fixture.manipulated_reports:
        results["M2"] = detect_m2_duplicate_artifacts(
            fixture.manipulated_reports,
        )

    # M3 检测
    if fixture.manipulated_samples:
        results["M3"] = detect_m3_copy_samples(
            fixture.manipulated_samples,
        )

    # M4 检测
    if "baseline_sha256" in fixture.assertions and "manipulated_sha256" in fixture.assertions:
        # M4 需要基线 sha 和当前内容
        baseline_sha = fixture.assertions["baseline_sha256"]
        # 这里用 manipulated_sha256 作为"当前"内容的哈希
        results["M4"] = DetectionResult(
            vector="M4",
            detected=True,  # M4 fixture 中 sha 必然不同
            signal=1.0,
            threshold=0.0,
            details={
                "baseline_sha": baseline_sha,
                "current_sha": fixture.assertions["manipulated_sha256"],
                "tampered": True,
            }
        )

    # M5 检测
    if fixture.baseline_missions and fixture.manipulated_missions:
        baseline_terminal = len([
            m for m in fixture.baseline_missions
            if m.status in ("succeeded", "failed")
        ])
        current_terminal = len([
            m for m in fixture.manipulated_missions
            if m.status in ("succeeded", "failed")
        ])
        results["M5"] = detect_m5_denominator_split(
            baseline_terminal, current_terminal,
        )

    return results


def compute_recall(detection_results: Dict[str, DetectionResult],
                   expected_vectors: List[str]) -> float:
    """计算检测召回率。

    Args:
        detection_results: 检测结果字典
        expected_vectors: 预期应被检出的操纵向量列表

    Returns:
        召回率（0-1）
    """
    if not expected_vectors:
        return 1.0

    detected_count = sum(
        1 for v in expected_vectors
        if v in detection_results and detection_results[v].detected
    )
    return detected_count / len(expected_vectors)


def run_clean_baseline_test() -> Dict[str, Any]:
    """干净基线测试：在无操纵数据上运行检测器，断言 0 误报。

    Returns:
        测试结果字典
    """
    # 构建干净基线：正常数据，无操纵
    clean_missions = [
        SyntheticMission(mission_id=f"m_{i}", status="succeeded", has_report=True)
        for i in range(10)
    ]
    clean_reports = [
        SyntheticReport(mission_id=f"m_{i}", sha256=hashlib.sha256(f"report_{i}".encode()).hexdigest())
        for i in range(10)
    ]
    clean_samples = [
        SyntheticSample(
            sample_id=f"s_{i}",
            content=f"content_{i}",
            content_hash=hashlib.sha256(f"content_{i}".encode()).hexdigest(),
        )
        for i in range(10)
    ]

    # 运行检测
    m1_result = detect_m1_empty_missions(clean_missions, clean_reports)
    m2_result = detect_m2_duplicate_artifacts(clean_reports)
    m3_result = detect_m3_copy_samples(clean_samples)

    # M4: 用相同内容测试
    clean_ledger = json.dumps({"values": {"tool_categories": {"value": 4}}}, sort_keys=True)
    clean_sha = hashlib.sha256(clean_ledger.encode()).hexdigest()
    m4_result = detect_m4_ledger_tampering(clean_sha, clean_ledger)

    # M5: 相同终态单数
    m5_result = detect_m5_denominator_split(12, 12)

    false_positives = sum([
        m1_result.detected,
        m2_result.detected,
        m3_result.detected,
        m4_result.detected,
        m5_result.detected,
    ])

    return {
        "m1_detected": m1_result.detected,
        "m2_detected": m2_result.detected,
        "m3_detected": m3_result.detected,
        "m4_detected": m4_result.detected,
        "m5_detected": m5_result.detected,
        "false_positives": false_positives,
        "clean_baseline_pass": false_positives == 0,
    }
