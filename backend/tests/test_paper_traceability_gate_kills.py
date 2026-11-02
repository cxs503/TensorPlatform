#!/usr/bin/env python3
"""Mutation-killer tests for the manuscript traceability gate (PAPER-TRACE).

Provenance: these tests were not written by reading the gate and guessing — they
were written from the surviving mutants of
``tools/audit/paper_gate_mutation_audit.py``.  The 21 pre-existing tests killed
92/186 single-site mutants (49.5%); each test below is aimed at a *specific
survivor*, so deleting any of them re-opens a measured blind spot of the suite.

Covered survivor classes
  * ``value_present`` relative tolerance (default 1e-6) — the function that
    decides "this number appears in the cited report" (PG8).
  * ``_close`` absolute+relative band — the shared tolerance of PG4/PG5/PG6.
  * PG4 column algebra: value/reference column *order*, zero-reference rows,
    truncated rows, full-width ``％`` headers, "%"-less headers accepting the
    fraction form (and *only* the fraction form as a fallback).
  * PG4 severity: an inconsistent error column must be FAIL, not WARN.
  * PG5 inline claims: the fraction form of a prose error must also be accepted.
  * ``iter_tables`` boundary: a pipe line at EOF must not be read as a table.

Run: python3 -m pytest backend/tests/test_paper_traceability_gate_kills.py -q --noconftest
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "paper_gate"))

from traceability_gate import (  # noqa: E402
    _close,
    check_inline_errors,
    check_manuscript,
    check_table_arithmetic,
    iter_tables,
    value_present,
)

TOL_PP, TOL_REL = 0.05, 0.005


# ───────────────── value_present: PG8's "is this number in the source?" ─────────────────

def test_value_present_tolerance_band_is_relative_1e6():
    """Claim within 1e-6 relative of a corpus value is present; 5e-6 is not.

    Kills the ``rtol`` numeric-literal scaling mutant (1e-6 -> 1e-5), which would
    make PG8 accept numbers that are *near* the source but not in it — i.e. exactly
    the "plausible but invented" numbers the gate exists to catch.
    """
    assert value_present(100.0001, [100.0]) is True      # rel. diff 1e-6
    assert value_present(100.0005, [100.0]) is False     # rel. diff 5e-6


def test_value_present_exact_hit_and_absent():
    assert value_present(0.0427, [0.0420, 0.0427]) is True
    assert value_present(0.0431, [0.0420, 0.0427]) is False


# ───────────────────────── _close: shared PG4/PG5/PG6 tolerance ─────────────────────────

def test_close_band_grows_with_magnitude():
    """band = tol_pp + tol_rel*|computed|, so 4 units of absolute error is fine at
    1e3 but fatal at 1e0.  Kills the ``+`` -> ``-`` mutant (band would go negative
    and every large-magnitude comparison would fail) and the ``*`` -> ``/`` mutant."""
    assert _close(1004.0, 1000.0, TOL_PP, TOL_REL) is True    # band 5.05
    assert _close(1006.0, 1000.0, TOL_PP, TOL_REL) is False   # 6 > 5.05
    assert _close(1.07, 1.00, TOL_PP, TOL_REL) is False       # 0.07 > band 0.055


def test_close_accepts_sign_agnostic_error_quotes():
    """Errors are quoted as magnitudes as often as with sign."""
    assert _close(3.0, -3.0, TOL_PP, TOL_REL) is True
    assert _close(-3.0, 3.0, TOL_PP, TOL_REL) is True
    assert _close(3.0, 3.0, TOL_PP, TOL_REL) is True


# ───────────────────────────── PG4 table arithmetic ─────────────────────────────

def test_pg4_column_order_indifferent():
    """Reference column *before* the value column must still be handled."""
    header = ["| 量 | 参考 | 数值 | 误差% |"]
    cells = header[0].strip("|").split("|")
    cells = [c.strip() for c in cells]
    rows = [["Cd", "0.0420", "0.0427", "1.6667"]]
    findings, checked = check_table_arithmetic(cells, rows, 1, TOL_PP, TOL_REL)
    assert checked == 1
    assert findings == [], findings


def test_pg4_zero_reference_row_is_skipped_not_fatal():
    """A 0 in the reference column must skip the row (no ZeroDivisionError, no
    bogus finding).  Kills the ``or`` -> ``and`` mutant on the guard chain."""
    cells = ["量", "参考", "数值", "误差%"]
    rows = [["Cd", "0", "0.0427", "1.6667"]]
    findings, checked = check_table_arithmetic(cells, rows, 1, TOL_PP, TOL_REL)
    assert checked == 0
    assert findings == []


def test_pg4_truncated_row_is_skipped_not_fatal():
    """A short row (missing the value cell) must be skipped, not indexed into."""
    cells = ["量", "参考", "数值", "误差%"]
    rows = [["Cd", "0.0420"]]
    findings, checked = check_table_arithmetic(cells, rows, 1, TOL_PP, TOL_REL)
    assert checked == 0
    assert findings == []


def test_pg4_fullwidth_percent_header_means_percent():
    """A full-width ``％`` header still declares percent units."""
    cells = ["量", "参考", "数值", "误差％"]
    rows = [["Cd", "1.00", "1.02", "2.0"]]          # consistent
    findings, checked = check_table_arithmetic(cells, rows, 1, TOL_PP, TOL_REL)
    assert checked == 1 and findings == [], findings


def test_pg4_header_without_unit_accepts_fraction_form():
    """Header with no ``%`` → the fraction form (0.02 for 2%) is accepted."""
    cells = ["量", "参考", "数值", "误差"]
    rows = [["Cd", "1.00", "1.02", "0.02"]]
    findings, checked = check_table_arithmetic(cells, rows, 1, TOL_PP, TOL_REL)
    assert checked == 1
    assert findings == [], findings


def test_pg4_correct_percent_value_not_flagged_when_header_lacks_unit():
    """A *correct* value must stay clean even though the fraction fallback fails.

    Kills ``not ok and not as_pct`` -> ``not ok or not as_pct``: the ``or`` mutant
    would run the fraction fallback on correct rows and emit a false FAIL — a
    gate that fails clean manuscripts is as damaging as one that passes dirty ones.
    """
    cells = ["量", "参考", "数值", "误差"]
    rows = [["Cd", "1.00", "1.02", "2.0"]]         # 2.0 stated as percent
    findings, checked = check_table_arithmetic(cells, rows, 1, TOL_PP, TOL_REL)
    assert checked == 1
    assert findings == [], findings


def test_pg4_inconsistency_is_fail_severity_and_drives_verdict():
    """PG4 must be severity FAIL (not WARN) — a WARN verdict would let the
    manuscript through.  Kills the ``FAIL`` -> ``WARN`` literal mutant."""
    md = (
        "溯源报告 #7；`artifacts/g.json#cd` 取值 0.0427。\n\n"
        "| 量 | 参考 | 数值 | 误差% |\n|---|---|---|---|\n| Cd | 0.0420 | 0.0427 | 9.9 |\n"
    )
    out = check_manuscript(md, artifact_roots=[Path("/nonexistent")],
                           report_ids=[7])
    codes = {f["code"]: f["severity"] for f in out["findings"]}
    assert codes.get("PG4_table_arithmetic") == "FAIL", out["findings"]
    assert out["verdict"] == "FAIL"


# ───────────────────────────── PG5 inline claims ─────────────────────────────

def test_pg5_accepts_fraction_form_of_inline_error():
    """``1.02 vs 1.00, 误差 0.02`` (fraction) must recompute to clean."""
    findings, checked = check_inline_errors(
        "1.02 vs 1.00, 误差 0.02", TOL_PP, TOL_REL)
    assert checked == 1
    assert findings == [], findings


def test_pg5_flags_wrong_percent_inline_error():
    findings, checked = check_inline_errors(
        "1.02 vs 1.00, 误差 9.9%", TOL_PP, TOL_REL)
    assert checked == 1
    assert [f["code"] for f in findings] == ["PG5_inline_error"]


# ───────────────────────────── table boundaries ─────────────────────────────

def test_table_at_eof_without_trailing_newline_is_parsed():
    md = "| 量 | 参考 |\n|---|---|\n| Cd | 0.0420 |"
    tables = list(iter_tables(md))
    assert len(tables) == 1
    assert len(tables[0][1]) == 1


def test_trailing_single_pipe_line_is_not_a_table():
    """A lone ``| 尾注 |`` at EOF must not raise and must not be a table."""
    md = "| 量 | 参考 |\n|---|---|\n| Cd | 0.0420 |\n\n| 尾注 |"
    tables = list(iter_tables(md))
    assert len(tables) == 1