#!/usr/bin/env python3
"""性能门禁自动化脚本（M-P3·P1）。

读取谱系 JSON（M-G1 产出，docs/council/ 下），对照判据，输出 PASS/FAIL 的门禁裁决。
用 SDAA Card 16 做一次真实验证。

判据：
  C1 几何验证：Cb/L/B 必须先验证（缺失 → FAIL）
  C2 误差阈值：对标实验/参考值误差 ≤15% 为可接受（>15% → FAIL）
  C3 门禁精度：对已知 PASS/FAIL case 裁决准确率 100%
  C4 端到端时限：端到端流程 <5min

用法：
  python tools/perf_gate.py [--lineage PATH] [--json] [--verbose]

输出：
  默认 human-readable 表格；--json 输出 JSON；--verbose 输出逐案例详情。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Geometry:
    L: Optional[float] = None
    B: Optional[float] = None
    H: Optional[float] = None
    T: Optional[float] = None
    Cb: Optional[float] = None


@dataclass
class CaseResults:
    max_velocity_error_pct: Optional[float] = None
    mean_velocity_error_pct: Optional[float] = None
    mass_conservation_error_pct: Optional[float] = None
    resistance_error_pct: Optional[float] = None
    thrust_error_pct: Optional[float] = None
    effective_power_error_pct: Optional[float] = None
    grid_resolution: Optional[str] = None
    failure_reason: Optional[str] = None


@dataclass
class Case:
    case_id: str
    name: str
    benchmark: str
    expected_verdict: str
    geometry: Optional[Dict[str, Any]] = None
    physics: Optional[Dict[str, Any]] = None
    results: Optional[Dict[str, Any]] = None
    evidence: Optional[Dict[str, Any]] = None
    failure_reason: Optional[str] = None


@dataclass
class Verdict:
    case_id: str
    verdict: str  # PASS / FAIL
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 判据检查
# ---------------------------------------------------------------------------

def check_geometry_verified(geometry: Optional[Dict[str, Any]]) -> tuple[bool, str]:
    """C1: 几何验证 — Cb/L/B 必须先验证。"""
    if geometry is None:
        return False, "geometry_not_verified: Cb/L/B missing"
    required = {"Cb", "L", "B"}
    missing = required - set(geometry.keys())
    if missing:
        return False, f"geometry_not_verified: missing {missing}"
    # 验证 Cb 合理性 (0 < Cb <= 1)
    cb = geometry.get("Cb")
    if cb is not None and (cb <= 0 or cb > 1):
        return False, f"geometry_invalid: Cb={cb} out of range (0, 1]"
    return True, "geometry_verified"


def check_error_threshold(results: Optional[Dict[str, Any]], max_error_pct: float = 15.0) -> tuple[bool, str]:
    """C2: 误差阈值 — 对标实验/参考值误差 ≤15% 为可接受。"""
    if results is None:
        return False, "results_missing: no results data"

    # 检查各类误差
    error_keys = [
        "max_velocity_error_pct",
        "resistance_error_pct",
        "thrust_error_pct",
        "effective_power_error_pct",
    ]
    for key in error_keys:
        val = results.get(key)
        if val is not None and val > max_error_pct:
            return False, f"error_threshold_exceeded: {key}={val}% > {max_error_pct}%"

    return True, "error_within_threshold"


def check_mass_conservation(results: Optional[Dict[str, Any]], max_mc_error: float = 1.0) -> tuple[bool, str]:
    """质量守恒检查 — mass_conservation_error_pct ≤ 1%。"""
    if results is None:
        return False, "results_missing"
    mc = results.get("mass_conservation_error_pct")
    if mc is not None and mc > max_mc_error:
        return False, f"mass_conservation_violated: {mc}% > {max_mc_error}%"
    return True, "mass_conservation_ok"


def evaluate_case(case: Case) -> Verdict:
    """对单个案例执行门禁裁决。"""
    reasons: List[str] = []
    details: Dict[str, Any] = {}

    # C1: 几何验证
    geo_ok, geo_reason = check_geometry_verified(case.geometry)
    details["geometry_verified"] = geo_ok
    details["geometry_reason"] = geo_reason
    if not geo_ok:
        reasons.append(geo_reason)

    # C2: 误差阈值
    err_ok, err_reason = check_error_threshold(case.results)
    details["error_threshold_ok"] = err_ok
    details["error_reason"] = err_reason
    if not err_ok:
        reasons.append(err_reason)

    # 质量守恒
    mc_ok, mc_reason = check_mass_conservation(case.results)
    details["mass_conservation_ok"] = mc_ok
    details["mass_conservation_reason"] = mc_reason
    if not mc_ok:
        reasons.append(mc_reason)

    # 综合判定
    if reasons:
        verdict = "FAIL"
    else:
        verdict = "PASS"

    return Verdict(
        case_id=case.case_id,
        verdict=verdict,
        reasons=reasons,
        details=details,
    )


# ---------------------------------------------------------------------------
# 谱系加载
# ---------------------------------------------------------------------------

def load_lineage(path: Path) -> Dict[str, Any]:
    """加载谱系 JSON。"""
    if not path.exists():
        raise FileNotFoundError(f"Lineage file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_cases(lineage: Dict[str, Any]) -> List[Case]:
    """从谱系 JSON 解析案例列表。"""
    cases = []
    for c in lineage.get("cases", []):
        cases.append(Case(
            case_id=c["case_id"],
            name=c["name"],
            benchmark=c["benchmark"],
            expected_verdict=c["expected_verdict"],
            geometry=c.get("geometry"),
            physics=c.get("physics"),
            results=c.get("results"),
            evidence=c.get("evidence"),
            failure_reason=c.get("failure_reason"),
        ))
    return cases


# ---------------------------------------------------------------------------
# 门禁执行
# ---------------------------------------------------------------------------

def run_gate(lineage_path: Path, verbose: bool = False) -> Dict[str, Any]:
    """执行性能门禁，返回裁决结果。"""
    started = time.perf_counter()

    # 加载谱系
    lineage = load_lineage(lineage_path)
    cases = parse_cases(lineage)

    # 逐案例裁决
    verdicts: List[Verdict] = []
    for case in cases:
        v = evaluate_case(case)
        verdicts.append(v)
        if verbose:
            print(f"  [{v.verdict}] {v.case_id}")
            for r in v.reasons:
                print(f"    → {r}")

    # 统计准确率
    total = len(verdicts)
    correct = sum(1 for v in verdicts if v.verdict == next(
        c["expected_verdict"] for c in lineage["cases"] if c["case_id"] == v.case_id
    ))
    accuracy = correct / total if total > 0 else 0.0

    wallclock = time.perf_counter() - started

    # 构建报告
    report = {
        "mission_id": lineage.get("mission_id", "m304"),
        "lineage_path": str(lineage_path),
        "total_cases": total,
        "correct_verdicts": correct,
        "accuracy": accuracy,
        "wallclock_s": round(wallclock, 3),
        "wallclock_pass": wallclock < 300,
        "verdicts": [
            {
                "case_id": v.case_id,
                "verdict": v.verdict,
                "expected": next(
                    c["expected_verdict"] for c in lineage["cases"]
                    if c["case_id"] == v.case_id
                ),
                "correct": v.verdict == next(
                    c["expected_verdict"] for c in lineage["cases"]
                    if c["case_id"] == v.case_id
                ),
                "reasons": v.reasons,
                "details": v.details,
            }
            for v in verdicts
        ],
        "criteria": lineage.get("criteria", []),
        "verdict_rules": lineage.get("verdict_rules", {}),
    }

    return report


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------

def format_table(report: Dict[str, Any]) -> str:
    """格式化人类可读表格。"""
    lines = []
    lines.append("=" * 80)
    lines.append("性能门禁裁决报告（M-P3·P1）")
    lines.append("=" * 80)
    lines.append(f"谱系文件: {report['lineage_path']}")
    lines.append(f"任务 ID: {report['mission_id']}")
    lines.append(f"案例总数: {report['total_cases']}")
    lines.append(f"正确裁决: {report['correct_verdicts']}/{report['total_cases']}")
    lines.append(f"裁决准确率: {report['accuracy']:.1%}")
    lines.append(f"端到端耗时: {report['wallclock_s']:.3f}s {'✅ <5min' if report['wallclock_pass'] else '❌ >=5min'}")
    lines.append("")
    lines.append("-" * 80)
    lines.append(f"{'Case ID':<25} {'Expected':<10} {'Actual':<10} {'Correct':<10} {'Reasons'}")
    lines.append("-" * 80)

    for v in report["verdicts"]:
        correct_mark = "✅" if v["correct"] else "❌"
        reasons_str = "; ".join(v["reasons"]) if v["reasons"] else "—"
        lines.append(
            f"{v['case_id']:<25} {v['expected']:<10} {v['verdict']:<10} {correct_mark:<10} {reasons_str}"
        )

    lines.append("-" * 80)
    lines.append("")

    # SDAA Card 16 专项
    sdaa = next((v for v in report["verdicts"] if "sdaa" in v["case_id"]), None)
    if sdaa:
        lines.append("★ SDAA Card 16 真实验证")
        lines.append(f"  裁决: {sdaa['verdict']}")
        lines.append(f"  理由: {'; '.join(sdaa['reasons']) if sdaa['reasons'] else '全部判据通过'}")
        lines.append(f"  详情: {json.dumps(sdaa['details'], ensure_ascii=False, indent=4)}")
        lines.append("")

    # 门禁判据总结
    lines.append("门禁判据摘要:")
    for c in report.get("criteria", []):
        lines.append(f"  {c['id']}: {c['name']} — {c['description']}")

    lines.append("")
    lines.append("=" * 80)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lineage",
        type=Path,
        default=Path(__file__).parent.parent / "docs" / "council" / "lineage_mg1.json",
        help="谱系 JSON 路径（默认: docs/council/lineage_mg1.json）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--verbose", action="store_true", help="输出逐案例详情")
    parser.add_argument(
        "--output",
        type=Path,
        help="输出文件路径（默认 stdout）",
    )
    args = parser.parse_args()

    try:
        report = run_gate(args.lineage, verbose=args.verbose)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in lineage file: {e}", file=sys.stderr)
        return 1

    # 输出
    if args.json:
        output = json.dumps(report, ensure_ascii=False, indent=2)
    else:
        output = format_table(report)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Report written to: {args.output}")
    else:
        print(output)

    # 退出码：准确率 100% 且 <5min → 0
    if report["accuracy"] < 1.0:
        return 1
    if not report["wallclock_pass"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
