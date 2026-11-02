"""误差宣称一致性审计的判据钉死测试（m1856）。

每个用例对应 ``tools/error_claim_audit.py`` 里的一条显式规则或一条已知陷阱：
**正控制**（构造违规必须被抓）+ **负控制**（诚实 FAIL / 披露 / 收敛链 / 审计对照表的 ✅
不得误报）。误报历史见模块 docstring：v1 用「子串判定词」导致 39/39 条 HARD 全为误报，
故本文件的负控制用例是硬约束，不得放宽。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("error_claim_audit", ROOT / "tools" / "error_claim_audit.py")
eca = importlib.util.module_from_spec(_spec)
sys.modules["error_claim_audit"] = eca
_spec.loader.exec_module(eca)


def _rep(md: str, rid: int = 1, title: str = "t"):
    return {"id": rid, "title": title, "markdown": md, "mission_id": 1856}


def _rules(md: str):
    return [v["rule"] for v in eca.analyze_report(_rep(md))["violations"]]


# --------------------------------------------------------------------------- #
# 1. 平台阈值常量（改常量必须同步改测试）
# --------------------------------------------------------------------------- #
def test_thresholds_pinned():
    assert eca.TARGET_TOL_PCT == 3.0
    assert eca.HARD_TOL_PCT == 15.0


def test_rules_tuple():
    assert eca.RULES == ("HARD_GT15", "SOFT_THRESH", "UNSUPPORTED")


# --------------------------------------------------------------------------- #
# 2. 正控制：必须抓到
# --------------------------------------------------------------------------- #
def test_hard_gt15_detected():
    assert "HARD_GT15" in _rules("最终相对误差 22.4%，判定达标。")


def test_hard_gt15_via_acceptance_criterion():
    assert "HARD_GT15" in _rules("静水阻力相对误差 18.0%，满足验收标准。")


def test_soft_thresh_self_contradiction():
    assert "SOFT_THRESH" in _rules("升力系数偏差 9.6%，满足<3%判据，判定达标。")


def test_hard_boundary_exactly_15_not_flagged():
    # 15% 恰在硬标准上界（">15% 不可接受"），不外推
    assert _rules("最大相对误差 15.0%，判定达标。") == []


def test_soft_not_flagged_without_declared_threshold():
    # 4.92% 无声明容差 → 只满足 15% 硬标准，判据不下结论（不是违规）
    assert _rules("设计点误差 -4.92% → PASS") == []


# --------------------------------------------------------------------------- #
# 3. 负控制：诚实声明/审计语境不得误报
# --------------------------------------------------------------------------- #
def test_honest_fail_not_flagged():
    assert _rules("该量相对误差 22.4%，判定不达标，已记入欠账。") == []


def test_disclosure_not_flagged():
    assert _rules("相对误差 22.4%，远超平台 15% 标准，属已知缺陷。") == []


def test_negated_pass_not_flagged():
    assert _rules("全部关键量无一项满足 <3%，K_T 偏差 300%。") == []


def test_checkmark_is_not_acceptance_token():
    # 审计对照表里的 ✅ 是「命中」而非对误差的背书
    assert _rules("| 1 | 旧实现偏差 +78.5% | 工件 | ✅ |") == []
    assert _rules("✅ 根因分析: 定位 +13% 误差主因为非收敛") == []


def test_pass_rate_is_not_acceptance_token():
    assert _rules("12 案例误差 33% 通过率，以这份数据为基准") == []


def test_convergence_chain_deferred():
    out = eca.analyze_report(_rep("误差链严格单调（73.87% → 27.19% → 0.048%），最细档 ≤ 3%，PASS"))
    rules = [c["rule"] for c in out["claims"]]
    assert "DEFERRED_CHAIN" in rules
    assert not out["violations"]


def test_table_row_deferred():
    out = eca.analyze_report(_rep("| 判据 | 实测 | 结论 |\n|---|---|---|\n| <3% | 19.6% 偏差 | 达标 |"))
    assert not out["violations"]
    assert any(c["rule"] == "DEFERRED_TABLE" for c in out["claims"])


def test_criterion_number_not_a_claim():
    # 「满足 3 % 判据」里的 3 % 是要求，不是实测误差
    assert eca.extract_claims("本条欠账为 0.3 % 的有界系统偏差，满足 3 % 判据") == []


def test_verdict_scope_deferred():
    # 判定词与数值分处不相邻子句 → 作用域不明，只计数不判级
    out = eca.analyze_report(_rep("第一段说明误差 12.0%；第二段另述某某检查全部达标（与本量无关的检查）"))
    assert not out["violations"]


# --------------------------------------------------------------------------- #
# 4. UNSUPPORTED：无数字的达标
# --------------------------------------------------------------------------- #
def test_unsupported_when_no_numbers():
    out = eca.analyze_report(_rep("误差判定：是否<3%：是。"))
    assert out["unsupported"]


def test_supported_when_numbers_present():
    out = eca.analyze_report(_rep("各项 density=1.82%，外推 3200 全部 <3%"))
    assert not out["unsupported"]


def test_unsupported_skips_disclaimer():
    out = eca.analyze_report(_rep("误差判定：是否<3%：是？这种「全部<3%」的说法是不诚实的"))
    assert not out["unsupported"]


# --------------------------------------------------------------------------- #
# 5. 结构性：切句 / 阈值解析 / 汇总 / 闸门 / 确定性
# --------------------------------------------------------------------------- #
def test_split_sentences_line_numbers():
    sents = eca.split_sentences("甲。乙。\n\n丙；")
    assert [ln for ln, _t in sents] == [1, 1, 3]


def test_declared_threshold_parsing():
    assert eca.declared_threshold("满足<5%判据") == 5.0
    assert eca.declared_threshold("不超过 10 %") == 10.0
    assert eca.declared_threshold("没有容差") is None


def test_summary_counts():
    rows = [eca.analyze_report(_rep("相对误差 22.4%，判定达标。", rid=1)),
            eca.analyze_report(_rep("相对误差 1.8%，判定达标。", rid=2))]
    s = eca.summarize(rows)
    assert s["reports_scanned"] == 2
    assert s["reports_with_violation"] == 1
    assert s["violation_items_by_rule"]["HARD_GT15"] == 1
    assert s["claims_total"] == 2


def test_render_markdown_mentions_rules():
    md = eca.render_markdown(eca.summarize([]), [], top=5)
    assert "HARD_GT15" in md and "SOFT_THRESH" in md and "15.0%" in md


def test_self_gate_exit_code(tmp_path):
    bad = {"reports": [{"report_id": 1, "violations": [{"rule": "HARD_GT15"}], "unsupported": []}]}
    p = tmp_path / "claims.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    assert eca.run_self(p) == 1
    p.write_text(json.dumps({"reports": [{"report_id": 1, "violations": [], "unsupported": []}]}))
    assert eca.run_self(p) == 0


def test_deterministic_output():
    md = "相对误差 22.4%，判定达标。\n另：偏差 9.6%，满足<3%判据。"
    a = json.dumps(eca.analyze_report(_rep(md)), ensure_ascii=False, sort_keys=True)
    b = json.dumps(eca.analyze_report(_rep(md)), ensure_ascii=False, sort_keys=True)
    assert a == b


# --------------------------------------------------------------------------- #
# 6. 变异测试（正/负控制的可复算验收）
# --------------------------------------------------------------------------- #
def _corpus(n: int = 25):
    body = "## ③ 计算结果\n\n| 量 | 值 |\n|---|---|\n| Rt | 12.3 N |\n" + ("说明文字。" * 80)
    return [{"id": i, "title": f"r{i}", "markdown": body, "mission_id": 1} for i in range(1, n + 1)]


def test_mutation_recall_and_false_positive():
    res = eca.run_mutation(_corpus(), per_template=10)
    assert res["positive_recall"] == 1.0
    assert res["negative_fp_rate"] == 0.0
    assert res["seed"] == eca.MUTATION_SEED


def test_mutation_deterministic():
    a = eca.run_mutation(_corpus(8), per_template=5)
    b = eca.run_mutation(_corpus(8), per_template=5)
    assert a == b