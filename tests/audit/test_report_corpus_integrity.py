"""Tests for tools.audit.report_corpus_integrity (m2053).

判据全部可手算：重复聚类用构造文档，算术自洽用闭式三元组，闸门用最小基线。
不冻结「当前输出」，只钉住语义。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.audit.report_corpus_integrity import (  # noqa: E402
    ABS_CONVENTION,
    DUP_THRESHOLD,
    DUPLICATE_REPUBLISH,
    MISMATCH,
    NEAR_DUPLICATE,
    OK,
    PERCENT_POINT_CONFUSION,
    ROUNDING_ARTIFACT,
    SIGN_CONVENTION,
    Report,
    analyze,
    audit_table_row,
    audit_tables,
    calibrate_table,
    classify,
    classify_interval,
    cluster_duplicates,
    gate_check,
    jaccard,
    load_corpus_json,
    normalize_title,
    number_interval,
    parse_number,
    parse_percent,
    parse_tables,
    shingles,
    tokenize,
)


def mk(rid: int, text: str, mission: int = 1, sk: str = "case-a",
       title: str = "同一标题", summary: str = "") -> Report:
    return Report(id=rid, mission=mission, scenario_key=sk, title=title,
                  summary=summary, markdown=text)


# ------------------------------------------------------------------ 指纹


def test_tokenize_splits_cjk_chars_and_ascii_words():
    toks = tokenize("阻力 Ct=0.0042 与 ITTC-57")
    assert "阻" in toks and "力" in toks
    assert "ct" in toks and "ittc" in toks
    assert "0.0042" in toks


def test_normalize_title_strips_mission_tag():
    a = normalize_title("波浪增阻律补全（m1797）")
    b = normalize_title("波浪增阻律补全 (m2014)")
    assert a == b == "波浪增阻律补全"


def test_shingles_deterministic_across_calls():
    t = "相对误差 0.05% 判据通过"
    assert shingles(t) == shingles(t)
    assert len(shingles(t)) > 0


def test_jaccard_bounds():
    a, b = shingles("完全相同的正文内容用于测试"), shingles("另一段毫不相干的正文")
    assert jaccard(a, a) == pytest.approx(1.0)
    assert jaccard(a, b) == pytest.approx(0.0)


# ------------------------------------------------------------------ 聚类


def test_cluster_flags_same_mission_republish():
    body = "\n".join(["# 报告", "## 结果", "误差 1.2% 通过", "## 结论", "全部判据通过"] * 4)
    reps = [
        mk(1, body, mission=7),
        mk(2, body + "（补一段附注）", mission=7),
        mk(3, "完全不同的内容：冰阻力 Lindqvist 模型验证与标定", mission=9, sk="ice"),
    ]
    cl = cluster_duplicates(reps)
    assert len(cl) == 1
    assert cl[0].kind in (DUPLICATE_REPUBLISH, NEAR_DUPLICATE)  # 同一成果族
    assert cl[0].ids == [1, 2]
    assert cl[0].canonical == 1 and cl[0].extra == 1


def test_cluster_requires_similarity_not_just_same_mission():
    reps = [
        mk(1, "甲" * 300, mission=5),
        mk(2, "乙" * 300, mission=5),
    ]
    assert cluster_duplicates(reps) == []


def test_cluster_marks_near_duplicate_between_thresholds():
    base = ["第%d段：阻力计算与网格收敛验证，误差 %d%% 通过。" % (i, i) for i in range(20)]
    rev = base[:14] + ["第%d段：改写后的说明文字，与原先完全不同。" % i for i in range(14, 20)]
    j = jaccard(shingles("\n".join(base)), shingles("\n".join(rev)))
    assert 0.5 < j < 0.95, j
    cl = cluster_duplicates([mk(1, "\n".join(base)), mk(2, "\n".join(rev), mission=2)], near_threshold=0.5)
    assert len(cl) == 1 and cl[0].kind in (NEAR_DUPLICATE, DUPLICATE_REPUBLISH)


# ------------------------------------------------------------ 数值解析


@pytest.mark.parametrize("cell,expected", [
    ("**20.5%**", 20.5),
    ("+942.35%", 942.35),
    ("-0.064%", -0.064),
    ("−3.4 %", -3.4),
    ("0.8 ‰", pytest.approx(0.08)),
    ("0.0039 / 0.0032", None),
    ("通过", None),
])
def test_parse_percent(cell, expected):
    got = parse_percent(cell)
    if expected is None:
        assert got is None
    else:
        assert got == expected


@pytest.mark.parametrize("cell,expected", [
    ("100,000", 100000.0),
    ("3.0 (Armaly)", 3.0),
    ("0.00833 (ITTC-57)", 0.00833),
    ("200×80×80", None),
    ("21.80°", None),
    ("0.0039 / 0.0032", None),
])
def test_parse_number(cell, expected):
    assert parse_number(cell) == expected


def test_number_interval_uses_printed_precision():
    lo, hi = number_interval("0.0034")
    assert lo == pytest.approx(0.00335) and hi == pytest.approx(0.00345)
    lo, hi = number_interval("2.26")
    assert lo == pytest.approx(2.255) and hi == pytest.approx(2.265)


# ------------------------------------------------------------ 约定裁决


def test_classify_three_conventions():
    assert classify(20.5, 20.5) == OK
    assert classify(20.5, -20.5) == SIGN_CONVENTION
    assert classify(-20.5, 20.5) == SIGN_CONVENTION
    assert classify(942.35, -21.21) is None


def test_classify_interval_explains_rounding():
    # 1002 号行的真实形态：打印 0.0038 / 0.0041，真误差 -7.32%，报告写 7.5%
    assert classify_interval(7.5, number_interval("0.0038"), number_interval("0.0041")) == ROUNDING_ARTIFACT
    # 不可解释：942.35% 与 21.21% 相差两个量级
    assert classify_interval(942.35, number_interval("35.0"), number_interval("44.4235")) is None


# ------------------------------------------------------------ 表级审计


def test_parse_tables_extracts_header_and_rows():
    md = "\n".join([
        "| 算例 | 计算值 | 参考值 | 误差% |",
        "|---|---|---|---|",
        "| A | 35.0 | 44.4235 | -21.21% |",
        "| B | 10.0 | 8.0 | 25.0% |",
    ])
    tabs = parse_tables(md)
    assert len(tabs) == 1
    assert tabs[0].header[0] == "算例"
    assert len(tabs[0].rows) == 2


def test_calibrate_table_picks_value_and_reference_columns():
    md = "\n".join([
        "| 算例 | 计算值 | 参考值 | 误差% |",
        "|---|---|---|---|",
        "| A | 35.0000 | 44.4235 | -21.21% |",
        "| B | 10.0 | 8.0 | 25.0% |",
        "| C | 6.0 | 5.0 | 20.0% |",
    ])
    tab = parse_tables(md)[0]
    cal = calibrate_table(tab)
    assert cal is not None
    assert (cal.val_col, cal.ref_col, cal.err_col) == (1, 2, 3)
    assert cal.matched == 3 and cal.total == 3


def test_audit_flags_percent_point_confusion():
    """(a−b)×100 冒充相对误差 —— m2053 在 1115/1116 号报告抓到的真实缺陷类。"""
    md = "\n".join([
        "| 算例 | 计算值 | 参考值 | 误差% |",
        "|---|---|---|---|",
        "| A | 35.0000 | 44.4235 | -21.21% |",
        "| B | 10.0 | 8.0 | 25.0% |",
        "| C | 6.0 | 5.0 | 20.0% |",
        "| D | 35.0 | 44.4235 | 942.35% |",
    ])
    flags, n_tab, n_checked, _ = audit_tables([mk(1, md)])
    assert n_tab == 1 and n_checked == 4
    kinds = [f.kind for f in flags]
    assert kinds.count(PERCENT_POINT_CONFUSION) == 1
    assert kinds.count(OK) >= 2


def test_audit_skips_uncalibratable_table():
    md = "\n".join([
        "| 算例 | 甲 | 乙 | 备注 |",
        "|---|---|---|---|",
        "| A | 1.0 | 2.0 | 说明 |",
        "| B | 3.0 | 4.0 | 说明 |",
    ])
    flags, n_tab, n_checked, n_skipped = audit_tables([mk(1, md)])
    assert n_tab == 0 and n_checked == 0 and flags == []
    assert n_skipped == 2


def test_audit_requires_minimum_calibration_rate():
    md = "\n".join([
        "| 算例 | 计算值 | 参考值 | 误差% |",
        "|---|---|---|---|",
        "| A | 1.0 | 2.0 | 99.0% |",
        "| B | 3.0 | 4.0 | 98.0% |",
        "| C | 5.0 | 6.0 | 97.0% |",
    ])
    tab = parse_tables(md)[0]
    assert calibrate_table(tab) is None  # 0/3 命中，不可认证


# ------------------------------------------------------------------ 主流程


def test_analyze_reports_inflation_and_summary():
    body = "阻力验证报告：误差 1.2% 通过。\n" * 20
    reps = [mk(1, body, mission=1), mk(2, body, mission=1, sk="case-a"),
            mk(3, "冰区推进完全不同的正文内容" * 20, mission=2, sk="ice")]
    res = analyze(reps)
    assert res.n_reports == 3
    assert res.n_extra_reports == 1
    assert res.summary()["inflation_pct"] == pytest.approx(33.333, abs=1e-3)


def test_gate_passes_on_identical_baseline_and_fails_on_new_cluster():
    body = "验证报告正文用于聚类判定。" * 30
    reps = [mk(1, body, mission=1), mk(2, body, mission=1)]
    res = analyze(reps)
    ok, problems = gate_check(res, res.to_dict())
    assert ok and problems == []

    baseline = {"clusters": [], "n_arith_mismatch": 0, "defect_slack": 0}
    ok, problems = gate_check(res, baseline)
    assert not ok and "新增重复簇" in problems[0]


def test_load_corpus_json_roundtrip(tmp_path):
    payload = {"reports": [{
        "id": 7, "mission_id": 3, "scenario_key": "k", "title": "T",
        "summary": "S", "markdown": "| 算例 | 计算值 | 参考值 | 误差% |\n|---|---|---|---|",
        "created_at": "2026-11-01", "status": "published",
    }]}
    f = tmp_path / "c.json"
    f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    reps = load_corpus_json(str(f))
    assert len(reps) == 1 and reps[0].id == 7 and reps[0].mission == 3


def test_audit_table_row_returns_none_for_non_triple():
    assert audit_table_row(["A", "1.0", "说明"]) is None
    assert audit_table_row(["A", "1.0", "2.0", "3.0"]) is None


def test_dup_and_near_thresholds_are_ordered():
    assert 0.0 < 0.60 < DUP_THRESHOLD <= 1.0