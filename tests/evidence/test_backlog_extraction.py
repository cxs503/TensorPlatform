"""P44-N09 欠账提取器测试（离线可复算，不触网）。

覆盖四层：
1. ``numeric`` —— 严格数值扫描（百分比/比值/千分位/±/尾部注释/整串匹配）；
2. ``mdtables`` —— GFM 表格切分（转义竖线、行内代码、分隔行、小节标题）；
3. ``extract`` —— 判词归类 + 欠账行产出（行号、多误差列展开、负例/负控抑制）；
4. 端到端 —— 在临时语料上跑 ``build_backlog`` + ``evaluate``，验 backlog.jsonl /
   coverage / dry-run 零派单 / 判据门。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.evidence.backlog_extraction import dedup, mdtables
from tools.evidence.backlog_extraction.build import build_backlog
from tools.evidence.backlog_extraction.evaluate import evaluate, loc_key
from tools.evidence.backlog_extraction.extract import (
    ReportMeta,
    classify_verdict,
    extract_from_markdown,
    find_node_token,
)
from tools.evidence.backlog_extraction.numeric import (
    parse_number_cell,
    parse_percent_cell,
    scan_leading_number,
)
from tools.evidence.backlog_extraction.schema import (
    ROOT_CAUSES,
    DebtRow,
    SchemaError,
    Source,
    dedupe_rows,
    normalize_metric,
    validate_jsonl_row,
)

# ------------------------------------------------------------------ numeric
def test_percent_cell_is_percent():
    got = scan_leading_number("−21.5%")
    assert got is not None
    assert got.is_percent
    assert got.as_percent == pytest.approx(-21.5)


def test_ratio_cell_scaled_to_percent():
    got = parse_number_cell("0.169")
    assert got is not None and got.scale_assumed
    assert got.as_percent == pytest.approx(16.9)


def test_plain_number_above_point_two_is_percent_not_ratio():
    """``10.526`` 是 10.526%（不是 1052.6%）——历史 bug 就死在这里。"""
    got = parse_number_cell("10.526")
    assert got is not None
    assert got.as_percent == pytest.approx(10.526)


def test_trailing_annotation_keeps_leading_value_without_rescaling():
    got = scan_leading_number("2/2 完整阶梯严格单调")
    assert got is not None
    assert got.value == pytest.approx(2.0)
    assert got.as_percent == pytest.approx(2.0)


def test_thousands_separator_and_pm():
    assert parse_number_cell("6 009").value == pytest.approx(6009)
    parsed = scan_leading_number("±0.07（n=24）")
    # 带尾部注释 → 按字面百分数（0.07%），不做比值 ×100 标定
    assert parsed is not None and parsed.as_percent == pytest.approx(0.07)


def test_placeholder_and_non_numeric_return_none():
    for cell in ("—", "n/a", "未测", "", "无数据", "同上"):
        assert scan_leading_number(cell) is None, cell


def test_parse_number_cell_rejects_trailing_comment():
    assert parse_number_cell("12.5%（注释）") is None
    assert scan_leading_number("12.5%（注释）") is not None


# ----------------------------------------------------------------- mdtables
def test_split_cells_escaped_pipe_and_code_span():
    line = r"| a | b\|c | `x|y` | d |"
    assert mdtables.split_cells(line) == ["a", "b|c", "`x|y`", "d"]


def test_parse_tables_captures_heading_and_line_numbers():
    text = "\n".join(
        [
            "# 报告",
            "",
            "## 6. 误差判定",
            "",
            "| 关键量 | 基准 | 误差 | <3%？ |",
            "|---|---|---|---|",
            "| 峰值压力 | Chuang | −21.5% | **否** |",
        ]
    )
    tables = mdtables.parse_tables(text)
    assert len(tables) == 1
    table = tables[0]
    assert table.heading == "6. 误差判定"
    assert table.rows[0][0][0] == "峰值压力"
    assert table.rows[0][1] == 7  # 物理行号（1-based）


# ------------------------------------------------------------- 判词与节点号
@pytest.mark.parametrize(
    "cell,expected",
    [
        ("**否（>3%，但 ≤15% 判据 ✓）**", "FAIL"),
        ("**否**", "FAIL"),
        ("**是**", "PASS"),
        ("WITHHELD", "WITHHELD"),
        ("❌ 未通过", "FAIL"),
        ("✅ 通过", "PASS"),
        ("succeeded ×9", None),
        ("GATE_REJECT（volume/cb）", None),
        ("NO", "FAIL"),
        ("YES", "PASS"),
        ("STUB", None),
    ],
)
def test_classify_verdict(cell, expected):
    assert classify_verdict(cell) == expected


def test_find_node_token():
    assert find_node_token("P21-N03 砰击压力 REAL 化") == "P21-N03"
    assert find_node_token("IN20-R5 停摆自检") == "IN20-R5"
    assert find_node_token("没有任何节点号") == ""


# -------------------------------------------------------------------- schema
def _row(**kw) -> DebtRow:
    base = dict(
        repo="TensorLBM",
        case="dam_break_sc",
        metric="a=40 @ T=2 err%",
        measured=None,
        reference=None,
        error_pct=-9.723,
        threshold_pct=3.0,
        lt_3pct=False,
        rootcause="implementation_defect",
        requeue_criterion="修复后复测",
        depends_on=(),
        verdict="FAIL",
        source=Source(kind="pending_verdict", file="pending/x.md", line=26),
    )
    base.update(kw)
    return DebtRow(**base)


def test_schema_rejects_bad_rootcause():
    with pytest.raises(SchemaError):
        _row(rootcause="跑不动").validate()


def test_fingerprint_ignores_formatting_but_not_error_bucket():
    a = _row(metric="a=40  @ T=2   err%")
    b = _row(metric="a=40 @ T=2 err%")
    assert a.fingerprint == b.fingerprint
    c = _row(error_pct=-19.9)
    assert a.fingerprint != c.fingerprint


def test_dedupe_rows_keeps_one_per_fingerprint():
    rows = [_row(), _row(source=Source(kind="pending_verdict", file="pending/y.md", line=53))]
    assert len(dedupe_rows(rows)) == 1


def test_normalize_metric_strips_markdown():
    assert normalize_metric("**C_T(Fn)**") == normalize_metric("c_t(fn)")


def test_validate_jsonl_row_catches_missing_key():
    payload = _row().to_dict()
    payload.pop("rootcause")
    with pytest.raises(SchemaError):
        validate_jsonl_row(payload)


# ------------------------------------------------------------------ 提取行为
DEEP_FAIR = "\n".join(
    [
        "## 6. 误差判定（逐项）",
        "",
        "| 关键量 | 基准 | 误差 | 是否 <3% |",
        "|---|---|---|---|",
        "| 龙骨压力 | Wagner 解析闭式 | 0.49% | 是 |",
        "| 峰值压力系数（S2，面积平均口径） | Chuang 型参考（二次引用 ±33%） | −21.5% | **否** |",
        "| 峰值压力系数（S2，龙骨口径） | 同上 | −50.0% | **否** |",
        "",
        "## 3. 负例矩阵",
        "",
        "| 场景 | 期望门 | 判定 |",
        "|---|---|---|",
        "| schema 越界 | SCHEMA_VERSION_MISMATCH | **FAIL** |",
    ]
)


def test_extract_reports_only_debt_rows_and_inherits_same_as_above():
    meta = ReportMeta(file="reports/r9999.md", report_id="1", mission_id="2", title="P21-N03 砰击")
    rows = extract_from_markdown(DEEP_FAIR, meta)
    metrics = [r.metric for r in rows]
    assert any("龙骨压力" not in m and "峰值压力" in m for m in metrics)
    assert all(r.verdict == "FAIL" for r in rows)
    # 「同上」行继承上一行基准文本 → 根因应落在参考受限
    same_above = [r for r in rows if "龙骨口径" in r.metric]
    assert same_above and same_above[0].rootcause == "reference_limited"
    # 负例小节被抑制
    assert not any("schema" in r.metric for r in rows)


def test_extract_multi_error_columns_expand_per_checkpoint():
    text = "\n".join(
        [
            "## 2. 基线结果",
            "",
            "| 网格 | T=1 err% | T=2 err% | 通过? |",
            "|---|---|---|---|",
            "| a=40 | +10.5 | -7.6 | ❌ |",
        ]
    )
    meta = ReportMeta(file="pending/TensorLBM__terminal_verdict_m163.md", repo="TensorLBM", benchmark="dam_break_sc", kind="pending_verdict")
    rows = extract_from_markdown(text, meta)
    assert {r.metric for r in rows} == {"a=40 @ T=1 err%", "a=40 @ T=2 err%"}
    assert all(r.case == "dam_break_sc" for r in rows)
    assert all(r.source.line == 5 for r in rows)


# -------------------------------------------------------------------- 近重
def test_cross_benchmark_same_gate_name_is_not_duplicate():
    a = _row(case="dam_break_sc", metric="G2 直接观测量 ≤3%")
    b = _row(case="dit_turbulence", metric="G2 直接观测量 ≤3%")
    res = dedup.compare(a, dedup.Target.from_row(b))
    assert res["decision"] != "duplicate"


def test_same_case_same_bucket_containment_is_duplicate():
    a = _row(case="dam_break_sc", metric="a=40", error_pct=10.6)
    b = _row(case="dam_break_sc", metric="a=40 @ T=1 err%", error_pct=10.526)
    res = dedup.compare(a, dedup.Target.from_row(b))
    assert res["decision"] == "duplicate"


def test_same_metric_different_caliber_is_not_duplicate():
    a = _row(case="IN28-N04", metric="C_T(DTMB 5415, Fn=0.28)", error_pct=26.5)
    b = _row(case="IN28-N04", metric="C_T(DTMB 5415, Fn=0.28)", error_pct=44.2)
    res = dedup.compare(a, dedup.Target.from_row(b))
    assert res["decision"] != "duplicate"


# ------------------------------------------------------------------ 端到端
CORPUS_REPORT = "\n".join(
    [
        "## 6. 误差判定",
        "",
        "| 关键量 | 基准 | 误差 | <3%？ |",
        "|---|---|---|---|",
        "| GZ(5°) | 闭式积分 | 0.025% | 是 |",
        "| 峰值压力 | Chuang（二次引用 ±33%） | −21.5% | **否** |",
    ]
)

CORPUS_PENDING = "\n".join(
    [
        "## 4. 四门判决",
        "",
        "| 门 | 标准 | 结果 |",
        "|---|---|---|",
        "| G2 直接观测量 | ≤3% | ❌ FAIL |",
        "| G3 单调收敛 | 档间差 ≤3% | ❌ FAIL |",
    ]
)


def _make_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    (corpus / "reports").mkdir(parents=True)
    (corpus / "pending").mkdir(parents=True)
    (corpus / "reports" / "r9001_m9001_attitude.md").write_text(CORPUS_REPORT, encoding="utf-8")
    (corpus / "pending" / "TensorLBM__terminal_verdict_m900.md").write_text(
        CORPUS_PENDING, encoding="utf-8"
    )
    index = {
        "n_reports": 1,
        "n_pending": 1,
        "reports": [
            {
                "kind": "report",
                "report_id": "9001",
                "mission_id": "9001",
                "title": "P21-N06 甲板上浪",
                "scenario_key": "deck_wetness",
                "file": "r9001_m9001_attitude.md",
                "chars": len(CORPUS_REPORT),
            }
        ],
        "pending": [
            {
                "kind": "pending_verdict",
                "repo": "TensorLBM",
                "benchmark": "dam_break_sc",
                "file": "TensorLBM__terminal_verdict_m900.md",
                "source_path": "/tmp/x.md",
                "chars": len(CORPUS_PENDING),
            }
        ],
    }
    (corpus / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return corpus


def test_build_backlog_end_to_end(tmp_path):
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "out"
    res = build_backlog(corpus, out, nodes_path=None, proposals_root=None)
    rows = [json.loads(l) for l in (out / "backlog.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(res["backlog"]) == 3
    for row in rows:
        validate_jsonl_row(row)
        assert row["rootcause"] in ROOT_CAUSES
    # 覆盖率表 & dry-run 报告都产出
    cov = json.loads((out / "coverage.json").read_text(encoding="utf-8"))
    assert cov["totals"]["debt_rows"] == 3
    dry = json.loads((out / "proposal_drafts.json").read_text(encoding="utf-8"))["dry_run"]
    assert dry["dispatch_attempted"] is False and dry["network_calls"] == 0
    assert dry["verdict"] == "ZERO_DUPLICATE_PROPOSALS"


def test_evaluate_gates_on_synthetic_gold(tmp_path):
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "out"
    build_backlog(corpus, out, nodes_path=None, proposals_root=None)
    backlog = [
        json.loads(l) for l in (out / "backlog.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    gold = {
        "debt": [
            {"loc": "r9001_m9001_attitude.md:6", "rootcause": "reference_limited", "verdict": "FAIL"},
            {"loc": "TensorLBM__terminal_verdict_m900.md:5", "rootcause": "implementation_defect", "verdict": "FAIL"},
            {"loc": "TensorLBM__terminal_verdict_m900.md:6", "rootcause": "discretization_resolution", "verdict": "FAIL"},
        ],
        "ok": [],
        "dedup_pairs": [],
    }
    result = evaluate(backlog, gold)
    assert result["row_level"]["recall"] == 1.0
    assert result["row_level"]["false_positive_rate"] == 0.0
    assert result["acceptance"]["checks"]["dedup_pair_accuracy>=0.90"] is False  # 无 pairs → 不计通过


def test_no_network_calls_in_build(monkeypatch, tmp_path):
    """构造器绝不做网络写：把 urllib/socket 的连接点全钉死。"""
    import socket

    def boom(*a, **k):  # pragma: no cover - 触发即失败
        raise AssertionError("build_backlog must be offline")

    monkeypatch.setattr(socket.socket, "connect", boom, raising=False)
    corpus = _make_corpus(tmp_path)
    res = build_backlog(corpus, tmp_path / "out2", nodes_path=None, proposals_root=None)
    assert res["dry_run"]["network_calls"] == 0