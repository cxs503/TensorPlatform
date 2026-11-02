#!/usr/bin/env python3
"""Mutation-killer tests, round 2, for the manuscript traceability gate.

Provenance
----------
Round 1 (``test_paper_traceability_gate_kills.py``, m1994) took the suite from
49.5% to 59.7% of 186 single-site mutants and left 75 survivors on the books,
of which roughly two dozen were flagged as *real, fixable* debt (severity
flips, finding line-number arithmetic, tolerance literals) rather than
equivalent mutants.

Every test below was written by reading a specific surviving mutant of
``tools/audit/paper_gate_mutation_audit.py`` and asking "which manuscript would
this mutant judge differently from the gate?" — not by reading the gate and
guessing at edge cases.  Deleting a test re-opens a *measured* blind spot.

Survivor classes covered here
  * severity literals — ``FAIL`` -> ``WARN`` (and ``WARN`` -> ``FAIL``)
    silently downgrades the verdict of a gate finding: PG1/PG2/PG3/PG4/PG5/
    PG6/PG7/PG8 severities and the ``verdict``/``counts.warn`` reduction.
  * finding line numbers — ``md[:m.start()].count("\\n") + 1`` and
    ``line_no + r_off + 1`` replaced by ``-1`` / ``line_no - r_off``: a
    reviewer sent to the wrong line.
  * tolerance literals — the ``tol_pp``/``tol_rel`` defaults, the ``/100.0``
    fraction fallback (PG4 and PG5) and the 1e-6 floor of ``value_present``.
  * column algebra — value/reference column order, missing-reference tables,
    truncated rows (must skip, not crash), the ``as_pct`` header test.
  * anchor window — the 160-character look-ahead used to read a PG6 claim.
  * CLI contract — exit codes 0/1/2, ``--tol-pp``/``--tol-rel`` defaults and
    the 40-finding print cap.

Run: python3 -m pytest backend/tests/test_paper_traceability_gate_kills_v2.py -q --noconftest
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "paper_gate"))

from traceability_gate import (  # noqa: E402
    _close,
    check_anchors,
    check_manuscript,
    check_report_values,
    check_table_arithmetic,
    main,
    value_present,
)


# ─────────────────────────────── helpers ───────────────────────────────

def _codes(out):
    return [f["code"] for f in out["findings"]]


def _one(out, code):
    found = [f for f in out["findings"] if f["code"] == code]
    assert len(found) == 1, f"expected exactly one {code}, got {_codes(out)}"
    return found[0]


PG4_MD = """line0

| 量 | 参考 | 数值 | 误差% |
|---|---|---|---|
| Cd | 0.0420 | 0.0427 | 1.00 |
| Ct | 0.1000 | 0.1030 | 3.00 |
| Cp | 1.0000 | 0.9900 | 5.00 |
"""


# ─────────────────── shared tolerance (_close / value_present) ───────────────────

def test_close_accepts_exact_hit_with_zero_band():
    """A stated error that equals the recomputed one exactly is *inside* the
    band even when the band collapses to zero (comparison must be inclusive)."""
    assert _close(100.0, 100.0, 0.0, 0.0) is True


def test_value_present_absolute_epsilon_is_inclusive_and_tiny():
    """Zero-valued claims need an absolute epsilon (relative tolerance is 0 at
    claim=0); the epsilon must be 1e-12 — inclusive at the boundary, and small
    enough that 5e-12 is still a miss."""
    assert value_present(0.0, [1e-12]) is True
    assert value_present(0.0, [5e-12]) is False


# ───────────────────────── PG4 table arithmetic ─────────────────────────

def test_pg4_finding_line_numbers_follow_the_row_offset():
    """Each finding must point at its own table row (start_line + row offset),
    so the drift of either term is measurable."""
    out = check_manuscript(PG4_MD, artifact_roots=[ROOT])
    lines = [f["line"] for f in out["findings"] if f["code"] == "PG4_table_arithmetic"]
    assert lines == [4, 6], lines
    assert out["findings"][0]["severity"] == "FAIL"


def test_pg4_value_column_after_reference_column_is_still_the_value():
    """Header order ``参考值 | 误差% | 数值`` — the value column must be taken
    by *name*, not by being the first non-error column."""
    header = ["参考值", "误差%", "数值"]
    rows = [["1000", "1.0", "1010"]]
    findings, checked = check_table_arithmetic(header, rows, 1, 0.05, 0.005)
    assert checked == 1
    assert findings == []


def test_pg4_table_without_reference_column_is_skipped_not_crashed():
    header = ["数值", "误差%"]
    rows = [["1010", "1.0"]]
    findings, checked = check_table_arithmetic(header, rows, 1, 0.05, 0.005)
    assert (findings, checked) == ([], 0)


def test_pg4_percentless_header_accepts_the_fraction_form():
    """Header without ``%``/``相对`` (``误差比``): the manuscript may quote the
    error as a fraction, so ``pct/100`` must be the fallback divisor."""
    header = ["参考", "数值", "误差比"]
    rows = [["1000", "1500", "0.5"]]          # recomputed 50% -> 0.5 as fraction
    findings, checked = check_table_arithmetic(header, rows, 1, 0.05, 0.005)
    assert checked == 1
    assert findings == []


def test_pg4_percent_header_disables_the_fraction_fallback():
    """A ``误差%`` header pins the unit: 0.01 must *not* be read as 0.01%."""
    header = ["参考", "数值", "误差%"]
    rows = [["1000", "1010", "0.01"]]
    findings, checked = check_table_arithmetic(header, rows, 1, 0.05, 0.005)
    assert checked == 1
    assert [f["code"] for f in findings] == ["PG4_table_arithmetic"]


def test_pg4_detail_reports_the_tolerance_band():
    header = ["参考", "数值", "误差%"]
    rows = [["1000", "1010", "5.0"]]
    findings, _ = check_table_arithmetic(header, rows, 1, 0.05, 0.005)
    assert "容差 0.05pp + 0.5%" in findings[0]["detail"]


# ───────────────────────── PG5 inline claims ─────────────────────────

def test_pg5_severity_is_fail_and_line_is_exact():
    md = "intro\n\nA = 0.0427 vs 0.0420 误差 1.00%\n"
    out = check_manuscript(md, artifact_roots=[ROOT])
    f = _one(out, "PG5_inline_error")
    assert f["severity"] == "FAIL"
    assert f["line"] == 3
    assert out["verdict"] == "FAIL"


def test_pg5_percentless_claim_accepts_fraction_form():
    md = "1500 vs 1000 误差 0.5\n"            # recomputed 50%
    assert _codes(check_manuscript(md, artifact_roots=[ROOT])) == []


def test_pg5_claim_inside_the_band_is_not_re_checked_as_a_fraction():
    """When the percent reading already fits, the fraction fallback must not be
    attempted (it would turn a correct claim into a finding)."""
    md = "3.33 vs 3.27 误差 1.83\n"           # 1.8349%, inside the ±0.059 band
    assert _codes(check_manuscript(md, artifact_roots=[ROOT])) == []


def test_pg5_zero_reference_claim_is_skipped_not_crashed():
    md = "5 vs 0 误差 1.2%\n"
    assert _codes(check_manuscript(md, artifact_roots=[ROOT])) == []


# ─────────────────── PG1 phantom path / command script ───────────────────

def test_pg1_phantom_path_severity_and_line():
    md = "l1\n\n引用 `artifacts/does_not_exist.json` 数值\n"
    out = check_manuscript(md, artifact_roots=[ROOT])
    f = _one(out, "PG1_phantom_path")
    assert f["severity"] == "FAIL"
    assert f["line"] == 3
    assert out["counts"]["by_code"]["PG1_phantom_path"] == 1


def test_pg1_missing_reproduction_script_is_warn_not_fail():
    """A missing reproduction script is a WARN: it must not fail the verdict,
    but it must still be counted as a warning."""
    md = "l1\n复现：`python3 tools/paper_gate/missing_script.py --x`\n"
    out = check_manuscript(md, artifact_roots=[ROOT])
    f = _one(out, "PG1_command_script")
    assert f["severity"] == "WARN"
    assert f["line"] == 2
    assert out["counts"]["warn"] == 1
    assert out["verdict"] == "WARN"


def test_pg1_bare_filename_is_not_a_path_claim():
    """``figure.png`` has no separator, so it is not a repo path claim; treating
    it as one manufactures phantom-path failures out of figure captions."""
    md = "见 `figure.png` 场量云图\n"
    assert _codes(check_manuscript(md, artifact_roots=[ROOT])) == []


# ───────────────────────── PG2 phantom report ─────────────────────────

def test_pg2_severity_and_line():
    md = "a\n\n引用 报告 #999999\n"
    out = check_manuscript(md, artifact_roots=[ROOT], report_ids=[1])
    f = _one(out, "PG2_phantom_report")
    assert f["severity"] == "FAIL"
    assert f["line"] == 3


# ───────────────────────── PG3 figures ─────────────────────────

def test_pg3_phantom_figure_severity_and_line(tmp_path):
    md = "# t\n\n![f](figures/nope.png)\n"
    out = check_manuscript(md, bundle=tmp_path, artifact_roots=[ROOT])
    f = _one(out, "PG3_phantom_figure")
    assert f["severity"] == "FAIL"
    assert f["line"] == 3


def test_pg3_stem_citation_satisfies_the_zero_citation_check(tmp_path):
    """A figure file whose *stem* appears in the text is cited; the H4 check
    must not demand the ``fig<N>`` spelling as well."""
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "Figure 3.png").write_bytes(b"png")
    md = "# t\n\n见 Figure 3 场量云图\n"
    assert _codes(check_manuscript(md, bundle=tmp_path, artifact_roots=[ROOT])) == []


def test_pg3_zero_citation_is_warn(tmp_path):
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "fig7.png").write_bytes(b"png")
    md = "# t\n\n正文与图无关\n"
    out = check_manuscript(md, bundle=tmp_path, artifact_roots=[ROOT])
    f = _one(out, "PG3_zero_citation_figure")
    assert f["severity"] == "WARN"
    assert out["verdict"] == "WARN"


# ───────────────────────── PG6 anchors ─────────────────────────

def _anchor_file(tmp_path, payload):
    p = tmp_path / "anchors.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_pg6_missing_pointer_severity_and_line(tmp_path):
    _anchor_file(tmp_path, {"a": 1})
    md = "x\n\n见 `anchors.json#b` 值 2\n"
    findings, total, resolved = check_anchors(md, [tmp_path], 0.05, 0.005)
    assert total == 1 and resolved == 0
    assert len(findings) == 1
    assert findings[0]["severity"] == "FAIL"
    assert findings[0]["line"] == 3


def test_pg6_value_mismatch_severity_and_line(tmp_path):
    _anchor_file(tmp_path, {"a": 5.0})
    md = "x\n\n见 `anchors.json#a` 值 7\n"
    findings, total, resolved = check_anchors(md, [tmp_path], 0.05, 0.005)
    assert total == 1 and resolved == 0
    assert findings[0]["severity"] == "FAIL"
    assert findings[0]["line"] == 3


def test_pg6_non_numeric_anchor_value_is_resolved(tmp_path):
    _anchor_file(tmp_path, {"name": "abc"})
    md = "见 `anchors.json#name` 值 2\n"
    findings, total, resolved = check_anchors(md, [tmp_path], 0.05, 0.005)
    assert findings == [] and total == 1 and resolved == 1


def test_pg6_claim_window_is_160_chars_forward_only(tmp_path):
    """The declared value is read from the 160 characters *after* the anchor.
    A number further ahead, or one before the anchor, is not the claim."""
    _anchor_file(tmp_path, {"a": 1.0})
    md = ("见 9.0 " + "x" * 150 + " `anchors.json#a` " + "y" * 200 + " 7.0\n")
    findings, total, resolved = check_anchors(md, [tmp_path], 0.05, 0.005)
    assert findings == [] and total == 1 and resolved == 1


# ───────────────────────── PG7 artifact provenance ─────────────────────────

def test_pg7_numeric_table_without_artifact_anchor_fails():
    md = """| 参考 | 数值 | 误差 |
|---|---|---|
| 1000 | 1010 | 1.0 |
"""
    f = _one(check_manuscript(md, artifact_roots=[ROOT]), "PG7_no_artifact_provenance")
    assert f["severity"] == "FAIL"


# ──────────────────── PG8 report-sourced values ────────────────────

def test_pg8_severity_and_line_numbers():
    md = """| 量 | 数值 | 来源 |
|---|---|---|
| Cd | 42.0 | 报告 #1 |
| Ct | 77.0 | 报告 #1 |
"""
    findings, total, resolved = check_report_values(md, {1: "cd 42.0"}, 0.05, 0.005)
    assert (total, resolved) == (2, 1)
    assert len(findings) == 1
    assert findings[0]["severity"] == "FAIL"
    assert findings[0]["line"] == 3


def test_pg8_truncated_row_is_skipped_not_crashed():
    md = """| 量 | 数值 | 来源 |
|---|---|---|
| Cd | 42.0 |
"""
    findings, total, resolved = check_report_values(md, {1: "cd 42.0"}, 0.05, 0.005)
    assert findings == [] and total == 0 and resolved == 0


def test_pg8_partially_unavailable_source_is_warn():
    """One cited report loaded, one not: the row is unverifiable, so WARN — it
    must not be silently value-checked against the half-corpus."""
    md = """| 量 | 数值 | 来源 |
|---|---|---|
| Cd | 77.0 | 报告 #1, 报告 #2 |
"""
    findings, _, _ = check_report_values(md, {1: "cd 42.0"}, 0.05, 0.005)
    assert [f["code"] for f in findings] == ["PG8_source_unavailable"]
    assert findings[0]["severity"] == "WARN"


def test_pg8_relative_tolerance_floor_is_one_e_6():
    """With ``tol_rel=0`` the floor is 1e-6 (not 1e-5): a 5e-6 relative drift is
    a miss."""
    md = """| 量 | 数值 | 来源 |
|---|---|---|
| A | 1.000005 | 报告 #1 |
"""
    findings, _, _ = check_report_values(md, {1: "value 1.0"}, 0.05, 0.0)
    assert [f["code"] for f in findings] == ["PG8_value_not_in_source"]


# ──────────────────── summary blocks (coverage / verdict) ────────────────────

def test_coverage_counts_only_sections_that_carry_a_total(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.json").write_text("{}", encoding="utf-8")
    md = "引用 `sub/a.json`\n"
    out = check_manuscript(md, artifact_roots=[tmp_path], report_ids=[])
    assert out["stats"]["paths"] == {"total": 1, "resolved": 1}
    assert out["coverage"]["resolvable_claims"] == 1
    assert out["coverage"]["rate"] == 1.0


def test_coverage_rate_is_rounded_to_four_decimals(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "sub" / "b.json").write_text("{}", encoding="utf-8")
    md = "引用 `sub/a.json` `sub/b.json` `sub/nope.json`\n"
    out = check_manuscript(md, artifact_roots=[tmp_path])
    assert out["coverage"]["resolvable_claims"] == 3
    assert out["coverage"]["resolved"] == 2
    assert out["coverage"]["rate"] == 0.6667


# ─────────────────────────── default tolerances ───────────────────────────

TOL_MD = """| 量 | 参考 | 数值 | 误差% |
|---|---|---|---|
| A | 1000 | 1001 | 0.5 |
| B | 1 | 3 | 202 |
"""

TOL_MD_PP = """| 量 | 参考 | 数值 | 误差% |
|---|---|---|---|
| A | 1000 | 1001 | 0.5 |
"""

TOL_MD_REL = """| 量 | 参考 | 数值 | 误差% |
|---|---|---|---|
| B | 1 | 3 | 202 |
"""


def test_default_tolerance_flags_both_near_and_far_errata():
    out = check_manuscript(TOL_MD, artifact_roots=[ROOT])
    assert [f["code"] for f in out["findings"]].count("PG4_table_arithmetic") == 2


# ─────────────────────────────── CLI contract ───────────────────────────────

def test_cli_exit_code_two_for_missing_manuscript(tmp_path):
    assert main(["--manuscript", str(tmp_path / "nope.md")]) == 2


def test_cli_exit_codes_zero_and_one(tmp_path):
    clean = tmp_path / "clean.md"
    clean.write_text("no claims at all\n", encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text("引用 `artifacts/none.json`\n", encoding="utf-8")
    assert main(["--manuscript", str(clean)]) == 0
    assert main(["--manuscript", str(bad)]) == 1


def test_cli_default_absolute_tolerance_is_0_05_pp(tmp_path):
    p = tmp_path / "m.md"
    p.write_text(TOL_MD_PP, encoding="utf-8")
    assert main(["--manuscript", str(p)]) == 1


def test_cli_default_relative_tolerance_is_0_005(tmp_path):
    p = tmp_path / "m.md"
    p.write_text(TOL_MD_REL, encoding="utf-8")
    assert main(["--manuscript", str(p)]) == 1


def test_cli_prints_at_most_forty_findings(tmp_path, capsys):
    lines = ["l0"]
    for i in range(45):
        lines.append(f"引用 `artifacts/missing_{i:02d}.json`")
    p = tmp_path / "many.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert main(["--manuscript", str(p)]) == 1
    printed = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("  [")]
    assert len(printed) == 40