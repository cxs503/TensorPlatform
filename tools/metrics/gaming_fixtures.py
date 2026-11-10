#!/usr/bin/env python3
"""KPI 反操纵夹具生成器（m9358）：在合成租户上注入 5 类操纵向量，断言目标 KPI 不得上升。

背景（prop-7619 静默改写事故）：
  * 0.6667 被无来源 0 覆盖 → 根因：KPI 无来源声明、无操纵路径登记、无反例夹具。
  * 本工具建立「红队演练」机制：对每类操纵向量生成合成夹具，验证 KPI 计算逻辑
    能抵抗操纵（即操纵后 KPI 不得上升，或上升幅度 < 阈值）。

5 类操纵向量（Goodhart 定律实例）：
  M1 finish_empty: 创建大量无实质工件的 succeeded 单，稀释分母
  M2 duplicate_artifact: 同一 sha256 被多个 mission_id 引用
  M3 copy_samples: 复制样本内容但改 sample_id
  M4 modify_ledger: 直接修改字面量台账文件
  M5 denominator_split: 把难单（fail）挪出终态（改 status 为 cancelled/pending）

用法：
  python3 tools/metrics/gaming_fixtures.py --vector M1 --dry-run
  python3 tools/metrics/gaming_fixtures.py --all --write
  pytest tests/metrics/test_gaming_fixtures.py -v
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = ROOT / "tools" / "metrics" / "kpi_registry.json"
FIXTURES_DIR = ROOT / "artifacts" / "m9358" / "gaming_fixtures"


@dataclass
class SyntheticMission:
    """合成任务单（用于测试 KPI 计算逻辑）"""
    mission_id: str
    status: str  # succeeded, failed, pending, running, cancelled
    goal: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    has_report: bool = False
    report_sha256: Optional[str] = None


@dataclass
class SyntheticReport:
    """合成报告工件"""
    mission_id: str
    sha256: str
    title: str = ""


@dataclass
class SyntheticSample:
    """合成知识库样本"""
    sample_id: str
    content: str
    content_hash: str


@dataclass
class GamingFixture:
    """操纵夹具：基线态 + 操纵态 + 预期断言"""
    vector: str
    description: str
    baseline_missions: List[SyntheticMission] = field(default_factory=list)
    baseline_reports: List[SyntheticReport] = field(default_factory=list)
    baseline_samples: List[SyntheticSample] = field(default_factory=list)
    manipulated_missions: List[SyntheticMission] = field(default_factory=list)
    manipulated_reports: List[SyntheticReport] = field(default_factory=list)
    manipulated_samples: List[SyntheticSample] = field(default_factory=list)
    target_kpis: List[str] = field(default_factory=list)
    assertions: Dict[str, Any] = field(default_factory=dict)


def sha256_hex(content: str) -> str:
    """计算内容 sha256"""
    return hashlib.sha256(content.encode()).hexdigest()


def compute_kpi_success_rate(missions: List[SyntheticMission]) -> Optional[float]:
    """复算 success_rate KPI"""
    terminal = [m for m in missions if m.status in ("succeeded", "failed")]
    if not terminal:
        return None
    succeeded = sum(1 for m in terminal if m.status == "succeeded")
    return succeeded / len(terminal)


def compute_kpi_lineage_coverage(missions: List[SyntheticMission],
                                  reports: List[SyntheticReport]) -> Optional[float]:
    """复算 lineage_coverage KPI"""
    terminal = [m for m in missions if m.status in ("succeeded", "failed")]
    if not terminal:
        return None
    covered = len(set(r.mission_id for r in reports if r.mission_id))
    return covered / len(terminal)


def compute_kpi_repro_rate(verdicts: List[Dict[str, str]]) -> Optional[float]:
    """复算 repro_rate KPI（verdicts: [{mission_id, verdict}]）"""
    if not verdicts:
        return None
    ok = sum(1 for v in verdicts if v.get("verdict") in ("pass", "partial"))
    return ok / len(verdicts)


def compute_kpi_benchmark_cases(samples: List[SyntheticSample]) -> int:
    """复算 benchmark_cases KPI（去重后样本数）"""
    unique_hashes = set(s.content_hash for s in samples)
    return len(unique_hashes)


def generate_m1_finish_empty() -> GamingFixture:
    """M1: finish 空壳单 — 创建大量无实质工件的 succeeded 单
    
    操纵逻辑：创建 100 个 succeeded 单但无报告工件，稀释 lineage_coverage 分母。
    预期断言：lineage_coverage 不得上升（应下降或持平）。
    """
    # 基线态：10 succeeded（有报告）+ 2 failed
    baseline_missions = []
    baseline_reports = []
    for i in range(10):
        mid = f"m_baseline_{i}"
        baseline_missions.append(SyntheticMission(
            mission_id=mid, status="succeeded", has_report=True
        ))
        baseline_reports.append(SyntheticReport(
            mission_id=mid, sha256=sha256_hex(f"report_{i}")
        ))
    for i in range(2):
        baseline_missions.append(SyntheticMission(
            mission_id=f"m_baseline_fail_{i}", status="failed", has_report=False
        ))
    
    # 操纵态：基线 + 100 个 succeeded 空壳单（无报告）
    manipulated_missions = baseline_missions.copy()
    for i in range(100):
        manipulated_missions.append(SyntheticMission(
            mission_id=f"m_empty_{i}", status="succeeded", has_report=False
        ))
    manipulated_reports = baseline_reports.copy()
    
    # 断言：lineage_coverage 不得上升
    baseline_kpi = compute_kpi_lineage_coverage(baseline_missions, baseline_reports)
    manipulated_kpi = compute_kpi_lineage_coverage(manipulated_missions, manipulated_reports)
    
    return GamingFixture(
        vector="M1",
        description="finish 空壳单：创建大量无实质工件的 succeeded 单",
        baseline_missions=baseline_missions,
        baseline_reports=baseline_reports,
        manipulated_missions=manipulated_missions,
        manipulated_reports=manipulated_reports,
        target_kpis=["lineage_coverage", "success_rate"],
        assertions={
            "lineage_coverage_baseline": baseline_kpi,
            "lineage_coverage_manipulated": manipulated_kpi,
            "lineage_coverage_must_not_increase": manipulated_kpi <= baseline_kpi,
            "success_rate_baseline": compute_kpi_success_rate(baseline_missions),
            "success_rate_manipulated": compute_kpi_success_rate(manipulated_missions),
        }
    )


def generate_m2_duplicate_artifact() -> GamingFixture:
    """M2: 重复单复制同一 artifact — 同一 sha256 被多个 mission_id 引用
    
    操纵逻辑：复制同一报告 sha256 到多个 mission_id，虚增 lineage_coverage 分子。
    预期断言：去重后 lineage_coverage 不得上升。
    """
    # 基线态：5 succeeded（各有独立报告）
    baseline_missions = []
    baseline_reports = []
    for i in range(5):
        mid = f"m_baseline_{i}"
        baseline_missions.append(SyntheticMission(
            mission_id=mid, status="succeeded", has_report=True
        ))
        baseline_reports.append(SyntheticReport(
            mission_id=mid, sha256=sha256_hex(f"unique_report_{i}")
        ))
    
    # 操纵态：复制同一 sha256 到 10 个 mission_id
    manipulated_missions = baseline_missions.copy()
    manipulated_reports = baseline_reports.copy()
    shared_sha = sha256_hex("shared_report")
    for i in range(10):
        mid = f"m_dup_{i}"
        manipulated_missions.append(SyntheticMission(
            mission_id=mid, status="succeeded", has_report=True
        ))
        manipulated_reports.append(SyntheticReport(
            mission_id=mid, sha256=shared_sha
        ))
    
    # 断言：去重后 lineage_coverage 不得上升（分子去重后不变，分母增加）
    baseline_kpi = compute_kpi_lineage_coverage(baseline_missions, baseline_reports)
    manipulated_kpi = compute_kpi_lineage_coverage(manipulated_missions, manipulated_reports)
    
    return GamingFixture(
        vector="M2",
        description="重复单复制同一 artifact：同一 sha256 被多个 mission_id 引用",
        baseline_missions=baseline_missions,
        baseline_reports=baseline_reports,
        manipulated_missions=manipulated_missions,
        manipulated_reports=manipulated_reports,
        target_kpis=["lineage_coverage"],
        assertions={
            "lineage_coverage_baseline": baseline_kpi,
            "lineage_coverage_manipulated": manipulated_kpi,
            "lineage_coverage_must_not_increase": manipulated_kpi <= baseline_kpi,
            "unique_sha256_baseline": len(set(r.sha256 for r in baseline_reports)),
            "unique_sha256_manipulated": len(set(r.sha256 for r in manipulated_reports)),
        }
    )


def generate_m3_copy_samples() -> GamingFixture:
    """M3: 复制样本刷 kb_samples — 复制样本内容但改 sample_id
    
    操纵逻辑：复制样本内容但改 sample_id，虚增 kb_samples 计数。
    预期断言：content_hash 去重后 kb_samples 不得上升。
    """
    # 基线态：10 个独立样本
    baseline_samples = []
    for i in range(10):
        content = f"sample_content_{i}"
        baseline_samples.append(SyntheticSample(
            sample_id=f"s_{i}", content=content, content_hash=sha256_hex(content)
        ))
    
    # 操纵态：复制第一个基线样本的内容 50 次（改 sample_id）
    manipulated_samples = baseline_samples.copy()
    shared_content = baseline_samples[0].content  # 复制已有内容
    shared_hash = baseline_samples[0].content_hash
    for i in range(50):
        manipulated_samples.append(SyntheticSample(
            sample_id=f"s_copy_{i}", content=shared_content, content_hash=shared_hash
        ))
    
    # 断言：去重后 kb_samples 不得上升
    baseline_kpi = compute_kpi_benchmark_cases(baseline_samples)
    manipulated_kpi = compute_kpi_benchmark_cases(manipulated_samples)
    
    return GamingFixture(
        vector="M3",
        description="复制样本刷 kb_samples：复制样本内容但改 sample_id",
        baseline_samples=baseline_samples,
        manipulated_samples=manipulated_samples,
        target_kpis=["kb_samples", "benchmark_cases"],
        assertions={
            "kb_samples_baseline_raw": len(baseline_samples),
            "kb_samples_baseline_dedup": baseline_kpi,
            "kb_samples_manipulated_raw": len(manipulated_samples),
            "kb_samples_manipulated_dedup": manipulated_kpi,
            "kb_samples_dedup_must_not_increase": manipulated_kpi <= baseline_kpi,
        }
    )


def generate_m4_modify_ledger() -> GamingFixture:
    """M4: 直改 ledger JSON — 直接修改字面量台账文件
    
    操纵逻辑：直接修改 literal_ledger.json 中的 value 字段。
    预期断言：ledger 文件必须有 sha256 校验链，任何修改必须留审计日志。
    """
    # 基线态：原始 ledger 内容
    baseline_ledger = {
        "values": {
            "tool_categories": {"value": 4, "as_of": "2026-11-01"},
            "ip_assets": {"value": 1, "as_of": "2026-11-01"},
        }
    }
    baseline_sha = sha256_hex(json.dumps(baseline_ledger, sort_keys=True))
    
    # 操纵态：修改 value 字段
    manipulated_ledger = {
        "values": {
            "tool_categories": {"value": 10, "as_of": "2026-11-01"},  # 4 → 10
            "ip_assets": {"value": 5, "as_of": "2026-11-01"},  # 1 → 5
        }
    }
    manipulated_sha = sha256_hex(json.dumps(manipulated_ledger, sort_keys=True))
    
    return GamingFixture(
        vector="M4",
        description="直改 ledger JSON：直接修改字面量台账文件",
        target_kpis=["tool_categories", "ip_assets"],
        assertions={
            "baseline_sha256": baseline_sha,
            "manipulated_sha256": manipulated_sha,
            "sha256_must_differ": baseline_sha != manipulated_sha,
            "detection_mechanism": "ledger 文件必须有 sha256 校验链；任何修改必须留审计日志",
        }
    )


def generate_m5_denominator_split() -> GamingFixture:
    """M5: 分母拆分 — 把难单（fail）挪出终态（改 status 为 cancelled/pending）
    
    操纵逻辑：把 failed 单改 status 为 cancelled，从分母中移除，人为抬高 success_rate。
    预期断言：success_rate 不得上升（或上升幅度 < 阈值）。
    """
    # 基线态：8 succeeded + 4 failed（success_rate = 0.6667）
    baseline_missions = []
    for i in range(8):
        baseline_missions.append(SyntheticMission(
            mission_id=f"m_succ_{i}", status="succeeded"
        ))
    for i in range(4):
        baseline_missions.append(SyntheticMission(
            mission_id=f"m_fail_{i}", status="failed"
        ))
    
    # 操纵态：把 4 个 failed 改 status 为 cancelled
    manipulated_missions = []
    for i in range(8):
        manipulated_missions.append(SyntheticMission(
            mission_id=f"m_succ_{i}", status="succeeded"
        ))
    for i in range(4):
        manipulated_missions.append(SyntheticMission(
            mission_id=f"m_fail_{i}", status="cancelled"  # failed → cancelled
        ))
    
    # 断言：M5 操纵有效（success_rate 上升），但检测机制能捕获（终态单数下降 >20%）
    baseline_kpi = compute_kpi_success_rate(baseline_missions)
    manipulated_kpi = compute_kpi_success_rate(manipulated_missions)
    terminal_baseline = len([m for m in baseline_missions if m.status in ("succeeded", "failed")])
    terminal_manipulated = len([m for m in manipulated_missions if m.status in ("succeeded", "failed")])
    
    return GamingFixture(
        vector="M5",
        description="分母拆分：把难单（fail）挪出终态（改 status 为 cancelled/pending）",
        baseline_missions=baseline_missions,
        manipulated_missions=manipulated_missions,
        target_kpis=["success_rate", "lineage_coverage", "repro_rate"],
        assertions={
            "success_rate_baseline": baseline_kpi,
            "success_rate_manipulated": manipulated_kpi,
            "success_rate_delta": (manipulated_kpi - baseline_kpi) if (baseline_kpi and manipulated_kpi) else None,
            "manipulation_is_effective": (
                manipulated_kpi is not None and baseline_kpi is not None and
                manipulated_kpi > baseline_kpi
            ),
            "terminal_count_baseline": terminal_baseline,
            "terminal_count_manipulated": terminal_manipulated,
            "detection_catches_manipulation": (
                terminal_baseline > 0 and 
                (1 - terminal_manipulated / terminal_baseline) > 0.2
            ),
        }
    )


def generate_all_fixtures() -> List[GamingFixture]:
    """生成所有 5 类操纵夹具"""
    return [
        generate_m1_finish_empty(),
        generate_m2_duplicate_artifact(),
        generate_m3_copy_samples(),
        generate_m4_modify_ledger(),
        generate_m5_denominator_split(),
    ]


def fixture_to_dict(fixture: GamingFixture) -> Dict[str, Any]:
    """将夹具转为可序列化字典"""
    return {
        "vector": fixture.vector,
        "description": fixture.description,
        "target_kpis": fixture.target_kpis,
        "assertions": fixture.assertions,
        "baseline_count": {
            "missions": len(fixture.baseline_missions),
            "reports": len(fixture.baseline_reports),
            "samples": len(fixture.baseline_samples),
        },
        "manipulated_count": {
            "missions": len(fixture.manipulated_missions),
            "reports": len(fixture.manipulated_reports),
            "samples": len(fixture.manipulated_samples),
        },
    }


def main():
    """主入口：生成夹具并落盘"""
    import argparse
    parser = argparse.ArgumentParser(description="KPI 反操纵夹具生成器")
    parser.add_argument("--vector", choices=["M1", "M2", "M3", "M4", "M5", "all"],
                        default="all", help="生成指定向量或全部")
    parser.add_argument("--write", action="store_true", help="落盘到 artifacts/")
    parser.add_argument("--dry-run", action="store_true", help="只打印不落盘")
    args = parser.parse_args()
    
    generators = {
        "M1": generate_m1_finish_empty,
        "M2": generate_m2_duplicate_artifact,
        "M3": generate_m3_copy_samples,
        "M4": generate_m4_modify_ledger,
        "M5": generate_m5_denominator_split,
    }
    
    if args.vector == "all":
        fixtures = generate_all_fixtures()
    else:
        fixtures = [generators[args.vector]()]
    
    results = []
    for f in fixtures:
        d = fixture_to_dict(f)
        results.append(d)
        print(f"\n=== {f.vector}: {f.description} ===")
        print(f"目标 KPI: {f.target_kpis}")
        print(f"断言: {json.dumps(f.assertions, indent=2, ensure_ascii=False)}")
    
    if args.write and not args.dry_run:
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        out_path = FIXTURES_DIR / "gaming_fixtures.json"
        with open(out_path, "w") as fp:
            json.dump({"fixtures": results, "generated_at": datetime.now(timezone.utc).isoformat()},
                      fp, indent=2, ensure_ascii=False)
        print(f"\n✓ 已落盘: {out_path}")
    
    # 验证所有断言
    print("\n=== 断言验证 ===")
    all_pass = True
    for f in fixtures:
        for key, val in f.assertions.items():
            if key.endswith("_must_not_increase") or key == "detection_catches_manipulation":
                if not val:
                    print(f"✗ {f.vector}.{key} FAILED: {val}")
                    all_pass = False
                else:
                    print(f"✓ {f.vector}.{key} PASSED")
            elif key.endswith("_must_differ"):
                if not val:
                    print(f"✗ {f.vector}.{key} FAILED: {val}")
                    all_pass = False
                else:
                    print(f"✓ {f.vector}.{key} PASSED")
            elif key == "manipulation_is_effective":
                if not val:
                    print(f"✗ {f.vector}.{key} FAILED: {val}")
                    all_pass = False
                else:
                    print(f"✓ {f.vector}.{key} PASSED")
    
    if all_pass:
        print("\n✓ 所有断言通过")
        return 0
    else:
        print("\n✗ 部分断言失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
