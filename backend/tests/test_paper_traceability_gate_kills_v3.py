#!/usr/bin/env python3
"""PAPER-TRACE 门的第三批杀手测试（P34-N7）：只打**实测 survivor**。

为什么有这个文件
----------------
`tools/audit/paper_gate_mutation_audit.py` 对门源码做单点变异，用测试集当杀手。
上一轮（v2，36 条杀手测试）之后仍有 14 个 survivor，其中若干是**真实盲区**而非
等价变异体：

* CLI 默认容差（``--tol-pp`` / ``--tol-rel``）从不被测试经过 → 把 0.05 改成 0.5
  也没人红（门可以悄悄变成「什么都不查」）；
* PG8 的**行号算术**（``line_no + r_off + 1``）没被断言 → 报错行号可以错到隔壁；
* 「误差列不被当作数值列重复参与复算」这条边界（``val_idx`` 过滤）没有负控样本；
* 截断行（列数不足）必须被**跳过**而不是崩，没有测试钉住；
* PG5 详情里 80 字截断、PG7 的 ``paths.total + anchors.total`` 求和，
  也都只被间接覆盖。

本文件的每条测试都由实测 survivor 反推（不猜），并把「等价变异体」显式记在
末尾作为已知欠账，不冒充可补。

运行
----
    python3 -m pytest backend/tests/test_paper_traceability_gate_kills_v3.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "paper_gate"))

from traceability_gate import (  # noqa: E402
    artifact_provenance,
    check_inline_errors,
    check_manuscript,
    check_table_arithmetic,
    iter_tables,
    main as gate_main,
)


COMPLIANT_MINI = """# 结果

| 量 | 参考 | 数值 | 误差% |
|---|---|---|---|
| C_T | 0.0420 | 0.0427 | 1.6667 |

数据出处：`tools/paper_gate/traceability_gate.py`
"""


# ── survivor const_scale[18]/[19]：CLI 默认容差必须真的是 0.05pp + 0.5% ──────
STATED_OFF_BY_008 = """# 结果

| 量 | 参考 | 数值 | 误差% |
|---|---|---|---|
| C_T | 0.0420 | 0.0427 | 1.7467 |

数据出处：`tools/paper_gate/traceability_gate.py`
"""


def _pg4_findings(md: str) -> list[dict]:
    out = []
    for header, rows, line_no in iter_tables(md):
        f, _ = check_table_arithmetic(header, rows, line_no, 0.05, 0.005)
        out += f
    return out


def test_cli_default_tolerance_band_is_strict(tmp_path):
    """误差列偏离 0.08pp：默认容差（0.05pp + 0.5%）= 0.0583 → 必须判 FAIL。

    变异体把 `--tol-pp` 默认 0.05 改成 0.5（或 `--tol-rel` 0.005 改成 0.05，
    带宽变 0.133）后，同一份稿件会被放行 → 本测试转红。
    """
    md = tmp_path / "manuscript.md"
    md.write_text(STATED_OFF_BY_008, encoding="utf-8")
    assert gate_main(["--manuscript", str(md), "--artifact-root", str(ROOT)]) == 1, \
        "0.08pp 的误差列必须判 FAIL（唯一 FAIL 源是 PG4）"
    out = check_manuscript(STATED_OFF_BY_008, artifact_roots=[ROOT])
    codes = [f["code"] for f in out["findings"]]
    assert "PG4_table_arithmetic" in codes
    # 负控：把容差手工放宽到 0.5pp，同一份稿件必须放行（说明上面那条 FAIL 是容差判出来的）
    assert gate_main(["--manuscript", str(md), "--artifact-root", str(ROOT),
                      "--tol-pp", "0.5"]) == 0


def test_cli_missing_manuscript_exit_code(tmp_path):
    """缺稿退出码必须是 2（变异把常量 2 放大成 20 时本测试转红）。"""
    assert gate_main(["--manuscript", str(tmp_path / "nope.md")]) == 2


def test_cli_json_out_indent_contract(tmp_path):
    """`--json-out` 落盘 JSON 也必须是 2 空格缩进（契约，供下游 diff/解析）。"""
    md = tmp_path / "manuscript.md"
    md.write_text(COMPLIANT_MINI, encoding="utf-8")
    out_path = tmp_path / "gate.json"
    gate_main(["--manuscript", str(md), "--artifact-root", str(ROOT),
               "--json-out", str(out_path)])
    raw = out_path.read_text(encoding="utf-8")
    assert '\n  "verdict"' in raw, raw[:400]
    assert json.loads(raw)["verdict"] in {"PASS", "WARN", "FAIL"}


def test_cli_json_indent_contract(tmp_path, capsys):
    """CLI 输出的 JSON 必须是 2 空格缩进（`indent=2` 是契约：下游解析靠它对齐）。"""
    md = tmp_path / "manuscript.md"
    md.write_text(COMPLIANT_MINI, encoding="utf-8")
    gate_main(["--manuscript", str(md), "--artifact-root", str(ROOT)])
    out = capsys.readouterr().out
    assert '\n  "verdict"' in out, out[:400]
    assert '\n ' * 1 in out


# ── survivor binop_swap[38]/[39]：PG8 报错行号必须是 line_no + r_off + 1 ────
PG8_MD = """# 结果

报告 #823 的行内数值如下：

| 来源 | 量 | 数值 |
|---|---|---|
| 报告 #823 | C_T | 0.0427 |
| 报告 #823 | C_P | 0.0427 |
| 报告 #823 | C_M | 0.0777 |
"""


def test_pg8_finding_line_number_is_exact(tmp_path):
    """PG8 的命中行必须报**第 8 行**（`line_no + r_off + 1`，r_off=2）。

    只有让造假数字落在 **r_off>0** 的行上，行号算术的 ±r_off 才可分辨：
    `line_no + r_off + 1` 改成 `line_no - r_off + 1`（或丢 +1）都会报错行。
    """
    md = tmp_path / "m.md"
    md.write_text(PG8_MD, encoding="utf-8")
    out = check_manuscript(PG8_MD, artifact_roots=[ROOT],
                           report_texts={823: "C_T = 0.0427, C_P = 0.0427"})
    hits = [f for f in out["findings"] if f["code"] == "PG8_value_not_in_source"]
    assert hits, "0.0777 不在报告 #823 正文里，必须被抓"
    assert [f["line"] for f in hits] == [8], [f["line"] for f in hits]


PG8_UNAVAILABLE_MD = """# 结果

| 来源 | 量 | 数值 |
|---|---|---|
| 报告 #823 | C_T | 0.0427 |
| 报告 #997 | C_P | 0.0427 |
"""


def test_pg8_source_unavailable_line_number_is_exact(tmp_path):
    """被引报告正文未载入时，`PG8_source_unavailable` 的行号也必须是
    `line_no + r_off + 1`（r_off=1 → 5）。这条分支的算术在旧测试里完全没覆盖。
    """
    out = check_manuscript(PG8_UNAVAILABLE_MD, artifact_roots=[ROOT],
                           report_texts={823: "C_T = 0.0427"})
    hits = [f for f in out["findings"] if f["code"] == "PG8_source_unavailable"]
    assert hits, "报告 #997 正文未载入，必须报 source_unavailable"
    assert [f["line"] for f in hits] == [5], [f["line"] for f in hits]


# ── survivor boolop_swap[3]：「误差列」不得被当成数值列再算一遍 ──────────────
def test_error_column_is_not_reused_as_value_column():
    """表头同时命中 _ERR_HDR 与 _VAL_HDR 时，「数值列」集合必须为空 → 无 finding。

    变异把 `i not in err_idx and i not in ref_idx` 的 and 改成 or 后，误差列会被
    当成数值列参与复算，凭空产生一条 PG4 finding → 本测试转红。
    """
    header = ["参考", "计算误差%"]
    rows = [["0.0420", "1.67"]]
    findings, checked = check_table_arithmetic(header, rows, 1, 0.05, 0.005)
    assert findings == [] and checked == 0


# ── survivor cmp_swap[11]：列数不足的行必须跳过而不是崩 ──────────────────────
def test_truncated_row_is_skipped_not_crashing():
    """`max(err_i, ref_i, val_i) >= len(row)` 的边界是「恰好少一列」。

    把 `>=` 改成 `>` 后，截断行会走进索引逻辑（IndexError）→ 本测试转红。
    """
    header = ["参考", "数值", "误差%"]
    rows = [["0.0420", "0.0427", "1.6667"], ["0.0420", "0.0427"]]  # 第 2 行截断
    findings, checked = check_table_arithmetic(header, rows, 1, 0.05, 0.005)
    assert findings == [] and checked == 1


# ── survivor const_scale[11]：PG5 详情把行内声明截断到 80 字 ────────────────
def test_pg5_detail_truncates_claim_to_80_chars():
    """详情里的声明串必须截到 80 字（常数 80 被放大成 800 时本测试转红）。"""
    big = "9" * 45
    md = f"系数 {big}.5 vs {big}.25，误差 99.9%（远大于真值）。"
    findings, checked = check_inline_errors(md, 0.05, 0.005)
    assert checked == 1 and findings
    detail = findings[0]["detail"]
    claim_part = detail.split("»")[0].split("«")[-1]
    assert len(claim_part) == 80, len(claim_part)


# ── survivor binop_swap[42]：PG7 的工件锚点数 = paths.total **+** anchors.total ─
def test_pg7_counts_paths_plus_anchors(tmp_path):
    """一个路径 + 一个锚点 ⇒ 锚点数 2（改成减法则变成 0，凭空报 PG7）。"""
    art = tmp_path / "artifacts.json"
    art.write_text(json.dumps({"kpi": {"kill_rate": 0.92}}), encoding="utf-8")
    md = ("# 结果\n\n| 量 | 参考 | 数值 | 误差% |\n|---|---|---|---|\n"
          "| C_T | 0.0420 | 0.0427 | 1.6667 |\n\n"
          f"数据出处：`{art}` 与 `{art}#json.kpi.kill_rate`\n")
    stats = {"paths": {"total": 1, "resolved": 1}, "anchors": {"total": 1}}
    assert artifact_provenance(md, stats) == [], "paths+anchors=2 时不得报 PG7"
    # 负控：把两条锚点都拿掉时必须报（否则本测试是恒真）
    assert artifact_provenance(md, {"paths": {"total": 0}, "anchors": {"total": 0}})


# ───────────────────────────── 已知等价变异体（不冒充欠账） ─────────────────────────────
@pytest.mark.parametrize("why", [
    "binop_swap[5]/[6] @L123：_close 取三者最小，t1/t2 的符号扰动被 t3 吃掉（语义等价）",
    "return_empty[0] @L169 / [1] @L175：早退分支 findings 恒为空，return [] 等价",
])
def test_known_equivalent_mutants_are_documented(why):
    """等价变异体不能被「杀」，但必须被记账（本测试的存在性即账本条目）。"""
    assert why