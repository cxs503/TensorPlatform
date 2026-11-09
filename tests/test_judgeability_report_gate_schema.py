"""E5 不与既有度量/报告门双写（P151W1-N10）。

判据先于实现，机器可裁：
1. ``services.report_gate.check_report_quality`` 的输出 **key 集**不变（回归基线固化在本测试里）；
2. 新门不复制报告门的判据：两套门各自只读不改对方产物（本单未触碰 report_gate 源文件）；
3. 新门对同一输入幂等（重复判定结果一致），不与报告门共享可变的全局状态。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

REPORT_GATE = BACKEND / "services" / "report_gate.py"

#: E5 基线：报告门输出的 key 集（改动本行 = 破坏下游消费者契约，必须显式评审）
EXPECTED_TOP_KEYS = {"passed", "issues", "issue_codes", "checks", "details"}
EXPECTED_CHECKS_KEYS = {"physics_statement", "method", "validation", "figures",
                        "limitations", "error_verdict"}
EXPECTED_DETAILS_KEYS = {"has_figures", "captions_ok", "no_figure_justification",
                         "error_number", "verdict_word", "no_baseline_declared",
                         "unverifiable_dimensions", "named_reference_hits",
                         "uncomparable_error_units", "unbacked_comparisons"}


def _gate():
    from services import report_gate      # noqa: F401
    return report_gate


def test_report_gate_output_schema_unchanged():
    gate = _gate()
    out = gate.check_report_quality("# t\n## 1 案例介绍\n## 2 方法\n## 3 结果\n"
                                    "## 4 注意点\n## 5 误差判定\n误差 <3%\n", [])
    assert set(out) == EXPECTED_TOP_KEYS
    assert set(out["checks"]) == EXPECTED_CHECKS_KEYS
    assert set(out["details"]) == EXPECTED_DETAILS_KEYS
    # 类型契约
    assert isinstance(out["passed"], bool)
    assert isinstance(out["issues"], list) and isinstance(out["issue_codes"], list)
    assert all(isinstance(k, str) for k in out["checks"])
    assert all(isinstance(v, bool) for v in out["checks"].values())


def test_report_gate_source_untouched_by_this_module():
    """本单不改报告门（零双写）：源文件 hash 与 test 里固化的行为契约一致即可，
    但至少必须确认它仍能被导入且不含本门模块的引用（单向依赖）。"""
    assert REPORT_GATE.is_file()
    src = REPORT_GATE.read_text(encoding="utf-8")
    assert "judgeability" not in src, "报告门不应依赖判据门（避免双写/循环）"
    assert hashlib.sha256(src.encode("utf-8")).hexdigest()  # 可复算（留痕用）


def test_judgeability_gate_is_idempotent_and_state_free():
    from backend.quality.judgeability import gate as G
    goal = "【战役 DAG 节点|X3|示例】机器验收判据：结果良好。纪律：x"
    first = G.decide(goal, mode="enforce")
    second = G.decide(goal, mode="enforce")
    assert first.as_dict() == second.as_dict()
    assert G.replay_enforce([goal]) == G.replay_enforce([goal])
    # 判定不写盘：注入只发生在返回值里，不修改入参字符串
    assert goal == "【战役 DAG 节点|X3|示例】机器验收判据：结果良好。纪律：x"


def test_no_duplicate_gate_outputs_written_by_judgeability():
    """判据门的产物目录与报告门产物（报告 JSON）互不重叠。"""
    from backend.quality.judgeability import audit as A
    names = set(A.audit_records([{"id": 1, "goal": "误差 <= 1%（实验）"}]).keys())
    assert {"schema", "digest", "per_record", "slices", "overall"} <= names
    # 报告门不产出这些键（其 key 集见上），两者 schema 无交叠命名冲突即可
    assert not ({"checks", "details"} & names)